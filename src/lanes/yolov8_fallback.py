"""
YOLOv8 fallback detector lane.

Kept as a quick-start / redundancy lane.
Thresholds are intentionally permissive (conf 0.20) to favour recall.
"""
import time
import numpy as np
from pathlib import Path
from typing import Dict, Any

from ultralytics import YOLO

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device


class YOLOv8FallbackLane(BaseLane):
    """Fallback detector using ultralytics YOLOv8."""

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.model: YOLO = None  # type: ignore
        self.conf_threshold = 0.20
        self.logger = setup_logger(f"YOLOv8Fallback-{camera_id}")

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("yolov8", {})
        weights = cfg.get("weights", "../yolov8n.pt")
        self.conf_threshold = cfg.get("conf", 0.20)

        # Resolve path relative to ai_module/
        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        if not Path(weights).exists():
            raise FileNotFoundError(f"YOLOv8 weights not found: {weights}")

        self.logger.info(f"Loading YOLOv8 from {weights}")
        self.model = YOLO(weights)

        # Device — use centralized selection
        dev = select_device(self.models_cfg)
        actual_device = dev.torch_device
        self._ul_device = 0 if dev.torch_gpu else "cpu"  # for predict() calls
        self.model.to(actual_device)

        self._initialized = True
        self.logger.info(f"YOLO inference device: {actual_device}")

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        if not self._initialized:
            self.init()

        t0 = time.perf_counter()
        results = self.model(frame_bgr, verbose=False, conf=self.conf_threshold,
                            device=self._ul_device,
                            half=isinstance(self._ul_device, int))  # FP16 on GPU
        dt = time.perf_counter() - t0

        best_score = 0.0
        best_bbox = None
        best_label = None
        trigger = False
        num_dets = 0

        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            num_dets = len(boxes)
            names = results[0].names  # class-id → name
            for i in range(num_dets):
                conf = float(boxes.conf[i])
                if conf > best_score:
                    best_score = conf
                    box = boxes.xyxy[i].cpu().numpy().tolist()
                    best_bbox = [int(c) for c in box]
                    cls_id = int(boxes.cls[i])
                    best_label = names.get(cls_id, f"class_{cls_id}")
                    trigger = True

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=best_score,
            trigger=trigger,
            bbox=best_bbox,
            label=best_label,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "num_detections": num_dets,
            },
        )
