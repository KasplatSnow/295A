"""
Fire / smoke detection lane – dedicated YOLO model.

Uses the ``fire_smoke`` key in ``models.yaml``.
Community pre-trained weights (no custom training needed).

Spec §5 safety rules:
  • If dedicated fire weights are missing → lane is DISABLED (no fallback).
  • Deterministic weights:
      Option A: luminous0219 fire-and-smoke-detection-yolov8 weights/best.pt
      Option B: HF repo SHOU-ISD/fire-and-smoke
  • Class filter is mandatory: only detections whose class name is in
    ``class_names`` (default {"fire","smoke"}) pass.
  • Quality gates: min_area_px, min_area_ratio, bbox aspect ratio, max_boxes.
  • Persistence tracking 4/8 in debug for aggregator two-stage confirm.
  • Flicker detection: fire bbox must persist at roughly the same location
    across multiple frames (not just single-frame noise).
"""
import time
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from collections import deque

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device
from ..logic.class_filter import resolve_class_filter


class _FlickerTracker:
    """
    Track fire/smoke bbox persistence across frames.
    A detection is considered non-flickering if a bbox reappears near
    the same location for ≥ min_frames out of a sliding window.
    """

    def __init__(self, window: int = 8, min_hits: int = 4,
                 iou_threshold: float = 0.25):
        self.window = window
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self._history: deque = deque(maxlen=window)

    @staticmethod
    def _iou(a: List[int], b: List[int]) -> float:
        x1 = max(a[0], b[0])
        y1 = max(a[1], b[1])
        x2 = min(a[2], b[2])
        y2 = min(a[3], b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
        area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        return inter / (area_a + area_b - inter + 1e-6)

    def update(self, kept_bboxes: List[List[int]]) -> Tuple[bool, int]:
        """
        Update with current frame's kept bboxes.
        Returns (is_persistent, hit_count).
        """
        self._history.append(kept_bboxes)
        if not kept_bboxes or len(self._history) < 2:
            return False, 0

        # Check how many past frames have a bbox overlapping with current best
        ref = kept_bboxes[0]  # highest-confidence detection
        hits = 0
        for past_bboxes in list(self._history)[:-1]:
            for pb in past_bboxes:
                if self._iou(ref, pb) >= self.iou_threshold:
                    hits += 1
                    break
        # Current frame also counts
        hits += 1
        return hits >= self.min_hits, hits


class FireSmokeYOLOLane(BaseLane):
    """Dedicated fire / smoke detection using a separate YOLO model."""

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.model = None
        self.conf_threshold = 0.30
        self.logger = setup_logger(f"FireSmokeYOLO-{camera_id}")

        # --- quality-gate defaults (overridden from config) ---
        self.class_names: Set[str] = {"fire", "smoke"}
        self.class_ids: Optional[Set[int]] = None
        self.min_area_px: int = 800
        self.min_area_ratio: float = 0.001
        self.min_persistence_hits: int = 4   # §5: 4/8
        self.max_boxes_considered: int = 3
        self.max_aspect_ratio: float = 8.0   # reject extreme aspect ratios
        self.min_aspect_ratio: float = 0.125

        # Rolling hit counter for persistence tracking (spec §5: 4/8)
        self._recent_trigger_flags: List[bool] = []
        self._persistence_window: int = 8  # §5: 4-of-8

        # Flicker tracker — detect spatially persistent fire bboxes
        self._flicker_tracker = _FlickerTracker(window=8, min_hits=4,
                                                 iou_threshold=0.25)

        # Lane can be disabled if weights are missing
        self._active = False

        # Resolved class-id filter (populated after model load)
        self._resolved_class_ids: Optional[Set[int]] = None
        # Full model.names mapping (exposed for diagnostics)
        self.model_names: Dict[int, str] = {}

        # Per-frame debug log for FP debugging panel
        self.last_debug_detections: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("fire_smoke", {})
        weights = cfg.get("weights", "models/fire_yolov8.pt")
        self.conf_threshold = cfg.get("conf", 0.30)

        # Config overrides
        self.class_names = set(cfg.get("class_names", ["fire", "smoke"]))
        self.class_ids = set(cfg["class_ids"]) if "class_ids" in cfg else None
        self.min_area_px = cfg.get("min_area_px", 800)
        self.min_area_ratio = cfg.get("min_area_ratio", 0.001)
        self.min_persistence_hits = cfg.get("min_persistence_hits", 4)   # §5
        self.max_boxes_considered = cfg.get("max_boxes_considered", 3)
        self.max_aspect_ratio = cfg.get("max_aspect_ratio", 8.0)
        self.min_aspect_ratio = cfg.get("min_aspect_ratio", 0.125)
        self._persistence_window = cfg.get("persistence_window", 8)      # §5
        flicker_min = cfg.get("flicker_min_hits", 4)
        self._flicker_tracker = _FlickerTracker(
            window=self._persistence_window, min_hits=flicker_min,
        )

        # Weights source: support git-clone luminous0219 repo or HF
        weights_source = cfg.get("weights_source", "")  # "luminous0219" or "hf" or ""

        # Resolve path
        if not Path(weights).is_absolute():
            weights = str((Path(__file__).parent.parent.parent / weights).resolve())

        if not Path(weights).exists():
            # Try to auto-download from specified source
            downloaded = self._try_download_weights(cfg, weights)
            if not downloaded:
                self.logger.warning(
                    f"Fire/smoke weights not found ({weights}). "
                    f"Lane DISABLED — no fire alerts will be produced."
                )
                self._active = False
                self._initialized = True
                return
            weights = downloaded

        from ultralytics import YOLO
        self.logger.info(f"Loading fire/smoke model from {weights}")
        self.model = YOLO(weights)

        # Resolve class filter using model's actual class names
        cfg_class_names = list(self.class_names) if self.class_names else None
        cfg_class_ids = list(self.class_ids) if self.class_ids else None
        resolved_ids, self.model_names = resolve_class_filter(
            self.model,
            class_names=cfg_class_names,
            class_ids=cfg_class_ids,
            lane_name="fire_smoke",
            logger=self.logger,
        )
        if resolved_ids is None and cfg_class_names:
            self.logger.error(
                "Fire/smoke lane DISABLED — class mapping failed. "
                f"Set models.fire_smoke.class_ids using model.names: {self.model_names}"
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
            f"Fire/smoke inference device: {actual_device}, "
            f"model.names: {self.model_names}, "
            f"persistence={self.min_persistence_hits}/{self._persistence_window}"
        )

    # ------------------------------------------------------------------
    def _try_download_weights(self, cfg: dict, target_path: str) -> Optional[str]:
        """Try to download weights from HF or luminous0219 git repo."""
        try:
            source = cfg.get("weights_source", "hf")
            if source == "luminous0219":
                # Clone luminous0219/fire-and-smoke-detection-yolov8 repo
                import subprocess
                repo_url = "https://github.com/luminous0219/fire-and-smoke-detection-yolov8.git"
                clone_dir = Path(__file__).parent.parent.parent / "third_party" / "fire-smoke-yolo"
                if not clone_dir.exists():
                    self.logger.info(f"Cloning fire/smoke weights from {repo_url}")
                    subprocess.run(
                        ["git", "clone", "--depth", "1", repo_url, str(clone_dir)],
                        check=True, capture_output=True, timeout=120,
                    )
                best_pt = clone_dir / "weights" / "best.pt"
                if best_pt.exists():
                    import shutil
                    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(best_pt, target_path)
                    self.logger.info(f"Copied fire/smoke weights to {target_path}")
                    return target_path
            else:
                # HF download (existing behavior)
                hf_repo = cfg.get("hf_repo_id", "")
                hf_file = cfg.get("hf_filename", "")
                if hf_repo and hf_file:
                    try:
                        from huggingface_hub import hf_hub_download
                        downloaded = hf_hub_download(
                            repo_id=hf_repo, filename=hf_file,
                        )
                        import shutil
                        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(downloaded, target_path)
                        self.logger.info(f"Downloaded fire/smoke weights from HF to {target_path}")
                        return target_path
                    except Exception as e:
                        self.logger.warning(f"HF download failed: {e}")
        except Exception as e:
            self.logger.warning(f"Weights download failed: {e}")
        return None

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

        frame_h, frame_w = frame_bgr.shape[:2]
        frame_area = frame_h * frame_w

        t0 = time.perf_counter()
        results = self.model(frame_bgr, verbose=False, conf=self.conf_threshold,
                            device=self._ul_device,
                            half=isinstance(self._ul_device, int))
        dt = time.perf_counter() - t0

        # ---- Collect & filter detections ----
        all_dets: List[Dict[str, Any]] = []
        kept_dets: List[Dict[str, Any]] = []

        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            names = results[0].names
            all_xyxy = boxes.xyxy.cpu().numpy()
            all_conf_arr = boxes.conf.cpu().numpy()
            all_cls_arr = boxes.cls.cpu().numpy()
            for i in range(len(boxes)):
                conf = float(all_conf_arr[i])
                cls_id = int(all_cls_arr[i])
                cls_name = names.get(cls_id, f"class_{cls_id}").lower()
                bbox = [int(c) for c in all_xyxy[i].tolist()]
                bw = max(1, bbox[2] - bbox[0])
                bh = max(1, bbox[3] - bbox[1])
                area = bw * bh
                area_ratio = area / max(frame_area, 1)
                aspect = bw / bh

                det_info = {
                    "class": cls_name,
                    "class_id": cls_id,
                    "conf": round(conf, 3),
                    "bbox": bbox,
                    "area_px": area,
                    "area_ratio": round(area_ratio, 6),
                    "aspect_ratio": round(aspect, 2),
                    "reason": "kept",
                }

                # Filter 1: class filter
                class_ok = True
                if self._resolved_class_ids is not None:
                    class_ok = cls_id in self._resolved_class_ids
                if not class_ok:
                    det_info["reason"] = f"class_rejected ({cls_name})"
                    all_dets.append(det_info)
                    continue

                # Filter 2: min area
                if area < self.min_area_px:
                    det_info["reason"] = f"area_too_small ({area}<{self.min_area_px})"
                    all_dets.append(det_info)
                    continue

                # Filter 3: area ratio
                if area_ratio < self.min_area_ratio:
                    det_info["reason"] = f"area_ratio_too_small ({area_ratio:.5f}<{self.min_area_ratio})"
                    all_dets.append(det_info)
                    continue

                # Filter 4: aspect ratio gate (reject extreme shapes)
                if aspect > self.max_aspect_ratio or aspect < self.min_aspect_ratio:
                    det_info["reason"] = f"aspect_ratio_rejected ({aspect:.2f})"
                    all_dets.append(det_info)
                    continue

                all_dets.append(det_info)
                kept_dets.append(det_info)

        # Limit to max_boxes_considered (highest conf first)
        kept_dets.sort(key=lambda d: d["conf"], reverse=True)
        kept_dets = kept_dets[:self.max_boxes_considered]

        # --- Best detection ---
        best_score = 0.0
        best_bbox = None
        kept_bboxes = []
        for d in kept_dets:
            kept_bboxes.append(d["bbox"])
            if d["conf"] > best_score:
                best_score = d["conf"]
                best_bbox = d["bbox"]

        trigger = best_score > self.conf_threshold and len(kept_dets) > 0

        # --- Persistence tracking (4/8) ---
        self._recent_trigger_flags.append(trigger)
        if len(self._recent_trigger_flags) > self._persistence_window:
            self._recent_trigger_flags = self._recent_trigger_flags[-self._persistence_window:]
        persistence_hits = sum(self._recent_trigger_flags)

        # --- Flicker detection (spatial persistence) ---
        flicker_persistent, flicker_hits = self._flicker_tracker.update(kept_bboxes)

        # Build reason codes
        reason_codes = []
        if trigger:
            reason_codes.append(f"detection ({best_score:.2f})")
        if persistence_hits >= self.min_persistence_hits:
            reason_codes.append(f"persistence ({persistence_hits}/{self._persistence_window})")
        if flicker_persistent:
            reason_codes.append(f"spatial_persistent ({flicker_hits})")
        else:
            reason_codes.append(f"flicker_check ({flicker_hits}/{self._flicker_tracker.min_hits})")

        # Store debug info
        self.last_debug_detections = all_dets[:10]

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=best_score,
            trigger=trigger,
            bbox=best_bbox,
            label="fire_smoke" if trigger else None,
            debug={
                "inference_ms": round(dt * 1000, 1),
                "num_raw_detections": len(all_dets),
                "num_kept": len(kept_dets),
                "persistence_hits": persistence_hits,
                "persistence_window": self._persistence_window,
                "min_persistence_required": self.min_persistence_hits,
                "flicker_persistent": flicker_persistent,
                "flicker_hits": flicker_hits,
                "reason_codes": reason_codes,
                "filters": {
                    "class_names": list(self.class_names),
                    "min_area_px": self.min_area_px,
                    "min_area_ratio": self.min_area_ratio,
                    "max_boxes": self.max_boxes_considered,
                    "aspect_ratio": [self.min_aspect_ratio, self.max_aspect_ratio],
                },
                "top_detections": all_dets[:5],
            },
        )
