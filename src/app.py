"""
Main orchestrator for CCTV AI Module (v2)
Manages camera ingestion, per-lane scheduling, GPU scheduler, auto-throttle,
AnyAnomaly subprocess, aggregation, evidence export, and alerting.

Supports:
  - Mode A: Realtime (RTSP / live webcam)
  - Mode B: Upload (offline video processing)
"""
import argparse
import time
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from pathlib import Path
from typing import Dict, List, Any, Optional
import sys
import os

# Ensure import paths
src_dir = Path(__file__).parent
parent_dir = src_dir.parent
sys.path.insert(0, str(src_dir))
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)

from src.common.config import Config
from src.common.log import setup_logger
from src.common.timeutil import now_iso_utc
from src.common.types import Observation, Alert

# Ingest backends
from src.ingest.opencv_reader import OpenCVReader
from src.ingest.ffmpeg_reader import FFmpegReader
from src.ingest.deepstream_stub import DeepStreamStub
from src.ingest.live_camera import LiveCameraReader
from src.ingest.frame_store import LatestFrameStore

# Detection lanes (legacy)
from src.lanes.person_zone import PersonZoneLane
from src.lanes.fire_smoke import FireSmokeLane
from src.lanes.violence import ViolenceLane
from src.lanes.vad_generic import VADGenericLane

# Detection lanes (new)
from src.lanes.rt_detr import RTDETRLane
from src.lanes.yolov8_fallback import YOLOv8FallbackLane
from src.lanes.fire_smoke_yolo import FireSmokeYOLOLane
from src.lanes.violence_candidate import ViolenceCandidateLane
from src.lanes.fall_candidate import FallCandidateLane
from src.lanes.yolov8_pose import YOLOv8PoseLane, PoseCache
from src.lanes.weapon_yolo import WeaponYOLOLane
from src.lanes.accident import AccidentLane
from src.lanes.anyanomaly import AnyAnomalyLane
from src.lanes.anomalyclip import AnomalyCLIPLane
from src.lanes.temporal_verifier import TemporalVerifierLane
from src.lanes.entity_identity import EntityIdentityLane

# Identity subsystem
from src.identity.store import EntityStore
from src.identity.face_embedder import FaceEmbedder
from src.identity.pet_embedder import PetEmbedder
from src.identity.matcher import IdentityMatcher
from src.identity.policy import IdentityPolicy

# Logic
from src.logic.aggregator import AlertAggregator
from src.logic.engine_loader import load_detector_engine
from src.logic.detection_cache import FrameDetectionCache

# Runtime
from src.runtime.gpu_scheduler import GPUScheduler
from src.runtime.auto_throttle import AutoThrottle
from src.runtime.doctor import Doctor
from src.runtime.device import select_device

# Services
from src.services.anyanomaly_client import AnyAnomalyClient

# Evidence
from src.evidence.ringbuffer import FrameRingBuffer
from src.evidence.exporter import EvidenceExporter

# API
from src.api.server import AlertServer

import cv2

# ======================================================================
# Lane registry — maps lane name -> class
# ======================================================================
LANE_REGISTRY = {
    # New lanes
    "rt_detr": RTDETRLane,
    "yolov8_fallback": YOLOv8FallbackLane,
    "fire_smoke_yolo": FireSmokeYOLOLane,
    "violence_candidate": ViolenceCandidateLane,
    "fall_candidate": FallCandidateLane,
    "yolo_pose": YOLOv8PoseLane,
    "weapon_yolo": WeaponYOLOLane,
    "accident": AccidentLane,
    "anyanomaly": AnyAnomalyLane,
    "anomalyclip": AnomalyCLIPLane,
    "temporal_verifier": TemporalVerifierLane,
    "entity_identity": EntityIdentityLane,
    # Legacy lanes
    "person_zone": PersonZoneLane,
    "fire_smoke": FireSmokeLane,
    "violence": ViolenceLane,
    "vad_generic": VADGenericLane,
}

# Lanes that accept zones in constructor
_ZONE_LANES = {"person_zone"}

# Lane name → Hz config category (maps to cameras.yaml sample_hz keys)
_LANE_HZ_CATEGORY = {
    "rt_detr": "detector",
    "yolov8_fallback": "detector",
    "fire_smoke_yolo": "detector",
    "fire_smoke": "detector",
    "person_zone": "detector",
    "violence": "detector",
    "violence_candidate": "anomaly",
    "fall_candidate": "anomaly",
    "yolo_pose": "detector",
    "weapon_yolo": "detector",
    "accident": "anomaly",
    "anyanomaly": "anomaly",
    "anomalyclip": "anomaly",
    "vad_generic": "anomaly",
    "temporal_verifier": "temporal",
    "entity_identity": "identity",
}


# ======================================================================
class CameraProcessor:
    """Processes a single camera: ingest -> lanes -> aggregator."""

    def __init__(
        self,
        camera_cfg: Dict[str, Any],
        models_cfg: Dict[str, Any],
        zones: List[Dict[str, Any]],
        aggregator: AlertAggregator,
        evidence_exporter: EvidenceExporter,
        gpu_scheduler: Optional[GPUScheduler] = None,
        auto_throttle: Optional[AutoThrottle] = None,
        anyanomaly_client: Optional[AnyAnomalyClient] = None,
        face_embedder=None,
        pet_embedder=None,
        identity_matcher=None,
        identity_stabilizer=None,
        frame_store: Optional[LatestFrameStore] = None,
    ):
        self.camera_id = camera_cfg["camera_id"]
        self.camera_cfg = camera_cfg
        self.models_cfg = models_cfg
        self.zones = zones
        self.aggregator = aggregator
        self.evidence_exporter = evidence_exporter
        self.gpu_scheduler = gpu_scheduler
        self.auto_throttle = auto_throttle
        self.anyanomaly_client = anyanomaly_client
        self._face_embedder = face_embedder
        self._pet_embedder = pet_embedder
        self._identity_matcher = identity_matcher
        self._identity_stabilizer = identity_stabilizer
        self._frame_store = frame_store

        self.logger = setup_logger(f"CameraProcessor-{self.camera_id}")

        # Ingest
        self.reader = self._create_reader()

        # Evidence ring buffer (20 s to cover 5+5+margin)
        self.ringbuffer = FrameRingBuffer(self.camera_id, max_seconds=20.0, fps=10.0)

        # Lanes
        self.lanes = self._create_lanes()

        # Per-lane scheduling
        self._sample_hz_map = self._build_sample_hz_map()

        # Evidence export pool (non-blocking for main processing thread)
        self._evidence_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="evidence")

        # Lane inference pool (parallel lane dispatch)
        self._lane_pool = ThreadPoolExecutor(
            max_workers=min(len(self.lanes) + 1, 6),
            thread_name_prefix="lane",
        )

        # Shared detection cache (avoids duplicate YOLO person forward passes)
        self._det_cache = FrameDetectionCache()
        self._wire_detection_cache()

        # Shared pose cache (yolov8_pose → fall_candidate)
        self._pose_cache = PoseCache()
        self._wire_pose_cache()

        # yolov8_fallback conditional mode — skip when RT-DETR found detections
        self._fallback_conditional = True
        self._last_rtdetr_had_dets = False

        # Control
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Stats
        self.stats = {
            "frames_processed": 0,
            "last_frame_ts": None,
            "last_alert_ts": None,
            "fps": 0.0,
        }

    # ------------------------------------------------------------------
    def _create_reader(self):
        backend = self.camera_cfg.get("ingest_backend", "opencv")
        source_type = self.camera_cfg.get("source_type", "rtsp")

        if backend == "live_camera" or source_type == "live_camera":
            idx = self.camera_cfg.get("camera_index", 0)
            return LiveCameraReader(self.camera_id, camera_index=idx)
        elif backend == "ffmpeg":
            return FFmpegReader(self.camera_id, self.camera_cfg["rtsp_url"])
        elif backend == "deepstream":
            return DeepStreamStub(self.camera_id, self.camera_cfg["rtsp_url"])
        else:
            return OpenCVReader(self.camera_id, self.camera_cfg.get("rtsp_url", "0"))

    # ------------------------------------------------------------------
    def _create_lanes(self) -> Dict[str, Any]:
        lanes = {}
        enabled = self.camera_cfg.get("enabled_lanes", [])
        device = self.models_cfg.get("device", "auto")

        for name in enabled:
            cls = LANE_REGISTRY.get(name)
            if cls is None:
                self.logger.warning(f"Unknown lane: {name}")
                continue
            try:
                if name in _ZONE_LANES:
                    lane = cls(name, self.camera_id, self.models_cfg, device, self.zones)
                else:
                    lane = cls(name, self.camera_id, self.models_cfg, device)

                # Wire dependencies BEFORE init so init() sees them
                if name == "temporal_verifier":
                    self.aggregator.set_temporal_verifier(lane)
                if name == "anyanomaly" and self.anyanomaly_client:
                    lane.set_client(self.anyanomaly_client)
                if name == "entity_identity":
                    if self._face_embedder:
                        lane.set_face_embedder(self._face_embedder)
                    if self._pet_embedder:
                        lane.set_pet_embedder(self._pet_embedder)
                    if self._identity_matcher:
                        lane.set_matcher(self._identity_matcher)
                    if self._identity_stabilizer:
                        lane.set_stabilizer(self._identity_stabilizer)
                    # Share person detector from person_zone if available
                    pz = lanes.get("person_zone")
                    if pz and hasattr(pz, "model") and pz.model is not None:
                        lane.set_person_detector(pz.model)

                lane.init()

                # Skip truly disabled lanes (weights missing, etc.)
                if hasattr(lane, "_active") and not lane._active:
                    self.logger.info(f"Lane {name} disabled (inactive) — not registered")
                    continue

                lanes[name] = lane
                self.logger.info(f"Initialised lane: {name}")

                # Wire person_zone into aggregator for loitering observation polling
                if name == "person_zone":
                    self.aggregator.set_person_zone_lane(self.camera_id, lane)

            except Exception as e:
                self.logger.error(f"Lane init failed ({name}): {e}")

        return lanes

    # ------------------------------------------------------------------
    def _build_sample_hz_map(self) -> Dict[str, float]:
        """Build per-lane Hz from camera config using spec keys: detector/anomaly/temporal."""
        raw = self.camera_cfg.get("sample_hz", 2)
        if isinstance(raw, dict):
            detector_hz = raw.get("detector", 2.0)
            anomaly_hz = raw.get("anomaly", 0.5)
            temporal_hz = raw.get("temporal", 0.2)
            identity_hz = raw.get("identity", 2.0)

            result = {}
            for lane_name in self.lanes:
                category = _LANE_HZ_CATEGORY.get(lane_name, "detector")
                if category == "detector":
                    result[lane_name] = detector_hz
                elif category == "anomaly":
                    result[lane_name] = anomaly_hz
                elif category == "temporal":
                    result[lane_name] = temporal_hz
                elif category == "identity":
                    result[lane_name] = identity_hz
                else:
                    result[lane_name] = detector_hz
            return result

        # Scalar -> uniform
        hz = float(raw) if raw else 2.0
        return {name: hz for name in self.lanes}

    # ------------------------------------------------------------------
    def _wire_detection_cache(self):
        """Inject shared detection cache into lanes that support it."""
        for lane_name, lane in self.lanes.items():
            if hasattr(lane, "set_detection_cache"):
                lane.set_detection_cache(self._det_cache)
                self.logger.debug(f"Wired detection cache → {lane_name}")

    # ------------------------------------------------------------------
    def _wire_pose_cache(self):
        """Inject shared pose cache into pose producer + fall consumer."""
        for lane_name, lane in self.lanes.items():
            if hasattr(lane, "set_pose_cache"):
                lane.set_pose_cache(self._pose_cache)
                self.logger.debug(f"Wired pose cache → {lane_name}")

    # ------------------------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        self.reader.start()
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
        self.logger.info("Started camera processor")

    def stop(self):
        self._running = False
        self.reader.stop()
        if self._thread:
            self._thread.join(timeout=5.0)
        # Shutdown thread pools gracefully
        self._lane_pool.shutdown(wait=False)
        self._evidence_pool.shutdown(wait=False)
        self.logger.info("Stopped camera processor")

    # ------------------------------------------------------------------
    def _process_loop(self):
        # Per-lane last-run timestamps for independent cadence
        lane_last_run: Dict[str, float] = {}
        frame_count = 0
        frame_seq = 0           # monotonic frame id for detection cache
        t_start = time.time()

        # Ring buffer insert gating (target ~10 fps for evidence)
        ring_target_interval = 1.0 / 10.0   # 100 ms
        last_ring_insert = 0.0

        # GPU lanes eligible for scheduler dispatch
        _GPU_LANES = frozenset({
            "rt_detr", "yolov8_fallback", "fire_smoke_yolo",
            "fire_smoke", "weapon_yolo", "entity_identity",
            "yolo_pose",
        })

        while self._running:
            try:
                frame, ts = self.reader.get_latest()
                if frame is None:
                    # Wait for new frame from reader (event-driven, no CPU spin)
                    if hasattr(self.reader, 'wait_for_frame'):
                        self.reader.wait_for_frame(timeout=0.5)
                    else:
                        time.sleep(0.5)
                    continue

                now = time.time()
                frame_seq += 1

                # ── Gate ring-buffer inserts to ~10 fps ───────────────
                if now - last_ring_insert >= ring_target_interval:
                    self.ringbuffer.add_frame(frame, ts)
                    last_ring_insert = now

                # §2.2 — update shared LatestFrameStore for API capture
                if self._frame_store is not None:
                    self._frame_store.update(self.camera_id, frame, ts)

                # ── Determine which lanes are due this cycle ──────────
                due_lanes: Dict[str, Any] = {}
                for lane_name, lane in self.lanes.items():
                    if getattr(lane, "on_demand", False):
                        continue

                    # Conditional fallback: skip yolov8_fallback when RTDETR
                    # found detections (saves an entire GPU forward pass)
                    if lane_name == "yolov8_fallback" and self._fallback_conditional:
                        if self._last_rtdetr_had_dets:
                            continue

                    hz = self._sample_hz_map.get(lane_name, 2.0)
                    if self.auto_throttle and lane_name in ("rt_detr", "yolov8_fallback"):
                        hz = self.auto_throttle.get_effective_hz(self.camera_id)

                    interval = 1.0 / max(hz, 0.01)
                    last = lane_last_run.get(lane_name, 0)
                    if now - last < interval:
                        continue
                    lane_last_run[lane_name] = now
                    due_lanes[lane_name] = lane

                # Skip expensive work when nothing is due
                if not due_lanes:
                    time.sleep(0.001)
                    continue

                # ── Dispatch due lanes in PARALLEL via thread pool ────
                futures: Dict[str, Future] = {}
                future_to_lane: Dict[Future, str] = {}
                for lane_name, lane in due_lanes.items():
                    # Build lane-specific kwargs
                    lane_kwargs: Dict[str, Any] = {}
                    if lane_name in ("yolov8_fallback", "fall_candidate"):
                        lane_kwargs["frame_seq"] = frame_seq

                    if self.gpu_scheduler and lane_name in _GPU_LANES:
                        fut = self.gpu_scheduler.submit(
                            lane_name, self.camera_id,
                            lane.infer, frame, ts, **lane_kwargs,
                        )
                        if fut is not None:
                            futures[lane_name] = fut
                            future_to_lane[fut] = lane_name
                    else:
                        fut = self._lane_pool.submit(
                            lane.infer, frame, ts, **lane_kwargs,
                        )
                        futures[lane_name] = fut
                        future_to_lane[fut] = lane_name

                # ── Collect results incrementally via as_completed ─────
                for fut in as_completed(future_to_lane, timeout=5.0):
                    lane_name = future_to_lane[fut]
                    try:
                        obs = fut.result()
                        if obs is None:
                            continue

                        if obs.debug is None:
                            obs.debug = {}

                        # Track RT-DETR detection state for conditional fallback
                        if lane_name == "rt_detr":
                            num_dets = obs.debug.get("num_detections", 0)
                            self._last_rtdetr_had_dets = num_dets > 0 or obs.trigger

                        # Update auto-throttle for detector lanes
                        dt_ms = obs.debug.get("lane_inference_ms") or obs.debug.get("inference_ms", 0)
                        if self.auto_throttle and lane_name in ("rt_detr", "yolov8_fallback"):
                            max_hz = self._sample_hz_map.get(lane_name, 2.0)
                            self.auto_throttle.update(self.camera_id, dt_ms, max_hz)

                        # AnyAnomaly trigger policy
                        if lane_name == "anomalyclip" and obs.score > 0:
                            aa_lane = self.lanes.get("anyanomaly")
                            if aa_lane and hasattr(aa_lane, "arm"):
                                aa_cfg = self.models_cfg.get("models", {}).get("anyanomaly", {})
                                candidate_thresh = aa_cfg.get("candidate_threshold", 0.40)
                                if obs.score >= candidate_thresh:
                                    aa_lane.arm(reason="anomalyclip_candidate")

                        if lane_name in ("rt_detr", "yolov8_fallback"):
                            suspicious = {"weapon", "knife", "gun", "fight", "violence"}
                            if obs.label and obs.label.lower() in suspicious:
                                aa_lane = self.lanes.get("anyanomaly")
                                if aa_lane and hasattr(aa_lane, "arm"):
                                    aa_lane.arm(reason=f"detector_{obs.label}")

                        alert = self.aggregator.process_observation(
                            obs,
                            evidence_request_callback=self._request_evidence_async,
                            ringbuffer=self.ringbuffer,
                        )
                        if alert:
                            self.stats["last_alert_ts"] = alert.ts_utc
                            self.logger.info(f"ALERT: {alert.type}")

                    except Exception as e:
                        self.logger.error(f"Lane {lane_name} error: {e}")

                # ── Poll loitering observations from person_zone ──────
                try:
                    self.aggregator.process_loitering(
                        self.camera_id,
                        evidence_request_callback=self._request_evidence_async,
                        ringbuffer=self.ringbuffer,
                    )
                except Exception as e:
                    self.logger.error(f"Loitering processing error: {e}")

                frame_count += 1
                self.stats["frames_processed"] = frame_count
                self.stats["last_frame_ts"] = ts
                if frame_count % 30 == 0:
                    elapsed = time.time() - t_start
                    self.stats["fps"] = frame_count / max(elapsed, 1)

                # Yield briefly — only needed when loop ran faster than source
                time.sleep(0.001)

            except Exception as e:
                self.logger.error(f"Processing error: {e}")
                time.sleep(1.0)

    # ------------------------------------------------------------------
    def _request_evidence(self, camera_id: str, alert_type: str, ts_utc: str) -> Dict[str, object]:
        try:
            ev_cfg = self.camera_cfg.get("evidence", {})
            return self.evidence_exporter.export_evidence(
                camera_id, alert_type, ts_utc, self.ringbuffer,
                pre_seconds=ev_cfg.get("pre_s", 5.0),
                post_seconds=ev_cfg.get("post_s", 5.0),
            )
        except Exception as e:
            self.logger.error(f"Evidence export failed: {e}")
            return {"keyframe_path": "", "clip_path": "", "partial_clip": True}

    # ------------------------------------------------------------------
    def _request_evidence_async(self, camera_id: str, alert_type: str, ts_utc: str) -> Dict[str, object]:
        """Semi-synchronous evidence: keyframe now, clip in background.

        Writes the keyframe JPEG immediately from the ring buffer so the
        alert contains a real path.  The video clip is exported in a
        background thread (needs to wait for post-event frames).
        """
        try:
            ev_cfg = self.camera_cfg.get("evidence", {})
            # Build the same file paths the full exporter would use
            cam_dir = self.evidence_exporter.evidence_dir / camera_id
            cam_dir.mkdir(parents=True, exist_ok=True)
            safe_ts = ts_utc.replace(":", "-").replace(".", "-")
            keyframe_path = cam_dir / f"{safe_ts}_{alert_type}.jpg"
            clip_path = cam_dir / f"{safe_ts}_{alert_type}.mp4"

            # Grab the most recent pre-event frame from ring buffer
            pre_frames = self.ringbuffer.get_frames_before(ts_utc, seconds=2.0)
            if pre_frames:
                kf_data = pre_frames[-1][1]  # (ts, jpeg_bytes)
            else:
                latest = self.ringbuffer.get_latest_frame()
                kf_data = latest[1] if latest else None

            # Write keyframe JPEG synchronously (< 5 ms)
            if kf_data:
                with open(keyframe_path, "wb") as f:
                    f.write(kf_data)

            # Dispatch full clip export in background thread
            self._evidence_pool.submit(
                self._export_clip_only, camera_id, alert_type, ts_utc,
                clip_path, ev_cfg,
            )

            return {
                "keyframe_path": str(keyframe_path),
                "clip_path": str(clip_path),
                "partial_clip": False,
            }
        except Exception as e:
            self.logger.error(f"Evidence keyframe export failed: {e}")
            return {"keyframe_path": "", "clip_path": "", "partial_clip": True}

    # ------------------------------------------------------------------
    def _export_clip_only(self, camera_id: str, alert_type: str, ts_utc: str,
                          clip_path, ev_cfg: dict):
        """Background clip export — waits for post-event frames then writes MP4."""
        try:
            pre_seconds = ev_cfg.get("pre_s", 5.0)
            post_seconds = ev_cfg.get("post_s", 5.0)

            pre_frames = self.ringbuffer.get_frames_before(ts_utc, seconds=pre_seconds)

            # Wait for post-event frames
            from src.common.timeutil import timestamp_diff_seconds
            partial_clip = False
            post_frames = []
            waited = 0.0
            poll = 0.5
            timeout = self.evidence_exporter.post_wait_timeout
            while waited < timeout:
                time.sleep(poll)
                waited += poll
                all_frames = self.ringbuffer.get_all_frames()
                post_frames = [
                    (t, d) for t, d in all_frames
                    if timestamp_diff_seconds(ts_utc, t) > 0
                ]
                if post_frames:
                    latest_post = post_frames[-1]
                    if timestamp_diff_seconds(ts_utc, latest_post[0]) >= post_seconds:
                        break
            else:
                partial_clip = True

            post_frames = [
                (t, d) for t, d in post_frames
                if 0 < timestamp_diff_seconds(ts_utc, t) <= post_seconds
            ]

            frames = pre_frames + post_frames
            if frames:
                self.evidence_exporter._export_clip(frames, clip_path)
                self.logger.info(
                    f"Clip exported {camera_id}/{alert_type}: "
                    f"{clip_path.name}, partial={partial_clip}"
                )
        except Exception as e:
            self.logger.error(f"Clip export failed: {e}")

    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "source_type": self.camera_cfg.get("source_type", "rtsp"),
            "connected": self.reader.is_connected(),
            "frames_processed": self.stats["frames_processed"],
            "fps": round(self.stats["fps"], 2),
            "buffer_size": self.ringbuffer.size(),
            "active_lanes": list(self.lanes.keys()),
            "last_frame": self.stats["last_frame_ts"],
            "last_alert": self.stats["last_alert_ts"],
        }


# ======================================================================
class CCTVAIModule:
    """Main application orchestrator."""

    def __init__(self, config_dir: str = "configs"):
        self.config = Config(config_dir)
        self.logger = setup_logger("CCTVAIModule")

        self.camera_configs = self.config.load_cameras()
        self.models_cfg = self.config.load_models()
        self.zones_cfg = self.config.load_zones()

        # ── Startup Doctor (runs BEFORE any model init) ───────────────
        self.doctor_report = Doctor.run_all(self.models_cfg)
        self.logger.info(
            f"Doctor: gpu_usable={self.doctor_report.gpu_usable}, "
            f"missing={len(self.doctor_report.missing)}, "
            f"auto_fetched={len(self.doctor_report.auto_fetched)}"
        )

        # Aggregator (default k/n/cooldown from first camera or fallback)
        k, n = 3, 5
        cooldown_s = 45
        if self.camera_configs:
            k, n = self.camera_configs[0].get("k_of_n", [3, 5])
            cooldown_s = self.camera_configs[0].get("cooldown_s", 45)

        # Fire two-stage threshold from config
        fire_sec = self.models_cfg.get("models", {}).get("fire_smoke", {}).get(
            "fire_secondary_threshold", 0.55
        )

        # Per-alert-type K/N overrides from incident framework
        per_type_kn = {
            "FALL": (4, 8),
            "FIRE_SMOKE": (4, 8),
            "WEAPON_DETECTED": (3, 5),
            "UNKNOWN_SEVERE_ANOMALY": (4, 8),
            "LOITERING": (3, 5),
        }

        self.aggregator = AlertAggregator(
            k=k, n=n, cooldown_s=cooldown_s,
            fire_secondary_threshold=fire_sec,
            per_type_kn=per_type_kn,
        )
        self.evidence_exporter = EvidenceExporter(evidence_dir="evidence")

        # Runtime: GPU scheduler
        runtime_cfg = self.models_cfg.get("runtime", {})
        self.gpu_scheduler = GPUScheduler(runtime_cfg)

        # Runtime: Auto-throttle
        budgets = runtime_cfg.get("budgets", {})
        target_ms = budgets.get("rtdetr", {}).get("max_latency_ms", 120)
        self.auto_throttle = AutoThrottle(target_ms=target_ms)

        # Services: AnyAnomaly client
        aa_cfg = self.models_cfg.get("models", {}).get("anyanomaly", {})
        self.anyanomaly_client = AnyAnomalyClient(aa_cfg) if aa_cfg.get("enabled", True) else None

        # ── Identity subsystem (§0-§8) ────────────────────────────────
        id_cfg = self.models_cfg.get("identity", {})
        self.identity_enabled = id_cfg.get("enabled", False)
        self.entity_store = None
        self.face_embedder = None
        self.pet_embedder = None
        self.identity_matcher = None
        self.identity_policy = None
        self.identity_stabilizer = None

        if self.identity_enabled:
            try:
                self.entity_store = EntityStore()
                face_cfg = id_cfg.get("face", {})
                face_cfg["enabled"] = True
                self.face_embedder = FaceEmbedder(face_cfg)

                pet_cfg = id_cfg.get("pet", {})
                self.pet_embedder = PetEmbedder(pet_cfg)

                matcher_cfg = id_cfg.get("matcher", {})
                face_thresh = face_cfg.get("match_threshold_sim", 0.50)
                pet_thresh = pet_cfg.get("match_threshold_sim", 0.30)
                face_margin = face_cfg.get("top2_margin", 0.08)
                pet_margin = pet_cfg.get("top2_margin", 0.05)
                self.identity_matcher = IdentityMatcher(
                    self.entity_store,
                    cfg=matcher_cfg,
                    face_threshold=face_thresh,
                    pet_threshold=pet_thresh,
                    face_margin=face_margin,
                    pet_margin=pet_margin,
                )

                # Create stabilizer (§4)
                from .identity.stabilizer import IdentityStabilizer
                runtime_cfg = id_cfg.get("runtime", {})
                stabilizer_cfg = {
                    "history_L": runtime_cfg.get("history_L", 7),
                    "accept_M": runtime_cfg.get("accept_M", 3),
                    "lock_s": runtime_cfg.get("lock_s", 8),
                    "unknown_grace_s": runtime_cfg.get("unknown_grace_s", 6),
                    "decay_per_s": runtime_cfg.get("decay_per_s", 0.06),
                    "reacquire_min_sim": runtime_cfg.get("reacquire_min_sim", 0.46),
                    "match_threshold_sim": face_thresh,
                    "top2_margin": face_margin,
                }
                self.identity_stabilizer = IdentityStabilizer(stabilizer_cfg, entity_store=self.entity_store)

                policy_cfg = self.config.load_policy()
                self.identity_policy = IdentityPolicy(policy_cfg)

                # Wire into aggregator
                self.aggregator.set_identity(self.identity_policy, self.entity_store)
                self.logger.info(
                    f"Identity subsystem ENABLED (face={self.face_embedder.available}, "
                    f"pet={self.pet_embedder.available}, stabilizer=yes)"
                )
            except Exception as e:
                self.logger.error(f"Identity subsystem init failed: {e}")
                self.identity_enabled = False
        else:
            self.logger.info("Identity subsystem DISABLED (identity.enabled=false)")

        # §4.3 — wire zone config into aggregator for zone-aware anomaly
        self.aggregator.set_zone_cameras(self.zones_cfg)

        self.processors: List[CameraProcessor] = []
        self.api_server: Optional[AlertServer] = None
        self.api_thread: Optional[threading.Thread] = None

        # §2.2 — shared latest-frame store across all cameras
        self.frame_store = LatestFrameStore()

    # ------------------------------------------------------------------
    def start(self):
        self.logger.info("=" * 60)
        self.logger.info("Starting CCTV AI Module v2")
        self.logger.info("=" * 60)

        # Start GPU scheduler
        self.gpu_scheduler.start()
        self.logger.info("GPU scheduler started")

        # Start AnyAnomaly subprocess
        if self.anyanomaly_client:
            self.anyanomaly_client.start()
            self.logger.info(f"AnyAnomaly client: available={self.anyanomaly_client.is_available}")

        # Camera processors
        for cam_cfg in self.camera_configs:
            try:
                cid = cam_cfg["camera_id"]
                zones = self.zones_cfg.get(cid, [])
                proc = CameraProcessor(
                    cam_cfg, self.models_cfg, zones,
                    self.aggregator, self.evidence_exporter,
                    gpu_scheduler=self.gpu_scheduler,
                    auto_throttle=self.auto_throttle,
                    anyanomaly_client=self.anyanomaly_client,
                    face_embedder=self.face_embedder,
                    pet_embedder=self.pet_embedder,
                    identity_matcher=self.identity_matcher,
                    identity_stabilizer=self.identity_stabilizer,
                    frame_store=self.frame_store,
                )
                proc.start()
                self.processors.append(proc)
                self.logger.info(f"Started processor for {cid}")
            except Exception as e:
                self.logger.error(f"Failed to start camera {cam_cfg['camera_id']}: {e}")

        # Alert -> WebSocket
        if self.api_server:
            def _ws_cb(alert: Alert):
                if self.api_server:
                    asyncio.run(self.api_server.broadcast_alert(alert.to_dict()))
            self.aggregator.add_alert_callback(_ws_cb)

        self.start_api_server()

        time.sleep(2)
        self.print_status()

        self.logger.info("=" * 60)
        self.logger.info("System started successfully")
        self.logger.info("Web UI: http://127.0.0.1:8080")
        self.logger.info("=" * 60)

    # ------------------------------------------------------------------
    def start_api_server(self):
        self.api_server = AlertServer(host="0.0.0.0", port=8080)
        self.api_server.set_aggregator(self.aggregator)
        self.api_server.set_camera_processors(self.processors)
        self.api_server.set_gpu_scheduler(self.gpu_scheduler)
        self.api_server.set_auto_throttle(self.auto_throttle)
        self.api_server.set_process_video_fn(self.process_uploaded_video)
        self.api_server.set_doctor_report(self.doctor_report)
        self.api_server.set_frame_store(self.frame_store)
        self.api_server.set_identity_components(
            self.entity_store, self.face_embedder,
            self.pet_embedder, self.identity_matcher,
            stabilizer=self.identity_stabilizer,
            enrollment_cfg=self.models_cfg.get("identity", {}).get("enrollment", {}),
        )
        self.api_thread = threading.Thread(target=self.api_server.run, daemon=True)
        self.api_thread.start()

    # ------------------------------------------------------------------
    def process_uploaded_video(self, video_path: str, job_id: str,
                               fps: float, force_anyanomaly: bool = False,
                               progress_callback=None) -> List[Dict]:
        """
        Offline mode: process an uploaded video through all lanes.
        Uses frame index math for evidence extraction.
        Returns list of alert dicts.
        """
        self.logger.info(f"Processing uploaded video: {video_path} (job={job_id})")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self.logger.error(f"Cannot open video: {video_path}")
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = cap.get(cv2.CAP_PROP_FPS) or fps

        # Create a temporary aggregator for this job
        k, n = 3, 5
        cooldown_s = 45
        if self.camera_configs:
            k, n = self.camera_configs[0].get("k_of_n", [3, 5])
            cooldown_s = self.camera_configs[0].get("cooldown_s", 45)
        job_aggregator = AlertAggregator(k=k, n=n, cooldown_s=cooldown_s)

        # Create lanes for processing
        device = self.models_cfg.get("device", "auto")
        camera_id = f"upload_{job_id}"
        lanes = {}
        enabled = ["rt_detr", "yolov8_fallback", "fire_smoke_yolo", "anomalyclip"]
        if force_anyanomaly:
            enabled.append("anyanomaly")

        for name in enabled:
            cls = LANE_REGISTRY.get(name)
            if cls is None:
                continue
            try:
                lane = cls(name, camera_id, self.models_cfg, device)
                lane.init()
                lanes[name] = lane
                if name == "anyanomaly" and self.anyanomaly_client:
                    lane.set_client(self.anyanomaly_client)
            except Exception as e:
                self.logger.warning(f"Offline lane init failed ({name}): {e}")

        # Ring buffer for evidence
        ringbuffer = FrameRingBuffer(camera_id, max_seconds=20.0, fps=video_fps)

        alerts = []
        frame_idx = 0
        sample_interval = max(1, int(video_fps / 2))  # ~2 Hz

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx += 1
            ts_utc = f"frame_{frame_idx}"

            ringbuffer.add_frame(frame, ts_utc)

            # Only process every N frames
            if frame_idx % sample_interval != 0:
                if progress_callback and total_frames > 0:
                    progress_callback(round(frame_idx / total_frames * 100, 1))
                continue

            for lane_name, lane in lanes.items():
                if getattr(lane, "on_demand", False):
                    continue
                try:
                    obs = lane.infer(frame, ts_utc)
                    alert = job_aggregator.process_observation(obs, ringbuffer=ringbuffer)
                    if alert:
                        alerts.append(alert.to_dict())
                except Exception as e:
                    self.logger.error(f"Offline lane {lane_name} error: {e}")

            if progress_callback and total_frames > 0:
                progress_callback(round(frame_idx / total_frames * 100, 1))

        cap.release()
        self.logger.info(f"Offline processing complete: {len(alerts)} alerts from {frame_idx} frames")
        return alerts

    # ------------------------------------------------------------------
    def print_status(self):
        print("\n" + "=" * 80)
        print("SYSTEM STATUS")
        print("=" * 80)
        for proc in self.processors:
            s = proc.get_stats()
            status = "CONNECTED" if s["connected"] else "DISCONNECTED"
            print(f"\nCamera: {s['camera_id']} ({s['source_type']})")
            print(f"   Status: {status}")
            print(f"   Frames: {s['frames_processed']} | FPS: {s['fps']}")
            print(f"   Buffer: {s['buffer_size']} frames")
            print(f"   Lanes:  {', '.join(s['active_lanes'])}")
            print(f"   Last Alert: {s['last_alert'] or 'None'}")

        # GPU scheduler stats
        if self.gpu_scheduler:
            metrics = self.gpu_scheduler.get_metrics()
            print(f"\nGPU Queue: {metrics.get('gpu_queue_length', 0)}")
            print(f"GPU Inflight: {metrics.get('gpu_inflight', 0)}")

        # Auto-throttle stats
        if self.auto_throttle:
            throttle_metrics = self.auto_throttle.get_metrics()
            for cam_id, tm in throttle_metrics.items():
                print(f"   [{cam_id}] EMA: {tm['ema_ms']:.0f}ms, Hz: {tm['effective_hz']:.2f}")

        # AnyAnomaly status
        if self.anyanomaly_client:
            print(f"\nAnyAnomaly: available={self.anyanomaly_client.is_available}, "
                  f"pending={self.anyanomaly_client.pending_count}")

        print("\n" + "=" * 80 + "\n")

    # ------------------------------------------------------------------
    def run(self):
        self.start()
        try:
            while True:
                time.sleep(30)
                self.print_status()
        except KeyboardInterrupt:
            self.logger.info("Shutdown requested")
            self.stop()

    def stop(self):
        self.logger.info("Stopping CCTV AI Module...")
        for proc in self.processors:
            proc.stop()
        if self.gpu_scheduler:
            self.gpu_scheduler.stop()
        if self.anyanomaly_client:
            self.anyanomaly_client.stop()
        self.logger.info("Shutdown complete")


# ======================================================================
def main():
    parser = argparse.ArgumentParser(description="CCTV AI Module v2")
    parser.add_argument("--config-dir", type=str, default="configs",
                        help="Path to configuration directory")
    args = parser.parse_args()

    app = CCTVAIModule(config_dir=args.config_dir)
    app.run()


if __name__ == "__main__":
    main()
