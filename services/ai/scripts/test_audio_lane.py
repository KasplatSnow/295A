#!/usr/bin/env python3
"""
PR-03 unit tests: audio_label_mapper + AudioAnomalyLane (mocked BEATs).

Tests:
  1. Label mapper: positive mapping rules
  2. Label mapper: blocklist (should return None)
  3. Label mapper: map_topk with score filtering
  4. AudioAnomalyLane: mocked BEATs output → Observation emitted above threshold
  5. AudioAnomalyLane: music label → no Observation (blocked)
  6. AudioAnomalyLane: score below threshold → no Observation
  7. AudioAnomalyLane: graceful failure when model not loaded
  8. Score smoother: temporal windowing

Usage (from services/ai/):
    python scripts/test_audio_lane.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Path anchors
_SCRIPT_DIR = Path(__file__).resolve().parent
_AI_ROOT    = _SCRIPT_DIR.parent
sys.path.insert(0, str(_AI_ROOT))

from src.lanes.audio_label_mapper import (
    CANONICAL_LABELS,
    _is_blocked,
    map_audio_label,
    map_topk,
)
from src.lanes.audio_anomaly import AudioAnomalyLane, _ScoreSmoother
from src.common.audio_types import AudioChunk
from src.common.types import Observation

import numpy as np

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


# ══════════════════════════════════════════════════════════════════════════════
# Helper: make a 1-second AudioChunk of silence (or sine)
# ══════════════════════════════════════════════════════════════════════════════
def _make_chunk(camera_id: str = "test_cam", seq: int = 1) -> AudioChunk:
    samples = np.zeros(16000, dtype=np.float32)
    return AudioChunk(
        camera_id=camera_id,
        ts_start_utc="2026-01-01T12:00:00Z",
        ts_end_utc="2026-01-01T12:00:01Z",
        ts_mid_utc="2026-01-01T12:00:00.5Z",
        samples=samples,
        sample_rate=16000,
        seq=seq,
    )


def _make_lane(cfg_overrides: dict = None) -> AudioAnomalyLane:
    """Return an AudioAnomalyLane with a small test config."""
    cfg = {
        "enabled": True,
        "device": "cpu",
        "fp16": False,
        "sample_rate": 16000,
        "top_k": 10,
        "min_raw_score": 0.10,
        "min_canonical_score": 0.50,
        "chunk_s": 1.0,
        "hop_s": 0.5,
        "alert_labels": {
            "audio_scream":       0.70,
            "audio_gunshot":      0.80,
            "audio_explosion":    0.80,
            "audio_glass_break":  0.65,
            "audio_siren":        0.75,
            "audio_alarm":        0.70,
            "audio_vehicle_crash": 0.75,
            "audio_shout":        0.75,
        },
        "smoothing": {"enabled": False, "window_s": 3.0, "ema_alpha": 0.45},
    }
    if cfg_overrides:
        cfg.update(cfg_overrides)
    return AudioAnomalyLane(camera_id="test_cam", cfg=cfg, models_cfg={})


# ══════════════════════════════════════════════════════════════════════════════
# Test 1-3: Label mapper
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelMapper(unittest.TestCase):

    # Positive mapping rules
    def test_screaming_maps_to_scream(self):
        self.assertEqual(map_audio_label("Screaming"), "audio_scream")

    def test_scream_maps_to_scream(self):
        self.assertEqual(map_audio_label("Scream"), "audio_scream")

    def test_gunshot_maps_to_gunshot(self):
        self.assertEqual(map_audio_label("Gunshot, gunfire"), "audio_gunshot")

    def test_explosion_maps_to_explosion(self):
        self.assertEqual(map_audio_label("Explosion"), "audio_explosion")

    def test_glass_maps_to_glass_break(self):
        self.assertEqual(map_audio_label("Glass"), "audio_glass_break")
        self.assertEqual(map_audio_label("Breaking"), "audio_glass_break")

    def test_siren_maps_to_siren(self):
        self.assertEqual(map_audio_label("Siren"), "audio_siren")
        self.assertEqual(map_audio_label("Civil defense siren"), "audio_siren")

    def test_alarm_maps_to_alarm(self):
        self.assertEqual(map_audio_label("Alarm"), "audio_alarm")
        self.assertEqual(map_audio_label("Fire alarm"), "audio_alarm")
        self.assertEqual(map_audio_label("Smoke detector"), "audio_alarm")

    def test_car_crash_maps_to_vehicle_crash(self):
        self.assertEqual(map_audio_label("Car crash"), "audio_vehicle_crash")

    def test_shout_maps_to_shout(self):
        self.assertEqual(map_audio_label("Shout"), "audio_shout")
        self.assertEqual(map_audio_label("Yell"), "audio_shout")

    # Blocklist — must return None
    def test_crash_cymbal_is_blocked(self):
        result = map_audio_label("Crash cymbal")
        self.assertIsNone(result, "Crash cymbal must NOT map to any alert label")

    def test_music_is_blocked(self):
        self.assertIsNone(map_audio_label("Music"))
        self.assertIsNone(map_audio_label("Pop music"))

    def test_speech_is_blocked(self):
        self.assertIsNone(map_audio_label("Speech"))
        self.assertIsNone(map_audio_label("Narration, monologue"))

    def test_dog_is_blocked(self):
        self.assertIsNone(map_audio_label("Dog bark"))

    def test_cap_gun_is_blocked(self):
        self.assertIsNone(map_audio_label("Cap gun"))

    def test_laughter_is_blocked(self):
        self.assertIsNone(map_audio_label("Laughter"))

    def test_keyboard_typing_is_blocked(self):
        self.assertIsNone(map_audio_label("Typing"))

    # Unmapped (not in rules, not blocked) → None
    def test_undefined_label_returns_none(self):
        self.assertIsNone(map_audio_label("Saxophone"))
        self.assertIsNone(map_audio_label("Rain"))

    # map_topk
    def test_map_topk_filters_by_score(self):
        raw = [
            ("Screaming", 0.85),
            ("Music", 0.70),
            ("Crash cymbal", 0.60),
            ("Glass", 0.20),
            ("Rain", 0.05),    # below min_score → excluded
        ]
        results = map_topk(raw, min_score=0.15)
        self.assertEqual(len(results), 4)  # Rain excluded
        # Screaming should map to audio_scream
        scream_entry = next(r for r in results if r["raw_label"] == "Screaming")
        self.assertEqual(scream_entry["canonical_label"], "audio_scream")
        # Music should map to None (blocked)
        music_entry = next(r for r in results if r["raw_label"] == "Music")
        self.assertIsNone(music_entry["canonical_label"])
        # Crash cymbal should map to None (blocked)
        cymbal_entry = next(r for r in results if r["raw_label"] == "Crash cymbal")
        self.assertIsNone(cymbal_entry["canonical_label"])
        # Glass should map to audio_glass_break
        glass_entry = next(r for r in results if r["raw_label"] == "Glass")
        self.assertEqual(glass_entry["canonical_label"], "audio_glass_break")


# ══════════════════════════════════════════════════════════════════════════════
# Test 4-7: AudioAnomalyLane (mocked BEATs)
# ══════════════════════════════════════════════════════════════════════════════

class TestAudioAnomalyLane(unittest.TestCase):
    """
    Tests use monkeypatching to replace _run_beats() with a controlled output,
    so no actual model file is needed.
    """

    def _run_lane_with_mock(self, raw_topk, lane=None):
        """Inject mock _run_beats into a lane and run one chunk."""
        if lane is None:
            lane = _make_lane()
        # Simulate model loaded
        lane._loaded = True
        lane._model = MagicMock()   # non-None sentinel
        lane._label_dict = {}

        chunk = _make_chunk()
        uncertainty_dict = {"composite": 0.2, "top1_confidence": 0.8, "margin": 0.6, "top_k_entropy": 0.1, "top_k_mass": 0.9}
        with patch.object(lane, "_run_beats", return_value=(raw_topk, uncertainty_dict)):
            return lane.infer_audio(chunk)

    def test_scream_above_threshold_triggers_observation(self):
        """Screaming at 0.85 > threshold 0.70 → should emit Observation."""
        raw_topk = [("Screaming", 0.85), ("Shout", 0.40), ("Music", 0.10)]
        obs = self._run_lane_with_mock(raw_topk)
        self.assertIsNotNone(obs, "Expected an Observation for scream above threshold")
        self.assertIsInstance(obs, Observation)
        self.assertEqual(obs.lane, "audio_anomaly")
        self.assertEqual(obs.label, "audio_scream")
        self.assertTrue(obs.trigger)
        self.assertGreaterEqual(obs.score, 0.70)

    def test_observation_has_correct_debug_modality(self):
        """Debug dict must have modality='audio'."""
        raw_topk = [("Screaming", 0.85)]
        obs = self._run_lane_with_mock(raw_topk)
        self.assertIsNotNone(obs)
        self.assertEqual(obs.debug.get("modality"), "audio")
        self.assertEqual(obs.debug.get("backend"), "beats")

    def test_music_does_not_trigger(self):
        """Music at any score → no Observation (blocked by label mapper)."""
        raw_topk = [("Music", 0.95), ("Pop music", 0.88)]
        obs = self._run_lane_with_mock(raw_topk)
        self.assertIsNone(obs, "Music must not trigger an audio alert")

    def test_crash_cymbal_does_not_trigger(self):
        """Crash cymbal must never map to audio_vehicle_crash or any label."""
        raw_topk = [("Crash cymbal", 0.95)]
        obs = self._run_lane_with_mock(raw_topk)
        self.assertIsNone(obs, "Crash cymbal must not trigger an audio alert")

    def test_score_below_threshold_returns_none(self):
        """Scream at 0.50 < threshold 0.70 → no Observation."""
        raw_topk = [("Screaming", 0.50)]
        obs = self._run_lane_with_mock(raw_topk)
        self.assertIsNone(obs, "Score below threshold must not trigger")

    def test_gunshot_above_threshold_triggers(self):
        """Gunshot at 0.82 > threshold 0.80 → should emit Observation."""
        raw_topk = [("Gunshot, gunfire", 0.82)]
        obs = self._run_lane_with_mock(raw_topk)
        self.assertIsNotNone(obs)
        self.assertEqual(obs.label, "audio_gunshot")

    def test_model_not_loaded_returns_none(self):
        """If model failed to load, infer_audio must return None without raising."""
        lane = _make_lane()
        lane._loaded = True
        lane._model = None    # simulate load failure
        chunk = _make_chunk()
        obs = lane.infer_audio(chunk)
        self.assertIsNone(obs, "Lane with no model must return None gracefully")

    def test_wrong_sample_rate_returns_none(self):
        """Chunk with wrong sample rate must be silently rejected."""
        lane = _make_lane()
        lane._loaded = True
        lane._model = MagicMock()
        chunk = AudioChunk(
            camera_id="test_cam",
            ts_start_utc="2026-01-01T12:00:00Z",
            ts_end_utc="2026-01-01T12:00:01Z",
            ts_mid_utc="2026-01-01T12:00:00.5Z",
            samples=np.zeros(8000, dtype=np.float32),
            sample_rate=8000,    # wrong — should be 16000
            seq=1,
        )
        obs = lane.infer_audio(chunk)
        self.assertIsNone(obs)

    def test_health_returns_expected_fields(self):
        """health() must return a dict with required keys."""
        lane = _make_lane()
        lane._loaded = True
        lane._model = None
        h = lane.health()
        required_keys = {
            "enabled", "model_loaded", "model_path", "device",
            "sample_rate", "backend", "infer_count", "alert_count",
        }
        for key in required_keys:
            self.assertIn(key, h, f"health() missing key: {key}")


# ══════════════════════════════════════════════════════════════════════════════
# Test 8: Score smoother
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreSmoother(unittest.TestCase):

    def test_smoother_rises_with_high_score(self):
        smoother = _ScoreSmoother(window_s=3.0, hop_s=0.5, ema_alpha=0.45)
        # Feed 0.0 then a high score
        smoother.update(0.0)
        smoother.update(0.0)
        high = smoother.update(0.90)
        self.assertGreater(high, 0.0, "EMA must rise after high score input")

    def test_smoother_decays_after_high_score(self):
        smoother = _ScoreSmoother(window_s=2.0, hop_s=0.5, ema_alpha=0.45)
        # Push high score
        for _ in range(6):
            smoother.update(0.90)
        # Now decay
        s1 = smoother.update(0.0)
        for _ in range(10):
            smoother.update(0.0)
        s2 = smoother.current()
        self.assertLess(s2, s1, "Smoother must decay toward 0 after scores drop")

    def test_smoother_window_limits_memory(self):
        """Scores older than window_s should not influence current output."""
        smoother = _ScoreSmoother(window_s=1.0, hop_s=0.5, ema_alpha=0.45)
        # Push a spike, then many zeros
        smoother.update(1.0)
        for _ in range(20):
            smoother.update(0.0)
        final = smoother.current()
        self.assertLess(final, 0.1, f"Smoother should decay to near 0, got {final:.4f}")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 60)
    print("PR-03 Audio Lane Unit Tests — VigilZone")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in (TestLabelMapper, TestAudioAnomalyLane, TestScoreSmoother):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
