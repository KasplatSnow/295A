"""
PR-02: Audio ring buffer for VigilZone.

Keeps a short rolling window of recent audio samples in memory
and exports WAV evidence clips on demand.

Design (plan Section 6.5):
    - Thread-safe: AudioReader thread writes, audio loop and evidence callback read.
    - Memory: float32 samples stored as np.ndarray, no disk I/O until export.
    - WAV export: uses soundfile if available, falls back to Python wave module.
    - Evidence response format matches plan Section 6.5.

Default config (matches models.yaml):
    ringbuffer_s = 30.0    (30 seconds rolling)
    evidence_pre_s  = 5.0
    evidence_post_s = 5.0
"""
from __future__ import annotations

import struct
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..common.audio_types import AudioChunk
from ..common.log import setup_logger


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class AudioRingBuffer:
    """
    Thread-safe rolling buffer of AudioChunk objects.

    Stores all samples from the last `max_seconds` seconds of audio.
    Exposes slicing by UTC timestamp and WAV evidence export.
    """

    def __init__(
        self,
        camera_id: str,
        sample_rate: int = 16000,
        max_seconds: float = 30.0,
    ):
        self.camera_id = camera_id
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self.logger = setup_logger(f"AudioRingBuffer-{camera_id}")

        # Deque of (ts_start_utc_datetime, ts_end_utc_datetime, samples_array)
        # We store per-chunk metadata so we can slice precisely by wall-clock time.
        self._buf: deque[Tuple[datetime, datetime, np.ndarray]] = deque()
        self._lock = threading.RLock()
        self._total_chunks = 0

        self.logger.info(
            f"AudioRingBuffer init: camera={camera_id} "
            f"sr={sample_rate} max={max_seconds}s"
        )

    # ── Write ───────────────────────────────────────────────────────────────────

    def add_chunk(self, chunk: AudioChunk) -> None:
        """Add an AudioChunk to the ring buffer. Evicts old data to stay within max_seconds."""
        dt_start = _parse_utc(chunk.ts_start_utc)
        dt_end   = _parse_utc(chunk.ts_end_utc)

        with self._lock:
            self._buf.append((dt_start, dt_end, chunk.samples.copy()))
            self._total_chunks += 1
            self._evict()

    def _evict(self) -> None:
        """Remove chunks older than max_seconds from the left. Must be called under lock."""
        if not self._buf:
            return
        # Cutoff = newest chunk end - max_seconds
        newest_end = self._buf[-1][1]
        from datetime import timedelta
        cutoff = newest_end - timedelta(seconds=self.max_seconds)
        while self._buf and self._buf[0][1] <= cutoff:
            self._buf.popleft()

    # ── Read ────────────────────────────────────────────────────────────────────

    def get_window(self, ts_utc: str, pre_s: float, post_s: float) -> np.ndarray:
        """
        Return a contiguous float32 array covering [ts_utc - pre_s, ts_utc + post_s].

        Chunks that partially overlap the window are included in full.
        If the buffer does not cover the full window, returns whatever is available.

        Args:
            ts_utc: Reference timestamp (ISO 8601 UTC).
            pre_s:  Seconds before ts_utc to include.
            post_s: Seconds after ts_utc to include.

        Returns:
            np.ndarray of float32 samples, possibly empty.
        """
        from datetime import timedelta
        ref = _parse_utc(ts_utc)
        window_start = ref - timedelta(seconds=pre_s)
        window_end   = ref + timedelta(seconds=post_s)

        with self._lock:
            segments: List[np.ndarray] = []
            for (chunk_start, chunk_end, samples) in self._buf:
                if chunk_end < window_start:
                    continue
                if chunk_start > window_end:
                    break
                # Clip samples to window boundaries
                clip_start_s = max(0.0, (window_start - chunk_start).total_seconds())
                clip_end_s   = min(
                    chunk_end.timestamp() - chunk_start.timestamp(),
                    (window_end - chunk_start).total_seconds(),
                )
                i_start = max(0, int(clip_start_s * self.sample_rate))
                i_end   = min(len(samples), int(clip_end_s * self.sample_rate))
                if i_start < i_end:
                    segments.append(samples[i_start:i_end])

        if not segments:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(segments).astype(np.float32)

    # ── WAV export ──────────────────────────────────────────────────────────────

    def export_wav(
        self,
        ts_utc: str,
        out_path: str,
        pre_s: float = 5.0,
        post_s: float = 5.0,
    ) -> Dict[str, Any]:
        """
        Export a WAV evidence clip centred on ts_utc.

        Args:
            ts_utc:   Reference timestamp (alert time).
            out_path: Destination file path (will create parent dirs).
            pre_s:    Seconds before alert to include.
            post_s:   Seconds after alert to include.

        Returns:
            Evidence dict (plan Section 6.5):
                {kind, path, sample_rate, duration_s, pre_s, post_s, samples}
        """
        samples = self.get_window(ts_utc, pre_s, post_s)
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if len(samples) == 0:
            self.logger.warning(
                f"export_wav: no samples available for ts={ts_utc} "
                f"(buffer has {self._total_chunks} chunks)"
            )
            return {
                "kind": "audio_wav",
                "path": str(dest),
                "sample_rate": self.sample_rate,
                "duration_s": 0.0,
                "pre_s": pre_s,
                "post_s": post_s,
                "samples": 0,
                "error": "no_audio_in_buffer",
            }

        duration_s = len(samples) / self.sample_rate
        self._write_wav(dest, samples)

        self.logger.info(
            f"export_wav: {dest.name} ({duration_s:.2f}s, {len(samples)} samples)"
        )
        return {
            "kind": "audio_wav",
            "path": str(dest),
            "sample_rate": self.sample_rate,
            "duration_s": round(duration_s, 3),
            "pre_s": pre_s,
            "post_s": post_s,
            "samples": len(samples),
        }

    def _write_wav(self, dest: Path, samples: np.ndarray) -> None:
        """Write float32 samples as a WAV file. Prefers soundfile, falls back to wave."""
        try:
            import soundfile as sf  # type: ignore[import]
            sf.write(str(dest), samples, self.sample_rate, subtype="PCM_16")
        except ImportError:
            # Fallback: convert float32 → int16, write with stdlib wave
            int16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
            with wave.open(str(dest), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)       # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(int16.tobytes())

    # ── Stats ───────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return buffer stats for the AI health endpoint."""
        with self._lock:
            n_chunks = len(self._buf)
            if self._buf:
                span_s = (
                    self._buf[-1][1] - self._buf[0][0]
                ).total_seconds()
                latest_ts = self._buf[-1][1].isoformat().replace("+00:00", "Z")
            else:
                span_s = 0.0
                latest_ts = None
        return {
            "camera_id": self.camera_id,
            "chunks_in_buffer": n_chunks,
            "buffer_span_s": round(span_s, 2),
            "max_seconds": self.max_seconds,
            "sample_rate": self.sample_rate,
            "total_chunks_received": self._total_chunks,
            "latest_chunk_ts": latest_ts,
        }
