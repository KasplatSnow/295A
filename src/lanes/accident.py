"""
Accident / traffic crash detection lane (optional).

Spec §7: Only enabled when camera is flagged as "traffic" mode.
  - For traffic mode: allow YOLO crash models / traffic anomaly datasets
    (DoTA, TU-DAT) as future integration.
  - Otherwise keep accident off and rely on fall + unknown anomaly.

Current implementation: stub with motion anomaly heuristic for traffic scenes.
When a proper crash detection model becomes available, it can be loaded here.

Future integration points:
  - Detection-of-Traffic-Anomaly (MoonBlvd/Detection-of-Traffic-Anomaly)
  - DoTA dataset / TU-DAT dataset fine-tuned models
"""
import time
import numpy as np
import cv2
from typing import Dict, Any, Optional, List
from collections import deque

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger


class AccidentLane(BaseLane):
    """
    Traffic accident/crash detection — stub with motion anomaly heuristic.

    Only activates if camera config has ``mode: traffic``.
    """

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.logger = setup_logger(f"Accident-{camera_id}")
        self._active = False
        self.model = None

        # Motion state
        self._prev_gray: Optional[np.ndarray] = None
        self._motion_history: deque = deque(maxlen=30)
        self._sudden_stop_threshold = 0.6
        self._baseline_motion: float = 0.0
        self._frames_processed: int = 0

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("accident", {})
        self._active = cfg.get("enabled", False)
        self._sudden_stop_threshold = cfg.get("sudden_stop_threshold", 0.6)

        # Future: load dedicated crash model if weights path provided
        weights = cfg.get("weights", "")
        if weights:
            from pathlib import Path
            if not Path(weights).is_absolute():
                weights = str((Path(__file__).parent.parent.parent / weights).resolve())
            if Path(weights).exists():
                try:
                    from ultralytics import YOLO
                    self.model = YOLO(weights)
                    from ..runtime.device import select_device
                    dev = select_device(self.models_cfg)
                    self._ul_device = 0 if dev.torch_gpu else "cpu"
                    self.model.to(dev.torch_device)
                    self.logger.info(f"Loaded accident model from {weights}")
                except Exception as e:
                    self.logger.warning(f"Accident model load failed: {e}")

        self._initialized = True
        if self._active:
            self.logger.info(
                f"Accident lane ENABLED (model={'loaded' if self.model else 'stub_motion'})"
            )
        else:
            self.logger.info("Accident lane DISABLED (camera not in traffic mode)")

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
                debug={"disabled": True, "reason": "not_traffic_mode"},
            )

        t0 = time.perf_counter()

        # ── If we have a dedicated model, use it ─────────────────────
        if self.model is not None:
            return self._infer_model(frame_bgr, ts_utc, t0)

        # ── Stub: motion anomaly heuristic for traffic ────────────────
        return self._infer_motion_stub(frame_bgr, ts_utc, t0)

    # ------------------------------------------------------------------
    def _infer_model(self, frame_bgr: np.ndarray, ts_utc: str,
                     t0: float) -> Observation:
        """Inference with dedicated crash detection model."""
        results = self.model(frame_bgr, verbose=False, conf=0.35,
                            device=self._ul_device)
        dt = time.perf_counter() - t0

        best_score = 0.0
        best_bbox = None
        best_label = None

        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            names = results[0].names
            all_xyxy = boxes.xyxy.cpu().numpy()
            all_conf = boxes.conf.cpu().numpy()
            all_cls = boxes.cls.cpu().numpy()
            for i in range(len(boxes)):
                conf = float(all_conf[i])
                cls_name = names.get(int(all_cls[i]), "crash").lower()
                if conf > best_score:
                    best_score = conf
                    best_bbox = [int(c) for c in all_xyxy[i].tolist()]
                    best_label = cls_name

        trigger = best_score > 0.35
        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=best_score,
            trigger=trigger,
            bbox=best_bbox,
            label=best_label if trigger else None,
            debug={
                "inference_ms": round((time.perf_counter() - t0) * 1000, 1),
                "mode": "model",
                "reason_codes": [f"model_detection ({best_score:.2f})"] if trigger else [],
            },
        )

    # ------------------------------------------------------------------
    def _infer_motion_stub(self, frame_bgr: np.ndarray, ts_utc: str,
                           t0: float) -> Observation:
        """
        Traffic anomaly heuristic: detect sudden motion pattern changes.
        In traffic scenes, a crash often causes:
          1. Sudden deceleration (high motion → low motion quickly)
          2. Unusual stopped vehicles (very low motion in normally active area)
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        motion_energy = 0.0
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            motion_energy = min(float(np.mean(diff)) / 80.0, 1.0)

        self._prev_gray = gray
        self._motion_history.append(motion_energy)
        self._frames_processed += 1

        # Need enough history for baseline
        if len(self._motion_history) < 10:
            dt = time.perf_counter() - t0
            return Observation(
                ts_utc=ts_utc,
                camera_id=self.camera_id,
                lane=self.lane_name,
                score=0.0,
                trigger=False,
                debug={
                    "inference_ms": round(dt * 1000, 1),
                    "mode": "stub_warmup",
                    "frames_buffered": len(self._motion_history),
                },
            )

        # Compute baseline (rolling average minus recent 3 frames)
        history = list(self._motion_history)
        baseline = float(np.mean(history[:-3])) if len(history) > 3 else float(np.mean(history))
        recent = float(np.mean(history[-3:]))
        self._baseline_motion = baseline

        # Sudden stop detection: baseline was high, recent is very low
        sudden_stop = (baseline > 0.15 and recent < baseline * 0.3)
        # Motion spike: could indicate collision
        motion_spike = (recent > baseline * 3.0 and recent > 0.4)

        score = 0.0
        reason_codes = []
        if sudden_stop:
            score = 0.7
            reason_codes.append(f"sudden_stop (baseline={baseline:.2f}, recent={recent:.2f})")
        elif motion_spike:
            score = 0.5
            reason_codes.append(f"motion_spike (baseline={baseline:.2f}, recent={recent:.2f})")

        trigger = score >= self._sudden_stop_threshold
        dt = time.perf_counter() - t0

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=round(score, 3),
            trigger=trigger,
            label="accident" if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "mode": "stub_motion",
                "motion_energy": round(motion_energy, 3),
                "baseline_motion": round(baseline, 3),
                "recent_motion": round(recent, 3),
                "sudden_stop": sudden_stop,
                "motion_spike": motion_spike,
                "reason_codes": reason_codes,
            },
        )
