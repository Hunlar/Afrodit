import os
import uuid
import glob
import json
import subprocess
import threading
import time
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
DOWNLOAD_DIR = os.environ.get("DOWNLOADS_PATH", os.path.join(os.path.dirname(__file__), "downloads"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", 3))
DOWNLOAD_TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", 900))
download_semaphore = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)

jobs = {}

# Optional remote Netscape-format cookies.txt. Keep this file outside the repo and
# never print its contents: it contains account session credentials.
YOUTUBE_COOKIES_URL = os.environ.get("YOUTUBE_COOKIES_URL", "").strip()
YOUTUBE_COOKIES_REFRESH_SECONDS = max(60, int(os.environ.get("YOUTUBE_COOKIES_REFRESH_SECONDS", "900")))
YOUTUBE_COOKIES_MAX_BYTES = min(10 * 1024 * 1024, max(1024, int(os.environ.get("YOUTUBE_COOKIES_MAX_BYTES", str(5 * 1024 * 1024)))))
YOUTUBE_COOKIES_PATH = os.environ.get(
    "YOUTUBE_COOKIES_PATH", os.path.join(tempfile.gettempdir(), "afrodit-youtube-cookies.txt")
)
_cookie_lock = threading.Lock()
_cookie_last_fetch = 0.0


def _is_youtube_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "youtu.be" or host.endswith(".youtube.com") or host == "youtube.com" or host.endswith(".youtube-nocookie.com") or host == "youtube-nocookie.com"
    except Exception:
        return False


def _validate_netscape_cookies(content):
    text = content.decode("utf-8-sig")
    lines = text.splitlines()
    has_youtube_cookie = False
    for line in lines:
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            domain = fields[0].removeprefix("#HttpOnly_").lstrip(".").lower()
            if domain == "youtube.com" or domain.endswith(".youtube.com") or domain == "google.com" or domain.endswith(".google.com"):
                has_youtube_cookie = True
    if not has_youtube_cookie:
        raise ValueError("Cookies dosyası geçerli Netscape biçiminde değil veya YouTube çerezi içermiyor")
    return text


def _get_youtube_cookie_args(url):
    """Return yt-dlp cookie arguments for YouTube URLs, refreshing a remote file safely."""
    global _cookie_last_fetch
    if not _is_youtube_url(url):
        return []
    if not YOUTUBE_COOKIES_URL:
        return []

    parsed = urlparse(YOUTUBE_COOKIES_URL)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("YOUTUBE_COOKIES_URL geçerli, kimlik bilgisi içermeyen bir HTTPS bağlantısı olmalı")

    with _cookie_lock:
        now = time.time()
        cache_ok = os.path.isfile(YOUTUBE_COOKIES_PATH) and now - _cookie_last_fetch < YOUTUBE_COOKIES_REFRESH_SECONDS
        if not cache_ok:
            req = Request(YOUTUBE_COOKIES_URL, headers={"User-Agent": "Afrodit/1.0 cookies-fetcher"})
            with urlopen(req, timeout=20) as response:
                final_url = urlparse(response.geturl())
                if final_url.scheme != "https":
                    raise ValueError("Cookies bağlantısı HTTPS dışına yönlendirildi")
                content = response.read(YOUTUBE_COOKIES_MAX_BYTES + 1)
            if len(content) > YOUTUBE_COOKIES_MAX_BYTES:
                raise ValueError("Cookies dosyası izin verilen boyutu aşıyor")
            cookie_text = _validate_netscape_cookies(content)
            temp_path = YOUTUBE_COOKIES_PATH + ".tmp"
            os.makedirs(os.path.dirname(YOUTUBE_COOKIES_PATH) or ".", exist_ok=True)
            with open(temp_path, "w", encoding="utf-8", newline="\n") as cookie_file:
                cookie_file.write(cookie_text)
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, YOUTUBE_COOKIES_PATH)
            _cookie_last_fetch = now

    return ["--cookies", YOUTUBE_COOKIES_PATH]


def _youtube_error_hint(url, message):
    if _is_youtube_url(url) and ("Sign in to confirm" in message or "not a bot" in message.lower()):
        if YOUTUBE_COOKIES_URL:
            return message + " | YouTube cookies ayarlı ancak YouTube doğrulaması devam ediyor; çerezleri yenileyin veya farklı bir erişim ağı deneyin."
        return message + " | Çözüm için Heroku Config Vars bölümüne güvenli bir cookies.txt HTTPS bağlantısı olarak YOUTUBE_COOKIES_URL ekleyin."
    return message


PROGRESS_TEMPLATE = (
    'download:{"downloaded_bytes":%(progress.downloaded_bytes)s,'
    '"total_bytes":%(progress.total_bytes)s,'
    '"speed":%(progress.speed)s,'
    '"eta":%(progress.eta)s}'
)


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]

    if not download_semaphore.acquire(timeout=30):
        job["status"] = "error"
        job["error"] = "Too many concurrent downloads, please try again later"
        return

    try:
        _do_download(job_id, url, format_choice, format_id)
    finally:
        download_semaphore.release()


def _do_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = [
        "yt-dlp", "--ignore-config", "--no-playlist", "-o", out_template,
        "--progress-template", PROGRESS_TEMPLATE,
        "--force-ipv4",
        "--downloader", "hls:ffmpeg",
        "--concurrent-fragments", "4",
        "--socket-timeout", "20",
        "--retries", "5",
        "--fragment-retries", "10",
        "--throttled-rate", "50K",
    ]

    try:
        cmd += _get_youtube_cookie_args(url)
    except Exception as exc:
        job["status"] = "error"
        job["error"] = f"YouTube cookies alınamadı: {exc}"
        return

    # Do not inherit a server-side yt-dlp config that may force an unavailable format.
    # Prefer the requested quality when it still exists, then fall back to any
    # available video+audio combination (or a combined format).
    if format_choice == "audio":
        cmd += ["-f", "bestaudio/best", "-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/bv*+ba/b", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]

    cmd.append(url)

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    timed_out = threading.Event()

    def _kill_on_timeout():
        timed_out.set()
        try:
            process.kill()
        except Exception:
            pass

    deadline_timer = threading.Timer(DOWNLOAD_TIMEOUT, _kill_on_timeout)
    deadline_timer.daemon = True
    deadline_timer.start()

    stderr_lines = []

    def _read_stderr():
        for line in process.stderr:
            line = line.rstrip("\n")
            stderr_lines.append(line)
            try:
                json_str = line.removeprefix("download:")
                progress_data = json.loads(json_str)
                total = progress_data.get("total_bytes")
                downloaded = progress_data.get("downloaded_bytes")
                percent = None
                if total and downloaded and total > 0:
                    percent = round(downloaded / total * 100, 1)
                job["progress"] = {
                    "percent": percent,
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                    "speed": progress_data.get("speed"),
                    "eta": progress_data.get("eta"),
                }
            except (json.JSONDecodeError, ValueError):
                pass

    stderr_reader = threading.Thread(target=_read_stderr, daemon=True)
    stderr_reader.start()

    try:
        returncode = process.wait()
    finally:
        deadline_timer.cancel()
        stderr_reader.join(timeout=5)

    if timed_out.is_set():
        job["status"] = "error"
        job["error"] = f"Download timed out ({DOWNLOAD_TIMEOUT // 60} min limit)"
        return

    try:
        if returncode != 0:
            job["status"] = "error"
            last_line = stderr_lines[-1] if stderr_lines else "Unknown error"
            job["error"] = _youtube_error_hint(url, last_line)
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        if chosen.endswith(".mp4"):
            try:
                codec_probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=codec_name",
                     "-of", "default=noprint_wrappers=1:nokey=1", chosen],
                    capture_output=True, text=True, timeout=10,
                )
                vcodec = (codec_probe.stdout or "").strip().lower()
            except Exception:
                vcodec = ""

            if vcodec in ("av1", "vp9", "vp8"):
                transcoded = chosen + ".h264.mp4"
                try:
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-i", chosen,
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                         "-c:a", "aac", "-b:a", "128k",
                         "-movflags", "+faststart",
                         transcoded],
                        capture_output=True, timeout=600,
                    )
                    if r.returncode == 0 and os.path.exists(transcoded) and os.path.getsize(transcoded) > 0:
                        os.replace(transcoded, chosen)
                    elif os.path.exists(transcoded):
                        os.remove(transcoded)
                except (subprocess.TimeoutExpired, OSError):
                    if os.path.exists(transcoded):
                        try: os.remove(transcoded)
                        except OSError: pass
            else:
                faststart_tmp = chosen + ".fs.mp4"
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", chosen, "-c", "copy",
                         "-movflags", "+faststart", faststart_tmp],
                        capture_output=True, timeout=120,
                    )
                    if os.path.exists(faststart_tmp) and os.path.getsize(faststart_tmp) > 0:
                        os.replace(faststart_tmp, chosen)
                    elif os.path.exists(faststart_tmp):
                        os.remove(faststart_tmp)
                except (subprocess.TimeoutExpired, OSError):
                    if os.path.exists(faststart_tmp):
                        try: os.remove(faststart_tmp)
                        except OSError: pass

        if chosen.endswith(".mp4"):
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height:format=duration",
                     "-of", "json", chosen],
                    capture_output=True, text=True, timeout=10,
                )
                info = json.loads(probe.stdout)
                stream = (info.get("streams") or [{}])[0]
                fmt = info.get("format") or {}
                job["width"] = stream.get("width")
                job["height"] = stream.get("height")
                dur = fmt.get("duration")
                job["duration"] = float(dur) if dur else None
            except Exception:
                pass

        job["status"] = "done"
        job["file"] = chosen
        job["file_path"] = os.path.abspath(chosen)
        ext = os.path.splitext(chosen)[1]
        title = job.get("title", "").strip()
        # Sanitize title for filename
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:20].strip()
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # --ignore-config prevents a global yt-dlp config from forcing a format
    # that is not available for the requested YouTube video.
    cmd = ["yt-dlp", "--ignore-config", "--no-playlist", "--no-warnings", "-j"]
    try:
        cmd += _get_youtube_cookie_args(url)
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            error_line = result.stderr.strip().split("\n")[-1]
            return jsonify({"error": _youtube_error_hint(url, error_line)}), 400

        info = json.loads(result.stdout)

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append({
                "id": f["format_id"],
                "label": f"{height}p",
                "height": height,
            })
        formats.sort(key=lambda x: x["height"], reverse=True)

        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "extractor": info.get("extractor", ""),
            "formats": formats,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
        "progress": job.get("progress"),
        "file_path": job.get("file_path"),
        "width": job.get("width"),
        "height": job.get("height"),
        "duration": job.get("duration"),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
