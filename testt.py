
# Quick webcam test for RTDETRv2 (native) with Ultralytics fallback
import cv2
import torch
import numpy as np
import sys
from pathlib import Path

# ── COCO-80 class names for annotation ──
COCO_NAMES = [
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

NATIVE_WEIGHTS = Path(__file__).parent / "models" / "rtdetrv2_r101vd_6x_coco_from_paddle.pth"
LEGACY_WEIGHTS = "rtdetr-l.pt"


def load_rtdetrv2_native(device: str):
    """Load RTDETRv2 R101vd via official repo (torch.hub)."""
    print(f"[RTDETRv2] Loading native engine on {device} …")

    # Temporarily remove paths that contain a local 'src' package so that
    # torch.hub can import the RT-DETR repo's own src.core without conflict.
    script_dir = str(Path(__file__).resolve().parent)
    workspace_dir = str(Path(__file__).resolve().parent.parent)
    saved_path = sys.path[:]
    saved_src = {k: v for k, v in sys.modules.items() if k == "src" or k.startswith("src.")}
    for k in saved_src:
        del sys.modules[k]
    sys.path = [p for p in sys.path if p not in ("", ".", script_dir, workspace_dir)]

    try:
        model = torch.hub.load(
            'lyuwenyu/RT-DETR', 'rtdetrv2_r101vd',
            pretrained=False, source='github',
        )
    finally:
        # Restore original sys.path and clear hub's src modules
        sys.path = saved_path
        for k in list(sys.modules):
            if k == "src" or k.startswith("src."):
                del sys.modules[k]
        sys.modules.update(saved_src)

    ckpt = torch.load(str(NATIVE_WEIGHTS), map_location='cpu')
    if 'ema' in ckpt and 'module' in ckpt['ema']:
        state = ckpt['ema']['module']
    elif 'model' in ckpt:
        state = ckpt['model']
    else:
        state = ckpt
    model.load_state_dict(state, strict=False)
    model = model.eval().to(device)
    print(f"[RTDETRv2] Loaded ✓")
    return model


def infer_native(model, frame_bgr, device, conf_thresh=0.25):
    """Run inference with native RTDETRv2 model, return annotated frame."""
    h, w = frame_bgr.shape[:2]
    img = cv2.resize(frame_bgr, (640, 640))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    tensor = torch.from_numpy(img.transpose(2, 0, 1)).unsqueeze(0).to(device)
    orig_sizes = torch.tensor([[h, w]], device=device)

    with torch.no_grad():
        out = model(tensor, orig_target_sizes=orig_sizes)

    # Deploy mode returns a tuple: (labels, boxes, scores) each with batch dim
    if isinstance(out, (list, tuple)) and not isinstance(out[0], dict):
        labels = out[0][0].cpu().numpy()
        boxes  = out[1][0].cpu().numpy()
        scores = out[2][0].cpu().numpy()
    else:
        # Non-deploy mode returns a list of dicts
        result = out[0] if isinstance(out, list) else out
        labels = result['labels'].cpu().numpy()
        boxes  = result['boxes'].cpu().numpy()
        scores = result['scores'].cpu().numpy()

    count = 0
    for i in range(len(scores)):
        if scores[i] < conf_thresh:
            continue
        count += 1
        x1, y1, x2, y2 = [int(v) for v in boxes[i]]
        cls_id = int(labels[i])
        name = COCO_NAMES[cls_id] if cls_id < len(COCO_NAMES) else f"cls_{cls_id}"
        cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), (0,255,0), 2)
        cv2.putText(frame_bgr, f"{name} {scores[i]:.2f}",
                    (x1, max(y1-8, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)
    return frame_bgr, count


def main():
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    use_native = NATIVE_WEIGHTS.exists()

    if use_native:
        model = load_rtdetrv2_native(device)
    else:
        print(f"[Fallback] RTDETRv2 weights not found, using Ultralytics {LEGACY_WEIGHTS}")
        from ultralytics import RTDETR
        model = RTDETR(LEGACY_WEIGHTS)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Could not open webcam.")
        return

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame from webcam.")
            break

        if use_native:
            annotated, n = infer_native(model, frame, device)
        else:
            results = model(frame)
            annotated = results[0].plot()
            n = len(results[0].boxes)

        print(f"Detected {n} objects in the current frame.")
        cv2.imshow("RTDETRv2 Detection", annotated)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()