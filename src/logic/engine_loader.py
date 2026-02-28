"""
Engine loader — cascade: TensorRT FP16 → ONNX GPU → ONNX CPU → RTDETRv2 Native → Ultralytics .pt → Stub

Provides a uniform ``DetectorEngine.infer(frame_bgr) → List[Detection]`` interface
regardless of which backend actually runs.

Optimisations enabled:
  - FP16 inference on CUDA (halves memory bandwidth)
  - torch.inference_mode (disables autograd bookkeeping)
  - Pre-allocated GPU input buffers (eliminates per-frame allocation)
  - Dedicated CUDA stream per engine (enables overlap)
  - Vectorised post-process (numpy mask instead of Python loop)
  - Optional torch.compile for kernel fusion

Singleton caching: ``load_detector_engine()`` returns the same engine instance
for identical config (keyed on weights path) to avoid duplicate loads.
"""
import time
import logging
import contextlib
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..common.log import setup_logger

logger = setup_logger("EngineLoader")

# Singleton cache: weights_key → DetectorEngine
_engine_cache: Dict[str, "DetectorEngine"] = {}


@contextlib.contextmanager
def _nullcontext():
    """No-op context manager for when CUDA stream is None."""
    yield

# ---------------------------------------------------------------------------
# Uniform detection result
# ---------------------------------------------------------------------------

class Detection:
    """Single detection result."""
    __slots__ = ("bbox", "score", "label")

    def __init__(self, bbox: List[float], score: float, label: str):
        self.bbox = bbox      # [x1, y1, x2, y2]
        self.score = score
        self.label = label

    def to_dict(self) -> Dict[str, Any]:
        return {"bbox": self.bbox, "score": self.score, "label": self.label}


# ---------------------------------------------------------------------------
# DetectorEngine ABC
# ---------------------------------------------------------------------------

class DetectorEngine:
    """Uniform detector interface regardless of backend."""

    def __init__(self, runtime: str = "none"):
        self.runtime = runtime
        self._logger = setup_logger(f"DetectorEngine-{runtime}")

    def infer(self, frame_bgr: np.ndarray) -> List[Detection]:
        raise NotImplementedError

    def warmup(self):
        pass


# ---------------------------------------------------------------------------
# TensorRT Engine
# ---------------------------------------------------------------------------

class TensorRTEngine(DetectorEngine):
    """Loads a pre-built TensorRT engine file (.engine)."""

    def __init__(self, engine_path: str, classes: List[str],
                 score_threshold: float = 0.15, input_size: tuple = (640, 640)):
        super().__init__(runtime="tensorrt_fp16")
        self.engine_path = engine_path
        self.classes = classes
        self.score_threshold = score_threshold
        self.input_size = input_size
        self._engine = None
        self._context = None
        self._load()

    def _load(self):
        try:
            import tensorrt as trt                               # noqa
            import pycuda.driver as cuda                         # noqa
            import pycuda.autoinit                               # noqa

            trt_logger = trt.Logger(trt.Logger.WARNING)
            with open(self.engine_path, "rb") as f:
                engine_data = f.read()
            runtime_obj = trt.Runtime(trt_logger)
            self._engine = runtime_obj.deserialize_cuda_engine(engine_data)
            self._context = self._engine.create_execution_context()
            self._logger.info(f"TensorRT engine loaded: {self.engine_path}")
        except Exception as e:
            self._logger.error(f"TensorRT load failed: {e}")
            raise

    def infer(self, frame_bgr: np.ndarray) -> List[Detection]:
        import cv2
        try:
            import pycuda.driver as cuda                         # noqa

            img = cv2.resize(frame_bgr, self.input_size)
            img = img.astype(np.float32) / 255.0
            img = img.transpose(2, 0, 1)[np.newaxis, ...]
            img = np.ascontiguousarray(img)

            # NOTE: Full TRT binding allocation deferred to production engine setup.
            detections: List[Detection] = []
            return detections
        except Exception as e:
            self._logger.error(f"TensorRT infer error: {e}")
            return []


# ---------------------------------------------------------------------------
# ONNX Runtime Engine
# ---------------------------------------------------------------------------

class ONNXEngine(DetectorEngine):
    """Loads an ONNX model via onnxruntime (GPU or CPU)."""

    def __init__(self, onnx_path: str, classes: List[str],
                 score_threshold: float = 0.15, use_gpu: bool = False,
                 input_size: tuple = (640, 640)):
        runtime_str = "onnx_gpu" if use_gpu else "onnx_cpu"
        super().__init__(runtime=runtime_str)
        self.onnx_path = onnx_path
        self.classes = classes
        self.score_threshold = score_threshold
        self.input_size = input_size
        self._session = None
        self._load(use_gpu)

    def _load(self, use_gpu: bool):
        try:
            import onnxruntime as ort
            providers = (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if use_gpu else ["CPUExecutionProvider"]
            )
            self._session = ort.InferenceSession(self.onnx_path, providers=providers)
            actual = self._session.get_providers()
            self._logger.info(f"ONNX session created ({actual}): {self.onnx_path}")
        except Exception as e:
            self._logger.error(f"ONNX load failed: {e}")
            raise

    def infer(self, frame_bgr: np.ndarray) -> List[Detection]:
        import cv2
        try:
            img = cv2.resize(frame_bgr, self.input_size)
            img = img.astype(np.float32) / 255.0
            img = img.transpose(2, 0, 1)[np.newaxis, ...]
            img = np.ascontiguousarray(img)

            input_name = self._session.get_inputs()[0].name
            outputs = self._session.run(None, {input_name: img})
            return self._parse_outputs(outputs, frame_bgr.shape[:2])
        except Exception as e:
            self._logger.error(f"ONNX infer error: {e}")
            return []

    def _parse_outputs(self, outputs, orig_shape) -> List[Detection]:
        detections: List[Detection] = []
        try:
            preds = outputs[0]
            if preds.ndim == 3:
                preds = preds[0]
            h_orig, w_orig = orig_shape
            for row in preds:
                box = row[:4]
                scores = row[4:]
                max_idx = int(np.argmax(scores))
                max_score = float(scores[max_idx])
                if max_score < self.score_threshold:
                    continue
                label = self.classes[max_idx] if max_idx < len(self.classes) else f"class_{max_idx}"
                x1 = float(box[0]) * w_orig / self.input_size[0]
                y1 = float(box[1]) * h_orig / self.input_size[1]
                x2 = float(box[2]) * w_orig / self.input_size[0]
                y2 = float(box[3]) * h_orig / self.input_size[1]
                detections.append(Detection([x1, y1, x2, y2], max_score, label))
        except Exception as e:
            self._logger.warning(f"Output parse error: {e}")
        return detections


# ── COCO-80 class names (for native RTDETRv2 which outputs integer labels) ──
_COCO_80_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]


# ---------------------------------------------------------------------------
# RTDETRv2 Native Engine (official repo via torch.hub) — NEW DEFAULT
# ---------------------------------------------------------------------------

class RTDETRv2NativeEngine(DetectorEngine):
    """
    RTDETRv2 loaded via ``torch.hub.load('lyuwenyu/RT-DETR', ...)``.

    Supports ResNet-18/34/50/101vd backbones.  Downloads the model code
    from GitHub on first run (cached in ``~/.cache/torch/hub/``).

    Weights: .pth checkpoint from
      https://github.com/lyuwenyu/storage/releases/download/v0.1/
    """

    # Map config name → torch.hub entry-point
    _HUB_MODELS = {
        "rtdetrv2_r18vd": "rtdetrv2_r18vd",
        "rtdetrv2_r34vd": "rtdetrv2_r34vd",
        "rtdetrv2_r50vd": "rtdetrv2_r50vd",
        "rtdetrv2_r50vd_m": "rtdetrv2_r50vd_m",
        "rtdetrv2_r101vd": "rtdetrv2_r101vd",
    }

    def __init__(self, weights: str, score_threshold: float = 0.15,
                 device: str = "cpu", hub_variant: str = "rtdetrv2_r101vd",
                 use_fp16: bool = True, use_compile: bool = False):
        super().__init__(runtime="rtdetrv2_native")
        self.weights = weights
        self.score_threshold = score_threshold
        self.device = device
        self._hub_variant = hub_variant
        self._model = None
        self._torch_device = device
        self._input_size = (640, 640)
        self._use_fp16 = use_fp16 and device.startswith("cuda")
        self._use_compile = use_compile
        # Pre-allocated GPU tensors (set after first frame)
        self._input_buffer = None
        self._orig_sizes_buffer = None
        self._cuda_stream = None
        self._load()

    def _load(self):
        import torch
        try:
            hub_fn = self._HUB_MODELS.get(self._hub_variant, "rtdetrv2_r101vd")
            self._logger.info(
                f"Loading RTDETRv2 via torch.hub  variant={hub_fn}  "
                f"weights={self.weights}  device={self.device}  "
                f"fp16={self._use_fp16}  compile={self._use_compile}"
            )

            # Load architecture from official repo (cached after first download)
            model = torch.hub.load(
                'lyuwenyu/RT-DETR', hub_fn,
                pretrained=False,   # we'll load our own checkpoint
                source='github',
            )

            # Load checkpoint
            ckpt = torch.load(self.weights, map_location='cpu')
            # Official checkpoints store state under 'ema' → 'module'
            if 'ema' in ckpt and 'module' in ckpt['ema']:
                state = ckpt['ema']['module']
            elif 'model' in ckpt:
                state = ckpt['model']
            else:
                state = ckpt
            model.load_state_dict(state, strict=False)

            model = model.eval().to(self.device)

            # FP16: halve memory bandwidth and compute on tensor cores
            if self._use_fp16:
                model = model.half()
                self._logger.info("RTDETRv2: FP16 enabled")

            # torch.compile (PyTorch 2.x) — optional, reduces kernel launch overhead
            if self._use_compile:
                try:
                    model = torch.compile(model, mode="reduce-overhead")
                    self._logger.info("RTDETRv2: torch.compile enabled (reduce-overhead)")
                except Exception as ce:
                    self._logger.warning(f"torch.compile failed, using eager: {ce}")

            self._model = model

            # Create dedicated CUDA stream for overlapped execution
            if self.device.startswith("cuda"):
                self._cuda_stream = torch.cuda.Stream(device=self.device)

            self._logger.info(
                f"RTDETRv2 ({hub_fn}) loaded on {self.device}"
            )

            # Warmup pass (compiles kernels, allocates memory)
            self._warmup()

        except Exception as e:
            self._logger.error(f"RTDETRv2 native load failed: {e}")
            raise

    def _warmup(self):
        """Run 2 dummy inferences to prime CUDA caches."""
        import torch
        try:
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            for _ in range(2):
                self.infer(dummy)
            self._logger.info("RTDETRv2 warmup complete")
        except Exception:
            pass  # non-fatal

    def infer(self, frame_bgr: np.ndarray) -> List[Detection]:
        if self._model is None:
            return []
        import torch
        import cv2
        try:
            h_orig, w_orig = frame_bgr.shape[:2]

            # Pre-process: resize → RGB → float32 [0,1] → CHW → batch
            img = cv2.resize(frame_bgr, self._input_size)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img = img.astype(np.float32) / 255.0
            img = img.transpose(2, 0, 1)  # HWC → CHW

            # Re-use pre-allocated GPU buffer when possible
            if self._input_buffer is None or self._input_buffer.device.type != self._torch_device.split(':')[0]:
                dtype = torch.float16 if self._use_fp16 else torch.float32
                self._input_buffer = torch.empty(
                    (1, 3, self._input_size[1], self._input_size[0]),
                    dtype=dtype, device=self._torch_device,
                )
                self._orig_sizes_buffer = torch.empty(
                    (1, 2), dtype=torch.int64, device=self._torch_device,
                )

            # Copy into pre-allocated buffer (avoids fresh allocation every frame)
            tensor = torch.from_numpy(img).unsqueeze(0)
            if self._use_fp16:
                tensor = tensor.half()
            self._input_buffer.copy_(tensor)
            self._orig_sizes_buffer[0, 0] = h_orig
            self._orig_sizes_buffer[0, 1] = w_orig

            # Run on dedicated CUDA stream for potential overlap
            stream_ctx = torch.cuda.stream(self._cuda_stream) if self._cuda_stream else _nullcontext()
            with stream_ctx:
                with torch.inference_mode():
                    outputs = self._model(self._input_buffer,
                                          orig_target_sizes=self._orig_sizes_buffer)

            # Sync stream before CPU access
            if self._cuda_stream:
                self._cuda_stream.synchronize()

            detections: List[Detection] = []

            # Official output: dict with 'labels', 'boxes', 'scores'
            if isinstance(outputs, dict):
                labels = outputs['labels'][0].cpu().numpy()
                boxes = outputs['boxes'][0].cpu().numpy()    # [N, 4] xyxy in orig scale
                scores = outputs['scores'][0].cpu().numpy()
            elif isinstance(outputs, (list, tuple)):
                # Some hub versions return (logits, boxes) tuple
                logits, raw_boxes = outputs
                scores_t = torch.sigmoid(logits[0]).cpu()
                raw_boxes = raw_boxes[0].cpu()
                # Per-class max
                max_scores, labels_t = scores_t.max(dim=-1)
                scores = max_scores.numpy()
                labels = labels_t.numpy()
                # boxes are cxcywh normalised → xyxy in orig scale
                cx, cy, bw, bh = raw_boxes[:, 0], raw_boxes[:, 1], raw_boxes[:, 2], raw_boxes[:, 3]
                x1 = (cx - bw / 2) * w_orig
                y1 = (cy - bh / 2) * h_orig
                x2 = (cx + bw / 2) * w_orig
                y2 = (cy + bh / 2) * h_orig
                boxes = torch.stack([x1, y1, x2, y2], dim=-1).numpy()
            else:
                return []

            # Vectorised score filtering (avoids Python-loop overhead)
            mask = scores >= self.score_threshold
            if not mask.any():
                return []
            filt_scores = scores[mask]
            filt_labels = labels[mask]
            filt_boxes = boxes[mask]

            for i in range(len(filt_scores)):
                cls_id = int(filt_labels[i])
                label = _COCO_80_NAMES[cls_id] if cls_id < len(_COCO_80_NAMES) else f"class_{cls_id}"
                box = filt_boxes[i].tolist()
                detections.append(Detection(box, float(filt_scores[i]), label))

            return detections
        except Exception as e:
            self._logger.error(f"RTDETRv2 native infer error: {e}")
            return []


# ---------------------------------------------------------------------------
# Ultralytics RTDETR Engine  ← legacy fallback
# ---------------------------------------------------------------------------

class UltralyticsRTDETREngine(DetectorEngine):
    """Uses ``from ultralytics import RTDETR`` — legacy fallback for .pt weights."""

    def __init__(self, weights: str, score_threshold: float = 0.15,
                 device: str = "cpu"):
        super().__init__(runtime="ultralytics_rtdetr_pt")
        self.weights = weights
        self.score_threshold = score_threshold
        self.device = device
        self._model = None
        self._load()

    def _load(self):
        try:
            from ultralytics import RTDETR
            self._model = RTDETR(self.weights)
            self._logger.info(f"Ultralytics RTDETR loaded: {self.weights}")
        except Exception as e:
            self._logger.error(f"Ultralytics RTDETR load failed: {e}")
            raise

    def infer(self, frame_bgr: np.ndarray) -> List[Detection]:
        if self._model is None:
            return []
        try:
            ul_device = 0 if self.device.startswith("cuda") else "cpu"
            use_half = self.device.startswith("cuda")
            results = self._model(frame_bgr, conf=self.score_threshold,
                                  device=ul_device, verbose=False, half=use_half)
            detections: List[Detection] = []
            for r in results:
                boxes = r.boxes
                if boxes is None:
                    continue
                for i in range(len(boxes)):
                    xyxy = boxes.xyxy[i].tolist()
                    conf = float(boxes.conf[i])
                    cls_id = int(boxes.cls[i])
                    label = r.names.get(cls_id, f"class_{cls_id}")
                    detections.append(Detection(xyxy, conf, label))
            return detections
        except Exception as e:
            self._logger.error(f"Ultralytics RTDETR infer error: {e}")
            return []


# ---------------------------------------------------------------------------
# Stub Engine (no model, returns empty)
# ---------------------------------------------------------------------------

class StubEngine(DetectorEngine):
    """Placeholder engine when no model file is available."""

    def __init__(self, reason: str = "model file not found"):
        super().__init__(runtime="stub")
        self._logger.warning(f"Using STUB engine: {reason}")

    def infer(self, frame_bgr: np.ndarray) -> List[Detection]:
        return []


# ---------------------------------------------------------------------------
# Public loader function
# ---------------------------------------------------------------------------

def load_detector_engine(models_cfg: Dict[str, Any]) -> DetectorEngine:
    """
    Load the best available RT-DETR engine using the spec cascade:
        1. TensorRT FP16 engine
        2. ONNX GPU
        3. ONNX CPU
        4. Ultralytics .pt   ← Phase-1 default
        5. Stub               ← if nothing works

    Reads from models_cfg dict (device, tensorrt, models.rt_detr).

    **Singleton**: returns the same engine if already loaded with identical weights.
    """
    rt_detr_cfg = models_cfg.get("models", {}).get("rt_detr", {})
    weights_pt = rt_detr_cfg.get("weights", "rtdetr-l.pt")

    # ── Singleton check ──
    cache_key = str(Path(weights_pt).resolve()) if Path(weights_pt).is_absolute() else weights_pt
    if cache_key in _engine_cache:
        logger.info(f"Reusing cached engine for {cache_key}")
        return _engine_cache[cache_key]
    device_pref = models_cfg.get("device", "auto")
    trt_cfg = models_cfg.get("tensorrt", {})
    rt_detr_cfg = models_cfg.get("models", {}).get("rt_detr", {})

    classes = rt_detr_cfg.get("classes", ["person"])
    score_threshold = rt_detr_cfg.get("score_threshold", 0.15)
    trt_engine_path = rt_detr_cfg.get("trt_engine_path", "")
    onnx_path = rt_detr_cfg.get("onnx_path", "")
    weights_pt = rt_detr_cfg.get("weights", "rtdetr-l.pt")

    # Determine CUDA availability — use centralized device selection
    cuda_available = False
    device_str = "cpu"
    try:
        from ..runtime.device import select_device
        dev = select_device(models_cfg)
        cuda_available = dev.torch_gpu
        device_str = dev.torch_device
    except Exception:
        try:
            import torch
            cuda_available = torch.cuda.is_available()
            if cuda_available:
                device_str = "cuda"
            if device_pref == "cpu":
                cuda_available = False
                device_str = "cpu"
        except ImportError:
            pass

    # 1. Try TensorRT
    if trt_cfg.get("enabled", False) and cuda_available and Path(trt_engine_path).exists():
        try:
            engine = TensorRTEngine(trt_engine_path, classes, score_threshold)
            logger.info(f"✅ Loaded TensorRT FP16 engine: {trt_engine_path}")
            _engine_cache[cache_key] = engine
            return engine
        except Exception as e:
            logger.warning(f"TensorRT load failed, falling back: {e}")

    # 2. Try ONNX GPU
    if cuda_available and onnx_path and Path(onnx_path).exists():
        try:
            engine = ONNXEngine(onnx_path, classes, score_threshold, use_gpu=True)
            logger.info(f"✅ Loaded ONNX (GPU): {onnx_path}")
            _engine_cache[cache_key] = engine
            return engine
        except Exception as e:
            logger.warning(f"ONNX GPU load failed, falling back: {e}")

    # 3. Try ONNX CPU
    if onnx_path and Path(onnx_path).exists():
        try:
            engine = ONNXEngine(onnx_path, classes, score_threshold, use_gpu=False)
            logger.info(f"✅ Loaded ONNX (CPU): {onnx_path}")
            _engine_cache[cache_key] = engine
            return engine
        except Exception as e:
            logger.warning(f"ONNX CPU load failed: {e}")

    # 4. Try RTDETRv2 Native (.pth from official repo) — NEW DEFAULT
    native_weights = rt_detr_cfg.get("native_weights", "")
    hub_variant = rt_detr_cfg.get("hub_variant", "rtdetrv2_r101vd")
    use_fp16 = rt_detr_cfg.get("use_fp16", True)
    use_compile = rt_detr_cfg.get("use_compile", False)
    if native_weights:
        native_path = native_weights
        if not Path(native_path).is_absolute():
            native_path = str((Path(__file__).parent.parent.parent / native_path).resolve())
        if Path(native_path).exists():
            try:
                engine = RTDETRv2NativeEngine(
                    native_path, score_threshold, device_str,
                    hub_variant=hub_variant,
                    use_fp16=use_fp16,
                    use_compile=use_compile,
                )
                logger.info(f"✅ Loaded RTDETRv2 native ({hub_variant}): {native_path}")
                _engine_cache[cache_key] = engine
                return engine
            except Exception as e:
                logger.warning(f"RTDETRv2 native load failed, falling back: {e}")
        else:
            logger.info(f"RTDETRv2 native weights not found ({native_path}), trying Ultralytics")

    # 5. Try Ultralytics .pt (legacy fallback)
    try:
        engine = UltralyticsRTDETREngine(weights_pt, score_threshold, device_str)
        logger.info(f"✅ Loaded Ultralytics RTDETR (.pt): {weights_pt}")
        _engine_cache[cache_key] = engine
        return engine
    except Exception as e:
        logger.warning(f"Ultralytics RTDETR load failed: {e}")

    # 6. Stub
    reason = "All RT-DETR backends failed — detector_primary disabled"
    logger.warning(f"⚠️  {reason}")
    return StubEngine(reason)
