"""
End-to-End tests for Multimodal AI pipeline.
Verifies Video-only regression, Audio-only synthetic, Fusion, and Audio failure degradation.
"""
import unittest
from unittest.mock import MagicMock, patch

from src.logic.aggregator import AlertAggregator
from src.logic.multimodal_fusion import MultimodalFusion
from src.common.types import Observation
from datetime import datetime, timezone, timedelta

def _ts(offset_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")

class TestE2EMultimodal(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.webhook_sender = MagicMock()
        self.aggregator = AlertAggregator(
            k=2, n=5, cooldown_s=30
        )
        self.aggregator.add_alert_callback(self.webhook_sender.send)
        
        # Enable Fusion
        self.fusion_engine = MultimodalFusion(
            camera_id="test_cam",
            cfg={
                "enabled": True,
                "audio_weight": 0.45,
                "video_weight": 0.55,
                "fused_alert_threshold": 0.72,
                "allow_audio_only_high_risk": True,
                "cooldown_s": { "generic_multimodal": 1 }
            }
        )

    def test_video_only_regression(self):
        """Test 1: video-only regression. Confirm old alerts work without audio."""
        obs = Observation(
            ts_utc=_ts(),
            camera_id="test_cam",
            lane="weapon_yolo",
            score=0.85,
            trigger=True,
            bbox=[0,0,10,10],
            label="gun"
        )
        # Feed video twice to meet K=2 threshold
        self.fusion_engine.feed_video([obs])
        self.aggregator.process_observation(obs)
        
        self.fusion_engine.feed_video([obs])
        self.aggregator.process_observation(obs)
        
        self.assertTrue(self.webhook_sender.send.called)
        alert = self.webhook_sender.send.call_args[0][0]
        self.assertEqual(alert.type, "WEAPON_DETECTED")

    def test_audio_only_synthetic(self):
        """Test 2: audio-only synthetic. Confirm audio_anomaly incident created."""
        obs = Observation(
            ts_utc=_ts(),
            camera_id="test_cam",
            lane="audio_anomaly",
            score=0.90,
            trigger=True,
            label="audio_gunshot",
            debug={"modality": "audio"}
        )
        # Audio anomaly lane bypasses video aggregator directly or via fusion allowed list
        # Since fusion allow_audio_only_high_risk=True, we process via fusion.
        self.fusion_engine.feed_audio(obs)
        self.fusion_engine.feed_video([]) # trigger fusion
        fused_list = self.fusion_engine.flush()
        print("FUSED LIST AUDIO ONLY:", fused_list)
        
        for f in fused_list:
            self.aggregator.process_observation(f)
            self.aggregator.process_observation(f)
        
        self.assertTrue(self.webhook_sender.send.called)
        alert = self.webhook_sender.send.call_args[0][0]
        self.assertEqual(alert.type, "AUDIO_ANOMALY")
        self.assertEqual(alert.label, "audio_gunshot_audio_only")

    def test_fusion(self):
        """Test 3: fusion. Confirm video_audio_fusion observation."""
        a_obs = Observation(
            ts_utc=_ts(),
            camera_id="test_cam",
            lane="audio_anomaly",
            score=0.85,
            trigger=True,
            label="audio_scream",
            debug={"modality": "audio"}
        )
        v_obs = Observation(
            ts_utc=_ts(),
            camera_id="test_cam",
            lane="rt_detr",
            score=0.90,
            trigger=True,
            label="person"
        )
        self.fusion_engine.feed_audio(a_obs)
        self.fusion_engine.feed_video([v_obs])
        fused_list = self.fusion_engine.flush()
        print("FUSED LIST FUSION:", fused_list)
        
        for f in fused_list:
            self.aggregator.process_observation(f)
            self.aggregator.process_observation(f) # hit K=2
        
        self.assertTrue(self.webhook_sender.send.called)
        alert = self.webhook_sender.send.call_args[0][0]
        self.assertEqual(alert.type, "AUDIO_ANOMALY")
        self.assertEqual(alert.label, "audio_scream_person_present")

    def test_audio_failure_degradation(self):
        """Test 4: audio failure degradation. Confirm video remains running."""
        # Simulated by passing None or no audio, video still processes
        v_obs = Observation(
            ts_utc=_ts(),
            camera_id="test_cam",
            lane="weapon_yolo",
            score=0.85,
            trigger=True,
            bbox=[0,0,10,10],
            label="gun"
        )
        # Audio thread crashed, no feed_audio called
        self.fusion_engine.feed_video([v_obs])
        self.aggregator.process_observation(v_obs)
        self.fusion_engine.feed_video([v_obs])
        self.aggregator.process_observation(v_obs)
        
        self.assertTrue(self.webhook_sender.send.called)
        alert = self.webhook_sender.send.call_args[0][0]
        self.assertEqual(alert.type, "WEAPON_DETECTED")

if __name__ == "__main__":
    unittest.main()
