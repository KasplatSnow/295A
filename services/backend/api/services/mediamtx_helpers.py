"""
Shared MediaMTX utility functions.

Extracted from views.py so both the relay reconciler and request handlers
can import them without circular dependencies.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse, urlunparse
from api.models import Camera
from server.runtime_services import (
    get_ai_base_url as resolve_ai_base_url,
    get_mediamtx_api_base as resolve_mediamtx_api_base,
    get_mediamtx_internal_rtsp_url,
    get_mediamtx_rtsp_base as resolve_mediamtx_rtsp_base,
)


# Bump this when the payload renderer changes (e.g. FFmpeg flags)
# to force a one-time controlled re-apply across all paths.
MEDIAMTX_PAYLOAD_RENDERER_VERSION = 5


def sanitize_stream_url(url: str) -> str:
    """Trim whitespace and accidental wrapping quotes from camera URLs."""
    value = str(url or "").strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        value = value[1:-1].strip()
    while value and value[0] in {"'", '"', "`"}:
        value = value[1:].strip()
    while value and value[-1] in {"'", '"', "`"}:
        value = value[:-1].strip()
    return value


def host_looks_local(hostname: str) -> bool:
    host = str(hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]"}


def hash_mediamtx_payload(payload: dict) -> str:
    """Stable hash of meaningful MediaMTX config fields.

    Includes renderer version so FFmpeg flag changes force
    a controlled one-time re-apply.
    """
    significant = {
        "_rv": MEDIAMTX_PAYLOAD_RENDERER_VERSION,
        "source": payload.get("source", ""),
        "sourceOnDemand": payload.get("sourceOnDemand"),
        "runOnInit": payload.get("runOnInit", ""),
        "runOnDemand": payload.get("runOnDemand", ""),
        "runOnInitRestart": payload.get("runOnInitRestart"),
        "runOnDemandRestart": payload.get("runOnDemandRestart"),
        "rtspTransport": payload.get("rtspTransport", ""),
        "runOnDemandStartTimeout": payload.get("runOnDemandStartTimeout"),
        "runOnDemandCloseAfter": payload.get("runOnDemandCloseAfter"),
        "sourceOnDemandStartTimeout": payload.get("sourceOnDemandStartTimeout"),
        "sourceOnDemandCloseAfter": payload.get("sourceOnDemandCloseAfter"),
        "sourceFingerprint": payload.get("sourceFingerprint"),
    }
    # Remove None values for stable serialization
    significant = {k: v for k, v in significant.items() if v is not None}
    canon = json.dumps(significant, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def get_canonical_camera_id(camera: Camera) -> str:
    """Return a stable ID for MediaMTX paths and AI registration."""
    from django.utils.text import slugify

    if camera.stream_path:
        return camera.stream_path
    if camera.ai_camera_id:
        return camera.ai_camera_id
    if camera.name:
        return slugify(camera.name)
    return f"camera-{camera.pk}"


def get_mediamtx_api_base() -> str:
    return resolve_mediamtx_api_base()


def get_ai_base_url() -> str:
    """Return the base URL for the AI module API."""
    return resolve_ai_base_url()


def get_relay_identity() -> tuple[frozenset[str], int]:
    """Return (set of hostnames that are "us", RTSP port).

    Reads from Django settings when available, falls back to env vars
    so the function is usable from management commands before full
    Django setup.
    """
    try:
        from django.conf import settings
        return settings.RELAY_RTSP_ALIASES, settings.RELAY_RTSP_PORT
    except Exception:
        # Fallback for management commands / non-Django contexts
        internal_rtsp = get_mediamtx_internal_rtsp_url()
        parsed = urlparse(internal_rtsp)
        host = (parsed.hostname or "127.0.0.1").lower()
        port = parsed.port or 8554
        aliases = frozenset({
            "127.0.0.1", "localhost", "0.0.0.0", "::1", host,
        })
        return aliases, port


def normalize_stream_url(url: str) -> str:
    """
    Standardize a stream URL for identity checks and deduplication.
    - Strips whitespace
    - Lowercases scheme and hostname
    - Removes default ports (554 for rtsp, 80 for http, 443 for https)
    - Normalizes empty paths
    """
    url = sanitize_stream_url(url)
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except Exception:
        return url.lower() # Fallback for non-standard formats

    scheme = (parsed.scheme or "rtsp").lower()
    netloc = (parsed.netloc or "").lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    path = parsed.path or "/"
    
    # Strip default ports to avoid 'rtsp://host' vs 'rtsp://host:554' mismatches
    if port:
        if (scheme == "rtsp" and port == 554) or \
           (scheme == "http" and port == 80) or \
           (scheme == "https" and port == 443):
            netloc = hostname

    # Reconstruct canonical URL
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))


def get_url_identity_hash(url: str) -> str:
    """Return a stable 16-char hex hash of the normalized URL."""
    norm = normalize_stream_url(url)
    if not norm:
        return ""
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def is_self_referential(url: str) -> bool:
    """Return True if *url* points back to our own MediaMTX relay.

    This is the authoritative guard that prevents loopback loops.
    Works in local dev, Docker, and cloud environments because it
    checks against ``RELAY_RTSP_ALIASES`` (driven by settings/env).
    """
    url = sanitize_stream_url(url)
    if not url:
        return False
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    port = parsed.port  # None when not specified in the URL

    aliases, relay_port = get_relay_identity()

    if hostname not in aliases:
        return False

    # Port not specified → assume default RTSP 554, which is NOT our relay
    if port is None:
        return False

    return port == relay_port


def get_mediamtx_loopback_url(stream_path: str) -> str:
    """Return the RTSP loopback URL for AI ingestion through MediaMTX."""
    base = resolve_mediamtx_rtsp_base()
    return f"{base.rstrip('/')}/{str(stream_path).lstrip('/')}"


def classify_camera_source(url: str) -> str:
    """Classify a camera source URL into a source kind string."""
    lowered = sanitize_stream_url(url).lower()
    if lowered.startswith((
        "rtsp://", "rtsps://", "rtmp://", "rtmps://",
        "srt://", "whep://", "wheps://"
    )):
        return "native"
    if ".m3u8" in lowered:
        return "hls"

    # If the URL contains frame_count, it's almost certainly intended to be a stream
    # even if it uses a "snapshot" or "oneshot" endpoint.
    if "frame_count" in lowered or "framecount" in lowered:
        return "mjpeg"

    if any(x in lowered for x in ["getoneshot", "snapshot"]):
        return "snapshot"
    if any(x in lowered for x in [".mjpg", ".mjpeg", "/mjpg", "/mjpeg", "nphmotionjpeg", "motionjpeg"]):
        return "mjpeg"
    return "unknown"


def _is_publisher_source_type(camera: Camera) -> bool:
    """Return True if the camera's source_type means MediaMTX should
    WAIT for a publisher rather than actively pulling a stream.

    This is the authoritative decision point — NOT the URL content.
    """
    # Webcam streams are pushed into MediaMTX by the AI service or
    # a webcam_publisher; MediaMTX must never try to pull them.
    if camera.source_type == Camera.SourceType.WEBCAM:
        return True

    # If there is no rtsp_url at all, there is nothing to pull.
    if not (camera.rtsp_url or "").strip():
        return True

    # If the user accidentally stored our own relay URL as the source,
    # treat it as a publisher path (prevents the loopback).
    if is_self_referential(camera.rtsp_url):
        return True

    return False


def build_mediamtx_path_payload(
    camera: Camera,
    path_name: str,
    source_kind: str,
    persistent: bool = False,
) -> dict:
    """Render the deterministic MediaMTX config payload for a camera path.

    The decision tree is:
      1. If the camera is a publisher type (webcam, no URL, or self-
         referential URL) → ``source: publisher``.
      2. If the source requires transcoding (mjpeg/snapshot) → FFmpeg bridge.
      3. Otherwise → native RTSP/HLS pull.
    """

    # ── Gate 1: Publisher-type sources (webcam, managed, no URL) ───
    if _is_publisher_source_type(camera):
        payload: dict = {
            "source": "publisher",
            # Explicitly reset pull-mode fields so that PATCHing an
            # existing pull path to publisher mode doesn't leave stale
            # sourceOnDemand=true which MediaMTX rejects.
            "sourceOnDemand": False,
        }

        # For webcam/cam_live, set up an FFmpeg on-demand bridge from
        # the AI service MJPEG endpoint.
        if camera.ai_camera_id == "cam_live":
            ai_api_base = get_ai_base_url()
            input_url = f"{ai_api_base}/stream/cam_live"
            rtsp_target_base = get_mediamtx_internal_rtsp_url().rstrip("/")
            command = (
                'ffmpeg -nostdin -loglevel error '
                f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
                f'-i "{input_url}" '
                f'-an -vf "scale=640:-2" '
                f'-c:v libx264 -preset superfast -r 5 -b:v 300k '
                f'-pix_fmt yuv420p '
                f'-f rtsp -rtsp_transport tcp {rtsp_target_base}/{path_name}'
            )
            if persistent:
                payload["runOnInit"] = command
                payload["runOnInitRestart"] = True
                # Explicitly clear on-demand so PATCH removes stale state
                payload["runOnDemand"] = ""
                payload["runOnDemandRestart"] = False
            else:
                payload["runOnDemand"] = command
                payload["runOnDemandRestart"] = True
                payload["runOnDemandStartTimeout"] = "30s"
                payload["runOnDemandCloseAfter"] = "10s"
                # Explicitly clear persistent so PATCH removes stale state
                payload["runOnInit"] = ""
                payload["runOnInitRestart"] = False

        return payload

    # ── Gate 2: Native RTSP / HLS pull ────────────────────────────
    if source_kind in ("native", "hls"):
        payload = {
            "source": sanitize_stream_url(camera.rtsp_url),
            "sourceOnDemand": not persistent,
        }
        if not persistent:
            payload["sourceOnDemandStartTimeout"] = "30s"
            payload["sourceOnDemandCloseAfter"] = "10s"

        if source_kind == "native" and sanitize_stream_url(camera.rtsp_url).lower().startswith("rtsp"):
            payload["rtspTransport"] = "tcp"
        if camera.source_fingerprint:
            payload["sourceFingerprint"] = camera.source_fingerprint
        return payload

    # ── Gate 3: MJPEG / Snapshot (FFmpeg Bridge) ──────────────────
    elif source_kind in ("mjpeg", "snapshot"):
        escaped_url = sanitize_stream_url(camera.rtsp_url).replace('"', '\\"')
        input_url = escaped_url

        ffmpeg_cmd = 'ffmpeg -nostdin -loglevel warning '
        if camera.rtsp_url.lower().startswith("https://"):
            ffmpeg_cmd += '-tls_verify 0 '

        rtsp_target_base = get_mediamtx_internal_rtsp_url().rstrip("/")
        command = (
            ffmpeg_cmd +
            f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
            f'-i "{input_url}" '
            f'-an -vf "scale=640:-2" '
            f'-c:v libx264 -preset superfast -r 5 -b:v 300k '
            f'-pix_fmt yuv420p '
            f'-f rtsp -rtsp_transport tcp {rtsp_target_base}/{path_name}'
        )

        payload = {
            "source": "publisher",
        }

        if persistent:
            payload["runOnInit"] = command
            payload["runOnInitRestart"] = True
            # Explicitly clear on-demand so PATCH removes stale state
            payload["runOnDemand"] = ""
            payload["runOnDemandRestart"] = False
        else:
            payload["runOnDemand"] = command
            payload["runOnDemandRestart"] = True
            payload["runOnDemandStartTimeout"] = "30s"
            payload["runOnDemandCloseAfter"] = "10s"
            # Explicitly clear persistent so PATCH removes stale state
            payload["runOnInit"] = ""
            payload["runOnInitRestart"] = False

        return payload
    else:
        # Unknown or explicitly "publisher" source_kind — safe fallback
        # to passive publisher mode.  This prevents crashes and ensures
        # the path exists in MediaMTX even if classification is unclear.
        return {"source": "publisher", "sourceOnDemand": False}


def _probe_rtsp(rtsp_url: str, timeout_s: int = 3) -> dict:
    """
    Probe an RTSP URL with ffprobe (fast), falling back to ffmpeg single-frame grab.
    Returns { ok, method, latency_ms, details?, error? }.
    """
    import json as _json
    import shutil
    import subprocess
    import time as _time

    result: dict = {"ok": False, "method": "none", "latency_ms": 0}

    # ── Try ffprobe first ─────────────────────────────────────
    if shutil.which("ffprobe"):
        t0 = _time.monotonic()
        try:
            proc = subprocess.Popen(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-rw_timeout",
                    str(timeout_s * 1_000_000),
                    "-show_streams",
                    "-select_streams",
                    "v:0",
                    "-print_format",
                    "json",
                    rtsp_url,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.wait(timeout=timeout_s + 2)
            stdout = proc.stdout.read(51200)  # Bound read to 50KB to prevent OOM
            stderr = proc.stderr.read(51200)

            latency = int((_time.monotonic() - t0) * 1000)
            if proc.returncode == 0 and stdout:
                try:
                    info = _json.loads(stdout)
                    streams = info.get("streams", [])
                    if streams:
                        s = streams[0]
                        return {
                            "ok": True,
                            "method": "ffprobe",
                            "latency_ms": latency,
                            "details": {
                                "codec": s.get("codec_name"),
                                "width": s.get("width"),
                                "height": s.get("height"),
                                "fps": s.get("r_frame_rate"),
                            },
                        }
                except _json.JSONDecodeError:
                    pass
            result["error"] = stderr.decode(errors="replace").strip()[:300]
        except subprocess.TimeoutExpired:
            proc.kill()
            result["error"] = f"ffprobe timed out ({timeout_s}s)"
            result["latency_ms"] = timeout_s * 1000
            return result
        except FileNotFoundError:
            pass  # fall through to ffmpeg

    # ── Fallback: ffmpeg single-frame grab ────────────────────
    t0 = _time.monotonic()
    try:
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "quiet",
                "-rtsp_transport", "tcp",
                "-rw_timeout", str(timeout_s * 1_000_000),
                "-i", rtsp_url,
                "-frames:v", "1",
                "-f", "null", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait(timeout=timeout_s + 2)
        stdout = proc.stdout.read(51200)
        stderr = proc.stderr.read(51200)

        latency = int((_time.monotonic() - t0) * 1000)
        if proc.returncode == 0:
            return {"ok": True, "method": "ffmpeg", "latency_ms": latency}
        result["method"] = "ffmpeg"
        result["latency_ms"] = latency
        result["error"] = stderr.decode(errors="replace").strip()[:300]
    except FileNotFoundError:
        result["error"] = "Neither ffprobe nor ffmpeg found on PATH"
    except subprocess.TimeoutExpired:
        proc.kill()
        result["error"] = f"ffmpeg timed out ({timeout_s}s)"
        result["latency_ms"] = timeout_s * 1000

    return result
