"""
Person-in-zone detection lane with Intrusion + Loitering support.

Intrusion: person enters a restricted zone (boundary crossing).
  - enter_grace_s: grace period to avoid false triggers on zone edges.

Loitering: dwell time > threshold within *any* zone or overall on camera.
  - loitering.threshold_s: seconds of continuous presence in a zone.
  - loitering.escalate_unknown_only: only escalate severity for unknown persons.

Both feed into the aggregator as separate alert types:
  person_zone → INTRUSION_PERSON_IN_ZONE
  person_zone → LOITERING  (label="loitering")

References:
  - MDPI 2224-2708/12/1/9 (dwell-time based loitering detection)
"""
import time
import numpy as np
from typing import Dict, Any, List, Optional, Set
from pathlib import Path
from collections import defaultdict

from .base import BaseLane
from ..common.types import Observation
from ..logic.tracker_iou import IOUTracker
from ..logic.zones import check_bbox_in_zones
from ..common.log import setup_logger
from ..runtime.device import select_device


class _TrackZoneState:
    """Per-track state for zone presence and loitering detection."""
    __slots__ = (
        "first_seen_in_zone", "last_seen_in_zone", "dwell_s",
        "crossed_boundary", "grace_start", "alert_fired",
        "loitering_alerted",
    )

    def __init__(self):
        self.first_seen_in_zone: float = 0.0
        self.last_seen_in_zone: float = 0.0
        self.dwell_s: float = 0.0
        self.crossed_boundary: bool = False
        self.grace_start: float = 0.0
        self.alert_fired: bool = False
        self.loitering_alerted: bool = False


class PersonZoneLane(BaseLane):
    """Detects persons in restricted zones with intrusion + loitering."""

    def __init__(self, lane_name: str, camera_id: str, models_cfg: Dict[str, Any],
                 device: str, zones: List[Dict[str, Any]]):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.zones = zones
        self.tracker = IOUTracker(iou_threshold=0.3, max_age=30)
        self.model = None
        self.logger = setup_logger(f"PersonZoneLane-{camera_id}")

        # ── Config defaults (overridden from models.yaml) ─────────────
        self.enter_grace_s: float = 1.0       # intrusion grace period
        self.loitering_threshold_s: float = 30.0
        self.loitering_escalate_unknown_only: bool = True
        self.loitering_enabled: bool = True

        # ── Per-track state: (track_id, zone_name) → _TrackZoneState ─
        self._track_zone_states: Dict[tuple, _TrackZoneState] = defaultdict(_TrackZoneState)
        # track_id → set of zones currently occupied
        self._track_current_zones: Dict[int, Set[str]] = defaultdict(set)
        # Tracks that are not in any zone (for boundary crossing)
        self._tracks_outside: Set[int] = set()

        # Loitering observation queue (emitted as separate obs)
        self._pending_loitering: List[Dict[str, Any]] = []

        # Diagnostics
        self.last_debug: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    def init(self):
        """Initialize YOLO model."""
        try:
            model_cfg = self.models_cfg["models"]["person_detector"]
            weights_path = model_cfg["weights"]

            if not Path(weights_path).is_absolute():
                base_path = Path(__file__).parent.parent.parent
                weights_path = (base_path / weights_path).resolve()

            if not Path(weights_path).exists():
                self.logger.error(f"Model weights not found: {weights_path}")
                raise FileNotFoundError(f"Model weights not found: {weights_path}")

            from ultralytics import YOLO
            self.logger.info(f"Loading YOLO model from {weights_path}")
            self.model = YOLO(str(weights_path))

            dev = select_device(self.models_cfg)
            actual_device = dev.torch_device
            self._ul_device = 0 if dev.torch_gpu else "cpu"
            self.model.to(actual_device)
            self.conf_threshold = model_cfg.get("conf", 0.25)

            # Load intrusion + loitering config
            intrusion_cfg = self.models_cfg.get("models", {}).get("intrusion", {})
            self.enter_grace_s = intrusion_cfg.get("enter_grace_s", 1.0)

            loiter_cfg = self.models_cfg.get("models", {}).get("loitering", {})
            self.loitering_threshold_s = loiter_cfg.get("threshold_s", 30.0)
            self.loitering_escalate_unknown_only = loiter_cfg.get("escalate_unknown_only", True)
            self.loitering_enabled = loiter_cfg.get("enabled", True)

            self._initialized = True
            self.logger.info(
                f"Person detector initialized on {actual_device} "
                f"(grace={self.enter_grace_s}s, loiter_thresh={self.loitering_threshold_s}s)"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize person detector: {e}")
            raise

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        """Run person detection, zone checking, intrusion + loitering logic."""
        if not self._initialized:
            self.init()

        self._pending_loitering.clear()

        try:
            results = self.model(
                frame_bgr, verbose=False, conf=self.conf_threshold,
                classes=[0], device=self._ul_device,
            )

            detections = []
            if len(results) > 0 and results[0].boxes is not None:
                boxes = results[0].boxes
                all_xyxy = boxes.xyxy.cpu().numpy()
                all_conf = boxes.conf.cpu().numpy()
                for i in range(len(boxes)):
                    box = all_xyxy[i].tolist()
                    conf = float(all_conf[i])
                    detections.append((box, conf))

            tracked = self.tracker.update(detections)
            now = time.monotonic()

            # ── Per-track zone analysis ───────────────────────────────
            active_track_ids = set()
            intrusion_candidates = []
            loitering_candidates = []

            for box, conf, track_id in tracked:
                active_track_ids.add(track_id)
                in_zone, zone_name = check_bbox_in_zones(box, self.zones)

                if in_zone and zone_name:
                    zkey = (track_id, zone_name)
                    zs = self._track_zone_states[zkey]

                    # First time in this zone?
                    was_outside = track_id in self._tracks_outside or zs.first_seen_in_zone == 0
                    if zs.first_seen_in_zone == 0:
                        zs.first_seen_in_zone = now
                        zs.grace_start = now

                    zs.last_seen_in_zone = now
                    zs.dwell_s = now - zs.first_seen_in_zone

                    # ── Intrusion: boundary crossing ──────────────────
                    if was_outside and not zs.crossed_boundary:
                        grace_elapsed = now - zs.grace_start
                        if grace_elapsed >= self.enter_grace_s:
                            zs.crossed_boundary = True
                            intrusion_candidates.append({
                                "box": box, "conf": conf, "track_id": track_id,
                                "zone_name": zone_name,
                                "reason": f"boundary_crossing (track {track_id} entered {zone_name})",
                            })

                    # ── Loitering: dwell time ─────────────────────────
                    if self.loitering_enabled and zs.dwell_s >= self.loitering_threshold_s:
                        if not zs.loitering_alerted:
                            zs.loitering_alerted = True
                            loitering_candidates.append({
                                "box": box, "conf": conf, "track_id": track_id,
                                "zone_name": zone_name,
                                "dwell_s": round(zs.dwell_s, 1),
                                "reason": f"loitering (track {track_id} in {zone_name} for {zs.dwell_s:.0f}s)",
                            })

                    self._track_current_zones[track_id].add(zone_name)
                    self._tracks_outside.discard(track_id)
                else:
                    # Track is outside all zones
                    self._tracks_outside.add(track_id)
                    self._track_current_zones[track_id].clear()

            # Evict stale tracks
            for tid in list(self._track_zone_states.keys()):
                if tid[0] not in active_track_ids:
                    del self._track_zone_states[tid]
            for tid in list(self._track_current_zones.keys()):
                if tid not in active_track_ids:
                    del self._track_current_zones[tid]
            self._tracks_outside -= (self._tracks_outside - active_track_ids)

            # ── Package loitering observations for aggregator ─────────
            for lc in loitering_candidates:
                self._pending_loitering.append({
                    "box": [int(b) for b in lc["box"]],
                    "conf": lc["conf"],
                    "track_id": lc["track_id"],
                    "zone_name": lc["zone_name"],
                    "dwell_s": lc["dwell_s"],
                    "reason": lc["reason"],
                })

            # ── Build intrusion observation (primary) ─────────────────
            best_intrusion = None
            max_score = 0.0
            for ic in intrusion_candidates:
                if ic["conf"] > max_score:
                    max_score = ic["conf"]
                    best_intrusion = ic

            # Also trigger on zone presence even without boundary crossing
            if not best_intrusion:
                for box, conf, track_id in tracked:
                    in_zone, zone_name = check_bbox_in_zones(box, self.zones)
                    if in_zone and conf > max_score:
                        max_score = conf
                        best_intrusion = {
                            "box": box, "conf": conf, "track_id": track_id,
                            "zone_name": zone_name,
                            "reason": "person_in_zone",
                        }

            trigger = best_intrusion is not None
            best_bbox = [int(b) for b in best_intrusion["box"]] if best_intrusion else None
            best_zone = best_intrusion["zone_name"] if best_intrusion else None
            best_track = best_intrusion["track_id"] if best_intrusion else None

            # Build reason codes
            reason_codes = []
            if best_intrusion:
                reason_codes.append(best_intrusion.get("reason", "person_in_zone"))
            for lc in loitering_candidates:
                reason_codes.append(lc["reason"])

            self.last_debug = {
                "total_persons": len(tracked),
                "in_zone": trigger,
                "intrusion_count": len(intrusion_candidates),
                "loitering_count": len(loitering_candidates),
                "active_tracks": len(active_track_ids),
                "reason_codes": reason_codes,
                "enter_grace_s": self.enter_grace_s,
                "loitering_threshold_s": self.loitering_threshold_s,
            }

            return Observation(
                ts_utc=ts_utc,
                camera_id=self.camera_id,
                lane=self.lane_name,
                score=max_score,
                trigger=trigger,
                bbox=best_bbox,
                label="person",
                zone_name=best_zone,
                track_id=best_track,
                debug=self.last_debug,
            )

        except Exception as e:
            self.logger.error(f"Inference error: {e}")
            return Observation(
                ts_utc=ts_utc,
                camera_id=self.camera_id,
                lane=self.lane_name,
                score=0.0,
                trigger=False,
                debug={"error": str(e)},
            )

    # ------------------------------------------------------------------
    def get_pending_loitering(self) -> List[Dict[str, Any]]:
        """Return pending loitering events (consumed by aggregator after infer)."""
        return list(self._pending_loitering)
