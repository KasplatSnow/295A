"""
PR-03: BEATs-based audio anomaly detection lane.

Consumes AudioChunk objects from the FFmpegAudioReader and emits
Observation objects using the existing Observation dataclass.

Design rules (plan Section 6.7):
  - Does NOT extend BaseLane (BaseLane.infer takes a video frame).
  - Exposes infer_audio(chunk) → Optional[Observation]
  - Uses separate self.audio_lane reference in app.py (not in self.lanes dict)
  - Modality field in debug → aggregator and fusion use this to discriminate
  - Fail gracefully: if model not loaded, return None (not crash)
  - FP16 only on CUDA, with safe float32 input handling
  - Score smoothing: temporal window with max-then-EMA strategy (plan §6.7)
  - Uncertainty: entropy-based (plan §6.7)
  - Does NOT modify Observation dataclass — uses trigger=True and lane="audio_anomaly"
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..common.audio_types import AudioChunk, AudioObservationDebug, AudioPrediction
from ..common.log import setup_logger
from ..common.types import Observation
from .audio_label_mapper import CANONICAL_LABELS, map_topk

# ── AI root (for model path resolution) ────────────────────────────────────
_AI_ROOT = Path(__file__).resolve().parent.parent.parent
_BEATS_SRC = _AI_ROOT / "third_party" / "beats"


class _ScoreSmoother:
    """
    Per-canonical-label temporal smoother.
    Strategy: max_then_ema (plan §6.7):
      1. Take max of last window_n raw scores.
      2. Apply EMA on top of that max.
    """

    def __init__(self, window_s: float, hop_s: float, ema_alpha: float = 0.45):
        self.window_n = max(1, int(window_s / max(hop_s, 0.1)))
        self.ema_alpha = ema_alpha
        self._history: deque = deque(maxlen=self.window_n)
        self._ema: float = 0.0

    def update(self, score: float) -> float:
        self._history.append(score)
        window_max = max(self._history)
        self._ema = self.ema_alpha * window_max + (1.0 - self.ema_alpha) * self._ema
        return self._ema

    def current(self) -> float:
        return self._ema


class AudioAnomalyLane:
    """
    BEATs-based audio anomaly detection lane.

    This is NOT a BaseLane subclass — audio inference operates on AudioChunk,
    not on video frames.  The caller (app.py audio_loop) holds a reference
    to this lane separately from self.lanes (the video lane registry).

    Public API (plan §6.7):
        name     = "audio_anomaly"
        modality = "audio"
        infer_audio(chunk) → Optional[Observation]
        health()           → dict
    """

    name = "audio_anomaly"
    modality = "audio"

    def __init__(
        self,
        camera_id: str,
        cfg: Dict[str, Any],
        models_cfg: Dict[str, Any],
        logger=None,
    ):
        """
        Args:
            camera_id:  Camera identifier.
            cfg:        models.audio_anomaly sub-dict from models.yaml.
            models_cfg: Full top-level models config (for device resolution).
            logger:     Optional pre-configured logger.
        """
        self.camera_id = camera_id
        self.cfg = cfg
        self.models_cfg = models_cfg
        self.logger = logger or setup_logger(f"AudioAnomalyLane-{camera_id}")

        # Config values
        self._model_path   = cfg.get("model_path", "")
        self._device_cfg   = cfg.get("device", "auto")
        self._fp16         = cfg.get("fp16", True)
        self._sample_rate  = cfg.get("sample_rate", 16000)
        self._top_k        = cfg.get("top_k", 10)
        self._min_raw      = cfg.get("min_raw_score", 0.15)
        self._min_canon    = cfg.get("min_canonical_score", 0.50)
        self._alert_labels: Dict[str, float] = cfg.get("alert_labels", {
            "audio_scream":       0.70,
            "audio_gunshot":      0.80,
            "audio_explosion":    0.80,
            "audio_glass_break":  0.65,
            "audio_siren":        0.75,
            "audio_alarm":        0.70,
            "audio_vehicle_crash": 0.75,
            "audio_shout":        0.75,
        })

        # Smoothing config
        smooth_cfg     = cfg.get("smoothing", {})
        self._smooth   = smooth_cfg.get("enabled", True)
        self._window_s = smooth_cfg.get("window_s", 3.0)
        self._ema_alpha= smooth_cfg.get("ema_alpha", 0.45)
        hop_s          = cfg.get("hop_s", 0.5)      # used by smoother window

        # One smoother per canonical label
        self._smoothers: Dict[str, _ScoreSmoother] = {
            label: _ScoreSmoother(self._window_s, hop_s, self._ema_alpha)
            for label in CANONICAL_LABELS
        }

        # Model state — loaded lazily on first infer_audio call
        self._model      = None
        self._label_dict: Optional[Dict[int, str]] = None
        self._device: Optional[str] = None
        self._fp16_active = False
        self._loaded     = False
        self._load_error = ""
        self._load_lock  = threading.Lock()

        # Stats
        self._infer_count = 0
        self._alert_count = 0
        self._last_chunk_ts: Optional[str] = None
        self._last_alert_ts: Optional[str] = None
        self._avg_latency_ms: float = 0.0
        self._last_heartbeat_at: float = 0.0

    # ── Lazy model loading ─────────────────────────────────────────────────────

    def _resolve_model_path(self) -> str:
        """Resolve model_path relative to AI root if not absolute."""
        p = os.getenv("AI_BEATS_MODEL_PATH") or self._model_path
        if not p:
            p = f"models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt"
        path = Path(p)
        if not path.is_absolute():
            path = _AI_ROOT / path
        return str(path.resolve())

    def _select_device(self) -> str:
        try:
            import torch
            if self._device_cfg == "auto":
                return "cuda" if torch.cuda.is_available() else "cpu"
            return self._device_cfg
        except ImportError:
            return "cpu"

    def _load_model(self) -> bool:
        """Load BEATs model. Returns True on success. Thread-safe."""
        with self._load_lock:
            if self._loaded:
                return self._model is not None

            checkpoint_path = self._resolve_model_path()
            if not Path(checkpoint_path).exists():
                self._load_error = f"Checkpoint not found: {checkpoint_path}"
                self.logger.warning(self._load_error)
                self._loaded = True
                return False

            # Ensure BEATs source is on path
            beats_src = str(_BEATS_SRC.resolve())
            if beats_src not in sys.path:
                sys.path.insert(0, beats_src)

            try:
                import torch
                from BEATs import BEATs, BEATsConfig  # type: ignore[import]
            except ImportError as exc:
                self._load_error = (
                    f"Cannot import BEATs: {exc}. "
                    f"Run: python scripts/download_beats.py --source-only"
                )
                self.logger.error(self._load_error)
                self._loaded = True
                return False

            try:
                self.logger.info(f"Loading BEATs checkpoint: {checkpoint_path}")
                checkpoint = torch.load(checkpoint_path, map_location="cpu")

                cfg_obj = BEATsConfig(checkpoint["cfg"])
                model = BEATs(cfg_obj)
                model.load_state_dict(checkpoint["model"])
                model.eval()

                self._label_dict = checkpoint.get("label_dict")
                if self._label_dict is None:
                    self.logger.warning(
                        "Checkpoint has no label_dict — classification disabled; "
                        "embedding-only mode active."
                    )

                device = self._select_device()
                model = model.to(device)

                # FP16 only on CUDA
                fp16_active = False
                if device.startswith("cuda") and self._fp16:
                    try:
                        model = model.half()
                        fp16_active = True
                        self.logger.info("BEATs running in FP16 mode on CUDA")
                    except Exception as fp16_err:
                        self.logger.warning(f"FP16 failed, falling back to FP32: {fp16_err}")

                self._model = model
                self._device = device
                self._fp16_active = fp16_active
                self._loaded = True

                n_labels = len(self._label_dict) if self._label_dict else 0
                self.logger.info(
                    f"BEATs loaded: device={device} fp16={fp16_active} "
                    f"labels={n_labels} path={checkpoint_path}"
                )
                return True

            except Exception as exc:
                self._load_error = f"Model load failed: {exc}"
                self.logger.error(self._load_error)
                self._loaded = True
                return False

    # ── Inference ──────────────────────────────────────────────────────────────

    def infer_audio(self, chunk: AudioChunk) -> Optional[Observation]:
        """
        Run BEATs inference on an AudioChunk.

        Returns:
            Observation with lane="audio_anomaly" if an alert label is detected
            above threshold after smoothing.  Returns None if no alert.
        """
        if not self._loaded:
            self._load_model()
        if self._model is None:
            return None

        # Validate chunk
        if chunk.sample_rate != self._sample_rate:
            self.logger.warning(
                f"Chunk sample_rate {chunk.sample_rate} != expected {self._sample_rate}; skipping"
            )
            return None
        if len(chunk.samples) == 0:
            return None

        self._last_chunk_ts = chunk.ts_mid_utc

        t0 = time.perf_counter()
        try:
            raw_topk, uncertainty = self._run_beats(chunk.samples)
        except Exception as exc:
            self.logger.exception(f"BEATs inference error: {exc}")
            return None
        latency_ms = (time.perf_counter() - t0) * 1000
        self._infer_count += 1

        self._avg_latency_ms = (
            0.9 * self._avg_latency_ms + 0.1 * latency_ms
            if self._infer_count > 1 else latency_ms
        )

        # ── Heartbeat Diagnostic (Plan Section 11) ─────────────────────────
        now = time.time()
        if now - self._last_heartbeat_at >= 5.0:
            rms = np.sqrt(np.mean(chunk.samples**2))
            self.logger.info(
                f"AudioHeartbeat: cam={self.camera_id} seq={chunk.seq} "
                f"rms={rms:.4f} latency={latency_ms:.1f}ms "
                f"unc={uncertainty['composite']:.3f}"
            )
            self._last_heartbeat_at = now

        # Map raw top-k to canonical labels
        mapped = map_topk(raw_topk, min_score=self._min_raw)

        # Aggregate canonical scores: take max score per canonical label
        canonical_scores: Dict[str, float] = {}
        for entry in mapped:
            cname = entry["canonical_label"]
            if cname is None:
                continue
            score = entry["score"]
            if cname not in canonical_scores or score > canonical_scores[cname]:
                canonical_scores[cname] = score

        # Apply temporal smoothing
        smoothed_scores: Dict[str, float] = {}
        for label, smoother in self._smoothers.items():
            raw_score = canonical_scores.get(label, 0.0)
            smoothed = smoother.update(raw_score) if self._smooth else raw_score
            smoothed_scores[label] = smoothed

        # Find best alert label that crosses its threshold
        best_label: Optional[str] = None
        best_score: float = 0.0
        for label, threshold in self._alert_labels.items():
            s = smoothed_scores.get(label, 0.0)
            if s >= threshold and s > best_score:
                best_label = label
                best_score = s

        # Assemble top label list for debug
        top_preds: List[AudioPrediction] = []
        for rank, (raw_lbl, raw_score) in enumerate(raw_topk[: self._top_k], start=1):
            from .audio_label_mapper import map_audio_label
            top_preds.append(AudioPrediction(
                raw_label=raw_lbl,
                canonical_label=map_audio_label(raw_lbl) or "",
                score=round(raw_score, 4),
                rank=rank,
            ))

        obs_debug = AudioObservationDebug(
            top_labels=top_preds,
            audio_score=best_score,
            audio_uncertainty=uncertainty,
            chunk_duration_s=chunk.duration_s(),
            backend="beats",
            model_path=self._resolve_model_path(),
            sample_rate=chunk.sample_rate,
            details={
                "canonical_scores": {k: round(v, 4) for k, v in smoothed_scores.items() if v > 0.01},
                "raw_canonical_scores": {k: round(v, 4) for k, v in canonical_scores.items() if v > 0.01},
                "smoothing_enabled": self._smooth,
                "inference_ms": round(latency_ms, 1),
                "chunk_seq": chunk.seq,
                "ts_start": chunk.ts_start_utc,
                "ts_end": chunk.ts_end_utc,
            },
        )

        if best_label is None:
            # No alert this chunk — return None (no spurious non-trigger Observations)
            return None

        self._alert_count += 1
        self._last_alert_ts = chunk.ts_mid_utc

        self.logger.info(
            f"AudioAlert: camera={self.camera_id} label={best_label} "
            f"score={best_score:.3f} uncertainty={uncertainty['composite']:.3f} "
            f"latency={latency_ms:.1f}ms"
        )

        return Observation(
            ts_utc=chunk.ts_mid_utc,
            camera_id=self.camera_id,
            lane="audio_anomaly",
            score=float(best_score),
            trigger=True,
            bbox=None,
            label=best_label,
            debug=obs_debug.to_dict(),
        )

    # ── BEATs forward pass ─────────────────────────────────────────────────────

    def _run_beats(self, samples: np.ndarray) -> Tuple[List[Tuple[str, float]], Dict[str, float]]:
        """
        Run BEATs forward pass.

        Returns:
            (top_k_labels, uncertainty)
            top_k_labels: list of (raw_label_str, probability) sorted desc
            uncertainty:  dict of uncertainty metrics
        """
        import torch

        # Convert to tensor — always float32 input regardless of FP16 model
        audio = torch.from_numpy(samples).float().unsqueeze(0).to(self._device)
        padding_mask = torch.zeros(1, len(samples), dtype=torch.bool, device=self._device)

        if self._fp16_active:
            audio = audio.half()

        with torch.no_grad():
            probs_tensor, _ = self._model.extract_features(audio, padding_mask=padding_mask)

        # probs_tensor: [1, num_labels] — post-softmax probabilities
        probs = probs_tensor[0].float().cpu().numpy()   # back to float32 for numpy

        # Sort probs to find top-K efficiently
        sorted_probs = np.sort(probs)[::-1]
        top_probs = sorted_probs[:self._top_k] if len(sorted_probs) >= self._top_k else sorted_probs
        
        top1_conf = float(top_probs[0]) if len(top_probs) > 0 else 0.0
        top2_conf = float(top_probs[1]) if len(top_probs) > 1 else 0.0
        margin = top1_conf - top2_conf
        
        top_k_mass = float(np.sum(top_probs))
        
        # Normalized entropy
        entropy = -float(np.sum(top_probs * np.log(top_probs + 1e-8)))
        max_entropy = math.log(len(top_probs)) if len(top_probs) > 1 else 1.0
        entropy_norm = float(np.clip(entropy / max_entropy, 0.0, 1.0))
        
        # Composite scalar uncertainty (0.0 to 1.0)
        # Low margin -> high uncertainty. High entropy -> high uncertainty.
        margin_unc = 1.0 - margin
        uncertainty_scalar = max(1.0 - top1_conf, margin_unc * 0.5, entropy_norm * 0.5)
        
        uncertainty = {
            "composite": float(np.clip(uncertainty_scalar, 0.0, 1.0)),
            "top1_confidence": top1_conf,
            "margin": margin,
            "top_k_entropy": entropy_norm,
            "top_k_mass": top_k_mass
        }

        # Build label list from checkpoint label_dict
        if self._label_dict is None:
            # No label dict — return raw indices as strings
            topk_idx = np.argsort(probs)[::-1][: self._top_k]
            return [(f"class_{i}", float(probs[i])) for i in topk_idx], uncertainty

        topk_idx = np.argsort(probs)[::-1][: self._top_k]
        results = [
            (self._label_dict[int(i)], float(probs[i]))
            for i in topk_idx
            if int(i) in self._label_dict
        ]
        return results, uncertainty

    # ── Health ─────────────────────────────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        """Return a health dict for the AI /status endpoint."""
        return {
            "enabled": True,
            "model_loaded": self._model is not None,
            "load_error": self._load_error or None,
            "model_path": self._resolve_model_path(),
            "device": self._device,
            "fp16": self._fp16_active,
            "sample_rate": self._sample_rate,
            "backend": "beats",
            "label_count": len(self._label_dict) if self._label_dict else 0,
            "infer_count": self._infer_count,
            "alert_count": self._alert_count,
            "avg_latency_ms": round(self._avg_latency_ms, 1),
            "last_chunk_ts": self._last_chunk_ts,
            "last_alert_ts": self._last_alert_ts,
            "smoothing_enabled": self._smooth,
            "alert_labels": self._alert_labels,
        }
