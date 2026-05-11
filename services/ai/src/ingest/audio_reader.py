"""
PR-02: FFmpeg-based audio reader for VigilZone.

Extracts mono 16 kHz float32 PCM from:
  - RTSP URLs with embedded audio track
  - Separate audio-only URLs
  - Local test files (WAV, MP4, etc.)

Design constraints (plan Section 6.4):
  - Non-blocking: runs in a background thread, does NOT stall the video loop
  - Chunked: emits AudioChunk objects of configurable duration with overlap
  - Fail-safe: audio failure MUST NOT crash the camera processor
  - In video_audio mode: degraded to video_only on audio failure (with health warning)
  - In audio_only mode: marks processor degraded, emits no fake alerts

FFmpeg command (plan Section 6.4):
    ffmpeg -hide_banner -loglevel warning -nostdin
           [-rtsp_transport tcp]   # only for rtsp:// sources
           -i <source_url>
           -vn -ac 1 -ar 16000 -f f32le pipe:1

PCM math:
  1 second @ 16kHz mono float32 = 16000 samples × 4 bytes = 64000 bytes/s
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from ..common.audio_types import AudioChunk
from ..common.log import setup_logger


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _offset_utc(base_utc: str, offset_s: float) -> str:
    """Return ISO timestamp = base_utc + offset_s seconds."""
    from datetime import timedelta
    dt = datetime.fromisoformat(base_utc.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


class FFmpegAudioReader:
    """
    Non-blocking FFmpeg audio reader.

    Runs a background thread that keeps a subprocess pipe to FFmpeg open,
    reads float32 PCM bytes, assembles them into AudioChunk objects with
    configurable chunk_s / hop_s, and exposes the latest chunk via get_latest().

    The thread restarts FFmpeg automatically on failure with exponential backoff.
    """

    _BYTES_PER_SAMPLE = 4          # float32
    _RECONNECT_BASE_S = 3.0
    _RECONNECT_MAX_S  = 60.0
    _RECONNECT_MULT   = 1.5

    def __init__(
        self,
        camera_id: str,
        source_url: str,
        sample_rate: int = 16000,
        chunk_s: float = 1.0,
        hop_s: float = 0.5,
        logger=None,
    ):
        """
        Args:
            camera_id:   Camera/source identifier for logging and chunk metadata.
            source_url:  RTSP URL, HTTP URL, or local file path.
            sample_rate: Must be 16000 for BEATs compatibility.
            chunk_s:     Duration of each emitted AudioChunk in seconds.
            hop_s:       Hop between consecutive chunks (hop_s < chunk_s → overlap).
            logger:      Optional pre-configured logger; creates one if None.
        """
        self.camera_id = camera_id
        self.source_url = source_url
        self.sample_rate = sample_rate
        self.chunk_s = chunk_s
        self.hop_s = min(hop_s, chunk_s)     # hop cannot exceed chunk
        self.logger = logger or setup_logger(f"AudioReader-{camera_id}")

        # Derived sizes in samples
        self._chunk_samples = int(chunk_s * sample_rate)
        self._hop_samples   = int(hop_s * sample_rate)
        self._bytes_per_chunk = self._chunk_samples * self._BYTES_PER_SAMPLE

        # State
        self._running = False
        self._healthy = False
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._latest: Optional[AudioChunk] = None
        self._new_chunk_event = threading.Event()
        self._stop_event = threading.Event()
        self._seq = 0
        self._chunks_total = 0
        self._reconnect_count = 0
        self._last_error: str = ""
        self._start_time: float = 0.0

        # Overlap buffer: carries leftover samples between hops
        self._overlap_buf: np.ndarray = np.empty(0, dtype=np.float32)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background audio reader thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._start_time = time.monotonic()
        self._thread = threading.Thread(
            target=self._reader_loop, daemon=True, name=f"audio-{self.camera_id}"
        )
        self._thread.start()
        self.logger.info(
            f"AudioReader started: src={self.source_url!r} "
            f"sr={self.sample_rate} chunk={self.chunk_s}s hop={self.hop_s}s"
        )

    def stop(self) -> None:
        """Signal stop and wait for the thread to exit."""
        self._running = False
        self._stop_event.set()
        self._new_chunk_event.set()     # unblock any waiting call
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        self._proc = None
        self._healthy = False
        self.logger.info(f"AudioReader stopped: camera={self.camera_id}")

    def get_latest(self) -> Optional[AudioChunk]:
        """Return the most recent AudioChunk (non-blocking). Returns None if none yet."""
        with self._lock:
            chunk = self._latest
            self._latest = None          # consume — caller processes once
        return chunk

    def wait_for_chunk(self, timeout: float = 0.5) -> bool:
        """Block until a new chunk is available or timeout expires. Returns True if chunk ready."""
        fired = self._new_chunk_event.wait(timeout=timeout)
        self._new_chunk_event.clear()
        return fired

    def is_healthy(self) -> bool:
        """True if FFmpeg subprocess is running and producing samples."""
        return self._healthy

    def stats(self) -> dict:
        """Return a health/stats dict for inclusion in the AI health endpoint."""
        return {
            "camera_id": self.camera_id,
            "source_url": self.source_url,
            "running": self._running,
            "healthy": self._healthy,
            "chunks_total": self._chunks_total,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
            "sample_rate": self.sample_rate,
            "chunk_s": self.chunk_s,
            "hop_s": self.hop_s,
            "uptime_s": round(time.monotonic() - self._start_time, 1) if self._start_time else 0,
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_ffmpeg_cmd(self) -> list:
        """Build the FFmpeg command for this source URL (plan Section 6.4)."""
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-nostdin",
        ]
        if self.source_url.startswith("rtsp://"):
            cmd += ["-rtsp_transport", "tcp"]
        cmd += [
            "-i", self.source_url,
            "-vn",                      # drop video
            "-ac", "1",                 # mono
            "-ar", str(self.sample_rate),
            "-f", "f32le",              # raw float32 little-endian PCM
            "pipe:1",                   # write to stdout
        ]
        return cmd

    def _reader_loop(self) -> None:
        """Main background loop. Restarts FFmpeg on failure with backoff."""
        backoff = self._RECONNECT_BASE_S
        while self._running:
            proc = None
            try:
                cmd = self._build_ffmpeg_cmd()
                self.logger.info(f"Starting FFmpeg: {' '.join(cmd)}")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
                self._proc = proc
                self._healthy = True
                backoff = self._RECONNECT_BASE_S
                self._overlap_buf = np.empty(0, dtype=np.float32)

                self._process_pipe(proc)

            except Exception as exc:
                self._last_error = str(exc)
                self.logger.warning(f"AudioReader error: {exc}")
            finally:
                self._healthy = False
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except Exception:
                        pass
                if self._proc is proc:
                    self._proc = None

            if not self._running:
                break

            self._reconnect_count += 1
            self.logger.warning(
                f"AudioReader reconnecting in {backoff:.1f}s "
                f"(attempt #{self._reconnect_count})"
            )
            if self._stop_event.wait(backoff):
                break
            backoff = min(backoff * self._RECONNECT_MULT, self._RECONNECT_MAX_S)

    def _process_pipe(self, proc: subprocess.Popen) -> None:
        """
        Read float32 PCM bytes from FFmpeg stdout pipe.
        Assembles samples into chunks of self._chunk_samples with overlap.

        Raw bytes consumed per loop = self._hop_samples × 4
        (We accumulate hop_samples of new data, prepend overlap, emit chunk.)
        """
        hop_bytes = self._hop_samples * self._BYTES_PER_SAMPLE
        session_start_utc = _now_utc()
        samples_elapsed = 0   # total samples read this session

        while self._running and not self._stop_event.is_set():
            raw = proc.stdout.read(hop_bytes)
            if not raw:
                # EOF — FFmpeg exited
                self.logger.warning(f"FFmpeg pipe EOF for {self.camera_id}")
                break

            new_samples = np.frombuffer(raw, dtype=np.float32)
            # Combine overlap + new samples
            combined = np.concatenate([self._overlap_buf, new_samples])

            if len(combined) < self._chunk_samples:
                # Not enough samples yet to fill a chunk — accumulate
                self._overlap_buf = combined
                continue

            # Emit chunk from the head
            chunk_samples = combined[: self._chunk_samples]
            self._overlap_buf = combined[self._hop_samples:]  # carry overlap

            samples_elapsed += self._hop_samples
            ts_start = _offset_utc(session_start_utc, (samples_elapsed - self._chunk_samples) / self.sample_rate)
            ts_end   = _offset_utc(session_start_utc, samples_elapsed / self.sample_rate)
            ts_mid   = _offset_utc(session_start_utc, (samples_elapsed - self._chunk_samples / 2) / self.sample_rate)

            self._seq += 1
            chunk = AudioChunk(
                camera_id=self.camera_id,
                ts_start_utc=ts_start,
                ts_end_utc=ts_end,
                ts_mid_utc=ts_mid,
                samples=chunk_samples.copy(),   # copy so overlap_buf mutation is safe
                sample_rate=self.sample_rate,
                channels=1,
                source=self.source_url,
                seq=self._seq,
            )
            self._chunks_total += 1

            with self._lock:
                self._latest = chunk
            self._new_chunk_event.set()

            if proc.poll() is not None:
                self.logger.warning(f"FFmpeg process exited (rc={proc.poll()})")
                break
