#!/usr/bin/env python3
"""
PR-04 unit tests: MultimodalFusion

Tests:
  1. Synergy pair: scream + person → fused observation emitted
  2. Synergy pair: gunshot + WEAPON_DETECTED → fused observation emitted
  3. Audio-only high-risk: gunshot with no video → emitted (allow_audio_only=True)
  4. Audio-only high-risk: gunshot with no video → NOT emitted (allow_audio_only=False)
  5. Generic co-occurrence: alarm + video activity → fused observation emitted
  6. Score below fused_threshold → no emission
  7. Cooldown: second emission within cooldown window → suppressed
  8. Cooldown: emission after cooldown expires → allowed
  9. Video passthrough mode: disabled fusion does not modify video obs
  10. Fused Observation fields: lane, label, trigger, debug.modality

Usage (from services/ai/):
    python scripts/test_multimodal_fusion.py
"""
from __future__ import annotations

import sys
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

_SCRIPT_DIR = Path(__file__).resolve().parent
_AI_ROOT    = _SCRIPT_DIR.parent
sys.path.insert(0, str(_AI_ROOT))

from src.logic.multimodal_fusion import MultimodalFusion
from src.common.types import Observation


def _ts(offset_s: float = 0.0) -> str:
    """ISO UTC timestamp offset from now."""
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


def _audio_obs(label: str, score: float = 0.85) -> Observation:
    return Observation(
        ts_utc=_ts(),
        camera_id="cam_test",
        lane="audio_anomaly",
        score=score,
        trigger=True,
        label=label,
        debug={"modality": "audio", "backend": "beats"},
    )


def _video_obs(lane: str, label: str, score: float = 0.80) -> Observation:
    return Observation(
        ts_utc=_ts(),
        camera_id="cam_test",
        lane=lane,
        score=score,
        trigger=True,
        label=label,
    )


def _make_fusion(cfg_overrides: dict = None) -> MultimodalFusion:
    cfg = {
        "enabled": True,
        "window_s": 10.0,
        "audio_weight": 0.45,
        "video_weight": 0.55,
        "synergy_bonus_confirmed": 0.12,
        "conflict_penalty": 0.10,
        "fused_alert_threshold": 0.72,
        "high_severity_threshold": 0.85,
        "allow_audio_only_high_risk": True,
        "allow_video_only_passthrough": True,
        "cooldown_s": {
            "audio_scream_person": 1,      # 1s cooldown for fast testing
            "gunshot_audio_only": 1,
            "explosion_audio_only": 1,
            "glass_break_intrusion": 1,
            "alarm_fire": 1,
            "generic_multimodal": 1,
        },
    }
    if cfg_overrides:
        cfg.update(cfg_overrides)
    return MultimodalFusion(camera_id="cam_test", cfg=cfg)


class TestMultimodalFusion(unittest.TestCase):

    # ── Test 1: scream + person → synergy ────────────────────────────────────
    def test_scream_person_synergy(self):
        fusion = _make_fusion()
        fusion.feed_audio(_audio_obs("audio_scream", score=0.85))
        fusion.feed_video([_video_obs("rt_detr", "person", score=0.90)])
        result = fusion.flush()
        self.assertEqual(len(result), 1, "Should emit one fused observation")
        obs = result[0]
        self.assertEqual(obs.lane, "audio_video_fusion")
        self.assertEqual(obs.label, "audio_scream_person_present")
        self.assertTrue(obs.trigger)
        self.assertGreaterEqual(obs.score, 0.72)

    # ── Test 2: gunshot + weapon → synergy ───────────────────────────────────
    def test_gunshot_weapon_synergy(self):
        fusion = _make_fusion()
        fusion.feed_audio(_audio_obs("audio_gunshot", score=0.90))
        fusion.feed_video([_video_obs("weapon_yolo", "gun", score=0.88)])
        result = fusion.flush()
        self.assertGreater(len(result), 0, "Should emit fused obs for gunshot+weapon")
        labels = [o.label for o in result]
        self.assertTrue(
            any("gunshot" in lbl for lbl in labels),
            f"Expected gunshot in fused label, got: {labels}"
        )

    # ── Test 3: audio-only high-risk (allow=True) ─────────────────────────────
    def test_gunshot_audio_only_allowed(self):
        fusion = _make_fusion({"allow_audio_only_high_risk": True})
        fusion.feed_audio(_audio_obs("audio_gunshot", score=0.85))
        fusion.feed_video([])   # no video
        result = fusion.flush()
        self.assertGreater(len(result), 0, "Gunshot should emit without video")
        self.assertIn("audio_only", result[0].label)

    # ── Test 4: audio-only high-risk (allow=False) ────────────────────────────
    def test_gunshot_audio_only_blocked(self):
        fusion = _make_fusion({"allow_audio_only_high_risk": False})
        fusion.feed_audio(_audio_obs("audio_gunshot", score=0.85))
        fusion.feed_video([])
        result = fusion.flush()
        self.assertEqual(len(result), 0, "Gunshot without video must be suppressed when allow=False")

    # ── Test 5: generic co-occurrence ─────────────────────────────────────────
    def test_generic_cooccurrence(self):
        fusion = _make_fusion()
        # audio_alarm is not in _HIGH_RISK_AUDIO_LABELS, but pairs with FIRE_SMOKE
        fusion.feed_audio(_audio_obs("audio_alarm", score=0.82))
        fusion.feed_video([
            _video_obs("fire_smoke_yolo", "fire_smoke", score=0.85)
        ])
        result = fusion.flush()
        self.assertGreater(len(result), 0, "alarm + fire_smoke should produce a fusion")
        obs = result[0]
        self.assertEqual(obs.lane, "audio_video_fusion")
        self.assertTrue(obs.trigger)

    # ── Test 6: score below threshold → no emission ───────────────────────────
    def test_low_score_no_emission(self):
        # Audio score very low → fused score will be below threshold
        fusion = _make_fusion()
        fusion.feed_audio(_audio_obs("audio_scream", score=0.30))
        fusion.feed_video([_video_obs("rt_detr", "person", score=0.30)])
        result = fusion.flush()
        self.assertEqual(len(result), 0, "Low scores should not trigger fusion alert")

    # ── Test 7: cooldown suppresses repeat emission ────────────────────────────
    def test_cooldown_suppresses_repeat(self):
        # cooldown set to 999s so second emission within window is blocked
        fusion = _make_fusion({"cooldown_s": {
            "audio_scream_person": 999,
            "gunshot_audio_only": 999,
            "explosion_audio_only": 999,
            "glass_break_intrusion": 999,
            "alarm_fire": 999,
            "generic_multimodal": 999,
        }})
        # First emission
        fusion.feed_audio(_audio_obs("audio_scream", score=0.85))
        fusion.feed_video([_video_obs("rt_detr", "person", score=0.90)])
        r1 = fusion.flush()
        self.assertEqual(len(r1), 1, "First emission should succeed")
        # Immediate repeat
        fusion.feed_audio(_audio_obs("audio_scream", score=0.85))
        fusion.feed_video([_video_obs("rt_detr", "person", score=0.90)])
        r2 = fusion.flush()
        self.assertEqual(len(r2), 0, "Second emission within cooldown must be suppressed")

    # ── Test 8: emission allowed after cooldown expires ───────────────────────
    def test_cooldown_expires(self):
        fusion = _make_fusion()   # 1s cooldown
        # First fire
        fusion.feed_audio(_audio_obs("audio_gunshot", score=0.85))
        fusion.feed_video([])
        r1 = fusion.flush()
        self.assertGreater(len(r1), 0, "First gunshot should fire")
        # Wait for cooldown
        time.sleep(1.1)
        # Second fire
        fusion.feed_audio(_audio_obs("audio_gunshot", score=0.85))
        fusion.feed_video([])
        r2 = fusion.flush()
        self.assertGreater(len(r2), 0, "Second gunshot after cooldown should fire")

    # ── Test 9: disabled fusion passes nothing ────────────────────────────────
    def test_disabled_fusion_emits_nothing(self):
        fusion = _make_fusion({"enabled": False})
        fusion.feed_audio(_audio_obs("audio_gunshot", score=0.95))
        fusion.feed_video([_video_obs("rt_detr", "person", score=0.95)])
        result = fusion.flush()
        self.assertEqual(len(result), 0, "Disabled fusion must emit nothing")

    # ── Test 10: fused Observation field contract ─────────────────────────────
    def test_fused_observation_fields(self):
        fusion = _make_fusion()
        fusion.feed_audio(_audio_obs("audio_scream", score=0.85))
        fusion.feed_video([_video_obs("rt_detr", "person", score=0.90)])
        result = fusion.flush()
        self.assertEqual(len(result), 1)
        obs = result[0]
        # Required fields
        self.assertEqual(obs.lane, "audio_video_fusion")
        self.assertTrue(obs.trigger)
        self.assertIsNotNone(obs.label)
        self.assertIsInstance(obs.score, float)
        self.assertIsNotNone(obs.debug)
        # Debug modality
        self.assertEqual(obs.debug.get("modality"), "fusion")
        # Debug must reference both modalities
        self.assertIn("audio_label", obs.debug)
        self.assertIn("audio_score", obs.debug)
        self.assertIn("video_context", obs.debug)

    # ── Test 11: stats dict completeness ──────────────────────────────────────
    def test_stats_dict(self):
        fusion = _make_fusion()
        s = fusion.stats()
        for key in ("enabled", "camera_id", "fusions_attempted", "fusions_emitted", "audio_only_emitted"):
            self.assertIn(key, s, f"stats() missing key: {key}")

    # ── Test 12: scream score sensitivity ────────────────────────────────────
    def test_score_computation_synergy_bonus(self):
        """Synergy pair must receive bonus: fused_score > weighted average."""
        fusion = _make_fusion()
        audio_score = 0.75
        video_score = 0.80
        base = 0.45 * audio_score + 0.55 * video_score
        synergy_score = fusion._compute_fused_score(audio_score, video_score, synergy=True)
        no_synergy_score = fusion._compute_fused_score(audio_score, video_score, synergy=False)
        self.assertGreater(synergy_score, no_synergy_score, "Synergy must add a bonus")
        self.assertAlmostEqual(no_synergy_score, base, places=3)

    # ── Test 13: explosion audio only → AUDIO_ANOMALY label ──────────────────
    def test_explosion_audio_only_label(self):
        fusion = _make_fusion()
        fusion.feed_audio(_audio_obs("audio_explosion", score=0.90))
        fusion.feed_video([])
        result = fusion.flush()
        self.assertGreater(len(result), 0)
        self.assertIn("audio_only", result[0].label)


def main() -> int:
    print("=" * 60)
    print("PR-04 Multimodal Fusion Unit Tests — VigilZone")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromTestCase(TestMultimodalFusion)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
