"""
Fire / smoke detection lane – dedicated YOLO model.

Uses the ``fire_smoke`` key in ``models.yaml``.
Community pre-trained weights (no custom training needed).

Key safety rules (spec §1):
  • If dedicated fire weights are missing → lane is DISABLED (no fallback).
  • Class filter is mandatory: only detections whose class name is in
    ``class_names`` (default {"fire","smoke"}) pass.
  • Quality gates: min_area_px, min_area_ratio, max_boxes_considered.
  • Persistence tracking via ``min_persistence_hits`` exported in debug for
    aggregator two-stage confirm logic.
"""
import time
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device
from ..logic.class_filter import resolve_class_filter


class FireSmokeYOLOLane(BaseLane):
    """Dedicated fire / smoke detection using a separate YOLO model."""

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.model = None
        self.conf_threshold = 0.30
        self.logger = setup_logger(f"FireSmokeYOLO-{camera_id}")

        # --- quality-gate defaults (overridden from config) ---
        self.class_names: Set[str] = {"fire", "smoke"}
        self.class_ids: Optional[Set[int]] = None
        self.min_area_px: int = 800
        self.min_area_ratio: float = 0.001
        self.min_persistence_hits: int = 2
        self.max_boxes_considered: int = 3

        # Rolling hit counter for persistence tracking
        self._recent_trigger_flags: List[bool] = []
        self._persistence_window: int = 5  # same as K-of-N window

        # Lane can be disabled if weights are missing
        self._active = False

        # Resolved class-id filter (populated after model load)
        self._resolved_class_ids: Optional[Set[int]] = None
        # Full model.names mapping (exposed for diagnostics)
        self.model_names: Dict[int, str] = {}

        # Per-frame debug log for FP debugging panel
        self.last_debug_detections: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("fire_smoke", {})
        weights = cfg.get("weights", "models/fire_yolov8.pt")
        self.conf_threshold = cfg.get("conf", 0.30)

        # Config overrides
        self.class_names = set(cfg.get("class_names", ["fire", "smoke"]))
        self.class_ids = set(cfg["class_ids"]) if "class_ids" in cfg else None
        self.min_area_px = cfg.get("min_area_px", 800)
        self.min_area_ratio = cfg.get("min_area_ratio", 0.001)
        self.min_persistence_hits = cfg.get("min_persistence_hits", 2)
        self.max_boxes_considered = cfg.get("max_boxes_considered", 3)

        # Resolve path
        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        if not Path(weights).exists():
            # *** NO FALLBACK — disable the lane ***
            self.logger.warning(
                f"Fire/smoke weights not found ({weights}). "
                f"Lane DISABLED — no fire alerts will be produced."
            )
            self._active = False
            self._initialized = True
            return

        from ultralytics import YOLO
        self.logger.info(f"Loading fire/smoke model from {weights}")
        self.model = YOLO(weights)

        # Resolve class filter using model's actual class names
        cfg_class_names = list(self.class_names) if self.class_names else None
        cfg_class_ids = list(self.class_ids) if self.class_ids else None
        resolved_ids, self.model_names = resolve_class_filter(
            self.model,
            class_names=cfg_class_names,
            class_ids=cfg_class_ids,
            lane_name="fire_smoke",
            logger=self.logger,
        )
        if resolved_ids is None and cfg_class_names:
            # Complete class mapping failure — disable lane
            self.logger.error(
                "Fire/smoke lane DISABLED — class mapping failed. "
                f"Set models.fire_smoke.class_ids using model.names: {self.model_names}"
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
            f"Fire/smoke inference device: {actual_device}, "
            f"model.names: {self.model_names}"
        )

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        if not self._initialized:
            self.init()

        # If lane is disabled, always return no-trigger
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

        frame_h, frame_w = frame_bgr.shape[:2]
        frame_area = frame_h * frame_w

        t0 = time.perf_counter()
        results = self.model(frame_bgr, verbose=False, conf=self.conf_threshold,
                            device=self._ul_device,
                            half=isinstance(self._ul_device, int))  # FP16 on GPU
        dt = time.perf_counter() - t0

        # ---- Collect & filter detections ----
        all_dets: List[Dict[str, Any]] = []
        kept_dets: List[Dict[str, Any]] = []

        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            names = results[0].names  # class-id → name mapping
            for i in range(len(boxes)):
                conf = float(boxes.conf[i])
                cls_id = int(boxes.cls[i])
                cls_name = names.get(cls_id, f"class_{cls_id}").lower()
                box = boxes.xyxy[i].cpu().numpy().tolist()
                bbox = [int(c) for c in box]
                area = max(0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
                area_ratio = area / max(frame_area, 1)

                det_info = {
                    "class": cls_name,
                    "class_id": cls_id,
                    "conf": round(conf, 3),
                    "bbox": bbox,
                    "area_px": area,
                    "area_ratio": round(area_ratio, 6),
                    "reason": "kept",
                }

                # --- Filter 1: class name / class id ---
                class_ok = False
                if self._resolved_class_ids is not None:
                    class_ok = cls_id in self._resolved_class_ids
                else:
                    # No filter resolved — allow all
                    class_ok = True
                if not class_ok:
                    det_info["reason"] = f"class_rejected ({cls_name})"
                    all_dets.append(det_info)
                    continue

                # --- Filter 2: min_area_px ---
                if area < self.min_area_px:
                    det_info["reason"] = f"area_too_small ({area}<{self.min_area_px})"
                    all_dets.append(det_info)
                    continue

                # --- Filter 3: min_area_ratio ---
                if area_ratio < self.min_area_ratio:
                    det_info["reason"] = f"area_ratio_too_small ({area_ratio:.5f}<{self.min_area_ratio})"
                    all_dets.append(det_info)
                    continue

                all_dets.append(det_info)
                kept_dets.append(det_info)

        # --- Limit to max_boxes_considered (highest conf first) ---
        kept_dets.sort(key=lambda d: d["conf"], reverse=True)
        kept_dets = kept_dets[:self.max_boxes_considered]

        # --- Determine trigger & best detection ---
        best_score = 0.0
        best_bbox = None
        for d in kept_dets:
            if d["conf"] > best_score:
                best_score = d["conf"]
                best_bbox = d["bbox"]

        trigger = best_score > self.conf_threshold and len(kept_dets) > 0

        # --- Persistence tracking ---
        self._recent_trigger_flags.append(trigger)
        if len(self._recent_trigger_flags) > self._persistence_window:
            self._recent_trigger_flags = self._recent_trigger_flags[-self._persistence_window:]
        persistence_hits = sum(self._recent_trigger_flags)

        # Store debug info for FP debugging panel (top N detections)
        self.last_debug_detections = all_dets[:10]

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=best_score,
            trigger=trigger,
            bbox=best_bbox,
            label="fire_smoke" if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "num_raw_detections": len(all_dets),
                "num_kept": len(kept_dets),
                "persistence_hits": persistence_hits,
                "persistence_window": self._persistence_window,
                "min_persistence_required": self.min_persistence_hits,
                "filters": {
                    "class_names": list(self.class_names),
                    "min_area_px": self.min_area_px,
                    "min_area_ratio": self.min_area_ratio,
                    "max_boxes": self.max_boxes_considered,
                },
                "top_detections": all_dets[:5],
            },
        )
