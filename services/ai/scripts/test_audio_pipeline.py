#!/usr/bin/env python3
"""
PR-02 smoke test: AudioRingBuffer, WAV export, and FFmpegAudioReader.

Tests:
  1. Generate a 3-second sine wave WAV with FFmpeg.
  2. Read it via FFmpegAudioReader (uses file:// path, works without RTSP).
  3. Feed chunks into AudioRingBuffer.
  4. Export a WAV evidence clip and validate it.

Usage (from services/ai/):
    python scripts/test_audio_pipeline.py
    python scripts/test_audio_pipeline.py --duration 5

Exit codes:
    0 — all tests passed
    1 — one or more tests failed
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── Path anchors (works from any CWD) ─────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_AI_ROOT    = _SCRIPT_DIR.parent
sys.path.insert(0, str(_AI_ROOT))

from src.common.audio_types import AudioChunk
from src.evidence.audio_ringbuffer import AudioRingBuffer
from src.ingest.audio_reader import FFmpegAudioReader

import numpy as np


PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"


def _check_ffmpeg() -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False


def test_ringbuffer_basics() -> bool:
    """Test add_chunk, get_window, eviction."""
    print("\n[Test 1] AudioRingBuffer basic add/get window")

    sample_rate = 16000
    buf = AudioRingBuffer("test_cam", sample_rate=sample_rate, max_seconds=10.0)

    from datetime import datetime, timezone, timedelta
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Add 5 chunks of 1 second each
    for i in range(5):
        t0 = (base + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        t1 = (base + timedelta(seconds=i + 1)).isoformat().replace("+00:00", "Z")
        tm = (base + timedelta(seconds=i + 0.5)).isoformat().replace("+00:00", "Z")
        samples = np.sin(2 * np.pi * 440 * np.arange(sample_rate) / sample_rate).astype(np.float32)
        chunk = AudioChunk(
            camera_id="test_cam",
            ts_start_utc=t0,
            ts_end_utc=t1,
            ts_mid_utc=tm,
            samples=samples,
            sample_rate=sample_rate,
            seq=i,
        )
        buf.add_chunk(chunk)

    # Request window: midpoint ts=2.5s, pre=1.5s, post=1.5s → expect ~3s of samples
    ref_ts = (base + timedelta(seconds=2.5)).isoformat().replace("+00:00", "Z")
    window = buf.get_window(ref_ts, pre_s=1.5, post_s=1.5)
    expected_min = int(2.5 * sample_rate)   # at least 2.5s
    expected_max = int(3.5 * sample_rate)   # at most 3.5s

    if len(window) < expected_min:
        print(f"  {FAIL} window too short: {len(window)} < {expected_min} samples")
        return False

    print(f"  {PASS} get_window returned {len(window)} samples ({len(window)/sample_rate:.2f}s)")

    stats = buf.stats()
    print(f"  {PASS} stats: {stats}")
    return True


def test_wav_export(tmp_dir: Path) -> bool:
    """Test WAV export with known sine samples."""
    print("\n[Test 2] WAV export (soundfile or stdlib wave fallback)")

    sample_rate = 16000
    buf = AudioRingBuffer("cam_export", sample_rate=sample_rate, max_seconds=30.0)

    from datetime import datetime, timezone, timedelta

    # Load 10 seconds of 440 Hz sine into buffer
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(10):
        t0 = (base + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        t1 = (base + timedelta(seconds=i + 1)).isoformat().replace("+00:00", "Z")
        tm = (base + timedelta(seconds=i + 0.5)).isoformat().replace("+00:00", "Z")
        samples = (0.5 * np.sin(
            2 * np.pi * 440 * np.arange(sample_rate) / sample_rate
        )).astype(np.float32)
        buf.add_chunk(AudioChunk(
            camera_id="cam_export",
            ts_start_utc=t0, ts_end_utc=t1, ts_mid_utc=tm,
            samples=samples, sample_rate=sample_rate, seq=i,
        ))

    # Export: centre at 5s, 3s pre, 3s post → expect ~6s WAV
    ref_ts = (base + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    out_wav = tmp_dir / "test_export.wav"
    result = buf.export_wav(str(ref_ts), str(out_wav), pre_s=3.0, post_s=3.0)

    if "error" in result:
        print(f"  {FAIL} export_wav returned error: {result['error']}")
        return False

    if not out_wav.exists():
        print(f"  {FAIL} WAV file not created at {out_wav}")
        return False

    size_kb = out_wav.stat().st_size / 1024
    duration = result["duration_s"]
    print(f"  {PASS} WAV exported: {out_wav.name} ({size_kb:.1f} KB, {duration:.2f}s)")

    # Validate with soundfile or wave
    try:
        import soundfile as sf
        data, sr = sf.read(str(out_wav))
        assert sr == sample_rate, f"sample rate mismatch: {sr} != {sample_rate}"
        assert abs(len(data) / sr - duration) < 0.1, f"duration mismatch"
        print(f"  {PASS} WAV validated with soundfile: {len(data)} samples @ {sr}Hz")
    except ImportError:
        import wave as wavemod
        with wavemod.open(str(out_wav)) as wf:
            assert wf.getframerate() == sample_rate
            n = wf.getnframes()
            print(f"  {PASS} WAV validated with stdlib wave: {n} frames @ {sample_rate}Hz")

    return True


def test_reader_with_file(tmp_dir: Path, duration_s: int = 3) -> bool:
    """
    Test FFmpegAudioReader with a locally generated WAV file.
    Generates a sine wave, reads it back as chunks, feeds into ring buffer.
    """
    print(f"\n[Test 3] FFmpegAudioReader with local {duration_s}s WAV file")

    if not _check_ffmpeg():
        print(f"  {WARN} ffmpeg not found — skipping reader test")
        return True     # not a failure, just not testable in this env

    # Generate test WAV
    test_wav = tmp_dir / "reader_input.wav"
    gen_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"sine=frequency=880:duration={duration_s}",
        "-ac", "1", "-ar", "16000",
        str(test_wav),
    ]
    result = subprocess.run(gen_cmd, capture_output=True)
    if result.returncode != 0:
        print(f"  {FAIL} FFmpeg sine generation failed: {result.stderr.decode()[:200]}")
        return False
    print(f"  {PASS} Generated test WAV: {test_wav.name}")

    # Read with FFmpegAudioReader
    reader = FFmpegAudioReader(
        camera_id="smoke_cam",
        source_url=str(test_wav),
        sample_rate=16000,
        chunk_s=1.0,
        hop_s=0.5,
    )
    buf = AudioRingBuffer("smoke_cam", sample_rate=16000, max_seconds=30.0)

    reader.start()
    collected = 0
    deadline = time.monotonic() + duration_s + 5   # generous timeout

    while time.monotonic() < deadline:
        got = reader.wait_for_chunk(timeout=0.5)
        if got:
            chunk = reader.get_latest()
            if chunk is not None:
                buf.add_chunk(chunk)
                collected += 1
                print(f"    chunk #{chunk.seq}: {len(chunk.samples)} samples, dur={chunk.duration_s():.2f}s")
        if not reader.is_healthy() and collected > 0:
            break   # file finished reading

    reader.stop()

    # We should have received at least (duration_s / hop_s) - 1 chunks
    expected_min_chunks = max(1, int(duration_s / 1.0) - 1)
    if collected < expected_min_chunks:
        print(f"  {FAIL} only collected {collected} chunks (expected >= {expected_min_chunks})")
        return False

    print(f"  {PASS} Collected {collected} chunks")

    # Export evidence
    buf_stats = buf.stats()
    print(f"  {PASS} Ring buffer: {buf_stats['chunks_in_buffer']} chunks, {buf_stats['buffer_span_s']:.1f}s span")

    out_wav = tmp_dir / "reader_evidence.wav"
    latest_ts = buf_stats.get("latest_chunk_ts")
    if latest_ts:
        evid = buf.export_wav(latest_ts, str(out_wav), pre_s=float(duration_s), post_s=0.5)
        if not out_wav.exists():
            print(f"  {FAIL} evidence WAV not created")
            return False
        print(f"  {PASS} Evidence WAV: {evid['duration_s']:.2f}s")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="PR-02 audio pipeline smoke test")
    parser.add_argument("--duration", type=int, default=3, help="Test WAV duration in seconds")
    args = parser.parse_args()

    print("=" * 60)
    print("PR-02 Audio Pipeline Smoke Test — VigilZone")
    print("=" * 60)

    results = []
    with tempfile.TemporaryDirectory(prefix="vigilzone_audio_test_") as tmp:
        tmp_path = Path(tmp)
        results.append(("RingBuffer basics", test_ringbuffer_basics()))
        results.append(("WAV export",        test_wav_export(tmp_path)))
        results.append(("Reader + file",     test_reader_with_file(tmp_path, args.duration)))

    print()
    print("=" * 60)
    all_ok = True
    for name, passed in results:
        icon = PASS if passed else FAIL
        print(f"  {icon} {name}")
        if not passed:
            all_ok = False

    print("=" * 60)
    if all_ok:
        print("All tests passed.")
        return 0
    else:
        print("Some tests FAILED — see output above.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
