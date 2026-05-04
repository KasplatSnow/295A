"""
Dedicated relay reconciler — reads desired state from Postgres,
applies to MediaMTX, and writes observed state back.

This replaces the imperative patching in views.py, signals.py,
and the apps.py startup thread.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import List

import requests as http_client

from django.db import connection, transaction
from django.utils import timezone

from api.models import Camera, MediaMTXDesiredPath, MediaMTXObservedPathState
from api.services.mediamtx_helpers import (
    build_mediamtx_path_payload,
    classify_camera_source,
    get_canonical_camera_id,
    get_mediamtx_api_base,
    hash_mediamtx_payload,
)
from api.services.runtime_registration_service import RuntimeRegistrationService

logger = logging.getLogger("relay_reconciler")

# Postgres advisory lock ID — must be unique across the application.
# Used to ensure only one reconciler runs at a time.
_ADVISORY_LOCK_ID = 900_701_001


@dataclass
class PathResult:
    stream_path: str
    action: str  # "applied", "removed", "verified", "converged", "skipped", "error"
    drift_detected: bool = False
    error: str = ""


@dataclass
class ReconcileResult:
    total: int = 0
    applied: int = 0
    removed: int = 0
    verified: int = 0
    converged: int = 0
    skipped: int = 0
    failed: int = 0
    results: List[PathResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "applied": self.applied,
            "removed": self.removed,
            "verified": self.verified,
            "converged": self.converged,
            "skipped": self.skipped,
            "failed": self.failed,
            "results": [
                {
                    "stream_path": r.stream_path,
                    "action": r.action,
                    "drift_detected": r.drift_detected,
                    "error": r.error,
                }
                for r in self.results
            ],
        }

    def merge(self, other: ReconcileResult) -> None:
        """Merge another result into this one."""
        self.total += other.total
        self.applied += other.applied
        self.removed += other.removed
        self.verified += other.verified
        self.converged += other.converged
        self.skipped += other.skipped
        self.failed += other.failed
        self.results.extend(other.results)


class RelayReconciler:
    """
    Dedicated relay reconciler worker.

    Reads MediaMTXDesiredPath rows from Postgres and drives MediaMTX
    runtime state to match. Writes observed state back for auditability.

    Three-tier reconcile logic:
        - converged: hash + generation match, recently verified → skip API
        - verified:  hash matches but stale → GET to confirm runtime, stamp
        - applied:   hash/generation mismatch → POST/PATCH + verify + stamp

    Modes:
        - active (default): apply/remove paths in MediaMTX
        - shadow: verify only, do not mutate MediaMTX
    """

    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 1.5  # seconds
    POST_MUTATION_COOLDOWN = 5.0  # seconds after any mutation

    def __init__(
        self,
        shadow_mode: bool = False,
        runtime_service: RuntimeRegistrationService | None = None,
    ):
        self.shadow_mode = shadow_mode
        self.runtime_service = runtime_service or RuntimeRegistrationService()
        self.http_session = http_client.Session()
        self.audit_interval_s = int(os.getenv("RECONCILER_AUDIT_INTERVAL_S", "60"))

    # ── Public API ───────────────────────────────────────────────

    def reconcile_all(self) -> ReconcileResult:
        """Full sweep: reconcile every MediaMTXDesiredPath row.

        Acquires a Postgres advisory lock so only one reconciler
        can run at a time (prevents races with the manual endpoint).
        """
        result = ReconcileResult()

        # Single-writer lock: skip gracefully if another reconciler is running
        # Only use advisory locks if the backend is PostgreSQL
        acquired = True
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [_ADVISORY_LOCK_ID])
                acquired = cursor.fetchone()[0]

            if not acquired:
                logger.debug("Reconciler lock held by another process, skipping sweep")
                return result

        try:
            result = self._do_reconcile_all()
        finally:
            if connection.vendor == "postgresql" and acquired:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [_ADVISORY_LOCK_ID])

        return result

    def _do_reconcile_all(self) -> ReconcileResult:
        result = ReconcileResult()
        had_mutations = False

        desired_paths = (
            MediaMTXDesiredPath.objects
            .select_related("camera")
            .all()
        )

        for desired in desired_paths:
            result.total += 1
            path_result = self.reconcile_one(desired)
            result.results.append(path_result)

            if path_result.action == "applied":
                result.applied += 1
                had_mutations = True
            elif path_result.action == "removed":
                result.removed += 1
                had_mutations = True
            elif path_result.action == "verified":
                result.verified += 1
            elif path_result.action == "converged":
                result.converged += 1
            elif path_result.action == "skipped":
                result.skipped += 1
            elif path_result.action == "error":
                result.failed += 1

        # Post-mutation cooldown: give MediaMTX time to stabilize
        if had_mutations:
            time.sleep(self.POST_MUTATION_COOLDOWN)

        return result

    def reconcile_paths(self, stream_paths: list[str]) -> ReconcileResult:
        """Reconcile a specific set of paths (usually from events).
        
        This bypasses the full-sweep lock as it's intended for fast,
        targeted updates.
        """
        result = ReconcileResult()
        if not stream_paths:
            return result

        logger.info("Partial sweep: reconciling %d specific paths", len(stream_paths))
        desired_rows = list(
            MediaMTXDesiredPath.objects.filter(stream_path__in=stream_paths).select_related("camera")
        )
        
        for desired in desired_rows:
            result.total += 1
            one_result = self.reconcile_one(desired)
            result.results.append(one_result)
            
            if one_result.action == "applied":
                result.applied += 1
            elif one_result.action == "removed":
                result.removed += 1
            elif one_result.action == "verified":
                result.verified += 1
            elif one_result.action == "converged":
                result.converged += 1
            elif one_result.action == "skipped":
                result.skipped += 1
            
            if one_result.error:
                result.failed += 1
        
        return result

    def reconcile_one(self, desired: MediaMTXDesiredPath) -> PathResult:
        """Single-path reconcile: apply/verify/remove one path."""
        stream_path = desired.stream_path

        try:
            if not desired.desired_enabled:
                return self._handle_disabled(desired)
            else:
                return self._handle_enabled(desired)
        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.error(
                "Reconcile failed for path %s: %s",
                stream_path,
                error_msg,
                exc_info=True,
            )
            self._write_observed_error(desired, error_msg)
            return PathResult(
                stream_path=stream_path,
                action="error",
                error=error_msg,
            )

    # ── Internal: enabled path handling ──────────────────────────

    def _handle_enabled(self, desired: MediaMTXDesiredPath) -> PathResult:
        """Ensure an enabled path exists in MediaMTX and matches desired config."""
        api_base = get_mediamtx_api_base()
        stream_path = desired.stream_path
        camera = desired.camera

        if not camera:
            return PathResult(
                stream_path=stream_path,
                action="skipped",
                error="No camera linked to desired path",
            )

        # ── Source-type-driven classification ────────────────────
        from api.services.mediamtx_helpers import _is_publisher_source_type

        if _is_publisher_source_type(camera):
            source_kind = "publisher"
        else:
            source_kind = desired.source_kind or classify_camera_source(camera.rtsp_url or "")

        # Persistence: only cam_live (webcam bridge) is always-on.
        # All other cameras use on-demand mode for resource efficiency.
        is_persistent = (
            camera.source_type == Camera.SourceType.WEBCAM
            and camera.ai_camera_id == "cam_live"
        )

        try:
            payload = build_mediamtx_path_payload(camera, stream_path, source_kind, persistent=is_persistent)
        except ValueError as exc:
            return PathResult(
                stream_path=stream_path,
                action="skipped",
                error=str(exc),
            )

        desired_hash = hash_mediamtx_payload(payload)

        # ── Tier 1: HOT SKIP ("converged") ────────────────────────
        # Hash + generation match AND recently verified → no API calls.
        if (
            desired.last_applied_payload_hash == desired_hash
            and desired.last_applied_generation == desired.path_generation
            and desired.last_verified_at is not None
            and (timezone.now() - desired.last_verified_at).total_seconds() < self.audit_interval_s
        ):
            return PathResult(
                stream_path=stream_path,
                action="converged",
                drift_detected=False,
            )

        # ── Tier 2: WARM VERIFY ───────────────────────────────────
        # Hash matches but verification is stale → GET to confirm runtime.
        if (
            desired.last_applied_payload_hash == desired_hash
            and desired.last_applied_generation == desired.path_generation
        ):
            current_state = self._get_runtime_path(api_base, stream_path)
            if current_state is not None and not self._detect_drift(current_state, payload, desired):
                # Runtime matches — stamp last_verified_at
                now = timezone.now()
                desired.last_verified_at = now
                desired.save(update_fields=["last_verified_at", "updated_at"])
                self._write_observed(
                    desired,
                    observed_enabled=True,
                    observed_source=current_state.get("source", ""),
                    observed_payload=current_state,
                    last_error="",
                )
                return PathResult(
                    stream_path=stream_path,
                    action="verified",
                    drift_detected=False,
                )
            # Runtime mismatch — fall through to cold apply
            logger.info(
                "Warm verify failed for %s (path %s in MediaMTX), falling through to cold apply",
                stream_path,
                "missing" if current_state is None else "drifted",
            )

        # ── Tier 3: COLD APPLY ────────────────────────────────────
        if self.shadow_mode:
            current_state = self._get_runtime_path(api_base, stream_path)
            self._write_observed(
                desired,
                observed_enabled=current_state is not None,
                observed_source=current_state.get("source", "") if current_state else "",
                observed_payload=current_state or {},
                last_error="",
            )
            logger.info(
                "[SHADOW] Would apply %s to path %s",
                "patch" if current_state else "add",
                stream_path,
            )
            return PathResult(
                stream_path=stream_path,
                action="verified",
                drift_detected=True,
            )

        # Active mode: apply changes
        # Strategy: try POST (add) first. If path already exists, fall back
        # to PATCH. This avoids the fragile DELETE+POST cycle that races
        # with MediaMTX's async config reload.
        current_state = self._get_runtime_path(api_base, stream_path)
        if current_state is not None:
            logger.info("Applying patch to MediaMTX path %s (cold apply)", stream_path)
            applied = self._apply_with_retry(api_base, stream_path, payload, current_state)
        else:
            logger.info("Adding MediaMTX path %s (cold apply)", stream_path)
            applied = self._apply_with_retry(api_base, stream_path, payload, None)

        if not applied:
            error_msg = f"Failed to apply path {stream_path} after {self.MAX_RETRIES} retries"
            self._write_observed_error(desired, error_msg)
            return PathResult(
                stream_path=stream_path,
                action="error",
                error=error_msg,
            )

        # Verify applied state
        verified_state = self._get_runtime_path(api_base, stream_path)

        now = timezone.now()
        if verified_state is not None and not self._detect_drift(verified_state, payload, desired):
            # Stamp convergence fields only if MediaMTX confirms existence and NO drift
            desired.last_applied_payload_hash = desired_hash
            desired.last_applied_generation = desired.path_generation
            desired.last_verified_at = now
            desired.save(update_fields=[
                "last_applied_payload_hash",
                "last_applied_generation",
                "last_verified_at",
                "updated_at",
            ])
        elif verified_state is not None:
            logger.warning("Applied path %s but drift still detected. Skipping stamp.", stream_path)
        else:
            logger.warning("Applied path %s but verification returned None. Skipping stamp.", stream_path)

        self._write_observed(
            desired,
            observed_enabled=verified_state is not None,
            observed_source=verified_state.get("source", "") if verified_state else "",
            observed_payload=verified_state or {},
            last_error="",
        )

        return PathResult(
            stream_path=stream_path,
            action="applied",
            drift_detected=False,
        )

    # ── Internal: disabled path handling ─────────────────────────

    def _handle_disabled(self, desired: MediaMTXDesiredPath) -> PathResult:
        """Remove a disabled path from MediaMTX."""
        api_base = get_mediamtx_api_base()
        stream_path = desired.stream_path

        current_state = self._get_runtime_path(api_base, stream_path)

        if current_state is None:
            # Already gone from MediaMTX
            self._write_observed(
                desired,
                observed_enabled=False,
                observed_source="",
                observed_payload={},
                last_error="",
            )
            # Clean up orphaned rows (camera was deleted, FK became NULL)
            if desired.camera is None:
                logger.info("Cleaning up orphaned desired path row: %s", stream_path)
                desired.delete()
            return PathResult(stream_path=stream_path, action="verified")

        if self.shadow_mode:
            logger.info("[SHADOW] Would remove disabled path %s", stream_path)
            self._write_observed(
                desired,
                observed_enabled=True,
                observed_source=current_state.get("source", ""),
                observed_payload=current_state,
                last_error="",
            )
            return PathResult(stream_path=stream_path, action="verified", drift_detected=True)

        # Active mode: remove
        removed = self._remove_with_retry(api_base, stream_path)
        if not removed:
            error_msg = f"Failed to remove path {stream_path} after {self.MAX_RETRIES} retries"
            self._write_observed_error(desired, error_msg)
            return PathResult(stream_path=stream_path, action="error", error=error_msg)

        self._write_observed(
            desired,
            observed_enabled=False,
            observed_source="",
            observed_payload={},
            last_error="",
        )

        # Clean up orphaned rows (camera was deleted, FK became NULL)
        if desired.camera is None:
            logger.info("Cleaning up orphaned desired path row: %s", stream_path)
            desired.delete()

        return PathResult(stream_path=stream_path, action="removed")

    # ── MediaMTX API interactions ────────────────────────────────

    def _config_path_exists(self, api_base: str, stream_path: str) -> bool | None:
        """Return whether a path exists in MediaMTX config without emitting 404 noise."""
        try:
            resp = self.http_session.get(
                f"{api_base}/v3/config/paths/list",
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Unexpected MediaMTX response for LIST paths: %s",
                    resp.status_code,
                )
                return None

            data = resp.json() or {}
            items = data.get("items") or []
            for item in items:
                name = str(item.get("name") or item.get("confName") or "")
                if name == stream_path:
                    return True
            return False
        except (http_client.ConnectionError, http_client.Timeout) as exc:
            logger.warning(
                "MediaMTX unreachable at %s while listing paths: %s",
                api_base, type(exc).__name__,
            )
            return None

    def _get_runtime_path(self, api_base: str, stream_path: str) -> dict | None:
        """GET current path config from MediaMTX. Returns None if not found or unreachable."""
        try:
            exists = self._config_path_exists(api_base, stream_path)
            if exists is False:
                return None

            resp = self.http_session.get(
                f"{api_base}/v3/config/paths/get/{stream_path}",
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            logger.warning(
                "Unexpected MediaMTX response for GET path %s: %s",
                stream_path,
                resp.status_code,
            )
            return None
        except (http_client.ConnectionError, http_client.Timeout) as exc:
            logger.warning(
                "MediaMTX unreachable at %s for path %s: %s",
                api_base, stream_path, type(exc).__name__,
            )
            return None

    def _apply_with_retry(
        self,
        api_base: str,
        stream_path: str,
        payload: dict,
        current_state: dict | None,
    ) -> bool:
        """Apply (add or patch) a path config with bounded retries.
        
        If POST (add) fails with 'path already exists', automatically
        falls back to PATCH for subsequent attempts.
        """
        use_patch = current_state is not None

        for attempt in range(self.MAX_RETRIES):
            try:
                if use_patch:
                    resp = self.http_session.patch(
                        f"{api_base}/v3/config/paths/patch/{stream_path}",
                        json=payload,
                        timeout=5,
                    )
                else:
                    resp = self.http_session.post(
                        f"{api_base}/v3/config/paths/add/{stream_path}",
                        json=payload,
                        timeout=5,
                    )

                if resp.status_code < 400:
                    return True

                # If POST failed with "path already exists", switch to PATCH
                if not use_patch and resp.status_code == 400:
                    err_text = resp.text.lower()
                    if "already exists" in err_text:
                        logger.info(
                            "Path %s already exists, switching to PATCH",
                            stream_path,
                        )
                        use_patch = True
                        continue  # Retry immediately with PATCH

                logger.warning(
                    "MediaMTX apply failed for %s (attempt %d): %s %s",
                    stream_path,
                    attempt + 1,
                    resp.status_code,
                    resp.text[:200],
                )
            except Exception as exc:
                logger.warning(
                    "MediaMTX apply error for %s (attempt %d): %s",
                    stream_path,
                    attempt + 1,
                    exc,
                )

            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_BACKOFF_BASE * (attempt + 1))

        return False

    def _remove_with_retry(self, api_base: str, stream_path: str) -> bool:
        """Remove a path config with bounded retries."""
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self.http_session.delete(
                    f"{api_base}/v3/config/paths/delete/{stream_path}",
                    timeout=5,
                )
                if resp.status_code in (200, 404):
                    return True

                logger.warning(
                    "MediaMTX remove failed for %s (attempt %d): %s",
                    stream_path,
                    attempt + 1,
                    resp.status_code,
                )
            except Exception as exc:
                logger.warning(
                    "MediaMTX remove error for %s (attempt %d): %s",
                    stream_path,
                    attempt + 1,
                    exc,
                )

            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_BACKOFF_BASE * (attempt + 1))

        return False

    # ── Drift detection ──────────────────────────────────────────

    def _detect_drift(
        self,
        current_state: dict | None,
        desired_payload: dict,
        desired: MediaMTXDesiredPath,
    ) -> bool:
        """Compare current MediaMTX runtime state against desired payload."""
        if current_state is None:
            return True  # Path missing entirely

        # Check key configuration fields
        fields_to_check = [
            "source",
            "sourceOnDemand",
            "runOnInit",
            "runOnDemand",
            "runOnInitRestart",
            "runOnDemandRestart",
            "runOnDemandStartTimeout",
            "runOnDemandCloseAfter",
            "sourceOnDemandStartTimeout",
            "sourceOnDemandCloseAfter",
            "sourceFingerprint",
            "rtspTransport",
        ]

        for field in fields_to_check:
            desired_val = desired_payload.get(field)
            if desired_val is None:
                continue

            current_val = current_state.get(field)

            # Normalize empty strings vs None for commands
            if field in ("runOnInit", "runOnDemand"):
                desired_val = (desired_val or "").strip()
                current_val = (current_val or "").strip()

            if desired_val != current_val:
                logger.info(
                    "Drift detected for path %s in field '%s': '%s' -> '%s'",
                    desired.stream_path,
                    field,
                    current_val,
                    desired_val,
                )
                return True

        return False

    # ── Observed state writeback ─────────────────────────────────

    def _write_observed(
        self,
        desired: MediaMTXDesiredPath,
        *,
        observed_enabled: bool | None,
        observed_source: str,
        observed_payload: dict,
        last_error: str,
    ) -> None:
        """Persist observed state and update drift/reconcile flags."""
        try:
            self.runtime_service.mark_observed_mediamtx_path(
                desired_path=desired,
                observed_enabled=observed_enabled,
                observed_source=observed_source,
                observed_payload=observed_payload,
                last_error=last_error,
            )
        except Exception as exc:
            logger.error(
                "Failed to write observed state for %s: %s",
                desired.stream_path,
                exc,
            )

    def _write_observed_error(self, desired: MediaMTXDesiredPath, error: str) -> None:
        """Write an error-only observed state update."""
        self._write_observed(
            desired,
            observed_enabled=None,
            observed_source="",
            observed_payload={},
            last_error=error,
        )
