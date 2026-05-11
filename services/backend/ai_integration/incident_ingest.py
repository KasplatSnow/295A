"""
Shared incident ingestion logic.

This module provides a single, reusable function that processes AI alert
events into Django Incident/Detection records and triggers real-time
WebSocket notifications — used by both:

  1. Webhook receiver (ai_integration/views.py → ai_webhook_receive)
  2. Redis subscriber (ai_integration/management/commands/subscribe_incidents.py)

This avoids duplicating camera resolution, incident creation, detection
persistence, and notification dispatch logic across two code paths.
"""

import json
import logging
import os
import time
import random
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional, Tuple

from django.db import transaction, IntegrityError, OperationalError
from django.utils import timezone

from api.models import (
    Camera, Detection, Incident, IncidentEventReceipt, Tenant,
)

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1 WS1.2: Redis Camera Context Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _get_redis_camera_context(camera_id: str) -> Optional[dict]:
    """Fetch the Redis cameractx projection for fast validation."""
    try:
        from ai_integration.redis_queue import get_redis_client
        raw = get_redis_client().get(f"cameractx:{camera_id}")
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.debug("cameractx lookup failed for %s: %s", camera_id, exc)
    return None


def _repair_camera_context(camera: Camera) -> None:
    """Rebuild a single cameractx entry from canonical DB state."""
    try:
        from ai_integration.redis_queue import get_redis_client
        cam_key = camera.ai_camera_id or camera.stream_path or str(camera.id)
        ctx_value = {
            "tenant_id": str(camera.tenant_id),
            "community_id": str(camera.tenant_id),
            "camera_name": camera.name,
            "stream_path": camera.stream_path,
            "policy_version": 1,
            "updated_at": timezone.now().isoformat(),
        }
        get_redis_client().set(f"cameractx:{cam_key}", json.dumps(ctx_value))
        logger.info("Repaired cameractx for %s", cam_key)
    except Exception as exc:
        logger.warning("cameractx repair failed for camera %s: %s", camera.id, exc)


def _queue_incident_notification(incident_id: int, event_id: str = "") -> None:
    """
    Broadcast the incident after the surrounding transaction commits.

    AI-originated incidents can be created from webhook or Redis subscriber
    processes, so we trigger the live notification explicitly here instead of
    relying on a model signal firing in a different execution path.

    Phase 1 WS1.3: Notification dispatch is decoupled from incident persistence.
    The incident is persisted first, and notification is dispatched asynchronously
    via a background thread after commit. Failure to notify does not roll back
    the incident.
    """

    def _broadcast():
        dispatch_start = time.time()
        try:
            from api.models import Incident
            from api.notification_service import NotificationService

            incident = Incident.objects.select_related("tenant", "camera").get(pk=incident_id)
            result = NotificationService.broadcast_incident(incident)
            dispatch_ms = (time.time() - dispatch_start) * 1000
            logger.info(
                "Notification dispatch completed for incident=%s event_id=%s "
                "alerts=%d ws=%s elapsed=%.1fms",
                incident_id, event_id,
                result.get("alerts_created", 0),
                result.get("websocket", "unknown"),
                dispatch_ms,
            )
        except Incident.DoesNotExist:
            logger.warning("Incident %s disappeared before notification dispatch", incident_id)
        except Exception as exc:
            logger.warning("Notification dispatch error for incident %s: %s", incident_id, exc)

    import threading
    transaction.on_commit(lambda: threading.Thread(target=_broadcast, daemon=True).start())

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Shared constants / helpers (moved from views.py module level)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INCIDENT_TYPE_MAP = {
    "fire":             Incident.Type.FIRE,
    "fire_smoke":       Incident.Type.FIRE,
    "weapon":           Incident.Type.ROBBERY,
    "weapon_detected":  Incident.Type.ROBBERY,
    "intrusion":        Incident.Type.INTRUSION,
    "intrusion_person_in_zone": Incident.Type.INTRUSION,
    "stranger":         Incident.Type.STRANGER,
    "loitering":        Incident.Type.INTRUSION,
    "abandoned_object": Incident.Type.OTHER,
    "crowd":            Incident.Type.OTHER,
    "fall":             Incident.Type.OTHER,
    "animal":           Incident.Type.OTHER,
    "anomaly":          Incident.Type.OTHER,
    "audio_anomaly":    Incident.Type.AUDIO_ANOMALY,
}

SEVERITY_MAP = {
    "critical": 5,
    "severe": 5,
    "high": 4,
    "medium": 3,
    "med": 3,
    "moderate": 3,
    "low": 2,
    "info": 1,
}

INCIDENT_ACTIVE_WINDOW_SECONDS = 60


# ── Reusable helpers ──────────────────────────────────────────────

def _resolve_tenant_hint(data: dict) -> Optional[Tenant]:
    """Resolve explicit tenant hint from payload."""
    raw_tenant_id = data.get("tenant_id")
    if raw_tenant_id is None:
        return None
    try:
        return Tenant.objects.get(pk=int(raw_tenant_id))
    except (TypeError, ValueError, Tenant.DoesNotExist):
        return None


def _resolve_camera(camera_id_str: str, tenant_hint: Optional[Tenant] = None):
    """Find Camera by AI ID, stream path, or name, avoiding ambiguous cross-tenant matches."""
    camera_id_str = (camera_id_str or "").strip()
    if not camera_id_str:
        return None, None, False

    if tenant_hint:
        cam = Camera.objects.filter(
            tenant=tenant_hint, ai_camera_id=camera_id_str,
        ).first()
        if cam:
            return cam, cam.tenant, False
        cam = Camera.objects.filter(
            tenant=tenant_hint, name=camera_id_str,
        ).first()
        if cam:
            return cam, cam.tenant, False
        cam = Camera.objects.filter(
            tenant=tenant_hint, stream_path=camera_id_str,
        ).first()
        if cam:
            return cam, cam.tenant, False
        return None, tenant_hint, False

    ai_matches = list(
        Camera.objects.filter(ai_camera_id=camera_id_str)
        .select_related("tenant")[:2]
    )
    if len(ai_matches) == 1:
        cam = ai_matches[0]
        return cam, cam.tenant, False
    if len(ai_matches) > 1:
        logger.warning("Ambiguous ai_camera_id '%s' across tenants", camera_id_str)
        return None, None, True

    stream_matches = list(
        Camera.objects.filter(stream_path=camera_id_str)
        .select_related("tenant")[:2]
    )
    if len(stream_matches) == 1:
        cam = stream_matches[0]
        return cam, cam.tenant, False
    if len(stream_matches) > 1:
        logger.warning("Ambiguous stream_path '%s' across tenants", camera_id_str)
        return None, None, True

    name_matches = list(
        Camera.objects.filter(name=camera_id_str)
        .select_related("tenant")[:2]
    )
    if len(name_matches) == 1:
        cam = name_matches[0]
        return cam, cam.tenant, False
    if len(name_matches) > 1:
        logger.warning("Ambiguous camera name '%s' across tenants", camera_id_str)
        return None, None, True

    return None, None, False


def _resolve_tenant_for_unmapped_camera(data: dict):
    """Resolve best tenant when camera is not mapped."""
    default_tenant_id = os.getenv("DEFAULT_AI_TENANT_ID")
    if default_tenant_id:
        try:
            return Tenant.objects.get(pk=int(default_tenant_id))
        except (TypeError, ValueError, Tenant.DoesNotExist):
            pass

    tenant_hint = _resolve_tenant_hint(data)
    if tenant_hint:
        return tenant_hint

    return None


def _normalize_alert_type(raw) -> str:
    text = str(raw or "other").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "fire_smoke": "fire_smoke",
        "fire": "fire",
        "weapon_detected": "weapon_detected",
        "weapon": "weapon",
        "intrusion_person_in_zone": "intrusion_person_in_zone",
        "person_zone": "intrusion_person_in_zone",
        "intrusion": "intrusion",
        "stranger": "stranger",
        "loitering": "loitering",
        "abandoned_object": "abandoned_object",
        "crowd": "crowd",
        "fall": "fall",
        "animal": "animal",
        "anomaly": "anomaly",
        "audio_anomaly": "audio_anomaly",
    }
    return aliases.get(text, text)


def _parse_severity(raw) -> int:
    if isinstance(raw, int):
        return max(1, min(5, raw))
    if isinstance(raw, str):
        return SEVERITY_MAP.get(raw.lower(), 3)
    return 3


def _parse_timestamp(raw) -> datetime:
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=dt_timezone.utc)
        except (ValueError, OSError, TypeError):
            pass
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return dt
        except (ValueError, TypeError):
            pass
    return timezone.now()


def _extract_entity_details(data: dict) -> dict:
    """Normalize entity metadata from payload variants."""
    raw_entity = data.get("entity") or data.get("identity") or {}
    if not isinstance(raw_entity, dict):
        return {}

    known_fields = {
        "id": raw_entity.get("id") or raw_entity.get("entity_id"),
        "name": raw_entity.get("name"),
        "type": raw_entity.get("type") or raw_entity.get("entity_type"),
        "kind": raw_entity.get("kind"),
        "species": raw_entity.get("species"),
        "confidence": raw_entity.get("confidence") or raw_entity.get("score"),
        "known_entity_id": raw_entity.get("known_entity_id") or raw_entity.get("db_id"),
    }
    return {k: v for k, v in known_fields.items() if v not in (None, "")}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN SHARED INGEST FUNCTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class IngestResult:
    """Result object from process_alert_event()."""
    def __init__(self, status: str, incident_id: Optional[int] = None, error: Optional[str] = None):
        self.status = status       # "created", "updated", "duplicate", "ignored", "error"
        self.incident_id = incident_id
        self.error = error

    def to_dict(self) -> dict:
        d = {"status": self.status}
        if self.incident_id: d["incident_id"] = self.incident_id
        if self.error: d["error"] = self.error
        return d


def retry_on_lock(retries=2, delay=0.1, backoff=2):
    """Decorator to retry transient database lock OperationalError."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exc = None
            current_delay = delay
            for i in range(retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    if "locked" in str(e).lower():
                        last_exc = e
                        jitter = random.uniform(0, 0.1)
                        logger.warning(
                            "Database locked (attempt %d/%d). Retrying in %.2fs...",
                            i + 1, retries, current_delay + jitter
                        )
                        time.sleep(current_delay + jitter)
                        current_delay *= backoff
                    else:
                        raise e
            raise last_exc
        return wrapper
    return decorator


@retry_on_lock(retries=2, delay=0.1)
def process_alert_event(
    data: dict,
    source: str = "webhook",
    event_id: Optional[str] = None,
) -> IngestResult:
    """
    Process a single AI alert event into Incident + Detection records.

    This is the shared core used by both webhook and Redis ingestion.

    Args:
        data:     The alert payload (the 'data' dict from the event envelope).
        source:   "webhook" or "redis" — for the idempotency receipt.
        event_id: Stable event identifier for deduplication.
                  Defaults to data["id"] if not provided.

    Returns:
        IngestResult with status and optional incident_id.
    """
    # ── Determine stable event_id for idempotency ─────────────
    if not event_id:
        event_id = str(data.get("id", "")).strip()

    # ── Idempotency check ─────────────────────────────────────
    if event_id:
        try:
            existing_receipt = IncidentEventReceipt.objects.filter(event_id=event_id).first()
            if existing_receipt:
                logger.info(
                    "Duplicate event_id=%s (already processed via %s), skipping",
                    event_id, existing_receipt.source,
                )
                return IngestResult(
                    status="duplicate",
                    incident_id=existing_receipt.incident_id,
                )
        except Exception as exc:
            logger.warning("Idempotency check failed: %s", exc)

    # ── Extract and validate fields ───────────────────────────
    camera_id_str = str(data.get("camera_id", "")).strip()
    if not camera_id_str:
        return IngestResult(status="error", error="data.camera_id is required")

    alert_type_raw = data.get("type", "other")
    alert_type = _normalize_alert_type(alert_type_raw)
    severity_raw = data.get("severity", 3)
    timestamp_raw = data.get("timestamp") or data.get("ts_utc")
    message = data.get("message", "")
    confidence = data.get("confidence")
    recognized_entity = _extract_entity_details(data)

    # ── Phase 1 WS1.1: Envelope-first resolution ──────────────
    # Trust event-carried business context first, validate via Redis
    # cameractx, fall back to DB only when needed.
    envelope_tenant_id = data.get("tenant_id")
    envelope_community_id = data.get("community_id")
    has_trusted_context = bool(envelope_tenant_id and envelope_community_id)

    if not has_trusted_context:
        logger.info(
            "Event %s missing canonical routing context (tenant_id=%s, community_id=%s). "
            "Falling back to DB resolution.",
            event_id, envelope_tenant_id, envelope_community_id,
        )

    # Validate against Redis cameractx if available
    redis_ctx = _get_redis_camera_context(camera_id_str)
    if redis_ctx and has_trusted_context:
        if str(redis_ctx.get("tenant_id")) != str(envelope_tenant_id):
            logger.warning(
                "Event %s tenant_id mismatch: envelope=%s vs cameractx=%s. "
                "Using cameractx (derived from canonical DB).",
                event_id, envelope_tenant_id, redis_ctx.get("tenant_id"),
            )
            envelope_tenant_id = redis_ctx.get("tenant_id")
            envelope_community_id = redis_ctx.get("community_id")
        logger.debug("cameractx HIT for %s", camera_id_str)
    elif not redis_ctx:
        logger.debug("cameractx MISS for %s — will repair after DB resolution", camera_id_str)

    # Resolve camera and tenant via DB
    tenant_hint = _resolve_tenant_hint(data)
    camera, tenant, ambiguous_camera = _resolve_camera(camera_id_str, tenant_hint=tenant_hint)

    if ambiguous_camera:
        return IngestResult(
            status="error",
            error=f"Ambiguous camera mapping for '{camera_id_str}'",
        )

    if not camera:
        tenant = _resolve_tenant_for_unmapped_camera(data)
        if not tenant:
            logger.warning("Rejecting event %s: No tenant resolved for camera %s", event_id, camera_id_str)
            return IngestResult(status="error", error="No tenant configured or ambiguous mapping")

        source_hint = str(data.get("source_type", "")).strip().lower()
        is_webcam = camera_id_str == "cam_live" or source_hint in {"webcam", "live_camera"}
        camera = Camera.objects.create(
            tenant=tenant,
            name=camera_id_str,
            ai_camera_id=camera_id_str,
            source_type=Camera.SourceType.WEBCAM if is_webcam else Camera.SourceType.REGISTERED,
            status=Camera.Status.ACTIVE,
        )
        logger.info("Auto-created camera '%s' for tenant '%s'", camera_id_str, tenant.name)

    incident_type = INCIDENT_TYPE_MAP.get(alert_type, Incident.Type.OTHER)
    severity = _parse_severity(severity_raw)
    alert_ts = _parse_timestamp(timestamp_raw)

    # Repair cameractx in Redis if it was missing (cache miss repair path)
    if camera and not redis_ctx:
        _repair_camera_context(camera)

    # Evidence URL (routed through Django proxy)
    evidence = data.get("evidence", {}) or {}
    keyframe = evidence.get("keyframe_path") or evidence.get("keyframe", "")
    clip = evidence.get("clip_path") or evidence.get("clip", "")
    media_key = f"/api/ai/evidence/{keyframe}" if keyframe else ""
    clip_url = f"/api/ai/evidence/{clip}" if clip else ""

    with transaction.atomic():
        # ── Active-window: reuse or create incident ───────────
        cutoff = alert_ts - timedelta(seconds=INCIDENT_ACTIVE_WINDOW_SECONDS)
        existing = (
            Incident.objects.filter(
                camera=camera,
                type=incident_type,
                status=Incident.Status.OPEN,
                started_at__gte=cutoff,
            )
            .order_by("-started_at")
            .first()
        )

        if existing:
            existing.severity = max(existing.severity, severity)
            existing.details = {
                **(existing.details or {}),
                "last_alert_id": data.get("id", ""),
                "last_message": message,
                "alert_count": (existing.details or {}).get("alert_count", 1) + 1,
            }
            if recognized_entity:
                existing.details["recognized_entity"] = recognized_entity
            if clip_url:
                existing.details["clip_url"] = clip_url
                
            # Phase 2: Ingest telemetry
            debug_info = data.get("debug", {})
            if "learned_fusion_shadow_score" in debug_info:
                existing.details["shadow_score"] = debug_info["learned_fusion_shadow_score"]
            if "audio_uncertainty" in debug_info:
                unc = debug_info["audio_uncertainty"]
                existing.details["uncertainty"] = unc.get("composite", 0.0) if isinstance(unc, dict) else float(unc)
                
            if media_key:
                existing.media_key = media_key
            existing.save(update_fields=["severity", "details", "media_key", "updated_at"])
            incident = existing
            created = False
        else:
            details_dict = {
                "ai_alert_id": data.get("id", ""),
                "message": message,
                "alert_type": alert_type,
                "alert_type_raw": alert_type_raw,
                "confidence": confidence,
                "alert_count": 1,
            }
            if recognized_entity:
                details_dict["recognized_entity"] = recognized_entity
            if clip_url:
                details_dict["clip_url"] = clip_url
                
            # Phase 2: Ingest telemetry
            debug_info = data.get("debug", {})
            if "learned_fusion_shadow_score" in debug_info:
                details_dict["shadow_score"] = debug_info["learned_fusion_shadow_score"]
            if "audio_uncertainty" in debug_info:
                unc = debug_info["audio_uncertainty"]
                details_dict["uncertainty"] = unc.get("composite", 0.0) if isinstance(unc, dict) else float(unc)

            incident = Incident(
                tenant=tenant,
                camera=camera,
                type=incident_type,
                status=Incident.Status.OPEN,
                severity=severity,
                started_at=alert_ts,
                details=details_dict,
                media_key=media_key,
            )
            incident._skip_broadcast_notification = True
            incident.save()
            created = True

        # Always store raw detection
        Detection.objects.create(
            tenant=tenant,
            camera=camera,
            ts=alert_ts,
            payload=data,
        )

        # ── Record receipt for idempotency ────────────────────
        if event_id:
            try:
                IncidentEventReceipt.objects.create(
                    event_id=event_id,
                    source=source,
                    incident=incident,
                )
            except IntegrityError:
                # Race condition: another worker already processed this event
                logger.info("Race-condition duplicate for event_id=%s", event_id)
                return IngestResult(status="duplicate", incident_id=incident.pk)

    logger.info(
        "AI %s ingest: %s incident #%s (%s, sev=%d) for camera '%s' [event_id=%s]",
        source,
        "created" if created else "updated",
        incident.pk,
        incident_type,
        severity,
        camera_id_str,
        event_id,
    )

    # ── Dispatch notifications ────────────────────────────────
    # Phase 1 WS1.3: Notification dispatch is decoupled from incident
    # persistence. The incident persists regardless of notification success.
    _queue_incident_notification(incident.pk, event_id=event_id or "")

    return IngestResult(
        status="created" if created else "updated",
        incident_id=incident.pk,
    )
