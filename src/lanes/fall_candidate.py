"""
Fall candidate lane — cheap heuristic detector.

Detects potential falls using person bbox aspect-ratio flip + sudden
vertical displacement heuristics. Requires a person to be present
(uses the same person detector / YOLO as person_zone lane).

When candidate triggers reach threshold, aggregator invokes temporal
verifier for full confirmation before emitting FALL alert.

This lane is CHEAP — runs at anomaly Hz (~0.5 Hz).
"""
import time
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import cv2

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device


class FallCandidateLane(BaseLane):
    """
    Detect potential falls using person bbox analysis:
      1. Aspect ratio flip (tall → wide = person went from standing to lying)
      2. Sudden vertical displacement (Y-center jumped downward)
      3. Track velocity spike (optional, from IoU tracker)
    """

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.logger = setup_logger(f"FallCandidate-{camera_id}")
        self.model = None
        self.conf_threshold = 0.25

        # Heuristic thresholds
        self.aspect_ratio_threshold = 1.0   # below 1.0 = wider than tall
        self.y_displacement_threshold = 40  # pixels
        self.velocity_threshold = 30.0      # pixels/frame

        # History for person tracking
        self._prev_persons: List[Tuple[List[int], float]] = []  # [(bbox, conf), ...]

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("person_detector", {})
        weights = cfg.get("weights", "../yolov8n.pt")
        self.conf_threshold = cfg.get("conf", 0.25)

        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        if not Path(weights).exists():
            self.logger.warning(f"Person detector weights not found ({weights}), fall lane disabled")
            self._initialized = True
            return

        from ultralytics import YOLO
        self.model = YOLO(weights)
        dev = select_device(self.models_cfg)
        actual_device = dev.torch_device
        self._ul_device = 0 if dev.torch_gpu else "cpu"
        self.model.to(actual_device)

        self._initialized = True
        self.logger.info(f"Fall candidate inference device: {actual_device}")

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        if not self._initialized:
            self.init()

        if self.model is None:
            return Observation(
                ts_utc=ts_utc, camera_id=self.camera_id, lane=self.lane_name,
                score=0.0, trigger=False, label=None,
                debug={"disabled": True, "reason": "no_person_model"},
            )

        t0 = time.perf_counter()

        # Detect persons (class 0 = person in COCO)
        results = self.model(frame_bgr, verbose=False, conf=self.conf_threshold,
                            classes=[0], device=self._ul_device)
        dt = time.perf_counter() - t0

        curr_persons: List[Tuple[List[int], float]] = []
        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            for i in range(len(boxes)):
                box = boxes.xyxy[i].cpu().numpy().tolist()
                conf = float(boxes.conf[i])
                curr_persons.append(([int(c) for c in box], conf))

        # --- Fall heuristics ---
        best_fall_score = 0.0
        best_bbox = None
        fall_reason = "none"

        for bbox, conf in curr_persons:
            x1, y1, x2, y2 = bbox
            w = x2 - x1
            h = y2 - y1
            aspect_ratio = h / max(w, 1)
            y_center = (y1 + y2) / 2

            # Check 1: Aspect ratio flip (person wider than tall = possibly fallen)
            ar_score = 0.0
            if aspect_ratio < self.aspect_ratio_threshold:
                # The lower the ratio, the more likely a fall
                ar_score = max(0, (self.aspect_ratio_threshold - aspect_ratio) / self.aspect_ratio_threshold)

            # Check 2: Match to previous frame person, check Y displacement
            y_disp_score = 0.0
            for prev_bbox, _ in self._prev_persons:
                px1, py1, px2, py2 = prev_bbox
                prev_y_center = (py1 + py2) / 2
                prev_aspect = (py2 - py1) / max(px2 - px1, 1)

                # IoU-based matching (simple)
                iou = self._compute_iou(bbox, prev_bbox)
                if iou < 0.1:
                    continue

                # Y displacement (downward motion)
                y_disp = y_center - prev_y_center
                if y_disp > self.y_displacement_threshold:
                    y_disp_score = min(y_disp / (self.y_displacement_threshold * 3), 1.0)

                # Velocity spike (combined x+y displacement)
                dx = abs((x1 + x2) / 2 - (px1 + px2) / 2)
                dy = abs(y_disp)
                velocity = np.sqrt(dx**2 + dy**2)
                if velocity > self.velocity_threshold:
                    y_disp_score = max(y_disp_score, min(velocity / (self.velocity_threshold * 3), 1.0))

                # Aspect ratio change (standing → lying)
                if prev_aspect > 1.2 and aspect_ratio < 1.0:
                    ar_score = max(ar_score, 0.7)

            # Combined score
            score = 0.6 * ar_score + 0.4 * y_disp_score
            score = min(score * conf, 1.0)  # Weight by detection confidence

            if score > best_fall_score:
                best_fall_score = score
                best_bbox = bbox
                fall_reason = f"ar={aspect_ratio:.2f}, y_disp_score={y_disp_score:.2f}"

        # Update history
        self._prev_persons = curr_persons

        trigger = best_fall_score > 0.45  # §3 tightened from 0.3 to reduce FPs
        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=round(best_fall_score, 3),
            trigger=trigger,
            bbox=best_bbox,
            label="fall_candidate" if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "num_persons": len(curr_persons),
                "fall_reason": fall_reason,
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _compute_iou(box_a: List[int], box_b: List[int]) -> float:
        x1 = max(box_a[0], box_b[0])
        y1 = max(box_a[1], box_b[1])
        x2 = min(box_a[2], box_b[2])
        y2 = min(box_a[3], box_b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area_a = max(0, box_a[2] - box_a[0]) * max(0, box_a[3] - box_a[1])
        area_b = max(0, box_b[2] - box_b[0]) * max(0, box_b[3] - box_b[1])
        union = area_a + area_b - inter
        return inter / max(union, 1e-6)
