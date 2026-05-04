"""
Django management command: subscribe_incidents

Long-running process that drains the Redis reliable queue for
AI incident events and processes them into the Django database.

Usage:
    python manage.py subscribe_incidents

Configuration (environment variables):
    REDIS_HOST             — Redis host (default: 127.0.0.1)
    REDIS_PORT             — Redis port (default: 6379)
    AI_INCIDENT_CHANNEL    — Redis stream name (default: vigilzone.ai.incidents)
"""

import json
import logging
import os
import signal
import time

from django.core.management.base import BaseCommand

from api.management.commands._runtime_waits import wait_for_redis
from ai_integration.redis_queue import (
    ack_stream_event,
    build_subscriber_status,
    claim_stale_stream_events,
    create_redis_client,
    ensure_incident_consumer_group,
    publish_subscriber_status,
    read_stream_events,
)
from server.redis_runtime import resolve_backend_redis_settings

logger = logging.getLogger("subscribe_incidents")


class Command(BaseCommand):
    help = "Consume Redis AI incident queue events and ingest them into Django."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_event_id = ""
        self._last_stream_entry_id = ""
        self._last_error = ""
        self._processed_count = 0

    def add_arguments(self, parser):
        defaults = resolve_backend_redis_settings()
        parser.add_argument(
            "--channel",
            type=str,
            default=defaults.incident_channel,
            help="Redis stream name to read from",
        )
        parser.add_argument(
            "--redis-url",
            type=str,
            default=defaults.url,
            help="Redis connection URL (preferred over host/port)",
        )
        parser.add_argument(
            "--redis-host",
            type=str,
            default=defaults.host if defaults.source == "host_port" else "",
            help="Redis host",
        )
        parser.add_argument(
            "--redis-port",
            type=int,
            default=defaults.port,
            help="Redis port",
        )

    def handle(self, *args, **options):
        settings = resolve_backend_redis_settings({
            **os.environ,
            "AI_INCIDENT_CHANNEL": options["channel"],
            "REDIS_URL": options["redis_url"],
            "REDIS_HOST": options["redis_host"],
            "REDIS_PORT": str(options["redis_port"]),
        })

        self.stdout.write(
            self.style.SUCCESS(
                f"Starting incident subscriber → {settings.connection_display} "
                f"stream={settings.incident_channel} group={settings.incident_consumer_group} "
                f"consumer={settings.incident_consumer_name} mode={settings.queue_mode}"
            )
        )
        wait_for_redis(self.stdout, self.style)

        # Graceful shutdown
        self._running = True

        def _signal_handler(signum, frame):
            self.stdout.write(self.style.WARNING(f"Received signal {signum}, shutting down..."))
            self._running = False

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        while self._running:
            try:
                self._subscribe_loop(settings)
            except Exception as exc:
                self._last_error = str(exc)
                logger.error("Subscriber loop error: %s", exc)
                if self._running:
                    self.stdout.write(
                        self.style.WARNING(f"Connection lost, reconnecting in 5s: {exc}")
                    )
                    time.sleep(5)

        self.stdout.write(self.style.SUCCESS("Incident subscriber stopped."))

    def _publish_status(self, client, settings, phase: str):
        publish_subscriber_status(
            client,
            settings.incident_channel,
            build_subscriber_status(
                settings=settings,
                phase=phase,
                pid=os.getpid(),
                last_event_id=self._last_event_id,
                last_error=self._last_error,
                processed_count=self._processed_count,
                stream_entry_id=self._last_stream_entry_id,
            ),
        )

    def _subscribe_loop(self, settings):
        """Connect to Redis stream consumer group and process messages until disconnected."""
        import redis

        client = create_redis_client(settings)

        # Verify connectivity
        client.ping()
        ensure_incident_consumer_group(client, settings)
        self._last_error = ""
        self._publish_status(client, settings, phase="connected")

        self.stdout.write(
            self.style.SUCCESS(
                f"Connected to Redis stream '{settings.incident_channel}' "
                f"group={settings.incident_consumer_group} consumer={settings.incident_consumer_name}"
            )
        )
        logger.info(
            "Connected to Redis stream=%s group=%s consumer=%s target=%s",
            settings.incident_channel,
            settings.incident_consumer_group,
            settings.incident_consumer_name,
            settings.connection_display,
        )

        try:
            while self._running:
                try:
                    self._publish_status(client, settings, phase="waiting")
                    claimed = claim_stale_stream_events(
                        client,
                        settings,
                        consumer_name=settings.incident_consumer_name,
                        count=10,
                    )
                    if claimed:
                        for stream_entry_id, fields in claimed:
                            if not self._running:
                                break
                            self._consume_stream_entry(client, settings, stream_entry_id, fields)
                        continue

                    pending_entries = read_stream_events(
                        client,
                        settings,
                        consumer_name=settings.incident_consumer_name,
                        pending=True,
                        count=10,
                        block_ms=1,
                    )
                    if pending_entries:
                        for stream_entry_id, fields in pending_entries:
                            if not self._running:
                                break
                            self._consume_stream_entry(client, settings, stream_entry_id, fields)
                        continue

                    entries = read_stream_events(
                        client,
                        settings,
                        consumer_name=settings.incident_consumer_name,
                        pending=False,
                        count=10,
                        block_ms=2000,
                    )
                    for stream_entry_id, fields in entries:
                        if not self._running:
                            break
                        self._consume_stream_entry(client, settings, stream_entry_id, fields)

                except redis.exceptions.TimeoutError:
                    continue  # Expected when block timeout expires
                except redis.ConnectionError as e:
                    self._last_error = str(e)
                    logger.error("Redis connection error during queue read: %s", e)
                    break
        finally:
            try:
                self._publish_status(client, settings, phase="stopped")
                client.close()
            except Exception:
                pass

    def _consume_stream_entry(self, client, settings, stream_entry_id: str, fields: dict) -> None:
        raw_data = fields.get("payload")
        if raw_data is None:
            logger.warning("Stream entry %s missing payload field, acking", stream_entry_id)
            ack_stream_event(client, settings, stream_entry_id)
            return

        self._last_stream_entry_id = stream_entry_id
        handled = self._process_message(raw_data)
        if handled:
            ack_stream_event(client, settings, stream_entry_id)
        self._publish_status(client, settings, phase="processed")

    def _process_message(self, raw_data: str) -> bool:
        """Parse and ingest a single Redis message. Returns True if processed or safely ignored."""
        try:
            envelope = json.loads(raw_data)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Invalid JSON in Redis message: %s", exc)
            return True

        event_type = envelope.get("event", "")
        if event_type != "alert.created":
            logger.debug("Ignoring event type: %s", event_type)
            return True

        data = envelope.get("data", {})
        if not data:
            logger.warning("Empty data in alert.created event")
            return True

        event_id = str(data.get("id", "")).strip()
        if not event_id:
            logger.warning("Missing event ID in alert.created event, skipping")
            return True
        self._last_event_id = event_id

        # Import here to ensure Django ORM is ready
        from ai_integration.incident_ingest import process_alert_event

        try:
            result = process_alert_event(
                data=data,
                source="redis_queue",
                event_id=event_id,
            )
            if result.status == "duplicate":
                self._last_error = ""
                logger.debug("Duplicate event %s already processed", event_id)
            elif result.status == "error":
                self._last_error = result.error or ""
                logger.error("Ingest error for event %s: %s", event_id, result.error)
            else:
                self._processed_count += 1
                self._last_error = ""
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Ingested event {event_id} → incident #{result.incident_id} ({result.status})"
                    )
                )
            return True
        except Exception as exc:
            self._last_error = str(exc)
            logger.error("Failed to ingest event %s: %s", event_id, exc, exc_info=True)
            return False
