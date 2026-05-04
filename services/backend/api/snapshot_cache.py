"""
Background snapshot cache — periodically grabs a frame for each camera
so the /api/streams/<id>/snapshot/ endpoint can return instantly.

Usage:
  start_snapshot_worker()   — call once from AppConfig.ready()
  get_cached_snapshot(pk)   — returns (jpeg_bytes, source, age_s) or None
"""

import logging
import os
import subprocess
import threading
import time

log = logging.getLogger("vigilzone.snapshot_cache")

# {camera_pk: {"data": bytes, "source": str, "ts": float}}
_store: dict[int, dict] = {}
_lock = threading.Lock()
_running = False

# How often to refresh each camera (seconds)
INTERVAL = float(os.getenv("SNAPSHOT_CACHE_INTERVAL", "2"))
# Maximum age before a cached image is considered stale
MAX_AGE = float(os.getenv("SNAPSHOT_CACHE_MAX_AGE", "5"))


def _get_ai_active_camera_ids(ai_base: str, ttl_s: float = 3.0):
    """Best-effort cached fetch of active AI camera IDs from /cameras."""
    import requests as http_client

    now = time.time()
    cache = getattr(_get_ai_active_camera_ids, "_cache", None)
    if cache and cache.get("base") == ai_base and (now - cache.get("ts", 0.0)) < ttl_s:
        return cache.get("ids")

    try:
        r = http_client.get(f"{ai_base}/cameras", timeout=1.0)
        ids = set()
        if r.status_code == 200 and r.content:
            payload = r.json()
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        cam_id = item.get("camera_id")
                        if cam_id:
                            ids.add(str(cam_id))
        _get_ai_active_camera_ids._cache = {"base": ai_base, "ts": now, "ids": ids}
        return ids
    except Exception:
        # Unknown active set -> caller may try all candidates.
        return None


def get_cached_snapshot(camera_pk: int):
    """Return (jpeg_bytes, source_str, age_seconds) or None."""
    with _lock:
        entry = _store.get(camera_pk)
    if entry is None:
        return None
    age = time.time() - entry["ts"]
    if age > MAX_AGE:
        return None
    return entry["data"], entry["source"], age


def _grab_frame(rtsp_url: str, ai_base: str, ai_cam_ids):
    """Try AI first, then ffmpeg fallback.  Returns (jpeg_bytes, source) or (None, None)."""
    import requests as http_client

    # AI primary — try candidate IDs/endpoints in order
    for ai_cam_id in ai_cam_ids:
        for url in (
            f"{ai_base}/frame/{ai_cam_id}",
            f"{ai_base}/api/v1/cameras/{ai_cam_id}/snapshot",
        ):
            try:
                r = http_client.get(url, params={"maxw": 1280, "quality": 70}, timeout=2)
                ct = r.headers.get("Content-Type", "")
                if r.status_code == 200 and "image" in ct and r.content:
                    return r.content, f"ai:{ai_cam_id}"
            except Exception:
                continue

    # ffmpeg fallback
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-rw_timeout", "3000000",
                "-i", rtsp_url,
                "-frames:v", "1", "-q:v", "5",
                "-f", "image2", "-vcodec", "mjpeg",
                "pipe:1",
            ],
            capture_output=True,
            timeout=6,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout, "ffmpeg"
    except Exception:
        pass

    return None, None


def _worker():
    """Background loop — runs in a daemon thread."""
    global _running
    log.info("Snapshot cache worker started  (interval=%.1fs, max_age=%.1fs)", INTERVAL, MAX_AGE)
    while _running:
        try:
            from api.models import Camera  # deferred import to avoid AppRegistryNotReady
            cameras = list(
                Camera.objects.filter(stream_path__isnull=False)
                .exclude(stream_path="")
                .values_list("pk", "rtsp_url", "stream_path", "ai_camera_id")
            )
        except Exception as exc:
            log.debug("snapshot_cache: cannot query cameras yet: %s", exc)
            time.sleep(INTERVAL)
            continue

        from server.runtime_services import get_ai_base_url, get_mediamtx_rtsp_base

        mediamtx_rtsp_base = get_mediamtx_rtsp_base()
        ai_base = get_ai_base_url()
        active_ids = _get_ai_active_camera_ids(ai_base)

        for pk, rtsp_url, stream_path, ai_camera_id in cameras:
            if not _running:
                break
            ai_candidates = []
            for cand in (ai_camera_id, stream_path, f"cam_{pk}"):
                if cand and cand not in ai_candidates:
                    ai_candidates.append(cand)
            if active_ids is not None:
                ai_candidates = [c for c in ai_candidates if c in active_ids]
            if not ai_candidates:
                # Avoid hammering known-invalid IDs when AI active set is known.
                if active_ids is not None:
                    continue
                ai_candidates = [ai_camera_id or stream_path or f"cam_{pk}"]
            url = rtsp_url or f"{mediamtx_rtsp_base}/{stream_path}"
            data, source = _grab_frame(url, ai_base, ai_candidates)
            if data:
                with _lock:
                    _store[pk] = {"data": data, "source": source, "ts": time.time()}

        time.sleep(INTERVAL)

    log.info("Snapshot cache worker stopped")


def start_snapshot_worker():
    """Start the background thread (idempotent — only one worker runs)."""
    global _running
    if _running:
        return
    _running = True
    t = threading.Thread(target=_worker, daemon=True, name="snapshot-cache")
    t.start()
