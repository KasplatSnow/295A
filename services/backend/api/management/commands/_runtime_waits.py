from __future__ import annotations

import time

import redis
import requests as http_client

from server.redis_runtime import resolve_backend_redis_settings
from server.runtime_services import get_ai_base_url
from api.services.mediamtx_helpers import get_mediamtx_api_base


def wait_for_redis(stdout, style, sleep_seconds: float = 5.0) -> None:
    cfg = resolve_backend_redis_settings()
    display = cfg.connection_display
    stdout.write(f"Waiting for Redis at {display}...")

    while True:
        try:
            if cfg.url:
                client = redis.from_url(cfg.url, decode_responses=True)
            else:
                client = redis.Redis(
                    host=cfg.host,
                    port=cfg.port,
                    db=cfg.db,
                    password=cfg.password,
                    decode_responses=True,
                )

            client.ping()
            info = client.info("server")
            version = str(info.get("redis_version", "unknown"))
            stdout.write(style.SUCCESS(f"Redis is reachable (version {version})."))
            return
        except Exception as exc:
            stdout.write(
                style.WARNING(f"Redis not ready at {display}: {type(exc).__name__}. Retrying in {int(sleep_seconds)}s...")
            )
            time.sleep(sleep_seconds)


def wait_for_ai(stdout, style, sleep_seconds: float = 5.0) -> None:
    ai_base = get_ai_base_url()
    stdout.write(f"Waiting for AI service at {ai_base}...")

    while True:
        try:
            resp = http_client.get(f"{ai_base}/api/v1/health", timeout=3)
            if resp.status_code == 200:
                stdout.write(style.SUCCESS("AI service is reachable."))
                return
        except Exception:
            pass
        time.sleep(sleep_seconds)


def wait_for_mediamtx(stdout, style, sleep_seconds: float = 5.0) -> None:
    api_base = get_mediamtx_api_base()
    stdout.write(f"Waiting for MediaMTX at {api_base}...")

    while True:
        try:
            resp = http_client.get(f"{api_base}/v3/config/global/get", timeout=3)
            if resp.status_code == 200:
                stdout.write(style.SUCCESS("MediaMTX is reachable."))
                return
        except Exception:
            pass
        time.sleep(sleep_seconds)
