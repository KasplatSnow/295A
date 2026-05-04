"""
FastAPI server for alerts, evidence, live frame feed,
and /metrics.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query, Body
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import List, Dict, Any, Optional
import asyncio
import json
import os
import uuid
import time
import threading
import shutil
import cv2
import numpy as np
import httpx
from ..common.log import setup_logger
from ..common.runtime import get_ai_staging_dir, get_backend_config_sync_base


class AlertServer:
    """
    FastAPI server with WebSocket support for real-time alerts.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8080,
                 evidence_dir: str = "evidence"):
        self.host = host
        self.port = port
        self.evidence_dir = Path(evidence_dir)

        self.app = FastAPI(
            title="VigilZone AI Module",
            version="2.0.0",
            description="Real-time CCTV anomaly detection microservice. "
                        "Exposes REST + WebSocket + Webhook endpoints for integration.",
        )

        cors_allowed_origins = [
            origin.strip()
            for origin in str(os.getenv("AI_CORS_ALLOWED_ORIGINS", "*") or "*").split(",")
            if origin.strip()
        ]
        allow_all_origins = cors_allowed_origins == ["*"]

        # CORS — open by default for local/dev, configurable for cloud deployments.
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_allowed_origins,
            allow_credentials=not allow_all_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self.logger = setup_logger("AlertServer")

        # WebSocket clients
        self.ws_clients: List[WebSocket] = []

        # Shared state — set by main app
        self.alert_buffer: List[Dict[str, Any]] = []
        self._camera_processors = {}   # set externally for live frame
        self._aggregator = None  # live aggregator reference

        # §A1 — staging uploads directory
        self._staging_dir = get_ai_staging_dir(Path.cwd())
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

        # Identity subsystem refs (set externally)
        self._entity_store = None       # EntityStore
        self._face_embedder = None      # FaceEmbedder
        self._pet_embedder = None       # PetEmbedder
        self._identity_matcher = None   # IdentityMatcher
        self._identity_stabilizer = None  # IdentityStabilizer
        self._enrollment_cfg = {}       # identity.enrollment config

        # Doctor report (set externally)
        self._doctor_report = None

        # App context for hot-loading cameras (set externally)
        self._app_context: Dict[str, Any] = {}

        # §2.2 — shared frame store for camera capture enrollment
        self._frame_store = None

        self._backend_sync_base = get_backend_config_sync_base().rstrip("/")
        self._sync_token = os.getenv("AI_WEBHOOK_TOKEN", "")
        self._sync_secret = os.getenv("AI_WEBHOOK_SECRET", "")

        # ── Webhook registry ──────────────────────────────────────────
        self._webhooks: Dict[str, Dict[str, Any]] = {}  # id → {url, events, secret, ...}
        self._load_webhooks()

        self._setup_routes()

    # ── Webhook persistence helpers ───────────────────────────────────
    def _load_webhooks(self):
        """Load webhooks only from canonical backend snapshot."""
        remote_webhooks = self._fetch_webhooks_from_backend()
        if isinstance(remote_webhooks, dict):
            merged: Dict[str, Dict[str, Any]] = {}
            for webhook_id, remote_payload in remote_webhooks.items():
                if not isinstance(remote_payload, dict):
                    continue

                existing_payload = self._webhooks.get(webhook_id, {})
                has_secret = bool(remote_payload.get("has_secret"))
                secret_value = None
                if has_secret:
                    secret_value = existing_payload.get("secret") or (self._sync_secret or None)
                    if secret_value is None:
                        self.logger.warning(
                            "Webhook %s requires a secret but no canonical runtime secret is configured",
                            webhook_id,
                        )

                merged[webhook_id] = {
                    "id": remote_payload.get("id") or webhook_id,
                    "url": remote_payload.get("url", ""),
                    "events": list(remote_payload.get("events") or []),
                    "secret": secret_value,
                    "metadata": remote_payload.get("metadata") or {},
                    "created_at": remote_payload.get("created_at") or time.time(),
                    "active": bool(remote_payload.get("active", True)),
                    "delivery_stats": remote_payload.get("delivery_stats")
                    or {"success": 0, "failure": 0, "last_status": None},
                }

            self._webhooks = merged
            self.logger.info("Loaded %s webhook(s) from backend snapshot", len(self._webhooks))
            return

        self._webhooks = {}
        self.logger.warning("Webhook snapshot unavailable from backend; starting with empty registry")

    def _fetch_webhooks_from_backend(self) -> Optional[Dict[str, Dict[str, Any]]]:
        if not self._backend_sync_base:
            return None

        headers: Dict[str, str] = {}
        if self._sync_token:
            headers["X-AI-WEBHOOK-TOKEN"] = self._sync_token
        elif self._sync_secret:
            import hmac
            import hashlib

            # GET request body is empty; sign empty payload for HMAC auth parity.
            signature = hmac.new(self._sync_secret.encode(), b"", hashlib.sha256).hexdigest()
            headers["X-Vigilzone-Signature"] = f"sha256={signature}"

        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self._backend_sync_base}/webhooks/snapshot/", headers=headers)
            if not resp.is_success:
                self.logger.debug(
                    "Webhook snapshot fetch failed: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None

            payload = resp.json()
            if isinstance(payload, dict):
                webhooks = payload.get("webhooks", payload)
                if isinstance(webhooks, dict):
                    return webhooks
        except Exception as exc:
            self.logger.debug("Webhook snapshot fetch failed: %s", exc)

        return None

    def _fetch_camera_config_from_backend(self, camera_id: str) -> Optional[Dict[str, Any]]:
        if not self._backend_sync_base:
            return None

        headers: Dict[str, str] = {}
        if self._sync_token:
            headers["X-AI-WEBHOOK-TOKEN"] = self._sync_token
        elif self._sync_secret:
            import hmac
            import hashlib

            signature = hmac.new(self._sync_secret.encode(), b"", hashlib.sha256).hexdigest()
            headers["X-Vigilzone-Signature"] = f"sha256={signature}"

        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self._backend_sync_base}/cameras/snapshot/", headers=headers)
            if not resp.is_success:
                self.logger.debug(
                    "Camera snapshot fetch failed: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None

            payload = resp.json()
            cameras = payload.get("cameras", []) if isinstance(payload, dict) else []
            if not isinstance(cameras, list):
                return None

            for row in cameras:
                if not isinstance(row, dict):
                    continue
                if str(row.get("camera_id", "")).strip() != camera_id:
                    continue

                enabled_lanes = list(row.get("enabled_lanes") or [])
                effective_entity_detection_enabled = bool(
                    row.get("effective_entity_detection_enabled", True)
                )
                if not effective_entity_detection_enabled:
                    enabled_lanes = [lane for lane in enabled_lanes if lane != "entity_identity"]

                return {
                    "camera_id": camera_id,
                    "rtsp_url": row.get("rtsp_url") or "",
                    "ingest_backend": row.get("ingest_backend") or "opencv",
                    "enabled_lanes": enabled_lanes,
                    "sample_hz": row.get("sample_hz") or 2.0,
                    "source_type": row.get("source_type") or ("rtsp" if row.get("rtsp_url") else "live_camera"),
                    "tenant_id": row.get("tenant_id"),
                    "community_id": row.get("community_id"),
                    "camera_name": row.get("camera_name") or camera_id,
                    "stream_path": row.get("stream_path") or camera_id,
                    "policy_version": row.get("policy_version") or 1,
                    "entity_detection_enabled": bool(row.get("entity_detection_enabled", True)),
                    "identity_runtime_enabled": bool(row.get("identity_runtime_enabled", True)),
                    "effective_entity_detection_enabled": effective_entity_detection_enabled,
                }
        except Exception as exc:
            self.logger.debug("Camera snapshot fetch failed: %s", exc)

        return None

    def _save_webhooks(self):
        """Mirror in-memory webhooks to canonical backend registry."""
        try:
            self._sync_webhooks_to_backend()
        except Exception as e:
            self.logger.error(f"Failed to save webhooks: {e}")

    def _post_internal_sync(self, path: str, payload: Dict[str, Any]) -> None:
        if not self._backend_sync_base:
            return

        url = f"{self._backend_sync_base}/{path.lstrip('/')}"
        headers = {"Content-Type": "application/json"}

        try:
            with httpx.Client(timeout=3.0) as client:
                if self._sync_token:
                    headers["X-AI-WEBHOOK-TOKEN"] = self._sync_token
                    client.post(url, json=payload, headers=headers)
                    return

                if self._sync_secret:
                    import hmac
                    import hashlib

                    body = json.dumps(payload)
                    sig = hmac.new(
                        self._sync_secret.encode(),
                        body.encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    headers["X-Vigilzone-Signature"] = f"sha256={sig}"
                    client.post(url, content=body, headers=headers)
                    return

                client.post(url, json=payload, headers=headers)
        except Exception as exc:
            self.logger.debug("Internal sync call failed for %s: %s", path, exc)

    def _sync_webhooks_to_backend(self):
        self._post_internal_sync("webhooks/sync/", {"webhooks": self._webhooks})

    def _sync_runtime_to_backend(self, payload: Dict[str, Any]):
        self._post_internal_sync("runtime/sync/", payload)

    @staticmethod
    def _identity_mutation_compat_enabled() -> bool:
        token = str(os.getenv("AI_ALLOW_IDENTITY_MUTATION_COMPAT", "")).strip().lower()
        return token in {"1", "true", "yes", "on"}

    def _require_known_entity_id(self, known_entity_id: Optional[str]):
        if self._identity_mutation_compat_enabled():
            return None
        if known_entity_id not in (None, ""):
            return None
        return JSONResponse(
            status_code=409,
            content={
                "error": (
                    "Legacy AI-owned canonical identity mutation is disabled. "
                    "Pass known_entity_id from backend canonical entity lifecycle."
                )
            },
        )

    def set_alert_buffer(self, buffer: List[Dict[str, Any]]):
        self.alert_buffer = buffer

    def set_aggregator(self, aggregator):
        """Set live aggregator reference for real-time alert access."""
        self._aggregator = aggregator

    def set_camera_processors(self, processors):
        """Accept list of CameraProcessor for live frame endpoint."""
        self._camera_processors = {proc.camera_id: proc for proc in processors}

    def set_app_context(self, ctx: dict):
        """
        Store shared application objects needed to hot-create new
        CameraProcessor instances at runtime (register endpoint).
        Expected keys: evidence_exporter, models_cfg, zones_cfg,
                       anyanomaly_client.
        """
        self._app_context = ctx

    def _find_camera_processor(self, camera_id: str):
        return self._camera_processors.get(camera_id)

    def _start_camera_processor(self, cam_cfg: Dict[str, Any]) -> bool:
        """Start one camera processor at runtime; returns True when started."""
        camera_id = str(cam_cfg.get("camera_id", "")).strip()
        if not camera_id:
            return False
        if self._find_camera_processor(camera_id):
            return True

        ctx = getattr(self, "_app_context", {})
        evidence_exp = ctx.get("evidence_exporter")
        models_cfg = ctx.get("models_cfg")
        zones_cfg = ctx.get("zones_cfg", {})
        anyanomaly = ctx.get("anyanomaly_client")

        if not (evidence_exp and models_cfg and self._aggregator):
            self.logger.error("Cannot hot-load %s: missing app context", camera_id)
            return False

        try:
            from ..app import CameraProcessor

            cfg = dict(cam_cfg)
            zones = zones_cfg.get(camera_id, [])
            proc = CameraProcessor(
                cfg, models_cfg, zones,
                self._aggregator, evidence_exp,
                gpu_scheduler=self._gpu_scheduler,
                auto_throttle=self._auto_throttle,
                anyanomaly_client=anyanomaly,
                face_embedder=self._face_embedder,
                pet_embedder=self._pet_embedder,
                identity_matcher=self._identity_matcher,
                identity_stabilizer=self._identity_stabilizer,
                frame_store=self._frame_store,
            )
            proc.start()
            self._camera_processors[camera_id] = proc
            self.logger.info("Hot-loaded camera processor for %s", camera_id)
            return True
        except Exception as exc:
            self.logger.error("Hot-load failed for %s: %s", camera_id, exc)
            return False

    def _stop_camera_processor(self, camera_id: str) -> bool:
        """Stop one running camera processor by id; returns True when stopped."""
        proc = self._find_camera_processor(camera_id)
        if not proc:
            return False
        try:
            proc.stop()
        except Exception as exc:
            self.logger.warning("Camera stop error for %s: %s", camera_id, exc)
        try:
            del self._camera_processors[camera_id]
        except KeyError:
            pass
        return True

    def _update_runtime_camera_metadata(self, camera_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Persist camera metadata used to enrich outbound alert events."""
        clean_metadata = {
            key: value
            for key, value in (metadata or {}).items()
            if key != "enabled" and value not in (None, "")
        }
        if not clean_metadata:
            return {}

        proc = self._find_camera_processor(camera_id)
        if proc is not None:
            proc.camera_cfg.update(clean_metadata)

        ctx = self._app_context if isinstance(self._app_context, dict) else {}
        camera_configs_by_id = ctx.get("camera_configs_by_id", {})
        if camera_id in camera_configs_by_id:
            camera_configs_by_id[camera_id].update(clean_metadata)

        success = True

        self._sync_runtime_to_backend({
            "camera_id": camera_id,
            **clean_metadata,
        })

        return {"metadata": clean_metadata, "success": success}

    @staticmethod
    def _parse_runtime_control_payload(payload: Any) -> tuple[bool, Dict[str, Any]]:
        """Accept either the legacy raw boolean or a richer metadata object."""
        if isinstance(payload, bool):
            return payload, {}
        if isinstance(payload, dict):
            enabled = payload.get("enabled")
            if isinstance(enabled, bool):
                return enabled, {k: v for k, v in payload.items() if k != "enabled"}
            if isinstance(enabled, str):
                lowered = enabled.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True, {k: v for k, v in payload.items() if k != "enabled"}
                if lowered in {"0", "false", "no", "off"}:
                    return False, {k: v for k, v in payload.items() if k != "enabled"}
        raise ValueError("runtime-control body must be a boolean or an object with boolean enabled")

    # ------------------------------------------------------------------
    def _setup_routes(self):

        @self.app.get("/")
        async def root():
            """Service root – returns basic health/identity info."""
            return {
                "service": "vigilzone-ai",
                "version": "2.0.0",
                "status": "running",
                "uptime_s": round(time.time() - self._start_time, 1),
            }

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
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "File not found"}, status_code=404)

        @self.app.get("/health")
        async def health():
            count = len(self._aggregator.get_recent_alerts()) if self._aggregator else len(self.alert_buffer)
            redis_transport = {}
            try:
                from ..services.redis_publisher import get_publisher
                redis_transport = get_publisher().status_snapshot()
            except Exception as exc:
                redis_transport = {"ready": False, "error": str(exc)}
            return {
                "status": "healthy",
                "alerts_count": count,
                "ws_clients": len(self.ws_clients),
                "redis_transport": redis_transport,
            }

        @self.app.get("/cameras")
        async def cameras():
            """List active cameras with stats."""
            result = []
            for proc in self._camera_processors.values():
                result.append(proc.get_stats())
            return result

        @self.app.get("/frame/{camera_id}")
        async def get_frame(
            camera_id: str,
            quality: int = Query(80, ge=10, le=100, description="JPEG quality (10-100)"),
            maxw: Optional[int] = Query(None, ge=160, le=3840, description="Max width — downscale for display (inference unaffected)"),
        ):
            """Return latest JPEG frame for a camera.

            Use ?quality=50&maxw=640 from the UI for faster preview frames.
            The AI inference pipeline always processes the full-resolution
            frame — this only affects the served JPEG.
            """
            # Verify camera exists
            proc_found = camera_id in self._camera_processors
            if not proc_found:
                return JSONResponse(status_code=404, content={"error": f"Camera '{camera_id}' not found"})

            # Primary: non-consumptive LatestFrameStore (written by processing loop)
            frame, ts = None, None
            source = "frame_store"
            if self._frame_store is not None:
                frame, ts = self._frame_store.get(camera_id)

            # Fallback: consumptive reader (only useful before first process-loop iteration)
            if frame is None:
                source = "reader_fallback"
                for proc in self._camera_processors.values():
                    if proc.camera_id == camera_id:
                        frame, ts = proc.reader.get_latest()
                        break

            if frame is None:
                return JSONResponse(status_code=404, content={"error": "No frame available yet"})

            out = frame
            h, w = out.shape[:2]
            if maxw and w > maxw:
                scale = maxw / w
                out = cv2.resize(out, (maxw, int(h * scale)), interpolation=cv2.INTER_AREA)
            _, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return StreamingResponse(
                iter([buf.tobytes()]),
                media_type="image/jpeg",
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Frame-Timestamp": ts or "",
                    "X-Frame-Source": source,
                    "Cache-Control": "no-store",
                    "X-Original-Size": f"{w}x{h}",
                },
            )

        # ==============================================================
        # UPLOAD MODE (Offline Video Processing) — REMOVED
        # ==============================================================

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
            for proc in self._camera_processors.values():
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
            for proc in self._camera_processors.values():
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
            tenant_id: Optional[str] = Body(None),
            known_entity_id: Optional[str] = Body(None),
        ):
            """Enroll person from previously staged images (never reads UploadFile streams)."""
            if self._entity_store is None or self._face_embedder is None:
                return {"error": "Identity subsystem not enabled"}

            compat_error = self._require_known_entity_id(known_entity_id)
            if compat_error is not None:
                return compat_error

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
            if not isinstance(meta, dict):
                meta = {}
            if tenant_id not in (None, ""):
                meta["tenant_id"] = str(tenant_id)
            if known_entity_id not in (None, ""):
                meta["known_entity_id"] = str(known_entity_id)

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
            tenant_id: Optional[str] = Body(None),
            known_entity_id: Optional[str] = Body(None),
        ):
            """Enroll pet from previously staged images."""
            if self._entity_store is None or self._pet_embedder is None:
                return {"error": "Identity subsystem not enabled or pet embedder unavailable"}

            compat_error = self._require_known_entity_id(known_entity_id)
            if compat_error is not None:
                return compat_error

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
            if not isinstance(meta, dict):
                meta = {}
            if tenant_id not in (None, ""):
                meta["tenant_id"] = str(tenant_id)
            if known_entity_id not in (None, ""):
                meta["known_entity_id"] = str(known_entity_id)

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
            tenant_id: Optional[str] = Form(None),
            known_entity_id: Optional[str] = Form(None),
        ):
            """Legacy enroll person — routes through staging workflow internally (§A2)."""
            if self._entity_store is None or self._face_embedder is None:
                return {"error": "Identity subsystem not enabled"}

            compat_error = self._require_known_entity_id(known_entity_id)
            if compat_error is not None:
                return compat_error

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
                tenant_id=tenant_id,
                known_entity_id=known_entity_id,
            )

        @self.app.post("/entities/enroll_pet")
        async def enroll_pet(
            name: str = Form(...),
            metadata_json: str = Form("{}"),
            files: List[UploadFile] = File(...),
            tenant_id: Optional[str] = Form(None),
            known_entity_id: Optional[str] = Form(None),
        ):
            """Legacy enroll pet — routes through staging workflow internally (§A2)."""
            if self._entity_store is None or self._pet_embedder is None:
                return {"error": "Identity subsystem not enabled or pet embedder unavailable"}

            compat_error = self._require_known_entity_id(known_entity_id)
            if compat_error is not None:
                return compat_error

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
                tenant_id=tenant_id,
                known_entity_id=known_entity_id,
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
            tenant_id: Optional[str] = Form(None),
            known_entity_id: Optional[str] = Form(None),
        ):
            """Capture a frame from a live camera and enroll a person."""
            if self._entity_store is None or self._face_embedder is None:
                return {"error": "Identity subsystem not enabled"}
            if self._frame_store is None:
                return {"error": "Frame store not available — no cameras running"}

            compat_error = self._require_known_entity_id(known_entity_id)
            if compat_error is not None:
                return compat_error

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
            if not isinstance(meta, dict):
                meta = {}
            if tenant_id not in (None, ""):
                meta["tenant_id"] = str(tenant_id)
            if known_entity_id not in (None, ""):
                meta["known_entity_id"] = str(known_entity_id)

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
            tenant_id: Optional[str] = Form(None),
            known_entity_id: Optional[str] = Form(None),
        ):
            """Capture a frame from a live camera and enroll a pet."""
            if self._entity_store is None or self._pet_embedder is None:
                return {"error": "Identity subsystem not enabled or pet embedder unavailable"}
            if self._frame_store is None:
                return {"error": "Frame store not available — no cameras running"}

            compat_error = self._require_known_entity_id(known_entity_id)
            if compat_error is not None:
                return compat_error

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
            if not isinstance(meta, dict):
                meta = {}
            if tenant_id not in (None, ""):
                meta["tenant_id"] = str(tenant_id)
            if known_entity_id not in (None, ""):
                meta["known_entity_id"] = str(known_entity_id)

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

        # ==============================================================
        # §BACKEND-OWNED — Stateless Embedding Generation Endpoint
        # ==============================================================

        @self.app.post("/api/v1/embeddings/generate")
        async def generate_embeddings(
            modality: str = Form(...),
            files: List[UploadFile] = File(...),
        ):
            """Stateless embedding generation — returns vectors without side-effects.

            This endpoint is called by the backend EntityProcessingService to
            compute embeddings from enrollment images. It does NOT create entities,
            store images, sync back, or mutate any state. The backend owns all
            persistence and lifecycle management.

            Parameters
            ----------
            modality : str
                "face" or "pet_clip"
            files : list of UploadFile
                Enrollment images to embed

            Returns
            -------
            dict with:
                embeddings: list of {filename, vector, dim, quality_score, embedding_model}
                failed: list of {filename, reason}
                outlier_rejected: int (count of embeddings removed by outlier filter)
            """
            if modality == "face":
                if self._face_embedder is None or not self._face_embedder.available:
                    return JSONResponse(
                        status_code=503,
                        content={"error": "Face embedder not available"},
                    )
            elif modality == "pet_clip":
                if self._pet_embedder is None or not self._pet_embedder.available:
                    return JSONResponse(
                        status_code=503,
                        content={"error": "Pet embedder not available"},
                    )
            else:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Unsupported modality: {modality}. Use 'face' or 'pet_clip'."},
                )

            enroll_cfg = self._enrollment_cfg
            outlier_z = enroll_cfg.get("outlier_reject_z", 2.5)
            max_embeddings = enroll_cfg.get("max_embeddings_per_entity", 10)

            raw_results = []
            failed = []

            for f in files:
                content = await f.read()
                fname = f.filename or "unknown.jpg"
                arr = np.frombuffer(content, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    failed.append({"filename": fname, "reason": "decode_failed"})
                    continue

                if modality == "face":
                    emb = self._face_embedder.embed_from_crop(img)
                    model_name = f"insightface/{self._face_embedder._model_pack}"
                    if emb is None:
                        failed.append({"filename": fname, "reason": "no_face_detected"})
                        continue
                    # Quality score: use detection score if available via full detect
                    faces = self._face_embedder.detect_faces(img)
                    quality = float(faces[0].det_score) if faces else None
                else:
                    emb = self._pet_embedder.embed(img)
                    model_name = f"open_clip/{self._pet_embedder._clip_model_name}"
                    if emb is None:
                        failed.append({"filename": fname, "reason": "embedding_failed"})
                        continue
                    quality = None

                raw_results.append({
                    "filename": fname,
                    "vector": emb,
                    "quality_score": quality,
                    "embedding_model": model_name,
                })

            # Outlier rejection on raw embeddings
            raw_vectors = [r["vector"] for r in raw_results]
            outlier_rejected = 0
            if len(raw_vectors) > 2:
                clean_vectors = _outlier_reject(raw_vectors, outlier_z)
                clean_set = {id(v) for v in clean_vectors}
                before_count = len(raw_results)
                raw_results = [r for r in raw_results if id(r["vector"]) in clean_set]
                outlier_rejected = before_count - len(raw_results)

            # Cap at max embeddings
            raw_results = raw_results[:max_embeddings]

            # Serialize vectors for JSON transport
            embeddings_out = []
            for r in raw_results:
                vec = r["vector"]
                embeddings_out.append({
                    "filename": r["filename"],
                    "vector": [float(x) for x in vec.tolist()],
                    "dim": len(vec),
                    "quality_score": r.get("quality_score"),
                    "embedding_model": r.get("embedding_model", ""),
                })

            return {
                "modality": modality,
                "embeddings": embeddings_out,
                "failed": failed,
                "outlier_rejected": outlier_rejected,
                "total_images": len(files),
            }

        @self.app.get("/entities")
        async def list_entities(category: Optional[str] = Query(None)):
            """List enrolled entities."""
            if self._entity_store is None:
                return {"error": "Identity subsystem not enabled"}
            entities = self._entity_store.list_entities(category)
            for entity in entities:
                metadata = entity.get("metadata") or {}
                entity["allowed_camera_ids"] = metadata.get("allowed_camera_ids", [])
                entity["last_seen"] = metadata.get("last_seen")
                entity["last_camera_id"] = metadata.get("last_camera_id")
            return entities

        @self.app.put("/entities/{entity_id}")
        async def update_entity(
            entity_id: str,
            payload: Dict[str, Any] = Body(default={}),
        ):
            """Update an enrolled entity's metadata and optional display fields."""
            if self._entity_store is None:
                return JSONResponse(
                    status_code=503,
                    content={"error": "Identity subsystem not enabled"},
                )
            updated = self._entity_store.update_entity(
                entity_id=entity_id,
                name=payload.get("name"),
                role=payload.get("role"),
                category=payload.get("category"),
                metadata=payload.get("metadata"),
            )
            if updated is None:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Entity not found: {entity_id}"},
                )
            if self._identity_matcher:
                self._identity_matcher.reload_indices()
            metadata = updated.get("metadata") or {}
            updated["allowed_camera_ids"] = metadata.get("allowed_camera_ids", [])
            updated["last_seen"] = metadata.get("last_seen")
            updated["last_camera_id"] = metadata.get("last_camera_id")
            return updated

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

        @self.app.get("/debug/identity_last_match")
        async def debug_identity_last_match(
            camera_id: str = Query(...),
            track_id: int = Query(...),
        ):
            """Return raw + stabilized identity info for a specific track.
            Useful for diagnosing identity display issues."""
            result: Dict[str, Any] = {
                "camera_id": camera_id,
                "track_id": track_id,
                "raw_cache": None,
                "stabilized": None,
                "entity_store_record": None,
            }

            # Raw identity cache from aggregator
            if self._aggregator:
                cache = self._aggregator._identity_cache.get(camera_id, {})
                raw = cache.get(track_id)
                if raw:
                    result["raw_cache"] = raw

            # Stabilized state
            if self._identity_stabilizer:
                states = self._identity_stabilizer.get_track_states(camera_id)
                for s in states:
                    if s.get("track_id") == track_id:
                        result["stabilized"] = s
                        break

            # Entity store record lookup (if there's an entity_id)
            eid = None
            if result["stabilized"] and result["stabilized"].get("entity_id"):
                eid = result["stabilized"]["entity_id"]
            elif result["raw_cache"] and result["raw_cache"].get("entity_id"):
                eid = result["raw_cache"]["entity_id"]

            if eid and self._entity_store:
                try:
                    rec = self._entity_store.get_entity(eid)
                    if rec:
                        result["entity_store_record"] = {
                            "entity_id": rec.get("entity_id"),
                            "name": rec.get("name"),
                            "category": rec.get("category"),
                            "role": rec.get("role"),
                        }
                except Exception:
                    pass

            return result

        @self.app.get("/debug/fall_state")
        async def debug_fall_state(camera_id: str = Query(...)):
            """Return last per-track fall features (angle, hip_drop, stillness, pose_conf)."""
            for proc in self._camera_processors.values():
                if proc.camera_id == camera_id:
                    fall_lane = proc.lanes.get("fall_candidate")
                    if fall_lane and hasattr(fall_lane, "last_fall_state"):
                        return {
                            "camera_id": camera_id,
                            "tracks": fall_lane.last_fall_state,
                        }
                    return {"camera_id": camera_id, "tracks": {}, "error": "fall_candidate lane not active"}
            return {"error": f"Camera {camera_id} not found"}

        # ==============================================================
        # SYSTEM DIAGNOSTICS (spec §6.3)
        # ==============================================================
        @self.app.get("/system/diagnostics")
        async def system_diagnostics():
            """Return device info, ORT providers, lane status, missing assets, suppression counters, motion stats, incident registry."""
            result: Dict[str, Any] = {
                "device": {},
                "lanes": {},
                "missing_assets": [],
                "suppression_counters": {},
                "motion_stats": {},
                "temporal_verifier_stats": {},
                "incident_registry": {},
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
                result["incident_registry"] = diag.get("incident_registry", {})

            # Lane status from camera processors
            for proc in self._camera_processors.values():
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
            redis_transport = {}
            try:
                from ..services.redis_publisher import get_publisher
                redis_transport = get_publisher().status_snapshot()
            except Exception as exc:
                redis_transport = {"ready": False, "error": str(exc)}
            return {
                "service": "vigilzone-ai",
                "status": "healthy",
                "version": "2.0.0",
                "uptime_seconds": round(uptime_s, 1),
                "cameras_active": cam_count,
                "alerts_total": alert_count,
                "ws_clients": len(self.ws_clients),
                "webhooks_registered": len(self._webhooks),
                "redis_transport": redis_transport,
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
            for proc in self._camera_processors.values():
                stats = proc.get_stats()
                result.append({
                    "camera_id": stats.get("camera_id"),
                    "source_type": stats.get("source_type"),
                    "active": stats.get("active", stats.get("connected", False)),
                    "fps": stats.get("fps", 0.0),
                    "lanes": stats.get("active_lanes", []),
                    "frame_count": stats.get("frame_count", stats.get("frames_processed", 0)),
                })
            return {"count": len(result), "cameras": result}

        @self.app.get("/api/v1/cameras/{camera_id}/runtime-status")
        async def api_camera_runtime_status(camera_id: str):
            """Return runtime status for a specific camera id."""
            proc = self._find_camera_processor(camera_id)
            return {
                "camera_id": camera_id,
                "running": proc is not None,
            }

        @self.app.post("/api/v1/cameras/{camera_id}/runtime-control")
        async def api_camera_runtime_control(
            camera_id: str,
            payload: Any = Body(..., description="true/false or { enabled, tenant_id, ... }"),
        ):
            """Runtime start/stop control (fully generalized for any registered camera)."""
            try:
                enabled, runtime_metadata = self._parse_runtime_control_payload(payload)
            except ValueError as exc:
                return JSONResponse(status_code=400, content={"error": str(exc)})

            running = self._find_camera_processor(camera_id) is not None
            if enabled and running:
                metadata_result = self._update_runtime_camera_metadata(camera_id, runtime_metadata)
                self._sync_runtime_to_backend({
                    "camera_id": camera_id,
                    "enabled": True,
                    "running": True,
                    **(metadata_result.get("metadata") or {}),
                })
                return {
                    "camera_id": camera_id,
                    "running": True,
                    "changed": False,
                    "metadata_applied": metadata_result,
                }
            if (not enabled) and (not running):
                metadata_result = self._update_runtime_camera_metadata(camera_id, runtime_metadata)
                self._sync_runtime_to_backend({
                    "camera_id": camera_id,
                    "enabled": False,
                    "running": False,
                    **(metadata_result.get("metadata") or {}),
                })
                return {
                    "camera_id": camera_id,
                    "running": False,
                    "changed": False,
                    "metadata_applied": metadata_result,
                }

            if not enabled:
                stopped = self._stop_camera_processor(camera_id)
                metadata_result = self._update_runtime_camera_metadata(camera_id, runtime_metadata)
                self._sync_runtime_to_backend({
                    "camera_id": camera_id,
                    "enabled": False,
                    "running": False,
                    **(metadata_result.get("metadata") or {}),
                })
                return {
                    "camera_id": camera_id,
                    "running": False,
                    "changed": stopped,
                    "metadata_applied": metadata_result,
                }

            # If enabling, we need a config.
            cfg = None
            # 1. Try in-memory app context
            camera_configs_by_id = self._app_context.get("camera_configs_by_id", {}) if isinstance(self._app_context, dict) else {}
            if isinstance(camera_configs_by_id, dict):
                cfg = camera_configs_by_id.get(camera_id)

            # 2. Canonical fallback from backend snapshot.
            if not cfg:
                cfg = self._fetch_camera_config_from_backend(camera_id)

            if not cfg:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Camera config '{camera_id}' not found in canonical registry."},
                )

            started = self._start_camera_processor(cfg)
            if not started:
                return JSONResponse(
                    status_code=500,
                    content={"error": f"Failed to start camera '{camera_id}'"},
                )

            metadata_result = self._update_runtime_camera_metadata(camera_id, runtime_metadata)
            self._sync_runtime_to_backend({
                "camera_id": camera_id,
                "enabled": True,
                "running": True,
                "ingest_backend": cfg.get("ingest_backend", "opencv"),
                "sample_hz": cfg.get("sample_hz", 2.0),
                "enabled_lanes": cfg.get("enabled_lanes", []),
                "entity_detection_enabled": cfg.get("entity_detection_enabled", True),
                "identity_runtime_enabled": cfg.get("identity_runtime_enabled", True),
                "effective_entity_detection_enabled": cfg.get("effective_entity_detection_enabled", True),
                "tenant_id": cfg.get("tenant_id"),
                "community_id": cfg.get("community_id"),
                "camera_name": cfg.get("camera_name"),
                "stream_path": cfg.get("stream_path") or camera_id,
                "rtsp_url": cfg.get("rtsp_url", ""),
                **(metadata_result.get("metadata") or {}),
            })
            return {
                "camera_id": camera_id,
                "running": True,
                "changed": True,
                "metadata_applied": metadata_result,
            }
 
        @self.app.get("/stream/{camera_id}")
        async def stream_camera(camera_id: str):
            """Stream MJPEG frames for a camera.
            
            This is used by MediaMTX to ingest the AI processed feed reliably
            over a long-lived connection.
            """
            # Verify camera exists
            proc_found = camera_id in self._camera_processors
            if not proc_found:
                return JSONResponse(status_code=404, content={"error": f"Camera '{camera_id}' not found"})

            async def frame_generator():
                last_ts = None
                while True:
                    if self._frame_store is None:
                        await asyncio.sleep(0.5)
                        continue
                    
                    frame, ts = self._frame_store.get(camera_id)
                    if frame is None or ts == last_ts:
                        # Wait for a fresh frame (AI module target is ~10fps)
                        await asyncio.sleep(0.05)
                        continue
                    
                    last_ts = ts

                    # Ultra-Light (360p) Optimization: 640px wide
                    h, w = frame.shape[:2]
                    target_w = 640
                    if w > target_w:
                        scale = target_w / w
                        frame = cv2.resize(frame, (target_w, int(h * scale)), interpolation=cv2.INTER_AREA)

                    # Encode at medium quality for the stream
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    yield (
                        b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n'
                    )

            return StreamingResponse(
                frame_generator(),
                media_type="multipart/x-mixed-replace; boundary=frame",
                headers={
                    "Cache-Control": "no-store",
                    "X-Stream-Source": "ai-detection-feed",
                }
            )


        @self.app.post("/api/v1/cameras/register")
        async def api_register_camera(
            camera_id: str = Body(..., description="Unique camera identifier"),
            rtsp_url: str = Body("", description="RTSP stream URL"),
            ingest_backend: str = Body("opencv", description="Ingest backend: opencv or ffmpeg"),
            enabled_lanes: List[str] = Body(
                ["rt_detr", "yolov8_fallback", "fire_smoke_yolo"],
                description="Detection lanes to enable",
            ),
            sample_hz: float = Body(2.0, description="Frame sample rate"),
            entity_detection_enabled: bool = Body(True, description="Enable identity lane for this camera"),
            identity_runtime_enabled: bool = Body(True, description="Enable identity runtime at tenant scope"),
            tenant_id: Optional[str] = Body(None, description="Tenant ID"),
            community_id: Optional[str] = Body(None, description="Community ID"),
            camera_name: Optional[str] = Body(None, description="Nice camera name"),
            stream_path: Optional[str] = Body(None, description="MediaMTX stream path"),
            policy_version: Optional[int] = Body(None, description="Routing policy version"),
        ):
            """
            Register or Update a runtime camera.
            Handles upserts (Objective 2): restarts processor if config changed.
            """
            cam_cfg = {
                "camera_id": camera_id,
                "rtsp_url": rtsp_url,
                "ingest_backend": ingest_backend,
                "enabled_lanes": enabled_lanes,
                "sample_hz": sample_hz,
                "entity_detection_enabled": entity_detection_enabled,
                "identity_runtime_enabled": identity_runtime_enabled,
                "effective_entity_detection_enabled": bool(entity_detection_enabled and identity_runtime_enabled),
                "source_type": "rtsp" if rtsp_url else "live_camera",
                "tenant_id": tenant_id,
                "community_id": community_id,
                "camera_name": camera_name,
                "stream_path": stream_path,
                "policy_version": policy_version,
            }

            sync_payload = {
                "camera_id": camera_id,
                "enabled": True,
                "rtsp_url": rtsp_url,
                "ingest_backend": ingest_backend,
                "enabled_lanes": enabled_lanes,
                "sample_hz": sample_hz,
                "entity_detection_enabled": entity_detection_enabled,
                "identity_runtime_enabled": identity_runtime_enabled,
                "effective_entity_detection_enabled": bool(entity_detection_enabled and identity_runtime_enabled),
                "tenant_id": tenant_id,
                "community_id": community_id,
                "camera_name": camera_name,
                "stream_path": stream_path,
                "policy_version": policy_version,
            }

            ctx = self._app_context if isinstance(self._app_context, dict) else {}
            camera_configs_by_id = ctx.get("camera_configs_by_id")

            existing_proc = self._find_camera_processor(camera_id)
            config_changed = False
            metadata_only_changed = False
            if existing_proc:
                current_cfg = dict(getattr(existing_proc, "camera_cfg", {}) or {})
                restart_required = any((
                    str(current_cfg.get("rtsp_url", "")) != rtsp_url,
                    str(current_cfg.get("ingest_backend", "opencv")) != ingest_backend,
                    set(current_cfg.get("enabled_lanes", []) or []) != set(enabled_lanes),
                    current_cfg.get("sample_hz") != sample_hz,
                    bool(current_cfg.get("entity_detection_enabled", True)) != bool(entity_detection_enabled),
                    bool(current_cfg.get("identity_runtime_enabled", True)) != bool(identity_runtime_enabled),
                ))
                if restart_required:
                    config_changed = True
                    self.logger.info(
                        "Re-registering camera %s: ingest config changed. Restarting processor.",
                        camera_id,
                    )
                    self._stop_camera_processor(camera_id)
                else:
                    metadata_only_changed = current_cfg != cam_cfg
                    existing_proc.camera_cfg.update(cam_cfg)
                    if isinstance(camera_configs_by_id, dict):
                        camera_configs_by_id[camera_id] = cam_cfg
                    self._sync_runtime_to_backend({
                        **sync_payload,
                        "running": True,
                    })
                    return {
                        "status": "already_registered",
                        "camera_id": camera_id,
                        "message": (
                            f"Camera {camera_id} is already active; metadata refreshed."
                            if metadata_only_changed
                            else f"Camera {camera_id} is already active with same config"
                        ),
                        "hot_loaded": True,
                    }

            if isinstance(camera_configs_by_id, dict):
                camera_configs_by_id[camera_id] = cam_cfg

            hot_loaded = self._start_camera_processor(cam_cfg)

            self._sync_runtime_to_backend({
                **sync_payload,
                "running": bool(hot_loaded),
            })

            return {
                "status": "updated" if config_changed else "registered",
                "camera_id": camera_id,
                "rtsp_url": rtsp_url,
                "hot_loaded": hot_loaded,
                "message": f"Camera {camera_id} {'updated and restarted' if config_changed else 'registered and started'}.",
            }

        @self.app.get("/api/v1/cameras/{camera_id}/snapshot")
        async def api_camera_snapshot(
            camera_id: str,
            quality: int = Query(80, ge=10, le=100),
            maxw: Optional[int] = Query(None, ge=160, le=3840),
        ):
            """Get latest JPEG snapshot from a camera (for embedding in main-drive UI)."""
            proc_found = camera_id in self._camera_processors
            if not proc_found:
                return JSONResponse(status_code=404, content={"error": f"Camera '{camera_id}' not found"})

            frame, ts = None, None
            source = "frame_store"
            if self._frame_store is not None:
                frame, ts = self._frame_store.get(camera_id)

            if frame is None:
                source = "reader_fallback"
                for proc in self._camera_processors.values():
                    if proc.camera_id == camera_id:
                        frame, ts = proc.reader.get_latest()
                        break

            if frame is None:
                return JSONResponse(status_code=404, content={"error": "No frame available yet"})

            out = frame
            h, w = out.shape[:2]
            if maxw and w > maxw:
                scale = maxw / w
                out = cv2.resize(out, (maxw, int(h * scale)), interpolation=cv2.INTER_AREA)
            _, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return StreamingResponse(
                iter([buf.tobytes()]),
                media_type="image/jpeg",
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Frame-Timestamp": ts or "",
                    "X-Frame-Source": source,
                    "Cache-Control": "no-store",
                },
            )

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
                "redis_transport": {},
            }
            if self._doctor_report:
                dev = self._doctor_report.device_info
                result["device"] = {
                    "torch_device": dev.torch_device,
                    "gpu_name": dev.device_name,
                    "gpu_usable": dev.gpu_usable,
                }
            for proc in self._camera_processors.values():
                result["cameras"].append({
                    "camera_id": proc.camera_id,
                    "active": True,
                    "lanes": list(proc.lanes.keys()),
                })
            if self._aggregator:
                result["diagnostics"] = self._aggregator.get_diagnostics()
            try:
                from ..services.redis_publisher import get_publisher
                result["redis_transport"] = get_publisher().status_snapshot()
            except Exception as exc:
                result["redis_transport"] = {"ready": False, "error": str(exc)}
            return result

    # ------------------------------------------------------------------
    def set_gpu_scheduler(self, scheduler):
        """Attach GPU scheduler for /metrics."""
        self._gpu_scheduler = scheduler

    def set_auto_throttle(self, throttle):
        """Attach auto-throttle for /metrics."""
        self._auto_throttle = throttle

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
    async def broadcast_alert(self, alert: Dict[str, Any]):
        """Broadcast a confirmed alert (incident) to WebSockets, Webhooks, and Redis."""
        message = json.dumps({"event": "alert.created", "data": alert})
        if self.ws_clients:
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

        # Redis Streams dispatch (for backend incident subscriber)
        try:
            from ..services.redis_publisher import get_publisher
            publisher = get_publisher()
            if publisher.is_enabled:
                publisher.publish_alert(alert)
        except Exception as exc:
            self.logger.debug("Redis publish skipped: %s", exc)

    async def broadcast_detection(self, detection: Dict[str, Any]):
        """Broadcast a real-time observation (unconfirmed detection) to UI for overlays."""
        message = json.dumps({"event": "observation.detected", "data": detection})
        if self.ws_clients:
            # We don't dispatch detections to Redis or Webhooks (too high frequency)
            disconnected = []
            for client in self.ws_clients:
                try:
                    await client.send_text(message)
                except Exception:
                    disconnected.append(client)
            for client in disconnected:
                if client in self.ws_clients:
                    self.ws_clients.remove(client)
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
