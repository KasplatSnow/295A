"""
Canonical entity store backed by backend internal identity endpoints.

No local SQLite or local embedding files are used as mutable truth.
The backend (Postgres + pgvector) is authoritative.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import numpy as np

from ..common.log import setup_logger
from ..common.runtime import get_ai_enroll_image_dir, get_backend_config_sync_base
from .schema import EntityRecord


class EntityStore:
    """HTTP-backed identity store with local in-memory cache for fast matching."""

    def __init__(self, enroll_img_dir: Optional[Path] = None):
        self.logger = setup_logger("EntityStore")

        self._backend_sync_base = get_backend_config_sync_base().rstrip("/")
        self._sync_token = os.environ.get("AI_WEBHOOK_TOKEN", "")
        self._sync_secret = os.environ.get("AI_WEBHOOK_SECRET", "")

        self.enroll_img_dir = enroll_img_dir or get_ai_enroll_image_dir(Path(__file__).resolve().parent.parent.parent)
        self.enroll_img_dir.mkdir(parents=True, exist_ok=True)

        self._entities_by_id: Dict[str, Dict] = {}
        self._embeddings_by_modality: Dict[str, List[Tuple[str, np.ndarray]]] = {
            "face": [],
            "pet_clip": [],
        }
        self._identity_version: str = ""

        self.reload_from_backend(force=True)

    # --- transport helpers -------------------------------------------------
    def _auth_headers(self, *, body: Optional[str] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._sync_token:
            headers["X-AI-WEBHOOK-TOKEN"] = self._sync_token
            return headers

        if self._sync_secret:
            payload = (body or "").encode()
            sig = hmac.new(self._sync_secret.encode(), payload, hashlib.sha256).hexdigest()
            headers["X-Vigilzone-Signature"] = f"sha256={sig}"
        return headers

    def _post_sync(self, payload: Dict) -> None:
        if not self._backend_sync_base:
            return

        body = json.dumps(payload)
        headers = {
            "Content-Type": "application/json",
            **self._auth_headers(body=body),
        }
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.post(
                    f"{self._backend_sync_base}/identity/sync/",
                    content=body,
                    headers=headers,
                )
            if not resp.is_success:
                self.logger.debug(
                    "Identity sync failed: %s %s",
                    resp.status_code,
                    resp.text[:240],
                )
        except Exception as exc:
            self.logger.debug("Identity sync failed: %s", exc)

    def _fetch_snapshot(self, tenant_id: Optional[str] = None) -> Optional[Dict]:
        if not self._backend_sync_base:
            return None

        headers = self._auth_headers()
        url = f"{self._backend_sync_base}/identity/snapshot/"
        if tenant_id:
            url += f"?tenant_id={tenant_id}"
            
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url, headers=headers)
            if not resp.is_success:
                self.logger.debug(
                    "Identity snapshot fetch failed: %s %s",
                    resp.status_code,
                    resp.text[:240],
                )
                return None

            payload = resp.json()
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            self.logger.debug("Identity snapshot fetch failed: %s", exc)

        return None

    # --- cache management --------------------------------------------------
    def current_identity_version(self, tenant_id: Optional[str] = None) -> str:
        if tenant_id:
            if not hasattr(self, '_tenant_versions'):
                self._tenant_versions = {}
            return self._tenant_versions.get(str(tenant_id), "")
        return self._identity_version

    def reload_from_backend(self, *, force: bool = False, tenant_id: Optional[str] = None) -> bool:
        snapshot = self._fetch_snapshot(tenant_id)
        if snapshot is None:
            if not self._entities_by_id:
                self.logger.warning(f"Identity snapshot unavailable from backend (tenant={tenant_id}); cache is empty")
            else:
                self.logger.warning(f"Identity snapshot unavailable from backend (tenant={tenant_id}); keeping existing cache")
            return False

        next_version = str(snapshot.get("identity_version") or "").strip()
        
        # Check against appropriate version tracker
        if not hasattr(self, '_tenant_versions'):
            self._tenant_versions = {}
            
        current_v = self._tenant_versions.get(str(tenant_id), "") if tenant_id else self._identity_version
        
        if (not force) and next_version and next_version == current_v:
            return False

        entities = snapshot.get("entities") if isinstance(snapshot, dict) else []
        embeddings = snapshot.get("embeddings") if isinstance(snapshot, dict) else []

        next_entities: Dict[str, Dict] = {}
        for row in entities if isinstance(entities, list) else []:
            if not isinstance(row, dict):
                continue
            entity_id = str(row.get("entity_id") or "").strip()
            if not entity_id:
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            next_entities[entity_id] = {
                "entity_id": entity_id,
                "known_entity_id": row.get("known_entity_id"),
                "tenant_id": str(row.get("tenant_id") or ""),
                "name": row.get("name") or entity_id,
                "category": row.get("category") or "KNOWN_PERSON",
                "role": row.get("role") or "VISITOR",
                "metadata": metadata,
                "created_at": row.get("created_at"),
            }

        next_embeddings: Dict[str, List[Tuple[str, np.ndarray]]] = {"face": [], "pet_clip": []}
        for row in embeddings if isinstance(embeddings, list) else []:
            if not isinstance(row, dict):
                continue
            entity_id = str(row.get("entity_id") or "").strip()
            modality = str(row.get("modality") or "").strip().lower()
            raw_vector = row.get("vector")
            if entity_id not in next_entities or modality not in next_embeddings:
                continue
            if not isinstance(raw_vector, list) or len(raw_vector) == 0:
                continue
            try:
                vec = np.asarray(raw_vector, dtype=np.float32).flatten()
            except Exception:
                continue
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            next_embeddings[modality].append((entity_id, vec))

        # Merge logic
        if tenant_id is None:
            # Global replacement
            self._entities_by_id = next_entities
            self._embeddings_by_modality = next_embeddings
            self._identity_version = next_version
        else:
            # Scoped replacement for this tenant only
            target_tenant_str = str(tenant_id)
            
            # Prune obsolete entities for this tenant
            prune_ids = [eid for eid, edata in self._entities_by_id.items() 
                         if str(edata.get("tenant_id", "")) == target_tenant_str]
                         
            for eid in prune_ids:
                if eid not in next_entities:
                    self._entities_by_id.pop(eid, None)
                    
            # Insert/Update fetched entities
            for eid, edata in next_entities.items():
                self._entities_by_id[eid] = edata
                
            # Rebuild embeddings list, keeping other tenants intact
            for mod in ["face", "pet_clip"]:
                current_lst = self._embeddings_by_modality.get(mod, [])
                # Keep items NOT belonging to the pruned set OR the newly fetched set
                filtered_lst = [item for item in current_lst if item[0] not in prune_ids and item[0] not in next_entities]
                # Append the new ones
                filtered_lst.extend(next_embeddings.get(mod, []))
                self._embeddings_by_modality[mod] = filtered_lst
                
            self._tenant_versions[target_tenant_str] = next_version

        fallback_v = f"fallback:{len(self._entities_by_id)}:{len(self._embeddings_by_modality.get('face', []))}"
        self.logger.info(
            "Identity cache loaded from backend (tenant=%s): version=%s entities=%s, face_vecs=%s, pet_vecs=%s",
            tenant_id or "global",
            next_version or fallback_v,
            len(self._entities_by_id),
            len(self._embeddings_by_modality.get("face", [])),
            len(self._embeddings_by_modality.get("pet_clip", [])),
        )
        return True

    def refresh_if_changed(self) -> bool:
        return self.reload_from_backend(force=False)

    # --- public API used by matcher/server -------------------------------
    def add_entity(self, record: EntityRecord, embeddings: Optional[Dict[str, np.ndarray]] = None) -> str:
        self.logger.warning(
            "DEPRECATION: add_entity() is a legacy compat path. "
            "Canonical entity creation should use the backend KnownEntityViewSet. "
            "entity_id=%s",
            record.entity_id,
        )
        self._post_sync(
            {
                "op": "upsert_entity",
                "entity_id": record.entity_id,
                "name": record.name,
                "category": record.category,
                "role": record.role,
                "metadata": record.metadata or {},
                "tenant_id": (record.metadata or {}).get("tenant_id"),
                "known_entity_id": (record.metadata or {}).get("known_entity_id"),
            }
        )
        self.reload_from_backend(force=True)

        if embeddings:
            for modality, vec in embeddings.items():
                self.add_embedding(record.entity_id, modality, vec)

        return record.entity_id

    def add_embedding(self, entity_id: str, modality: str, vec: np.ndarray):
        self.logger.warning(
            "DEPRECATION: add_embedding() is a legacy compat path. "
            "Canonical embedding persistence should use backend-owned processing. "
            "entity_id=%s modality=%s",
            entity_id, modality,
        )
        vector = np.asarray(vec, dtype=np.float32).flatten()
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm

        self._post_sync(
            {
                "op": "add_embedding",
                "entity_id": entity_id,
                "modality": modality,
                "vector": vector.tolist(),
            }
        )
        self.reload_from_backend(force=True)

    def remove_entity(self, entity_id: str) -> bool:
        self.logger.warning(
            "DEPRECATION: remove_entity() is a legacy compat path. "
            "Canonical entity deletion should use the backend KnownEntityViewSet. "
            "entity_id=%s",
            entity_id,
        )
        self._post_sync({"op": "remove_entity", "entity_id": entity_id})

        img_dir = self.enroll_img_dir / entity_id
        if img_dir.exists():
            import shutil

            shutil.rmtree(img_dir, ignore_errors=True)

        self.reload_from_backend(force=True)
        return entity_id not in self._entities_by_id

    def list_entities(self, category: Optional[str] = None) -> List[Dict]:
        rows = list(self._entities_by_id.values())
        if category:
            token = str(category).strip().upper()
            rows = [row for row in rows if str(row.get("category", "")).upper() == token]
        return sorted(rows, key=lambda row: str(row.get("entity_id", "")), reverse=True)

    def get_entity(self, entity_id: str) -> Optional[Dict]:
        return self._entities_by_id.get(str(entity_id).strip())

    def update_entity(
        self,
        entity_id: str,
        *,
        name: Optional[str] = None,
        role: Optional[str] = None,
        category: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[Dict]:
        payload = {
            "op": "update_entity",
            "entity_id": entity_id,
        }
        if name is not None:
            payload["name"] = name
        if role is not None:
            payload["role"] = role
        if category is not None:
            payload["category"] = category
        if metadata is not None:
            payload["metadata"] = metadata

        self._post_sync(payload)
        self.reload_from_backend(force=True)
        return self.get_entity(entity_id)

    def record_sighting(self, entity_id: str, camera_id: str):
        self._post_sync(
            {
                "op": "record_sighting",
                "entity_id": entity_id,
                "camera_id": camera_id,
            }
        )
        cached = self._entities_by_id.get(str(entity_id).strip())
        if cached is not None:
            metadata = dict(cached.get("metadata") or {})
            metadata["last_camera_id"] = str(camera_id)
            cached["metadata"] = metadata

    def get_all_embeddings(self, modality: str) -> Tuple[List[str], Optional[np.ndarray]]:
        rows = self._embeddings_by_modality.get(str(modality).strip().lower(), [])
        if not rows:
            return [], None

        ids = [row[0] for row in rows]
        matrix = np.stack([row[1] for row in rows]).astype(np.float32)
        return ids, matrix

    @staticmethod
    def generate_id() -> str:
        return f"ent_{uuid.uuid4().hex[:12]}"
