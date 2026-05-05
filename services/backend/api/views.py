import os
import uuid as uuid_mod
import logging
import hashlib
import hmac
import time
import socket
from urllib.parse import urlparse
from typing import List
from rest_framework import viewsets, permissions, status
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.conf import settings
from django.utils import timezone
from server.runtime_services import get_mediamtx_external_url
from ai_integration.redis_queue import (
    append_incident_event,
    build_test_incident_event,
    create_redis_client,
    read_subscriber_status,
    stream_length,
)
from server.redis_runtime import resolve_backend_redis_settings
from .models import (
    Tenant, Membership, Camera, CameraZone, Incident, Detection,
    Alert, AuditLog, Profile, Invitation, KnownEntity, KnownEntityAsset,
    NotificationChannel,
    severity_level_for_value,
)
from .stream_workers import STREAM_WORKERS
from .serializers import (
    TenantSerializer, MyTenantSerializer, MembershipSerializer,
    CameraSafeSerializer, CameraAdminSerializer, CameraWriteSerializer,
    CameraStreamSerializer,
    IncidentSerializer, DetectionSerializer, AlertSerializer, AuditLogSerializer,
    ProfileSerializer, InvitationCreateSerializer, PendingInvitationSerializer,
    KnownEntitySerializer, CameraZoneSerializer, NotificationChannelSerializer,
)
from .notification_service import NotificationService
from .services.camera_config_service import CameraConfigService
from .services.notification_policy_service import NotificationPolicyService
from .services.runtime_registration_service import RuntimeRegistrationService
from .services.tenant_config_service import TenantConfigService
from .services.outbox_service import OutboxService
from .services.entity_processing_service import EntityProcessingService
from .services.probe_service import ProbeService
from .services.mediamtx_helpers import (
    classify_camera_source,
    get_canonical_camera_id,
    get_ai_base_url,
    get_mediamtx_api_base,
    get_mediamtx_loopback_url,
    build_mediamtx_path_payload,
)
from .services.ai_runtime_control_service import (
    RelayNotReadyError,
    start_ai_runtime,
    stop_ai_runtime,
)


tenant_config_service = TenantConfigService()
camera_config_service = CameraConfigService()
notification_policy_service = NotificationPolicyService()
runtime_registration_service = RuntimeRegistrationService()
entity_processing_service = EntityProcessingService()

class IsAuthenticatedOrReadOnly(permissions.IsAuthenticatedOrReadOnly):
    pass

def get_active_tenant(request, required=True):
    """
    Optimized resolver that leverages the request-cached tenant object.
    """
    tenant = getattr(request, "tenant", None)
    if tenant:
        return tenant
    
    # Fallback/Auto-discovery for requests where middleware didn't run or header missing
    if request.user and request.user.is_authenticated:
        # If no tenant selected, try to find the only one
        memberships = Membership.objects.filter(user=request.user)
        if memberships.count() == 1:
            return memberships.first().tenant

    if required:
        raise PermissionDenied("Active tenant required. Please provide X-Tenant-ID header.")
    return None

def assert_member(request, tenant):
    """Checks if the current user is a member of the tenant (uses request cache)."""
    if not request.user or not request.user.is_authenticated:
        raise NotAuthenticated()
    
    # Fast path: check request.membership cache
    membership = getattr(request, "membership", None)
    if membership and membership.tenant == tenant:
        return True
    
    # Slow path: direct DB check
    return Membership.objects.filter(user=request.user, tenant=tenant).exists()


def get_membership(request, tenant):
    """Retrieves membership for the current user and tenant (uses request cache)."""
    if not request.user or not request.user.is_authenticated:
        raise NotAuthenticated()
    
    # Fast path: check request.membership cache
    membership = getattr(request, "membership", None)
    if membership and membership.tenant == tenant:
        return membership
    
    return Membership.objects.filter(user=request.user, tenant=tenant).first()


def assert_non_viewer(request, tenant):
    """Enforces that the user is not a read-only viewer (uses request cache)."""
    membership = get_membership(request, tenant)
    if not membership:
        raise PermissionDenied("Not a member of this tenant.")
    if membership.role == Membership.Role.VIEWER:
        raise PermissionDenied("Viewer role is read-only.")
    return membership

class TenantScopedViewSet(viewsets.ModelViewSet):
    """
    Base ViewSet that filters by tenant={X-Tenant-ID} and sets tenant on create.
    """
    tenant_field = "tenant"   # override if different

    def get_queryset(self):
        tenant = get_active_tenant(self.request)
        if not assert_member(self.request, tenant):
            raise PermissionDenied("Not a member of this tenant.")
        return super().get_queryset().filter(**{self.tenant_field: tenant})

    def perform_create(self, serializer):
        tenant = get_active_tenant(self.request)
        assert_non_viewer(self.request, tenant)
        serializer.save(**{self.tenant_field: tenant})

    def perform_update(self, serializer):
        tenant = get_active_tenant(self.request)
        assert_non_viewer(self.request, tenant)
        serializer.save()

    def perform_destroy(self, instance):
        tenant = get_active_tenant(self.request)
        assert_non_viewer(self.request, tenant)
        instance.delete()

class TenantViewSet(viewsets.ModelViewSet):
    serializer_class = TenantSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Tenant.objects.all()
        # return only tenants where the user has a membership
        return Tenant.objects.filter(memberships__user=user).distinct()
    
    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = tenant_config_service.create_tenant_with_owner(
            user=self.request.user,
            name=serializer.validated_data["name"],
            plan=serializer.validated_data.get("plan", "free"),
        )
        serializer.instance = tenant


    @action(detail=False, methods=["get"], url_path="mine")
    def mine(self, request):
        qs = (
            Membership.objects
            .select_related("tenant")
            .filter(user=request.user)
            .order_by("tenant__name")
        )
        return Response(MyTenantSerializer(qs, many=True).data)

class MembershipViewSet(TenantScopedViewSet):
    queryset = Membership.objects.select_related("user", "tenant").all()
    serializer_class = MembershipSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        tenant = get_active_tenant(self.request)
        assert_role_in(self.request, tenant, allowed_roles={"owner", "admin"})
        serializer.save(tenant=tenant)

    def perform_update(self, serializer):
        tenant = get_active_tenant(self.request)
        assert_role_in(self.request, tenant, allowed_roles={"owner", "admin"})
        serializer.save()

    def perform_destroy(self, instance):
        tenant = get_active_tenant(self.request)
        assert_role_in(self.request, tenant, allowed_roles={"owner", "admin"})
        instance.delete()


# ── MediaMTX helpers (Phase 4: canonical imports from shared module) ──
from api.services.mediamtx_helpers import (
    get_canonical_camera_id as _get_canonical_camera_id,       # backward-compat alias
    get_mediamtx_api_base as _get_mediamtx_api_base,           # backward-compat alias
    get_mediamtx_loopback_url as _get_mediamtx_loopback_url,   # backward-compat alias
    classify_camera_source,
    build_mediamtx_path_payload,
)

from typing import Dict, List

def reconcile_all_cameras_to_mediamtx() -> Dict[str, object]:
    """
    Delegates to the RelayReconciler for a full desired-state sweep.
    Kept as a backward-compatible entry point for API action callers.
    """
    from api.services.relay_reconciler import RelayReconciler

    reconciler = RelayReconciler(shadow_mode=False)
    result = reconciler.reconcile_all()
    return result.as_dict()



class CameraViewSet(TenantScopedViewSet):
    queryset = Camera.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        # Write operations use CameraWriteSerializer (accepts legacy aliases)
        if self.action in ("create", "update", "partial_update"):
            return CameraWriteSerializer
        # Read: admin sees rtsp_url, others don't
        if self.request.user and self.request.user.is_staff:
            return CameraAdminSerializer
        return CameraSafeSerializer

    def _assert_camera_write_access(self, request):
        tenant = get_active_tenant(request)
        return assert_non_viewer(request, tenant)

    def perform_create(self, serializer):
        """
        Save camera and persist MediaMTX desired state.
        The relay reconciler worker handles the actual MediaMTX provisioning.
        """
        tenant = get_active_tenant(self.request)
        assert_non_viewer(self.request, tenant)
        camera = camera_config_service.create_camera(
            tenant=tenant,
            attrs=dict(serializer.validated_data),
        )
        serializer.instance = camera
        # NOTE: camera_config_service.create_camera() already persists
        # MediaMTXDesiredPath and emits outbox events. The reconciler
        # will apply the path to MediaMTX.

    def perform_update(self, serializer):
        """Update camera — desired state is persisted by the service layer."""
        tenant = get_active_tenant(self.request)
        assert_non_viewer(self.request, tenant)
        camera = camera_config_service.update_camera(
            camera=serializer.instance,
            attrs=dict(serializer.validated_data),
        )
        serializer.instance = camera
        # NOTE: camera_config_service.update_camera() already persists
        # MediaMTXDesiredPath. The reconciler applies changes.

    def perform_destroy(self, instance):
        """Delete camera — desired state is marked disabled by the service layer."""
        # camera_config_service.delete_camera() marks MediaMTXDesiredPath
        # as disabled and emits an outbox event. The reconciler handles
        # the actual MediaMTX path removal.
        camera_config_service.delete_camera(camera=instance)

    @action(detail=False, methods=["post"], url_path="reconcile_mediamtx")
    def reconcile_mediamtx(self, request):
        self._assert_camera_write_access(request)
        summary = reconcile_all_cameras_to_mediamtx()
        status_code = status.HTTP_200_OK if summary["failed"] == 0 else status.HTTP_207_MULTI_STATUS
        return Response(summary, status=status_code)

    @action(detail=True, methods=["post"], url_path="sync_to_ai")
    def sync_to_ai(self, request, pk=None):
        """POST /api/cameras/{id}/sync_to_ai/ — explicitly enable AI ingestion."""
        self._assert_camera_write_access(request)
        camera = self.get_object()
        try:
            result = start_ai_runtime(
                camera,
                ingest_backend=str(request.data.get("ingest_backend", "opencv")),
                enabled_lanes=request.data.get(
                    "enabled_lanes",
                    camera.enabled_lanes if camera.enabled_lanes else ["rt_detr", "person_zone"],
                ),
                sample_hz=float(request.data.get("sample_hz", 2.0)),
                policy_version=int(request.data.get("policy_version", 1)),
            )
        except RelayNotReadyError as exc:
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
            return Response(
                {"error": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            "status": "synced",
            "ai_camera_id": result["camera_id"],
            "stream_path": result["stream_path"],
            "hot_loaded": result.get("hot_loaded", False),
            "rtsp_url_sent": result["rtsp_url_sent"],
            "path_name": result["path_name"],
            "db_rtsp_url": camera.rtsp_url,
            "loopback_rtsp_url": result["loopback_rtsp_url"],
            "running": result["running"],
            "waited_seconds": result.get("waited_seconds", 0.0),
        })

    @action(detail=True, methods=["post"], url_path="runtime_control")
    def runtime_control(self, request, pk=None):
        """POST /api/cameras/{id}/runtime_control/ — start/stop AI task (Phase 3)."""
        self._assert_camera_write_access(request)
        camera = self.get_object()
        enabled = request.data.get("enabled")
        if enabled is None:
            return Response({"error": "Field 'enabled' (bool) is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if bool(enabled):
                result = start_ai_runtime(
                    camera,
                    ingest_backend=str(request.data.get("ingest_backend", "opencv")),
                    enabled_lanes=request.data.get(
                        "enabled_lanes",
                        camera.enabled_lanes if camera.enabled_lanes else ["rt_detr", "person_zone"],
                    ),
                    sample_hz=float(request.data.get("sample_hz", 2.0)),
                    policy_version=int(request.data.get("policy_version", 1)),
                )
            else:
                result = stop_ai_runtime(
                    camera,
                    ingest_backend=str(request.data.get("ingest_backend", "opencv")),
                    enabled_lanes=request.data.get(
                        "enabled_lanes",
                        camera.enabled_lanes if camera.enabled_lanes else ["rt_detr", "person_zone"],
                    ),
                    sample_hz=float(request.data.get("sample_hz", 2.0)),
                    policy_version=int(request.data.get("policy_version", 1)),
                )
        except RelayNotReadyError as exc:
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
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(
            {
                "running": result["running"],
                "camera_id": result["camera_id"],
                "stream_path": result["stream_path"],
                "waited_seconds": result.get("waited_seconds", 0.0),
            },
            status=status.HTTP_200_OK,
        )

    # ── Zone CRUD ────────────────────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="zones")
    def zones(self, request, pk=None):
        """GET/POST /api/cameras/{id}/zones/"""
        camera = self.get_object()
        if request.method == "GET":
            qs = camera_config_service.camera_repository.list_camera_zones(camera=camera)
            return Response(CameraZoneSerializer(qs, many=True).data)
        # POST — create new zone
        self._assert_camera_write_access(request)
        ser = CameraZoneSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        zone = camera_config_service.create_camera_zone(camera=camera, attrs=dict(ser.validated_data))
        return Response(CameraZoneSerializer(zone).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["put", "delete"], url_path=r"zones/(?P<zone_id>\d+)")
    def zone_detail(self, request, pk=None, zone_id=None):
        """PUT/DELETE /api/cameras/{id}/zones/{zone_id}/"""
        self._assert_camera_write_access(request)
        camera = self.get_object()
        try:
            zone = CameraZone.objects.get(pk=zone_id, camera=camera)
        except CameraZone.DoesNotExist:
            return Response({"error": "Zone not found"}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            camera_config_service.delete_camera_zone(zone=zone)
            return Response(status=status.HTTP_204_NO_CONTENT)
        # PUT
        ser = CameraZoneSerializer(zone, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        zone = camera_config_service.update_camera_zone(zone=zone, attrs=dict(ser.validated_data))
        return Response(CameraZoneSerializer(zone).data)

    @action(detail=True, methods=["post"], url_path="sync_zones_to_ai")
    def sync_zones_to_ai(self, request, pk=None):
        """POST /api/cameras/{id}/sync_zones_to_ai/ — push zones to AI."""
        import requests as http_client
        self._assert_camera_write_access(request)
        camera = self.get_object()
        from api.services.mediamtx_helpers import get_ai_base_url
        ai_base = get_ai_base_url()
        cam_id = camera.ai_camera_id or f"cam_{camera.pk}"
        zones_payload = list(
            CameraZone.objects.filter(camera=camera, enabled=True).values(
                "zone_name", "zone_type", "polygon_points"
            )
        )
        try:
            resp = http_client.put(
                f"{ai_base}/api/v1/cameras/{cam_id}/zones",
                json=zones_payload, timeout=3.0,
            )
            return Response({"status": "synced", "ai_status": resp.status_code, "zones_sent": len(zones_payload)})
        except http_client.Timeout:
            return Response(
                {
                    "error": "AI service is busy or starting up.", 
                    "suggestion": "The request was sent, please refresh in a few moments."
                }, 
                status=status.HTTP_504_GATEWAY_TIMEOUT
            )
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    @action(detail=True, methods=["post"], url_path="sync_ai_settings")
    def sync_ai_settings(self, request, pk=None):
        """POST /api/cameras/{id}/sync_ai_settings/ — push per-camera thresholds to AI."""
        import requests as http_client
        self._assert_camera_write_access(request)
        camera = self.get_object()
        from api.services.mediamtx_helpers import get_ai_base_url
        ai_base = get_ai_base_url()
        cam_id = camera.ai_camera_id or f"cam_{camera.pk}"
        payload = {
            "min_confidence": camera.min_confidence,
            "min_bbox_area": camera.min_bbox_area,
            "k_of_n": [camera.k_of_n_k, camera.k_of_n_n],
            "cooldown_s": camera.cooldown_s,
        }
        try:
            resp = http_client.put(
                f"{ai_base}/api/v1/cameras/{cam_id}/settings",
                json=payload, timeout=3.0,
            )
            return Response({"status": "synced", "ai_status": resp.status_code})
        except http_client.Timeout:
            return Response(
                {
                    "error": "AI service is busy or starting up.", 
                    "suggestion": "The request was sent, please refresh in a few moments."
                }, 
                status=status.HTTP_504_GATEWAY_TIMEOUT
            )
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    def _do_test_connection(self, rtsp_url, camera=None, timeout_s=5):
        """Unified tiered connection probe for saved and unsaved cameras."""
        import requests as http_client
        if not rtsp_url:
            return Response({"ok": False, "category": "missing_url", "error": "No URL provided."}, status=status.HTTP_400_BAD_REQUEST)
        
        source_kind = classify_camera_source(rtsp_url)
        path_name = get_canonical_camera_id(camera) if camera else f"test-{uuid_mod.uuid4().hex[:8]}"

        # ── Gate 0: Network Pre-flight ──────────────────────────
        # Check if target host is reachable before touching MediaMTX.
        net_ok, net_msg = ProbeService.check_network_availability(rtsp_url, source_kind)
        if not net_ok:
            return Response({
                "ok": False,
                "category": "network_unreachable",
                "message": net_msg,
                "error": net_msg,
                "source_kind": source_kind
            })

        api_base = get_mediamtx_api_base()
        
        # Decide if we need to provision a temporary path.
        # Saved and active cameras already have a path managed by the reconciler.
        # Do NOT patch/post the real path — that races with the reconciler.
        is_temporary = True
        if camera:
            from .models import MediaMTXDesiredPath
            desired = MediaMTXDesiredPath.objects.filter(camera=camera, desired_enabled=True).first()
            if desired:
                is_temporary = False
                path_name = desired.stream_path

        if is_temporary:
            # Use _probe_{uuid} for guaranteed uniqueness under concurrent tests.
            path_name = f"_probe_{uuid_mod.uuid4().hex[:12]}"
            # Provision temporary path in MediaMTX for probing.
            # Use persistent=True so MediaMTX starts the source (e.g. FFmpeg bridge) 
            # immediately without waiting for a consumer, allowing Gate 1 to work.
            temp_cam = camera if camera else Camera(rtsp_url=rtsp_url)
            try:
                payload = build_mediamtx_path_payload(temp_cam, path_name, source_kind, persistent=True)
                http_client.post(f"{api_base}/v3/config/paths/add/{path_name}", json=payload, timeout=3)
            except Exception as exc:
                return Response({"ok": False, "category": "path_provision_failed", "message": str(exc), "error": str(exc)})

        # ── Gate 1: MediaMTX State Gate ─────────────────────────
        # Wait for MediaMTX to report the source is successfully connected.
        mtx_ok, mtx_msg = ProbeService.wait_for_mediamtx_state(path_name, timeout_s=timeout_s)
        if not mtx_ok:
            if is_temporary:
                try:
                    http_client.delete(f"{api_base}/v3/config/paths/delete/{path_name}", timeout=2)
                except Exception: pass
            return Response({
                "ok": False,
                "category": "mediamtx_connection_timeout",
                "message": mtx_msg,
                "error": mtx_msg,
                "source_kind": source_kind,
                "path_name": path_name
            })

        # ── Gate 2: Media Verification ──────────────────────────
        # Final confirmation that frame data is actually usable.
        loopback_url = get_mediamtx_loopback_url(path_name)
        probe_result = ProbeService.run_media_probe(loopback_url, timeout_s=timeout_s)
        
        # Cleanup temporary test path
        if is_temporary:
            try:
                http_client.delete(f"{api_base}/v3/config/paths/delete/{path_name}", timeout=2)
            except Exception:
                pass
                
        return Response(probe_result)

    # ── Test connection (existing camera) ───────────────────
    @action(detail=True, methods=["post"], url_path="test_connection")
    def test_connection_detail(self, request, pk=None):
        """POST /api/cameras/{id}/test_connection/ — test stored RTSP URL via MediaMTX."""
        self._assert_camera_write_access(request)
        camera = self.get_object()
        timeout_s = min(int(request.data.get("timeout_s", 5)), 15)
        return self._do_test_connection(camera.rtsp_url, camera=camera, timeout_s=timeout_s)

    # ── Test connection (unsaved URL) ───────────────────────
    @action(detail=False, methods=["post"], url_path="test_connection")
    def test_connection_list(self, request):
        """
        CLOUD FIX: HTTP 202 Accepted Pattern.
        Instead of running ffprobe in the web thread, enqueue it.
        """
        self._assert_camera_write_access(request)
        rtsp_url = request.data.get("rtsp_url", "").strip()
        if not rtsp_url:
            return Response({"error": "rtsp_url is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Example pseudo-code for Celery task queuing
        # task = probe_rtsp_task.delay(rtsp_url)
        # return Response({"task_id": task.id, "status": "processing"}, status=status.HTTP_202_ACCEPTED)

        # and Phase 2 infrastructure migration.
        timeout_s = min(int(request.data.get("timeout_s", 8)), 15)
        return self._do_test_connection(rtsp_url, camera=None, timeout_s=timeout_s)

class IncidentViewSet(TenantScopedViewSet):
    queryset = Incident.objects.select_related("camera").all()
    serializer_class = IncidentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        """Create incident. Notifications are sent automatically via Django signal."""
        tenant = get_active_tenant(self.request)
        assert_non_viewer(self.request, tenant)
        
        # Save the incident - the post_save signal will broadcast notifications
        serializer.save(tenant=tenant)

    def get_queryset(self):
        qs = super().get_queryset().order_by("-started_at")
        status_filter = self.request.query_params.get("status")
        type_filter = self.request.query_params.get("type")
        search = (self.request.query_params.get("search") or "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        if type_filter:
            qs = qs.filter(type=type_filter)
        if search:
            query = (
                Q(type__icontains=search)
                | Q(camera__name__icontains=search)
                | Q(details_text__icontains=search)
            )
            if search.isdigit():
                query |= Q(id=int(search))
            qs = qs.annotate(details_text=Cast("details", output_field=TextField())).filter(query)
        return qs

    @action(detail=True, methods=["post"], url_path="acknowledge")
    def acknowledge(self, request, pk=None):
        tenant = get_active_tenant(request)
        assert_non_viewer(request, tenant)
        incident = self.get_object()
        if incident.status == "resolved":
            return Response({"error": "Incident already resolved"}, status=status.HTTP_400_BAD_REQUEST)
        incident.status = "acknowledged"
        incident.save(update_fields=["status", "updated_at"])
        # Audit
        AuditLog.objects.create(
            tenant=tenant, actor=request.user,
            action="incident.acknowledge", target_type="incident", target_id=str(incident.pk),
        )
        return Response(IncidentSerializer(incident).data)

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        tenant = get_active_tenant(request)
        assert_non_viewer(request, tenant)
        incident = self.get_object()
        incident.status = "resolved"
        incident.ended_at = timezone.now()
        incident.save(update_fields=["status", "ended_at", "updated_at"])
        AuditLog.objects.create(
            tenant=tenant, actor=request.user,
            action="incident.resolve", target_type="incident", target_id=str(incident.pk),
        )
        return Response(IncidentSerializer(incident).data)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """Aggregated incident stats for dashboard & reports."""
        from django.db.models import Count
        from django.db.models.functions import TruncDate
        import datetime

        tenant = get_active_tenant(request)
        qs = Incident.objects.filter(tenant=tenant)

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - datetime.timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        today_count = qs.filter(started_at__gte=today_start).count()
        week_count = qs.filter(started_at__gte=week_start).count()
        month_count = qs.filter(started_at__gte=month_start).count()
        total_count = qs.count()

        # Type breakdown
        type_breakdown = list(qs.values("type").annotate(count=Count("id")).order_by("-count"))

        # Per-day (last 7 days)
        seven_days_ago = today_start - datetime.timedelta(days=6)
        per_day = list(
            qs.filter(started_at__gte=seven_days_ago)
            .annotate(day=TruncDate("started_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )

        # Status breakdown
        status_breakdown = list(qs.values("status").annotate(count=Count("id")))

        return Response({
            "today": today_count,
            "week": week_count,
            "month": month_count,
            "total": total_count,
            "type_breakdown": type_breakdown,
            "per_day": [{"day": str(d["day"]), "count": d["count"]} for d in per_day],
            "status_breakdown": status_breakdown,
        })

class DetectionViewSet(TenantScopedViewSet):
    queryset = Detection.objects.all()
    serializer_class = DetectionSerializer
    permission_classes = [permissions.IsAuthenticated]

class AlertViewSet(TenantScopedViewSet):
    queryset = Alert.objects.all()
    serializer_class = AlertSerializer
    permission_classes = [permissions.IsAuthenticated]

class AuditLogViewSet(TenantScopedViewSet):
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

class ProfileViewSet(viewsets.ModelViewSet):
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Users can only access their own profile
        return Profile.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        tenant = get_active_tenant(self.request)
        assert_non_viewer(self.request, tenant)
        serializer.save(user=self.request.user)

    def list(self, request, *args, **kwargs):
        """Return single profile (auto-create if missing)."""
        profile, _ = Profile.objects.get_or_create(user=request.user)
        return Response(ProfileSerializer(profile).data)

    @action(detail=False, methods=["get", "put", "patch"], url_path="me")
    def me(self, request):
        profile, _ = Profile.objects.get_or_create(user=request.user)
        if request.method == "GET":
            return Response(ProfileSerializer(profile).data)
        tenant = get_active_tenant(request)
        assert_non_viewer(request, tenant)
        serializer = ProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProfileSerializer(profile).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """Single endpoint for all dashboard data."""
    import datetime
    from django.db.models import Count, Q
    from django.core.cache import cache

    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied("Not a member of this tenant.")

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - datetime.timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    incidents = Incident.objects.filter(tenant=tenant)

    # Cameras - optimized with select_related for AI sync state
    cameras_qs = Camera.objects.filter(tenant=tenant).select_related("runtime_registration").values(
        "id", "name", "site", "status", "ai_camera_id", "source_type", "stream_path", "rtsp_url",
        "runtime_registration__desired_enabled"
    )
    
    cameras = []
    for cam in cameras_qs:
        cam["has_stream"] = bool(cam.pop("rtsp_url", None))
        cam["is_ai_synced"] = bool(cam.pop("runtime_registration__desired_enabled", False))
        cameras.append(cam)

    # Aggregated counts - combined into fewer queries
    incidents_aggs = incidents.aggregate(
        today=Count('id', filter=Q(started_at__gte=today_start)),
        week=Count('id', filter=Q(started_at__gte=week_start)),
        month=Count('id', filter=Q(started_at__gte=month_start)),
        open=Count('id', filter=Q(status=Incident.Status.OPEN)),
        critical=Count('id', filter=Q(severity__gte=4, started_at__gte=today_start)),
    )
    
    cameras_aggs = Camera.objects.filter(tenant=tenant).aggregate(
        total=Count('id'),
        live=Count('id', filter=Q(status=Camera.Status.ACTIVE))
    )

    stats = {
        "today": incidents_aggs["today"],
        "week": incidents_aggs["week"],
        "month": incidents_aggs["month"],
        "open": incidents_aggs["open"],
        "critical": incidents_aggs["critical"],
        "camera_total": cameras_aggs["total"],
        "camera_live": cameras_aggs["live"],
    }

    # Recent incidents (last 10)
    recent_incidents = list(
        incidents.order_by("-started_at")[:10].values(
            "id", "type", "status", "severity", "started_at",
            "camera__name", "camera__source_type", "details",
        )
    )

    # Type breakdown
    type_breakdown = list(incidents.values("type").annotate(count=Count("id")).order_by("-count"))

    # Recent audit
    recent_audit = list(
        AuditLog.objects.filter(tenant=tenant)
        .select_related("actor")
        .order_by("-created_at")[:10]
        .values("id", "action", "target_type", "target_id", "created_at", "actor__username")
    )

    # AI health check (cached to avoid blocking dashboard polling)
    ai_healthy = cache.get("dashboard_ai_health")
    if ai_healthy is None:
        try:
            import requests as http_client
            from api.services.mediamtx_helpers import get_ai_base_url
            ai_base = get_ai_base_url()
            resp = http_client.get(f"{ai_base}/api/v1/health", timeout=1.0)
            ai_healthy = resp.status_code == 200
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("AI Engine health check failed: %s", exc)
            ai_healthy = False
        cache.set("dashboard_ai_health", ai_healthy, 15)  # Cache for 15s

    # Fetch streams health
    camera_ids = [cam["id"] for cam in cameras]
    cfg = _stream_preview_config()
    streams_health = STREAM_WORKERS.health_for_cameras(camera_ids, default_fps=cfg["fps"])

    # Fetch known entities for household/neighbor
    recent_entities = list(
        KnownEntity.objects.filter(
            tenant=tenant, 
            group__in=[KnownEntity.Group.HOUSEHOLD, KnownEntity.Group.NEIGHBOR]
        ).exclude(status=KnownEntity.Status.DELETED).order_by("-created_at")[:8].values(
            "id", "name", "category", "group"
        )
    )

    return Response({
        "cameras": cameras,
        "stats": stats,
        "recent_incidents": recent_incidents,
        "type_breakdown": type_breakdown,
        "recent_audit": [
            {**a, "actor": a.pop("actor__username", None)} for a in recent_audit
        ],
        "ai_healthy": ai_healthy,
        "streams_health": streams_health,
        "entities": recent_entities,
    })

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def auth_context(request):
    tenant = get_active_tenant(request, required=False)

    # If no tenant header, and user has exactly one membership, auto-select it
    if tenant is None:
        memberships = Membership.objects.select_related("tenant").filter(user=request.user)
        if memberships.count() == 1:
            tenant = memberships.first().tenant

    role = None
    tenant_payload = None

    if tenant:
        m = Membership.objects.filter(user=request.user, tenant=tenant).first()
        if m:
            role = m.role
            tenant_payload = {"id": tenant.id, "name": tenant.name}

    return Response({
        "user": {
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
            "is_superuser": request.user.is_superuser,
            "is_staff": request.user.is_staff,
        },
        "tenant": tenant_payload,
        "role": role,
    })

def assert_role_in(request, tenant, allowed_roles):
    if not request.user or not request.user.is_authenticated:
        raise NotAuthenticated()
    membership = Membership.objects.filter(user=request.user, tenant=tenant).first()
    if not membership or membership.role not in allowed_roles:
        raise PermissionDenied("Insufficient permissions.")
    return membership


class KnownEntityViewSet(TenantScopedViewSet):
    """
    Django-authoritative CRUD for enrolled entities.

    POST stores entity metadata + enrollment assets and enqueues asynchronous
    processing. The request path does not depend on AI availability.
    """
    queryset = KnownEntity.objects.all()
    serializer_class = KnownEntitySerializer
    permission_classes = [permissions.IsAuthenticated]
    # Accept both JSON and multipart
    from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _assert_entity_admin(self, request, tenant):
        return assert_role_in(request, tenant, allowed_roles={"owner", "admin"})

    def get_queryset(self):
        qs = super().get_queryset().prefetch_related("cameras").order_by("-created_at")
        cat = self.request.query_params.get("category")
        group = self.request.query_params.get("group")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.exclude(status=KnownEntity.Status.DELETED)
        if cat:
            qs = qs.filter(category=cat)
        if group:
            qs = qs.filter(group=group)
        return qs

    def create(self, request, *args, **kwargs):
        """
        Override create to handle multipart form data with images and enqueue
        backend-owned processing.
        """
        # Build serializer from POST data (DRF handles QueryDict correctly)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenant = get_active_tenant(self.request)
        self._assert_entity_admin(self.request, tenant)
        entity = serializer.save(
            tenant=tenant,
            status=KnownEntity.Status.PENDING,
            detection_enabled=False,
            created_by=request.user,
            updated_by=request.user,
        )

        uploaded_files = request.FILES.getlist("files")
        if len(uploaded_files) > 10:
            return Response({"error": "Maximum 10 files allowed per entity."}, status=status.HTTP_400_BAD_REQUEST)

        saved_assets = 0
        for upload in uploaded_files:
            if upload.size > 5 * 1024 * 1024:
                return Response({"error": f"File {upload.name} exceeds 5MB limit."}, status=status.HTTP_400_BAD_REQUEST)

            upload.seek(0)
            content = upload.read()
            upload.seek(0)
            checksum = hashlib.sha256(content).hexdigest()

            # Best-effort image dimension extraction
            img_width = None
            img_height = None
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(content))
                img_width, img_height = img.size
                if img_width < 64 or img_height < 64:
                    return Response({"error": f"Image {upload.name} dimensions too small (min 64x64)."}, status=status.HTTP_400_BAD_REQUEST)
                if img_width > 4096 or img_height > 4096:
                    return Response({"error": f"Image {upload.name} dimensions too large (max 4096x4096)."}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                # If Pillow failed to parse, we might reject or allow.
                if isinstance(e, ValueError) or "cannot identify image file" in str(e).lower():
                    return Response({"error": f"File {upload.name} is not a valid image."}, status=status.HTTP_400_BAD_REQUEST)

            asset = KnownEntityAsset.objects.create(
                tenant=tenant,
                entity=entity,
                asset_type=KnownEntityAsset.AssetType.ENROLLMENT_IMAGE,
                file=upload,
                storage_uri="",
                checksum=checksum,
                content_type=(upload.content_type or "application/octet-stream"),
                uploaded_by=request.user,
                is_active=True,
                width=img_width,
                height=img_height,
            )
            if not asset.storage_uri and asset.file:
                asset.storage_uri = asset.file.name
                asset.save(update_fields=["storage_uri", "updated_at"])
            saved_assets += 1

        processing_job, job_created = entity_processing_service.enqueue_job(
            entity=entity,
            requested_by=request.user,
            metadata={"asset_count": saved_assets},
        )

        logger.info("Successfully created entity: %s (ID: %s) for tenant %s", entity.name, entity.id, tenant.id)

        AuditLog.objects.create(
            tenant=tenant,
            actor=request.user,
            action="entity.create",
            target_type="entity",
            target_id=str(entity.id),
            meta={
                "entity_id": entity.id,
                "category": entity.category,
                "group": entity.group,
                "status": entity.status,
                "detection_enabled": entity.detection_enabled,
                "asset_count": saved_assets,
                "processing_job_id": processing_job.id,
            },
        )
        result = self.get_serializer(entity).data
        headers = self.get_success_headers(result)
        result["processing"] = {
            "job_id": processing_job.id,
            "status": processing_job.status,
            "queued": bool(job_created),
            "asset_count": saved_assets,
        }
        result["ai_enrollment"] = {
            "status": "queued",
            "embeddings_stored": 0,
            "saved_images_count": saved_assets,
            "failed_images": [],
        }
        return Response(result, status=status.HTTP_201_CREATED, headers=headers)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        ai_map = self._fetch_ai_entity_state_map()
        if isinstance(response.data, list):
            response.data = [self._merge_ai_identity_state(item, ai_map=ai_map) for item in response.data]
        elif isinstance(response.data, dict) and isinstance(response.data.get("results"), list):
            response.data["results"] = [
                self._merge_ai_identity_state(item, ai_map=ai_map)
                for item in response.data["results"]
            ]
        return response

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        if isinstance(response.data, dict):
            response.data = self._merge_ai_identity_state(response.data, ai_map=self._fetch_ai_entity_state_map())
        return response

    def update(self, request, *args, **kwargs):
        tenant = get_active_tenant(request)
        self._assert_entity_admin(request, tenant)
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        previous_detection_enabled = instance.detection_enabled
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        changed_fields = sorted(serializer.validated_data.keys())
        entity = serializer.save(updated_by=request.user)

        if changed_fields:
            AuditLog.objects.create(
                tenant=tenant,
                actor=request.user,
                action="entity.update",
                target_type="entity",
                target_id=str(entity.id),
                meta={
                    "entity_id": entity.id,
                    "changed_fields": changed_fields,
                },
            )

        if "detection_enabled" in changed_fields and previous_detection_enabled != entity.detection_enabled:
            AuditLog.objects.create(
                tenant=tenant,
                actor=request.user,
                action="entity.toggle_detection",
                target_type="entity",
                target_id=str(entity.id),
                meta={
                    "entity_id": entity.id,
                    "previous_detection_enabled": previous_detection_enabled,
                    "detection_enabled": entity.detection_enabled,
                },
            )

        OutboxService().emit(
            aggregate_type="known_entity",
            aggregate_id=entity.id,
            event_type="identity.entity_updated",
            payload={
                "tenant_id": entity.tenant_id,
                "known_entity_id": entity.id,
                "changed_fields": changed_fields,
            },
        )
        # 2. Opportunistic/Low-Latency Sync: Attempt immediate advisory update to AI service.
        # This is NOT the source of truth and is strictly for minimizing transition latency.
        self._sync_entity_to_ai(entity)
        return Response(self.get_serializer(entity).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def _allowed_ai_camera_ids(self, entity) -> List[str]:
        """Legacy helper for direct AI sync."""
        allowed = []
        for cam in entity.cameras.all():
            cam_id = (cam.ai_camera_id or "").strip() or str(cam.id)
            allowed.append(cam_id)
        return sorted(set(allowed))

    def _sync_entity_to_ai(self, entity):
        """
        Legacy Advisory Sync (Deprecated).
        Pushes metadata changes directly to the AI service for low-latency updates.
        Durable convergence is now handled via Outbox -> Redis Stream.
        """
        if not entity.ai_entity_id:
            return

        import requests as http_client

        from api.services.mediamtx_helpers import get_ai_base_url
        ai_base = get_ai_base_url()
        payload = {
            "name": entity.name,
            "category": "PET" if entity.category == KnownEntity.Category.PET else "KNOWN_PERSON",
            "role": "NEIGHBOR" if entity.group == KnownEntity.Group.NEIGHBOR else "VISITOR",
            "metadata": {
                "allowed_camera_ids": self._allowed_ai_camera_ids(entity),
                "tenant_id": str(entity.tenant_id),
                "known_entity_id": str(entity.id),
            },
        }
        try:
            http_client.put(
                f"{ai_base}/entities/{entity.ai_entity_id}",
                json=payload,
                timeout=5,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Legacy AI entity sync failed (advisory): %s", exc)


    def _fetch_ai_entity_state_map(self):
        import requests as http_client

        from api.services.mediamtx_helpers import get_ai_base_url
        ai_base = get_ai_base_url()
        try:
            resp = http_client.get(f"{ai_base}/entities", timeout=5)
            if resp.status_code != 200:
                return {}
            raw = resp.json()
            entities = raw if isinstance(raw, list) else []
            return {
                str(item.get("entity_id") or item.get("id") or ""): item
                for item in entities
                if isinstance(item, dict)
            }
        except Exception:
            return {}

    def _merge_ai_identity_state(self, payload: dict, ai_map=None):
        """Overlay AI-managed identity state (last_seen/last_camera_id) in API responses."""
        ai_entity_id = str(payload.get("ai_entity_id") or "").strip()
        if not ai_entity_id:
            return payload

        by_id = ai_map if isinstance(ai_map, dict) else self._fetch_ai_entity_state_map()
        ai_item = by_id.get(ai_entity_id)
        if not ai_item:
            return payload
        if ai_item.get("last_seen"):
            payload["last_seen"] = ai_item.get("last_seen")
        if ai_item.get("last_camera_id"):
            payload["last_camera_id"] = ai_item.get("last_camera_id")
        return payload

    def perform_destroy(self, instance):
        """Soft-delete in Django and best-effort delete in AI."""
        import requests as http_client
        tenant = get_active_tenant(self.request)
        self._assert_entity_admin(self.request, tenant)

        # 1. Authoritative Durable Sync: Emit OutboxEvent for reliable, transactional convergence.
        OutboxService().emit(
            aggregate_type="known_entity",
            aggregate_id=instance.id,
            event_type="identity.entity_removed",
            payload={
                "tenant_id": instance.tenant_id,
                "known_entity_id": instance.id,
                "soft_deleted": True,
            },
        )

        # 2. Opportunistic Advisory Sync: Attempt immediate removal from AI matcher during transition.
        # Not required for correctness as Outbox + Healer will eventually converge.
        if instance.ai_entity_id:
            import requests as http_client
            from api.services.mediamtx_helpers import get_ai_base_url
            ai_base = get_ai_base_url()
            try:
                http_client.delete(
                    f"{ai_base}/entities/{instance.ai_entity_id}",
                    timeout=5,
                )
            except Exception:
                pass

        # 3. Local Consistency: Perform a soft-delete by updating status.
        # This keeps the record in DB but removes it from the default UI filter.
        instance.status = KnownEntity.Status.DELETED
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["status", "deleted_at", "updated_at"])

        AuditLog.objects.create(
            tenant=tenant,
            actor=self.request.user,
            action="entity.delete",
            target_type="entity",
            target_id=str(instance.id),
            meta={
                "entity_id": instance.id,
                "status": instance.status,
                "detection_enabled": instance.detection_enabled,
                "soft_deleted": True,
            },
        )


class InvitationViewSet(viewsets.ModelViewSet):
    queryset = Invitation.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ["create"]:
            return InvitationCreateSerializer
        if self.action in ["pending"]:
            return PendingInvitationSerializer
        return PendingInvitationSerializer

    def create(self, request, *args, **kwargs):
        # tenant-scoped (requires header) — invite into current tenant
        tenant = get_active_tenant(request)  # required
        assert_role_in(request, tenant, allowed_roles={"owner", "admin"})

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower()
        
        # Check if a pending invitation already exists for this email + tenant
        existing_invite = Invitation.objects.filter(
            tenant=tenant,
            email=email,
            status=Invitation.Status.PENDING,
            expires_at__gt=timezone.now()
        ).first()
        
        if existing_invite:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"email": "A pending invitation already exists for this email."})

        inv = Invitation.objects.create(
            tenant=tenant,
            email=email,
            role=serializer.validated_data["role"],
            invited_by=request.user,
        )
        return Response(PendingInvitationSerializer(inv).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="pending")
    def pending(self, request):
        # no tenant header required
        email = (request.user.email or "").lower()
        qs = (
            Invitation.objects
            .select_related("tenant", "invited_by")
            .filter(email=email, status="pending", expires_at__gt=timezone.now())
            .order_by("-created_at")
        )
        return Response(PendingInvitationSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="accept")
    @transaction.atomic
    def accept(self, request, pk=None):
        inv = self.get_object()

        if (request.user.email or "").lower() != inv.email.lower():
            raise PermissionDenied("This invitation is not for your account.")

        if not inv.is_valid():
            raise PermissionDenied("Invitation is not valid (expired or already used).")

        Membership.objects.get_or_create(
            tenant=inv.tenant,
            user=request.user,
            defaults={"role": inv.role},
        )

        inv.status = "accepted"
        inv.accepted_by = request.user
        inv.accepted_at = timezone.now()
        inv.save(update_fields=["status", "accepted_by", "accepted_at", "updated_at"])

        return Response({"ok": True, "tenant_id": inv.tenant.id, "role": inv.role})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §B  STREAM ENDPOINTS (OpenCV preview + MJPEG)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _stream_preview_config() -> dict:
    return {
        "fps": int(getattr(settings, "STREAM_PREVIEW_FPS", 3)),
        "max_width": int(getattr(settings, "STREAM_PREVIEW_MAX_WIDTH", 960)),
        "jpeg_quality": int(getattr(settings, "STREAM_PREVIEW_JPEG_QUALITY", 70)),
        "idle_ttl_s": int(getattr(settings, "STREAM_IDLE_TTL_SECONDS", 60)),
        "ffmpeg_capture_options": str(
            getattr(settings, "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;3000000")
        ),
        "prefer_ai_snapshots": bool(
            getattr(settings, "STREAM_PREVIEW_PREFER_AI_SNAPSHOTS", True)
        ),
        "rtsp_fallback_enabled": bool(
            getattr(settings, "STREAM_PREVIEW_RTSP_FALLBACK_ENABLED", True)
        ),
        "ai_snapshot_timeout_s": float(
            getattr(settings, "STREAM_PREVIEW_AI_SNAPSHOT_TIMEOUT_SECONDS", 2.0)
        ),
    }


def _camera_ai_sync_enabled(camera: Camera) -> bool:
    from api.models import AIRuntimeRegistration

    registration = getattr(camera, "runtime_registration", None)
    if registration is None:
        registration = AIRuntimeRegistration.objects.filter(camera=camera).only("desired_enabled").first()
    return bool(registration and registration.desired_enabled)


def _should_allow_preview_worker(camera: Camera, cfg: dict) -> bool:
    if cfg["rtsp_fallback_enabled"]:
        return True

    from api.services.mediamtx_helpers import is_self_referential

    source_url = (camera.rtsp_url or "").strip()
    if not source_url or is_self_referential(source_url):
        return False

    return not _camera_ai_sync_enabled(camera)


def _ai_snapshot_candidate_ids(camera: Camera) -> list[str]:
    candidates: list[str] = []
    for raw in (camera.ai_camera_id, camera.stream_path, f"cam_{camera.pk}"):
        candidate = str(raw or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _fetch_ai_snapshot(
    camera: Camera,
    *,
    timeout_s: float,
    quality: int,
    max_width: int,
) -> tuple[bytes | None, str | None, str, str]:
    """Fetch a snapshot from the AI service without opening a new RTSP reader.

    Returns ``(jpeg_bytes, frame_ts, source_label, error_message)``.
    """
    import requests as http_client

    ai_base = get_ai_base_url().rstrip("/")
    last_error = ""
    params = {"quality": int(quality), "maxw": int(max_width)}

    for candidate in _ai_snapshot_candidate_ids(camera):
        for path in (
            f"/api/v1/cameras/{candidate}/snapshot",
            f"/frame/{candidate}",
        ):
            url = f"{ai_base}{path}"
            try:
                resp = http_client.get(url, params=params, timeout=timeout_s)
            except http_client.Timeout:
                last_error = f"ai_snapshot_timeout:{candidate}"
                continue
            except http_client.RequestException as exc:
                last_error = f"ai_snapshot_error:{type(exc).__name__}"
                continue

            content_type = str(resp.headers.get("Content-Type", ""))
            if resp.status_code == 200 and "image" in content_type and resp.content:
                frame_ts = str(
                    resp.headers.get("X-Frame-Timestamp")
                    or resp.headers.get("X-Timestamp")
                    or ""
                )
                return resp.content, frame_ts, f"ai:{candidate}", ""

            if resp.status_code == 404:
                last_error = f"ai_snapshot_not_found:{candidate}"
                continue

            last_error = f"ai_snapshot_http_{resp.status_code}:{candidate}"

    return None, None, "", last_error


def _build_stream_token(camera_id: int, ttl_s: int = 60) -> tuple[str, int]:
    exp = int(time.time()) + ttl_s
    payload = f"{camera_id}.{exp}".encode()
    sig = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()[:32]
    return f"{camera_id}.{exp}.{sig}", ttl_s


def _verify_stream_token(token: str, camera_id: int) -> tuple[bool, dict]:
    try:
        tok_cam, tok_exp, tok_sig = token.split(".")
        if int(tok_cam) != int(camera_id):
            return False, {"error": "camera_mismatch"}
        if time.time() > float(tok_exp):
            return False, {"error": "expired"}
        payload = f"{tok_cam}.{tok_exp}".encode()
        expected = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expected, tok_sig):
            return False, {"error": "signature_mismatch"}
        return True, {"camera_id": int(tok_cam), "exp": int(tok_exp)}
    except Exception:
        return False, {"error": "malformed"}


def _camera_from_jwt_scope(request, camera_id: int) -> Camera:
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    try:
        return Camera.objects.get(pk=camera_id, tenant=tenant)
    except Camera.DoesNotExist:
        raise PermissionDenied("Camera not found for tenant")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def streams_list(request):
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    cameras = Camera.objects.filter(tenant=tenant).order_by("name")
    return Response(CameraStreamSerializer(cameras, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def streams_detail(request, camera_id):
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    try:
        camera = Camera.objects.get(pk=camera_id, tenant=tenant)
    except Camera.DoesNotExist:
        return Response({"error": "Camera not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response(CameraStreamSerializer(camera).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def streams_signed_token(request, camera_id):
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    try:
        Camera.objects.get(pk=camera_id, tenant=tenant)
    except Camera.DoesNotExist:
        return Response({"error": "Camera not found"}, status=status.HTTP_404_NOT_FOUND)

    token, ttl = _build_stream_token(camera_id, ttl_s=60)
    return Response({"token": token, "ttl": ttl})


@api_view(["GET"])
@permission_classes([])
def streams_snapshot(request, camera_id):
    """JWT member auth OR signed query token auth."""
    from django.http import HttpResponse

    token_param = request.GET.get("token", "")
    camera = None

    if token_param:
        ok, _payload = _verify_stream_token(token_param, camera_id)
        if not ok:
            return Response({"error": "Invalid stream token"}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            camera = Camera.objects.get(pk=camera_id)
        except Camera.DoesNotExist:
            return Response({"error": "Camera not found"}, status=status.HTTP_404_NOT_FOUND)
    elif request.user and request.user.is_authenticated:
        try:
            camera = _camera_from_jwt_scope(request, camera_id)
        except PermissionDenied as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    else:
        raise NotAuthenticated("Provide Authorization header or ?token= parameter")

    cfg = _stream_preview_config()
    last_error = ""

    if cfg["prefer_ai_snapshots"]:
        jpeg, frame_ts, preview_source, last_error = _fetch_ai_snapshot(
            camera,
            timeout_s=float(cfg["ai_snapshot_timeout_s"]),
            quality=int(cfg["jpeg_quality"]),
            max_width=int(cfg["max_width"]),
        )
        if jpeg:
            resp = HttpResponse(jpeg, content_type="image/jpeg")
            resp["Cache-Control"] = "no-store"
            resp["X-Frame-Timestamp"] = str(frame_ts or "")
            resp["X-Stream-Status"] = "connected"
            resp["X-Preview-Source"] = preview_source
            return resp

    jpeg = None
    frame_ts = None
    if _should_allow_preview_worker(camera, cfg):
        worker = STREAM_WORKERS.ensure_running(
            camera,
            fps=int(cfg["fps"]),
            max_width=int(cfg["max_width"]),
            jpeg_quality=int(cfg["jpeg_quality"]),
            idle_ttl_s=int(cfg["idle_ttl_s"]),
            ffmpeg_capture_options=str(cfg["ffmpeg_capture_options"]),
        )
        worker.touch()
        jpeg, frame_ts, last_error = STREAM_WORKERS.get_latest_jpeg(int(camera.pk))

        if jpeg:
            resp = HttpResponse(jpeg, content_type="image/jpeg")
            resp["Cache-Control"] = "no-store"
            resp["X-Frame-Timestamp"] = str(frame_ts or "")
            resp["X-Stream-Status"] = "connected"
            resp["X-Preview-Source"] = "backend_rtsp_worker"
            return resp

    return Response(
        {
            "status": "warming_up",
            "last_error": last_error or "preview_unavailable",
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def community_activity(request):
    """Unified tenant timeline for dashboard/community activity cards."""
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()

    limit = min(int(request.query_params.get("limit", 20)), 100)
    events = []

    for log in AuditLog.objects.filter(tenant=tenant).select_related("actor").order_by("-created_at")[:limit]:
        actor_name = log.actor.username if log.actor else "System"
        events.append({
            "type": "audit",
            "title": log.action.replace(".", " ").replace("_", " ").title(),
            "description": log.meta.get("message") if isinstance(log.meta, dict) else "",
            "timestamp": log.created_at,
            "actor": actor_name,
            "related_type": log.target_type,
            "related_id": log.target_id,
        })

    for incident in Incident.objects.filter(tenant=tenant).select_related("camera").order_by("-updated_at")[:limit]:
        status_label = incident.get_status_display()
        events.append({
            "type": "incident",
            "title": f"Incident {incident.get_type_display()} {status_label}",
            "description": f"Camera: {incident.camera.name}",
            "timestamp": incident.updated_at,
            "actor": None,
            "related_type": "incident",
            "related_id": str(incident.id),
        })

    for entity in KnownEntity.objects.filter(tenant=tenant).order_by("-updated_at")[:limit]:
        events.append({
            "type": "entity",
            "title": f"Entity updated: {entity.name}",
            "description": f"Category: {entity.category}",
            "timestamp": entity.updated_at,
            "actor": None,
            "related_type": "entity",
            "related_id": str(entity.id),
        })

    for camera in Camera.objects.filter(tenant=tenant).order_by("-updated_at")[:limit]:
        events.append({
            "type": "camera",
            "title": f"Camera updated: {camera.name}",
            "description": f"Status: {camera.status}",
            "timestamp": camera.updated_at,
            "actor": None,
            "related_type": "camera",
            "related_id": str(camera.id),
        })

    for inv in Invitation.objects.filter(tenant=tenant).select_related("invited_by").order_by("-updated_at")[:limit]:
        inviter = inv.invited_by.username if inv.invited_by else "System"
        events.append({
            "type": "invitation",
            "title": f"Invitation {inv.status}",
            "description": f"{inv.email} ({inv.role})",
            "timestamp": inv.updated_at,
            "actor": inviter,
            "related_type": "invitation",
            "related_id": str(inv.id),
        })

    events.sort(key=lambda item: item["timestamp"], reverse=True)
    payload = []
    for item in events[:limit]:
        payload.append({
            **item,
            "timestamp": item["timestamp"].isoformat() if item["timestamp"] else None,
        })
    return Response(payload)


def _mjpeg_generator(camera_id: int, fps: int):
    interval = 1.0 / max(1, fps)
    boundary = b"--frame\r\n"
    try:
        while True:
            STREAM_WORKERS.touch(camera_id)
            jpeg, _frame_ts, _err = STREAM_WORKERS.get_latest_jpeg(camera_id)
            if jpeg:
                yield (
                    boundary
                    + b"Content-Type: image/jpeg\r\nContent-Length: "
                    + str(len(jpeg)).encode()
                    + b"\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )
            time.sleep(interval)
    finally:
        STREAM_WORKERS.remove_viewer(camera_id)


@api_view(["GET"])
@permission_classes([])
def streams_mjpeg(request, camera_id):
    """
    CLOUD FIX: Django no longer spawns stateful OpenCV threads.
    It simply validates the token and acts as a stateless reverse proxy 
    or redirects to the dedicated streaming server (MediaMTX).
    """
    token_param = request.GET.get("token", "")
    ok, _payload = _verify_stream_token(token_param, camera_id) if token_param else (False, {})
    if not ok:
        return Response({"error": "Invalid stream token"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        camera = Camera.objects.get(pk=camera_id)
    except Camera.DoesNotExist:
        return Response({"error": "Camera not found"}, status=status.HTTP_404_NOT_FOUND)

    # Resolve stream_path from canonical relay desired state
    from api.models import MediaMTXDesiredPath
    desired_path = MediaMTXDesiredPath.objects.filter(
        camera=camera, desired_enabled=True
    ).first()
    stream_path = desired_path.stream_path if desired_path else (camera.stream_path or f"cam_{camera.id}")

    mediamtx_url = get_mediamtx_external_url()

    # Redirect directly to MediaMTX API for the HLS/WebRTC/MJPEG feed
    from django.shortcuts import redirect
    return redirect(f"{mediamtx_url}/{stream_path}/stream")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def streams_health(request):
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()

    camera_ids = list(Camera.objects.filter(tenant=tenant).values_list("id", flat=True))
    cfg = _stream_preview_config()
    return Response(STREAM_WORKERS.health_for_cameras(camera_ids, default_fps=cfg["fps"]))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §4  NOTIFICATION SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logger = logging.getLogger(__name__)


@api_view(["GET", "PUT", "PATCH"])
@permission_classes([IsAuthenticated])
def notification_settings(request):
    """GET/PATCH /api/notifications/settings/ — tenant channel prefs + user instant-alert preferences."""
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    settings_snapshot = notification_policy_service.get_notification_settings(
        tenant=tenant,
        user=request.user,
    )
    channel = settings_snapshot["channel"]
    instant_levels = settings_snapshot["instant_notification_levels"]

    if request.method == "GET":
        return Response({
            **NotificationChannelSerializer(channel).data,
            "instant_notification_levels": instant_levels,
            "available_instant_notification_levels": [
                {"value": "critical", "label": "Critical"},
                {"value": "severe", "label": "Severe"},
                {"value": "moderate", "label": "Moderate"},
                {"value": "low", "label": "Low"},
                {"value": "info", "label": "Info"},
            ],
        })

    channel_payload = dict(request.data)
    instant_levels = channel_payload.pop("instant_notification_levels", None)

    if channel_payload:
        assert_role_in(request, tenant, allowed_roles={"owner", "admin"})

    updated_snapshot = notification_policy_service.set_notification_settings(
        tenant=tenant,
        user=request.user,
        channel_payload=channel_payload,
        instant_levels=instant_levels,
    )
    channel = updated_snapshot["channel"]
    instant_levels = updated_snapshot["instant_notification_levels"]

    return Response({
        **NotificationChannelSerializer(channel).data,
        "instant_notification_levels": instant_levels,
        "available_instant_notification_levels": [
            {"value": "critical", "label": "Critical"},
            {"value": "severe", "label": "Severe"},
            {"value": "moderate", "label": "Moderate"},
            {"value": "low", "label": "Low"},
            {"value": "info", "label": "Info"},
        ],
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_test(request):
    """POST /api/notifications/test/ — send a test notification."""
    from django.core.mail import send_mail as django_send_mail

    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    assert_role_in(request, tenant, allowed_roles={"owner", "admin"})
    channel = notification_policy_service.get_notification_settings(
        tenant=tenant,
        user=request.user,
    )["channel"]
    results = {"email": None, "push": None}
    if channel.email_enabled and channel.email_recipients:
        try:
            django_send_mail(
                subject="[VigilZone] Test Notification",
                message="This is a test notification from VigilZone.",
                from_email=None,  # uses DEFAULT_FROM_EMAIL
                recipient_list=channel.email_recipients,
            )
            results["email"] = "sent"
        except Exception as exc:
            results["email"] = f"error: {exc}"
    else:
        results["email"] = "disabled or no recipients"

    if channel.push_enabled and channel.fcm_tokens:
        results["push"] = "placeholder — FCM not configured yet"
    else:
        results["push"] = "disabled or no tokens"

    return Response(results)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_register_device(request):
    """POST /api/notifications/register_device/ — store FCM token."""
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    token = request.data.get("token", "").strip()
    if not token:
        return Response({"error": "token required"}, status=status.HTTP_400_BAD_REQUEST)
    channel = notification_policy_service.register_device_token(tenant=tenant, token=token)
    tokens = list(channel.fcm_tokens or [])
    return Response({"stored": True, "total_tokens": len(tokens)})


def dispatch_notifications(incident: Incident):
    """Fire notifications for an incident.
    Now broadcasts to all tenant members via WebSocket + creates alerts."""
    NotificationService.broadcast_incident(incident)


def _bool_from_env(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_bool(raw, default=False):
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _ensure_tenant_webcam_camera(tenant: Tenant, enabled: bool | None = None) -> Camera:
    """Ensure a stable cam_live record exists for the tenant."""
    return camera_config_service.ensure_webcam_camera(tenant=tenant, enabled=enabled)


def _set_ai_webcam_runtime(enabled: bool, tenant: Tenant | None = None) -> dict:
    import requests as http_client

    from api.services.mediamtx_helpers import get_ai_base_url
    ai_base = get_ai_base_url()
    control_url = f"{ai_base}/api/v1/cameras/cam_live/runtime-control"
    status_url = f"{ai_base}/api/v1/cameras/cam_live/runtime-status"

    timeout_s = 25
    control_payload: bool | dict = bool(enabled)
    if tenant is not None:
        control_payload = {
            "enabled": bool(enabled),
            "tenant_id": tenant.id,
            "camera_id": "cam_live",
            "source_type": "webcam",
        }
    resp = http_client.post(control_url, json=control_payload, timeout=timeout_s)
    if resp.status_code >= 400:
        raise RuntimeError(f"AI runtime-control failed: {resp.status_code} {resp.text[:200]}")

    status_payload = {"running": None}
    try:
        status_resp = http_client.get(status_url, timeout=5)
        if status_resp.ok:
            status_payload = status_resp.json() or status_payload
    except Exception:
        pass

    result = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    result.update({"status": status_payload})
    return result


def _get_ai_webcam_runtime_status() -> dict:
    import requests as http_client

    from api.services.mediamtx_helpers import get_ai_base_url
    ai_base = get_ai_base_url()
    status_url = f"{ai_base}/api/v1/cameras/cam_live/runtime-status"
    resp = http_client.get(status_url, timeout=5)
    if resp.status_code >= 400:
        raise RuntimeError(f"AI runtime-status failed: {resp.status_code} {resp.text[:200]}")
    payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return payload or {"running": None}


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def ai_webcam_state(request):
    """
    GET  /api/ai/webcam-state/ — persisted + runtime webcam state for cam_live.
    POST /api/ai/webcam-state/ — update persisted webcam state and apply runtime toggle.
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()

    runtime_setting = runtime_registration_service.get_or_create_tenant_runtime_setting(tenant=tenant)

    if request.method == "POST":
        assert_role_in(request, tenant, allowed_roles={"owner", "admin"})
        if "enabled" not in request.data:
            return Response({"error": "enabled is required"}, status=status.HTTP_400_BAD_REQUEST)

        enabled = _coerce_bool(request.data.get("enabled"), default=False)
        webcam_camera = _ensure_tenant_webcam_camera(tenant, enabled=enabled)
        runtime_setting = runtime_registration_service.set_webcam_enabled(
            tenant=tenant,
            enabled=enabled,
        )

        try:
            ai_result = _set_ai_webcam_runtime(enabled, tenant=tenant)
            runtime_payload = ai_result.get("status", {}) or {}
            running = runtime_payload.get("running")
            runtime_registration_service.mark_ai_camera_observed_state(
                camera=webcam_camera,
                running=bool(running) if isinstance(running, bool) else None,
                ingest_backend="live_camera",
                sample_hz=None,
                lanes=list(webcam_camera.enabled_lanes or []),
            )
            desired_status = (
                Camera.Status.ACTIVE if running else Camera.Status.INACTIVE
            ) if isinstance(running, bool) else (Camera.Status.ACTIVE if enabled else Camera.Status.INACTIVE)
            if webcam_camera.status != desired_status:
                webcam_camera.status = desired_status
                webcam_camera.save(update_fields=["status", "updated_at"])
            return Response({
                "webcam_enabled": runtime_setting.webcam_enabled,
                "runtime": runtime_payload,
                "camera_id": webcam_camera.ai_camera_id,
                "camera_db_id": webcam_camera.id,
                "applied": True,
            })
        except Exception as exc:
            runtime = {"running": None}
            try:
                runtime = _get_ai_webcam_runtime_status()
                if isinstance(runtime.get("running"), bool) and runtime["running"] == enabled:
                    runtime_registration_service.mark_ai_camera_observed_state(
                        camera=webcam_camera,
                        running=runtime.get("running"),
                        ingest_backend="live_camera",
                        sample_hz=None,
                        lanes=list(webcam_camera.enabled_lanes or []),
                    )
                    desired_status = Camera.Status.ACTIVE if enabled else Camera.Status.INACTIVE
                    if webcam_camera.status != desired_status:
                        webcam_camera.status = desired_status
                        webcam_camera.save(update_fields=["status", "updated_at"])
                    return Response({
                        "webcam_enabled": runtime_setting.webcam_enabled,
                        "runtime": runtime,
                        "camera_id": webcam_camera.ai_camera_id,
                        "camera_db_id": webcam_camera.id,
                        "applied": True,
                        "warning": str(exc),
                    })
            except Exception:
                pass
            runtime_registration_service.mark_ai_camera_observed_state(
                camera=webcam_camera,
                running=None,
                ingest_backend="live_camera",
                sample_hz=None,
                lanes=list(webcam_camera.enabled_lanes or []),
                error=str(exc),
            )
            fallback_status = Camera.Status.INACTIVE if not enabled else webcam_camera.status
            if webcam_camera.status != fallback_status:
                webcam_camera.status = fallback_status
                webcam_camera.save(update_fields=["status", "updated_at"])
            return Response({
                "webcam_enabled": runtime_setting.webcam_enabled,
                "runtime": runtime,
                "camera_id": webcam_camera.ai_camera_id,
                "camera_db_id": webcam_camera.id,
                "applied": False,
                "warning": str(exc),
            }, status=status.HTTP_502_BAD_GATEWAY)

    runtime = {"running": None}
    if _bool_from_env(os.getenv("FETCH_AI_RUNTIME_STATUS", "true"), default=True):
        try:
            runtime = _get_ai_webcam_runtime_status()
        except Exception:
            runtime = {"running": None}

    return Response({
        "webcam_enabled": runtime_setting.webcam_enabled,
        "runtime": runtime,
    })


def _ensure_user_alert_backfill(tenant, user, max_incidents=300):
    """Create per-user alerts for recent incidents that have no user-scoped alert yet."""
    incidents = list(
        Incident.objects.filter(tenant=tenant)
        .select_related("camera")
        .order_by("-started_at", "-id")[:max_incidents]
    )
    if not incidents:
        return 0

    incident_ids = [inc.id for inc in incidents]
    existing_alert_incident_ids = set(
        Alert.objects.filter(incident_id__in=incident_ids)
        .filter(
            Q(payload__user_id=str(user.id))
            | Q(payload__user_id__isnull=True)
        )
        .values_list("incident_id", flat=True)
    )

    missing_incidents = [inc for inc in incidents if inc.id not in existing_alert_incident_ids]
    if not missing_incidents:
        return 0

    severity_labels = {1: "Low", 2: "Medium-Low", 3: "Medium", 4: "High", 5: "Critical"}
    profile, _ = Profile.objects.get_or_create(user=user)
    alerts = []
    for incident in missing_incidents:
        if not profile.allows_instant_notification(incident.severity):
            continue
        severity_level = severity_level_for_value(incident.severity)
        alerts.append(Alert(
            incident=incident,
            channel="realtime",
            payload={
                "title": f"🚨 {incident.get_type_display()} Detected",
                "message": f"{severity_labels.get(incident.severity, 'Unknown')} severity incident at {incident.camera.name if incident.camera else 'Unknown camera'}",
                "data": {
                    "incident_id": str(incident.id),
                    "type": incident.type,
                    "status": incident.status,
                    "severity": incident.severity,
                    "severity_level": severity_level,
                    "camera_id": incident.camera_id if not isinstance(incident.camera_id, uuid_mod.UUID) else str(incident.camera_id),
                    "camera_name": incident.camera.name if incident.camera else None,
                    "started_at": incident.started_at.isoformat() if incident.started_at else None,
                    "details": incident.details,
                },
                "severity": incident.severity,
                "severity_level": severity_level,
                "user_id": str(user.id),
                "username": user.username,
                "backfilled": True,
            }
        ))

    Alert.objects.bulk_create(alerts)
    return len(alerts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §5  REAL-TIME NOTIFICATION API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _alerts_for_user_in_tenant(user, tenant):
    """Return alerts visible to a user for a tenant, covering legacy and current storage."""
    return Alert.objects.filter(incident__tenant=tenant).filter(
        Q(user=user)
        | Q(user__isnull=True, payload__user_id=str(user.id))
        | Q(user__isnull=True, payload__user_id=user.id)
        | Q(user__isnull=True, payload__user_id__isnull=True)
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notifications_list(request):
    """
    GET /api/notifications/ — List notifications for current user.
    
    Query params:
    - limit: Max notifications to return (default 50, max 100)
    - offset: Pagination offset
    - unread_only: If 'true', only return unread notifications
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()

    _ensure_user_alert_backfill(tenant, request.user)
    
    limit = min(int(request.query_params.get("limit", 50)), 100)
    offset = int(request.query_params.get("offset", 0))
    unread_only = request.query_params.get("unread_only", "false").lower() == "true"
    
    alerts_qs = _alerts_for_user_in_tenant(request.user, tenant).select_related("incident", "incident__camera").order_by("-created_at")
    
    if unread_only:
        alerts_qs = alerts_qs.filter(delivered_at__isnull=True)
    
    total_count = alerts_qs.count()
    alerts = list(alerts_qs[offset:offset + limit])
    severity_labels = {
        5: "Critical",
        4: "High",
        3: "Medium",
        2: "Low",
        1: "Info",
    }
    
    def _safe_str(val):
        """Convert UUID or other non-serializable objects to strings."""
        if isinstance(val, uuid_mod.UUID):
            return str(val)
        return val

    notifications = []
    for alert in alerts:
        payload = alert.payload or {}
        payload_data = {k: _safe_str(v) for k, v in (payload.get("data") or {}).items()}
        incident = alert.incident
        camera_name = payload.get("camera_name") or payload_data.get("camera_name")
        if not camera_name and incident and incident.camera:
            camera_name = incident.camera.name

        incident_type_label = incident.get_type_display() if incident else "Incident"
        severity_value = payload.get("severity", incident.severity if incident else None)
        severity_label = severity_labels.get(severity_value, "Unknown") if severity_value is not None else None

        title = payload.get("title")
        if not title:
            title = f"{incident_type_label} Detected"

        message = payload.get("message")
        if not message:
            if severity_label and camera_name:
                message = f"{severity_label} severity incident at {camera_name}"
            elif camera_name:
                message = f"Incident detected at {camera_name}"
            else:
                message = "New incident detected"

        notifications.append({
            "id": str(alert.id),
            "type": "incident",
            "title": title,
            "message": message,
            "data": payload_data,
            "is_read": alert.delivered_at is not None,
            "created_at": alert.created_at.isoformat(),
            "incident_id": str(alert.incident_id) if alert.incident_id else None,
            "incident_type": incident_type_label if incident else None,
            "severity": severity_value,
            "severity_level": payload.get("severity_level") or payload_data.get("severity_level") or (severity_level_for_value(incident.severity) if incident else None),
            "camera_name": camera_name,
            "alert_id": str(alert.id),
        })
    
    return Response({
        "notifications": notifications,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notifications_mark_read(request):
    """
    POST /api/notifications/mark-read/ — Mark notifications as read.
    
    Body: { "notification_ids": [1, 2, 3] } or { "mark_all": true }
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    
    notification_ids = request.data.get("notification_ids", [])
    mark_all = request.data.get("mark_all", False)
    
    if mark_all:
        # Mark all unread notifications for this tenant as read
        updated = _alerts_for_user_in_tenant(request.user, tenant).filter(
            delivered_at__isnull=True
        ).update(delivered_at=timezone.now())
    elif notification_ids:
        # Mark specific notifications as read
        updated = _alerts_for_user_in_tenant(request.user, tenant).filter(
            id__in=notification_ids,
        ).update(delivered_at=timezone.now())
    else:
        return Response({"error": "Provide notification_ids or mark_all=true"}, status=status.HTTP_400_BAD_REQUEST)
    
    return Response({
        "marked_read": updated,
        "success": True,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notifications_unread_count(request):
    """
    GET /api/notifications/unread-count/ — Get unread notification count.
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    
    count = _alerts_for_user_in_tenant(request.user, tenant).filter(
        delivered_at__isnull=True
    ).count()
    
    return Response({"unread_count": count})


def _notification_transport_snapshot() -> dict:
    channel_cfg = settings.CHANNEL_LAYERS.get("default", {}) if hasattr(settings, "CHANNEL_LAYERS") else {}
    backend_path = str(channel_cfg.get("BACKEND", ""))
    uses_redis = "channels_redis" in backend_path
    redis_settings = resolve_backend_redis_settings()

    redis_reachable = False
    redis_error = None
    subscriber_status = None
    subscriber_healthy = False
    client = None

    try:
        client = create_redis_client(redis_settings)
        client.ping()
        redis_reachable = True
        subscriber_status = read_subscriber_status(client, redis_settings.incident_channel)
    except Exception as exc:
        redis_error = str(exc)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    subscriber_healthy = bool(subscriber_status)

    return {
        "channel_backend": backend_path,
        "uses_redis": uses_redis,
        "realtime_ready": bool(uses_redis and redis_reachable and subscriber_healthy),
        "queue_mode": redis_settings.queue_mode,
        "incident_stream": redis_settings.incident_channel,
        "incident_channel": redis_settings.incident_channel,
        "incident_consumer_group": redis_settings.incident_consumer_group,
        "incident_consumer_name": redis_settings.incident_consumer_name,
        "redis": redis_settings.to_diagnostics(),
        "redis_reachable": redis_reachable,
        "redis_error": redis_error,
        "subscriber_healthy": subscriber_healthy,
        "subscriber": subscriber_status,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notifications_transport_status(request):
    """
    GET /api/notifications/transport-status/ — report notification transport health.

    The status is healthy only when Redis is reachable and the subscriber
    heartbeat is still present.
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()

    return Response(_notification_transport_snapshot())


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notifications_broadcast(request):
    """
    POST /api/notifications/broadcast/ — Send a broadcast message to all tenant members.
    Requires owner or admin role.
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    
    assert_role_in(request, tenant, allowed_roles={"owner", "admin"})
    
    title = request.data.get("title", "").strip()
    message = request.data.get("message", "").strip()
    notification_type = request.data.get("type", "broadcast")
    
    if not title or not message:
        return Response({"error": "title and message are required"}, status=status.HTTP_400_BAD_REQUEST)
    
    result = NotificationService.broadcast_message(
        tenant_id=tenant.id,
        title=title,
        message=message,
        notification_type=notification_type,
        data=request.data.get("data", {})
    )
    
    return Response({
        "success": True,
        "result": result,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notifications_test_realtime(request):
    """
    POST /api/notifications/test-realtime/ — Send a test realtime notification.
    Useful for testing SSE connectivity.
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    assert_role_in(request, tenant, allowed_roles={"owner", "admin"})
    
    result = NotificationService.broadcast_message(
        tenant_id=tenant.id,
        title="🔔 Test Notification",
        message="This is a test notification to verify realtime connectivity.",
        notification_type="test",
        data={
            "test": True,
            "user_id": request.user.id,
            "username": request.user.username,
        }
    )
    
    return Response({
        "success": True,
        "result": result,
        "message": "Test notification sent to all connected clients"
    })

# Backwards compatibility alias
notifications_test_websocket = notifications_test_realtime

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notifications_test_incident(request):
    """
    POST /api/notifications/test-incident/ — append a synthetic incident to Redis Streams.
    This exercises the canonical AI -> Redis stream -> subscriber -> SSE/browser live transport path.
    """
    tenant = get_active_tenant(request)
    if not assert_member(request, tenant):
        raise PermissionDenied()
    assert_role_in(request, tenant, allowed_roles={"owner", "admin"})

    camera_token = str(request.data.get("camera_id", "")).strip()
    camera = None
    if camera_token:
        camera = (
            Camera.objects.filter(tenant=tenant)
            .filter(
                Q(ai_camera_id=camera_token)
                | Q(stream_path=camera_token)
                | Q(name=camera_token)
            )
            .first()
        )
        if camera is None and camera_token.isdigit():
            camera = Camera.objects.filter(pk=int(camera_token), tenant=tenant).first()
    if camera is None:
        camera = (
            Camera.objects.filter(tenant=tenant, status=Camera.Status.ACTIVE)
            .order_by("id")
            .first()
            or Camera.objects.filter(tenant=tenant).order_by("id").first()
        )
    if camera is None:
        return Response(
            {"error": "No camera available for this tenant"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    incident_type = str(request.data.get("type", "intrusion")).strip() or "intrusion"
    try:
        severity = max(1, min(5, int(request.data.get("severity", 4))))
    except (TypeError, ValueError):
        severity = 4

    event = build_test_incident_event(
        camera_id=camera.ai_camera_id or camera.stream_path or camera.name,
        tenant_id=tenant.id,
        incident_type=incident_type,
        severity=severity,
    )
    redis_settings = resolve_backend_redis_settings()

    try:
        client = create_redis_client(redis_settings)
        client.ping()
        stream_entry_id = append_incident_event(client, redis_settings.incident_channel, event)
        current_stream_length = stream_length(client, redis_settings.incident_channel)
        subscriber_status = read_subscriber_status(client, redis_settings.incident_channel)
        client.close()
    except Exception as exc:
        return Response(
            {
                "success": False,
                "error": str(exc),
                "redis": redis_settings.to_diagnostics(),
                "incident_stream": redis_settings.incident_channel,
                "incident_channel": redis_settings.incident_channel,
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response({
        "success": True,
        "queued": True,
        "event_id": event["data"]["id"],
        "stream_entry_id": stream_entry_id,
        "stream_length": current_stream_length,
        "camera_id": event["data"]["camera_id"],
        "incident_stream": redis_settings.incident_channel,
        "incident_channel": redis_settings.incident_channel,
        "redis": redis_settings.to_diagnostics(),
        "subscriber": subscriber_status,
    })


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §7  DEBUG SYSTEM ENDPOINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DJANGO_START = timezone.now()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def debug_system(request):
    """GET /api/debug/system/ — aggregated system diagnostics with AI fallbacks."""
    import requests as http_client

    # Try multiple AI base URLs in order
    from api.services.mediamtx_helpers import get_ai_base_url
    ai_candidates = [get_ai_base_url()]
    # Deduplicate while preserving order
    seen = set()
    ai_urls = []
    for u in ai_candidates:
        u = u.rstrip("/")
        if u not in seen:
            seen.add(u)
            ai_urls.append(u)

    # Django info
    django_uptime = (timezone.now() - _DJANGO_START).total_seconds()
    db_ok = False
    try:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass

    # AI info — try each URL until one works
    ai_reachable = False
    ai_error = None
    ai_base_used = None
    ai_status = None
    ai_cameras = None

    for base_url in ai_urls:
        try:
            r = http_client.get(f"{base_url}/api/v1/health", timeout=3)
            if r.status_code == 200:
                ai_reachable = True
                ai_base_used = base_url
                break
        except Exception:
            continue

    if ai_reachable and ai_base_used:
        try:
            r = http_client.get(f"{ai_base_used}/api/v1/system/status", timeout=5)
            if r.status_code == 200:
                ai_status = r.json()
        except Exception as exc:
            ai_error = f"status fetch failed: {exc}"
        try:
            r = http_client.get(f"{ai_base_used}/api/v1/cameras", timeout=5)
            if r.status_code == 200:
                ai_cameras = r.json()
        except Exception:
            pass
    else:
        ai_error = f"AI unreachable at all candidates: {ai_urls}"

    # Fallback camera data from Django DB
    django_cameras = None
    if not ai_cameras:
        tenant = get_active_tenant(request, required=False)
        if tenant:
            django_cameras = list(
                Camera.objects.filter(tenant=tenant).values(
                    "id", "name", "status", "ai_camera_id", "stream_path"
                )
            )

    return Response({
        "django": {
            "uptime_seconds": round(django_uptime),
            "db_ok": db_ok,
            "debug_mode": os.getenv("DJANGO_DEBUG", "1") == "1",
        },
        "notifications": _notification_transport_snapshot(),
        "ai": ai_status,
        "ai_cameras": ai_cameras,
        "ai_reachable": ai_reachable,
        "ai_error": ai_error,
        "ai_base_used": ai_base_used,
        "ai_urls_tried": ai_urls,
        "django_cameras": django_cameras,
    })
