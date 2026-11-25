"""Tests that event_client functions are fire-and-forget (never raise)."""
import asyncio
import os

# Point at an unreachable URL before importing event_client
os.environ["DASHBOARD_URL"] = "http://localhost:99999"

import event_client


def test_send_download_start_no_raise():
    asyncio.run(event_client.send_download_start(
        job_id="job-1",
        user_id=12345,
        username="testuser",
        chat_id=67890,
        url="https://example.com/video",
        platform="youtube",
        format="video",
        quality="best",
        title="Test Video",
    ))


def test_send_progress_no_raise():
    asyncio.run(event_client.send_progress(
        job_id="job-1",
        percent=42.5,
        speed=1024000.0,
        eta=30.0,
        downloaded_bytes=10485760,
        total_bytes=25165824,
    ))


def test_send_download_done_no_raise():
    asyncio.run(event_client.send_download_done(
        job_id="job-1",
        file_size_bytes=25165824,
        duration_seconds=12.5,
        filename="video.mp4",
    ))


def test_send_download_error_no_raise():
    asyncio.run(event_client.send_download_error(
        job_id="job-1",
        error_message="Download timed out",
    ))

def test_send_progress_uses_dashboard_event_name(monkeypatch):
    from unittest.mock import patch

    sent = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            sent.append(json)

    with patch("event_client.httpx.AsyncClient", return_value=FakeClient()):
        asyncio.run(event_client.send_progress(
            job_id="job-progress",
            percent=50,
            speed=1000,
            eta=10,
            downloaded_bytes=500,
            total_bytes=1000,
        ))

    assert sent[0]["type"] == "download_progress"
