import json
import logging
import os
import threading
import time
from typing import Optional

from ..common.runtime import resolve_ai_redis_settings
from .redis_publisher import _get_redis, _sanitize_redis_url

logger = logging.getLogger("IdentityStreamSubscriber")

class IdentityStreamSubscriber:
    """
    Subscribes to the core event backbone (Redis Streams) to observe when
    entity processing succeeds on the backend. When an event arrives,
    it triggers an automatic reload of the Identity Store for the affected tenant.
    
    Advisory Fan-out Pattern:
    Every AI instance receives these advisory reload signals. It uses non-group
    XREAD starting from the 'tail' ($) at startup. Convergence correctness is
    ultimately guaranteed by the watermark self-healer.
    """

    def __init__(self, entity_store):
        self._entity_store = entity_store

        redis_settings = resolve_ai_redis_settings()
        self._redis_configured = redis_settings.configured
        self._url = redis_settings.url
        self._host = redis_settings.host
        self._port = redis_settings.port
        
        # Configure best-effort stream listening
        self._stream = os.getenv("AI_EVENT_STREAM", "vigilzone:stream:events")
        
        # We start by reading only NEW messages arriving after we connected ('$')
        # Each replica maintains its own cursor for fan-out behavior.
        self._last_id = "$" 
        
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
            
        redis_mod = _get_redis()
        if redis_mod is None:
            logger.warning("Redis library missing, IdentityStreamSubscriber cannot start.")
            return
        if not self._redis_configured:
            logger.warning(
                "IdentityStreamSubscriber cannot start: Redis is not explicitly configured. "
                "The watermark self-healer remains active."
            )
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="IdentitySubscriberThread", daemon=True)
        self._thread.start()
        logger.info(f"IdentityStreamSubscriber started (Advisory Fan-out) on {self._stream}")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _get_client(self):
        redis_mod = _get_redis()
        if self._url:
            return redis_mod.Redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=10,
                retry_on_timeout=True,
            )
        return redis_mod.Redis(
            host=self._host,
            port=self._port,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            retry_on_timeout=True,
        )

    def _run_loop(self):
        client = None
        while not self._stop_event.is_set():
            try:
                if client is None:
                    client = self._get_client()
                    client.ping()
                    logger.info("IdentityStreamSubscriber connected to Redis.")

                # Fan-out trigger: every replica reads its own stream tail
                messages = client.xread(
                    streams={self._stream: self._last_id},
                    count=10,
                    block=5000
                )

                if messages:
                    for stream_name, msg_list in messages:
                        for message_id, msg_data in msg_list:
                            self._process_message(msg_data)
                            # Update our pointer to the last message seen
                            self._last_id = message_id

            except Exception as e:
                logger.error(f"Error in advisory stream subscriber: {e}")
                client = None
                if not self._stop_event.is_set():
                    time.sleep(5.0) # Backoff
            
    def _process_message(self, msg_data):
        try:
            event_type = msg_data.get("event_type", "")
            
            # Advisory Alignment: Match exact event names emitted by the backend
            relevant_events = {
                "identity.processing_succeeded",
                "identity.entity_updated", 
                "identity.entity_removed"
            }
            
            if event_type in relevant_events:
                raw_payload = msg_data.get("payload", "{}")
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                
                tenant_id = payload.get("tenant_id")
                known_entity_id = payload.get("known_entity_id")
                
                logger.debug(f"Advisory Identity Trigger: {event_type} (tenant={tenant_id}, entity={known_entity_id})")
                
                if self._entity_store:
                    # Trigger targeted reload
                    self._entity_store.reload_from_backend(force=True, tenant_id=tenant_id)
            
        except Exception as e:
            # We log but do not block the stream loop for advisory triggers
            # as the self-healer is the source of truth for eventual consistency.
            logger.warning(f"Failed to process advisory identity event: {e}")
