"""
AI Integration views.

1. Proxy endpoints (JWT-protected)   → forward UI requests to AI service
2. Webhook receiver  (token-protected) → persist AI alerts into Django models
"""
import hashlib
import hmac
import json
import logging
import math
import os

from django.db import models, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from api.models import (
    Camera,
    KnownEntity,
    KnownEntityEmbedding,
    SchemaBootstrapState,
    ServiceWebhook,
    Tenant,
)
from api.services.ai_runtime_control_service import (
    RelayNotReadyError,
    start_ai_runtime,
    stop_ai_runtime,
)
from api.services.camera_config_service import CameraConfigService
from api.services.outbox_service import OutboxService
from api.services.runtime_registration_service import RuntimeRegistrationService
from api.services.mediamtx_helpers import (
    get_canonical_camera_id,
    get_mediamtx_loopback_url,
)
from api.services.webhook_registry_service import WebhookRegistryService
from api.views import (
    assert_member,
    assert_non_viewer,
    get_active_tenant,
)
from .proxy import proxy_request

logger = logging.getLogger(__name__)
camera_config_service = CameraConfigService()
runtime_registration_service = RuntimeRegistrationService()
webhook_registry_service = WebhookRegistryService()
outbox_service = OutboxService()


def _ai_identity_legacy_mutation_enabled() -> bool:
    token = str(os.getenv("AI_ALLOW_IDENTITY_MUTATION_COMPAT", "")).strip().lower()
    return token in {"1", "true", "yes", "on"}


def _is_ai_internal_request_authenticated(request) -> bool:
    """Accept token or HMAC auth used by existing AI webhook flows."""
    expected_token = os.getenv("AI_WEBHOOK_TOKEN", "")
    webhook_secret = os.getenv("AI_WEBHOOK_SECRET", "")

    if expected_token:
        received_token = request.headers.get("X-AI-WEBHOOK-TOKEN", "")
        if received_token == expected_token:
            return True

    if webhook_secret:
        sig_header = request.headers.get("X-Vigilzone-Signature", "")
        if sig_header.startswith("sha256="):
            received_sig = sig_header[7:]
            body_bytes = request.body if hasattr(request, "body") else b""
            if isinstance(body_bytes, memoryview):
                body_bytes = bytes(body_bytes)
            expected_sig = hmac.new(
                webhook_secret.encode(), body_bytes, hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(received_sig, expected_sig):
                return True

    return not (expected_token or webhook_secret)


def _resolve_control_camera(request, raw_camera_id):
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        return None, tenant, Response(
            {"error": "Not a member of this tenant."},
            status=status.HTTP_403_FORBIDDEN,
        )

    camera = None
    camera_token = str(raw_camera_id or "").strip()
    if not camera_token:
        return None, tenant, Response(
            {"error": "camera_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if camera_token.isdigit():
        camera = Camera.objects.filter(pk=int(camera_token), tenant=tenant).first()

    if camera is None:
        camera = Camera.objects.filter(
            tenant=tenant,
        ).filter(
            models.Q(ai_camera_id=camera_token) | models.Q(stream_path=camera_token)
        ).first()

    if camera is None:
        return None, tenant, Response(
            {"error": "Camera not found for tenant"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return camera, tenant, None


def _set_camera_runtime(camera: Camera, enabled: bool) -> dict:
    if enabled:
        result = start_ai_runtime(
            camera,
            ingest_backend="opencv",
            enabled_lanes=list(camera.enabled_lanes or ["rt_detr", "yolov8_fallback", "fire_smoke_yolo", "person_zone"]),
            sample_hz=2.0,
            policy_version=1,
        )
    else:
        result = stop_ai_runtime(
            camera,
            ingest_backend="opencv",
            enabled_lanes=list(camera.enabled_lanes or ["rt_detr", "yolov8_fallback", "fire_smoke_yolo", "person_zone"]),
            sample_hz=2.0,
            policy_version=1,
        )

    return {
        **result,
        "camera_db_id": camera.id,
        "camera_id": result["camera_id"],
        "name": camera.name,
        "stream_path": result["stream_path"],
        "loopback_rtsp_url": result["loopback_rtsp_url"],
        "running": bool(result["running"]),
        "runtime": result["runtime"],
    }


def _serialize_tenant_camera(camera: Camera) -> dict:
    """Build a tenant-authoritative camera payload for Live AI listings."""
    runtime = getattr(camera, "runtime_registration", None)
    runtime_settings = getattr(camera.tenant, "runtime_settings", None)
    camera_id = camera.ai_camera_id or camera.stream_path or f"camera_{camera.id}"

    running = None
    if runtime is not None and isinstance(runtime.observed_enabled, bool):
        running = bool(runtime.observed_enabled)
    elif runtime is not None:
        running = bool(runtime.desired_enabled)
    else:
        running = camera.status == Camera.Status.ACTIVE

    fps = None
    if runtime is not None and isinstance(runtime.observed_sample_hz, (int, float)):
        fps = float(runtime.observed_sample_hz)
    elif runtime is not None and isinstance(runtime.desired_sample_hz, (int, float)):
        fps = float(runtime.desired_sample_hz)

    runtime_identity_enabled = True
    if runtime_settings is not None:
        runtime_identity_enabled = bool(runtime_settings.identity_runtime_enabled)
    camera_identity_enabled = bool(camera.entity_detection_enabled)
    effective_entity_detection_enabled = runtime_identity_enabled and camera_identity_enabled

    enabled_lanes = list(camera.enabled_lanes or [])
    if not effective_entity_detection_enabled:
        enabled_lanes = [lane for lane in enabled_lanes if lane != "entity_identity"]

    return {
        "camera_db_id": camera.id,
        "camera_id": camera_id,
        "camera_name": camera.name,
        "stream_path": camera.stream_path or camera_id,
        "source_type": "live_camera"
        if camera.source_type == Camera.SourceType.WEBCAM
        else "rtsp",
        "status": "active" if running else "inactive",
        "running": bool(running),
        "location": camera.site or "",
        "fps": fps,
        "active_tracks": 0,
        "enabled_lanes": enabled_lanes,
        "entity_detection_enabled": camera_identity_enabled,
        "identity_runtime_enabled": runtime_identity_enabled,
        "effective_entity_detection_enabled": effective_entity_detection_enabled,
        "policy_version": runtime.desired_policy_version if runtime is not None else 1,
        "tenant_id": str(camera.tenant_id) if camera.tenant_id is not None else None,
        "community_id": str(camera.tenant_id) if camera.tenant_id is not None else None,
    }


def _build_tenant_camera_payloads(tenant: Tenant) -> list[dict]:
    camera_query = (
        Camera.objects.filter(tenant=tenant)
        .select_related("runtime_registration", "tenant__runtime_settings")
        .order_by("id")
    )
    return [_serialize_tenant_camera(camera) for camera in camera_query]


def _camera_identity_runtime_flags(camera: Camera) -> tuple[bool, bool, bool]:
    runtime_settings = getattr(camera.tenant, "runtime_settings", None)
    runtime_identity_enabled = True
    if runtime_settings is not None:
        runtime_identity_enabled = bool(runtime_settings.identity_runtime_enabled)
    camera_identity_enabled = bool(camera.entity_detection_enabled)
    effective_entity_detection_enabled = runtime_identity_enabled and camera_identity_enabled
    return camera_identity_enabled, runtime_identity_enabled, effective_entity_detection_enabled


def _normalize_embedding_vector(raw_vector, target_dim: int = 512) -> tuple[list[float], int]:
    if not isinstance(raw_vector, (list, tuple)):
        raise ValueError("vector must be a list of numbers")

    values: list[float] = []
    for value in raw_vector:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            raise ValueError("vector contains non-numeric values")

    if not values:
        raise ValueError("vector cannot be empty")

    source_dim = len(values)
    if source_dim > target_dim:
        values = values[:target_dim]
    elif source_dim < target_dim:
        values = values + [0.0] * (target_dim - source_dim)

    norm = math.sqrt(sum(component * component for component in values))
    if norm > 0.0:
        values = [component / norm for component in values]

    return values, source_dim


def _ai_category_to_backend(value: str) -> str:
    token = str(value or "").strip().upper()
    if token == "PET":
        return KnownEntity.Category.PET
    if token == "VEHICLE":
        return KnownEntity.Category.VEHICLE
    return KnownEntity.Category.PERSON


def _backend_category_to_ai(value: str) -> str:
    token = str(value or "").strip().lower()
    if token == KnownEntity.Category.PET:
        return "PET"
    if token == KnownEntity.Category.VEHICLE:
        return "VEHICLE"
    return "KNOWN_PERSON"


def _ai_role_to_group(role: str) -> str:
    token = str(role or "").strip().upper()
    if token == "NEIGHBOR":
        return KnownEntity.Group.NEIGHBOR
    return KnownEntity.Group.HOUSEHOLD


def _group_to_ai_role(group: str, category: str) -> str:
    if str(category or "").strip().lower() == KnownEntity.Category.PET:
        return "PET"
    if str(group or "").strip().lower() == KnownEntity.Group.NEIGHBOR:
        return "NEIGHBOR"
    return "VISITOR"


def _resolve_sync_tenant(raw_tenant_id) -> Tenant | None:
    try:
        if raw_tenant_id in (None, ""):
            return None
        return Tenant.objects.filter(pk=int(raw_tenant_id)).first()
    except (TypeError, ValueError):
        return None


def _resolve_entity_for_sync(*, tenant: Tenant | None, entity_id: str, known_entity_id) -> KnownEntity | None:
    if known_entity_id not in (None, ""):
        try:
            known_entity_pk = int(known_entity_id)
        except (TypeError, ValueError):
            known_entity_pk = None
        if known_entity_pk is not None:
            query = KnownEntity.objects.filter(pk=known_entity_pk)
            if tenant is not None:
                query = query.filter(tenant=tenant)
            entity = query.first()
            if entity is not None:
                return entity

    clean_entity_id = str(entity_id or "").strip()
    if not clean_entity_id:
        return None

    query = KnownEntity.objects.filter(ai_entity_id=clean_entity_id)
    if tenant is not None:
        query = query.filter(tenant=tenant)
    return query.order_by("id").first()


def _camera_tokens_for_entity(entity: KnownEntity) -> list[str]:
    tokens = []
    for camera in entity.cameras.all():
        token = (camera.ai_camera_id or "").strip() or (camera.stream_path or "").strip() or str(camera.id)
        if token:
            tokens.append(token)
    return sorted(set(tokens))


def _apply_allowed_camera_ids(entity: KnownEntity, allowed_camera_ids) -> None:
    if not isinstance(allowed_camera_ids, list):
        return

    requested = [str(item).strip() for item in allowed_camera_ids if str(item).strip()]
    if not requested:
        entity.cameras.clear()
        return

    camera_query = Camera.objects.filter(tenant=entity.tenant)
    matched = []
    for token in requested:
        camera = camera_query.filter(
            models.Q(ai_camera_id=token) | models.Q(stream_path=token)
        ).first()
        if camera is None and token.isdigit():
            camera = camera_query.filter(pk=int(token)).first()
        if camera is not None:
            matched.append(camera)

    if matched:
        unique_by_id = {camera.id: camera for camera in matched}
        entity.cameras.set([unique_by_id[key] for key in sorted(unique_by_id.keys())])
    else:
        entity.cameras.clear()


def _emit_identity_outbox_event(*, aggregate_type: str, aggregate_id, event_type: str, payload: dict) -> None:
    outbox_service.emit(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. PROXY ENDPOINTS  (JWT-protected, for the UI)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_cameras(request):
    """GET /api/ai/cameras/ → tenant-scoped camera list (backend-authoritative)."""
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        return Response(
            {"error": "Not a member of this tenant."},
            status=status.HTTP_403_FORBIDDEN,
        )

    payload = _build_tenant_camera_payloads(tenant)
    return Response(payload, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_alerts(request):
    """GET /api/ai/alerts/ → tenant-scoped alerts only."""
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        return Response(
            {"error": "Not a member of this tenant."},
            status=status.HTTP_403_FORBIDDEN,
        )

    allowed_camera_ids = set()
    for camera in Camera.objects.filter(tenant=tenant).only("id", "ai_camera_id", "stream_path"):
        if camera.ai_camera_id:
            allowed_camera_ids.add(str(camera.ai_camera_id).strip())
        if camera.stream_path:
            allowed_camera_ids.add(str(camera.stream_path).strip())
        allowed_camera_ids.add(str(camera.id))

    resp = proxy_request(request, "/api/v1/alerts")
    if resp.status_code == 404:
        resp = proxy_request(request, "/alerts")

    if resp.status_code >= 400:
        return resp

    try:
        raw = json.loads(resp.content)
    except Exception:
        return Response([], status=status.HTTP_200_OK)

    def _allowed(item: dict) -> bool:
        camera_id = str(item.get("camera_id") or "").strip()
        return bool(camera_id) and camera_id in allowed_camera_ids

    if isinstance(raw, list):
        return Response([item for item in raw if isinstance(item, dict) and _allowed(item)])

    if isinstance(raw, dict):
        alerts = raw.get("alerts")
        if isinstance(alerts, list):
            raw["alerts"] = [item for item in alerts if isinstance(item, dict) and _allowed(item)]
            return Response(raw)

    return Response([], status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_frame(request, camera_id):
    """GET /api/ai/frame/<camera_id>/ → proxy AI snapshot (binary stream)."""
    camera_id = (camera_id or "").strip()
    if not camera_id:
        return Response({"error": "camera_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        return Response(
            {"error": "Not a member of this tenant."},
            status=status.HTTP_403_FORBIDDEN,
        )

    camera = Camera.objects.filter(tenant=tenant).filter(
        models.Q(ai_camera_id=camera_id) | models.Q(stream_path=camera_id)
    ).first()
    if camera is None and camera_id.isdigit():
        camera = Camera.objects.filter(tenant=tenant, pk=int(camera_id)).first()
    if camera is None:
        return Response({"error": "Camera not found for tenant"}, status=status.HTTP_404_NOT_FOUND)

    resolved_camera_id = (camera.ai_camera_id or "").strip() or (camera.stream_path or "").strip() or str(camera.id)

    resp = proxy_request(request, f"/frame/{resolved_camera_id}", stream=True)
    if resp.status_code == 404:
        resp = proxy_request(
            request, f"/api/v1/cameras/{resolved_camera_id}/snapshot", stream=True
        )
    return resp


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_system_status(request):
    """GET /api/ai/system/status/ → proxy to AI system status."""
    return proxy_request(request, "/api/v1/system/status")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_start(request):
    """POST /api/ai/start/ → authorize tenant camera and start AI runtime."""
    camera, tenant, error_response = _resolve_control_camera(
        request,
        request.data.get("camera_id"),
    )
    if error_response is not None:
        return error_response

    assert_non_viewer(request, tenant)

    try:
        payload = _set_camera_runtime(camera, enabled=True)
    except RelayNotReadyError as exc:
        logger.info("AI start deferred for camera %s: %s", camera.id, exc.detail)
        return Response(
            {
                "error": exc.detail,
                "code": exc.code,
                "retryable": exc.retryable,
                "waited_seconds": exc.waited_seconds,
            },
            status=exc.status_code,
        )
    except Exception as exc:
        logger.warning("Failed to start AI runtime for camera %s: %s", camera.id, exc)
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    payload["status"] = "started"
    return Response(payload, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_stop(request):
    """POST /api/ai/stop/ → authorize tenant camera and stop AI runtime."""
    camera, tenant, error_response = _resolve_control_camera(
        request,
        request.data.get("camera_id"),
    )
    if error_response is not None:
        return error_response

    assert_non_viewer(request, tenant)

    try:
        payload = _set_camera_runtime(camera, enabled=False)
    except RelayNotReadyError as exc:
        logger.info("AI stop deferred for camera %s: %s", camera.id, exc.detail)
        return Response(
            {
                "error": exc.detail,
                "code": exc.code,
                "retryable": exc.retryable,
                "waited_seconds": exc.waited_seconds,
            },
            status=exc.status_code,
        )
    except Exception as exc:
        logger.warning("Failed to stop AI runtime for camera %s: %s", camera.id, exc)
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    payload["status"] = "stopped"
    return Response(payload, status=status.HTTP_200_OK)


def _deprecation_headers() -> dict:
    """Standard deprecation response headers for legacy AI enrollment proxies."""
    return {
        "Deprecation": "true",
        "Sunset": "2026-07-01",
        "X-Deprecation-Notice": (
            "This endpoint proxies to legacy AI enrollment. "
            "Use POST /api/entities/ (backend-owned) instead."
        ),
    }


def _proxy_with_deprecation(request, ai_path, *, method_label="enrollment"):
    """Proxy request to AI with deprecation headers and warning log."""
    logger.warning(
        "DEPRECATION: Legacy AI %s proxy called: %s %s → %s",
        method_label, request.method, request.path, ai_path,
    )
    resp = proxy_request(request, ai_path)
    for key, value in _deprecation_headers().items():
        resp[key] = value
    return resp


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def ai_entities(request):
    """
    GET  /api/ai/entities/        → list entities from AI
    POST /api/ai/entities/        → enroll entity (multipart images)

    DEPRECATED for POST: Use POST /api/entities/ (backend-owned) instead.
    """
    if request.method == "POST":
        return _proxy_with_deprecation(request, "/entities", method_label="entity_create")
    return proxy_request(request, "/entities")


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def ai_entity_detail(request, entity_id):
    """
    PUT    /api/ai/entities/<entity_id>/ → update AI entity metadata.
    DELETE /api/ai/entities/<entity_id>/ → remove entity.

    DEPRECATED: Use PUT/DELETE /api/entities/{id}/ (backend-owned) instead.
    """
    return _proxy_with_deprecation(
        request, f"/entities/{entity_id}",
        method_label="entity_detail",
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_entity_images(request, entity_id):
    """GET /api/ai/entities/<entity_id>/images/ → list enrollment images."""
    return proxy_request(request, f"/entities/{entity_id}/images")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_entity_enroll_person(request):
    """POST /api/ai/entities/enroll_person/ → enroll person (multipart).

    DEPRECATED: Use POST /api/entities/ (backend-owned) instead.
    """
    return _proxy_with_deprecation(request, "/entities/enroll_person", method_label="enroll_person")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_entity_enroll_pet(request):
    """POST /api/ai/entities/enroll_pet/ → enroll pet (multipart).

    DEPRECATED: Use POST /api/entities/ (backend-owned) instead.
    """
    return _proxy_with_deprecation(request, "/entities/enroll_pet", method_label="enroll_pet")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_entity_enroll_person_upload(request):
    """POST /api/ai/entities/enroll_person_from_upload/ → enroll from staged images.

    DEPRECATED: Use POST /api/entities/ (backend-owned) instead.
    """
    return _proxy_with_deprecation(request, "/entities/enroll_person_from_upload", method_label="enroll_person_upload")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_entity_enroll_pet_upload(request):
    """POST /api/ai/entities/enroll_pet_from_upload/ → enroll from staged images.

    DEPRECATED: Use POST /api/entities/ (backend-owned) instead.
    """
    return _proxy_with_deprecation(request, "/entities/enroll_pet_from_upload", method_label="enroll_pet_upload")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_upload_enroll_images(request):
    """POST /api/ai/uploads/enroll_images/ → stage images for preview.

    DEPRECATED: Use POST /api/entities/ with multipart files instead.
    """
    return _proxy_with_deprecation(request, "/uploads/enroll_images", method_label="upload_enroll_images")


# ── Webhook registration ─────────────────────────────────────


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_webhooks_register(request):
    """POST /api/ai/webhooks/register/ → register webhook in AI service."""
    return proxy_request(request, "/webhooks")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_webhooks_list(request):
    """GET /api/ai/webhooks/ → list registered webhooks."""
    return proxy_request(request, "/webhooks")


@api_view(["GET"])
@permission_classes([AllowAny])
def ai_internal_webhooks_snapshot(request):
    """
    GET /api/ai/internal/webhooks/snapshot/

    Canonical webhook snapshot used by AI for read-cutover.
    Returns webhook registry rows currently stored in backend tables.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    tenant = None
    tenant_id = request.query_params.get("tenant_id")
    try:
        if tenant_id is not None:
            tenant = Tenant.objects.filter(pk=int(tenant_id)).first()
    except (TypeError, ValueError):
        tenant = None

    webhooks = webhook_registry_service.list_webhooks(tenant=tenant)
    payload = {}
    for webhook in webhooks:
        payload[webhook.webhook_id] = {
            "id": webhook.webhook_id,
            "url": webhook.url,
            "events": list(webhook.events or []),
            "active": bool(webhook.active),
            "metadata": webhook.metadata or {},
            "delivery_stats": webhook.delivery_stats or {},
            "has_secret": bool(webhook.has_secret),
            "source": webhook.source,
        }

    return Response({"webhooks": payload, "count": len(payload)}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([AllowAny])
def ai_internal_cameras_snapshot(request):
    """
    GET /api/ai/internal/cameras/snapshot/

    Canonical camera+zone snapshot used by AI for startup read-cutover.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    tenant = None
    tenant_id = request.query_params.get("tenant_id")
    try:
        if tenant_id is not None:
            tenant = Tenant.objects.filter(pk=int(tenant_id)).first()
    except (TypeError, ValueError):
        tenant = None

    camera_query = (
        Camera.objects.select_related("tenant", "tenant__runtime_settings", "runtime_registration", "mediamtx_desired_path")
        .prefetch_related("zones")
        .order_by("id")
    )
    if tenant is not None:
        camera_query = camera_query.filter(tenant=tenant)

    cameras_payload = []
    zones_payload = {}

    for camera in camera_query:
        runtime = getattr(camera, "runtime_registration", None)
        desired_path = getattr(camera, "mediamtx_desired_path", None)

        camera_id = camera.ai_camera_id or camera.stream_path or f"camera_{camera.id}"
        enabled = bool(runtime.desired_enabled) if runtime is not None else camera.status == Camera.Status.ACTIVE
        ingest_backend = (
            runtime.desired_ingest_backend
            if runtime is not None and runtime.desired_ingest_backend
            else "live_camera"
            if camera.source_type == Camera.SourceType.WEBCAM
            else "opencv"
        )
        sample_hz = (
            float(runtime.desired_sample_hz)
            if runtime is not None and isinstance(runtime.desired_sample_hz, (float, int))
            else 2.0
        )
        enabled_lanes = list(runtime.desired_lanes or []) if runtime is not None else list(camera.enabled_lanes or [])
        camera_identity_enabled, runtime_identity_enabled, effective_entity_detection_enabled = _camera_identity_runtime_flags(camera)
        if not effective_entity_detection_enabled:
            enabled_lanes = [lane for lane in enabled_lanes if lane != "entity_identity"]
        from api.services.mediamtx_helpers import get_mediamtx_loopback_url
        effective_path = camera.stream_path or camera.ai_camera_id or f"camera_{camera.id}"
        rtsp_url = get_mediamtx_loopback_url(effective_path)

        cameras_payload.append(
            {
                "camera_id": camera_id,
                "camera_name": camera.name,
                "stream_path": camera.stream_path or camera_id,
                "source_type": "live_camera"
                if camera.source_type == Camera.SourceType.WEBCAM
                else "rtsp",
                "rtsp_url": rtsp_url,
                "enabled": enabled,
                "ingest_backend": ingest_backend,
                "sample_hz": sample_hz,
                "enabled_lanes": enabled_lanes,
                "entity_detection_enabled": camera_identity_enabled,
                "audio_enabled": bool(camera.audio_enabled),
                "identity_runtime_enabled": runtime_identity_enabled,
                "effective_entity_detection_enabled": effective_entity_detection_enabled,
                "k_of_n": [camera.k_of_n_k, camera.k_of_n_n],
                "cooldown_s": camera.cooldown_s,
                "policy_version": runtime.desired_policy_version if runtime is not None else 1,
                "tenant_id": str(camera.tenant_id) if camera.tenant_id is not None else None,
                "community_id": str(camera.tenant_id) if camera.tenant_id is not None else None,
            }
        )

        zone_rows = []
        for zone in camera.zones.all():
            zone_rows.append(
                {
                    "name": zone.zone_name,
                    "type": zone.zone_type,
                    "points": zone.polygon_points or [],
                    "enabled": bool(zone.enabled),
                }
            )
        zones_payload[camera_id] = zone_rows

    return Response(
        {
            "cameras": cameras_payload,
            "zones": zones_payload,
            "count": len(cameras_payload),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def ai_internal_policy_snapshot(request):
    """
    GET /api/ai/internal/policy/snapshot/

    Canonical identity policy snapshot used by AI startup read-cutover.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    global_key = "ai.policy.snapshot.v1"
    selected_key = global_key

    tenant_id = request.query_params.get("tenant_id")
    try:
        if tenant_id is not None:
            selected_key = f"ai.policy.snapshot.tenant.{int(tenant_id)}.v1"
    except (TypeError, ValueError):
        selected_key = global_key

    row = SchemaBootstrapState.objects.filter(key=selected_key).first()
    if row is None and selected_key != global_key:
        row = SchemaBootstrapState.objects.filter(key=global_key).first()

    if row is None:
        return Response(
            {"error": "Canonical policy snapshot not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    payload = row.value if isinstance(row.value, dict) else {}
    policy = payload.get("policy", payload)
    if not isinstance(policy, dict):
        policy = {}

    return Response(
        {
            "policy": policy,
            "version": int(payload.get("version") or 1),
            "key": row.key,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def ai_internal_sync_webhooks(request):
    """
    POST /api/ai/internal/webhooks/sync/

    Best-effort dual-write endpoint used by AI to mirror webhook registry
    into canonical backend tables while local JSON is still active.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    payload = request.data
    webhooks = payload.get("webhooks", payload)
    if not isinstance(webhooks, dict):
        return Response(
            {"error": "Invalid payload. Expected object with webhook map."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    tenant = None
    tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
    try:
        if tenant_id is not None:
            tenant = Tenant.objects.filter(pk=int(tenant_id)).first()
    except (TypeError, ValueError):
        tenant = None

    synced = webhook_registry_service.sync_from_ai_registry(webhooks=webhooks, tenant=tenant)
    return Response({"synced": synced}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def ai_internal_sync_runtime(request):
    """
    POST /api/ai/internal/runtime/sync/

    Mirrors camera runtime desired/observed state into canonical tables.
    Legacy AI local runtime files remain untouched during migration.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    payload = request.data if isinstance(request.data, dict) else {}
    camera_id = str(payload.get("camera_id", "")).strip()
    if not camera_id:
        return Response(
            {"error": "camera_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    tenant = None
    tenant_id = payload.get("tenant_id")
    try:
        if tenant_id is not None:
            tenant = Tenant.objects.filter(pk=int(tenant_id)).first()
    except (TypeError, ValueError):
        tenant = None

    camera_query = Camera.objects.all()

    camera = camera_query.filter(
        models.Q(ai_camera_id=camera_id) | models.Q(stream_path=camera_id) | models.Q(name=camera_id)
    ).first()

    if camera is None and tenant is not None:
        camera = camera_config_service.create_camera(
            tenant=tenant,
            attrs={
                "name": payload.get("camera_name") or camera_id,
                "ai_camera_id": camera_id,
                "stream_path": payload.get("stream_path") or camera_id,
                "rtsp_url": payload.get("rtsp_url") or "",
                "source_type": Camera.SourceType.WEBCAM
                if str(payload.get("source_type") or "").lower() in {"webcam", "live_camera"}
                else Camera.SourceType.REGISTERED,
            },
        )

    if camera is None:
        return Response(
            {"error": f"Camera '{camera_id}' not found and tenant hint missing"},
            status=status.HTTP_404_NOT_FOUND,
        )

    update_attrs = {}
    for key in ("camera_name", "stream_path", "rtsp_url"):
        if payload.get(key) not in (None, ""):
            model_key = "name" if key == "camera_name" else key
            update_attrs[model_key] = payload.get(key)
    if isinstance(payload.get("entity_detection_enabled"), bool):
        update_attrs["entity_detection_enabled"] = bool(payload.get("entity_detection_enabled"))
    if payload.get("source_type") in {Camera.SourceType.WEBCAM, Camera.SourceType.REGISTERED}:
        update_attrs["source_type"] = payload.get("source_type")
    if update_attrs:
        camera = camera_config_service.update_camera(camera=camera, attrs=update_attrs)

    if isinstance(payload.get("identity_runtime_enabled"), bool) and camera.tenant is not None:
        runtime_registration_service.set_identity_runtime_enabled(
            tenant=camera.tenant,
            enabled=bool(payload.get("identity_runtime_enabled")),
        )

    lanes = payload.get("enabled_lanes") or camera.enabled_lanes or []
    sample_hz = payload.get("sample_hz")
    if not isinstance(sample_hz, (float, int)):
        sample_hz = 2.0

    runtime_registration_service.register_ai_camera_desired_state(
        camera=camera,
        enabled=bool(payload.get("enabled", True)),
        ingest_backend=str(payload.get("ingest_backend") or "opencv"),
        sample_hz=float(sample_hz),
        lanes=list(lanes),
        policy_version=int(payload.get("policy_version") or 1),
        metadata={
            "tenant_id": payload.get("tenant_id") or camera.tenant_id,
            "community_id": payload.get("community_id") or camera.tenant_id,
            "camera_name": payload.get("camera_name") or camera.name,
            "stream_path": payload.get("stream_path") or camera.stream_path,
        },
    )

    if payload.get("rtsp_url") not in (None, ""):
        runtime_registration_service.set_desired_mediamtx_path(
            camera=camera,
            stream_path=camera.stream_path or camera.ai_camera_id or camera_id,
            source_uri=str(payload.get("rtsp_url")),
            source_kind=camera.source_kind,
            desired_enabled=bool(payload.get("enabled", True)),
            relay_mode="relay_only",
            transcode_required=False,
        )

    runtime_registration_service.mark_ai_camera_observed_state(
        camera=camera,
        running=payload.get("running") if isinstance(payload.get("running"), bool) else None,
        ingest_backend=str(payload.get("ingest_backend") or ""),
        sample_hz=float(sample_hz),
        lanes=list(lanes),
        error=str(payload.get("error") or ""),
    )

    return Response(
        {"synced": True, "camera_id": camera.ai_camera_id or camera.stream_path, "camera_db_id": camera.id},
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def ai_internal_identity_snapshot(request):
    """
    GET /api/ai/internal/identity/snapshot/

    Canonical identity entity+embedding snapshot used by AI matcher.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    tenant = _resolve_sync_tenant(request.query_params.get("tenant_id"))
    snapshot_camera = None
    camera_token = str(request.query_params.get("camera_id") or "").strip()
    if camera_token:
        camera_query = Camera.objects.select_related("tenant", "tenant__runtime_settings")
        if tenant is not None:
            camera_query = camera_query.filter(tenant=tenant)

        snapshot_camera = camera_query.filter(
            models.Q(ai_camera_id=camera_token) | models.Q(stream_path=camera_token)
        ).first()
        if snapshot_camera is None and camera_token.isdigit():
            snapshot_camera = camera_query.filter(pk=int(camera_token)).first()

        if snapshot_camera is None:
            return Response(
                {"error": f"Camera '{camera_token}' not found for identity snapshot"},
                status=status.HTTP_404_NOT_FOUND,
            )

        _, _, effective_entity_detection_enabled = _camera_identity_runtime_flags(snapshot_camera)
        if not effective_entity_detection_enabled:
            return Response(
                {
                    "entities": [],
                    "embeddings": [],
                    "count_entities": 0,
                    "count_embeddings": 0,
                    "identity_version": f"camera_disabled:{camera_token}:0:0",
                },
                status=status.HTTP_200_OK,
            )

    entity_query = (
        KnownEntity.objects.select_related("tenant", "last_camera")
        .prefetch_related("cameras", "embeddings")
        .filter(
            status=KnownEntity.Status.READY,
            detection_enabled=True,
            deleted_at__isnull=True,
        )
        .order_by("id")
    )
    if tenant is not None:
        entity_query = entity_query.filter(tenant=tenant)
    if snapshot_camera is not None:
        entity_query = entity_query.filter(
            models.Q(cameras__isnull=True) | models.Q(cameras=snapshot_camera)
        ).distinct()

    entities_payload = []
    embeddings_payload = []
    latest_changed_at_iso = ""

    for entity in entity_query:
        ai_entity_id = str(entity.ai_entity_id or "").strip()
        if not ai_entity_id:
            continue

        if entity.updated_at is not None:
            entity_updated_iso = entity.updated_at.isoformat()
            if entity_updated_iso > latest_changed_at_iso:
                latest_changed_at_iso = entity_updated_iso

        metadata = {
            "allowed_camera_ids": _camera_tokens_for_entity(entity),
        }
        if entity.last_seen is not None:
            metadata["last_seen"] = entity.last_seen.isoformat()
        if entity.last_camera is not None:
            metadata["last_camera_id"] = (
                (entity.last_camera.ai_camera_id or "").strip()
                or (entity.last_camera.stream_path or "").strip()
                or str(entity.last_camera_id)
            )

        entities_payload.append(
            {
                "entity_id": ai_entity_id,
                "known_entity_id": entity.id,
                "name": entity.name,
                "category": _backend_category_to_ai(entity.category),
                "role": _group_to_ai_role(entity.group, entity.category),
                "status": entity.status,
                "detection_enabled": bool(entity.detection_enabled),
                "embedding_version": int(entity.embedding_version or 1),
                "tenant_id": str(entity.tenant_id),
                "metadata": metadata,
            }
        )

        for embedding in entity.embeddings.filter(is_active=True, deleted_at__isnull=True):
            if embedding.vector is None:
                continue
            vector_values = [float(value) for value in list(embedding.vector)]
            embeddings_payload.append(
                {
                    "entity_id": ai_entity_id,
                    "known_entity_id": entity.id,
                    "tenant_id": str(entity.tenant_id),
                    "modality": embedding.modality,
                    "vector": vector_values,
                    "source_dim": int(embedding.source_dim or len(vector_values)),
                    "metadata": embedding.metadata or {},
                }
            )

            if embedding.updated_at is not None:
                embedding_updated_iso = embedding.updated_at.isoformat()
                if embedding_updated_iso > latest_changed_at_iso:
                    latest_changed_at_iso = embedding_updated_iso

    if latest_changed_at_iso:
        identity_version = f"{latest_changed_at_iso}:{len(entities_payload)}:{len(embeddings_payload)}"
    else:
        identity_version = f"empty:{len(entities_payload)}:{len(embeddings_payload)}"

    return Response(
        {
            "entities": entities_payload,
            "embeddings": embeddings_payload,
            "count_entities": len(entities_payload),
            "count_embeddings": len(embeddings_payload),
            "identity_version": identity_version,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def ai_internal_identity_watermark(request):
    """
    GET /api/ai/internal/identity/watermark/
    
    Fast version check for identity self-healing.
    Returns the same identity_version format as snapshot, but without loading payloads.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    tenant = _resolve_sync_tenant(request.query_params.get("tenant_id"))
    
    entity_query = KnownEntity.objects.filter(
        status=KnownEntity.Status.READY,
        detection_enabled=True,
        deleted_at__isnull=True,
    )
    if tenant is not None:
        entity_query = entity_query.filter(tenant=tenant)
        
    counts = entity_query.aggregate(
        max_entity_updated=models.Max('updated_at'),
        count_entities=models.Count('id')
    )
    
    embedding_query = KnownEntityEmbedding.objects.filter(
        entity__in=entity_query,
        is_active=True,
        deleted_at__isnull=True,
        vector__isnull=False
    )
    
    emb_counts = embedding_query.aggregate(
        max_embedding_updated=models.Max('updated_at'),
        count_embeddings=models.Count('id')
    )
    
    max_e = counts['max_entity_updated']
    max_emb = emb_counts['max_embedding_updated']
    
    latest_iso = ""
    if max_e:
        latest_iso = max(latest_iso, max_e.isoformat())
    if max_emb:
        latest_iso = max(latest_iso, max_emb.isoformat())
        
    if latest_iso:
        identity_version = f"{latest_iso}:{counts['count_entities']}:{emb_counts['count_embeddings']}"
    else:
        identity_version = f"empty:{counts['count_entities']}:{emb_counts['count_embeddings']}"
        
    return Response({
        "identity_version": identity_version,
        "tenant_id": str(tenant.id) if tenant else None
    }, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def ai_internal_sync_identity(request):
    """
    POST /api/ai/internal/identity/sync/

    Canonical identity sync endpoint used by AI to mirror entity and
    embedding changes into backend Postgres pgvector storage.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    payload = request.data if isinstance(request.data, dict) else {}
    op = str(payload.get("op") or "").strip().lower()
    tenant = _resolve_sync_tenant(payload.get("tenant_id"))
    entity_id = str(payload.get("entity_id") or "").strip()
    known_entity_id = payload.get("known_entity_id")

    if op == "upsert_entity":
        logger.info(
            "DEPRECATION: AI-driven upsert_entity via internal sync is a legacy compat path. "
            "Canonical entity creation should use the backend KnownEntityViewSet. "
            "entity_id=%s tenant_id=%s",
            entity_id, payload.get("tenant_id"),
        )
        if not entity_id:
            return Response({"error": "entity_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if tenant is None and known_entity_id in (None, ""):
            return Response(
                {"error": "tenant_id is required when known_entity_id is not provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            entity = _resolve_entity_for_sync(
                tenant=tenant,
                entity_id=entity_id,
                known_entity_id=known_entity_id,
            )

            name = str(payload.get("name") or "").strip() or entity_id
            category = _ai_category_to_backend(payload.get("category"))
            group = _ai_role_to_group(payload.get("role"))

            created = False
            dirty_fields: list[str] = []
            if entity is None:
                if not _ai_identity_legacy_mutation_enabled():
                    return Response(
                        {
                            "error": (
                                "AI-driven entity creation via internal sync is deprecated. "
                                "Create canonical entities in backend first and pass known_entity_id."
                            )
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                if tenant is None:
                    return Response(
                        {"error": "Unable to resolve tenant for new entity"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                entity = KnownEntity.objects.create(
                    tenant=tenant,
                    name=name,
                    category=category,
                    group=group,
                    ai_entity_id=entity_id,
                )
                created = True
            else:
                if entity.ai_entity_id != entity_id:
                    entity.ai_entity_id = entity_id
                    dirty_fields.append("ai_entity_id")
                if name and entity.name != name:
                    entity.name = name
                    dirty_fields.append("name")
                if entity.category != category:
                    entity.category = category
                    dirty_fields.append("category")
                if entity.group != group:
                    entity.group = group
                    dirty_fields.append("group")
                if dirty_fields:
                    entity.save(update_fields=dirty_fields + ["updated_at"])

            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            allowed_camera_ids_updated = "allowed_camera_ids" in metadata
            if allowed_camera_ids_updated:
                _apply_allowed_camera_ids(entity, metadata.get("allowed_camera_ids"))

            _emit_identity_outbox_event(
                aggregate_type="known_entity",
                aggregate_id=entity.id,
                event_type="identity.entity_created" if created else "identity.entity_updated",
                payload={
                    "tenant_id": entity.tenant_id,
                    "known_entity_id": entity.id,
                    "ai_entity_id": entity.ai_entity_id,
                    "created": created,
                    "updated_fields": sorted(dirty_fields),
                    "allowed_camera_ids_updated": allowed_camera_ids_updated,
                },
            )

        return Response(
            {
                "synced": True,
                "created": created,
                "known_entity_id": entity.id,
                "entity_id": entity.ai_entity_id,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    if op == "add_embedding":
        logger.info(
            "DEPRECATION: AI-driven add_embedding via internal sync is a legacy compat path. "
            "Canonical embedding persistence should use backend-owned processing. "
            "entity_id=%s modality=%s",
            entity_id, payload.get("modality"),
        )
        entity = _resolve_entity_for_sync(
            tenant=tenant,
            entity_id=entity_id,
            known_entity_id=known_entity_id,
        )
        if entity is None:
            return Response(
                {"error": "Entity not found for embedding sync"},
                status=status.HTTP_404_NOT_FOUND,
            )

        modality = str(payload.get("modality") or "").strip().lower()
        if modality == "pet":
            modality = KnownEntityEmbedding.Modality.PET_CLIP
        if modality not in {
            KnownEntityEmbedding.Modality.FACE,
            KnownEntityEmbedding.Modality.PET_CLIP,
        }:
            return Response({"error": "Unsupported modality"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            vector, source_dim = _normalize_embedding_vector(payload.get("vector"), target_dim=512)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            embedding = KnownEntityEmbedding.objects.create(
                tenant=entity.tenant,
                entity=entity,
                modality=modality,
                vector=vector,
                source_dim=source_dim,
                generated_by="ai_enrollment",
                metadata=metadata,
            )

            _emit_identity_outbox_event(
                aggregate_type="known_entity_embedding",
                aggregate_id=embedding.id,
                event_type="identity.embedding_added",
                payload={
                    "tenant_id": entity.tenant_id,
                    "known_entity_id": entity.id,
                    "ai_entity_id": entity.ai_entity_id,
                    "embedding_id": embedding.id,
                    "modality": modality,
                    "source_dim": source_dim,
                },
            )

        return Response(
            {
                "synced": True,
                "embedding_id": embedding.id,
                "known_entity_id": entity.id,
                "entity_id": entity.ai_entity_id,
                "modality": modality,
                "source_dim": source_dim,
            },
            status=status.HTTP_201_CREATED,
        )

    if op == "update_entity":
        entity = _resolve_entity_for_sync(
            tenant=tenant,
            entity_id=entity_id,
            known_entity_id=known_entity_id,
        )
        if entity is None:
            return Response({"error": "Entity not found"}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            dirty_fields = []
            name = payload.get("name")
            if isinstance(name, str) and name.strip() and entity.name != name.strip():
                entity.name = name.strip()
                dirty_fields.append("name")

            category_raw = payload.get("category")
            if category_raw not in (None, ""):
                category = _ai_category_to_backend(category_raw)
                if entity.category != category:
                    entity.category = category
                    dirty_fields.append("category")

            role_raw = payload.get("role")
            if role_raw not in (None, ""):
                group = _ai_role_to_group(role_raw)
                if entity.group != group:
                    entity.group = group
                    dirty_fields.append("group")

            if dirty_fields:
                entity.save(update_fields=dirty_fields + ["updated_at"])

            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            allowed_camera_ids_updated = "allowed_camera_ids" in metadata
            if allowed_camera_ids_updated:
                _apply_allowed_camera_ids(entity, metadata.get("allowed_camera_ids"))

            _emit_identity_outbox_event(
                aggregate_type="known_entity",
                aggregate_id=entity.id,
                event_type="identity.entity_updated",
                payload={
                    "tenant_id": entity.tenant_id,
                    "known_entity_id": entity.id,
                    "ai_entity_id": entity.ai_entity_id,
                    "updated_fields": sorted(dirty_fields),
                    "allowed_camera_ids_updated": allowed_camera_ids_updated,
                },
            )

        return Response(
            {
                "synced": True,
                "known_entity_id": entity.id,
                "entity_id": entity.ai_entity_id,
            },
            status=status.HTTP_200_OK,
        )

    if op == "remove_entity":
        entity = _resolve_entity_for_sync(
            tenant=tenant,
            entity_id=entity_id,
            known_entity_id=known_entity_id,
        )
        if entity is None:
            return Response({"synced": True, "removed": False}, status=status.HTTP_200_OK)

        with transaction.atomic():
            removed_payload = {
                "tenant_id": entity.tenant_id,
                "known_entity_id": entity.id,
                "ai_entity_id": entity.ai_entity_id,
            }
            entity.delete()
            _emit_identity_outbox_event(
                aggregate_type="known_entity",
                aggregate_id=removed_payload["known_entity_id"],
                event_type="identity.entity_removed",
                payload=removed_payload,
            )

        return Response({"synced": True, "removed": True}, status=status.HTTP_200_OK)

    if op == "record_sighting":
        entity = _resolve_entity_for_sync(
            tenant=tenant,
            entity_id=entity_id,
            known_entity_id=known_entity_id,
        )
        if entity is None:
            return Response({"synced": False, "error": "Entity not found"}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            entity.last_seen = timezone.now()
            update_fields = ["last_seen", "updated_at"]

            camera = None
            camera_token = str(payload.get("camera_id") or "").strip()
            if camera_token:
                camera_filter = models.Q(ai_camera_id=camera_token) | models.Q(stream_path=camera_token)
                if camera_token.isdigit():
                    camera_filter |= models.Q(pk=int(camera_token))
                camera = Camera.objects.filter(tenant=entity.tenant).filter(camera_filter).order_by("id").first()
                if camera is not None and entity.last_camera_id != camera.id:
                    entity.last_camera = camera
                    update_fields.append("last_camera")

            entity.save(update_fields=update_fields)
            _emit_identity_outbox_event(
                aggregate_type="known_entity",
                aggregate_id=entity.id,
                event_type="identity.entity_sighting_recorded",
                payload={
                    "tenant_id": entity.tenant_id,
                    "known_entity_id": entity.id,
                    "ai_entity_id": entity.ai_entity_id,
                    "camera_id": camera.id if camera is not None else None,
                    "camera_token": camera_token or None,
                },
            )

        return Response({"synced": True}, status=status.HTTP_200_OK)

    return Response(
        {"error": "Unsupported identity sync operation"},
        status=status.HTTP_400_BAD_REQUEST,
    )


# ── Evidence / static files proxy ─────────────────────────────


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_evidence(request, camera_id, filename):
    """GET /api/ai/evidence/<camera_id>/<filename> → stream evidence file."""
    return proxy_request(request, f"/evidence/{camera_id}/{filename}", stream=True)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_enroll_image(request, entity_id, filename):
    """GET /api/ai/enroll_images/<entity_id>/<filename> → stream enrollment image."""
    return proxy_request(
        request, f"/enroll_images/{entity_id}/{filename}", stream=True
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. WEBHOOK RECEIVER  (called by AI service, token-protected)
#    All ingestion logic is in ai_integration.incident_ingest
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@api_view(["POST"])
@permission_classes([AllowAny])
def ai_webhook_receive(request):
    """
    POST /api/ai/webhook/receive/

    Called by the AI service when an alert is created.
    Protected by X-AI-WEBHOOK-TOKEN header (shared secret).

    Now delegates to the shared ingest function for consistent processing
    with the Redis subscriber path.
    """
    if not _is_ai_internal_request_authenticated(request):
        return Response(
            {"error": "Unauthorized — invalid or missing webhook credentials"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    payload = request.data
    event = payload.get("event", "")
    data = payload.get("data", {})

    if event != "alert.created":
        logger.info("Ignoring webhook event: %s", event)
        return Response({"status": "ignored", "event": event})

    # Extract stable event ID for idempotency
    event_id = str(data.get("id", "")).strip() or None

    # Delegate to shared ingest function
    from .incident_ingest import process_alert_event

    result = process_alert_event(
        data=data,
        source="webhook",
        event_id=event_id,
    )

    if result.status == "error":
        http_status = status.HTTP_400_BAD_REQUEST
        if "Ambiguous" in (result.error or ""):
            http_status = status.HTTP_409_CONFLICT
        return Response(
            {"error": result.error, "status": result.status},
            status=http_status,
        )

    if result.status == "duplicate":
        return Response(
            {"status": "duplicate", "incident_id": result.incident_id},
            status=status.HTTP_200_OK,
        )

    return Response(
        {
            "status": result.status,
            "incident_id": result.incident_id,
        },
        status=status.HTTP_201_CREATED if result.status == "created" else status.HTTP_200_OK,
    )
