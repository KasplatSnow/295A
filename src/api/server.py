"""
FastAPI server for alerts, evidence, live frame feed,
upload mode (offline video processing), and /metrics.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query, Body
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import List, Dict, Any, Optional
import asyncio
import json
import uuid
import time
import threading
import shutil
import cv2
import numpy as np
import httpx
from ..common.log import setup_logger


class AlertServer:
    """
    FastAPI server with WebSocket support for real-time alerts.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8080,
                 evidence_dir: str = "evidence", static_dir: str = None):
        self.host = host
        self.port = port
        self.evidence_dir = Path(evidence_dir)
        self.static_dir = Path(static_dir) if static_dir else Path(__file__).parent / "static"

        self.app = FastAPI(
            title="VigilZone AI Module",
            version="2.0.0",
            description="Real-time CCTV anomaly detection microservice. "
                        "Exposes REST + WebSocket + Webhook endpoints for integration.",
        )

        # CORS — allow the main-drive microservice (and any origin during dev)
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],        # tighten in production
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self.logger = setup_logger("AlertServer")

        # WebSocket clients
        self.ws_clients: List[WebSocket] = []

        # Shared state — set by main app
        self.alert_buffer: List[Dict[str, Any]] = []
        self._camera_processors = []   # set externally for live frame
        self._aggregator = None  # live aggregator reference

        # Upload jobs
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._job_alerts: Dict[str, List[Dict[str, Any]]] = {}
        self._upload_dir = Path("uploads")
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        # §A1 — staging uploads directory
        self._staging_dir = Path("data/staging_uploads")
        self._staging_dir.mkdir(parents=True, exist_ok=True)

        # §C5 — debug counters for suppression/diagnostics
        self._suppression_counters: Dict[str, int] = {}
        self._last_motion_stats: Dict[str, Any] = {}
        self._last_temporal_stats: Dict[str, Any] = {}

        # Startup time for uptime tracking
        self._start_time = time.time()

        # GPU scheduler + throttle refs (set externally)
        self._gpu_scheduler = None
        self._auto_throttle = None

        # Video processor callback (set by main app)
        self._process_video_fn = None

        # Identity subsystem refs (set externally)
        self._entity_store = None       # EntityStore
        self._face_embedder = None      # FaceEmbedder
        self._pet_embedder = None       # PetEmbedder
        self._identity_matcher = None   # IdentityMatcher
        self._identity_stabilizer = None  # IdentityStabilizer
        self._enrollment_cfg = {}       # identity.enrollment config

        # Doctor report (set externally)
        self._doctor_report = None

        # §2.2 — shared frame store for camera capture enrollment
        self._frame_store = None

        # ── Webhook registry ──────────────────────────────────────────
        self._webhooks: Dict[str, Dict[str, Any]] = {}  # id → {url, events, secret, ...}
        self._webhook_file = Path("data/webhooks.json")
        self._webhook_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_webhooks()

        self._setup_routes()

    # ── Webhook persistence helpers ───────────────────────────────────
    def _load_webhooks(self):
        """Load webhooks from disk."""
        if self._webhook_file.exists():
            try:
                self._webhooks = json.loads(self._webhook_file.read_text())
                self.logger.info(f"Loaded {len(self._webhooks)} webhook(s)")
            except Exception as e:
                self.logger.error(f"Failed to load webhooks: {e}")

    def _save_webhooks(self):
        """Persist webhooks to disk."""
        try:
            self._webhook_file.write_text(json.dumps(self._webhooks, indent=2))
        except Exception as e:
            self.logger.error(f"Failed to save webhooks: {e}")

    def set_alert_buffer(self, buffer: List[Dict[str, Any]]):
        self.alert_buffer = buffer

    def set_aggregator(self, aggregator):
        """Set live aggregator reference for real-time alert access."""
        self._aggregator = aggregator

    def set_camera_processors(self, processors):
        """Accept list of CameraProcessor for live frame endpoint."""
        self._camera_processors = processors

    # ------------------------------------------------------------------
    def _setup_routes(self):

        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            index_file = self.static_dir / "index.html"
            if index_file.exists():
                return FileResponse(index_file)
            return HTMLResponse(content="<h1>CCTV AI Module v2</h1><p>UI not found</p>")

        @self.app.get("/app.js")
        async def get_app_js():
            js_file = self.static_dir / "app.js"
            if js_file.exists():
                return FileResponse(js_file)
            return HTMLResponse(content="// Not found", media_type="application/javascript")

        @self.app.get("/alerts")
        async def get_alerts(limit: int = 200):
            if self._aggregator:
                return self._aggregator.get_recent_alerts(limit)
            return self.alert_buffer[-limit:] if self.alert_buffer else []

        @self.app.get("/evidence/{camera_id}/{filename}")
        async def get_evidence(camera_id: str, filename: str):
            file_path = self.evidence_dir / camera_id / filename
            if file_path.exists():
                return FileResponse(file_path)
            return {"error": "File not found"}

        @self.app.get("/health")
        async def health():
            count = len(self._aggregator.get_recent_alerts()) if self._aggregator else len(self.alert_buffer)
            return {
                "status": "healthy",
                "alerts_count": count,
                "ws_clients": len(self.ws_clients),
            }

        @self.app.get("/cameras")
        async def cameras():
            """List active cameras with stats."""
            result = []
            for proc in self._camera_processors:
                result.append(proc.get_stats())
            return result

        @self.app.get("/frame/{camera_id}")
        async def get_frame(camera_id: str):
            """Return latest JPEG frame for a camera."""
            for proc in self._camera_processors:
                if proc.camera_id == camera_id:
                    frame, ts = proc.reader.get_latest()
                    if frame is not None:
                        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        return StreamingResponse(
                            iter([buf.tobytes()]),
                            media_type="image/jpeg",
                            headers={"X-Timestamp": ts or ""},
                        )
            return {"error": "Camera not found or no frame available"}

        # ==============================================================
        # UPLOAD MODE (Offline Video Processing) — spec §8
        # ==============================================================
        @self.app.post("/upload_video")
        async def upload_video(file: UploadFile = File(...),
                               force_anyanomaly: bool = False):
            """Upload a video file for offline processing."""
            job_id = str(uuid.uuid4())[:12]
            video_path = self._upload_dir / f"{job_id}_{file.filename}"

            # Save uploaded file
            with open(video_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            self._jobs[job_id] = {
                "job_id": job_id,
                "filename": file.filename,
                "video_path": str(video_path),
                "status": "queued",
                "progress": 0.0,
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "force_anyanomaly": force_anyanomaly,
                "alerts_count": 0,
                "error": None,
            }
            self._job_alerts[job_id] = []

            # Launch processing in background thread
            thread = threading.Thread(
                target=self._run_upload_job,
                args=(job_id,),
                daemon=True,
            )
            thread.start()

            return {"job_id": job_id, "status": "queued"}

        @self.app.get("/jobs")
        async def list_jobs():
            """List all upload jobs."""
            return list(self._jobs.values())

        @self.app.get("/jobs/{job_id}")
        async def get_job(job_id: str):
            """Get status of a specific upload job."""
            job = self._jobs.get(job_id)
            if not job:
                return {"error": "Job not found"}
            return job

        @self.app.get("/jobs/{job_id}/alerts")
        async def get_job_alerts(job_id: str):
            """Get alerts from a completed upload job."""
            if job_id not in self._jobs:
                return {"error": "Job not found"}
            return self._job_alerts.get(job_id, [])

        # ==============================================================
        # METRICS — spec addendum §5
        # ==============================================================
        @self.app.get("/metrics")
        async def get_metrics():
            """Observability: per-lane avg_ms, p95_ms, runs/min, dropped_count, queue length, per-camera effective_sample_hz."""
            metrics: Dict[str, Any] = {
                "gpu": {},
                "cameras": {},
            }

            # GPU scheduler metrics
            if self._gpu_scheduler:
                metrics["gpu"] = self._gpu_scheduler.get_metrics()

            # Per-camera effective Hz from auto-throttle
            if self._auto_throttle:
                metrics["cameras"] = self._auto_throttle.get_metrics()

            # Camera processor stats
            for proc in self._camera_processors:
                cam_id = proc.camera_id
                if cam_id not in metrics["cameras"]:
                    metrics["cameras"][cam_id] = {}
                metrics["cameras"][cam_id]["stats"] = proc.get_stats()

            return metrics

        # ==============================================================
        # FP DEBUGGING PANEL — spec §4 (fire lane debug)
        # ==============================================================
        @self.app.get("/fire_debug")
        async def fire_debug():
            """Return top N detections from fire lane for FP debugging."""
            result = []
            for proc in self._camera_processors:
                fire_lane = proc.lanes.get("fire_smoke_yolo")
                if fire_lane and hasattr(fire_lane, "last_debug_detections"):
                    result.append({
                        "camera_id": proc.camera_id,
                        "active": getattr(fire_lane, "_active", False),
                        "detections": fire_lane.last_debug_detections,
                    })
            return result

        # ==============================================================
        # §A1 — STAGING UPLOAD + ENROLL-FROM-UPLOAD WORKFLOW
        # ==============================================================

        @self.app.post("/uploads/enroll_images")
        async def upload_enroll_images(
            files: List[UploadFile] = File(...),
            upload_id: Optional[str] = Form(None),
        ):
            """Upload images to staging area for preview before enrollment.
            If upload_id is provided, append to existing staging folder."""
            if upload_id:
                # Append to existing staging
                safe_id = Path(upload_id).name
                stage_dir = self._staging_dir / safe_id
                if not stage_dir.exists():
                    return {"error": f"Upload ID '{upload_id}' not found"}
                # Count existing files to continue numbering
                existing = list(stage_dir.iterdir())
                start_idx = len(existing)
            else:
                upload_id = f"upl_{uuid.uuid4().hex[:12]}"
                stage_dir = self._staging_dir / upload_id
                stage_dir.mkdir(parents=True, exist_ok=True)
                start_idx = 0

            stored = []
            for idx, f in enumerate(files):
                content = await f.read()
                safe_name = _sanitize_filename(f.filename or "image.jpg")
                fname = f"{start_idx + idx}_{safe_name}"
                (stage_dir / fname).write_bytes(content)
                stored.append({"filename": fname, "url": f"/staging/{upload_id}/{fname}"})

            # Return all files in staging (including previously uploaded)
            all_files = []
            for fpath in sorted(stage_dir.iterdir()):
                if fpath.is_file():
                    all_files.append({"filename": fpath.name, "url": f"/staging/{upload_id}/{fpath.name}"})

            return {"upload_id": upload_id, "stored": stored, "all_files": all_files}

        @self.app.get("/staging/{upload_id}/{filename}")
        async def serve_staging_image(upload_id: str, filename: str):
            """Serve a staged enrollment image for preview."""
            safe_name = Path(filename).name
            safe_id = Path(upload_id).name
            file_path = self._staging_dir / safe_id / safe_name
            if not file_path.exists():
                return {"error": "File not found"}
            return FileResponse(file_path)

        @self.app.post("/entities/enroll_person_from_upload")
        async def enroll_person_from_upload(
            upload_id: str = Body(...),
            name: str = Body(...),
            role: str = Body("VISITOR"),
            metadata_json: str = Body("{}"),
        ):
            """Enroll person from previously staged images (never reads UploadFile streams)."""
            if self._entity_store is None or self._face_embedder is None:
                return {"error": "Identity subsystem not enabled"}

            stage_dir = self._staging_dir / Path(upload_id).name
            if not stage_dir.exists():
                return {"error": f"Upload ID '{upload_id}' not found"}

            from ..identity.schema import EntityRecord, EntityCategory
            enroll_cfg = self._enrollment_cfg
            min_images = enroll_cfg.get("min_images", 1)
            max_embeddings = enroll_cfg.get("max_embeddings_per_entity", 10)
            outlier_z = enroll_cfg.get("outlier_reject_z", 2.5)

            # Load images from disk (robust — no stream issues)
            image_files = sorted(stage_dir.iterdir())
            embeddings_list = []
            failed_images = []
            for img_path in image_files:
                if not img_path.is_file():
                    continue
                content = img_path.read_bytes()
                arr = np.frombuffer(content, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    failed_images.append({"file": img_path.name, "reason": "decode_failed"})
                    continue
                emb = self._face_embedder.embed_from_crop(img)
                if emb is None:
                    failed_images.append({"file": img_path.name, "reason": "no_face_detected"})
                    continue
                embeddings_list.append((img_path.name, content, emb))

            if not embeddings_list:
                return {
                    "error": "No detectable face found in any uploaded image",
                    "failed_images": failed_images,
                }

            if len(embeddings_list) < min_images:
                return {
                    "error": f"Need at least {min_images} good face images, got {len(embeddings_list)}",
                    "faces_detected": len(embeddings_list),
                    "failed_images": failed_images,
                }

            # Outlier rejection
            raw_embs = [e[2] for e in embeddings_list]
            clean_embs = _outlier_reject(raw_embs, outlier_z)
            clean_embs = clean_embs[:max_embeddings]

            entity_id = self._entity_store.generate_id()
            try:
                meta = json.loads(metadata_json)
            except Exception:
                meta = {}

            record = EntityRecord(
                entity_id=entity_id,
                name=name,
                category=EntityCategory.KNOWN_PERSON,
                role=role.upper(),
                metadata=meta,
            )
            self._entity_store.add_entity(record, {})
            for emb in clean_embs:
                self._entity_store.add_embedding(entity_id, "face", emb)

            # Save enrollment images to permanent location
            img_dir = self._entity_store.enroll_img_dir / entity_id
            img_dir.mkdir(parents=True, exist_ok=True)
            saved_filenames = []
            for fname, content, _ in embeddings_list:
                (img_dir / fname).write_bytes(content)
                saved_filenames.append(fname)

            if self._identity_matcher:
                self._identity_matcher.reload_indices()

            # Delete staging folder on success
            try:
                shutil.rmtree(stage_dir)
            except Exception:
                pass

            return {
                "entity_id": entity_id,
                "name": name,
                "category": "KNOWN_PERSON",
                "embeddings_stored": len(clean_embs),
                "outlier_rejected": len(raw_embs) - len(clean_embs),
                "saved_images_count": len(saved_filenames),
                "saved_filenames": saved_filenames,
                "saved_image_urls": [f"/enroll_images/{entity_id}/{fn}" for fn in saved_filenames],
                "failed_images": failed_images,
            }

        @self.app.post("/entities/enroll_pet_from_upload")
        async def enroll_pet_from_upload(
            upload_id: str = Body(...),
            name: str = Body(...),
            metadata_json: str = Body("{}"),
        ):
            """Enroll pet from previously staged images."""
            if self._entity_store is None or self._pet_embedder is None:
                return {"error": "Identity subsystem not enabled or pet embedder unavailable"}

            stage_dir = self._staging_dir / Path(upload_id).name
            if not stage_dir.exists():
                return {"error": f"Upload ID '{upload_id}' not found"}

            from ..identity.schema import EntityRecord, EntityCategory
            enroll_cfg = self._enrollment_cfg
            max_embeddings = enroll_cfg.get("max_embeddings_per_entity", 10)
            outlier_z = enroll_cfg.get("outlier_reject_z", 2.5)

            image_files = sorted(stage_dir.iterdir())
            embeddings_list = []
            failed_images = []
            for img_path in image_files:
                if not img_path.is_file():
                    continue
                content = img_path.read_bytes()
                arr = np.frombuffer(content, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    failed_images.append({"file": img_path.name, "reason": "decode_failed"})
                    continue
                emb = self._pet_embedder.embed(img)
                if emb is None:
                    failed_images.append({"file": img_path.name, "reason": "embedding_failed"})
                    continue
                embeddings_list.append((img_path.name, content, emb))

            if not embeddings_list:
                return {
                    "error": "Could not compute embedding for any uploaded image",
                    "failed_images": failed_images,
                }

            raw_embs = [e[2] for e in embeddings_list]
            clean_embs = _outlier_reject(raw_embs, outlier_z)
            clean_embs = clean_embs[:max_embeddings]

            entity_id = self._entity_store.generate_id()
            try:
                meta = json.loads(metadata_json)
            except Exception:
                meta = {}

            record = EntityRecord(
                entity_id=entity_id,
                name=name,
                category=EntityCategory.PET,
                role="PET",
                metadata=meta,
            )
            self._entity_store.add_entity(record, {})
            for emb in clean_embs:
                self._entity_store.add_embedding(entity_id, "pet_clip", emb)

            img_dir = self._entity_store.enroll_img_dir / entity_id
            img_dir.mkdir(parents=True, exist_ok=True)
            saved_filenames = []
            for fname, content, _ in embeddings_list:
                (img_dir / fname).write_bytes(content)
                saved_filenames.append(fname)

            if self._identity_matcher:
                self._identity_matcher.reload_indices()

            try:
                shutil.rmtree(stage_dir)
            except Exception:
                pass

            return {
                "entity_id": entity_id,
                "name": name,
                "category": "PET",
                "embeddings_stored": len(clean_embs),
                "saved_images_count": len(saved_filenames),
                "saved_filenames": saved_filenames,
                "saved_image_urls": [f"/enroll_images/{entity_id}/{fn}" for fn in saved_filenames],
                "failed_images": failed_images,
            }

        # ==============================================================
        # IDENTITY ENROLLMENT API (spec §6 + calibration §3)
        # ==============================================================

        def _outlier_reject(embeddings: list, z_threshold: float = 2.5) -> list:
            """Remove outlier embeddings > z_threshold std dev from centroid."""
            if len(embeddings) <= 2:
                return embeddings
            matrix = np.stack(embeddings).astype(np.float32)
            centroid = np.mean(matrix, axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            sims = matrix @ centroid
            mean_sim = float(np.mean(sims))
            std_sim = float(np.std(sims))
            if std_sim < 1e-6:
                return embeddings
            kept = []
            for emb, sim in zip(embeddings, sims):
                z = (mean_sim - float(sim)) / std_sim  # lower sim → higher z
                if z <= z_threshold:
                    kept.append(emb)
            return kept if kept else embeddings  # never discard all

        def _sanitize_filename(raw: str) -> str:
            """Replace illegal characters in filename."""
            import re
            name = Path(raw).name
            return re.sub(r'[<>:"/\\|?*]', '_', name)

        @self.app.post("/entities/enroll_person")
        async def enroll_person(
            name: str = Form(...),
            role: str = Form("VISITOR"),
            metadata_json: str = Form("{}"),
            files: List[UploadFile] = File(...),
        ):
            """Legacy enroll person — routes through staging workflow internally (§A2)."""
            if self._entity_store is None or self._face_embedder is None:
                return {"error": "Identity subsystem not enabled"}

            # §A2: persist to staging first, then delegate to staging-based logic
            upload_id = f"upl_{uuid.uuid4().hex[:12]}"
            stage_dir = self._staging_dir / upload_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            for idx, f in enumerate(files):
                content = await f.read()
                safe_name = _sanitize_filename(f.filename or "image.jpg")
                fname = f"{idx}_{safe_name}"
                (stage_dir / fname).write_bytes(content)

            # Delegate to the staging-based enrollment logic
            return await enroll_person_from_upload(
                upload_id=upload_id,
                name=name,
                role=role,
                metadata_json=metadata_json,
            )

        @self.app.post("/entities/enroll_pet")
        async def enroll_pet(
            name: str = Form(...),
            metadata_json: str = Form("{}"),
            files: List[UploadFile] = File(...),
        ):
            """Legacy enroll pet — routes through staging workflow internally (§A2)."""
            if self._entity_store is None or self._pet_embedder is None:
                return {"error": "Identity subsystem not enabled or pet embedder unavailable"}

            # §A2: persist to staging first, then delegate
            upload_id = f"upl_{uuid.uuid4().hex[:12]}"
            stage_dir = self._staging_dir / upload_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            for idx, f in enumerate(files):
                content = await f.read()
                safe_name = _sanitize_filename(f.filename or "image.jpg")
                fname = f"{idx}_{safe_name}"
                (stage_dir / fname).write_bytes(content)

            return await enroll_pet_from_upload(
                upload_id=upload_id,
                name=name,
                metadata_json=metadata_json,
            )

        @self.app.delete("/entities/{entity_id}")
        async def delete_entity(entity_id: str):
            """Remove an enrolled entity."""
            if self._entity_store is None:
                return {"error": "Identity subsystem not enabled"}
            removed = self._entity_store.remove_entity(entity_id)
            if self._identity_matcher and removed:
                self._identity_matcher.reload_indices()
            return {"removed": removed, "entity_id": entity_id}

        # §1.4 — verification endpoints for enrollment images
        @self.app.get("/entities/{entity_id}/images")
        async def list_entity_images(entity_id: str):
            """Return list of saved enrollment image URLs for an entity."""
            if self._entity_store is None:
                return {"error": "Identity subsystem not enabled"}
            img_dir = self._entity_store.enroll_img_dir / entity_id
            if not img_dir.exists():
                return {"entity_id": entity_id, "images": []}
            filenames = sorted(f.name for f in img_dir.iterdir() if f.is_file())
            urls = [f"/enroll_images/{entity_id}/{fn}" for fn in filenames]
            return {"entity_id": entity_id, "images": urls, "filenames": filenames}

        @self.app.get("/enroll_images/{entity_id}/{filename}")
        async def serve_enroll_image(entity_id: str, filename: str):
            """Serve a saved enrollment image file."""
            if self._entity_store is None:
                return {"error": "Identity subsystem not enabled"}
            # Sanitize to prevent path traversal
            safe_name = Path(filename).name
            file_path = self._entity_store.enroll_img_dir / entity_id / safe_name
            if not file_path.exists():
                return {"error": "File not found"}
            return FileResponse(file_path)

        # ==============================================================
        # §2.1 — CAMERA CAPTURE ENROLLMENT (from live feed)
        # ==============================================================

        @self.app.post("/entities/enroll_person_from_camera")
        async def enroll_person_from_camera(
            camera_id: str = Form(...),
            name: str = Form(...),
            role: str = Form("VISITOR"),
            metadata_json: str = Form("{}"),
        ):
            """Capture a frame from a live camera and enroll a person."""
            if self._entity_store is None or self._face_embedder is None:
                return {"error": "Identity subsystem not enabled"}
            if self._frame_store is None:
                return {"error": "Frame store not available — no cameras running"}

            frame, ts = self._frame_store.get(camera_id)
            if frame is None:
                return {"error": f"No recent frame available for camera '{camera_id}'"}

            from ..identity.schema import EntityRecord, EntityCategory

            # Extract face embedding from captured frame
            emb = self._face_embedder.embed_from_crop(frame)
            if emb is None:
                return {"error": "No detectable face in captured frame. Try again when a face is visible."}

            enroll_cfg = self._enrollment_cfg
            entity_id = self._entity_store.generate_id()
            try:
                meta = json.loads(metadata_json)
            except Exception:
                meta = {}

            record = EntityRecord(
                entity_id=entity_id,
                name=name,
                category=EntityCategory.KNOWN_PERSON,
                role=role.upper(),
                metadata=meta,
            )
            self._entity_store.add_entity(record, {})
            self._entity_store.add_embedding(entity_id, "face", emb)

            # Save captured frame as enrollment image
            img_dir = self._entity_store.enroll_img_dir / entity_id
            img_dir.mkdir(parents=True, exist_ok=True)
            safe_ts = (ts or "capture").replace(":", "-").replace(" ", "_")
            fname = f"capture_{safe_ts}.jpg"
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            (img_dir / fname).write_bytes(buf.tobytes())

            if self._identity_matcher:
                self._identity_matcher.reload_indices()

            return {
                "entity_id": entity_id,
                "name": name,
                "category": "KNOWN_PERSON",
                "embeddings_stored": 1,
                "source": "camera_capture",
                "camera_id": camera_id,
                "saved_filenames": [fname],
                "capture_image_url": f"/enroll_images/{entity_id}/{fname}",
            }

        @self.app.post("/entities/enroll_pet_from_camera")
        async def enroll_pet_from_camera(
            camera_id: str = Form(...),
            name: str = Form(...),
            metadata_json: str = Form("{}"),
        ):
            """Capture a frame from a live camera and enroll a pet."""
            if self._entity_store is None or self._pet_embedder is None:
                return {"error": "Identity subsystem not enabled or pet embedder unavailable"}
            if self._frame_store is None:
                return {"error": "Frame store not available — no cameras running"}

            frame, ts = self._frame_store.get(camera_id)
            if frame is None:
                return {"error": f"No recent frame available for camera '{camera_id}'"}

            from ..identity.schema import EntityRecord, EntityCategory

            emb = self._pet_embedder.embed(frame)
            if emb is None:
                return {"error": "Could not compute pet embedding from captured frame."}

            entity_id = self._entity_store.generate_id()
            try:
                meta = json.loads(metadata_json)
            except Exception:
                meta = {}

            record = EntityRecord(
                entity_id=entity_id,
                name=name,
                category=EntityCategory.PET,
                role="PET",
                metadata=meta,
            )
            self._entity_store.add_entity(record, {})
            self._entity_store.add_embedding(entity_id, "pet_clip", emb)

            # Save captured frame
            img_dir = self._entity_store.enroll_img_dir / entity_id
            img_dir.mkdir(parents=True, exist_ok=True)
            safe_ts = (ts or "capture").replace(":", "-").replace(" ", "_")
            fname = f"capture_{safe_ts}.jpg"
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            (img_dir / fname).write_bytes(buf.tobytes())

            if self._identity_matcher:
                self._identity_matcher.reload_indices()

            return {
                "entity_id": entity_id,
                "name": name,
                "category": "PET",
                "embeddings_stored": 1,
                "source": "camera_capture",
                "camera_id": camera_id,
                "saved_filenames": [fname],
                "capture_image_url": f"/enroll_images/{entity_id}/{fname}",
            }

        @self.app.get("/entities")
        async def list_entities(category: Optional[str] = Query(None)):
            """List enrolled entities."""
            if self._entity_store is None:
                return {"error": "Identity subsystem not enabled"}
            return self._entity_store.list_entities(category)

        @self.app.post("/identity/reload")
        async def reload_identity():
            """Rebuild in-memory matcher indices from DB."""
            if self._identity_matcher is None:
                return {"error": "Identity matcher not available"}
            self._identity_matcher.reload_indices()
            return {"status": "reloaded"}

        # ==============================================================
        # IDENTITY DEBUG ENDPOINT (spec §6.2)
        # ==============================================================
        @self.app.get("/identity/state")
        async def identity_state(camera_id: str = Query(...)):
            """Return current per-track identity states for a camera."""
            if self._identity_stabilizer is None:
                return {"error": "Identity stabilizer not available"}
            states = self._identity_stabilizer.get_track_states(camera_id)
            return {"camera_id": camera_id, "tracks": states}

        # ==============================================================
        # SYSTEM DIAGNOSTICS (spec §6.3)
        # ==============================================================
        @self.app.get("/system/diagnostics")
        async def system_diagnostics():
            """Return device info, ORT providers, lane status, missing assets, suppression counters, motion stats."""
            result: Dict[str, Any] = {
                "device": {},
                "lanes": {},
                "missing_assets": [],
                "suppression_counters": {},
                "motion_stats": {},
                "temporal_verifier_stats": {},
            }

            # Device info from doctor report
            if self._doctor_report:
                dev = self._doctor_report.device_info
                result["device"] = {
                    "torch_device": dev.torch_device,
                    "torch_version": dev.torch_version,
                    "torch_gpu": dev.torch_gpu,
                    "ort_cuda": dev.ort_cuda,
                    "gpu_usable": dev.gpu_usable,
                    "device_name": dev.device_name,
                    "ort_version": dev.ort_version,
                    "ort_providers": dev.ort_providers,
                    "ort_available_providers": dev.ort_available_providers,
                }
                result["missing_assets"] = [
                    {"config_key": m.config_key, "path": m.expected_path, "fix": m.fix_hint}
                    for m in self._doctor_report.missing
                ]

            # §C5: suppression counters and motion stats from aggregator
            if self._aggregator:
                diag = self._aggregator.get_diagnostics()
                result["suppression_counters"] = diag.get("suppression_counters", {})
                result["motion_stats"] = diag.get("motion_stats", {})
                result["temporal_verifier_stats"] = diag.get("temporal_verifier_stats", {})

            # Lane status from camera processors
            for proc in self._camera_processors:
                cam_id = proc.camera_id
                enabled_cfg = proc.camera_cfg.get("enabled_lanes", [])
                active_lanes = list(proc.lanes.keys())
                disabled = [l for l in enabled_cfg if l not in active_lanes]
                # Collect model.names for lanes that have them (fire_smoke, weapon_yolo)
                lane_model_names: Dict[str, Any] = {}
                for lane_name, lane in proc.lanes.items():
                    if hasattr(lane, "model_names") and lane.model_names:
                        lane_model_names[lane_name] = lane.model_names
                result["lanes"][cam_id] = {
                    "enabled": active_lanes,
                    "disabled": disabled,
                    "model_names": lane_model_names,
                }

            return result

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self.ws_clients.append(websocket)
            self.logger.info(f"WS client connected (total: {len(self.ws_clients)})")
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                self.ws_clients.remove(websocket)
                self.logger.info(f"WS client disconnected (remaining: {len(self.ws_clients)})")

        # ==============================================================
        # WEBHOOK MANAGEMENT — register/list/delete/test webhooks
        # ==============================================================

        @self.app.post("/webhooks")
        async def register_webhook(
            url: str = Body(..., description="Target URL to POST alerts to"),
            events: List[str] = Body(
                ["alert.created"],
                description="Event types to subscribe to: alert.created, alert.resolved, entity.enrolled, entity.deleted, system.health",
            ),
            secret: Optional[str] = Body(None, description="Optional shared secret for HMAC-SHA256 signature verification"),
            metadata: Optional[Dict[str, Any]] = Body(None, description="Optional metadata for reference"),
        ):
            """
            Register a webhook endpoint. The AI module will POST events to this URL.
            Returns the webhook ID for management.
            """
            wh_id = f"wh_{uuid.uuid4().hex[:12]}"
            self._webhooks[wh_id] = {
                "id": wh_id,
                "url": url,
                "events": events,
                "secret": secret,
                "metadata": metadata or {},
                "created_at": time.time(),
                "active": True,
                "delivery_stats": {"success": 0, "failure": 0, "last_status": None},
            }
            self._save_webhooks()
            self.logger.info(f"Webhook registered: {wh_id} → {url} events={events}")
            return {"id": wh_id, "url": url, "events": events, "active": True}

        @self.app.get("/webhooks")
        async def list_webhooks():
            """List all registered webhooks."""
            return [
                {"id": w["id"], "url": w["url"], "events": w["events"],
                 "active": w.get("active", True), "delivery_stats": w.get("delivery_stats", {})}
                for w in self._webhooks.values()
            ]

        @self.app.delete("/webhooks/{webhook_id}")
        async def delete_webhook(webhook_id: str):
            """Remove a registered webhook."""
            if webhook_id not in self._webhooks:
                return JSONResponse(status_code=404, content={"error": "Webhook not found"})
            del self._webhooks[webhook_id]
            self._save_webhooks()
            return {"removed": True, "webhook_id": webhook_id}

        @self.app.put("/webhooks/{webhook_id}")
        async def update_webhook(
            webhook_id: str,
            url: Optional[str] = Body(None),
            events: Optional[List[str]] = Body(None),
            active: Optional[bool] = Body(None),
            secret: Optional[str] = Body(None),
        ):
            """Update an existing webhook's URL, events, or active status."""
            wh = self._webhooks.get(webhook_id)
            if not wh:
                return JSONResponse(status_code=404, content={"error": "Webhook not found"})
            if url is not None:
                wh["url"] = url
            if events is not None:
                wh["events"] = events
            if active is not None:
                wh["active"] = active
            if secret is not None:
                wh["secret"] = secret
            self._save_webhooks()
            return {"id": webhook_id, "url": wh["url"], "events": wh["events"], "active": wh["active"]}

        @self.app.post("/webhooks/test")
        async def test_webhook(
            url: str = Body(..., description="URL to send a test payload to"),
        ):
            """Send a test event to verify the webhook endpoint is reachable."""
            test_payload = {
                "event": "webhook.test",
                "timestamp": time.time(),
                "data": {"message": "This is a test event from VigilZone AI Module"},
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=test_payload)
                return {"status": resp.status_code, "ok": resp.is_success, "body": resp.text[:500]}
            except Exception as e:
                return {"status": None, "ok": False, "error": str(e)}

        # ==============================================================
        # MICROSERVICE INTEGRATION API  (/api/v1/...)
        # Dedicated JSON-first endpoints for the main-drive service.
        # ==============================================================

        @self.app.get("/api/v1/health")
        async def api_health():
            """
            Lightweight health-check for service-mesh / load-balancer probes.
            Returns module status, uptime, camera count, alert count.
            """
            uptime_s = time.time() - self._start_time
            cam_count = len(self._camera_processors)
            alert_count = len(self._aggregator.get_recent_alerts()) if self._aggregator else len(self.alert_buffer)
            return {
                "service": "vigilzone-ai",
                "status": "healthy",
                "version": "2.0.0",
                "uptime_seconds": round(uptime_s, 1),
                "cameras_active": cam_count,
                "alerts_total": alert_count,
                "ws_clients": len(self.ws_clients),
                "webhooks_registered": len(self._webhooks),
            }

        @self.app.get("/api/v1/alerts")
        async def api_alerts(
            limit: int = Query(50, ge=1, le=1000),
            severity: Optional[str] = Query(None, description="Filter by severity: SEVERE, HIGH, MED, LOW"),
            alert_type: Optional[str] = Query(None, description="Filter by type: FIRE_SMOKE, VIOLENCE_FIGHT, etc."),
            camera_id: Optional[str] = Query(None, description="Filter by camera"),
        ):
            """
            Fetch recent alerts with optional filters.
            Designed for polling integration (complement to webhooks).
            """
            if self._aggregator:
                all_alerts = self._aggregator.get_recent_alerts(limit=1000)
            else:
                all_alerts = [a if isinstance(a, dict) else a for a in self.alert_buffer[-1000:]]

            # Apply filters
            filtered = all_alerts
            if severity:
                filtered = [a for a in filtered if a.get("severity", "").upper() == severity.upper()]
            if alert_type:
                filtered = [a for a in filtered if a.get("type", "").upper() == alert_type.upper()]
            if camera_id:
                filtered = [a for a in filtered if a.get("camera_id") == camera_id]

            return {"count": len(filtered[-limit:]), "alerts": filtered[-limit:]}

        @self.app.get("/api/v1/cameras")
        async def api_cameras():
            """List cameras with their current status and config summary."""
            result = []
            for proc in self._camera_processors:
                stats = proc.get_stats()
                result.append({
                    "camera_id": stats.get("camera_id"),
                    "source_type": stats.get("source_type"),
                    "active": stats.get("active", True),
                    "fps": stats.get("fps"),
                    "lanes": stats.get("enabled_lanes", []),
                    "frame_count": stats.get("frame_count", 0),
                })
            return {"count": len(result), "cameras": result}

        @self.app.get("/api/v1/cameras/{camera_id}/snapshot")
        async def api_camera_snapshot(camera_id: str):
            """Get latest JPEG snapshot from a camera (for embedding in main-drive UI)."""
            for proc in self._camera_processors:
                if proc.camera_id == camera_id:
                    frame, ts = proc.reader.get_latest()
                    if frame is not None:
                        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        return StreamingResponse(
                            iter([buf.tobytes()]),
                            media_type="image/jpeg",
                            headers={"X-Timestamp": ts or "", "Cache-Control": "no-cache"},
                        )
            return JSONResponse(status_code=404, content={"error": "Camera not found or no frame"})

        @self.app.get("/api/v1/entities")
        async def api_entities(
            category: Optional[str] = Query(None, description="Filter: KNOWN_PERSON, PET"),
        ):
            """List enrolled entities."""
            if self._entity_store is None:
                return JSONResponse(status_code=503, content={"error": "Identity subsystem not enabled"})
            entities = self._entity_store.list_entities(category)
            return {"count": len(entities), "entities": entities}

        @self.app.get("/api/v1/entities/{entity_id}")
        async def api_entity_detail(entity_id: str):
            """Get a single entity with enrollment images."""
            if self._entity_store is None:
                return JSONResponse(status_code=503, content={"error": "Identity subsystem not enabled"})
            entity = self._entity_store.get_entity(entity_id)
            if not entity:
                return JSONResponse(status_code=404, content={"error": "Entity not found"})
            # Attach image URLs
            img_dir = self._entity_store.enroll_img_dir / entity_id
            images = []
            if img_dir.exists():
                images = [f"/enroll_images/{entity_id}/{f.name}" for f in sorted(img_dir.iterdir()) if f.is_file()]
            entity["images"] = images
            return entity

        @self.app.get("/api/v1/system/status")
        async def api_system_status():
            """
            Full system status for service dashboard: device info, lanes, webhooks,
            suppression stats, missing assets.
            """
            result: Dict[str, Any] = {
                "service": "vigilzone-ai",
                "version": "2.0.0",
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "device": {},
                "cameras": [],
                "webhooks": len(self._webhooks),
                "diagnostics": {},
            }
            if self._doctor_report:
                dev = self._doctor_report.device_info
                result["device"] = {
                    "torch_device": dev.torch_device,
                    "gpu_name": dev.device_name,
                    "gpu_usable": dev.gpu_usable,
                }
            for proc in self._camera_processors:
                result["cameras"].append({
                    "camera_id": proc.camera_id,
                    "active": True,
                    "lanes": list(proc.lanes.keys()),
                })
            if self._aggregator:
                result["diagnostics"] = self._aggregator.get_diagnostics()
            return result

    # ------------------------------------------------------------------
    def set_gpu_scheduler(self, scheduler):
        """Attach GPU scheduler for /metrics."""
        self._gpu_scheduler = scheduler

    def set_auto_throttle(self, throttle):
        """Attach auto-throttle for /metrics."""
        self._auto_throttle = throttle

    def set_process_video_fn(self, fn):
        """Set the callback for offline video processing."""
        self._process_video_fn = fn

    def set_doctor_report(self, report):
        """Attach startup doctor report for /system/diagnostics."""
        self._doctor_report = report

    def set_frame_store(self, frame_store):
        """§2.2 — attach shared LatestFrameStore for camera capture enrollment."""
        self._frame_store = frame_store

    def set_identity_components(self, store, face_embedder, pet_embedder, matcher,
                               stabilizer=None, enrollment_cfg=None):
        """Wire identity subsystem references for enrollment API."""
        self._entity_store = store
        self._face_embedder = face_embedder
        self._pet_embedder = pet_embedder
        self._identity_matcher = matcher
        self._identity_stabilizer = stabilizer
        self._enrollment_cfg = enrollment_cfg or {}

    # ------------------------------------------------------------------
    def _run_upload_job(self, job_id: str):
        """Process an uploaded video file (runs in background thread)."""
        job = self._jobs.get(job_id)
        if not job:
            return

        job["status"] = "processing"
        job["started_at"] = time.time()

        try:
            video_path = job["video_path"]
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                job["status"] = "error"
                job["error"] = "Cannot open video file"
                return

            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            job_alerts = []

            if self._process_video_fn:
                # Use main app's processor for full lane pipeline
                alerts = self._process_video_fn(
                    video_path=video_path,
                    job_id=job_id,
                    fps=fps,
                    force_anyanomaly=job.get("force_anyanomaly", False),
                    progress_callback=lambda p: job.__setitem__("progress", p),
                )
                job_alerts = alerts
            else:
                # Basic frame-by-frame stub processing
                frame_idx = 0
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    frame_idx += 1
                    if total_frames > 0:
                        job["progress"] = round(frame_idx / total_frames * 100, 1)
                # No alerts without lane pipeline

            cap.release()

            self._job_alerts[job_id] = [a if isinstance(a, dict) else a for a in job_alerts]
            job["alerts_count"] = len(job_alerts)
            job["status"] = "completed"
            job["finished_at"] = time.time()
            job["progress"] = 100.0

            self.logger.info(f"Upload job {job_id} completed: {len(job_alerts)} alerts")

        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
            job["finished_at"] = time.time()
            self.logger.error(f"Upload job {job_id} failed: {e}")

    # ------------------------------------------------------------------
    async def broadcast_alert(self, alert: Dict[str, Any]):
        """Push alert to WebSocket clients AND to registered webhooks."""
        # WebSocket push
        if self.ws_clients:
            message = json.dumps(alert)
            disconnected = []
            for client in self.ws_clients:
                try:
                    await client.send_text(message)
                except Exception:
                    disconnected.append(client)
            for client in disconnected:
                if client in self.ws_clients:
                    self.ws_clients.remove(client)

        # Webhook dispatch (async, non-blocking)
        await self._dispatch_webhooks("alert.created", alert)

    async def _dispatch_webhooks(self, event_type: str, data: Any):
        """POST event to all active webhooks subscribed to this event type."""
        targets = [
            wh for wh in self._webhooks.values()
            if wh.get("active", True) and event_type in wh.get("events", [])
        ]
        if not targets:
            return

        payload = {
            "event": event_type,
            "timestamp": time.time(),
            "data": data,
        }

        async def _send(wh: Dict):
            headers = {"Content-Type": "application/json", "X-Vigilzone-Event": event_type}
            # HMAC signature if secret is configured
            if wh.get("secret"):
                import hmac, hashlib
                body = json.dumps(payload)
                sig = hmac.new(wh["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
                headers["X-Vigilzone-Signature"] = f"sha256={sig}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(wh["url"], json=payload, headers=headers)
                wh.setdefault("delivery_stats", {"success": 0, "failure": 0})
                if resp.is_success:
                    wh["delivery_stats"]["success"] += 1
                else:
                    wh["delivery_stats"]["failure"] += 1
                wh["delivery_stats"]["last_status"] = resp.status_code
            except Exception as e:
                wh.setdefault("delivery_stats", {"success": 0, "failure": 0})
                wh["delivery_stats"]["failure"] += 1
                wh["delivery_stats"]["last_status"] = str(e)
                self.logger.debug(f"Webhook delivery failed ({wh['url']}): {e}")

        # Fire all webhook calls concurrently — don't block alert pipeline
        await asyncio.gather(*[_send(wh) for wh in targets], return_exceptions=True)

    async def dispatch_entity_event(self, event_type: str, entity_data: Dict[str, Any]):
        """Dispatch entity.enrolled / entity.deleted events to webhooks."""
        await self._dispatch_webhooks(event_type, entity_data)

    # ------------------------------------------------------------------
    def run(self):
        import uvicorn
        self.logger.info(f"Starting server on {self.host}:{self.port}")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="info")
