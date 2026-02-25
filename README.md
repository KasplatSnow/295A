# VigilZone AI Module

Real-time CCTV anomaly detection **microservice** for Windows 10/11. Part of a
larger surveillance platform. Supports **RTSP cameras** and **live USB/webcam**
feeds, with multiple detection lanes running in parallel per camera.

Designed for easy integration with the **main-drive microservice** via REST API,
WebSockets, and Webhooks.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Integration Guide](#integration-guide)
  - [Base URL](#base-url)
  - [Authentication](#authentication)
  - [CORS](#cors)
- [API Reference](#api-reference)
  - [Health & Status](#health--status)
  - [Alerts](#alerts)
  - [Cameras & Frames](#cameras--frames)
  - [Entities (Identity)](#entities-identity)
  - [Webhooks](#webhooks)
  - [Video Upload (Offline)](#video-upload-offline)
  - [System Diagnostics](#system-diagnostics)
  - [WebSocket (Real-Time Push)](#websocket-real-time-push)
- [Webhook Payload Format](#webhook-payload-format)
- [Alert Schema](#alert-schema)
- [Entity Schema](#entity-schema)
- [Detection Lanes](#detection-lanes)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Adding a New Lane](#adding-a-new-lane)
- [License](#license)

---

## Features

- **Dual input** – RTSP streams *or* USB/laptop webcam (live_camera)
- **11 detection lanes** running in parallel per camera
- **K-of-N voting** (default 3/5) + **cooldown** (45 s) + **session dedup**
- **Identity subsystem** – enroll persons/pets, suppress alerts for known entities
- **Webhook integration** – register HTTP endpoints, receive real-time event POSTs
- **REST API** – JSON-first `/api/v1/` endpoints for microservice integration
- **WebSocket** – real-time alert push on `ws://<host>:8080/ws`
- **CORS enabled** – cross-origin requests allowed for frontend integration
- **Evidence export** – 5 s pre + 5 s post clip per alert
- **Web UI** built-in at `/` for monitoring and entity management

---

## Architecture

```text
ai_module/
├── configs/
│   ├── cameras.yaml        # Camera sources, per-lane Hz, evidence settings
│   ├── models.yaml         # Model paths, thresholds
│   └── zones.yaml          # Polygon zones per camera
├── src/
│   ├── api/                # FastAPI server + webhooks + static Web UI
│   ├── common/             # types, config, logging, timeutil
│   ├── ingest/             # OpenCV, FFmpeg, live_camera readers
│   ├── lanes/              # 11 detection lanes
│   ├── logic/              # aggregator, voting, cooldown, deduper, zones
│   ├── evidence/           # ring buffer + exporter
│   └── identity/           # face/pet embedders, entity store, matcher, policy
├── data/                   # Runtime data (auto-created)
│   ├── entities.db         # SQLite entity store
│   ├── embeddings/         # Face/pet embedding vectors
│   ├── enroll_images/      # Saved enrollment images
│   └── webhooks.json       # Persisted webhook registrations
├── evidence/               # Alert evidence output (auto-created)
├── alerts/                 # JSONL alert logs (auto-created)
├── run.py                  # Entry point
└── requirements.txt
```

---

## Quick Start

### 1. Install PyTorch

```bash
# CPU only
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# CUDA 12.1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

### 2. Install dependencies

```bash
cd ai_module
pip install -r requirements.txt
```

### 3. Run

```bash
python run.py
```

### 4. Verify

```bash
curl http://localhost:8080/api/v1/health
```

Expected response:

```json
{
  "service": "vigilzone-ai",
  "status": "healthy",
  "version": "2.0.0",
  "uptime_seconds": 12.3,
  "cameras_active": 1,
  "alerts_total": 0,
  "ws_clients": 0,
  "webhooks_registered": 0
}
```

---

## Integration Guide

### Base URL

```text
http://<host>:8080
```

All `/api/v1/` endpoints return JSON and are designed for **machine-to-machine**
integration with the main-drive microservice.

The root `/` endpoints (without `/api/v1/`) serve the built-in Web UI and remain
backward-compatible.

### Authentication

Currently **no authentication** is required. The service is intended to run on a
private network or behind a reverse proxy / API gateway that handles auth.

### CORS

CORS is enabled for all origins (`*`) by default. Tighten `allow_origins` in
production by editing `server.py`.

---

## API Reference

### Health & Status

#### `GET /api/v1/health`

Lightweight health probe for load balancers / service mesh.

**Response** `200 OK`

```json
{
  "service": "vigilzone-ai",
  "status": "healthy",
  "version": "2.0.0",
  "uptime_seconds": 3600.5,
  "cameras_active": 2,
  "alerts_total": 47,
  "ws_clients": 1,
  "webhooks_registered": 3
}
```

#### `GET /api/v1/system/status`

Full system status including device info, lane status, and diagnostics.

**Response** `200 OK`

```json
{
  "service": "vigilzone-ai",
  "version": "2.0.0",
  "uptime_seconds": 3600.5,
  "device": {
    "torch_device": "cuda:0",
    "gpu_name": "NVIDIA GeForce GTX 1650",
    "gpu_usable": true
  },
  "cameras": [
    {"camera_id": "cam_live", "active": true, "lanes": ["rt_detr", "yolov8_fallback", "fire_smoke_yolo", "person_zone"]}
  ],
  "webhooks": 3,
  "diagnostics": {
    "suppression_counters": {"motion_explained_by_benign": 12},
    "motion_stats": {},
    "temporal_verifier_stats": {}
  }
}
```

---

### Alerts

#### `GET /api/v1/alerts`

Fetch recent alerts with optional filters.

| Query Param   | Type   | Default | Description                                      |
|---------------|--------|---------|--------------------------------------------------|
| `limit`       | int    | 50      | Max alerts to return (1-1000)                    |
| `severity`    | string | —       | Filter: `SEVERE`, `HIGH`, `MED`, `LOW`           |
| `alert_type`  | string | —       | Filter: `FIRE_SMOKE`, `VIOLENCE_FIGHT`, `FALL`, `WEAPON_DETECTED`, `INTRUSION_PERSON_IN_ZONE`, `UNKNOWN_SEVERE_ANOMALY` |
| `camera_id`   | string | —       | Filter by camera ID                              |

**Response** `200 OK`

```json
{
  "count": 2,
  "alerts": [
    {
      "ts_utc": "2026-02-24T10:15:30.123Z",
      "camera_id": "cam_live",
      "type": "VIOLENCE_FIGHT",
      "severity": "SEVERE",
      "confidence": 0.87,
      "session_id": "a1b2c3d4e5f67890",
      "label": "fighting",
      "k_of_n": {"k": 3, "n": 5, "hits": 3},
      "entity": {"id": null, "name": null, "category": null, "confidence": 0.0},
      "evidence": {"keyframe_path": "evidence/cam_live/..._VIOLENCE.jpg", "clip_path": "..."},
      "payload": {"zone_name": "lobby", "track_id": 42}
    }
  ]
}
```

#### `GET /alerts?limit=200`

Legacy endpoint — returns flat array of alerts (no filters).

---

### Cameras & Frames

#### `GET /api/v1/cameras`

List active cameras with status.

**Response** `200 OK`

```json
{
  "count": 1,
  "cameras": [
    {
      "camera_id": "cam_live",
      "source_type": "live_camera",
      "active": true,
      "fps": 30.0,
      "lanes": ["rt_detr", "yolov8_fallback", "fire_smoke_yolo", "person_zone"],
      "frame_count": 12345
    }
  ]
}
```

#### `GET /api/v1/cameras/{camera_id}/snapshot`

Returns latest JPEG frame from camera. Use for embedding snapshots in
the main-drive UI.

**Response** `200 OK` — `image/jpeg` binary

**Response** `404` — camera not found

**Headers:**

- `X-Timestamp` — UTC timestamp of the frame
- `Cache-Control: no-cache`

#### `GET /frame/{camera_id}`

Legacy frame endpoint (same behavior).

---

### Entities (Identity)

The identity subsystem allows enrolling known persons and pets. Once enrolled,
the system automatically recognizes them and **suppresses intrusion alerts** for
known entities.

#### `GET /api/v1/entities`

List all enrolled entities.

| Query Param | Type   | Description                          |
|-------------|--------|--------------------------------------|
| `category`  | string | Filter: `KNOWN_PERSON`, `PET`        |

**Response** `200 OK`

```json
{
  "count": 2,
  "entities": [
    {
      "entity_id": "ent_a1b2c3d4e5f6",
      "name": "John Doe",
      "category": "KNOWN_PERSON",
      "role": "OWNER",
      "metadata": {},
      "created_at": "2026-02-24T08:00:00Z"
    }
  ]
}
```

#### `GET /api/v1/entities/{entity_id}`

Get entity details including enrollment images.

**Response** `200 OK`

```json
{
  "entity_id": "ent_a1b2c3d4e5f6",
  "name": "John Doe",
  "category": "KNOWN_PERSON",
  "role": "OWNER",
  "metadata": {},
  "images": ["/enroll_images/ent_a1b2c3d4e5f6/0_face.jpg"]
}
```

#### `POST /entities/enroll_person_from_upload`

Two-step enrollment: first upload images, then enroll.

**Step 1** — Upload images to staging:

```bash
curl -X POST http://localhost:8080/uploads/enroll_images \
  -F "files=@face1.jpg" \
  -F "files=@face2.jpg" \
  -F "files=@face3.jpg"
```

**Response:**

```json
{
  "upload_id": "upl_abc123def456",
  "stored": [{"filename": "0_face1.jpg", "url": "/staging/upl_abc123def456/0_face1.jpg"}],
  "all_files": [...]
}
```

**Step 2** — Enroll from staging:

```bash
curl -X POST http://localhost:8080/entities/enroll_person_from_upload \
  -H "Content-Type: application/json" \
  -d '{"upload_id": "upl_abc123def456", "name": "John Doe", "role": "OWNER"}'
```

**Response:**

```json
{
  "entity_id": "ent_a1b2c3d4e5f6",
  "name": "John Doe",
  "category": "KNOWN_PERSON",
  "embeddings_stored": 3,
  "saved_images_count": 3,
  "saved_image_urls": ["/enroll_images/ent_a1b2c3d4e5f6/0_face1.jpg"],
  "failed_images": []
}
```

#### `POST /entities/enroll_pet_from_upload`

Same two-step flow for pets. Body: `{"upload_id": "...", "name": "Buddy"}`.

#### `POST /entities/enroll_person`

Legacy single-step multipart enrollment (internally routes through staging).

```bash
curl -X POST http://localhost:8080/entities/enroll_person \
  -F "name=John Doe" \
  -F "role=OWNER" \
  -F "files=@face1.jpg" \
  -F "files=@face2.jpg"
```

#### `POST /entities/enroll_pet`

Legacy single-step multipart pet enrollment.

#### `POST /entities/enroll_person_from_camera`

Capture a frame from a live camera and enroll the detected face.

```bash
curl -X POST http://localhost:8080/entities/enroll_person_from_camera \
  -F "camera_id=cam_live" \
  -F "name=John Doe" \
  -F "role=FAMILY"
```

#### `POST /entities/enroll_pet_from_camera`

Same for pets — capture from live camera.

#### `DELETE /entities/{entity_id}`

Remove an enrolled entity and all its embeddings.

**Response:**

```json
{"removed": true, "entity_id": "ent_a1b2c3d4e5f6"}
```

#### `POST /identity/reload`

Force-reload matcher indices after external DB changes.

#### `GET /identity/state?camera_id=cam_live`

Per-track identity debug state for a camera.

---

### Webhooks

Webhooks provide **push-based** integration. Register a URL, and the AI module
will POST events to it in real time. Webhooks are persisted to disk and survive
restarts.

#### `POST /webhooks`

Register a new webhook.

**Request Body (JSON):**

```json
{
  "url": "https://main-drive.example.com/api/ai-events",
  "events": ["alert.created", "entity.enrolled", "entity.deleted"],
  "secret": "my-shared-secret-key",
  "metadata": {"environment": "production"}
}
```

| Field      | Type     | Required | Description                                          |
|------------|----------|----------|------------------------------------------------------|
| `url`      | string   | Yes      | Target URL to receive POST events                    |
| `events`   | string[] | No       | Event types to subscribe (default: `["alert.created"]`) |
| `secret`   | string   | No       | Shared secret for HMAC-SHA256 signature verification |
| `metadata` | object   | No       | Optional metadata for your reference                 |

**Supported Event Types:**

| Event              | Trigger                                |
|--------------------|----------------------------------------|
| `alert.created`    | New confirmed alert                    |
| `entity.enrolled`  | New entity enrolled                    |
| `entity.deleted`   | Entity deleted                         |
| `system.health`    | Periodic health ping (future)          |
| `webhook.test`     | Test event from `/webhooks/test`       |

**Response** `200 OK`

```json
{
  "id": "wh_abc123def456",
  "url": "https://main-drive.example.com/api/ai-events",
  "events": ["alert.created", "entity.enrolled"],
  "active": true
}
```

#### `GET /webhooks`

List all registered webhooks with delivery stats.

**Response:**

```json
[
  {
    "id": "wh_abc123def456",
    "url": "https://main-drive.example.com/api/ai-events",
    "events": ["alert.created"],
    "active": true,
    "delivery_stats": {"success": 142, "failure": 3, "last_status": 200}
  }
]
```

#### `PUT /webhooks/{webhook_id}`

Update a webhook's URL, events, active status, or secret.

**Request Body (JSON):**

```json
{
  "url": "https://new-url.example.com/events",
  "events": ["alert.created", "entity.enrolled"],
  "active": true
}
```

#### `DELETE /webhooks/{webhook_id}`

Remove a registered webhook.

#### `POST /webhooks/test`

Send a test event to any URL to verify connectivity.

**Request Body (JSON):**

```json
{"url": "https://main-drive.example.com/api/ai-events"}
```

**Response:**

```json
{"status": 200, "ok": true, "body": "...first 500 chars of response..."}
```

---

### Webhook Payload Format

Every webhook delivery is a POST request with this structure:

```json
{
  "event": "alert.created",
  "timestamp": 1740412530.123,
  "data": { ... }
}
```

**Headers sent with every delivery:**

| Header                    | Description                                       |
|---------------------------|---------------------------------------------------|
| `Content-Type`            | `application/json`                                |
| `X-Vigilzone-Event`       | Event type (e.g. `alert.created`)                 |
| `X-Vigilzone-Signature`   | `sha256=<hex>` HMAC-SHA256 (only if secret is set)|

**Verifying signatures (example in Python):**

```python
import hmac, hashlib

def verify_signature(body: bytes, secret: str, signature_header: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return signature_header == f"sha256={expected}"
```

---

### Video Upload (Offline)

#### `POST /upload_video`

Upload a video file for offline processing.

```bash
curl -X POST http://localhost:8080/upload_video -F "file=@surveillance.mp4"
```

**Response:**

```json
{"job_id": "abc123", "status": "queued"}
```

#### `GET /jobs`

List all upload processing jobs.

#### `GET /jobs/{job_id}`

Get status + progress of a job.

#### `GET /jobs/{job_id}/alerts`

Get alerts generated by a completed job.

---

### System Diagnostics

#### `GET /system/diagnostics`

Full diagnostic dump: device info, lane status, suppression counters,
motion stats, temporal verifier stats, missing assets.

#### `GET /metrics`

Observability: per-lane avg_ms, p95_ms, runs/min, dropped_count, queue length.

---

### WebSocket (Real-Time Push)

#### `WS /ws`

Connect via WebSocket to receive alerts in real time.

```javascript
const ws = new WebSocket("ws://localhost:8080/ws");
ws.onmessage = (event) => {
    const alert = JSON.parse(event.data);
    console.log("Alert:", alert.type, alert.severity);
};
```

Each message is a full [Alert JSON object](#alert-schema).

---

## Alert Schema

```json
{
  "ts_utc": "2026-02-24T10:15:30.123Z",
  "camera_id": "cam_live",
  "type": "VIOLENCE_FIGHT",
  "label": "fighting",
  "severity": "SEVERE",
  "confidence": 0.87,
  "session_id": "a1b2c3d4e5f67890",
  "k_of_n": {"k": 3, "n": 5, "hits": 3},
  "cooldown_s": 45,
  "evidence": {
    "keyframe_path": "evidence/cam_live/..._VIOLENCE.jpg",
    "clip_path": "evidence/cam_live/..._VIOLENCE.mp4",
    "partial_clip": false
  },
  "entity": {
    "id": "ent_a1b2c3d4e5f6",
    "name": "John Doe",
    "category": "KNOWN_PERSON",
    "confidence": 0.92
  },
  "payload": {
    "bboxes": [[100, 150, 200, 300]],
    "zone_name": "lobby",
    "track_id": 42,
    "lane_votes": [
      {"lane": "rt_detr", "score": 0.91, "trigger": true},
      {"lane": "yolov8_fallback", "score": 0.85, "trigger": true}
    ],
    "temporal_verifier": {"confirmed": true, "score": 0.78}
  },
  "debug": {}
}
```

### Alert Types

| Type                         | Severity | Description                        |
|------------------------------|----------|------------------------------------|
| `FIRE_SMOKE`                 | SEVERE   | Fire or smoke detected             |
| `VIOLENCE_FIGHT`             | SEVERE   | Violence/fighting detected         |
| `FALL`                       | SEVERE   | Person fall detected               |
| `WEAPON_DETECTED`            | SEVERE   | Weapon (gun/knife) detected        |
| `INTRUSION_PERSON_IN_ZONE`   | HIGH     | Person in restricted zone          |
| `UNKNOWN_SEVERE_ANOMALY`     | HIGH     | Unclassified anomaly               |

---

## Entity Schema

```json
{
  "entity_id": "ent_a1b2c3d4e5f6",
  "name": "John Doe",
  "category": "KNOWN_PERSON",
  "role": "OWNER",
  "metadata": {},
  "created_at": "2026-02-24T08:00:00Z"
}
```

### Categories

| Category         | Description           |
|------------------|-----------------------|
| `KNOWN_PERSON`   | Enrolled person       |
| `UNKNOWN_PERSON` | Detected but unknown  |
| `PET`            | Enrolled pet          |
| `UNKNOWN_ANIMAL` | Detected unknown animal |

### Roles

`OWNER`, `FAMILY`, `FRIEND`, `NEIGHBOR`, `VISITOR`, `EMPLOYEE`, `CONTRACTOR`, `PET`

---

## Detection Lanes

| Lane                 | Alert Type                 | Severity | Model                  |
|----------------------|----------------------------|----------|------------------------|
| `rt_detr`            | *dynamic from label*       | varies   | RT-DETR (TRT/ONNX)    |
| `yolov8_fallback`    | *dynamic from label*       | varies   | YOLOv8n               |
| `fire_smoke_yolo`    | FIRE_SMOKE                 | SEVERE   | YOLO fire/smoke        |
| `violence_candidate` | VIOLENCE_FIGHT             | SEVERE   | Motion/pose stub       |
| `fall_candidate`     | FALL                       | SEVERE   | Motion/pose stub       |
| `weapon_yolo`        | WEAPON_DETECTED            | SEVERE   | YOLO weapon            |
| `anyanomaly`         | UNKNOWN_SEVERE_ANOMALY     | HIGH     | AnyAnomaly checkpoint  |
| `anomalyclip`        | UNKNOWN_SEVERE_ANOMALY     | HIGH     | AnomalyCLIP/motion     |
| `temporal_verifier`  | *(confirms other alerts)*  | —        | X3D-S (Kinetics-400)   |
| `person_zone`        | INTRUSION_PERSON_IN_ZONE   | HIGH     | YOLOv8 + IoU tracker   |
| `entity_identity`    | *(identity resolution)*    | —        | InsightFace + CLIP     |

---

## Configuration

### cameras.yaml

```yaml
cameras:
  - camera_id: cam_live
    source_type: live_camera
    camera_index: 0
    sample_hz:
      detector_primary: 2
      detector_fallback: 2
      fire_smoke: 2
      anomaly_generic: 0.5
      temporal_verifier: 0.2
    enabled_lanes:
      - rt_detr
      - yolov8_fallback
      - fire_smoke_yolo
      - anyanomaly
      - anomalyclip
      - temporal_verifier
      - person_zone
      - entity_identity
    cooldown_s: 45
    k_of_n: [3, 5]
    evidence:
      pre_s: 5
      post_s: 5
```

### models.yaml

Model paths and thresholds for all detectors. See `configs/models.yaml` for full reference.

### zones.yaml

Define restricted polygon zones per camera for intrusion detection.

---

## Endpoint Quick-Reference Table

| Method | Path                                      | Purpose                          |
|--------|-------------------------------------------|----------------------------------|
| GET    | `/api/v1/health`                          | Health probe                     |
| GET    | `/api/v1/alerts`                          | Alerts (with filters)            |
| GET    | `/api/v1/cameras`                         | Camera list                      |
| GET    | `/api/v1/cameras/{id}/snapshot`           | Camera JPEG snapshot             |
| GET    | `/api/v1/entities`                        | Entity list                      |
| GET    | `/api/v1/entities/{id}`                   | Entity detail + images           |
| GET    | `/api/v1/system/status`                   | Full system status               |
| POST   | `/webhooks`                               | Register webhook                 |
| GET    | `/webhooks`                               | List webhooks                    |
| PUT    | `/webhooks/{id}`                          | Update webhook                   |
| DELETE | `/webhooks/{id}`                          | Remove webhook                   |
| POST   | `/webhooks/test`                          | Test webhook connectivity        |
| POST   | `/uploads/enroll_images`                  | Stage enrollment images          |
| POST   | `/entities/enroll_person_from_upload`     | Enroll person (from staging)     |
| POST   | `/entities/enroll_pet_from_upload`        | Enroll pet (from staging)        |
| POST   | `/entities/enroll_person`                 | Enroll person (multipart)        |
| POST   | `/entities/enroll_pet`                    | Enroll pet (multipart)           |
| POST   | `/entities/enroll_person_from_camera`     | Enroll person (live capture)     |
| POST   | `/entities/enroll_pet_from_camera`        | Enroll pet (live capture)        |
| DELETE | `/entities/{id}`                          | Delete entity                    |
| POST   | `/identity/reload`                        | Rebuild matcher indices          |
| GET    | `/identity/state?camera_id=X`             | Per-track identity state         |
| POST   | `/upload_video`                           | Upload video for processing      |
| GET    | `/jobs`                                   | List upload jobs                 |
| GET    | `/jobs/{id}`                              | Job status                       |
| GET    | `/jobs/{id}/alerts`                       | Job alerts                       |
| GET    | `/system/diagnostics`                     | Full diagnostics                 |
| GET    | `/metrics`                                | Performance metrics              |
| WS     | `/ws`                                     | Real-time alert stream           |
| GET    | `/health`                                 | Legacy health check              |
| GET    | `/alerts?limit=N`                         | Legacy alerts                    |
| GET    | `/cameras`                                | Legacy camera list               |
| GET    | `/frame/{camera_id}`                      | Legacy frame endpoint            |
| GET    | `/`                                       | Web UI                           |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Model weights not found" | Drop `.pt`/`.onnx` files in `models/` or parent dir; update `models.yaml` |
| Webcam not detected | Check `camera_index` in `cameras.yaml` (try 0, 1, 2) |
| RTSP connection drops | System auto-reconnects; verify URL/network |
| Port 8080 in use | Change port in `src/app.py` → `AlertServer(port=XXXX)` |
| GPU not detected | Install CUDA PyTorch; set `device: cpu` in `models.yaml` |
| Webhook delivery fails | Check `/webhooks` for `delivery_stats`; use `/webhooks/test` to verify URL |

---

## Adding a New Lane

1. Create `src/lanes/my_lane.py` extending `BaseLane`
2. Register in `LANE_REGISTRY` dict in `src/app.py`
3. Map lane → alert type in `LANE_TO_ALERT_TYPE` in `src/logic/aggregator.py`
4. Add lane name to `enabled_lanes` in `cameras.yaml`

---

## License

MIT
