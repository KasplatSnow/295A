from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

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
    raw = _env_text(name, default="").lower()
    if not raw:
        return default
    return raw in TRUE_VALUES


def host_looks_local(hostname: str) -> bool:
    host = str(hostname or "").strip().lower()
    return host in LOCAL_HOSTS


def _normalize_base_url(raw: str) -> str:
    return str(raw or "").strip().rstrip("/")


def get_ai_api_port() -> int:
    return _env_int("AI_API_PORT", "AI_PORT", default=8001)


def get_ai_public_base_url() -> str:
    configured = _env_text("AI_PUBLIC_BASE_URL")
    if configured:
        return _normalize_base_url(configured)
    return f"http://127.0.0.1:{get_ai_api_port()}"


def get_backend_base_internal() -> str:
    configured = _env_text("BACKEND_BASE_INTERNAL")
    if configured:
        return _normalize_base_url(configured)

    host = _env_text("DJANGO_HOST", default="127.0.0.1")
    port = _env_int("DJANGO_PORT", default=8000)
    return f"http://{host}:{port}"


def get_backend_config_sync_base() -> str:
    configured = _env_text("BACKEND_CONFIG_SYNC_BASE")
    if configured:
        return _normalize_base_url(configured)
    return f"{get_backend_base_internal()}/api/ai/internal"


def get_ai_data_dir(base_dir: Path | None = None) -> Path:
    configured = _env_text("AI_DATA_DIR")
    if configured:
        return Path(configured)
    root = base_dir or Path.cwd()
    return root / "data"


def get_ai_evidence_dir(base_dir: Path | None = None) -> Path:
    configured = _env_text("AI_EVIDENCE_DIR")
    if configured:
        return Path(configured)
    root = base_dir or Path.cwd()
    return root / "evidence"


def get_ai_staging_dir(base_dir: Path | None = None) -> Path:
    configured = _env_text("AI_STAGING_DIR")
    if configured:
        return Path(configured)
    return get_ai_data_dir(base_dir) / "staging_uploads"


def get_ai_enroll_image_dir(base_dir: Path | None = None) -> Path:
    configured = _env_text("AI_ENROLL_IMAGE_DIR")
    if configured:
        return Path(configured)
    return get_ai_data_dir(base_dir) / "enroll_images"


def _sanitize_redis_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme:
        return url
    if parsed.password is None:
        return url

    username = parsed.username or ""
    auth = quote(username, safe="") if username else ""
    if auth:
        auth = f"{auth}:***"
    else:
        auth = ":***"

    netloc = auth + "@"
    if parsed.hostname:
        netloc += parsed.hostname
    if parsed.port:
        netloc += f":{parsed.port}"

    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


@dataclass(frozen=True)
class AIRedisSettings:
    url: str
    host: str
    port: int
    configured: bool
    source: str

    @property
    def connection_display(self) -> str:
        if self.url:
            return _sanitize_redis_url(self.url)
        return f"redis://{self.host}:{self.port}"


def resolve_ai_redis_settings() -> AIRedisSettings:
    redis_url = _env_text("AI_REDIS_URL", "REDIS_URL")
    redis_host = _env_text("AI_REDIS_HOST", "REDIS_HOST", default="")
    redis_port = _env_int("AI_REDIS_PORT", "REDIS_PORT", default=6379)
    configured = bool(redis_url or redis_host)
    source = "redis_url" if redis_url else "host_port" if redis_host else "defaults"

    return AIRedisSettings(
        url=redis_url,
        host=redis_host or "127.0.0.1",
        port=redis_port,
        configured=configured,
        source=source,
    )


def validate_runtime_environment() -> None:
    allow_local = env_flag("ALLOW_LOCALHOST_SERVICE_URLS", default=False)
    debug_mode = env_flag("AI_DEBUG", default=env_flag("DJANGO_DEBUG", default=False))
    strict_raw = _env_text("STRICT_SERVICE_URL_VALIDATION", default="").lower()
    strict_validation = env_flag(
        "STRICT_SERVICE_URL_VALIDATION",
        default=(not debug_mode and strict_raw not in {"0", "false", "no", "off"}),
    )

    if not strict_validation or allow_local:
        return

    problems: list[str] = []

    for env_name, raw_value, schemes in (
        ("BACKEND_BASE_INTERNAL", get_backend_base_internal(), ("http", "https")),
        ("BACKEND_CONFIG_SYNC_BASE", get_backend_config_sync_base(), ("http", "https")),
    ):
        parsed = urlparse(raw_value)
        if not parsed.scheme or parsed.scheme.lower() not in schemes:
            problems.append(f"{env_name} has an invalid URL: {raw_value}")
            continue
        if host_looks_local(parsed.hostname or ""):
            problems.append(
                f"{env_name} points to localhost ({raw_value}). "
                "Set the real backend service address or ALLOW_LOCALHOST_SERVICE_URLS=1 for single-host deployments."
            )

    redis_settings = resolve_ai_redis_settings()
    if env_flag("AI_USE_REDIS_PUBLISH", default=False) and not redis_settings.configured:
        problems.append(
            "AI_USE_REDIS_PUBLISH is enabled but AI_REDIS_URL/REDIS_URL or AI_REDIS_HOST/REDIS_HOST is missing"
        )
    elif redis_settings.configured:
        if redis_settings.url:
            parsed = urlparse(redis_settings.url)
            if host_looks_local(parsed.hostname or ""):
                problems.append(
                    f"AI_REDIS_URL/REDIS_URL points to localhost ({redis_settings.connection_display}). "
                    "Use the real Redis service address or ALLOW_LOCALHOST_SERVICE_URLS=1 for single-host deployments."
                )
        elif host_looks_local(redis_settings.host):
            problems.append(
                f"AI_REDIS_HOST/REDIS_HOST points to localhost ({redis_settings.connection_display}). "
                "Use the real Redis service address or ALLOW_LOCALHOST_SERVICE_URLS=1 for single-host deployments."
            )

    if problems:
        raise RuntimeError("AI runtime environment validation failed: " + " | ".join(problems))
