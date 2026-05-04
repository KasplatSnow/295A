from __future__ import annotations

from django.db import transaction
from django.utils.text import slugify

from api.models import Camera, CameraZone, MediaMTXDesiredPath, Tenant
from api.repositories.camera_repository import CameraConfigRepository
from api.repositories.runtime_repository import RuntimeRepository
from api.services.mediamtx_helpers import is_self_referential, sanitize_stream_url
from api.services.outbox_service import OutboxService


class CameraConfigService:
    def __init__(
        self,
        camera_repository: CameraConfigRepository | None = None,
        runtime_repository: RuntimeRepository | None = None,
        outbox_service: OutboxService | None = None,
    ):
        self.camera_repository = camera_repository or CameraConfigRepository()
        self.runtime_repository = runtime_repository or RuntimeRepository()
        self.outbox_service = outbox_service or OutboxService()

    @staticmethod
    def _resolve_source_uri(camera: Camera) -> str:
        """Return the source_uri to persist in MediaMTXDesiredPath.

        For publisher-type cameras (webcam, no URL, or self-referential
        URL) this returns an empty string — the reconciler will
        configure MediaMTX in ``source: publisher`` mode.

        For registered cameras with a real external URL, it returns
        the URL as-is.
        """
        if camera.source_type == Camera.SourceType.WEBCAM:
            return ""
        url = sanitize_stream_url(camera.rtsp_url)
        if not url or is_self_referential(url):
            return ""
        return url

    def _ensure_stream_identity(self, camera: Camera) -> Camera:
        update_fields: list[str] = []
        if not camera.stream_path:
            if camera.ai_camera_id:
                camera.stream_path = camera.ai_camera_id
            elif camera.name:
                camera.stream_path = slugify(camera.name)
            update_fields.append("stream_path")
        if not camera.ai_camera_id and camera.stream_path:
            camera.ai_camera_id = camera.stream_path
            update_fields.append("ai_camera_id")
        if update_fields:
            update_fields.append("updated_at")
            camera.save(update_fields=update_fields)
        return camera

    @transaction.atomic
    def create_camera(self, *, tenant: Tenant, attrs: dict) -> Camera:
        camera = self.camera_repository.create_camera(tenant=tenant, attrs=attrs)
        camera = self._ensure_stream_identity(camera)
        self.runtime_repository.get_or_create_ai_runtime_registration(camera=camera)
        self.runtime_repository.set_desired_mediamtx_path(
            camera=camera,
            stream_path=camera.stream_path or camera.ai_camera_id or f"camera-{camera.pk}",
            source_uri=self._resolve_source_uri(camera),
            source_kind=camera.source_kind,
            desired_enabled=camera.status == Camera.Status.ACTIVE,
            relay_mode=MediaRelayMode.from_camera(camera),
            transcode_required=MediaRelayMode.transcode_required(camera),
        )
        self.outbox_service.emit(
            aggregate_type="mediamtx_desired_path",
            aggregate_id=camera.id,
            event_type="mediamtx.desired_path_changed",
            payload={
                "tenant_id": camera.tenant_id,
                "camera_id": camera.id,
                "stream_path": camera.stream_path,
            },
        )
        self.outbox_service.emit(
            aggregate_type="camera",
            aggregate_id=camera.id,
            event_type="camera.created",
            payload={
                "tenant_id": camera.tenant_id,
                "camera_id": camera.id,
                "ai_camera_id": camera.ai_camera_id,
                "stream_path": camera.stream_path,
                "source_type": camera.source_type,
                "status": camera.status,
            },
        )
        return camera

    @transaction.atomic
    def update_camera(self, *, camera: Camera, attrs: dict) -> Camera:
        camera = self.camera_repository.update_camera(camera=camera, attrs=attrs)
        camera = self._ensure_stream_identity(camera)
        runtime_registration = self.runtime_repository.get_or_create_ai_runtime_registration(camera=camera)
        self.runtime_repository.set_ai_runtime_desired_state(
            camera=camera,
            enabled=bool(runtime_registration.desired_enabled),
            ingest_backend="opencv",
            sample_hz=2.0,
            lanes=list(camera.enabled_lanes or []),
            policy_version=1,
            metadata={
                "camera_name": camera.name,
                "tenant_id": camera.tenant_id,
                "stream_path": camera.stream_path,
            },
        )
        self.runtime_repository.set_desired_mediamtx_path(
            camera=camera,
            stream_path=camera.stream_path or camera.ai_camera_id or f"camera-{camera.pk}",
            source_uri=self._resolve_source_uri(camera),
            source_kind=camera.source_kind,
            desired_enabled=camera.status == Camera.Status.ACTIVE,
            relay_mode=MediaRelayMode.from_camera(camera),
            transcode_required=MediaRelayMode.transcode_required(camera),
        )
        self.outbox_service.emit(
            aggregate_type="mediamtx_desired_path",
            aggregate_id=camera.id,
            event_type="mediamtx.desired_path_changed",
            payload={
                "tenant_id": camera.tenant_id,
                "camera_id": camera.id,
                "stream_path": camera.stream_path,
            },
        )
        self.outbox_service.emit(
            aggregate_type="camera",
            aggregate_id=camera.id,
            event_type="camera.updated",
            payload={
                "tenant_id": camera.tenant_id,
                "camera_id": camera.id,
                "updated_fields": sorted(attrs.keys()),
                "ai_camera_id": camera.ai_camera_id,
                "stream_path": camera.stream_path,
                "status": camera.status,
            },
        )
        return camera

    @transaction.atomic
    def delete_camera(self, *, camera: Camera) -> None:
        payload = {
            "tenant_id": camera.tenant_id,
            "camera_id": camera.id,
            "ai_camera_id": camera.ai_camera_id,
            "stream_path": camera.stream_path,
        }
        # Mark relay desired state as disabled so the reconciler removes the path
        try:
            desired_path = MediaMTXDesiredPath.objects.filter(camera=camera).first()
            if desired_path:
                desired_path.desired_enabled = False
                desired_path.path_generation = max(1, desired_path.path_generation + 1)
                desired_path.save(update_fields=[
                    "desired_enabled", "path_generation", "updated_at",
                ])
                self.outbox_service.emit(
                    aggregate_type="mediamtx_desired_path",
                    aggregate_id=camera.id,
                    event_type="mediamtx.desired_path_disabled",
                    payload={
                        "tenant_id": camera.tenant_id,
                        "camera_id": camera.id,
                        "stream_path": desired_path.stream_path,
                        "path_generation": int(desired_path.path_generation),
                    },
                )
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to mark MediaMTX desired path disabled for camera %s",
                camera.id,
            )
        self.camera_repository.delete_camera(camera=camera)
        self.outbox_service.emit(
            aggregate_type="camera",
            aggregate_id=payload["camera_id"],
            event_type="camera.deleted",
            payload=payload,
        )

    @transaction.atomic
    def ensure_webcam_camera(self, *, tenant: Tenant, enabled: bool | None = None) -> Camera:
        camera = self.camera_repository.ensure_webcam_camera(tenant=tenant, enabled=enabled)
        self.runtime_repository.get_or_create_ai_runtime_registration(camera=camera)
        return camera

    @transaction.atomic
    def create_camera_zone(self, *, camera: Camera, attrs: dict) -> CameraZone:
        zone = self.camera_repository.create_camera_zone(camera=camera, attrs=attrs)
        self.outbox_service.emit(
            aggregate_type="camera_zone",
            aggregate_id=zone.id,
            event_type="camera_zone.created",
            payload={
                "camera_id": camera.id,
                "tenant_id": camera.tenant_id,
                "zone_id": zone.id,
                "zone_name": zone.zone_name,
                "zone_type": zone.zone_type,
                "enabled": zone.enabled,
            },
        )
        return zone

    @transaction.atomic
    def update_camera_zone(self, *, zone: CameraZone, attrs: dict) -> CameraZone:
        zone = self.camera_repository.update_camera_zone(zone=zone, attrs=attrs)
        self.outbox_service.emit(
            aggregate_type="camera_zone",
            aggregate_id=zone.id,
            event_type="camera_zone.updated",
            payload={
                "camera_id": zone.camera_id,
                "tenant_id": zone.camera.tenant_id,
                "zone_id": zone.id,
                "updated_fields": sorted(attrs.keys()),
                "zone_name": zone.zone_name,
                "zone_type": zone.zone_type,
                "enabled": zone.enabled,
            },
        )
        return zone

    @transaction.atomic
    def delete_camera_zone(self, *, zone: CameraZone) -> None:
        payload = {
            "camera_id": zone.camera_id,
            "tenant_id": zone.camera.tenant_id,
            "zone_id": zone.id,
            "zone_name": zone.zone_name,
        }
        self.camera_repository.delete_camera_zone(zone=zone)
        self.outbox_service.emit(
            aggregate_type="camera_zone",
            aggregate_id=payload["zone_id"],
            event_type="camera_zone.deleted",
            payload=payload,
        )


class MediaRelayMode:
    @staticmethod
    def from_camera(camera: Camera) -> str:
        source_kind = (camera.source_kind or "").lower()
        if source_kind in {"mjpeg", "snapshot"}:
            return "transcode"
        if source_kind in {"hls"}:
            return "remux"
        return "relay_only"

    @staticmethod
    def transcode_required(camera: Camera) -> bool:
        return MediaRelayMode.from_camera(camera) == "transcode"
