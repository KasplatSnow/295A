import os
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - handled at runtime
    cv2 = None


@dataclass
class WorkerHealth:
    connected: bool
    last_frame_ts: float | None
    last_error: str
    fps_config: int
    viewers: int


class StreamWorker:
    def __init__(
        self,
        *,
        camera_id: int,
        source: str,
        fps: int,
        max_width: int,
        jpeg_quality: int,
        idle_ttl_s: int,
        ffmpeg_capture_options: str,
    ) -> None:
        self.camera_id = camera_id
        self.source = source
        self.fps = max(1, int(fps))
        self.max_width = max(0, int(max_width))
        self.jpeg_quality = min(95, max(30, int(jpeg_quality)))
        self.idle_ttl_s = max(10, int(idle_ttl_s))
        self.ffmpeg_capture_options = ffmpeg_capture_options.strip()

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._latest_jpeg: bytes | None = None
        self._last_frame_ts: float | None = None
        self._last_error: str = ""
        self._connected = False
        self._viewers = 0
        self._last_access_ts = time.time()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"stream-worker-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout_s)

    def touch(self) -> None:
        with self._lock:
            self._last_access_ts = time.time()

    def add_viewer(self) -> None:
        with self._lock:
            self._viewers += 1
            self._last_access_ts = time.time()

    def remove_viewer(self) -> None:
        with self._lock:
            self._viewers = max(0, self._viewers - 1)
            self._last_access_ts = time.time()

    def get_latest(self) -> tuple[bytes | None, float | None, str]:
        with self._lock:
            return self._latest_jpeg, self._last_frame_ts, self._last_error

    def health(self) -> WorkerHealth:
        with self._lock:
            return WorkerHealth(
                connected=self._connected,
                last_frame_ts=self._last_frame_ts,
                last_error=self._last_error,
                fps_config=self.fps,
                viewers=self._viewers,
            )

    def _should_idle_stop(self) -> bool:
        with self._lock:
            idle_for = time.time() - self._last_access_ts
            return self._viewers <= 0 and idle_for > self.idle_ttl_s

    def _set_error(self, msg: str) -> None:
        with self._lock:
            self._connected = False
            self._last_error = msg[:500]

    def _set_frame(self, jpeg: bytes) -> None:
        now = time.time()
        with self._lock:
            self._latest_jpeg = jpeg
            self._last_frame_ts = now
            self._connected = True
            self._last_error = ""

    def _open_capture(self):
        if cv2 is None:
            raise RuntimeError("opencv-python-headless is not installed")

        source = (self.source or "").strip()
        use_ffmpeg = "://" in source

        # Common webcam convention: camera.rtsp_url == "0" means local device index 0.
        if source.isdigit():
            return cv2.VideoCapture(int(source))

        if use_ffmpeg:
            if self.ffmpeg_capture_options:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self.ffmpeg_capture_options
            return cv2.VideoCapture(source, cv2.CAP_FFMPEG)

        return cv2.VideoCapture(source)

    def _run(self) -> None:
        interval = 1.0 / max(1, self.fps)
        cap = None
        try:
            while not self._stop_event.is_set():
                if self._should_idle_stop():
                    break

                if cap is None or not cap.isOpened():
                    try:
                        cap = self._open_capture()
                    except Exception as exc:
                        self._set_error(f"open failed: {exc}")
                        time.sleep(min(2.0, interval))
                        continue

                    if cap is None or not cap.isOpened():
                        self._set_error("open failed: capture not opened")
                        time.sleep(min(2.0, interval))
                        continue

                t0 = time.time()
                ok, frame = cap.read()
                if not ok or frame is None:
                    self._set_error("capture read failed; reconnecting")
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    time.sleep(min(1.0, interval))
                    continue

                if self.max_width > 0 and frame.shape[1] > self.max_width:
                    ratio = self.max_width / float(frame.shape[1])
                    target_h = max(1, int(frame.shape[0] * ratio))
                    frame = cv2.resize(frame, (self.max_width, target_h), interpolation=cv2.INTER_AREA)

                ok, enc = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)],
                )
                if not ok:
                    self._set_error("jpeg encoding failed")
                else:
                    self._set_frame(enc.tobytes())

                sleep_for = interval - (time.time() - t0)
                if sleep_for > 0:
                    time.sleep(sleep_for)
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            with self._lock:
                self._connected = False


class StreamWorkerManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: dict[int, StreamWorker] = {}

    def _resolve_camera_source(self, camera: Any) -> str:
        from api.models import AIRuntimeRegistration, MediaMTXDesiredPath, MediaMTXObservedPathState
        from api.services.mediamtx_helpers import (
            _is_publisher_source_type,
            classify_camera_source,
            get_mediamtx_loopback_url,
            is_self_referential,
            sanitize_stream_url,
        )

        direct_source = sanitize_stream_url(getattr(camera, "rtsp_url", "") or "")
        stream_path = (getattr(camera, "stream_path", "") or "").strip()

        if _is_publisher_source_type(camera):
            return get_mediamtx_loopback_url(stream_path) if stream_path else (direct_source or "0")

        direct_source_safe = bool(direct_source) and not is_self_referential(direct_source)
        direct_kind = classify_camera_source(direct_source) if direct_source else ""
        stored_kind = str(getattr(camera, "source_kind", "") or "").lower()
        direct_http_kind = direct_kind in {"mjpeg", "snapshot"} or (
            stored_kind in {"mjpeg", "snapshot"}
            and direct_source.lower().startswith(("http://", "https://"))
        )
        if direct_source_safe and direct_http_kind:
            return direct_source

        runtime_registration = getattr(camera, "runtime_registration", None)
        if runtime_registration is None:
            runtime_registration = AIRuntimeRegistration.objects.filter(camera=camera).only("desired_enabled").first()
        ai_synced = bool(runtime_registration and runtime_registration.desired_enabled)

        desired_path = getattr(camera, "mediamtx_desired_path", None)
        if desired_path is None:
            desired_path = MediaMTXDesiredPath.objects.filter(camera=camera).only("id", "desired_enabled").first()

        observed_state = getattr(desired_path, "observed_state", None) if desired_path else None
        if desired_path is not None and observed_state is None:
            observed_state = MediaMTXObservedPathState.objects.filter(desired_path=desired_path).only(
                "observed_enabled",
                "last_error",
            ).first()

        relay_ready = bool(
            stream_path
            and desired_path
            and desired_path.desired_enabled
            and observed_state
            and observed_state.observed_enabled
            and not (observed_state.last_error or "").strip()
        )

        if direct_source_safe and (not ai_synced or not relay_ready):
            return direct_source

        if stream_path:
            return get_mediamtx_loopback_url(stream_path)

        return direct_source or "0"

    def _prune(self) -> None:
        with self._lock:
            dead = [cid for cid, w in self._workers.items() if not w.running and w.health().viewers <= 0]
            for cid in dead:
                self._workers.pop(cid, None)

    def ensure_running(self, camera: Any, *, fps: int, max_width: int, jpeg_quality: int, idle_ttl_s: int, ffmpeg_capture_options: str) -> StreamWorker:
        camera_id = int(camera.pk)
        source = self._resolve_camera_source(camera)

        with self._lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                worker = StreamWorker(
                    camera_id=camera_id,
                    source=source,
                    fps=fps,
                    max_width=max_width,
                    jpeg_quality=jpeg_quality,
                    idle_ttl_s=idle_ttl_s,
                    ffmpeg_capture_options=ffmpeg_capture_options,
                )
                self._workers[camera_id] = worker

        worker.touch()
        worker.start()
        self._prune()
        return worker

    def add_viewer(self, camera_id: int) -> None:
        with self._lock:
            worker = self._workers.get(int(camera_id))
        if worker:
            worker.add_viewer()

    def remove_viewer(self, camera_id: int) -> None:
        with self._lock:
            worker = self._workers.get(int(camera_id))
        if worker:
            worker.remove_viewer()

    def touch(self, camera_id: int) -> None:
        with self._lock:
            worker = self._workers.get(int(camera_id))
        if worker:
            worker.touch()

    def get_latest_jpeg(self, camera_id: int) -> tuple[bytes | None, float | None, str]:
        with self._lock:
            worker = self._workers.get(int(camera_id))
        if not worker:
            return None, None, "worker_not_running"
        return worker.get_latest()

    def health_for_cameras(self, camera_ids: list[int], default_fps: int) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        with self._lock:
            workers = dict(self._workers)

        for cid in camera_ids:
            worker = workers.get(int(cid))
            if not worker:
                result[str(cid)] = {
                    "connected": False,
                    "last_frame_ts": None,
                    "last_error": "worker_not_running",
                    "fps_config": default_fps,
                    "viewers": 0,
                }
                continue
            h = worker.health()
            result[str(cid)] = {
                "connected": h.connected,
                "last_frame_ts": h.last_frame_ts,
                "last_error": h.last_error,
                "fps_config": h.fps_config,
                "viewers": h.viewers,
            }

        self._prune()
        return result


STREAM_WORKERS = StreamWorkerManager()
