import logging
import os
import threading
import time
from typing import Optional

import httpx
from ..common.runtime import get_backend_config_sync_base

logger = logging.getLogger("IdentitySelfHealer")


class IdentitySelfHealer:
    """
    Periodic self-healing daemon.
    
    To guard against lost stream messages or extended stream outages, this
    service checks the lightweight `/identity/watermark/` endpoint on a fixed
    schedule. If the backend's watermark indicates newer entities or embeddings,
    it orchestrates a targeted repair reload.
    """

    def __init__(self, entity_store, poll_interval_seconds=300):
        self._entity_store = entity_store
        self._poll_interval = poll_interval_seconds
        
        self._backend_sync_base = get_backend_config_sync_base().rstrip("/")
        
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
            
        if not self._backend_sync_base:
            logger.warning("IdentitySelfHealer cannot start: Backend URL not configured.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="IdentityHealerThread", daemon=True)
        self._thread.start()
        logger.info(f"IdentitySelfHealer thread started, ticking every {self._poll_interval}s")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _auth_headers(self):
        # We reuse the EntityStore's built-in auth helper since it shares secrets
        if hasattr(self._entity_store, "_auth_headers"):
            return self._entity_store._auth_headers()
        return {}

    def _run_loop(self):
        while not self._stop_event.is_set():
            # Wait for the interval, aborting early if stopped
            if self._stop_event.wait(self._poll_interval):
                break
                
            self._check_watermarks()

    def _check_watermarks(self):
        """Perform the high-speed watermark check against the backend."""
        headers = self._auth_headers()
        url = f"{self._backend_sync_base}/identity/watermark/"
        
        try:
            with httpx.Client(timeout=4.0) as client:
                # Assuming single-tenant or global check for the heal loop initially
                resp = client.get(url, headers=headers)
                
            if not resp.is_success:
                logger.debug(f"Watermark fetch failed: {resp.status_code}")
                return

            payload = resp.json()
            if not isinstance(payload, dict):
                return
                
            backend_version = str(payload.get("identity_version") or "").strip()
            tenant_id = payload.get("tenant_id")
            
            # Compare with the local current version
            local_version = self._entity_store.current_identity_version(tenant_id)
            
            if backend_version and local_version and backend_version != local_version:
                logger.warning(
                    f"Identity Drift Detected (tenant={tenant_id or 'global'}). "
                    f"Local: '{local_version}', Backend: '{backend_version}'. Initiating self-heal reload."
                )
                self._entity_store.reload_from_backend(force=True, tenant_id=tenant_id)
                
        except Exception as exc:
            logger.debug(f"Watermark self-heal check failed: {exc}")
