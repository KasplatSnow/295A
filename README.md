# VigilZone

[![Security: AI-Powered](https://img.shields.io/badge/Security-AI--Powered-blueviolet)](https://github.com/SkiddieAhn/Paper-AnyAnomaly)
[![Audio: BEATs](https://img.shields.io/badge/Audio-BEATs-green)](https://github.com/microsoft/unilm/tree/master/beats)
[![Backend: Django](https://img.shields.io/badge/Backend-Django-092e20)](https://www.djangoproject.com/)
[![Frontend: React](https://img.shields.io/badge/Frontend-React-61dafb)](https://reactjs.org/)
[![Streaming: WebRTC](https://img.shields.io/badge/Streaming-WebRTC-orange)](https://mediamtx.com/)

**VigilZone** is a real-time, multi-tenant audio-visual anomaly detection and notification platform for community surveillance. It combines multi-lane video detection, BEATs-based audio event recognition, temporal audio-video fusion with per-camera normality profiling, evidence capture, incident persistence, and real-time operator notification into a single deployable workflow.

---

## Key Capabilities

| Modality | Detection Capabilities |
|:---|:---|
| **Video Lanes** | Person/object detection (YOLOv8/YOLOv12, RT-DETR v2), fire & smoke, weapons, pose-based fall detection, violence candidates, vehicle accidents, zone intrusion |
| **Audio Lane** | BEATs-based classification — screams, glass break, alarms, sirens, vehicle crash, gunshot-like, explosion-like sounds |
| **Audio-Video Fusion** | Temporal correlation within configurable window, confidence boosting on multimodal agreement, normality profiling (EMA), uncertainty gating, critical label protection |
| **Identity** | InsightFace facial recognition, CLIP-based pet identification |
| **Zero-Shot** | AnomalyCLIP + MiniCPM-V for open-vocabulary anomaly detection |

### Platform Features

- **Multi-tenant isolation** — tenant-scoped data, RBAC (admin/operator/viewer), JWT auth with refresh rotation
- **Incident lifecycle** — idempotent creation via event receipts, state transitions, audit trail
- **Evidence management** — keyframe images, video clips, audio WAV segments linked per incident
- **Real-time notifications** — SSE/WebSocket push with per-user alert state, unread tracking, REST recovery
- **Configurable camera modes** — `video_only`, `audio_only`, or `audio_video` per camera with runtime switching
- **Normality adaptation** — per-camera EMA-based background pattern learning with critical label protection
- **Learned fusion (shadow mode)** — neural fusion head logging parallel predictions for future promotion
- **GCP cloud deployment** — automated deploy scripts with GPU instance provisioning

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Operator Browser                                │
│                     React 18 + TanStack Query + SSE                         │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ JWT-authenticated REST + SSE
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Django Control Plane                                  │
│         REST API · ASGI (Uvicorn) · Tenant Auth · Incident CRUD             │
│         Notification State · Redis Consumer · Evidence Metadata              │
└──────────────┬──────────────────────────────────┬───────────────────────────┘
               │                                  │
               ▼                                  ▼
┌──────────────────────────┐        ┌──────────────────────────────────────────┐
│       PostgreSQL         │        │              Redis Streams                │
│  Tenants · Cameras       │        │   alert.created events · Pub/Sub         │
│  Incidents · Evidence    │        │   Notification transport · Cache          │
│  Alerts · Receipts       │        └──────────────────┬───────────────────────┘
└──────────────────────────┘                           │
                                                       ▲ publish
                                                       │
┌─────────────────────────────────────────────────────────────────────────────┐
│                          FastAPI AI Service                                   │
│                                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────────────┐    │
│  │ Video Lanes │  │  Audio Lane  │  │      Multimodal Fusion          │    │
│  │ YOLO/RT-DETR│  │  BEATs +     │  │  Temporal window · Scoring      │    │
│  │ Fire/Smoke  │  │  FFmpeg      │  │  Normality · Uncertainty gate   │    │
│  │ Weapon/Fall │  │  16kHz mono  │  │  Evidence export · Publication  │    │
│  │ Violence    │  │  Label map   │  │  Learned fusion (shadow)        │    │
│  └─────────────┘  └──────────────┘  └─────────────────────────────────┘    │
│                                                                              │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ OpenCV + FFmpeg
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    MediaMTX (RTSP/WebRTC Relay)                              │
│                         Camera Streams                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tech Stack

| Layer | Technologies |
|:---|:---|
| **AI Engine** | PyTorch, YOLOv8/v12, RT-DETR v2, BEATs (audio), InsightFace, AnomalyCLIP, open_clip, Ultralytics, ONNX/TensorRT |
| **Backend** | Django 5.2, DRF, Django Channels, SimpleJWT, Redis Streams consumer groups |
| **Frontend** | React 18, TypeScript, Vite, TanStack Query, Radix UI, Tailwind CSS, EventSource (SSE) |
| **Streaming** | MediaMTX (WebRTC/RTSP), OpenCV, FFmpeg (audio extraction) |
| **Persistence** | PostgreSQL 15+ (pgvector-ready), Redis 7+ (Streams, Pub/Sub, Cache) |
| **Deployment** | Docker Compose (local), GCP Compute Engine (GPU), Nginx reverse proxy |

---

## Audio-Video Fusion Pipeline

The AI service implements configurable temporal fusion between video and audio observations:

```
Fusion Score:  c_fused = α·c_video + β·c_audio + γ·temporal_agreement
               (α=0.45, β=0.35, γ=0.20 for audio_video mode)

Normality:     μ_new = (1-λ)·μ_old + λ·c_current     (λ=0.05, EMA adaptation)

Gating:        alert_eligible = (c_adjusted > τ_gate) ∧ (¬suppressed ∨ is_critical)
```

- **Critical labels** (scream, alarm, glass_break, gunshot, explosion) are never suppressed by normality
- **Shadow-mode learned fusion** logs parallel predictions without affecting production alerts
- **Evidence capture** generates keyframes, video clips, and audio WAV segments for operator review

---

## Quick Start

### Docker (Recommended)

```bash
# 1. Prepare environment
cp .env.example .env

# 2. Launch the stack
docker compose up --build -d

# 3. Bootstrap database
docker compose exec backend python manage.py bootstrap_postgres_config
docker compose exec backend python manage.py createsuperuser
```

Open [http://localhost:8085](http://localhost:8085) to start monitoring.

### Local Development

**AI Service** (Python 3.11+, CUDA recommended):
```bash
cd services/ai
pip install -r requirements.txt
python run.py
```

**Django Backend:**
```bash
cd services/backend
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

**React Dashboard:**
```bash
cd web/ui
npm install
npm run dev
```

### GCP Cloud Deployment

```bash
# Deploy with GPU instance
./gcp_deploy.sh

# Stop/cleanup
./gcp_stop.sh
```

---

## Project Structure

```
├── services/
│   ├── ai/                    # FastAPI AI inference service
│   │   ├── src/
│   │   │   ├── lanes/        # Detection lanes (video + audio)
│   │   │   ├── logic/        # Multimodal fusion, normality, learned fusion
│   │   │   ├── evidence/     # Keyframe/clip/WAV export, ring buffers
│   │   │   ├── ingest/       # Camera readers (OpenCV, FFmpeg audio)
│   │   │   ├── incidents/    # Incident state machine
│   │   │   └── identity/     # Face/pet recognition
│   │   └── models/           # Model weights (BEATs, YOLO, etc.)
│   └── backend/               # Django control plane
│       ├── api/               # REST endpoints, models, auth
│       ├── ai_integration/    # AI webhook + Redis consumer
│       └── server/            # Django settings, ASGI config
├── web/ui/                    # React operator dashboard
│   └── client/src/
│       ├── components/        # NotificationBell, CameraFeed, AlertCard, etc.
│       └── pages/             # Dashboard, Incidents, Cameras, Settings
├── deploy/                    # Nginx config, deployment assets
├── report_doc/                # IEEE-formatted project documentation
├── plan/                      # Architecture roadmaps
├── docker-compose.yml         # Full-stack orchestration
├── gcp_deploy.sh             # GCP GPU instance deployment
└── Makefile                   # Build shortcuts
```

---

## Management Commands

| Command | Purpose |
|:---|:---|
| `python manage.py register_ai_webhook` | Sync AI Engine webhook with backend |
| `python manage.py bootstrap_postgres_config` | Idempotent setup of default settings |
| `/api/streams/health/` | MJPEG/RTSP streaming diagnostics |
| `/api/ai/system/status/` | GPU thermal, memory, model status |

---

## Documentation

Full IEEE-formatted project documentation is available in [`report_doc/`](report_doc/):
- System architecture and design
- AI model pipeline with mathematical formulations
- Testing methodology and evaluation matrix
- Demonstration plan with timed script

---

## References

- [BEATs: Audio Pre-Training with Acoustic Tokenizers](https://github.com/microsoft/unilm/tree/master/beats) (ICML 2023)
- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) (YOLOv8/v12)
- [RT-DETR: DETRs Beat YOLOs on Real-Time Object Detection](https://arxiv.org/abs/2304.08069) (CVPR 2024)
- [AVadCLIP: Audio-Visual Collaboration for Robust Video Anomaly Detection](https://arxiv.org/abs/2504.04495) (2025)
- [UCF-Crime: Real-World Anomaly Detection in Surveillance Videos](https://arxiv.org/abs/1801.04264) (CVPR 2018)

---

*Built by the VigilZone Team — CMPE 295B, San Jose State University*
