"""
Redis Streams publisher for AI incident events.

Publishes raw alert events to a dedicated Redis stream so the backend
subscriber can create Incident/Detection records and trigger real-time
WebSocket notifications.

Configuration via environment variables:
    AI_USE_REDIS_PUBLISH   — "1" to enable (default: disabled)
    AI_REDIS_HOST          — Redis host (default: 127.0.0.1)
    AI_REDIS_PORT          — Redis port (default: 6379)
    AI_INCIDENT_CHANNEL    — Redis stream name (default: vigilzone.ai.incidents)
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from ..common.runtime import resolve_ai_redis_settings

logger = logging.getLogger("RedisPublisher")

# Lazy-loaded redis module (only imported when enabled)
_redis_mod = None


def _sanitize_redis_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme or parsed.password is None:
        return url
    auth = f"{parsed.username or 'default'}:***@"
    netloc = auth + (parsed.hostname or "")
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def _get_redis():
    global _redis_mod
    if _redis_mod is None:
        import redis as _r
        _redis_mod = _r
    return _redis_mod


class IncidentRedisPublisher:
    """Publishes normalized incident events to the Redis incident stream."""

    def __init__(self):
        redis_settings = resolve_ai_redis_settings()
        self._redis_configured = redis_settings.configured
        self._url = redis_settings.url
        self._host = redis_settings.host if redis_settings.source != "defaults" else ""
        self._port = redis_settings.port if redis_settings.source != "defaults" else None
        self._channel = os.getenv("AI_INCIDENT_CHANNEL", "vigilzone.ai.incidents")
        self._client: Optional[Any] = None
        self._enabled = str(os.getenv("AI_USE_REDIS_PUBLISH", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        self._last_publish: Dict[str, Any] = {
            "ok": False,
            "event_id": "",
            "stream_entry_id": "",
            "timestamp": None,
            "stream_length": None,
            "error": "",
        }

        if self._enabled:
            if not self._redis_configured:
                logger.error(
                    "Redis incident stream publisher enabled, but Redis is not explicitly configured. "
                    "Set AI_REDIS_URL/REDIS_URL or AI_REDIS_HOST/REDIS_HOST."
                )
                return
            try:
                redis = _get_redis()
                if self._url:
                    self._client = redis.Redis.from_url(
                        self._url,
                        decode_responses=True,
                        socket_connect_timeout=5,
                        socket_timeout=5,
                        retry_on_timeout=True,
                    )
                else:
                    self._client = redis.Redis(
                        host=self._host,
                        port=self._port,
                        decode_responses=True,
                        socket_connect_timeout=5,
                        socket_timeout=5,
                        retry_on_timeout=True,
                    )
                # Test connectivity
                self._client.ping()
                connection_display = self.connection_display
                logger.info(
                    "Redis incident stream publisher CONNECTED and ENABLED → %s stream=%s",
                    connection_display,
                    self._channel,
                )
            except Exception as exc:
                logger.error("Redis publisher connection FAILED to %s: %s", self.connection_display, exc)
                self._client = None
        else:
            logger.info("Redis publisher disabled (AI_USE_REDIS_PUBLISH != 1)")

    @property
    def connection_display(self) -> str:
        return _sanitize_redis_url(self._url) or f"redis://{self._host}:{self._port}"

    @property
    def is_enabled(self) -> bool:
        return self._enabled and self._client is not None

    def status_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "ready": self.is_enabled,
            "queue_mode": "redis_stream",
            "redis": self.connection_display,
            "incident_stream": self._channel,
            "incident_channel": self._channel,
            "last_publish": dict(self._last_publish),
        }

    def publish_alert(self, alert: Dict[str, Any]) -> bool:
        """
        Publish a raw alert event to the Redis incident stream.

        Uses the alert's existing 'id' field as the stable event_id to
        support idempotent processing by the backend subscriber.

        Returns True if published successfully, False otherwise.
        """
        if not self.is_enabled:
            return False

        # Build the normalized event payload
        event_id = str(alert.get("id") or alert.get("session_id") or f"ai-{time.time()}")
        camera_id = str(alert.get("camera_id", ""))

        event = {
            "event": "alert.created",
            "timestamp": alert.get("ts_utc") or alert.get("timestamp") or time.time(),
            "data": {
                "id": event_id,
                "event_type": "incident.detected.v1",
                "camera_id": camera_id,
                "camera_name": alert.get("camera_name", ""),
                "stream_path": alert.get("stream_path", ""),
                "type": alert.get("type", "other"),
                "severity": alert.get("severity", 3),
                "message": alert.get("message", ""),
                "confidence": alert.get("confidence"),
                "evidence": alert.get("evidence", {}),
                "entity": alert.get("entity") or alert.get("identity"),
                # Trusted business context from backend registration
                "tenant_id": alert.get("tenant_id"),
                "community_id": alert.get("community_id"),
                "policy_version": alert.get("policy_version"),
                # Pass through the full raw alert for backend enrichment
                "source_type": alert.get("source_type"),
                "trace": {
                    "producer": "ai-service",
                    "schema_version": 1,
                },
            },
        }

        try:
            stream_entry_id = self._client.xadd(self._channel, {"payload": json.dumps(event)})
            stream_len = self._client.xlen(self._channel)
            self._last_publish = {
                "ok": True,
                "event_id": event_id,
                "stream_entry_id": stream_entry_id,
                "timestamp": time.time(),
                "stream_length": stream_len,
                "error": "",
            }
            logger.info(
                "Added alert to incident stream event_id=%s camera=%s stream_entry_id=%s stream_length=%d",
                event_id,
                camera_id,
                stream_entry_id,
                stream_len,
            )
            return True
        except Exception as exc:
            self._last_publish = {
                "ok": False,
                "event_id": event_id,
                "stream_entry_id": "",
                "timestamp": time.time(),
                "stream_length": None,
                "error": str(exc),
            }
            logger.error(
                "Failed to append alert to incident stream event_id=%s camera=%s: %s",
                event_id, camera_id, exc,
            )
            return False


# Module-level singleton — lazy init on first import
_publisher: Optional[IncidentRedisPublisher] = None


def get_publisher() -> IncidentRedisPublisher:
    """Get or create the module-level publisher singleton."""
    global _publisher
    if _publisher is None:
        _publisher = IncidentRedisPublisher()
    return _publisher
