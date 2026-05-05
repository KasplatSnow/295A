from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests as http_client

from api.models import Camera, MediaMTXDesiredPath
from api.services.camera_config_service import CameraConfigService, MediaRelayMode
from api.services.relay_reconciler import RelayReconciler
from api.services.mediamtx_helpers import (
    get_ai_base_url,
    get_canonical_camera_id,
    get_mediamtx_loopback_url,
)
from api.services.runtime_registration_service import RuntimeRegistrationService


class RelayNotReadyError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        detail: str,
        status_code: int,
        waited_seconds: float = 0.0,
        retryable: bool = True,
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.waited_seconds = round(float(waited_seconds), 2)
        self.retryable = retryable


@dataclass
class RelayReadyState:
    desired_path: MediaMTXDesiredPath
    waited_seconds: float


camera_config_service = CameraConfigService()
runtime_registration_service = RuntimeRegistrationService()
relay_reconciler = RelayReconciler()


def _relay_ready_timeout_seconds() -> float:
    return max(0.0, float(os.getenv("AI_SYNC_RELAY_READY_TIMEOUT_SECONDS", "25")))


def _relay_ready_poll_seconds() -> float:
    return max(0.1, float(os.getenv("AI_SYNC_RELAY_READY_POLL_SECONDS", "1.0")))


def _default_enabled_lanes(camera: Camera, enabled_lanes: list[str] | None) -> list[str]:
    if enabled_lanes:
        return list(enabled_lanes)
    if camera.enabled_lanes:
        return list(camera.enabled_lanes)
    return ["rt_detr", "person_zone"]


def _runtime_metadata(camera: Camera, stream_path: str) -> dict:
    return {
        "tenant_id": str(camera.tenant.id) if camera.tenant else None,
        "community_id": str(camera.tenant.id) if camera.tenant else None,
        "stream_path": stream_path,
    }


def _get_desired_path(camera: Camera) -> MediaMTXDesiredPath | None:
    return (
        MediaMTXDesiredPath.objects
        .select_related("observed_state")
        .filter(camera=camera, desired_enabled=True)
        .first()
    )


def _evaluate_relay_state(camera: Camera) -> tuple[MediaMTXDesiredPath | None, RelayNotReadyError | None]:
    desired_path = _get_desired_path(camera)
    if desired_path is None:
        return None, RelayNotReadyError(
            code="no_active_relay_path",
            detail=(
                "No active MediaMTX relay path is available for this camera. "
                "Activate the camera for monitoring first, then retry Sync AI."
            ),
            status_code=409,
            retryable=False,
        )

    if desired_path.last_applied_generation != desired_path.path_generation:
        return desired_path, RelayNotReadyError(
            code="relay_apply_pending",
            detail=(
                "The relay path exists, but MediaMTX is still applying the latest camera changes. "
                "Retry in a few seconds."
            ),
            status_code=503,
        )

    if desired_path.last_error:
        return desired_path, RelayNotReadyError(
            code="relay_error",
            detail=(
                "The relay path is not ready yet because the last MediaMTX reconcile reported an error: "
                f"{desired_path.last_error[:200]}"
            ),
            status_code=503,
        )

    try:
        observed_state = desired_path.observed_state
    except Exception:
        observed_state = None

    if observed_state is None:
        return desired_path, RelayNotReadyError(
            code="relay_unobserved",
            detail=(
                "The relay path has been requested, but MediaMTX has not observed it yet. "
                "Retry after the reconciler catches up."
            ),
            status_code=503,
        )

    if observed_state.last_error:
        return desired_path, RelayNotReadyError(
            code="relay_observe_error",
            detail=(
                "The relay path is not ready because MediaMTX last reported: "
                f"{observed_state.last_error[:200]}"
            ),
            status_code=503,
        )

    if observed_state.observed_enabled is not True:
        return desired_path, RelayNotReadyError(
            code="relay_not_live",
            detail=(
                "MediaMTX has not confirmed this relay path as live yet. "
                "Retry in a few seconds."
            ),
            status_code=503,
        )

    return desired_path, None


def _ensure_desired_relay_path(camera: Camera) -> MediaMTXDesiredPath:
    desired_path = _get_desired_path(camera)
    if desired_path is not None:
        return desired_path

    if camera.status != Camera.Status.ACTIVE:
        raise RelayNotReadyError(
            code="relay_inactive_camera",
            detail=(
                "This camera is not active yet, so its relay path has not been provisioned. "
                "Activate the camera first, then retry Sync AI."
            ),
            status_code=409,
            retryable=False,
        )

    desired_path = runtime_registration_service.set_desired_mediamtx_path(
        camera=camera,
        stream_path=camera.stream_path or camera.ai_camera_id or f"camera-{camera.pk}",
        source_uri=camera_config_service._resolve_source_uri(camera),
        source_kind=camera.source_kind or "",
        desired_enabled=True,
        relay_mode=MediaRelayMode.from_camera(camera),
        transcode_required=MediaRelayMode.transcode_required(camera),
    )

    try:
        relay_reconciler.reconcile_one(desired_path)
    except Exception:
        # Best-effort bootstrap; readiness polling below will surface any remaining issue.
        pass

    return desired_path


def _best_effort_reconcile(desired_path: MediaMTXDesiredPath | None) -> None:
    if desired_path is None:
        return
    try:
        relay_reconciler.reconcile_one(desired_path)
    except Exception:
        # Readiness polling will surface the authoritative error state.
        pass


def wait_for_relay_readiness(
    camera: Camera,
    *,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> RelayReadyState:
    timeout_s = _relay_ready_timeout_seconds() if timeout_seconds is None else max(0.0, timeout_seconds)
    poll_s = _relay_ready_poll_seconds() if poll_interval_seconds is None else max(0.1, poll_interval_seconds)
    started = time.monotonic()
    last_error: RelayNotReadyError | None = None

    while True:
        desired_path, error = _evaluate_relay_state(camera)
        waited = time.monotonic() - started

        if error is None and desired_path is not None:
            return RelayReadyState(desired_path=desired_path, waited_seconds=waited)

        if error is not None:
            last_error = error
            last_error.waited_seconds = round(waited, 2)
            if error.status_code == 409 and not error.retryable:
                raise last_error
            _best_effort_reconcile(desired_path)

        if waited >= timeout_s:
            if last_error is None:
                raise RelayNotReadyError(
                    code="relay_wait_timeout",
                    detail="Timed out waiting for MediaMTX relay readiness.",
                    status_code=503,
                    waited_seconds=waited,
                )
            raise last_error

        time.sleep(poll_s)


def build_ai_registration_payload(
    camera: Camera,
    *,
    stream_path: str,
    ingest_backend: str = "opencv",
    enabled_lanes: list[str] | None = None,
    sample_hz: float = 2.0,
    policy_version: int = 1,
) -> dict:
    lanes = _default_enabled_lanes(camera, enabled_lanes)
    return {
        "camera_id": stream_path,
        "camera_name": camera.name,
        "rtsp_url": get_mediamtx_loopback_url(stream_path),
        "stream_path": stream_path,
        "ingest_backend": str(ingest_backend or "opencv"),
        "enabled_lanes": lanes,
        "sample_hz": float(sample_hz),
        "tenant_id": str(camera.tenant.id) if camera.tenant else None,
        "community_id": str(camera.tenant.id) if camera.tenant else None,
        "policy_version": int(policy_version),
    }


def _read_runtime_status(ai_base: str, camera_id: str, fallback_running: bool) -> dict:
    runtime_status = {"running": fallback_running}
    try:
        status_resp = http_client.get(
            f"{ai_base}/api/v1/cameras/{camera_id}/runtime-status",
            timeout=5,
        )
        if status_resp.ok:
            runtime_status = status_resp.json() or runtime_status
    except Exception:
        pass
    return runtime_status


def start_ai_runtime(
    camera: Camera,
    *,
    ingest_backend: str = "opencv",
    enabled_lanes: list[str] | None = None,
    sample_hz: float = 2.0,
    policy_version: int = 1,
) -> dict:
    _ensure_desired_relay_path(camera)
    ready_state = wait_for_relay_readiness(camera)
    stream_path = ready_state.desired_path.stream_path
    payload = build_ai_registration_payload(
        camera,
        stream_path=stream_path,
        ingest_backend=ingest_backend,
        enabled_lanes=enabled_lanes,
        sample_hz=sample_hz,
        policy_version=policy_version,
    )
    ai_base = get_ai_base_url()

    register_resp = http_client.post(
        f"{ai_base}/api/v1/cameras/register",
        json=payload,
        timeout=15,
    )
    if register_resp.status_code not in (200, 201):
        runtime_registration_service.mark_ai_camera_observed_state(
            camera=camera,
            running=False,
            ingest_backend=str(payload["ingest_backend"]),
            sample_hz=float(payload["sample_hz"]),
            lanes=list(payload["enabled_lanes"]),
            error=f"AI register failed: {register_resp.status_code}",
        )
        raise RuntimeError(
            f"AI register failed: {register_resp.status_code} {register_resp.text[:200]}"
        )

    runtime_resp = http_client.post(
        f"{ai_base}/api/v1/cameras/{stream_path}/runtime-control",
        json=True,
        timeout=10,
    )
    if runtime_resp.status_code >= 400:
        runtime_registration_service.mark_ai_camera_observed_state(
            camera=camera,
            running=False,
            ingest_backend=str(payload["ingest_backend"]),
            sample_hz=float(payload["sample_hz"]),
            lanes=list(payload["enabled_lanes"]),
            error=f"AI runtime-control failed: {runtime_resp.status_code}",
        )
        raise RuntimeError(
            f"AI runtime-control failed: {runtime_resp.status_code} {runtime_resp.text[:200]}"
        )

    runtime_status = _read_runtime_status(ai_base, stream_path, True)
    if not bool(runtime_status.get("running")):
        runtime_registration_service.mark_ai_camera_observed_state(
            camera=camera,
            running=False,
            ingest_backend=str(payload["ingest_backend"]),
            sample_hz=float(payload["sample_hz"]),
            lanes=list(payload["enabled_lanes"]),
            error="AI runtime did not start",
        )
        raise RuntimeError("AI runtime did not start")

    update_attrs: dict = {}
    if camera.ai_camera_id != stream_path:
        update_attrs["ai_camera_id"] = stream_path
    if camera.stream_path != stream_path:
        update_attrs["stream_path"] = stream_path
    if update_attrs:
        camera_config_service.update_camera(camera=camera, attrs=update_attrs)

    runtime_registration_service.register_ai_camera_desired_state(
        camera=camera,
        enabled=True,
        ingest_backend=str(payload["ingest_backend"]),
        sample_hz=float(payload["sample_hz"]),
        lanes=list(payload["enabled_lanes"]),
        policy_version=int(payload["policy_version"]),
        metadata=_runtime_metadata(camera, stream_path),
    )
    runtime_registration_service.mark_ai_camera_observed_state(
        camera=camera,
        running=True,
        ingest_backend=str(payload["ingest_backend"]),
        sample_hz=float(payload["sample_hz"]),
        lanes=list(payload["enabled_lanes"]),
    )

    hot_loaded = False
    try:
        hot_loaded = bool((register_resp.json() or {}).get("hot_loaded", False))
    except Exception:
        hot_loaded = False

    return {
        "camera_db_id": camera.id,
        "camera_id": stream_path,
        "name": camera.name,
        "stream_path": camera.stream_path or stream_path,
        "loopback_rtsp_url": payload["rtsp_url"],
        "rtsp_url_sent": payload["rtsp_url"],
        "path_name": stream_path,
        "running": True,
        "runtime": runtime_status,
        "hot_loaded": hot_loaded,
        "waited_seconds": round(ready_state.waited_seconds, 2),
        "payload": payload,
    }


def stop_ai_runtime(
    camera: Camera,
    *,
    ingest_backend: str = "opencv",
    enabled_lanes: list[str] | None = None,
    sample_hz: float = 2.0,
    policy_version: int = 1,
) -> dict:
    stream_path = camera.stream_path or camera.ai_camera_id or get_canonical_camera_id(camera)
    lanes = _default_enabled_lanes(camera, enabled_lanes)
    ai_base = get_ai_base_url()

    runtime_registration_service.register_ai_camera_desired_state(
        camera=camera,
        enabled=False,
        ingest_backend=str(ingest_backend or "opencv"),
        sample_hz=float(sample_hz),
        lanes=lanes,
        policy_version=int(policy_version),
        metadata=_runtime_metadata(camera, stream_path),
    )

    runtime_resp = http_client.post(
        f"{ai_base}/api/v1/cameras/{stream_path}/runtime-control",
        json=False,
        timeout=10,
    )
    if runtime_resp.status_code >= 400:
        runtime_registration_service.mark_ai_camera_observed_state(
            camera=camera,
            running=None,
            ingest_backend=str(ingest_backend or "opencv"),
            sample_hz=float(sample_hz),
            lanes=lanes,
            error=f"AI runtime-control failed: {runtime_resp.status_code}",
        )
        raise RuntimeError(
            f"AI runtime-control failed: {runtime_resp.status_code} {runtime_resp.text[:200]}"
        )

    runtime_status = _read_runtime_status(ai_base, stream_path, False)
    if bool(runtime_status.get("running")):
        runtime_registration_service.mark_ai_camera_observed_state(
            camera=camera,
            running=True,
            ingest_backend=str(ingest_backend or "opencv"),
            sample_hz=float(sample_hz),
            lanes=lanes,
            error="AI runtime did not stop",
        )
        raise RuntimeError("AI runtime did not stop")

    runtime_registration_service.mark_ai_camera_observed_state(
        camera=camera,
        running=False,
        ingest_backend=str(ingest_backend or "opencv"),
        sample_hz=float(sample_hz),
        lanes=lanes,
    )

    return {
        "camera_db_id": camera.id,
        "camera_id": stream_path,
        "name": camera.name,
        "stream_path": camera.stream_path or stream_path,
        "loopback_rtsp_url": get_mediamtx_loopback_url(stream_path),
        "running": False,
        "runtime": runtime_status,
        "waited_seconds": 0.0,
    }
