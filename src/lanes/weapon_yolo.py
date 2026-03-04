"""
Weapon detection lane — dedicated YOLO model with proximity gating.

Spec §6:
  • Default weights: HF Shantanukadam/weapon_detection → All_weapon.pt
  • Require weapon bbox to be NEAR a person bbox (or near hands if pose on).
  • Persistence 3/5.
  • Severity HIGH/SEVERE only if unknown person or restricted zone
    (handled by aggregator + identity policy).

If weights are absent → lane is DISABLED gracefully (no fallback).
Produces WEAPON_DETECTED alerts through aggregator.
"""
import time
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, Set, List, Tuple
from collections import deque

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device
from ..logic.class_filter import resolve_class_filter


def _bbox_center(bbox: List[int]) -> Tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _bbox_distance(a: List[int], b: List[int]) -> float:
    ca, cb = _bbox_center(a), _bbox_center(b)
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5


class WeaponYOLOLane(BaseLane):
    """Dedicated weapon detection with person-proximity gating."""

    # COCO hand keypoint indices (left_wrist=9, right_wrist=10)
    _WRIST_INDICES = (9, 10)

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.model = None
        self.conf_threshold = 0.35
        self.logger = setup_logger(f"WeaponYOLO-{camera_id}")
        self.class_names: Set[str] = {"knife", "gun", "weapon"}
        self._active = False
        self._resolved_class_ids: Optional[Set[int]] = None
        self.model_names: Dict[int, str] = {}

        # ── Proximity gating ─────────────────────────────────────────
        self.proximity_required: bool = True
        self.proximity_thresh_px: int = 200
        self.hand_proximity_thresh_px: int = 100  # tighter if pose available

        # ── Persistence 3/5 ──────────────────────────────────────────
        self._persistence_window: int = 5
        self._persistence_k: int = 3
        self._recent_trigger_flags: deque = deque(maxlen=5)

        # ── Shared caches (injected externally) ──────────────────────
        self._detection_cache = None
        self._pose_cache = None

    # ------------------------------------------------------------------
    def set_detection_cache(self, cache):
        self._detection_cache = cache

    def set_pose_cache(self, cache):
        self._pose_cache = cache

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("weapon_yolo", {})

        if not cfg.get("enabled", True):
            self.logger.info("Weapon YOLO lane disabled in config")
            self._active = False
            self._initialized = True
            return

        weights = cfg.get("weights", "models/weapon_yolov8.pt")
        self.conf_threshold = cfg.get("conf", 0.35)
        self.class_names = set(cfg.get("class_names", ["knife", "gun", "weapon"]))
        self.proximity_required = cfg.get("proximity_required", True)
        self.proximity_thresh_px = cfg.get("proximity_thresh_px", 200)
        self.hand_proximity_thresh_px = cfg.get("hand_proximity_thresh_px", 100)
        self._persistence_window = cfg.get("persistence_n", 5)
        self._persistence_k = cfg.get("persistence_k", 3)
        self._recent_trigger_flags = deque(maxlen=self._persistence_window)

        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        if not Path(weights).exists():
            # Try HF download
            downloaded = self._try_download_weights(cfg, weights)
            if not downloaded:
                self.logger.warning(
                    f"Weapon YOLO weights not found ({weights}). "
                    f"Lane DISABLED — no weapon alerts will be produced."
                )
                self._active = False
                self._initialized = True
                return
            weights = downloaded

        from ultralytics import YOLO
        self.logger.info(f"Loading weapon model from {weights}")
        self.model = YOLO(weights)

        cfg_class_names = list(self.class_names) if self.class_names else None
        cfg_class_ids = list(cfg["class_ids"]) if "class_ids" in cfg else None
        resolved_ids, self.model_names = resolve_class_filter(
            self.model,
            class_names=cfg_class_names,
            class_ids=cfg_class_ids,
            lane_name="weapon_yolo",
            logger=self.logger,
        )
        if resolved_ids is None and cfg_class_names:
            self.logger.error(
                "Weapon YOLO lane DISABLED — class mapping failed. "
                f"Set models.weapon_yolo.class_ids using model.names: {self.model_names}"
            )
            self._active = False
            self._initialized = True
            return
        self._resolved_class_ids = resolved_ids

        dev = select_device(self.models_cfg)
        actual_device = dev.torch_device
        self._ul_device = 0 if dev.torch_gpu else "cpu"
        self.model.to(actual_device)

        self._active = True
        self._initialized = True
        self.logger.info(
            f"Weapon inference device: {actual_device}, "
            f"model.names: {self.model_names}, "
            f"proximity_required={self.proximity_required}, "
            f"persistence={self._persistence_k}/{self._persistence_window}"
        )

    # ------------------------------------------------------------------
    def _try_download_weights(self, cfg: dict, target_path: str) -> Optional[str]:
        """Auto-download weapon weights from HF."""
        hf_repo = cfg.get("hf_repo_id", "Shantanukadam/weapon_detection")
        hf_file = cfg.get("hf_filename", "All_weapon.pt")
        if not hf_repo or not hf_file:
            return None
        try:
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(repo_id=hf_repo, filename=hf_file)
            import shutil
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(downloaded, target_path)
            self.logger.info(f"Downloaded weapon weights from HF to {target_path}")
            return target_path
        except Exception as e:
            self.logger.warning(f"HF weapon download failed: {e}")
            return None

    # ------------------------------------------------------------------
    def _get_person_bboxes(self) -> List[List[int]]:
        """Get person bboxes from detection cache."""
        persons = []
        if self._detection_cache is not None:
            cached = self._detection_cache.get_latest(self.camera_id)
            if cached:
                for det in cached.get("detections", []):
                    label = (det.get("label") or "").lower()
                    if label == "person" and "bbox" in det:
                        persons.append([int(c) for c in det["bbox"]])
        return persons

    # ------------------------------------------------------------------
    def _get_hand_positions(self) -> List[Tuple[float, float]]:
        """Get wrist/hand keypoints from pose cache if available."""
        hands = []
        if self._pose_cache is None:
            return hands
        pose_data = self._pose_cache.get(self.camera_id, max_age_s=2.0)
        if not pose_data:
            return hands
        for person in pose_data:
            kpts = person.get("keypoints", [])
            if not kpts or len(kpts) < 11:
                continue
            for idx in self._WRIST_INDICES:
                if idx < len(kpts):
                    kp = kpts[idx]
                    if len(kp) >= 3 and kp[2] > 0.3:  # conf > 0.3
                        hands.append((float(kp[0]), float(kp[1])))
        return hands

    # ------------------------------------------------------------------
    def _check_proximity(self, weapon_bbox: List[int]) -> Tuple[bool, str]:
        """
        Check if weapon bbox is near a person bbox or near hands.
        Returns (is_near, reason).
        """
        if not self.proximity_required:
            return True, "proximity_disabled"

        weapon_center = _bbox_center(weapon_bbox)

        # Check hand proximity first (tighter threshold, more confident)
        hands = self._get_hand_positions()
        for hx, hy in hands:
            dist = ((weapon_center[0] - hx) ** 2 + (weapon_center[1] - hy) ** 2) ** 0.5
            if dist <= self.hand_proximity_thresh_px:
                return True, f"near_hand (dist={dist:.0f}px)"

        # Check person bbox proximity
        person_bboxes = self._get_person_bboxes()
        for pb in person_bboxes:
            dist = _bbox_distance(weapon_bbox, pb)
            if dist <= self.proximity_thresh_px:
                return True, f"near_person (dist={dist:.0f}px)"

        if not person_bboxes and not hands:
            # No person data available — allow through (can't gate)
            return True, "no_person_data_available"

        return False, f"no_person_nearby (closest={self._closest_person_dist(weapon_bbox, person_bboxes):.0f}px)"

    def _closest_person_dist(self, weapon_bbox: List[int], person_bboxes: List[List[int]]) -> float:
        if not person_bboxes:
            return float("inf")
        return min(_bbox_distance(weapon_bbox, pb) for pb in person_bboxes)

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        if not self._initialized:
            self.init()

        if not self._active:
            return Observation(
                ts_utc=ts_utc,
                camera_id=self.camera_id,
                lane=self.lane_name,
                score=0.0,
                trigger=False,
                label=None,
                debug={"disabled": True, "reason": "weights_missing"},
            )

        t0 = time.perf_counter()
        results = self.model(frame_bgr, verbose=False, conf=self.conf_threshold,
                            device=self._ul_device,
                            half=isinstance(self._ul_device, int))
        dt = time.perf_counter() - t0

        best_score = 0.0
        best_bbox = None
        best_label = None
        num_dets = 0
        all_weapon_dets = []

        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            names = results[0].names
            num_dets = len(boxes)
            all_xyxy = boxes.xyxy.cpu().numpy()
            all_conf_arr = boxes.conf.cpu().numpy()
            all_cls_arr = boxes.cls.cpu().numpy()
            for i in range(num_dets):
                conf = float(all_conf_arr[i])
                cls_id = int(all_cls_arr[i])
                cls_name = names.get(cls_id, f"class_{cls_id}").lower()

                if self._resolved_class_ids is not None:
                    if cls_id not in self._resolved_class_ids:
                        continue

                bbox = [int(c) for c in all_xyxy[i].tolist()]
                all_weapon_dets.append({"bbox": bbox, "conf": conf, "label": cls_name})

                if conf > best_score:
                    best_score = conf
                    best_bbox = bbox
                    best_label = cls_name

        # ── Proximity gating ─────────────────────────────────────────
        proximity_ok = False
        proximity_reason = "no_detection"
        if best_bbox is not None:
            proximity_ok, proximity_reason = self._check_proximity(best_bbox)

        trigger = (best_score > self.conf_threshold
                   and best_label is not None
                   and proximity_ok)

        # ── Persistence tracking (3/5) ────────────────────────────────
        self._recent_trigger_flags.append(trigger)
        persistence_hits = sum(self._recent_trigger_flags)

        # Build reason codes
        reason_codes = []
        if best_label:
            reason_codes.append(f"detected:{best_label} ({best_score:.2f})")
        if proximity_ok:
            reason_codes.append(proximity_reason)
        elif best_bbox:
            reason_codes.append(f"suppressed:{proximity_reason}")
        if persistence_hits >= self._persistence_k:
            reason_codes.append(f"persistence ({persistence_hits}/{self._persistence_window})")

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=best_score if proximity_ok else 0.0,
            trigger=trigger,
            bbox=best_bbox if trigger else None,
            label=best_label if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "num_detections": num_dets,
                "num_weapon_dets": len(all_weapon_dets),
                "proximity_ok": proximity_ok,
                "proximity_reason": proximity_reason,
                "persistence_hits": persistence_hits,
                "persistence_k": self._persistence_k,
                "persistence_n": self._persistence_window,
                "reason_codes": reason_codes,
            },
        )
