"""
PR-02: Audio data types for the VigilZone audio pipeline.

These types flow:
    FFmpegAudioReader → AudioChunk → AudioAnomalyLane → AudioObservation (via Observation)
    AudioChunk → AudioRingBuffer → WAV evidence export

IMPORTANT: Do NOT serialize `samples` (np.ndarray) to JSON.
           Only ts/metadata fields go into the incident payload.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass(frozen=True)
class AudioChunk:
    """
    A single chunk of mono float32 PCM audio produced by FFmpegAudioReader.

    Spec (plan Section 6.3):
        samples    : mono float32, shape (N,), values in approx [-1.0, 1.0]
        sample_rate: must be 16000 for BEATs
        channels   : always 1 (mono)
    """
    camera_id: str
    ts_start_utc: str   # ISO 8601 — wall-clock start of this chunk
    ts_end_utc: str     # ISO 8601 — wall-clock end
    ts_mid_utc: str     # ISO 8601 — midpoint, used as the observation timestamp
    samples: np.ndarray # shape (N,), dtype float32, mono
    sample_rate: int = 16000
    channels: int = 1
    source: str = "rtsp_audio"
    seq: int = 0        # monotonically increasing sequence number per camera

    def duration_s(self) -> float:
        """Actual duration in seconds based on sample count."""
        return len(self.samples) / max(self.sample_rate, 1)

    def __repr__(self) -> str:
        return (
            f"AudioChunk(camera={self.camera_id!r}, seq={self.seq}, "
            f"ts={self.ts_mid_utc!r}, dur={self.duration_s():.2f}s, "
            f"sr={self.sample_rate}, samples={len(self.samples)})"
        )


@dataclass(frozen=True)
class AudioPrediction:
    """
    Single label prediction from the BEATs model for one AudioChunk.
    Produced by AudioAnomalyLane and stored in debug payloads.
    """
    raw_label: str           # original AudioSet label string
    canonical_label: str     # mapped product label or "" if not mapped
    score: float             # probability [0.0, 1.0]
    rank: int                # 1-based rank among top-k predictions


@dataclass(frozen=True)
class AudioObservationDebug:
    """
    Debug payload attached to Observation.debug when lane == "audio_anomaly".
    All fields are JSON-serialisable (no numpy arrays).
    """
    top_labels: List[AudioPrediction] = field(default_factory=list)
    audio_score: float = 0.0
    audio_uncertainty: Dict[str, float] = field(default_factory=dict)
    snr_estimate: Optional[float] = None
    chunk_duration_s: float = 0.0
    backend: str = "beats"
    model_path: str = ""
    sample_rate: int = 16000
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe dict for embedding in Observation.debug."""
        return {
            "modality": "audio",
            "backend": self.backend,
            "model_path": self.model_path,
            "sample_rate": self.sample_rate,
            "audio_score": round(self.audio_score, 4),
            "audio_uncertainty": {k: round(v, 4) for k, v in self.audio_uncertainty.items()},
            "snr_estimate": round(self.snr_estimate, 2) if self.snr_estimate is not None else None,
            "chunk_duration_s": round(self.chunk_duration_s, 3),
            "raw_top_labels": [
                {
                    "raw_label": p.raw_label,
                    "canonical_label": p.canonical_label,
                    "score": round(p.score, 4),
                    "rank": p.rank,
                }
                for p in self.top_labels
            ],
            **self.details,
        }
