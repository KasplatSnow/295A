import unittest
from typing import Dict, Any

from src.logic.multimodal_fusion import MultimodalFusion
from src.common.types import Observation

class TestUncertaintyGating(unittest.TestCase):
    def setUp(self):
        cfg = {
            "enabled": True,
            "uncertainty_threshold": 0.6,
            "window_s": 10.0,
            "audio_weight": 0.5,
            "video_weight": 0.5,
            "synergy_bonus_confirmed": 0.2,
            "fused_alert_threshold": 0.7,
            "allow_audio_only_high_risk": True,
            "normality_ema_alpha": 0.1,
        }
        self.fusion = MultimodalFusion(camera_id="cam_1", cfg=cfg)

    def _audio_obs(self, label: str, score: float, composite_unc: float) -> Observation:
        return Observation(
            ts_utc="2026-05-10T12:00:00Z",
            camera_id="cam_1",
            lane="audio_anomaly",
            score=score,
            trigger=True,
            label=label,
            debug={"audio_uncertainty": {"composite": composite_unc}}
        )

    def _video_obs(self, label: str, score: float) -> Observation:
        return Observation(
            ts_utc="2026-05-10T12:00:01Z",
            camera_id="cam_1",
            lane="weapon_yolo",
            score=score,
            trigger=True,
            label=label
        )

    def test_high_uncertainty_blocks_synergy(self):
        # Audio scream with high uncertainty (e.g. lots of background noise)
        a_obs = self._audio_obs("audio_scream", 0.6, composite_unc=0.8)
        v_obs = self._video_obs("person", 0.6)
        
        self.fusion.feed_audio(a_obs)
        self.fusion.feed_video([v_obs])
        fused = self.fusion.flush()
        
        # Audio weight 0.5 * 0.6 + Video weight 0.5 * 0.6 = 0.6 base score.
        # If synergy was applied (+0.2), score = 0.8 > threshold 0.7.
        # But uncertainty is 0.8 > 0.6 threshold, so synergy is blocked.
        # Score remains 0.6, which is < threshold 0.7.
        self.assertEqual(len(fused), 0)

    def test_low_uncertainty_allows_synergy(self):
        # Same setup, but low uncertainty
        a_obs = self._audio_obs("audio_scream", 0.6, composite_unc=0.2)
        v_obs = self._video_obs("person", 0.6)
        
        self.fusion.feed_audio(a_obs)
        self.fusion.feed_video([v_obs])
        fused = self.fusion.flush()
        
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0].label, "audio_scream_person_present")
        self.assertGreaterEqual(fused[0].score, 0.75) # 0.6 + 0.2 synergy

    def test_high_uncertainty_blocks_audio_only(self):
        # Gunshot is allowed audio-only
        a_obs = self._audio_obs("audio_gunshot", 0.9, composite_unc=0.9)
        
        self.fusion.feed_audio(a_obs)
        self.fusion.feed_video([])
        fused = self.fusion.flush()
        
        # Uncertainty > 0.6, should block the audio-only alert
        self.assertEqual(len(fused), 0)

if __name__ == "__main__":
    unittest.main()
