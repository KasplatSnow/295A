"""
LatestFrameStore — thread-safe shared dict of latest frames per camera.

§2.2: The API server queries this store to capture fresh frames
without re-opening RTSP connections.

Usage:
    store = LatestFrameStore()
    store.update("cam1", frame_bgr, ts_utc)   # called in CameraProcessor loop
    frame, ts = store.get("cam1")              # called from API endpoints
"""
import threading
from typing import Dict, Optional, Tuple

import numpy as np


class LatestFrameStore:
    """Thread-safe store mapping camera_id → (ts_utc, frame_bgr)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frames: Dict[str, Tuple[str, np.ndarray]] = {}

    def update(self, camera_id: str, frame_bgr: np.ndarray, ts_utc: str) -> None:
        """Update the latest frame for a camera (called from processing thread).

        Stores a *view* of the caller's frame.  The copy is deferred to
        ``get()`` so the fast path (processing loop) is not penalised.
        """
        with self._lock:
            self._frames[camera_id] = (ts_utc, frame_bgr)

    def get(self, camera_id: str) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """Retrieve latest frame for a camera.  Returns (frame_bgr, ts_utc) or (None, None)."""
        with self._lock:
            entry = self._frames.get(camera_id)
            if entry is None:
                return None, None
            ts, frame = entry
            return frame.copy(), ts

    def camera_ids(self):
        """Return list of camera_ids that have frames stored."""
        with self._lock:
            return list(self._frames.keys())
