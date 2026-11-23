"""Run Afrodit's dashboard, downloader API, and Telegram bot in one Heroku dyno."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
PORT = int(os.environ.get("PORT", "8000"))
RECLIP_PORT = int(os.environ.get("RECLIP_PORT", "8899"))
DOWNLOADS_PATH = os.environ.get("DOWNLOADS_PATH", "/tmp/afrodit-downloads")
DB_PATH = os.environ.get("DB_PATH", "/tmp/afrodit.db")

processes: list[subprocess.Popen] = []
stopping = False


def _stop(_signum=None, _frame=None):
    global stopping
    stopping = True


def _wait_for_port(host: str, port: int, timeout: int = 45) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not stopping:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _start(name: str, command: list[str], cwd: Path, env: dict[str, str]):
    print(f"[Afrodit] Starting {name}: {' '.join(command)}", flush=True)
    process = subprocess.Popen(command, cwd=str(cwd), env=env)
    processes.append(process)
    return process


def main() -> int:
    if not os.environ.get("BOT_TOKEN"):
        print("[Afrodit] BOT_TOKEN is required.", file=sys.stderr, flush=True)
        return 1
    if not os.environ.get("ADMIN_PASSWORD"):
        print("[Afrodit] ADMIN_PASSWORD is required.", file=sys.stderr, flush=True)
        return 1

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    downloads = Path(DOWNLOADS_PATH)
    downloads.mkdir(parents=True, exist_ok=True)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    common_env = os.environ.copy()
    common_env["DOWNLOADS_PATH"] = str(downloads)
    common_env["DB_PATH"] = DB_PATH
    common_env["RECLIP_URL"] = f"http://127.0.0.1:{RECLIP_PORT}"
    common_env["DASHBOARD_URL"] = f"http://127.0.0.1:{PORT}"

    reclip_env = common_env.copy()
    reclip_env["HOST"] = "127.0.0.1"
    reclip_env["PORT"] = str(RECLIP_PORT)

    dashboard_env = common_env.copy()
    dashboard_env["PORT"] = str(PORT)

    try:
        reclip = _start(
            "downloader API", [PYTHON, "-u", "app.py"], ROOT / "reclip", reclip_env
        )
        _start(
            "dashboard",
            [PYTHON, "-u", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(PORT)],
            ROOT / "dashboard",
            dashboard_env,
        )

        if not _wait_for_port("127.0.0.1", RECLIP_PORT):
            print("[Afrodit] Downloader API did not become ready.", file=sys.stderr, flush=True)
            return 1
        if reclip.poll() is not None:
            print("[Afrodit] Downloader API exited during startup.", file=sys.stderr, flush=True)
            return 1

        _start("Telegram bot", [PYTHON, "-u", "bot.py"], ROOT / "bot", common_env.copy())
        print(f"[Afrodit] All services started. Dashboard port: {PORT}; downloader port: {RECLIP_PORT}.", flush=True)

        while not stopping:
            for process in processes:
                code = process.poll()
                if code is not None:
                    print(f"[Afrodit] A service exited with code {code}; stopping dyno.", file=sys.stderr, flush=True)
                    return code or 1
            time.sleep(1)
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
        print("[Afrodit] Services stopped.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
