"""
Violence candidate lane — person-centric proximity + local motion.

Spec §4: Violence candidate requires:
  • ≥2 persons in proximity (within ``proximity_thresh_px``)
  • High local motion *around* the interacting person bboxes
  • Person-centric clip ROI for temporal verifier (X3D/SlowFast)

If temporal verifier unavailable: do NOT emit SEVERE.
Emit MED only if persistence is very strong (4/5).

Uses shared FrameDetectionCache or PoseCache for person detections.
If neither available, falls back to frame-differencing alone (legacy).

References:
  - ShwetaNagapure/RWF-2000-X3D-Violence-Detection
  - MDPI Person-Centric Violence Detection
"""
import time
import numpy as np
import cv2
from typing import Dict, Any, Optional, List, Tuple
from collections import deque

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger


def _bbox_center(bbox: List[float]) -> Tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _bbox_distance(a: List[float], b: List[float]) -> float:
    ca, cb = _bbox_center(a), _bbox_center(b)
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5


def _expand_bbox(bbox: List[float], margin: float, h: int, w: int) -> List[int]:
    """Expand bbox by margin fraction, clamp to frame."""
    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    mx, my = bw * margin, bh * margin
    return [
        max(0, int(bbox[0] - mx)),
        max(0, int(bbox[1] - my)),
        min(w, int(bbox[2] + mx)),
        min(h, int(bbox[3] + my)),
    ]


class ViolenceCandidateLane(BaseLane):
    """Person-centric violence candidate: proximity + local motion."""

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.logger = setup_logger(f"ViolenceCandidate-{camera_id}")

        # ── Config defaults ───────────────────────────────────────────
        self.motion_threshold = 0.60
        self.proximity_thresh_px = 200   # max distance between person centers
        self.min_persons = 2             # minimum persons in proximity group
        self.local_motion_weight = 0.50
        self.global_motion_weight = 0.30
        self.proximity_weight = 0.20
        self.candidate_hits = 3
        self.bbox_expand_margin = 0.3    # expand person bbox for local motion ROI

        # ── State ─────────────────────────────────────────────────────
        self._prev_gray: Optional[np.ndarray] = None
        self._energy_history: deque = deque(maxlen=10)
        self._detection_cache = None  # set externally
        self._pose_cache = None       # set externally

        # Person-centric clip buffer (for temporal verifier)
        self._person_clip_buffer: deque = deque(maxlen=32)  # ~2s @ 16fps

    # ------------------------------------------------------------------
    def set_detection_cache(self, cache):
        self._detection_cache = cache

    def set_pose_cache(self, cache):
        self._pose_cache = cache

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("violence_candidate", {})
        self.motion_threshold = cfg.get("motion_threshold", 0.60)
        self.candidate_hits = cfg.get("candidate_hits", 3)
        self.proximity_thresh_px = cfg.get("proximity_thresh_px", 200)
        self.min_persons = cfg.get("min_persons", 2)
        self.local_motion_weight = cfg.get("local_motion_weight", 0.50)
        self.global_motion_weight = cfg.get("global_motion_weight", 0.30)
        self.proximity_weight = cfg.get("proximity_weight", 0.20)
        self._initialized = True
        self.logger.info(
            f"Violence candidate lane ready "
            f"(motion_threshold={self.motion_threshold}, "
            f"proximity={self.proximity_thresh_px}px, min_persons={self.min_persons})"
        )

    # ------------------------------------------------------------------
    def _get_person_bboxes(self) -> List[List[float]]:
        """Get current person bboxes from detection cache or pose cache."""
        persons = []

        # Try pose cache first (more reliable)
        if self._pose_cache is not None:
            pose_data = self._pose_cache.get(self.camera_id, max_age_s=2.0)
            if pose_data:
                for p in pose_data:
                    if "bbox" in p:
                        persons.append(p["bbox"])
                if persons:
                    return persons

        # Fallback: detection cache
        if self._detection_cache is not None:
            cached = self._detection_cache.get_latest(self.camera_id)
            if cached:
                for det in cached.get("detections", []):
                    label = (det.get("label") or "").lower()
                    if label == "person" and "bbox" in det:
                        persons.append(det["bbox"])

        return persons

    # ------------------------------------------------------------------
    def _find_proximity_groups(
        self, bboxes: List[List[float]],
    ) -> List[List[int]]:
        """
        Find groups of ≥min_persons persons within proximity_thresh_px.
        Returns list of groups (each group = list of indices into bboxes).
        """
        n = len(bboxes)
        if n < self.min_persons:
            return []

        # Simple greedy: build adjacency then find connected components
        adj = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = _bbox_distance(bboxes[i], bboxes[j])
                if d <= self.proximity_thresh_px:
                    adj[i].append(j)
                    adj[j].append(i)

        visited = [False] * n
        groups = []
        for start in range(n):
            if visited[start]:
                continue
            # BFS
            component = []
            stack = [start]
            while stack:
                node = stack.pop()
                if visited[node]:
                    continue
                visited[node] = True
                component.append(node)
                for nb in adj[node]:
                    if not visited[nb]:
                        stack.append(nb)
            if len(component) >= self.min_persons:
                groups.append(component)

        return groups

    # ------------------------------------------------------------------
    def _compute_local_motion(
        self,
        gray: np.ndarray,
        bboxes: List[List[float]],
        group_indices: List[int],
    ) -> float:
        """Compute average motion energy within the expanded bboxes of a group."""
        if self._prev_gray is None:
            return 0.0

        h, w = gray.shape[:2]
        energies = []
        for idx in group_indices:
            bbox = _expand_bbox(bboxes[idx], self.bbox_expand_margin, h, w)
            x1, y1, x2, y2 = bbox
            if x2 <= x1 or y2 <= y1:
                continue
            local_prev = self._prev_gray[y1:y2, x1:x2]
            local_curr = gray[y1:y2, x1:x2]
            if local_prev.shape != local_curr.shape:
                continue
            diff = cv2.absdiff(local_prev, local_curr)
            energy = float(np.mean(diff)) / 80.0
            energies.append(min(energy, 1.0))

        return float(np.mean(energies)) if energies else 0.0

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        if not self._initialized:
            self.init()

        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]

        # ── Global motion energy ──────────────────────────────────────
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        global_motion = 0.0
        dense_ratio = 0.0
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            global_motion = min(float(np.mean(diff)) / 80.0, 1.0)
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
            dense_ratio = float(np.sum(thresh > 0)) / max(thresh.size, 1)

        # ── Person proximity analysis ─────────────────────────────────
        person_bboxes = self._get_person_bboxes()
        groups = self._find_proximity_groups(person_bboxes)
        multi_person_proximity = len(groups) > 0
        num_interacting_persons = sum(len(g) for g in groups)

        # ── Local motion around interacting persons ───────────────────
        local_motion = 0.0
        group_bboxes = []
        if groups:
            best_group = max(groups, key=len)
            local_motion = self._compute_local_motion(gray, person_bboxes, best_group)

            # Merge bboxes of the best interacting group
            for idx in best_group:
                group_bboxes.append(person_bboxes[idx])

        # Store person clip ROI for temporal verifier
        if group_bboxes:
            merged = self._merge_bboxes(group_bboxes, h, w)
            roi = frame_bgr[merged[1]:merged[3], merged[0]:merged[2]]
            if roi.size > 0:
                self._person_clip_buffer.append(roi.copy())

        # ── Score: proximity + local motion + global motion ───────────
        prox_score = 1.0 if multi_person_proximity else 0.0
        score = (
            self.local_motion_weight * local_motion
            + self.global_motion_weight * global_motion
            + self.proximity_weight * prox_score
        )
        score = min(score, 1.0)

        # Spike detection
        self._energy_history.append(score)
        avg = np.mean(list(self._energy_history)[:-1]) if len(self._energy_history) > 1 else 0
        spike = score - avg

        # Boost score if strong spike + proximity
        if spike > 0.15 and multi_person_proximity:
            score = min(score + 0.15, 1.0)

        trigger = score >= self.motion_threshold and multi_person_proximity

        self._prev_gray = gray
        dt = time.perf_counter() - t0

        # Build reason codes
        reason_codes = []
        if multi_person_proximity:
            reason_codes.append(f"proximity_group ({num_interacting_persons} persons)")
        if local_motion > 0.3:
            reason_codes.append(f"high_local_motion ({local_motion:.2f})")
        if global_motion > 0.5:
            reason_codes.append(f"high_global_motion ({global_motion:.2f})")

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=round(score, 3),
            trigger=trigger,
            label="violence_candidate" if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "global_motion": round(global_motion, 3),
                "local_motion": round(local_motion, 3),
                "dense_motion_ratio": round(dense_ratio, 4),
                "multi_person_proximity": multi_person_proximity,
                "num_persons_detected": len(person_bboxes),
                "num_interacting_persons": num_interacting_persons,
                "proximity_groups": len(groups),
                "spike": round(spike, 3),
                "threshold": self.motion_threshold,
                "reason_codes": reason_codes,
                "high_local_motion": local_motion > 0.3,
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _merge_bboxes(bboxes: List[List[float]], h: int, w: int) -> List[int]:
        """Merge a list of bboxes into one enclosing bbox."""
        x1 = max(0, int(min(b[0] for b in bboxes)))
        y1 = max(0, int(min(b[1] for b in bboxes)))
        x2 = min(w, int(max(b[2] for b in bboxes)))
        y2 = min(h, int(max(b[3] for b in bboxes)))
        return [x1, y1, x2, y2]

    # ------------------------------------------------------------------
    def get_person_clip_frames(self, max_frames: int = 16) -> List[np.ndarray]:
        """Return person-centric clip frames for temporal verifier."""
        frames = list(self._person_clip_buffer)
        if len(frames) > max_frames:
            step = len(frames) / max_frames
            indices = [int(i * step) for i in range(max_frames)]
            frames = [frames[i] for i in indices]
        return frames
