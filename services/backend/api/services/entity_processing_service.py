from __future__ import annotations

import hashlib
import logging
import os

import requests
from django.db import transaction
from django.utils import timezone

from api.models import (
    AuditLog,
    KnownEntity,
    KnownEntityAsset,
    KnownEntityEmbedding,
    KnownEntityProcessingJob,
)
from api.services.outbox_service import OutboxService
from server.runtime_services import get_ai_base_url


logger = logging.getLogger(__name__)

# Target dimension for identity embeddings, must match
# IDENTITY_EMBEDDING_DIM in models.py
_IDENTITY_EMBEDDING_DIM = 512


class EntityProcessingService:
    """
    Backend-owned enrollment orchestration.

    The worker calls the AI service's stateless embedding generation endpoint
    to compute vectors, then persists them directly to Postgres. No sync-back
    loop, no polling — the backend owns the full lifecycle.
    """

    def __init__(self, outbox_service: OutboxService | None = None):
        self.outbox_service = outbox_service or OutboxService()

    @transaction.atomic
    def enqueue_job(
        self,
        *,
        entity: KnownEntity,
        requested_by=None,
        metadata: dict | None = None,
    ) -> tuple[KnownEntityProcessingJob, bool]:
        active = (
            KnownEntityProcessingJob.objects.select_for_update()
            .filter(
                entity=entity,
                status__in=[
                    KnownEntityProcessingJob.Status.QUEUED,
                    KnownEntityProcessingJob.Status.PROCESSING,
                ],
            )
            .order_by("-created_at")
            .first()
        )
        if active is not None:
            return active, False

        job = KnownEntityProcessingJob.objects.create(
            tenant=entity.tenant,
            entity=entity,
            requested_by=requested_by,
            status=KnownEntityProcessingJob.Status.QUEUED,
            metadata=metadata or {},
        )

        AuditLog.objects.create(
            tenant=entity.tenant,
            actor=requested_by,
            action="entity.enqueue_processing",
            target_type="entity",
            target_id=str(entity.id),
            meta={
                "entity_id": entity.id,
                "job_id": job.id,
                "status": job.status,
                "metadata": job.metadata,
            },
        )
        self.outbox_service.emit(
            aggregate_type="known_entity",
            aggregate_id=entity.id,
            event_type="identity.processing_enqueued",
            payload={
                "tenant_id": entity.tenant_id,
                "known_entity_id": entity.id,
                "job_id": job.id,
                "status": job.status,
            },
        )

        return job, True

    def process_queued_jobs(self, *, limit: int = 10, entity_id: int | None = None) -> dict:
        summary = {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
        }

        for _ in range(max(0, int(limit))):
            job = self._claim_next_job(entity_id=entity_id)
            if job is None:
                break

            summary["processed"] += 1
            if self._process_claimed_job(job.id):
                summary["succeeded"] += 1
            else:
                summary["failed"] += 1

        return summary

    @transaction.atomic
    def _claim_next_job(self, *, entity_id: int | None = None) -> KnownEntityProcessingJob | None:
        query = (
            KnownEntityProcessingJob.objects.select_for_update(skip_locked=True)
            .filter(status=KnownEntityProcessingJob.Status.QUEUED)
            .order_by("created_at")
        )
        if entity_id is not None:
            query = query.filter(entity_id=entity_id)

        job = query.first()
        if job is None:
            return None

        now = timezone.now()
        job.status = KnownEntityProcessingJob.Status.PROCESSING
        job.started_at = now
        job.error = ""
        job.attempts = int(job.attempts) + 1
        job.save(update_fields=["status", "started_at", "error", "attempts", "updated_at"])

        entity = job.entity
        entity.status = KnownEntity.Status.PROCESSING
        entity.processing_started_at = now
        entity.processing_completed_at = None
        entity.processing_error = ""
        entity.save(
            update_fields=[
                "status",
                "processing_started_at",
                "processing_completed_at",
                "processing_error",
                "updated_at",
            ]
        )
        return job

    def _process_claimed_job(self, job_id: int) -> bool:
        job = KnownEntityProcessingJob.objects.select_related("entity", "tenant", "requested_by").get(pk=job_id)
        entity = job.entity
        assets = list(
            KnownEntityAsset.objects.filter(
                entity=entity,
                is_active=True,
                asset_type=KnownEntityAsset.AssetType.ENROLLMENT_IMAGE,
            ).order_by("created_at")
        )

        if not assets:
            self._mark_failed(job=job, entity=entity, reason="No active enrollment assets found for processing.")
            return False

        # Guard: If entity was soft-deleted during the claiming/start window, abort.
        if entity.status == KnownEntity.Status.DELETED:
            logger.info("Aborting processing for entity %s: status is DELETED", entity.id)
            job.status = KnownEntityProcessingJob.Status.FAILED
            job.error = "Entity deleted during processing."
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at", "updated_at"])
            return False

        try:
            # ── Step 1: Determine modality ──────────────────────────────
            modality = "pet_clip" if entity.category == KnownEntity.Category.PET else "face"

            # ── Step 2: Call stateless AI embedding endpoint ────────────
            ai_result = self._generate_embeddings(modality=modality, assets=assets)
            embeddings_data = ai_result.get("embeddings", [])
            failed_images = ai_result.get("failed", [])
            outlier_rejected = ai_result.get("outlier_rejected", 0)

            if not embeddings_data:
                fail_reasons = "; ".join(f"{f['filename']}: {f['reason']}" for f in failed_images)
                raise RuntimeError(
                    f"AI embedding generation returned no vectors. "
                    f"Failed images: {fail_reasons or 'none provided'}"
                )

            # ── Step 3: Persist embeddings directly to Postgres ─────────
            # Deactivate any previous embeddings for this entity (re-enrollment)
            with transaction.atomic():
                KnownEntityEmbedding.objects.filter(
                    entity=entity, is_active=True,
                ).update(is_active=False, deleted_at=timezone.now())

                # Build asset lookup by filename for provenance
                asset_lookup = {}
                for asset in assets:
                    fname = os.path.basename(asset.file.name) if asset.file else ""
                    asset_lookup[fname] = asset

                stored_count = 0
                for emb_data in embeddings_data:
                    vector = emb_data.get("vector", [])
                    if not vector:
                        continue

                    # Normalize to target dimension
                    vector, source_dim = self._normalize_vector(vector)

                    filename = emb_data.get("filename", "")
                    source_asset = asset_lookup.get(filename)

                    KnownEntityEmbedding.objects.create(
                        tenant=entity.tenant,
                        entity=entity,
                        modality=modality,
                        vector=vector,
                        source_dim=source_dim,
                        is_active=True,
                        quality_score=emb_data.get("quality_score"),
                        embedding_model=emb_data.get("embedding_model", ""),
                        embedding_version=1,
                        source_image_uri=(source_asset.storage_uri if source_asset else ""),
                        source_checksum=(source_asset.checksum if source_asset else ""),
                        generated_by="backend_processing",
                        metadata={
                            "source_filename": filename,
                            "job_id": job.id,
                        },
                    )
                    stored_count += 1

            if stored_count < 1:
                raise RuntimeError(
                    f"AI returned {len(embeddings_data)} embeddings but none could be stored."
                )

            # ── Step 4: Mark entity as READY ────────────────────────────
            now = timezone.now()
            
            # Race Condition Guard: Do not overwrite DELETED status
            if entity.status == KnownEntity.Status.DELETED:
                logger.info("Job success for entity %s, but entity is DELETED. Skipping status update.", entity.id)
            else:
                entity.status = KnownEntity.Status.READY
                entity.processing_error = ""
                entity.processing_completed_at = now
                if entity.ready_at is None:
                    entity.ready_at = now
                entity.save(
                    update_fields=[
                        "status",
                        "processing_error",
                        "processing_completed_at",
                        "ready_at",
                        "updated_at",
                    ]
                )

            job.status = KnownEntityProcessingJob.Status.COMPLETED
            job.finished_at = now
            job.error = ""
            job.save(update_fields=["status", "finished_at", "error", "updated_at"])

            AuditLog.objects.create(
                tenant=entity.tenant,
                actor=job.requested_by,
                action="entity.processing_succeeded",
                target_type="entity",
                target_id=str(entity.id),
                meta={
                    "entity_id": entity.id,
                    "job_id": job.id,
                    "embeddings_stored": stored_count,
                    "outlier_rejected": outlier_rejected,
                    "failed_images": len(failed_images),
                    "status": entity.status,
                    "generated_by": "backend_processing",
                },
            )
            self.outbox_service.emit(
                aggregate_type="known_entity",
                aggregate_id=entity.id,
                event_type="identity.processing_succeeded",
                payload={
                    "tenant_id": entity.tenant_id,
                    "known_entity_id": entity.id,
                    "job_id": job.id,
                    "embeddings_stored": stored_count,
                },
            )
            logger.info(
                "Entity processing completed: entity=%s stored=%d outlier_rejected=%d failed=%d",
                entity.id, stored_count, outlier_rejected, len(failed_images),
            )
            return True
        except Exception as exc:
            logger.warning("Entity processing failed for entity=%s job=%s: %s", entity.id, job.id, exc)
            self._mark_failed(job=job, entity=entity, reason=str(exc))
            return False

    def _generate_embeddings(self, *, modality: str, assets: list[KnownEntityAsset]) -> dict:
        """Call the AI stateless embedding generation endpoint with retry strategy.

        POST /api/v1/embeddings/generate
        - modality: "face" or "pet_clip"
        - files: multipart enrollment images
        """
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        ai_base = get_ai_base_url().rstrip("/")
        url = f"{ai_base}/api/v1/embeddings/generate"
        
        timeout = float(os.getenv("ENTITY_EMBED_GENERATE_TIMEOUT_S", "60"))

        # Configure retry strategy (5 retries, ~15s total backoff)
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        files_payload = []
        for asset in assets:
            try:
                with asset.file.open("rb") as handle:
                    content = handle.read()
            except Exception as exc:
                logger.warning("Cannot read asset %s: %s", asset.id, exc)
                continue
            filename = os.path.basename(asset.file.name) if asset.file else f"asset-{asset.id}.jpg"
            files_payload.append(
                ("files", (filename, content, asset.content_type or "application/octet-stream"))
            )

        if not files_payload:
            raise RuntimeError("No readable enrollment assets to send for embedding generation.")

        logger.info("Calling AI embedding generation: %s (modality=%s)", url, modality)
        try:
            response = session.post(
                url,
                data={"modality": modality},
                files=files_payload,
                timeout=timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"Failed to connect to AI service at {url}. "
                "Ensure the AI service is running and accessible. Error: {e}"
            )

        if response.status_code == 503:
            raise RuntimeError(
                f"AI embedding service unavailable at {url} (modality={modality}). "
                "The identity subsystem may not be enabled on the AI service."
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"AI embedding generation failed at {url}: {response.status_code}: {response.text[:300]}"
            )

        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        return payload

    @staticmethod
    def _normalize_vector(vector_list: list) -> tuple[list, int]:
        """Normalize a vector to the target embedding dimension.

        If the source has more dimensions than the target, truncate.
        If fewer, zero-pad. Returns (normalized_vector, source_dim).
        """
        import numpy as np

        arr = np.array(vector_list, dtype=np.float32)
        source_dim = len(arr)

        if source_dim > _IDENTITY_EMBEDDING_DIM:
            arr = arr[:_IDENTITY_EMBEDDING_DIM]
        elif source_dim < _IDENTITY_EMBEDDING_DIM:
            arr = np.pad(arr, (0, _IDENTITY_EMBEDDING_DIM - source_dim))

        # L2-normalize
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm

        return arr.tolist(), source_dim

    @transaction.atomic
    def _mark_failed(self, *, job: KnownEntityProcessingJob, entity: KnownEntity, reason: str) -> None:
        now = timezone.now()
        message = (reason or "Processing failed").strip()
        
        # Race Condition Guard: Do not overwrite DELETED status
        if entity.status == KnownEntity.Status.DELETED:
            logger.info("Job failed for entity %s, but entity is DELETED. Skipping status update.", entity.id)
        else:
            entity.status = KnownEntity.Status.FAILED
            entity.processing_error = message[:5000]
            entity.processing_completed_at = now
            entity.save(
                update_fields=[
                    "status",
                    "processing_error",
                    "processing_completed_at",
                    "updated_at",
                ]
            )

        if int(job.attempts) >= int(job.max_attempts):
            job.status = KnownEntityProcessingJob.Status.FAILED
        else:
            job.status = KnownEntityProcessingJob.Status.FAILED
        job.finished_at = now
        job.error = message[:5000]
        job.save(update_fields=["status", "finished_at", "error", "updated_at"])

        AuditLog.objects.create(
            tenant=entity.tenant,
            actor=job.requested_by,
            action="entity.processing_failed",
            target_type="entity",
            target_id=str(entity.id),
            meta={
                "entity_id": entity.id,
                "job_id": job.id,
                "status": entity.status,
                "error": message[:500],
            },
        )
        self.outbox_service.emit(
            aggregate_type="known_entity",
            aggregate_id=entity.id,
            event_type="identity.processing_failed",
            payload={
                "tenant_id": entity.tenant_id,
                "known_entity_id": entity.id,
                "job_id": job.id,
                "error": message[:500],
            },
        )
