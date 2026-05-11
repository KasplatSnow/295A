import unittest

try:
    import torch
    from src.logic.learned_fusion import LearnedFusionHead, LearnedFusionModel
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

@unittest.skipIf(not HAS_TORCH, "PyTorch not installed in this environment")
class TestLearnedFusion(unittest.TestCase):
    def test_model_forward(self):
        model = LearnedFusionModel(hidden_dim=32)
        features = {
            "audio_score_raw": 0.9,
            "audio_score_adjusted": 0.8,
            "audio_uncertainty": 0.1,
            "audio_label": "audio_scream",
            "video_score": 0.7,
            "video_label": "person",
            "video_lane": "yolo_weapon",
            "time_delta_ms": 10.0,
            "normality_mean": 0.1,
            "normality_std": 0.05,
            "normality_z": 2.0,
            "hour_sin": 1.0,
            "hour_cos": 0.0,
        }
        
        score = model(features)
        
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        
    def test_head_predict(self):
        head = LearnedFusionHead()
        self.assertFalse(head.is_trained) # Should be false as no checkpoint was provided
        
        features = {
            "audio_score_raw": 0.5,
        }
        
        score = head.predict(features)
        self.assertIsInstance(score, float)

if __name__ == "__main__":
    unittest.main()
