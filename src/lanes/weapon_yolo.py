"""
Weapon detection lane — dedicated YOLO model.

Uses the ``weapon_yolo`` key in ``models.yaml``.
Requires custom-trained model with weapon classes (knife, gun, weapon).

If weights are absent → lane is DISABLED gracefully (no fallback).
Produces WEAPON_DETECTED alerts through aggregator.
"""
import time
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, Set

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device
from ..logic.class_filter import resolve_class_filter


class WeaponYOLOLane(BaseLane):
    """Dedicated weapon detection using a separate YOLO model."""

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.model = None
        self.conf_threshold = 0.35
        self.logger = setup_logger(f"WeaponYOLO-{camera_id}")
        self.class_names: Set[str] = {"knife", "gun", "weapon"}
        self._active = False
        # Resolved class-id filter (populated after model load)
        self._resolved_class_ids: Optional[Set[int]] = None
        # Full model.names mapping (exposed for diagnostics)
        self.model_names: Dict[int, str] = {}

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

        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        if not Path(weights).exists():
            self.logger.warning(
                f"Weapon YOLO weights not found ({weights}). "
                f"Lane DISABLED — no weapon alerts will be produced."
            )
            self._active = False
            self._initialized = True
            return

        from ultralytics import YOLO
        self.logger.info(f"Loading weapon model from {weights}")
        self.model = YOLO(weights)

        # Resolve class filter using model's actual class names
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
            # Complete class mapping failure — disable lane
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
            f"model.names: {self.model_names}"
        )

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

        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            names = results[0].names
            num_dets = len(boxes)
            for i in range(num_dets):
                conf = float(boxes.conf[i])
                cls_id = int(boxes.cls[i])
                cls_name = names.get(cls_id, f"class_{cls_id}").lower()

                # Class filter: only weapon classes (resolved IDs)
                if self._resolved_class_ids is not None:
                    if cls_id not in self._resolved_class_ids:
                        continue
                # else: no filter → allow all

                if conf > best_score:
                    best_score = conf
                    box = boxes.xyxy[i].cpu().numpy().tolist()
                    best_bbox = [int(c) for c in box]
                    best_label = cls_name

        trigger = best_score > self.conf_threshold and best_label is not None

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=best_score,
            trigger=trigger,
            bbox=best_bbox,
            label=best_label if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "num_detections": num_dets,
            },
        )
