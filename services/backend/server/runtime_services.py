from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

TRUE_VALUES = {"1", "true", "yes", "on"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]"}


def _env_text(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def _env_int(*names: str, default: int) -> int:
    raw = _env_text(*names, default="")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in TRUE_VALUES


def host_looks_local(hostname: str) -> bool:
    host = str(hostname or "").strip().lower()
    return host in LOCAL_HOSTS


def _normalize_base_url(raw: str) -> str:
    return str(raw or "").strip().rstrip("/")


def get_backend_bind_host() -> str:
    return _env_text("DJANGO_HOST", default="127.0.0.1")


def get_backend_bind_port() -> int:
    return _env_int("DJANGO_PORT", default=8000)


def get_ai_bind_host() -> str:
    return _env_text("AI_HOST", default="127.0.0.1")


def get_ai_bind_port() -> int:
    return _env_int("AI_BASE_PORT", "AI_API_PORT", "AI_PORT", default=8001)


def get_public_base_url() -> str:
    configured = _env_text("PUBLIC_BASE_URL")
    if configured:
        return _normalize_base_url(configured)
    return f"http://{get_backend_bind_host()}:{get_backend_bind_port()}"


def get_ai_base_url() -> str:
    configured = _env_text("AI_BASE_INTERNAL")
    if configured:
        return _normalize_base_url(configured)
    return f"http://{get_ai_bind_host()}:{get_ai_bind_port()}"


def get_mediamtx_internal_rtsp_url() -> str:
    configured = _env_text("MEDIAMTX_INTERNAL_RTSP_URL")
    if configured:
        return _normalize_base_url(configured)
    relay_host = _env_text("RELAY_RTSP_HOST", default="127.0.0.1")
    relay_port = _env_int("RELAY_RTSP_PORT", default=8554)
    return f"rtsp://{relay_host}:{relay_port}"


def get_mediamtx_rtsp_base() -> str:
    configured = _env_text("MEDIAMTX_RTSP_BASE")
    if configured:
        return _normalize_base_url(configured)
    return get_mediamtx_internal_rtsp_url()


def get_mediamtx_api_base() -> str:
    configured = _env_text("MEDIAMTX_API_URL")
    if configured:
        return _normalize_base_url(configured)

    parsed = urlparse(get_mediamtx_internal_rtsp_url())
    host = (parsed.hostname or _env_text("RELAY_RTSP_HOST", default="127.0.0.1")).strip()
    return f"http://{host}:9997"


def get_mediamtx_external_url() -> str:
    configured = _env_text("MEDIAMTX_EXTERNAL_URL")
    if configured:
        return _normalize_base_url(configured)
    return "http://127.0.0.1:8888"


def get_backend_media_root(base_dir: Path) -> Path:
    configured = _env_text("MEDIA_ROOT")
    if configured:
        return Path(configured)
    return base_dir / "media"
