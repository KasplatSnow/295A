"""
Simple IoU-based tracker for person detections.

Optimised: vectorised numpy IoU matrix + greedy assignment (avoids O(N·M) Python loops).
"""
import numpy as np
from typing import List, Tuple, Optional


def compute_iou(box1: List[float], box2: List[float]) -> float:
    """
    Compute IoU between two boxes [x1, y1, x2, y2]
    """
    x1_min, y1_min, x1_max, y1_max = box1
    x2_min, y2_min, x2_max, y2_max = box2
    
    # Intersection
    inter_x_min = max(x1_min, x2_min)
    inter_y_min = max(y1_min, y2_min)
    inter_x_max = min(x1_max, x2_max)
    inter_y_max = min(y1_max, y2_max)
    
    inter_width = max(0, inter_x_max - inter_x_min)
    inter_height = max(0, inter_y_max - inter_y_min)
    inter_area = inter_width * inter_height
    
    # Union
    box1_area = (x1_max - x1_min) * (y1_max - y1_min)
    box2_area = (x2_max - x2_min) * (y2_max - y2_min)
    union_area = box1_area + box2_area - inter_area
    
    if union_area == 0:
        return 0.0
    
    return inter_area / union_area


def _iou_matrix(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    """
    Vectorised IoU computation between two sets of boxes.

    Args:
        boxes_a: (N, 4) array of [x1, y1, x2, y2]
        boxes_b: (M, 4) array of [x1, y1, x2, y2]

    Returns:
        (N, M) IoU matrix
    """
    # Broadcast intersection
    x1 = np.maximum(boxes_a[:, 0:1], boxes_b[:, 0])   # (N, M)
    y1 = np.maximum(boxes_a[:, 1:2], boxes_b[:, 1])
    x2 = np.minimum(boxes_a[:, 2:3], boxes_b[:, 2])
    y2 = np.minimum(boxes_a[:, 3:4], boxes_b[:, 3])

    inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)   # (N, M)

    area_a = (boxes_a[:, 2] - boxes_a[:, 0]) * (boxes_a[:, 3] - boxes_a[:, 1])  # (N,)
    area_b = (boxes_b[:, 2] - boxes_b[:, 0]) * (boxes_b[:, 3] - boxes_b[:, 1])  # (M,)

    union = area_a[:, None] + area_b[None, :] - inter
    union = np.maximum(union, 1e-6)
    return inter / union


class IOUTracker:
    """IoU-based tracker with vectorised matching."""
    
    def __init__(self, iou_threshold: float = 0.3, max_age: int = 30):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.tracks = {}  # track_id -> {"box": [x1,y1,x2,y2], "age": int}
        self.next_id = 1
    
    def update(self, detections: List[Tuple[List[float], float]]) -> List[Tuple[List[float], float, int]]:
        """
        Update tracker with new detections.

        Uses vectorised IoU matrix for O(1)-per-pair matching instead of
        nested Python loops.

        Args:
            detections: List of (box, confidence) where box is [x1, y1, x2, y2]
        Returns:
            List of (box, confidence, track_id)
        """
        # Age existing tracks and remove stale ones
        for track_id in list(self.tracks.keys()):
            self.tracks[track_id]["age"] += 1
            if self.tracks[track_id]["age"] > self.max_age:
                del self.tracks[track_id]
        
        if not detections:
            return []

        # Fast path: no existing tracks → assign all as new
        if not self.tracks:
            results = []
            for box, conf in detections:
                new_id = self.next_id
                self.next_id += 1
                self.tracks[new_id] = {"box": box, "age": 0}
                results.append((box, conf, new_id))
            return results
        
        # Build arrays for vectorised IoU
        det_boxes = np.array([box for box, _ in detections], dtype=np.float64)    # (D, 4)
        track_ids = list(self.tracks.keys())
        track_boxes = np.array([self.tracks[tid]["box"] for tid in track_ids],
                               dtype=np.float64)                                  # (T, 4)

        iou_mat = _iou_matrix(det_boxes, track_boxes)  # (D, T)

        # Greedy assignment: for each detection pick the best unmatched track
        matched_tracks = set()
        results = []

        # Process detections in order of best available IoU (descending)
        for det_idx in range(len(detections)):
            box, conf = detections[det_idx]
            row = iou_mat[det_idx]

            # Mask already-matched tracks
            for mj in matched_tracks:
                row[mj] = -1.0

            best_j = int(row.argmax())
            best_iou = row[best_j]

            if best_iou >= self.iou_threshold:
                tid = track_ids[best_j]
                self.tracks[tid]["box"] = box
                self.tracks[tid]["age"] = 0
                matched_tracks.add(best_j)
                results.append((box, conf, tid))
            else:
                new_id = self.next_id
                self.next_id += 1
                self.tracks[new_id] = {"box": box, "age": 0}
                results.append((box, conf, new_id))
        
        return results
