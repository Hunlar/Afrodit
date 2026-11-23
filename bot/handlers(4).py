import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, MessageEntity, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from reclip_client import (
    ReclipDownloadError,
    ReclipError,
    ReclipInfoError,
    ReclipServiceDown,
    get_info,
    poll_status,
    start_download,
)
import event_client

logger = logging.getLogger(__name__)

# Custom emoji data loaded from the Telegram emoji sticker set at startup.
_custom_emoji_id: str | None = None
_custom_emoji_fallback: str | None = None


async def initialize_custom_emojis(bot) -> None:
    """Load one usable custom emoji from CUSTOM_EMOJI_PACK; fail safely."""
    global _custom_emoji_id, _custom_emoji_fallback
    pack_name = os.environ.get("CUSTOM_EMOJI_PACK", "RestrictedEmoji").strip()
    if not pack_name:
        logger.info("CUSTOM_EMOJI_PACK is empty; custom emojis are disabled")
        return
    try:
        sticker_set = await bot.get_sticker_set(pack_name)
        for sticker in sticker_set.stickers:
            emoji_id = getattr(sticker, "custom_emoji_id", None)
            fallback = getattr(sticker, "emoji", None)
            if emoji_id and fallback:
                _custom_emoji_id = emoji_id
                _custom_emoji_fallback = fallback
                logger.info("Loaded a custom emoji from sticker set %s", pack_name)
                return
        logger.warning("Sticker set %s contains no usable custom emoji", pack_name)
    except Exception as exc:
        # A missing/inaccessible pack must never prevent the bot from starting.
        logger.warning("Could not load custom emoji pack %s: %s", pack_name, exc)


def _custom_emoji_prefix(text: str):
    """Prefix text with a custom emoji entity when one was loaded."""
    if not _custom_emoji_id or not _custom_emoji_fallback:
        return text, None
    fallback = _custom_emoji_fallback
    # Telegram entity offsets and lengths are measured in UTF-16 code units.
    length = len(fallback.encode("utf-16-le")) // 2
    entity = MessageEntity(
        type=MessageEntity.CUSTOM_EMOJI,
        offset=0,
        length=length,
        custom_emoji_id=_custom_emoji_id,
    )
    return f"{fallback} {text}", [entity]

DOWNLOADS_PATH = os.environ.get("DOWNLOADS_PATH", "/downloads")
URL_REGEX = re.compile(r"https?://[^\s<>\"']+")
STATE_TTL = 600  # 10 minutes
CAPTION_MAX = 1000  # Telegram caption limit is 1024, leave headroom
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
TELEGRAM_AUDIO_EXTENSIONS = {".mp3", ".m4a"}


def _media_caption(title: str) -> str:
    """Caption for downloaded video/audio, including the channel credit."""
    return _truncate_caption(f"{title}\n\n> @SancakMain")


def _truncate_caption(text: str) -> str:
    """Truncate a caption to fit within Telegram's 1024-char limit."""
    if not text:
        return ""
    if len(text) <= CAPTION_MAX:
        return text
    return text[: CAPTION_MAX - 1] + "…"

_state: dict[str, dict] = {}
_stats = {"downloads": 0, "errors": 0, "started": time.time()}
_user_prefs: dict[int, dict] = {}  # user_id -> {"quality": "best"|"720"|"480", "format": "video"|"audio"}

SUPPORTED_PLATFORMS = [
    "YouTube", "TikTok", "Instagram", "Twitter/X", "Reddit",
    "Facebook", "Vimeo", "Twitch", "Dailymotion", "SoundCloud",
    "Bandcamp", "Bilibili", "Pinterest", "Tumblr", "Threads",
    "LinkedIn", "Loom", "Streamable", "and 1000+ more via yt-dlp",
]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Merhaba! Ben Afrodit. 💜\n\n"
        "Instagram, TikTok, Pinterest, YouTube, X ve Reddit bağlantılarını "
        "buraya gönder; videoyu veya fotoğrafı otomatik olarak indireyim.\n\n"
        "Bir grupta da bağlantı paylaşıldığında otomatik indirme yapabilirim.\n\n"
        "Komutlar:\n"
        "/help - Yardım\n"
        "/platforms - Desteklenen platformlar\n"
        "/settings - Tercihler\n"
        "/stats - İstatistikler\n"
        "/mp3 <link> - MP3 indir\n"
        "/mp4 <link> - MP4 indir\n\n"
        "Kanalımız: https://t.me/sancakmain\n"
        "Bot: https://t.me/AfroditDwnBot"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Beni Gruba Ekle", url="https://t.me/AfroditDwnBot?startgroup=true")],
        [InlineKeyboardButton("📢 Sancak Kanalı", url="https://t.me/sancakmain")],
        [InlineKeyboardButton("🤖 Afrodit Bot", url="https://t.me/AfroditDwnBot")],
    ])
    text, entities = _custom_emoji_prefix(text)
    await update.message.reply_text(
        text, reply_markup=keyboard, entities=entities, disable_web_page_preview=True
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Afrodit nasıl kullanılır? 💜\n\n"
        "Bir mesaj veya fotoğraf açıklaması içinde desteklenen bir bağlantı paylaş. "
        "Afrodit bağlantıyı otomatik algılar ve en iyi mevcut video kalitesini "
        "indirip aynı sohbete gönderir. Kalite seçme butonuna basman gerekmez.\n\n"
        "Komutlar:\n"
        "/mp4 <link> - En iyi mevcut kalitede video indir\n"
        "/mp3 <link> - MP3 ses indir\n"
        "/platforms - Desteklenen platformlar\n"
        "/settings - Tercihlerini görüntüle\n"
        "/stats - Bot istatistikleri\n\n"
        "Özel sohbetlerde bağlantıyı doğrudan gönder. Gruplarda otomatik algılama "
        "için Afrodit'in tüm mesajları görebilmesi gerekir."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Beni Gruba Ekle", url="https://t.me/AfroditDwnBot?startgroup=true")],
        [InlineKeyboardButton("📢 Sancak Kanalı", url="https://t.me/sancakmain")],
    ])
    text, entities = _custom_emoji_prefix(text)
    await update.message.reply_text(text, entities=entities, reply_markup=keyboard)


async def cmd_platforms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Supported platforms:\n\n" + "\n".join(f"  {p}" for p in SUPPORTED_PLATFORMS)
    text, entities = _custom_emoji_prefix(text)
    await update.message.reply_text(text, entities=entities)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_s = int(time.time() - _stats["started"])
    hours, remainder = divmod(uptime_s, 3600)
    mins, secs = divmod(remainder, 60)

    downloads_dir = Path(DOWNLOADS_PATH)
    disk_mb = 0
    file_count = 0
    if downloads_dir.exists():
        for f in downloads_dir.iterdir():
            if f.is_file():
                disk_mb += f.stat().st_size / (1024 * 1024)
                file_count += 1

    text = (
        f"Afrodit İstatistikleri:\n\n"
        f"  Uptime: {hours}h {mins}m {secs}s\n"
        f"  Downloads: {_stats['downloads']}\n"
        f"  Errors: {_stats['errors']}\n"
        f"  Cached files: {file_count}\n"
        f"  Disk usage: {disk_mb:.1f} MB\n"
        f"  Active sessions: {len(_state)}\n"
    )
    await update.message.reply_text(text)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    prefs = _user_prefs.get(uid, {})
    quality = prefs.get("quality", "best")
    fmt = prefs.get("format", "video")
    text = (
        f"Your preferences:\n\n"
        f"  Default quality: {quality}\n"
        f"  Default format: {fmt}\n\n"
        f"Change:\n"
        f"  /setquality <best/720/480>\n"
        f"  /setformat <video/audio>\n"
    )
    await update.message.reply_text(text)


async def cmd_setquality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /setquality <best/1080/720/480/360>")
        return
    q = context.args[0].lower()
    valid = ["best", "1080", "720", "480", "360"]
    if q not in valid:
        await update.message.reply_text(f"Invalid quality. Options: {', '.join(valid)}")
        return
    _user_prefs.setdefault(uid, {})["quality"] = q
    await update.message.reply_text(f"Default quality set to: {q}")


async def cmd_setformat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /setformat <video/audio>")
        return
    f = context.args[0].lower()
    if f not in ("video", "audio"):
        await update.message.reply_text("Invalid format. Options: video, audio")
        return
    _user_prefs.setdefault(uid, {})["format"] = f
    await update.message.reply_text(f"Default format set to: {f}")


def _extract_urls_from_command(update: Update) -> list[str]:
    """Extract URLs from command text or from the replied-to message."""
    text = update.message.text or ""
    urls = URL_REGEX.findall(text)
    if not urls and update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        urls = URL_REGEX.findall(reply_text)
    return urls


async def cmd_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct MP3 download without format picker."""
    urls = _extract_urls_from_command(update)
    if not urls:
        await update.message.reply_text("Usage: /mp3 <link>\nOr reply to a message containing a link.")
        return
    for url in urls:
        msg = await update.message.reply_text("Downloading MP3...")
        await _direct_download(update, msg, url, "audio", None)


async def cmd_mp4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct best-quality MP4 download without format picker."""
    urls = _extract_urls_from_command(update)
    if not urls:
        await update.message.reply_text("Usage: /mp4 <link>\nOr reply to a message containing a link.")
        return
    for url in urls:
        msg = await update.message.reply_text("Downloading MP4 (best quality)...")
        await _direct_download(update, msg, url, "video", None)


async def cmd_best(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias for /mp4."""
    await cmd_mp4(update, context)


async def _direct_download(update: Update, status_msg, url: str, fmt: str, format_id: str | None):
    """Download without the interactive picker flow."""
    try:
        info = await get_info(url)
    except ReclipServiceDown:
        await _edit_safe(status_msg, "Download service temporarily unavailable.")
        _stats["errors"] += 1
        return
    except ReclipError as e:
        await _edit_safe(status_msg, f"Error: {e}")
        _stats["errors"] += 1
        return

    title = info.get("title", "download")
    entry = {"url": url, "info": info, "user_id": update.effective_user.id}

    try:
        job_id = await start_download(url, fmt, format_id, title)
    except ReclipError as e:
        await _edit_safe(status_msg, f"Error: {e}")
        _stats["errors"] += 1
        return

    try:
        await event_client.send_download_start(
            job_id=job_id,
            user_id=update.effective_user.id,
            username=update.effective_user.username or str(update.effective_user.id),
            chat_id=update.effective_chat.id,
            url=url,
            platform=info.get("extractor", "unknown"),
            format=fmt,
            quality=format_id or "best",
            title=title,
        )
    except Exception:
        pass

    _direct_download_start = time.time()
    file_path = None
    for _ in range(450):
        await asyncio.sleep(2)
        try:
            status = await poll_status(job_id)
        except ReclipServiceDown:
            await _edit_safe(status_msg, "Download service unavailable.")
            _stats["errors"] += 1
            await event_client.send_download_error(job_id=job_id, error_message="Download service unavailable")
            return
        except ReclipError:
            continue

        st = status.get("status")
        if st == "done":
            file_path = status.get("file_path") or status.get("filename")
            video_meta = {
                "width": status.get("width"),
                "height": status.get("height"),
                "duration": status.get("duration"),
            }
            break
        elif st == "error":
            await _edit_safe(status_msg, f"Error: {status.get('error', 'Unknown error')}")
            _stats["errors"] += 1
            await event_client.send_download_error(job_id=job_id, error_message=status.get("error", "Unknown error"))
            return
        else:
            progress = status.get("progress")
            if progress and isinstance(progress, dict) and progress.get("percent") is not None:
                text = f"Downloading... {progress['percent']}%"
                try:
                    await event_client.send_progress(
                        job_id=job_id,
                        percent=progress.get("percent"),
                        speed=progress.get("speed"),
                        eta=progress.get("eta"),
                        downloaded_bytes=progress.get("downloaded_bytes"),
                        total_bytes=progress.get("total_bytes"),
                    )
                except Exception:
                    pass
            else:
                text = "Downloading..."
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass

    if not file_path:
        await _edit_safe(status_msg, "Download timed out.")
        _stats["errors"] += 1
        await event_client.send_download_error(job_id=job_id, error_message="Download timed out")
        return

    local_path = Path(DOWNLOADS_PATH) / Path(file_path).name
    if not local_path.exists():
        local_path = Path(file_path)
    if not local_path.exists():
        await _edit_safe(status_msg, "File not found after download.")
        _stats["errors"] += 1
        await event_client.send_download_error(job_id=job_id, error_message="File not found after download")
        return

    for attempt in range(2):
        try:
            with open(local_path, "rb") as f:
                if local_path.suffix.lower() in IMAGE_EXTENSIONS and local_path.stat().st_size <= 10 * 1024 * 1024:
                    await update.message.chat.send_photo(photo=f, caption=_truncate_caption(title))
                elif fmt == "video" and local_path.suffix.lower() == ".mp4":
                    dur = video_meta.get("duration")
                    await update.message.chat.send_video(
                        video=f,
                        caption=_media_caption(title),
                        supports_streaming=True,
                        width=video_meta.get("width"),
                        height=video_meta.get("height"),
                        duration=int(dur) if dur else None,
                    )
                elif fmt == "audio" and local_path.suffix.lower() in TELEGRAM_AUDIO_EXTENSIONS:
                    await update.message.chat.send_audio(
                        audio=f, caption=_media_caption(title), title=title
                    )
                else:
                    caption = _media_caption(title) if fmt in ("video", "audio") else _truncate_caption(title)
                    await update.message.chat.send_document(document=f, caption=caption)
            _stats["downloads"] += 1
            try:
                await event_client.send_download_done(
                    job_id=job_id,
                    file_size_bytes=local_path.stat().st_size,
                    duration_seconds=time.time() - _direct_download_start,
                    filename=local_path.name,
                )
            except Exception:
                pass
            break
        except Exception:
            if attempt == 0:
                await asyncio.sleep(1)
            else:
                await _edit_safe(status_msg, "Failed to upload file to Telegram.")
                _stats["errors"] += 1
                await event_client.send_download_error(job_id=job_id, error_message="Failed to upload to Telegram")
                return

    try:
        await status_msg.edit_text(f"Sent: {title}")
    except Exception:
        pass
    try:
        local_path.unlink()
    except Exception:
        pass


def _state_key(chat_id: int, message_id: int, url_hash: str) -> str:
    return f"{chat_id}:{message_id}:{url_hash}"


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]


def _evict_stale():
    now = time.time()
    expired = [k for k, v in _state.items() if now - v["created"] > STATE_TTL]
    for k in expired:
        del _state[k]


def _format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _build_format_buttons(message_id: int, url_hash: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("MP4", callback_data=f"fmt:{message_id}:{url_hash}:video"),
            InlineKeyboardButton("MP3", callback_data=f"fmt:{message_id}:{url_hash}:audio"),
        ]
    ])


def _build_quality_buttons(message_id: int, url_hash: str, formats: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for fmt in formats:
        label = fmt.get("label", fmt.get("id", "?"))
        buttons.append(
            InlineKeyboardButton(label, callback_data=f"qty:{message_id}:{url_hash}:{fmt['id']}")
        )
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("Best quality", callback_data=f"qty:{message_id}:{url_hash}:best")])
    return InlineKeyboardMarkup(rows)


async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically download every supported URL posted in a chat.

    This intentionally skips the old MP4/MP3 and quality-selection buttons:
    a regular link in a private chat or group starts a best-quality video/photo
    download immediately. Captions are also scanned for URLs.
    """
    message = update.effective_message
    if message is None:
        return

    text = message.text or message.caption or ""
    urls = URL_REGEX.findall(text)
    if not urls:
        return

    # Remove common punctuation accidentally attached to a URL.
    urls = [url.rstrip(".,!?;:)") for url in urls]

    for url in urls:
        status_msg = await message.reply_text("Afrodit bağlantıyı algıladı. İndiriliyor... ⏳")
        await _direct_download(update, status_msg, url, "video", None)


def _escape_md(text: str) -> str:
    special = r"_*[]()~`>#+-=|{}.!\\"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


async def format_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _evict_stale()
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 4:
        return
    _, msg_id_str, uhash, fmt = parts
    msg_id = int(msg_id_str)

    key = _state_key(query.message.chat_id, msg_id, uhash)
    entry = _state.get(key)
    if not entry:
        key = _state_key(query.message.chat_id, query.message.message_id, uhash)
        entry = _state.get(key)
    if not entry:
        await query.edit_message_text("Session expired. Please send the link again.")
        return

    if fmt == "back":
        keyboard = _build_format_buttons(query.message.message_id, uhash)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    if fmt == "audio":
        asyncio.create_task(
            download_and_send(query, entry, format="audio", format_id=None)
        )
    elif fmt == "video":
        formats = entry["info"].get("formats", [])
        if not formats:
            asyncio.create_task(
                download_and_send(query, entry, format="video", format_id=None)
            )
            return

        keyboard = _build_quality_buttons(query.message.message_id, uhash, formats[:6])
        await query.edit_message_reply_markup(reply_markup=keyboard)


async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _evict_stale()
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 4:
        return
    _, msg_id_str, uhash, format_id = parts
    msg_id = int(msg_id_str)

    key = _state_key(query.message.chat_id, msg_id, uhash)
    entry = _state.get(key)
    if not entry:
        key = _state_key(query.message.chat_id, query.message.message_id, uhash)
        entry = _state.get(key)
    if not entry:
        await query.edit_message_text("Session expired. Please send the link again.")
        return

    fid = None if format_id == "best" else format_id
    asyncio.create_task(
        download_and_send(query, entry, format="video", format_id=fid)
    )


async def download_and_send(query, entry: dict, format: str, format_id: str | None):
    chat_id = query.message.chat_id
    message = query.message
    url = entry["url"]
    title = entry["info"].get("title", "download")

    try:
        await message.edit_caption(caption="Starting download...") if message.photo else await message.edit_text("Starting download...")
    except Exception:
        pass

    try:
        job_id = await start_download(url, format, format_id, title)
    except ReclipServiceDown:
        await _edit_safe(message, "Download service temporarily unavailable.")
        _stats["errors"] += 1
        return
    except ReclipDownloadError as e:
        await _edit_safe(message, f"Download failed: {e}")
        _stats["errors"] += 1
        return
    except ReclipError as e:
        await _edit_safe(message, f"Error: {e}")
        _stats["errors"] += 1
        return

    try:
        await event_client.send_download_start(
            job_id=job_id,
            user_id=entry["user_id"],
            username=str(entry.get("user_id", "")),
            chat_id=chat_id,
            url=url,
            platform=entry["info"].get("extractor", "unknown"),
            format=format,
            quality=format_id or "best",
            title=title,
        )
    except Exception:
        pass

    file_path = None
    for _ in range(450):
        await asyncio.sleep(2)
        try:
            status = await poll_status(job_id)
        except ReclipServiceDown:
            await _edit_safe(message, "Download service temporarily unavailable.")
            _stats["errors"] += 1
            await event_client.send_download_error(job_id=job_id, error_message="Download service temporarily unavailable")
            return
        except ReclipError:
            continue

        st = status.get("status")
        if st == "done":
            file_path = status.get("file_path") or status.get("filename")
            video_meta = {
                "width": status.get("width"),
                "height": status.get("height"),
                "duration": status.get("duration"),
            }
            break
        elif st == "error":
            await _edit_safe(message, f"Error: {status.get('error', 'Unknown error')}")
            _stats["errors"] += 1
            await event_client.send_download_error(job_id=job_id, error_message=status.get("error", "Unknown error"))
            return
        else:
            progress = status.get("progress")
            if progress and isinstance(progress, dict) and progress.get("percent") is not None:
                text = f"Downloading... {progress['percent']}%"
                try:
                    await event_client.send_progress(
                        job_id=job_id,
                        percent=progress.get("percent"),
                        speed=progress.get("speed"),
                        eta=progress.get("eta"),
                        downloaded_bytes=progress.get("downloaded_bytes"),
                        total_bytes=progress.get("total_bytes"),
                    )
                except Exception:
                    pass
            else:
                text = "Downloading..."
            try:
                if message.photo:
                    await message.edit_caption(caption=text)
                else:
                    await message.edit_text(text)
            except Exception:
                pass

    if not file_path:
        await _edit_safe(message, "Download timed out.")
        _stats["errors"] += 1
        await event_client.send_download_error(job_id=job_id, error_message="Download timed out")
        return

    local_path = Path(DOWNLOADS_PATH) / Path(file_path).name
    if not local_path.exists():
        local_path = Path(file_path)
    if not local_path.exists():
        await _edit_safe(message, "File not found after download.")
        _stats["errors"] += 1
        await event_client.send_download_error(job_id=job_id, error_message="File not found after download")
        return

    for attempt in range(2):
        try:
            with open(local_path, "rb") as f:
                if local_path.suffix.lower() in IMAGE_EXTENSIONS and local_path.stat().st_size <= 10 * 1024 * 1024:
                    await query.message.chat.send_photo(photo=f, caption=_truncate_caption(title))
                elif format == "video" and local_path.suffix.lower() == ".mp4":
                    dur = video_meta.get("duration")
                    await query.message.chat.send_video(
                        video=f,
                        caption=_media_caption(title),
                        supports_streaming=True,
                        width=video_meta.get("width"),
                        height=video_meta.get("height"),
                        duration=int(dur) if dur else None,
                    )
                elif format == "audio" and local_path.suffix.lower() in TELEGRAM_AUDIO_EXTENSIONS:
                    await query.message.chat.send_audio(
                        audio=f, caption=_media_caption(title), title=title
                    )
                else:
                    caption = _media_caption(title) if format in ("video", "audio") else _truncate_caption(title)
                    await query.message.chat.send_document(document=f, caption=caption)
            _stats["downloads"] += 1
            try:
                await event_client.send_download_done(
                    job_id=job_id,
                    file_size_bytes=local_path.stat().st_size,
                    duration_seconds=time.time() - entry["created"],
                    filename=local_path.name,
                )
            except Exception:
                pass
            break
        except Exception:
            if attempt == 0:
                logger.warning("Upload failed, retrying once")
                await asyncio.sleep(1)
            else:
                logger.exception("Upload failed after retry")
                await _edit_safe(message, "Failed to upload file to Telegram.")
                _stats["errors"] += 1
                await event_client.send_download_error(job_id=job_id, error_message="Failed to upload to Telegram")
                return

    try:
        sent_text = _truncate_caption(f"Sent: {title}")
        if message.photo:
            await message.edit_caption(caption=sent_text)
        else:
            await message.edit_text(sent_text)
    except Exception:
        pass

    try:
        local_path.unlink()
    except Exception:
        logger.debug("Could not delete %s", local_path)


async def _edit_safe(message, text: str):
    try:
        if message.photo:
            await message.edit_caption(caption=text)
        else:
            await message.edit_text(text)
    except Exception:
        logger.debug("Failed to edit message with: %s", text)


def register_handlers(application):
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("platforms", cmd_platforms))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CommandHandler("setquality", cmd_setquality))
    application.add_handler(CommandHandler("setformat", cmd_setformat))
    application.add_handler(CommandHandler("mp3", cmd_mp3))
    application.add_handler(CommandHandler("mp4", cmd_mp4))
    application.add_handler(CommandHandler("best", cmd_best))
    application.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.CAPTION, url_handler))
    application.add_handler(CallbackQueryHandler(format_callback, pattern=r"^fmt:"))
    application.add_handler(CallbackQueryHandler(quality_callback, pattern=r"^qty:"))
