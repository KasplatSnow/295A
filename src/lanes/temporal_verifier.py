"""
Temporal verifier lane – X3D-S via PyTorchVideo TorchHub (or local checkpoint).

Accepts a short clip (from ring buffer) and outputs a confirm boolean + confidence
for violence / fall.  Only invoked when a candidate event needs temporal confirmation.

IMPORTANT: This lane is ON-DEMAND ONLY.
  Do NOT run temporal verifier continuously.
  Run only when candidate event appears (via verify_clip()).
  The infer() method is a no-op that returns trigger=False.

Loading strategies (config ``source``):
  • ``torchhub`` (default) — ``torch.hub.load("facebookresearch/pytorchvideo", "x3d_s", pretrained=True)``
    No local ``.pth`` file required.  Model weights are downloaded and cached automatically.
  • ``local`` — ``torch.load(model_path)`` from ``models/x3d_s.pth``.

Day-1 fallback: optical-flow + motion-energy classifier (stub).
"""
import time
import numpy as np
import cv2
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger


# Kinetics-400 action labels used by X3D-S pretrained on Kinetics
_KINETICS_VIOLENCE_LABELS = {
    "punching person (boxing)", "wrestling", "slapping",
    "headbutting", "kicking person", "pushing",
    "drop kicking", "side kick", "front raises",
}
_KINETICS_FALL_LABELS = {
    "falling off chair", "faceplanting", "tripping",
}


class TemporalVerifierLane(BaseLane):
    """
    Temporal verifier — ON-DEMAND ONLY.
    The process loop should skip this lane; call verify_clip() directly
    from the aggregator when a candidate violence/fall event appears.
    """

    # Flag for orchestrator to skip in main loop
    on_demand = True

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.conf_threshold = 0.5
        self.logger = setup_logger(f"TemporalVerifier-{camera_id}")
        self._stub = True
        self._model = None
        self._torch_device = "cpu"
        self._prev_gray = None

    # ------------------------------------------------------------------
    def init(self):
        cfg = self.models_cfg.get("models", {}).get("temporal_verifier", {})
        self.conf_threshold = cfg.get("conf", 0.5)
        kind = cfg.get("kind", "x3d")
        source = cfg.get("source", "torchhub")

        # Determine torch device
        try:
            from ..runtime.device import select_device
            dev = select_device(self.models_cfg)
            self._torch_device = dev.torch_device
        except Exception:
            self._torch_device = "cpu"

        loaded = False

        # Strategy 1: TorchHub (no local file needed)
        if source == "torchhub":
            loaded = self._load_torchhub(cfg)

        # Strategy 2: Local file
        if not loaded and source == "local":
            loaded = self._load_local(cfg)

        # Fallback: try local file even if source==torchhub, it might be there
        if not loaded and source != "local":
            loaded = self._load_local(cfg)

        if loaded:
            self._stub = False
        else:
            if source == "torchhub":
                self.logger.warning(
                    "TorchHub load failed and no local checkpoint — using motion-energy stub. "
                    "Install pytorchvideo: pip install pytorchvideo"
                )
            else:
                model_path = cfg.get("model_path", "models/x3d_s.pth")
                self.logger.warning(
                    f"Temporal verifier model not found ({model_path}), using motion-energy stub"
                )
            self._stub = True

        self._initialized = True
        self.logger.info(f"Temporal verifier ready (stub={self._stub})")

        # §C5 warmup: run a synthetic clip through the model so diagnostics
        # show real values immediately and any kernel/shape errors surface now.
        self._warmup()

    # ------------------------------------------------------------------
    def _warmup(self):
        """Run a synthetic 16-frame clip through the model to validate the
        forward pass and populate ``_last_run_stats`` for diagnostics."""
        try:
            # 16 black 224×224 BGR frames — minimal memory, deterministic
            dummy_frames = [np.zeros((224, 224, 3), dtype=np.uint8) for _ in range(16)]
            result = self.verify_clip(dummy_frames, target_label="violence", fps=10.0)
            self.logger.info(
                f"Warmup verify_clip OK — confirmed={result['confirmed']}, "
                f"score={result['score']:.3f}, "
                f"input_shape={result.get('debug', {}).get('input_shape', '?')}"
            )
        except Exception as e:
            self.logger.warning(f"Warmup verify_clip failed: {e}")

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Whether the temporal verifier is usable (model loaded or stub active)."""
        return self._initialized

    @property
    def reason_unavailable(self) -> Optional[str]:
        """Reason string if not available, else None."""
        if not self._initialized:
            return "init() not yet called"
        return None

    @property
    def is_stub(self) -> bool:
        """Whether the verifier is using the motion-energy stub."""
        return self._stub

    # ------------------------------------------------------------------
    def _load_torchhub(self, cfg: Dict[str, Any]) -> bool:
        """Load X3D-S (or compatible) model from PyTorchVideo TorchHub."""
        hub_repo = cfg.get("hub_repo", "facebookresearch/pytorchvideo")
        hub_model = cfg.get("hub_model", "x3d_s")
        pretrained = cfg.get("pretrained", True)
        try:
            import torch
            self.logger.info(f"Loading temporal verifier from TorchHub: {hub_repo}/{hub_model}")
            model = torch.hub.load(
                hub_repo, hub_model,
                pretrained=pretrained,
            )
            model = model.eval()
            model = model.to(self._torch_device)
            self._model = model
            self.logger.info(
                f"Temporal verifier ({hub_model}) loaded via TorchHub on {self._torch_device}"
            )
            return True
        except Exception as e:
            self.logger.warning(f"TorchHub load failed ({hub_repo}/{hub_model}): {e}")
            return False

    # ------------------------------------------------------------------
    def _load_local(self, cfg: Dict[str, Any]) -> bool:
        """Load model from a local .pth checkpoint."""
        model_path = cfg.get("model_path", "models/x3d_s.pth")
        if not Path(model_path).is_absolute():
            model_path = str((Path(__file__).parent.parent.parent / model_path).resolve())

        if not Path(model_path).exists():
            return False

        try:
            import torch
            self._model = torch.load(model_path, map_location=self._torch_device)
            if hasattr(self._model, "eval"):
                self._model.eval()
            self.logger.info(f"Temporal verifier loaded from local: {model_path}")
            return True
        except Exception as e:
            self.logger.warning(f"Local model load failed ({model_path}): {e}")
            return False

    # ------------------------------------------------------------------
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        """
        NO-OP: Temporal verifier must NOT run continuously.
        This method exists only to satisfy the BaseLane interface.
        The orchestrator should skip this lane in the main loop
        (check lane.on_demand == True).
        """
        if not self._initialized:
            self.init()

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=0.0,
            trigger=False,
            label=None,
            debug={"on_demand": True, "skipped": True},
        )

    # ------------------------------------------------------------------
    # On-demand clip verification
    # ------------------------------------------------------------------
    def verify_clip(self, frames_bgr: List[np.ndarray],
                    target_label: str = "violence",
                    fps: float = 10.0) -> Dict[str, Any]:
        """
        §B1: Run temporal verification on RAW FRAMES ONLY.

        Args:
            frames_bgr: List of BGR np.ndarray images (raw, not JPEG bytes).
            target_label: What to verify (``violence`` / ``fall`` / ``fire``).
            fps: Source FPS for informational purposes.

        Returns:
            {"confirmed": bool, "score": float, "debug": {...}}
        """
        if not self._initialized:
            self.init()

        if not frames_bgr:
            return {"confirmed": False, "score": 0.0,
                    "debug": {"input_shape": None, "clip_len": 0, "used_padding": False}}

        t0 = time.perf_counter()

        if self._stub:
            result = self._stub_raw_verify(frames_bgr)
        else:
            result = self._model_raw_verify(frames_bgr, target_label)

        dt = time.perf_counter() - t0
        clip_len = result.get("debug", {}).get("clip_len", len(frames_bgr))
        self.logger.debug(
            f"Clip verify ({clip_len} frames): confirmed={result['confirmed']}, "
            f"score={result['score']:.2f}, {dt*1000:.1f} ms"
        )

        # §C5: Store last run stats for diagnostics
        self._last_run_stats = {
            "last_input_shape": result.get("debug", {}).get("input_shape"),
            "padding_applied": result.get("debug", {}).get("used_padding", False),
            "device": self._torch_device,
            "last_run_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_run_latency_ms": round(dt * 1000, 1),
            "last_score": result.get("score", 0.0),
            "stub": self._stub,
        }

        return result

    def get_last_run_stats(self) -> Dict[str, Any]:
        """§C5: Return last temporal verifier run stats for diagnostics."""
        stats = getattr(self, "_last_run_stats", {})
        # Always include base info even before first run
        stats.setdefault("stub", self._stub)
        stats.setdefault("device", self._torch_device)
        stats.setdefault("available", self.available)
        stats.setdefault("reason_unavailable", self.reason_unavailable)
        return stats

    # --- helpers -------------------------------------------------------
    def _motion_energy(self, frame_bgr: np.ndarray) -> float:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        score = 0.0
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            score = float(np.mean(diff)) / 80.0
            score = min(score, 1.0)
        self._prev_gray = gray
        return score

    def _stub_raw_verify(self, frames_bgr: List[np.ndarray]) -> Dict[str, Any]:
        """Motion-based clip verification stub using raw BGR frames."""
        energies = []
        prev_gray = None
        for frame in frames_bgr:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)
            if prev_gray is not None:
                diff = cv2.absdiff(prev_gray, gray)
                energies.append(float(np.mean(diff)) / 80.0)
            prev_gray = gray

        if not energies:
            return {"confirmed": False, "score": 0.0,
                    "debug": {"input_shape": None, "clip_len": len(frames_bgr), "used_padding": False}}

        avg = float(np.mean(energies))
        return {
            "confirmed": avg > self.conf_threshold,
            "score": round(min(avg, 1.0), 3),
            "debug": {
                "input_shape": f"stub({len(frames_bgr)} frames)",
                "clip_len": len(frames_bgr),
                "used_padding": False,
            },
        }

    def _pad_and_preprocess_clip(self, frames_bgr: List[np.ndarray],
                                  clip_len: int = 16) -> Optional[Any]:
        """
        §B2/B3: Preprocess raw BGR frames into X3D-compatible tensor.

        Steps:
        1. Pad by repeating last frame if < clip_len
        2. Resize to 224x224
        3. BGR→RGB, float32 [0,1], normalize (Kinetics mean/std)
        4. Stack → tensor shape (1, 3, T, 224, 224)

        Returns torch.Tensor or None on failure.
        """
        import torch

        # §B2: Pad by repeating last frame until clip_len
        used_padding = len(frames_bgr) < clip_len
        padded = list(frames_bgr)
        while len(padded) < clip_len:
            padded.append(padded[-1].copy())
        # Take exactly clip_len frames
        padded = padded[:clip_len]

        imgs = []
        for frame in padded:
            # Resize to 256x256 then centre-crop 224x224
            resized = cv2.resize(frame, (256, 256))
            y0, x0 = (256 - 224) // 2, (256 - 224) // 2
            cropped = resized[y0:y0+224, x0:x0+224]
            # BGR→RGB, float32 [0,1]
            rgb = cropped[:, :, ::-1].astype(np.float32) / 255.0
            # Normalize (Kinetics-400 standard values)
            mean = np.array([0.45, 0.45, 0.45], dtype=np.float32)
            std = np.array([0.225, 0.225, 0.225], dtype=np.float32)
            rgb = (rgb - mean) / std
            imgs.append(rgb)

        # [T, H, W, C] → [C, T, H, W] → [1, C, T, H, W]
        clip = np.stack(imgs, axis=0)      # [T, H, W, C]
        clip = clip.transpose(3, 0, 1, 2)  # [C, T, H, W]
        clip_tensor = torch.from_numpy(clip).unsqueeze(0).to(self._torch_device)

        # §B3: Log tensor shape
        self.logger.info(
            f"TemporalVerifier input tensor: {tuple(clip_tensor.shape)} device={self._torch_device}"
        )

        return clip_tensor, used_padding

    def _model_raw_verify(self, frames_bgr: List[np.ndarray],
                           target_label: str) -> Dict[str, Any]:
        """
        §B1-B5: Run X3D-S on raw BGR frames with proper preprocessing.
        Never falls back due to clip being too small (we pad instead).
        Only falls back if model forward pass or preprocessing fails.
        """
        import torch

        target_labels = (
            _KINETICS_VIOLENCE_LABELS if target_label == "violence"
            else _KINETICS_FALL_LABELS
        )

        try:
            # §B2/B3: Preprocess with padding
            result = self._pad_and_preprocess_clip(frames_bgr, clip_len=16)
            if result is None:
                raise RuntimeError("Preprocessing returned None")
            clip_tensor, used_padding = result

            input_shape = str(tuple(clip_tensor.shape))

            with torch.no_grad():
                logits = self._model(clip_tensor)
                probs = torch.softmax(logits, dim=1).squeeze(0).cpu().numpy()

            # Try to load Kinetics-400 label list
            try:
                from pytorchvideo.data.kinetics import KINETICS_400_LABELS  # type: ignore
                labels = KINETICS_400_LABELS
            except ImportError:
                # Without labels, use argmax + confidence as proxy
                max_prob = float(np.max(probs))
                return {
                    "confirmed": max_prob > self.conf_threshold,
                    "score": round(min(max_prob, 1.0), 3),
                    "debug": {
                        "input_shape": input_shape,
                        "clip_len": 16,
                        "used_padding": used_padding,
                        "note": "kinetics_labels_unavailable",
                    },
                }

            # Sum probabilities for target labels
            target_score = 0.0
            for idx, lbl in enumerate(labels):
                if lbl.lower() in {t.lower() for t in target_labels}:
                    target_score += float(probs[idx])

            confirmed = target_score > self.conf_threshold
            return {
                "confirmed": confirmed,
                "score": round(min(target_score, 1.0), 3),
                "debug": {
                    "input_shape": input_shape,
                    "clip_len": 16,
                    "used_padding": used_padding,
                },
            }

        except Exception as e:
            # §B5: Only fall back to stub if model forward pass or preprocessing fails
            self.logger.warning(f"X3D-S forward pass failed ({e}), falling back to stub")
            return self._stub_raw_verify(frames_bgr)
