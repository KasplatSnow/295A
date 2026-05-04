"""
Configuration loader
"""
import hashlib
import hmac
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
import yaml

from .runtime import get_backend_config_sync_base


class Config:
    """Configuration manager"""
    
    def __init__(self, config_dir: str = "configs"):
        self.config_dir = Path(config_dir)
        self._cameras = None
        self._zones = None
        self._models = None
        self._policy = None

        self._backend_sync_base = get_backend_config_sync_base().rstrip("/")
        self._sync_token = os.getenv("AI_WEBHOOK_TOKEN", "")
        self._sync_secret = os.getenv("AI_WEBHOOK_SECRET", "")

    def _require_backend_snapshot(self, domain: str) -> None:
        raise RuntimeError(
            f"Canonical {domain} config snapshot is unavailable from backend. "
            "Mutable runtime config must come from canonical backend snapshots."
        )

    def _fetch_cameras_snapshot_from_backend(self) -> Optional[Dict[str, Any]]:
        if not self._backend_sync_base:
            return None

        headers: Dict[str, str] = {}
        if self._sync_token:
            headers["X-AI-WEBHOOK-TOKEN"] = self._sync_token
        elif self._sync_secret:
            signature = hmac.new(self._sync_secret.encode(), b"", hashlib.sha256).hexdigest()
            headers["X-Vigilzone-Signature"] = f"sha256={signature}"

        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self._backend_sync_base}/cameras/snapshot/", headers=headers)
            if not resp.is_success:
                return None

            payload = resp.json()
            if not isinstance(payload, dict):
                return None

            cameras = payload.get("cameras")
            zones = payload.get("zones")
            if not isinstance(cameras, list) or not isinstance(zones, dict):
                return None

            return {
                "cameras": cameras,
                "zones": zones,
            }
        except Exception:
            return None

    def _fetch_policy_snapshot_from_backend(self) -> Optional[Dict[str, Any]]:
        if not self._backend_sync_base:
            return None

        headers: Dict[str, str] = {}
        if self._sync_token:
            headers["X-AI-WEBHOOK-TOKEN"] = self._sync_token
        elif self._sync_secret:
            signature = hmac.new(self._sync_secret.encode(), b"", hashlib.sha256).hexdigest()
            headers["X-Vigilzone-Signature"] = f"sha256={signature}"

        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self._backend_sync_base}/policy/snapshot/", headers=headers)
            if not resp.is_success:
                return None

            payload = resp.json()
            if not isinstance(payload, dict):
                return None

            policy = payload.get("policy", {})
            if isinstance(policy, dict):
                return policy
        except Exception:
            return None

        return None
    
    def load_cameras(self) -> List[Dict[str, Any]]:
        """Load camera configurations"""
        if self._cameras is None:
            snapshot = self._fetch_cameras_snapshot_from_backend()
            if snapshot is not None:
                self._cameras = snapshot.get("cameras", [])
                self._zones = snapshot.get("zones", {})
            else:
                self._require_backend_snapshot("camera")
        return self._cameras
    
    def load_zones(self) -> Dict[str, List[Dict[str, Any]]]:
        """Load zone configurations"""
        if self._zones is None:
            if self._cameras is None:
                self.load_cameras()
            if self._zones is None:
                self._require_backend_snapshot("zone")
        return self._zones
    
    def load_models(self) -> Dict[str, Any]:
        """Load model configurations"""
        if self._models is None:
            with open(self.config_dir / "models.yaml", "r") as f:
                self._models = yaml.safe_load(f)
        return self._models
    
    def get_camera_config(self, camera_id: str) -> Dict[str, Any]:
        """Get configuration for a specific camera"""
        cameras = self.load_cameras()
        for cam in cameras:
            if cam["camera_id"] == camera_id:
                return cam
        raise ValueError(f"Camera {camera_id} not found in configuration")
    
    def get_zones_for_camera(self, camera_id: str) -> List[Dict[str, Any]]:
        """Get zones for a specific camera"""
        zones = self.load_zones()
        return zones.get(camera_id, [])

    def load_policy(self) -> Dict[str, Any]:
        """Load identity policy configuration"""
        if self._policy is None:
            snapshot = self._fetch_policy_snapshot_from_backend()
            if snapshot is not None:
                self._policy = snapshot
            else:
                self._require_backend_snapshot("policy")
        return self._policy
