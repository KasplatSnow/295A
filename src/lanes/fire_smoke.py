"""
Fire/Smoke detection lane using YOLO
"""
import numpy as np
from typing import Dict, Any
from pathlib import Path
from ultralytics import YOLO
from .base import BaseLane
from ..common.types import Observation
from ..common.log import setup_logger
from ..runtime.device import select_device


class FireSmokeLane(BaseLane):
    """Detects fire and smoke"""
    
    def __init__(self, lane_name: str, camera_id: str, models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.model = None
        self.logger = setup_logger(f"FireSmokeLane-{camera_id}")
    
    def init(self):
        """Initialize YOLO model"""
        try:
            model_cfg = self.models_cfg['models']['fire_smoke_detector']
            weights_path = model_cfg['weights']
            
            # Resolve relative path
            if not Path(weights_path).is_absolute():
                base_path = Path(__file__).parent.parent.parent
                weights_path = (base_path / weights_path).resolve()
            
            if not Path(weights_path).exists():
                self.logger.warning(f"Fire/smoke model not found at {weights_path}, using placeholder")
                # Use a generic YOLO model as placeholder
                weights_path = Path(__file__).parent.parent.parent / ".." / "yolov8n.pt"
                if not weights_path.exists():
                    raise FileNotFoundError(f"No YOLO model available")
            
            self.logger.info(f"Loading fire/smoke model from {weights_path}")
            self.model = YOLO(str(weights_path))
            
            dev = select_device(self.models_cfg)
            actual_device = dev.torch_device
            self._ul_device = 0 if dev.torch_gpu else "cpu"
            
            self.model.to(actual_device)
            self.conf_threshold = model_cfg.get('conf', 0.20)
            self._initialized = True
            self.logger.info(f"Fire/smoke detector initialized on {actual_device}")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize fire/smoke detector: {e}")
            raise
    
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        """Run fire/smoke detection"""
        if not self._initialized:
            self.init()
        
        try:
            # Run YOLO detection
            results = self.model(frame_bgr, verbose=False, conf=self.conf_threshold,
                                device=self._ul_device,
                                half=isinstance(self._ul_device, int))  # FP16 on GPU
            
            # Extract highest confidence detection
            max_score = 0.0
            best_bbox = None
            
            if len(results) > 0 and results[0].boxes is not None:
                boxes = results[0].boxes
                for i in range(len(boxes)):
                    conf = float(boxes.conf[i])
                    if conf > max_score:
                        max_score = conf
                        box = boxes.xyxy[i].cpu().numpy()
                        best_bbox = [int(b) for b in box.tolist()]
            
            trigger = max_score > self.conf_threshold
            
            return Observation(
                ts_utc=ts_utc,
                camera_id=self.camera_id,
                lane=self.lane_name,
                score=max_score,
                trigger=trigger,
                bbox=best_bbox,
                label="fire_smoke" if trigger else None,
                debug={"detections": len(results[0].boxes) if len(results) > 0 and results[0].boxes is not None else 0}
            )
            
        except Exception as e:
            self.logger.error(f"Inference error: {e}")
            return Observation(
                ts_utc=ts_utc,
                camera_id=self.camera_id,
                lane=self.lane_name,
                score=0.0,
                trigger=False,
                debug={"error": str(e)}
            )
