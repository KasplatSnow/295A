import abc
import json
import logging
import os
import time
import signal
import threading
from typing import List, Protocol

from django.db import transaction
from django.utils import timezone
import redis

from api.models import KnownEntityProcessingJob, OutboxEvent
from api.services.entity_processing_service import EntityProcessingService
from server.redis_runtime import resolve_backend_redis_settings

logger = logging.getLogger("worker_services")

class WorkerProcessor(abc.ABC):
    """SRP interface for a background task runner."""
    
    @abc.abstractmethod
    def run_once(self) -> int:
        """Execute one batch. Returns number of items processed."""
        pass

    @abc.abstractmethod
    def get_name(self) -> str:
        pass


class EntityEmbeddingProcessor(WorkerProcessor):
    """Processes queued entity enrollment/embedding jobs."""
    
    def __init__(self, limit: int = 10):
        self.limit = limit
        self.service = EntityProcessingService()

    def run_once(self) -> int:
        summary = self.service.process_queued_jobs(limit=self.limit)
        return summary.get("processed", 0)

    def get_name(self) -> str:
        return "EntityEmbeddingProcessor"


class OutboxStreamPublisherProcessor(WorkerProcessor):
    """Drains transactional outbox rows into Redis Streams with safe claiming."""
    
    def __init__(self, batch_size: int = 100, stream_name: str = "vigilzone:stream:events"):
        self.batch_size = batch_size
        self.stream_name = stream_name
        self._redis_client = None

    def _get_client(self):
        if self._redis_client is None:
            cfg = resolve_backend_redis_settings()
            if cfg.url:
                # Prioritize full URL (handles passwords and non-standard ports from .env)
                self._redis_client = redis.from_url(cfg.url, decode_responses=True)
            else:
                # Fallback to discrete settings
                pool = redis.ConnectionPool(
                    host=cfg.host,
                    port=cfg.port,
                    db=cfg.db,
                    password=cfg.password,
                    decode_responses=True,
                )
                self._redis_client = redis.Redis(connection_pool=pool)
        return self._redis_client

    def run_once(self) -> int:
        r = self._get_client()

        # Version Check: Redis Streams (XADD) require Redis 5.0+.
        info = r.info("server")
        ver_str = info.get("redis_version", "0.0.0")
        major_ver = int(ver_str.split(".")[0])
        if major_ver < 5:
            raise RuntimeError(
                f"Redis version {ver_str} is too old. Redis Streams (XADD) require version 5.0 or higher."
            )

        processed = 0
        with transaction.atomic():
            events = list(
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(published_at__isnull=True)
                .order_by("created_at")[:self.batch_size]
            )
            
            processed = 0
            for event in events:
                try:
                    actual_stream = os.getenv("AI_EVENT_STREAM", self.stream_name)
                    r.xadd(
                        actual_stream,
                        {
                            "event_id": str(event.id),
                            "event_type": event.event_type,
                            "aggregate_type": event.aggregate_type,
                            "aggregate_id": str(event.aggregate_id),
                            "payload": json.dumps(event.payload or {}),
                            "timestamp": str(event.created_at.timestamp()),
                        },
                    )
                    event.published_at = timezone.now()
                    event.save(update_fields=["published_at"])
                    processed += 1
                except Exception as e:
                    logger.error(f"Failed to publish event {event.id} to Redis: {e}")
                    break
        
        return processed

    def get_name(self) -> str:
        return "OutboxStreamPublisherProcessor"


class RelayReconcilerProcessor(WorkerProcessor):
    """Reconciles MediaMTX relay paths with event-driven wakeups."""

    def __init__(
        self,
        shadow_mode: bool = False,
        stream_name: str = "vigilzone:stream:events",
        audit_interval: float = 300.0,
    ):
        self.shadow_mode = shadow_mode
        self.stream_name = stream_name
        self.audit_interval = audit_interval
        
        self._reconciler = None
        self._redis_client = None
        self._last_stream_id = "$"
        self._last_audit_at = 0.0

    def _get_reconciler(self):
        if self._reconciler is None:
            from api.services.relay_reconciler import RelayReconciler
            self._reconciler = RelayReconciler(shadow_mode=self.shadow_mode)
        return self._reconciler

    def _get_redis(self):
        if self._redis_client is None:
            cfg = resolve_backend_redis_settings()
            if cfg.url:
                self._redis_client = redis.from_url(cfg.url, decode_responses=True)
            else:
                self._redis_client = redis.Redis(
                    host=cfg.host, port=cfg.port, db=cfg.db, password=cfg.password,
                    decode_responses=True
                )
        return self._redis_client

    def run_once(self) -> int:
        r = self._get_redis()
        reconciler = self._get_reconciler()
        now = time.time()
        processed = 0

        try:
            actual_stream = os.getenv("AI_EVENT_STREAM", self.stream_name)
            streams = r.xread({actual_stream: self._last_stream_id}, count=10, block=100)
            
            affected_paths = set()
            if streams:
                for _, messages in streams:
                    for msg_id, data in messages:
                        self._last_stream_id = msg_id
                        event_type = data.get("event_type", "")
                        
                        if event_type and event_type.startswith("mediamtx."):
                            try:
                                payload = json.loads(data.get("payload", "{}"))
                                path = payload.get("stream_path")
                                if path:
                                    affected_paths.add(path)
                            except Exception:
                                pass

            if affected_paths:
                reconciler.reconcile_paths(list(affected_paths))
                processed = len(affected_paths)
        except Exception as e:
            logger.error(f"Error reading relay events from Redis: {e}")
            time.sleep(1.0)

        if now - self._last_audit_at >= self.audit_interval:
            logger.info("Performing periodic reconciler audit (full sweep)")
            reconciler.reconcile_all()
            self._last_audit_at = now
            return 0
        
        return processed

    def get_name(self) -> str:
        mode = "shadow" if self.shadow_mode else "active"
        return f"RelayReconcilerProcessor({mode}, event-driven)"


class NotificationBackfillProcessor(WorkerProcessor):
    """Processes incident.created events for background notification tasks."""

    def __init__(self, stream_name: str = "vigilzone:stream:events"):
        self.stream_name = stream_name
        self._redis_client = None
        self._last_stream_id = "$"

    def _get_redis(self):
        if self._redis_client is None:
            cfg = resolve_backend_redis_settings()
            if cfg.url:
                self._redis_client = redis.from_url(cfg.url, decode_responses=True)
            else:
                self._redis_client = redis.Redis(
                    host=cfg.host, port=cfg.port, db=cfg.db, password=cfg.password,
                    decode_responses=True
                )
        return self._redis_client

    def run_once(self) -> int:
        r = self._get_redis()
        actual_stream = os.getenv("AI_EVENT_STREAM", self.stream_name)
        
        try:
            streams = r.xread({actual_stream: self._last_stream_id}, count=10, block=100)
            processed = 0
            
            if streams:
                from api.notification_service import NotificationService
                for _, messages in streams:
                    for msg_id, data in messages:
                        self._last_stream_id = msg_id
                        event_type = data.get("event_type", "")
                        
                        if event_type == "incident.created":
                            payload = json.loads(data.get("payload", "{}"))
                            incident_id = payload.get("incident_id")
                            if incident_id:
                                logger.info(f"Backfilling notifications for incident {incident_id}")
                                NotificationService.backfill_incident(incident_id)
                                processed += 1
            return processed
        except Exception as e:
            logger.error(f"Error in NotificationBackfillProcessor: {e}")
            time.sleep(1.0)
            return 0

    def get_name(self) -> str:
        return "NotificationBackfillProcessor"


class BaseWorkerService:
    """Orchestrates the lifecycle of a WorkerProcessor."""
    
    def __init__(self, processor: WorkerProcessor, poll_interval: float = 1.0):
        self.processor = processor
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run_forever(self):
        name = self.processor.get_name()
        logger.info(f"Starting {name} loop (poll={self.poll_interval}s)")
        
        while not self._stop_event.is_set():
            try:
                processed_count = self.processor.run_once()
                
                if processed_count == 0:
                    time.sleep(self.poll_interval)
                else:
                    time.sleep(0.01)
                    
            except Exception as e:
                logger.error(f"Unexpected error in {name}: {e}", exc_info=True)
                time.sleep(5.0)
        
        logger.info(f"{name} loop stopped.")
