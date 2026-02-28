"""
Shared per-frame detection cache.

Eliminates redundant YOLO forward passes when multiple lanes (yolov8_fallback,
fall_candidate, entity_identity) need person detections from the same frame.

Usage:
    cache = FrameDetectionCache()
    cache.put("person_yolo", frame_seq, detections)
    hit = cache.get("person_yolo", frame_seq)   # returns detections or None
"""
import threading
from typing import Any, Dict, Optional, Tuple


class FrameDetectionCache:
    """Thread-safe, single-frame detection cache keyed by (model_key, frame_seq).

    Only the *latest* frame's results are kept per model_key — no memory growth.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # model_key → (frame_seq, detections)
        self._store: Dict[str, Tuple[int, Any]] = {}

    def put(self, model_key: str, frame_seq: int, detections: Any) -> None:
        """Store detections for the current frame."""
        with self._lock:
            self._store[model_key] = (frame_seq, detections)

    def get(self, model_key: str, frame_seq: int) -> Optional[Any]:
        """Return cached detections if they match the current frame_seq, else None."""
        with self._lock:
            entry = self._store.get(model_key)
            if entry is not None and entry[0] == frame_seq:
                return entry[1]
            return None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
