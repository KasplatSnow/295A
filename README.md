# 🛡️ VigilZone

[![Security: AI-Powered](https://img.shields.io/badge/Security-AI--Powered-blueviolet)](https://github.com/SkiddieAhn/Paper-AnyAnomaly)
[![Backend: Django](https://img.shields.io/badge/Backend-Django-092e20)](https://www.djangoproject.com/)
[![Frontend: React](https://img.shields.io/badge/Frontend-React-61dafb)](https://reactjs.org/)
[![Streaming: WebRTC](https://img.shields.io/badge/Streaming-WebRTC-orange)](https://mediamtx.com/)

**VigilZone** is a state-of-the-art, enterprise-grade AI surveillance platform designed for real-time security automation. It unifies high-performance computer vision, low-latency streaming, and centralized incident management into a single, robust monorepo.

---

## ✨ Core Functionalities

*   🔥 **Fire & Smoke Detection**: Dedicated high-recall models to detect fire and smoke in early stages.
*   🔫 **Weapon & Violence Gating**: Identify guns, knives, and suspicious physical interactions (fighting, crowding).
*   🧍 **Pose-Based Fall Detection**: Monitor elderly or restricted areas for falls using person-centric pose estimation.
*   🆔 **Entity Identity (Face & Pet)**: 
    *   **InsightFace**: Professional-grade facial recognition for registered personnel.
    *   **CLIP Pet-ID**: Distinguish between known household pets and intruders.
*   🎥 **WebRTC Live Relay**: Sub-second latency streaming via MediaMTX, ensuring what you see on the dashboard is happening *right now*.
*   📍 **Smart Zones & Intrusion**: Draw polygon boundaries on your cameras; get alerts only when they matter.
*   🧠 **AnyAnomaly (VLB Integration)**: Leveraging MiniCPM-V and AnomalyCLIP for "zero-shot" detection of unusual events that standard models might miss.
*   📱 **Real-Time Notifications**: Instant WebSocket push to the dashboard and email/persistent alerting.

---

## 🏗️ Technical Architecture

VigilZone is built as a **Monolith-First** architecture for rapid deployment and consistency, while being microservice-ready.

```text
                                 ┌──────────────────┐
                                 │      Nginx       │ (Reverse Proxy)
                                 └────────┬─────────┘
                                          │
                  ┌───────────────────────┼───────────────────────┐
                  │                       │                       │
         ┌────────▼────────┐     ┌────────▼────────┐     ┌────────▼────────┐
         │    React UI     │     │  Django Backend │     │    AI Engine    │
         │ (Vite + Radix)  │     │ (REST + WS)     │     │ (FastAPI + CV)  │
         └─────────────────┘     └────────┬────────┘     └────────┬────────┘
                                          │                       │
                                 ┌────────▼────────┐     ┌────────▼────────┐
                                 │   PostgreSQL    │     │      Redis      │
                                 │ (Single Source) │     │(Streams/PubSub) │
                                 └─────────────────┘     └─────────────────┘
                                          ▲                       ▲
                                          │                       │
                                 ┌────────┴───────────────────────┴────────┐
                                 │               MediaMTX                  │
                                 │       (RTSP / WebRTC / Relay)           │
                                 └─────────────────────────────────────────┘
```

### 🛠️ The Tech Stack

| Layer | Technologies |
|:---:|:---|
| **AI Engine** | PyTorch, RT-DETR v2, YOLOv8, Ultralytics, InsightFace, open_clip, Transformers |
| **Backend** | Django 5.2, Django REST Framework, Django Channels (WebSockets), SimpleJWT |
| **Frontend** | React 18, Vite, Wouter (Routing), TanStack Query, Radix UI, Tailwind CSS |
| **Streaming** | MediaMTX (WebRTC/RTSP), OpenCV (Legacy/Fallbacks) |
| **Persistence** | PostgreSQL (SSoT), Redis (Streams & Cache) |

---

## 🚀 Quick Start

### 🐳 Docker (Recommended)
The fastest way to get VigilZone up and running.

```bash
# 1. Prepare your environment
cp .env.example .env

# 2. Fire up the stack
docker compose up --build

# 3. Bootstrap the database
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py bootstrap_postgres_config
docker compose exec backend python manage.py createsuperuser
```
Open [http://localhost:8085](http://localhost:8085) to start monitoring!

---

## 🔧 Developer Setup (Local)

### 1. AI Module
Requires Python 3.11+ and CUDA (for GPU acceleration).
```bash
cd services/ai
pip install -r requirements.txt
python run.py
```

### 2. Django Backend
```bash
cd services/backend
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### 3. React UI
```bash
cd web/ui
npm install
npm run dev
```

---

## 🧪 Management & Automation

*   **Webhook Mapping**: Use `python manage.py register_ai_webhook` to sync your AI Engine with the backend.
*   **Asset Bootstrap**: Use `python manage.py bootstrap_postgres_config` for an idempotent setup of default settings and models.
*   **Streaming Health**: Check `/api/streams/health/` for real-time diagnostics of MJPEG and RTSP workers.
*   **CUDA Diagnostics**: Check `/api/ai/system/status/` for GPU thermal and memory stats.

---

## 🤝 Project Structure

*   `services/ai/`: FastAPI worker cluster.
*   `services/backend/`: Django monolith (Incidents, Auth, Multi-tenancy).
*   `web/ui/`: Modern React dashboard.
*   `deploy/nginx/`: Edge routing and security.
*   `plan/`: High-level architecture and implementation roadmaps.

---

*Built with ❤️ by the VigilZone Team.*
