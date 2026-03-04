"""
Fall candidate lane — pose-based multi-factor scoring.

Uses YOLOv8-Pose keypoints (via shared PoseCache) to compute per-person
fall features:
  • torso angle (shoulder_mid ↔ hip_mid)
  • hip height drop (normalized by bbox height)
  • lying persistence (torso horizontal for ≥ persist_s)
  • post-fall stillness (velocity below threshold for ≥ still_s)
  • bbox aspect ratio (supportive signal only)

Trigger rule (eliminates huge FPs):
  Candidate = (hip_drop AND torso_horizontal) OR
              (torso_horizontal AND aspect_ratio_wide)
  trigger=True only if lying_persist AND (post_fall_still OR hip_drop_strong)

Emits reason_codes in debug: hip_drop, torso_horizontal, lying_persist, post_fall_still
"""
import math
import time
import numpy as np
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device
from ..logic.detection_cache import FrameDetectionCache

# COCO keypoint indices
_L_SHOULDER = 5
_R_SHOULDER = 6
_L_HIP = 11
_R_HIP = 12


def _mid(kp_a: List[float], kp_b: List[float]) -> Tuple[float, float]:
    """Midpoint of two keypoints (x, y)."""
    return (kp_a[0] + kp_b[0]) / 2.0, (kp_a[1] + kp_b[1]) / 2.0


def _angle_to_horizontal(dx: float, dy: float) -> float:
    """Angle of (dx, dy) vector relative to horizontal (0° = flat, 90° = vertical)."""
    rad = math.atan2(abs(dy), abs(dx) + 1e-9)
    return math.degrees(rad)


class _TrackHistory:
    """Per-person rolling history for fall feature tracking."""

    __slots__ = (
        "torso_angles", "hip_y_norms", "positions", "timestamps",
        "lying_start", "still_start", "max_hip_y_norm",
    )

    def __init__(self, max_len: int = 30):
        self.torso_angles: deque = deque(maxlen=max_len)
        self.hip_y_norms: deque = deque(maxlen=max_len)
        self.positions: deque = deque(maxlen=max_len)      # (cx, cy)
        self.timestamps: deque = deque(maxlen=max_len)      # monotonic
        self.lying_start: Optional[float] = None
        self.still_start: Optional[float] = None
        self.max_hip_y_norm: float = 0.0                    # running max


class FallCandidateLane(BaseLane):
    """
    Pose-based fall detection with multi-factor scoring.
    Consumes keypoints from PoseCache (written by yolov8_pose lane).
    Falls back to own lightweight person detector if pose unavailable.
    """

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.logger = setup_logger(f"FallCandidate-{camera_id}")
        self.model = None          # lightweight YOLO for fallback only

        # Config
        fall_cfg = models_cfg.get("models", {}).get("fall", {})
        self.angle_thresh_deg = fall_cfg.get("angle_thresh_deg", 35.0)
        self.hip_drop_thresh = fall_cfg.get("hip_drop_thresh", 0.20)
        self.persist_s = fall_cfg.get("persist_s", 1.0)
        self.still_s = fall_cfg.get("still_s", 1.2)
        self.still_vel_thresh = fall_cfg.get("still_vel_thresh", 8.0)
        self.min_pose_conf = fall_cfg.get("min_pose_conf", 0.35)
        self.conf_threshold = 0.25

        # Shared caches
        self._pose_cache = None            # set by app.py
        self._det_cache: Optional[FrameDetectionCache] = None
        self._frame_seq: int = 0
        self._ul_device = "cpu"

        # Per-track history (track_id → _TrackHistory)
        self._tracks: Dict[int, _TrackHistory] = defaultdict(lambda: _TrackHistory())
        self._track_last_seen: Dict[int, float] = {}

        # Expose latest fall state for debug endpoint
        self.last_fall_state: Dict[int, Dict[str, Any]] = {}

    def set_pose_cache(self, cache):
        self._pose_cache = cache

    def set_detection_cache(self, cache: FrameDetectionCache) -> None:
        self._det_cache = cache

    # ------------------------------------------------------------------
    def init(self):
        fall_cfg = self.models_cfg.get("models", {}).get("fall", {})
        if not fall_cfg.get("enabled", True):
            self._initialized = True
            self._active = False
            self.logger.info("Fall candidate lane disabled in config")
            return

        # Load lightweight person detector as fallback (when pose cache empty)
        cfg = self.models_cfg.get("models", {}).get("person_detector", {})
        weights = cfg.get("weights", "../yolov8n.pt")
        self.conf_threshold = cfg.get("conf", 0.25)

        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        if Path(weights).exists():
            from ultralytics import YOLO
            self.model = YOLO(weights)
            dev = select_device(self.models_cfg)
            self._ul_device = 0 if dev.torch_gpu else "cpu"
            self.model.to(dev.torch_device)
            self.logger.info(f"Fall candidate fallback detector on {dev.torch_device}")
        else:
            self.logger.warning(f"Person detector weights not found ({weights}), "
                                "fall lane depends entirely on pose cache")

        self._initialized = True
        self._active = True
        self.logger.info("Fall candidate lane ready (pose-based)")

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str,
              frame_seq: int = 0) -> Observation:
        if not self._initialized:
            self.init()
        if hasattr(self, '_active') and not self._active:
            return Observation(
                ts_utc=ts_utc, camera_id=self.camera_id, lane=self.lane_name,
                score=0.0, trigger=False, label=None,
                debug={"disabled": True},
            )

        t0 = time.perf_counter()
        now_mono = time.monotonic()

        # ── 1. Get pose data from shared cache ───────────────────────
        persons: Optional[List[Dict[str, Any]]] = None
        if self._pose_cache is not None:
            persons = self._pose_cache.get(self.camera_id, max_age_s=1.0)

        # Fallback: use bbox-only from detection cache or own YOLO
        if persons is None or len(persons) == 0:
            persons = self._fallback_persons(frame_bgr, frame_seq)

        # ── 2. Expire old tracks ─────────────────────────────────────
        expired = [tid for tid, ts in self._track_last_seen.items()
                   if now_mono - ts > 5.0]
        for tid in expired:
            self._tracks.pop(tid, None)
            self._track_last_seen.pop(tid, None)

        # ── 3. Compute fall features per person ──────────────────────
        best_score = 0.0
        best_bbox = None
        best_debug: Dict[str, Any] = {}
        all_states: Dict[int, Dict[str, Any]] = {}

        for idx, person in enumerate(persons):
            kp = person.get("keypoints")     # list of [x, y, conf] × 17
            bbox = person.get("bbox")
            pose_conf = person.get("pose_conf", 0.0)

            if bbox is None:
                continue

            x1, y1, x2, y2 = bbox
            bw = max(x2 - x1, 1)
            bh = max(y2 - y1, 1)
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0

            # Match to existing track by proximity
            track_id = self._match_track(bbox, now_mono)
            hist = self._tracks[track_id]
            self._track_last_seen[track_id] = now_mono

            reason_codes: List[str] = []
            torso_angle: Optional[float] = None
            hip_y_norm: Optional[float] = None
            hip_drop = 0.0
            lying_persist = False
            post_fall_still = False
            velocity = 0.0

            has_pose = (kp is not None and len(kp) >= 13 and pose_conf >= self.min_pose_conf)

            if has_pose:
                ls = kp[_L_SHOULDER]
                rs = kp[_R_SHOULDER]
                lh = kp[_L_HIP]
                rh = kp[_R_HIP]

                shoulder_ok = (ls[2] >= self.min_pose_conf and rs[2] >= self.min_pose_conf)
                hip_ok = (lh[2] >= self.min_pose_conf and rh[2] >= self.min_pose_conf)

                if shoulder_ok and hip_ok:
                    smx, smy = _mid(ls, rs)
                    hmx, hmy = _mid(lh, rh)

                    # Torso angle (0° = horizontal/fallen, 90° = upright)
                    dx_torso = hmx - smx
                    dy_torso = hmy - smy
                    torso_angle = _angle_to_horizontal(dx_torso, dy_torso)

                    # Hip height normalized (0 = top of bbox, 1 = bottom)
                    hip_y_norm = (hmy - y1) / bh

                    # Track maximum hip_y_norm seen (used for drop calc)
                    if hist.hip_y_norms:
                        hist.max_hip_y_norm = max(hist.max_hip_y_norm,
                                                  max(hist.hip_y_norms))
                    else:
                        hist.max_hip_y_norm = max(hist.max_hip_y_norm, hip_y_norm)
                    hip_drop = hip_y_norm - hist.max_hip_y_norm

                    # Feature: torso_horizontal
                    torso_horizontal = torso_angle < self.angle_thresh_deg
                    if torso_horizontal:
                        reason_codes.append("torso_horizontal")

                    # Feature: hip_drop
                    hip_drop_triggered = hip_drop > self.hip_drop_thresh
                    if hip_drop_triggered:
                        reason_codes.append("hip_drop")

                    # Feature: lying_persist
                    if torso_horizontal:
                        if hist.lying_start is None:
                            hist.lying_start = now_mono
                        lying_duration = now_mono - hist.lying_start
                        if lying_duration >= self.persist_s:
                            lying_persist = True
                            reason_codes.append("lying_persist")
                    else:
                        hist.lying_start = None

                    # Motion / velocity
                    if hist.positions:
                        prev_cx, prev_cy = hist.positions[-1]
                        prev_t = hist.timestamps[-1]
                        dt_track = max(now_mono - prev_t, 0.01)
                        velocity = math.sqrt((cx - prev_cx) ** 2 +
                                             (cy - prev_cy) ** 2) / dt_track
                    else:
                        velocity = 0.0

                    # Feature: post_fall_still
                    if velocity < self.still_vel_thresh:
                        if hist.still_start is None:
                            hist.still_start = now_mono
                        still_duration = now_mono - hist.still_start
                        if still_duration >= self.still_s:
                            post_fall_still = True
                            reason_codes.append("post_fall_still")
                    else:
                        hist.still_start = None

                    # Record history
                    hist.torso_angles.append(torso_angle)
                    hist.hip_y_norms.append(hip_y_norm)
                    hist.positions.append((cx, cy))
                    hist.timestamps.append(now_mono)

            else:
                # No pose — record position only (for fallback heuristic)
                hist.positions.append((cx, cy))
                hist.timestamps.append(now_mono)

            # Aspect ratio (supportive)
            aspect_ratio = bh / bw
            aspect_wide = aspect_ratio < 0.85

            # ──────────────────────────────────────────────────────────
            # Candidate trigger rule
            # ──────────────────────────────────────────────────────────
            is_candidate = False
            hip_drop_strong = hip_drop > self.hip_drop_thresh * 1.5

            if has_pose and torso_angle is not None:
                torso_horizontal = torso_angle < self.angle_thresh_deg
                hip_drop_triggered = hip_drop > self.hip_drop_thresh
                is_candidate = ((hip_drop_triggered and torso_horizontal) or
                                (torso_horizontal and aspect_wide))
            else:
                # Without pose: very weak signal (bbox only — almost never triggers)
                if aspect_wide and self._bbox_fall_heuristic(hist, velocity):
                    is_candidate = True

            trigger = False
            score = 0.0

            if is_candidate:
                # Score ramp: 0.3 base, +0.2 hip_drop, +0.3 lying_persist, +0.2 stillness
                score = 0.30
                if hip_drop > self.hip_drop_thresh:
                    score += min(hip_drop / (self.hip_drop_thresh * 2), 0.20)
                if lying_persist:
                    score += 0.30
                if post_fall_still:
                    score += 0.20
                score = min(score, 1.0)

                # Hard trigger gate
                trigger = lying_persist and (post_fall_still or hip_drop_strong)

            if pose_conf < self.min_pose_conf and trigger:
                trigger = False
                reason_codes.append("low_pose_conf_suppressed")

            state = {
                "track_id": track_id,
                "torso_angle": round(torso_angle, 1) if torso_angle is not None else None,
                "hip_y_norm": round(hip_y_norm, 3) if hip_y_norm is not None else None,
                "hip_drop": round(hip_drop, 3),
                "velocity": round(velocity, 1),
                "lying_persist": lying_persist,
                "lying_duration_s": round(now_mono - hist.lying_start, 2) if hist.lying_start else 0.0,
                "post_fall_still": post_fall_still,
                "still_duration_s": round(now_mono - hist.still_start, 2) if hist.still_start else 0.0,
                "pose_conf": round(pose_conf, 3),
                "aspect_ratio": round(aspect_ratio, 2),
                "reason_codes": reason_codes,
                "score": round(score, 3),
                "trigger": trigger,
            }
            all_states[track_id] = state

            if score > best_score:
                best_score = score
                best_bbox = bbox
                best_debug = state

        # Expose full state for debug endpoint
        self.last_fall_state = all_states

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=round(best_score, 3),
            trigger=best_debug.get("trigger", False),
            bbox=best_bbox,
            label="fall_candidate" if best_debug.get("trigger") else None,
            debug={
                "inference_ms": round((time.perf_counter() - t0) * 1000, 1),
                "num_persons": len(persons),
                "reason_codes": best_debug.get("reason_codes", []),
                "torso_angle": best_debug.get("torso_angle"),
                "hip_drop": best_debug.get("hip_drop", 0.0),
                "pose_conf": best_debug.get("pose_conf", 0.0),
                "velocity": best_debug.get("velocity", 0.0),
                "lying_persist": best_debug.get("lying_persist", False),
                "lying_duration_s": best_debug.get("lying_duration_s", 0.0),
                "post_fall_still": best_debug.get("post_fall_still", False),
                "still_duration_s": best_debug.get("still_duration_s", 0.0),
            },
        )

    # ------------------------------------------------------------------
    def _fallback_persons(self, frame_bgr: np.ndarray,
                          frame_seq: int) -> List[Dict[str, Any]]:
        """Get person bboxes from detection cache or fallback YOLO (no pose)."""
        persons: List[Dict[str, Any]] = []

        # Try shared cache first
        if self._det_cache is not None and frame_seq > 0:
            cached = self._det_cache.get("person_yolo", frame_seq)
            if cached is not None:
                for bbox_item, conf in cached:
                    persons.append({
                        "bbox": bbox_item,
                        "keypoints": None,
                        "pose_conf": 0.0,
                        "det_conf": conf,
                    })
                return persons

        # Fallback: own YOLO (no pose keypoints)
        if self.model is not None:
            results = self.model(
                frame_bgr, verbose=False, conf=self.conf_threshold,
                classes=[0], device=self._ul_device,
                half=isinstance(self._ul_device, int),
            )
            if results and results[0].boxes is not None:
                boxes = results[0].boxes
                xyxy = boxes.xyxy.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                for i in range(len(boxes)):
                    bbox_item = [int(c) for c in xyxy[i].tolist()]
                    persons.append({
                        "bbox": bbox_item,
                        "keypoints": None,
                        "pose_conf": 0.0,
                        "det_conf": float(confs[i]),
                    })

        return persons

    # ------------------------------------------------------------------
    def _match_track(self, bbox: List[int], now_mono: float) -> int:
        """Match bbox to existing track by center distance, or assign new ID."""
        best_iou = 0.0
        best_tid = -1
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        bw = max(bbox[2] - bbox[0], 1)

        for tid, hist in self._tracks.items():
            if not hist.positions:
                continue
            age = now_mono - self._track_last_seen.get(tid, 0)
            if age > 3.0:
                continue
            prev_cx, prev_cy = hist.positions[-1]
            dist = math.sqrt((cx - prev_cx) ** 2 + (cy - prev_cy) ** 2)
            norm_dist = dist / bw
            if norm_dist < 1.5:
                iou_approx = 1.0 / (1.0 + norm_dist)
                if iou_approx > best_iou:
                    best_iou = iou_approx
                    best_tid = tid

        if best_tid >= 0:
            return best_tid
        new_id = max(self._tracks.keys(), default=-1) + 1
        return new_id

    # ------------------------------------------------------------------
    @staticmethod
    def _bbox_fall_heuristic(hist: _TrackHistory, velocity: float) -> bool:
        """Very weak bbox-only fallback: wide aspect + sudden position drop."""
        if len(hist.positions) < 3:
            return False
        y_values = [p[1] for p in list(hist.positions)[-5:]]
        if len(y_values) >= 3:
            y_drop = y_values[-1] - min(y_values[:-1])
            if y_drop > 50 and velocity < 5.0:
                return True
        return False
