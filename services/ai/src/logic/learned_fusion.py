import math
import hashlib
from typing import Dict, Any, Optional

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    nn = object
    
if HAS_TORCH:
    class LearnedFusionModel(nn.Module):
        """
        A small MLP designed to predict a fused anomaly score based on multimodal features.
        Currently used in shadow mode.
        """
        def __init__(self, hidden_dim: int = 64, num_buckets: int = 1024):
            super().__init__()
            self.num_buckets = num_buckets
            
            # Categorical embeddings (using hashing trick to avoid strict vocabularies)
            self.audio_label_emb = nn.Embedding(num_buckets, 16)
            self.video_label_emb = nn.Embedding(num_buckets, 16)
            self.video_lane_emb = nn.Embedding(128, 8)
            
            # Scalar features count:
            # audio_score_raw (1)
            # audio_score_adjusted (1)
            # audio_uncertainty (1)
            # video_score (1)
            # time_delta_ms (1)
            # normality_mean (1)
            # normality_std (1)
            # normality_z (1)
            # hour_sin (1)
            # hour_cos (1)
            # Total = 10 scalar features
            num_scalar_features = 10
            
            input_dim = 16 + 16 + 8 + num_scalar_features
            
            self.fc1 = nn.Linear(input_dim, hidden_dim)
            self.fc2 = nn.Linear(hidden_dim, hidden_dim // 2)
            self.fc3 = nn.Linear(hidden_dim // 2, 1)
            
        def _hash_label(self, label: str, buckets: int) -> int:
            if not label:
                return 0
            h = int(hashlib.md5(label.encode("utf-8")).hexdigest(), 16)
            return h % buckets

        def forward(self, features: Dict[str, Any]) -> float:
            """
            Forward pass for a single observation dictionary.
            Returns a float score in [0.0, 1.0].
            """
            # Extract and hash categorical
            audio_label_idx = self._hash_label(features.get("audio_label", ""), self.num_buckets)
            video_label_idx = self._hash_label(features.get("video_label", ""), self.num_buckets)
            video_lane_idx = self._hash_label(features.get("video_lane", ""), 128)
            
            # Device management
            device = next(self.parameters()).device
            
            a_lbl = torch.tensor([audio_label_idx], dtype=torch.long, device=device)
            v_lbl = torch.tensor([video_label_idx], dtype=torch.long, device=device)
            v_lane = torch.tensor([video_lane_idx], dtype=torch.long, device=device)
            
            a_emb = self.audio_label_emb(a_lbl) # [1, 16]
            v_emb = self.video_label_emb(v_lbl) # [1, 16]
            lane_emb = self.video_lane_emb(v_lane) # [1, 8]
            
            # Extract scalars
            scalars = [
                float(features.get("audio_score_raw", 0.0)),
                float(features.get("audio_score_adjusted", 0.0)),
                float(features.get("audio_uncertainty", 0.0)),
                float(features.get("video_score", 0.0)),
                float(features.get("time_delta_ms", 0.0)) / 1000.0, # Normalize roughly
                float(features.get("normality_mean", 0.0)),
                float(features.get("normality_std", 0.0)),
                float(features.get("normality_z", 0.0)) / 10.0, # Normalize roughly
                float(features.get("hour_sin", 0.0)),
                float(features.get("hour_cos", 0.0)),
            ]
            
            s_tensor = torch.tensor([scalars], dtype=torch.float32, device=device) # [1, 10]
            
            x = torch.cat([a_emb, v_emb, lane_emb, s_tensor], dim=1) # [1, 50]
            
            x = F.relu(self.fc1(x))
            x = F.relu(self.fc2(x))
            logits = self.fc3(x) # [1, 1]
            
            score = torch.sigmoid(logits).item()
            return round(score, 4)

    class LearnedFusionHead:
        """
        Wrapper for the learned fusion model.
        Handles checkpoint loading and graceful degradation.
        """
        def __init__(self, checkpoint_path: Optional[str] = None):
            self.model = LearnedFusionModel()
            self.is_trained = False
            
            if checkpoint_path:
                try:
                    self.model.load_state_dict(torch.load(checkpoint_path, map_location="cpu"))
                    self.is_trained = True
                except Exception as e:
                    # Failing to load checkpoint is fine in MVP, we just run the initialized weights
                    print(f"Failed to load learned fusion checkpoint: {e}. Using random weights in shadow mode.")
                    
            self.model.eval()

        def predict(self, features: Dict[str, Any]) -> float:
            with torch.no_grad():
                return self.model(features)
else:
    # Dummy mock for environments without PyTorch
    class LearnedFusionHead:
        def __init__(self, checkpoint_path: Optional[str] = None):
            self.is_trained = False
        def predict(self, features: Dict[str, Any]) -> float:
            return 0.5
