"""
YOLOv8 Pose estimation lane — provides per-person keypoints.

Uses the Ultralytics YOLO pose model (default yolov8n-pose.pt).
Outputs per person: bbox, keypoints (x,y,conf) for 17 COCO points,
and an average pose confidence.

Results are written to a shared *pose cache* so that other lanes
(e.g. fall_candidate) can consume pose data without a redundant
forward pass.

COCO keypoint order (17 points):
  0  nose              9  left_wrist
  1  left_eye         10  right_hip
  2  right_eye        11  left_hip  (NOTE: left/right are subject's)
  3  left_ear         12  right_knee
  4  right_ear        13  left_knee
  5  left_shoulder    14  right_ankle
  6  right_shoulder   15  left_ankle
  7  left_elbow       16  (unused in COCO-pose, sometimes mapped)
  8  right_elbow

Runs at configurable rate (default = detector Hz, ~5 Hz).
"""
import time
import threading
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device


class PoseCache:
    """Thread-safe latest-pose store shared between lanes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}   # camera_id → latest pose result
        self._ts: Dict[str, float] = {}   # camera_id → monotonic timestamp

    def put(self, camera_id: str, persons: List[Dict[str, Any]], ts_utc: str):
        with self._lock:
            self._data[camera_id] = {"persons": persons, "ts_utc": ts_utc}
            self._ts[camera_id] = time.monotonic()

    def get(self, camera_id: str, max_age_s: float = 1.0) -> Optional[List[Dict[str, Any]]]:
        """Return latest persons list or None if stale / missing."""
        with self._lock:
            if camera_id not in self._data:
                return None
            age = time.monotonic() - self._ts.get(camera_id, 0)
            if age > max_age_s:
                return None
            return self._data[camera_id]["persons"]


class YOLOv8PoseLane(BaseLane):
    """YOLOv8 pose estimation lane."""

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.logger = setup_logger(f"YOLOv8Pose-{camera_id}")
        self.model = None
        self._pose_cache: Optional[PoseCache] = None
        self._ul_device = "cpu"

        cfg = models_cfg.get("models", {}).get("yolo_pose", {})
        self.conf = cfg.get("conf", 0.25)
        self.kpt_conf = cfg.get("kpt_conf", 0.30)

    # ------------------------------------------------------------------
    def set_pose_cache(self, cache: PoseCache):
        self._pose_cache = cache

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("yolo_pose", {})
        if not cfg.get("enabled", True):
            self._initialized = True
            self._active = False
            self.logger.info("YOLOv8 Pose lane disabled in config")
            return

        weights = cfg.get("weights", "yolov8n-pose.pt")
        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        # Auto-download if missing (Ultralytics handles this)
        from ultralytics import YOLO
        self.model = YOLO(weights)

        dev = select_device(self.models_cfg)
        actual_device = dev.torch_device
        self._ul_device = 0 if dev.torch_gpu else "cpu"
        self.model.to(actual_device)

        self._initialized = True
        self._active = True
        self.logger.info(f"YOLOv8 Pose lane ready on {actual_device} "
                         f"(weights={Path(weights).name})")

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        if not self._initialized:
            self.init()

        if self.model is None:
            return Observation(
                ts_utc=ts_utc, camera_id=self.camera_id, lane=self.lane_name,
                score=0.0, trigger=False, label=None,
                debug={"disabled": True},
            )

        t0 = time.perf_counter()
        results = self.model(
            frame_bgr, verbose=False, conf=self.conf,
            device=self._ul_device,
            half=isinstance(self._ul_device, int),
        )
        dt = time.perf_counter() - t0

        persons: List[Dict[str, Any]] = []

        if results and results[0].keypoints is not None:
            kpts_all = results[0].keypoints          # Keypoints object
            boxes = results[0].boxes

            # kpts_all.data shape: (N, 17, 3)  — x, y, conf per keypoint
            kpts_data = kpts_all.data.cpu().numpy()  # (N, 17, 3)
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()

            for i in range(len(kpts_data)):
                kp = kpts_data[i]                     # (17, 3)
                # Filter low-conf keypoints
                visible_mask = kp[:, 2] >= self.kpt_conf
                pose_conf = float(kp[visible_mask, 2].mean()) if visible_mask.any() else 0.0
                bbox = [int(c) for c in xyxy[i].tolist()]
                persons.append({
                    "bbox": bbox,
                    "keypoints": kp.tolist(),          # list of [x, y, conf]
                    "pose_conf": round(pose_conf, 3),
                    "det_conf": float(confs[i]),
                    "visible_kpts": int(visible_mask.sum()),
                })

        # Publish to shared cache
        if self._pose_cache is not None:
            self._pose_cache.put(self.camera_id, persons, ts_utc)

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=0.0,
            trigger=False,          # Pose lane never triggers alerts itself
            label=None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "num_persons": len(persons),
                "persons": persons,  # full pose data for debugging
            },
        )
