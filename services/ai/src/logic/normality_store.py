import json
import logging
import math
import os
import threading
from typing import Dict, Any, Tuple

from src.common.log import setup_logger

_CRITICAL_LABELS = {
    "audio_scream",
    "audio_gunshot",
    "audio_explosion",
    "audio_glass_break",
    "audio_alarm",
    "audio_fire_alarm",
    "audio_vehicle_crash",
}

_BACKGROUND_LABELS = {
    "audio_train",
    "audio_vehicle",
    "audio_engine",
    "audio_traffic",
    "audio_wind",
    "audio_rain",
    "audio_crowd",
    "audio_air_conditioner",
}

class NormalityStore:
    """
    Maintains a persistent exponential moving average (EMA) of ambient audio scores
    per camera to establish baseline normality profiles.
    """
    def __init__(self, persist_path: str = "/app/data/normality/normality_profiles_v1.json", ema_alpha: float = 0.05, logger: logging.Logger = None):
        self.persist_path = persist_path
        self.ema_alpha = ema_alpha
        self.logger = logger or setup_logger("NormalityStore")
        
        # camera_id -> label -> {"mean": float, "var": float, "count": int}
        self.profiles: Dict[str, Dict[str, Dict[str, float]]] = {}
        self._lock = threading.RLock()
        
        self._load()

    def _load(self):
        if not os.path.exists(self.persist_path):
            self.logger.info(f"No existing normality profile found at {self.persist_path}. Starting fresh.")
            return
            
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                self.profiles = json.load(f)
            self.logger.info(f"Loaded normality profiles for {len(self.profiles)} cameras.")
        except Exception as e:
            self.logger.error(f"Failed to load normality profiles from {self.persist_path}: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            with open(self.persist_path, "w", encoding="utf-8") as f:
                json.dump(self.profiles, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save normality profiles to {self.persist_path}: {e}")

    def update_baseline(self, camera_id: str, label: str, score: float) -> None:
        """
        Update the normality profile. Will refuse to update for critical labels.
        """
        if label in _CRITICAL_LABELS:
            return  # Never learn critical labels as background
            
        # Only learn from explicitly defined background-like labels
        # If we want to learn generic non-critical labels, we could remove this check,
        # but the spec says "Only auto-suppress background-like labels such as..."
        if label not in _BACKGROUND_LABELS:
            return

        with self._lock:
            if camera_id not in self.profiles:
                self.profiles[camera_id] = {}
                
            if label not in self.profiles[camera_id]:
                self.profiles[camera_id][label] = {"mean": score, "var": 0.0, "count": 1}
            else:
                stats = self.profiles[camera_id][label]
                # EMA update
                diff = score - stats["mean"]
                stats["mean"] += self.ema_alpha * diff
                # Incremental variance update
                stats["var"] = (1 - self.ema_alpha) * (stats["var"] + self.ema_alpha * (diff ** 2))
                stats["count"] += 1
                
            # Naive flush on every update is expensive, maybe flush periodically?
            # For this MVP, we save immediately or we can defer it.
            # To avoid disk thrashing, we save every 100 updates.
            # Actually, let's just implement a simple counter.
            if not hasattr(self, "_save_counter"):
                self._save_counter = 0
            self._save_counter += 1
            if self._save_counter % 50 == 0:
                self._save()

    def force_save(self):
        with self._lock:
            self._save()

    def get_adjusted_score(self, camera_id: str, label: str, raw_score: float) -> Tuple[float, float, float, float]:
        """
        Returns (adjusted_score, mean, std, z_score).
        If the label is critical, adjusted_score is identical to raw_score.
        """
        if label in _CRITICAL_LABELS or label not in _BACKGROUND_LABELS:
            return raw_score, 0.0, 0.0, 0.0
            
        with self._lock:
            cam_profile = self.profiles.get(camera_id, {})
            stats = cam_profile.get(label)
            
            if not stats or stats["count"] < 10:
                # Not enough data to confidently suppress
                return raw_score, 0.0, 0.0, 0.0
                
            mean = stats["mean"]
            std = math.sqrt(stats["var"])
            
            # Z-score: how many standard deviations above the mean is this?
            # If standard deviation is 0, we use a small epsilon
            z_score = (raw_score - mean) / (std + 1e-5)
            
            # Simple heuristic: if score is very close to mean, suppress it heavily.
            # If it's a huge spike (z_score > 3), suppress it less.
            if raw_score <= mean:
                adjusted = 0.0
            else:
                # E.g. raw=0.8, mean=0.6. Diff=0.2.
                adjusted = max(0.0, raw_score - mean)
                
            return adjusted, mean, std, z_score
