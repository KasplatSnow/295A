import logging
import socket
import time
from typing import Any, Tuple
from urllib.parse import urlparse

import requests

from api.models import Camera, MediaMTXDesiredPath
from api.services.mediamtx_helpers import (
    classify_camera_source,
    get_mediamtx_api_base,
    get_relay_identity,
)

logger = logging.getLogger(__name__)


def _extract_jpeg_from_multipart(buffer: bytes) -> bytes | None:
    start = buffer.find(b"\xff\xd8")
    if start == -1:
        return None
    end = buffer.find(b"\xff\xd9", start + 2)
    if end == -1:
        return None
    return buffer[start:end + 2]


class ProbeService:
    """Service to handle tiered camera connection testing.

    Implements three-gate validation:
    1. Network Pre-flight (TCP/HTTP)
    2. MediaMTX State Gate (Observation)
    3. Media Verification (ffprobe loopback)
    """

    @staticmethod
    def check_network_availability(url: str, source_kind: str, timeout: float = 3.0) -> Tuple[bool, str]:
        """Gate 0: Lightweight pre-flight check based on source kind."""
        if not url:
            return False, "No URL provided"

        parsed = urlparse(url)

        # -- Tier 1: TCP Probe (for RTSP/RTMP/SRT) --
        if source_kind == "native":
            host = parsed.hostname
            # Note: parsed.port is None if not explicitly in string; 
            # we default to 554 for RTSP if scheme suggests it.
            port = parsed.port
            if not port:
                if parsed.scheme in ["rtsp", "rtsps"]:
                    port = 554
                elif parsed.scheme in ["rtmp", "rtmps"]:
                    port = 1935
                else:
                    port = 554

            if not host:
                return False, f"Invalid host in URL: {url}"
            
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True, "TCP Connection Successful"
            except Exception as e:
                return False, f"Network unreachable (TCP {host}:{port}): {e}"

        # -- Tier 2: HTTP Probe (for HLS/MJPEG/Snapshots) --
        if source_kind in ["hls", "mjpeg", "snapshot"]:
            try:
                # Try HEAD first as it's lightest
                resp = requests.head(url, timeout=timeout, allow_redirects=True)
                if resp.status_code < 400:
                    return True, "HTTP Host Reachable"
                
                # Some cameras/servers reject HEAD; fallback to limited GET
                resp = requests.get(url, timeout=timeout, stream=True)
                if resp.status_code < 400:
                    resp.close()
                    return True, "HTTP Host Reachable"
                
                return False, f"HTTP Error: {resp.status_code}"
            except Exception as e:
                return False, f"HTTP unreachable: {e}"

        return True, f"Skipping network pre-flight for source kind: {source_kind}"

    @staticmethod
    def wait_for_mediamtx_state(path_name: str, timeout_s: float = 5.0) -> Tuple[bool, str]:
        """Gate 1: Poll MediaMTX for observed source connection state."""
        api_base = get_mediamtx_api_base()
        start_time = time.time()

        while time.time() - start_time < timeout_s:
            try:
                resp = requests.get(f"{api_base}/v3/paths/get/{path_name}", timeout=2.0)
                if resp.status_code == 200:
                    data = resp.json()
                    source_state = data.get("sourceState", "")
                    if source_state == "connected":
                        return True, "MediaMTX source connected"
                    if source_state == "error":
                        err_msg = data.get("sourceError", "Unknown source error")
                        return False, f"MediaMTX report: {err_msg}"
                elif resp.status_code == 404:
                    # Path hasn't been created yet
                    pass
            except Exception:
                pass
            time.sleep(0.5)

        return False, f"Timed out after {timeout_s}s waiting for MediaMTX connection"

    @staticmethod
    def run_media_probe(loopback_url: str, timeout_s: int = 5) -> dict:
        """Gate 2: Bounded final media verification via ffprobe."""
        from api.services.mediamtx_helpers import _probe_rtsp
        return _probe_rtsp(loopback_url, timeout_s=timeout_s)

    @staticmethod
    def run_http_media_probe(url: str, source_kind: str, timeout_s: int = 5) -> dict:
        """Final media verification for direct HTTP snapshot/MJPEG sources."""
        started = time.monotonic()
        try:
            if source_kind == "snapshot":
                resp = requests.get(url, timeout=timeout_s)
                content_type = str(resp.headers.get("Content-Type", ""))
                if resp.status_code == 200 and "image" in content_type and resp.content:
                    return {
                        "ok": True,
                        "method": "http_snapshot",
                        "latency_ms": int((time.monotonic() - started) * 1000),
                        "details": {
                            "content_type": content_type,
                            "bytes": len(resp.content),
                        },
                    }
                return {
                    "ok": False,
                    "category": "http_media_invalid",
                    "method": "http_snapshot",
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "error": f"Snapshot endpoint returned HTTP {resp.status_code}",
                }

            if source_kind == "mjpeg":
                with requests.get(url, stream=True, timeout=(3.0, timeout_s)) as resp:
                    content_type = str(resp.headers.get("Content-Type", "")).lower()
                    if resp.status_code != 200:
                        return {
                            "ok": False,
                            "category": "http_media_invalid",
                            "method": "http_mjpeg",
                            "latency_ms": int((time.monotonic() - started) * 1000),
                            "error": f"MJPEG endpoint returned HTTP {resp.status_code}",
                        }
                    if "multipart" not in content_type and "mjpeg" not in content_type and "jpeg" not in content_type:
                        return {
                            "ok": False,
                            "category": "http_media_invalid",
                            "method": "http_mjpeg",
                            "latency_ms": int((time.monotonic() - started) * 1000),
                            "error": f"Unexpected content type: {content_type or 'unknown'}",
                        }

                    buffer = bytearray()
                    for chunk in resp.iter_content(chunk_size=4096):
                        if not chunk:
                            continue
                        buffer.extend(chunk)
                        jpeg = _extract_jpeg_from_multipart(buffer)
                        if jpeg:
                            return {
                                "ok": True,
                                "method": "http_mjpeg",
                                "latency_ms": int((time.monotonic() - started) * 1000),
                                "details": {
                                    "content_type": content_type,
                                    "bytes": len(jpeg),
                                },
                            }
                        if len(buffer) > 2_000_000:
                            break

                    return {
                        "ok": False,
                        "category": "http_media_invalid",
                        "method": "http_mjpeg",
                        "latency_ms": int((time.monotonic() - started) * 1000),
                        "error": "Could not extract a JPEG frame from MJPEG stream",
                    }
        except requests.Timeout:
            return {
                "ok": False,
                "category": "http_media_timeout",
                "method": f"http_{source_kind}",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": f"Timed out after {timeout_s}s reading {source_kind} source",
            }
        except requests.RequestException as exc:
            return {
                "ok": False,
                "category": "http_media_error",
                "method": f"http_{source_kind}",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": str(exc),
            }

        return {
            "ok": False,
            "category": "unsupported_source",
            "method": f"http_{source_kind}",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"Unsupported HTTP media source kind: {source_kind}",
        }
