from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils import timezone

from api.models import (
    AIRuntimeRegistration,
    Camera,
    MediaMTXDesiredPath,
    MediaMTXObservedPathState,
    Tenant,
    TenantRuntimeSetting,
)


class RuntimeRepository:
    def get_or_create_tenant_runtime_setting(self, *, tenant: Tenant) -> TenantRuntimeSetting:
        runtime_setting, _ = TenantRuntimeSetting.objects.get_or_create(tenant=tenant)
        return runtime_setting

    def set_webcam_enabled(self, *, tenant: Tenant, enabled: bool) -> TenantRuntimeSetting:
        runtime_setting = self.get_or_create_tenant_runtime_setting(tenant=tenant)
        runtime_setting.webcam_enabled = enabled
        runtime_setting.save(update_fields=["webcam_enabled", "updated_at"])
        return runtime_setting

    def set_identity_runtime_enabled(self, *, tenant: Tenant, enabled: bool) -> TenantRuntimeSetting:
        runtime_setting = self.get_or_create_tenant_runtime_setting(tenant=tenant)
        runtime_setting.identity_runtime_enabled = enabled
        runtime_setting.save(update_fields=["identity_runtime_enabled", "updated_at"])
        return runtime_setting

    def get_or_create_ai_runtime_registration(self, *, camera: Camera) -> AIRuntimeRegistration:
        defaults = {
            "desired_enabled": False,
            "desired_ingest_backend": "opencv",
            "desired_sample_hz": 2.0,
            "desired_lanes": list(camera.enabled_lanes or []),
            "desired_policy_version": 1,
        }
        registration, _ = AIRuntimeRegistration.objects.get_or_create(
            camera=camera,
            defaults=defaults,
        )
        return registration

    def set_ai_runtime_desired_state(
        self,
        *,
        camera: Camera,
        enabled: bool,
        ingest_backend: str,
        sample_hz: float,
        lanes: list[str],
        policy_version: int,
        metadata: dict | None = None,
    ) -> AIRuntimeRegistration:
        registration = self.get_or_create_ai_runtime_registration(camera=camera)
        registration.desired_enabled = enabled
        registration.desired_ingest_backend = ingest_backend
        registration.desired_sample_hz = sample_hz
        registration.desired_lanes = list(lanes or [])
        registration.desired_policy_version = policy_version
        if metadata is not None:
            registration.source_metadata = metadata
        registration.save(
            update_fields=[
                "desired_enabled",
                "desired_ingest_backend",
                "desired_sample_hz",
                "desired_lanes",
                "desired_policy_version",
                "source_metadata",
                "updated_at",
            ]
        )
        return registration

    def set_ai_runtime_observed_state(
        self,
        *,
        camera: Camera,
        running: bool | None,
        ingest_backend: str = "",
        sample_hz: float | None = None,
        lanes: list[str] | None = None,
        error: str = "",
    ) -> AIRuntimeRegistration:
        registration = self.get_or_create_ai_runtime_registration(camera=camera)
        registration.observed_enabled = running
        if ingest_backend:
            registration.observed_ingest_backend = ingest_backend
        if sample_hz is not None:
            registration.observed_sample_hz = sample_hz
        if lanes is not None:
            registration.observed_lanes = list(lanes)
        registration.last_error = error or ""
        registration.observed_last_seen_at = timezone.now()
        registration.save(
            update_fields=[
                "observed_enabled",
                "observed_ingest_backend",
                "observed_sample_hz",
                "observed_lanes",
                "last_error",
                "observed_last_seen_at",
                "updated_at",
            ]
        )
        return registration

    def set_desired_mediamtx_path(
        self,
        *,
        camera: Camera,
        stream_path: str,
        source_uri: str,
        source_kind: str,
        desired_enabled: bool = True,
        relay_mode: str = MediaMTXDesiredPath.RelayMode.RELAY_ONLY,
        transcode_required: bool = False,
    ) -> MediaMTXDesiredPath:
        stream_path = str(stream_path or "").strip()
        conflicting_path = (
            MediaMTXDesiredPath.objects
            .select_related("camera")
            .filter(stream_path=stream_path)
            .exclude(camera=camera)
            .first()
        )
        if conflicting_path is not None:
            if conflicting_path.camera_id is not None:
                owner = conflicting_path.camera
                owner_label = f"{owner.name} (ID {owner.pk})" if owner else f"camera ID {conflicting_path.camera_id}"
                raise ValidationError({
                    "stream_path": (
                        f"Stream path '{stream_path}' is already used by {owner_label}. "
                        "Edit that camera or choose a unique stream path."
                    )
                })

            if MediaMTXDesiredPath.objects.filter(camera=camera).exclude(pk=conflicting_path.pk).exists():
                raise ValidationError({
                    "stream_path": (
                        f"Stream path '{stream_path}' already exists as an unattached relay path, "
                        "but this camera already has a relay path."
                    )
                })

            desired_path = conflicting_path
            desired_path.camera = camera
            created = False
        else:
            desired_path, created = MediaMTXDesiredPath.objects.get_or_create(
                camera=camera,
                defaults={
                    "stream_path": stream_path,
                    "desired_enabled": desired_enabled,
                    "relay_mode": relay_mode,
                    "source_uri": source_uri,
                    "source_kind": source_kind,
                    "transcode_required": transcode_required,
                },
            )
        if not created:
            # Only bump generation when relay-significant fields actually change.
            # This prevents unnecessary cold applies after harmless saves.
            changed = False
            relay_fields = {
                "camera": camera,
                "stream_path": stream_path,
                "desired_enabled": desired_enabled,
                "relay_mode": relay_mode,
                "source_uri": source_uri,
                "source_kind": source_kind,
                "transcode_required": transcode_required,
            }
            for field_name, new_val in relay_fields.items():
                if getattr(desired_path, field_name) != new_val:
                    setattr(desired_path, field_name, new_val)
                    changed = True

            if changed:
                desired_path.path_generation = max(1, desired_path.path_generation + 1)
                desired_path.save(
                    update_fields=[
                        "stream_path",
                        "camera",
                        "desired_enabled",
                        "relay_mode",
                        "source_uri",
                        "source_kind",
                        "transcode_required",
                        "path_generation",
                        "updated_at",
                    ]
                )
        return desired_path

    def mark_observed_mediamtx_path(
        self,
        *,
        desired_path: MediaMTXDesiredPath,
        observed_enabled: bool | None,
        observed_source: str,
        observed_payload: dict | None = None,
        last_error: str = "",
    ) -> MediaMTXObservedPathState:
        observed, _ = MediaMTXObservedPathState.objects.get_or_create(
            desired_path=desired_path
        )
        observed.observed_enabled = observed_enabled
        observed.observed_source = observed_source
        observed.observed_payload = observed_payload or {}
        observed.observed_at = timezone.now()
        observed.last_error = last_error
        observed.save(
            update_fields=[
                "observed_enabled",
                "observed_source",
                "observed_payload",
                "observed_at",
                "last_error",
                "updated_at",
            ]
        )
        desired_path.last_reconciled_at = observed.observed_at
        desired_path.drift_detected = bool(last_error)
        desired_path.last_error = last_error
        desired_path.save(update_fields=["last_reconciled_at", "drift_detected", "last_error", "updated_at"])
        return observed
