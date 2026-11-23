import os
import logging
import httpx

logger = logging.getLogger(__name__)

RECLIP_URL = os.environ.get("RECLIP_URL", "http://reclip:8899")


class ReclipError(Exception):
    pass


class ReclipInfoError(ReclipError):
    pass


class ReclipDownloadError(ReclipError):
    pass


class ReclipServiceDown(ReclipError):
    pass


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=RECLIP_URL)


def _response_error(resp: httpx.Response) -> str:
    """Extract the useful error returned by the Reclip API."""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("message")
            if detail:
                return str(detail)[:700]
    except (ValueError, TypeError):
        pass
    return (resp.text or "No error details returned")[:700]


async def get_info(url: str) -> dict:
    try:
        async with _client() as client:
            resp = await client.post("/api/info", json={"url": url}, timeout=60.0)
            if resp.is_error:
                detail = _response_error(resp)
                logger.warning("Reclip info request failed (%s): %s", resp.status_code, detail)
                raise ReclipInfoError(
                    f"Info request failed: {resp.status_code} - {detail}"
                )
            return resp.json()
    except httpx.ConnectError:
        raise ReclipServiceDown("Cannot reach reclip service")
    except httpx.TimeoutException:
        raise ReclipInfoError("Info request timed out")
    except ReclipInfoError:
        raise
    except Exception as e:
        raise ReclipInfoError(f"Info request failed: {e}")


async def start_download(url: str, format: str, format_id: str | None, title: str) -> str:
    payload = {"url": url, "format": format, "title": title}
    if format_id:
        payload["format_id"] = format_id
    try:
        async with _client() as client:
            resp = await client.post("/api/download", json=payload, timeout=60.0)
            if resp.is_error:
                detail = _response_error(resp)
                raise ReclipDownloadError(
                    f"Download request failed: {resp.status_code} - {detail}"
                )
            data = resp.json()
            return data["job_id"]
    except httpx.ConnectError:
        raise ReclipServiceDown("Cannot reach reclip service")
    except httpx.TimeoutException:
        raise ReclipDownloadError("Download request timed out")
    except ReclipDownloadError:
        raise
    except (KeyError, ValueError) as e:
        raise ReclipDownloadError(f"Malformed response: {e}")
    except Exception as e:
        raise ReclipDownloadError(f"Download request failed: {e}")


async def poll_status(job_id: str) -> dict:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/status/{job_id}", timeout=10.0)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise ReclipServiceDown("Cannot reach reclip service")
    except httpx.TimeoutException:
        raise ReclipError("Status request timed out")
    except httpx.HTTPStatusError as e:
        detail = _response_error(e.response)
        raise ReclipError(f"Status request failed: {e.response.status_code} - {detail}")
    except Exception as e:
        raise ReclipError(f"Status request failed: {e}")
