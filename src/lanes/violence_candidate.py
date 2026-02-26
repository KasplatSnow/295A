"""
Violence candidate lane — cheap heuristic detector.

Uses motion-energy + person density change to flag possible violence.
When candidate_hits reach threshold (2/5), the aggregator will invoke
the temporal verifier for full confirmation before emitting VIOLENCE_FIGHT.

This lane is CHEAP — runs at anomaly Hz (~0.5 Hz).
"""
import time
import numpy as np
import cv2
from typing import Dict, Any, Optional

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger


class ViolenceCandidateLane(BaseLane):
    """Motion-energy + person density heuristic for violence candidate detection."""

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.logger = setup_logger(f"ViolenceCandidate-{camera_id}")
        self.motion_threshold = 0.60  # §3 tightened from 0.55
        self.candidate_hits = 2
        self._prev_gray: Optional[np.ndarray] = None
        self._prev_person_count: int = 0
        self._energy_history: list = []
        self._max_history = 10

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("violence_candidate", {})
        self.motion_threshold = cfg.get("motion_threshold", 0.60)
        self.candidate_hits = cfg.get("candidate_hits", 2)
        self._initialized = True
        self.logger.info(
            f"Violence candidate lane ready "
            f"(motion_threshold={self.motion_threshold})"
        )

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        if not self._initialized:
            self.init()

        t0 = time.perf_counter()

        # --- Motion energy ---
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        motion_energy = 0.0
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            motion_energy = float(np.mean(diff)) / 80.0
            motion_energy = min(motion_energy, 1.0)
        self._prev_gray = gray

        # --- Motion energy spike detection ---
        self._energy_history.append(motion_energy)
        if len(self._energy_history) > self._max_history:
            self._energy_history = self._energy_history[-self._max_history:]

        # Compute spike: current energy vs rolling average
        avg_energy = np.mean(self._energy_history[:-1]) if len(self._energy_history) > 1 else 0
        spike = motion_energy - avg_energy

        # --- Dense motion region detection (proxy for clustered people fighting) ---
        # Count high-motion pixels as fraction of frame
        dense_motion_ratio = 0.0
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
            dense_motion_ratio = float(np.sum(thresh > 0)) / max(thresh.size, 1)

        # --- Score: weighted combination ---
        score = 0.6 * motion_energy + 0.3 * min(spike * 2, 1.0) + 0.1 * dense_motion_ratio
        score = min(score, 1.0)

        trigger = score >= self.motion_threshold

        dt = time.perf_counter() - t0

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=round(score, 3),
            trigger=trigger,
            label="violence_candidate" if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "motion_energy": round(motion_energy, 3),
                "spike": round(spike, 3),
                "dense_motion_ratio": round(dense_motion_ratio, 4),
                "threshold": self.motion_threshold,
            },
        )
