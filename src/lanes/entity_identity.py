"""
Entity-identity lane — runs AFTER person/animal detections.

ENTITY-AWARE WORKFLOW (runtime):
  1. Detector (RT-DETR / YOLOv8) produces persons/animals + track_id.
  2. This lane (entity_identity) crops face/pet region → computes embedding
     via InsightFace (buffalo_l) for faces, CLIP for pets.
  3. IdentityMatcher compares embedding against enrolled vectors (cosine sim).
  4. IdentityStabilizer converts noisy per-frame matches into stable
     identity per track_id using M-of-L voting + lock + decay.
  5. Aggregator uses entity identity for severity/suppression:
       UNKNOWN_PERSON in restricted zone → HIGH
       KNOWN_OWNER/FAMILY              → LOW or suppress (policy-driven)
       PET (enrolled)                  → suppress pet alerts
  6. Alert JSON includes entity{id, name, category, confidence}
     and payload.identity debug stats (best_sim, margin, quality_ok, locked).

For every tracked person:
  • crop person → face detect on crop (preferred) → embed → match → stabilize
  • fallback: face detect on full frame if no crop faces found
For every animal (cat/dog) bbox:
  • crop → pet embedder (with area ratio check) → match → emit identity

Integrates IdentityStabilizer for anti-flicker M-of-L confirmation.
Respects max_tracks_per_frame to keep realtime.

Observation.label = "identity"
Observation.debug["identities"] = [{entity_id, name, category, confidence, track_id, bbox, ...}]
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from .base import BaseLane
from ..common.log import setup_logger
from ..common.types import Observation
from ..identity.face_embedder import FaceEmbedder
from ..identity.pet_embedder import PetEmbedder
from ..identity.matcher import IdentityMatcher
from ..identity.schema import EntityCategory, IdentityMatch

# COCO labels for animals (cat=15, dog=16 in COCO-80)
_ANIMAL_COCO_LABELS = {"cat", "dog"}
_ANIMAL_COCO_IDS = {15, 16}


class EntityIdentityLane(BaseLane):
    """
    Identity lane — pluggable, runs at sample_hz identity (default 2 Hz).
    """

    on_demand = False  # runs on schedule, not on-demand

    def __init__(self, lane_name: str, camera_id: str,
                 models_cfg: Dict[str, Any], device: str):
        super().__init__(lane_name, camera_id, models_cfg, device)
        self.logger = setup_logger(f"EntityIdentity-{camera_id}")

        # These are wired from outside after construction
        self._face_embedder: Optional[FaceEmbedder] = None
        self._pet_embedder: Optional[PetEmbedder] = None
        self._matcher: Optional[IdentityMatcher] = None
        self._stabilizer = None  # IdentityStabilizer, set externally

        # Person detector reference (shared with person_zone lane)
        self._person_detector = None  # YOLO model

        # Per-track identity cache:  track_id → (IdentityMatch, expire_time)
        self._track_cache: Dict[int, tuple] = {}
        self._track_cache_s = 5.0
        self._max_tracks_per_frame = 6

    # ── Wiring (called from app.py after construction) ────────────────
    def set_face_embedder(self, embedder: FaceEmbedder):
        self._face_embedder = embedder

    def set_pet_embedder(self, embedder: PetEmbedder):
        self._pet_embedder = embedder

    def set_matcher(self, matcher: IdentityMatcher):
        self._matcher = matcher

    def set_stabilizer(self, stabilizer):
        """Set IdentityStabilizer for anti-flicker."""
        self._stabilizer = stabilizer

    def set_person_detector(self, model):
        """Share the YOLO person detector model (avoids loading twice)."""
        self._person_detector = model

    # ── Init ──────────────────────────────────────────────────────────
    def init(self):
        id_cfg = self.models_cfg.get("identity", {})
        runtime_cfg = id_cfg.get("runtime", {})
        self._track_cache_s = runtime_cfg.get("track_cache_s", 5.0)
        self._max_tracks_per_frame = runtime_cfg.get("max_tracks_per_frame", 6)
        self._initialized = True
        self.logger.info(
            f"Entity identity lane ready (cache_s={self._track_cache_s}, "
            f"max_tracks={self._max_tracks_per_frame}, "
            f"face={'yes' if self._face_embedder and self._face_embedder.available else 'no'}, "
            f"pet={'yes' if self._pet_embedder and self._pet_embedder.available else 'no'}, "
            f"stabilizer={'yes' if self._stabilizer else 'no'})"
        )

    # ── Infer ─────────────────────────────────────────────────────────
    def infer(self, frame_bgr: np.ndarray, ts_utc: str) -> Observation:
        """
        Run identity inference on current frame.
        Returns Observation with debug["identities"] list.
        """
        identities: List[Dict[str, Any]] = []
        now = time.monotonic()
        frame_h, frame_w = frame_bgr.shape[:2]
        frame_area = frame_h * frame_w

        # ── 1. Detect persons + animals in a SINGLE YOLO forward pass ──
        person_boxes, animal_boxes = self._detect_persons_and_animals(frame_bgr)

        # Enforce max_tracks_per_frame limit
        if len(person_boxes) > self._max_tracks_per_frame:
            # Keep highest-confidence persons
            person_boxes.sort(key=lambda x: x[1], reverse=True)
            person_boxes = person_boxes[:self._max_tracks_per_frame]

        for pbox, pconf, track_id in person_boxes:
            cached = self._get_cached(track_id, now)
            if cached is not None:
                identities.append(self._match_to_dict(cached, track_id, pbox))
                continue

            # Prefer face detection on person crop (§7: no full-frame face detect)
            best_face = None
            quality_ok = True

            if self._face_embedder and self._face_embedder.available:
                crop = self._safe_crop(frame_bgr, pbox)
                if crop is not None and crop.size > 0:
                    offset_x, offset_y = max(0, pbox[0]), max(0, pbox[1])
                    crop_faces = self._face_embedder.detect_faces_on_crop(crop, offset_x, offset_y)
                    if crop_faces:
                        best_face = crop_faces[0]
                        quality_ok = best_face.quality_ok

            if best_face is not None and self._matcher:
                match = self._matcher.match_face(best_face.embedding)
                match.quality_ok = quality_ok
            else:
                match = IdentityMatch(
                    entity_id=None, name=None,
                    category=EntityCategory.UNKNOWN_PERSON,
                    confidence=0.0, score=0.0,
                    quality_ok=quality_ok,
                )

            # Feed to stabilizer if available
            if self._stabilizer is not None:
                stab_result = self._stabilizer.update(
                    camera_id=self.camera_id,
                    track_id=track_id,
                    entity_id=match.entity_id,
                    best_sim=match.best_sim,
                    second_sim=match.second_sim,
                    margin=match.margin,
                    quality_ok=match.quality_ok,
                    category_hint=match.category,
                    entity_name=match.name,
                )
                # Override match with stabilized output
                match = IdentityMatch(
                    entity_id=stab_result["entity_id"],
                    name=stab_result.get("name"),
                    category=stab_result["category"],
                    confidence=stab_result["confidence"],
                    score=match.score,
                    best_sim=stab_result["best_sim"],
                    second_sim=match.second_sim,
                    margin=stab_result["margin"],
                    quality_ok=match.quality_ok,
                )

            self._set_cache(track_id, match, now)
            d = self._match_to_dict(match, track_id, pbox)
            if self._stabilizer is not None:
                d["locked"] = stab_result.get("locked", False)
            identities.append(d)

        # ── 2. Detect animals (already found in single pass above) ────
        for abox, aconf, alabel in animal_boxes:
            crop = self._safe_crop(frame_bgr, abox)
            if crop is None or crop.size == 0:
                continue

            if self._pet_embedder and self._pet_embedder.available and self._matcher:
                emb = self._pet_embedder.embed(crop, frame_area=frame_area)
                if emb is not None:
                    match = self._matcher.match_pet(emb)
                else:
                    match = IdentityMatch(
                        entity_id=None, name=None,
                        category=EntityCategory.UNKNOWN_ANIMAL,
                        confidence=0.0, score=0.0,
                    )
            else:
                # No pet embedder → mark as UNKNOWN_ANIMAL
                match = IdentityMatch(
                    entity_id=None, name=None,
                    category=EntityCategory.UNKNOWN_ANIMAL,
                    confidence=0.0, score=0.0,
                )
            identities.append(self._match_to_dict(match, None, abox))

        # ── Build observation ─────────────────────────────────────────
        has_known = any(
            i["category"] in (EntityCategory.KNOWN_PERSON, EntityCategory.PET)
            for i in identities
        )
        best_conf = max((i["confidence"] for i in identities), default=0.0)

        return Observation(
            ts_utc=ts_utc,
            camera_id=self.camera_id,
            lane=self.lane_name,
            score=best_conf,
            trigger=len(identities) > 0,
            label="identity",
            debug={
                "identities": identities,
                "num_persons": len(person_boxes),
                "num_animals": len(animal_boxes),
                "has_known": has_known,
            },
        )

    # ── Person + Animal detection (SINGLE YOLO pass) ────────────────
    def _detect_persons_and_animals(self, frame_bgr: np.ndarray):
        """
        Single YOLO forward pass for person (0) + cat (15) + dog (16).

        Returns:
            persons: [(bbox, conf, track_id), ...]
            animals: [(bbox, conf, label), ...]
        """
        persons = []
        animals = []
        if self._person_detector is None:
            return persons, animals
        try:
            # Combined class list → one forward pass instead of two
            results = self._person_detector(
                frame_bgr, verbose=False, conf=0.25,
                classes=[0, 15, 16],
            )
            if len(results) > 0 and results[0].boxes is not None:
                boxes = results[0].boxes
                names = results[0].names
                # Batch GPU→CPU transfer (single memcpy)
                all_xyxy = boxes.xyxy.cpu().numpy().astype(int)
                all_conf = boxes.conf.cpu().numpy()
                all_cls = boxes.cls.cpu().numpy().astype(int)
                all_ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None
                for i in range(len(boxes)):
                    box = all_xyxy[i].tolist()
                    conf = float(all_conf[i])
                    cls_id = int(all_cls[i])
                    if cls_id == 0:
                        track_id = int(all_ids[i]) if all_ids is not None else i
                        persons.append((box, conf, track_id))
                    elif cls_id in _ANIMAL_COCO_IDS:
                        label = names.get(cls_id, f"class_{cls_id}").lower()
                        animals.append((box, conf, label))
        except Exception as e:
            self.logger.error(f"Person+animal detect error: {e}")
        return persons, animals

    # Kept for backward compat (external callers)
    def _detect_persons(self, frame_bgr: np.ndarray) -> List[tuple]:
        """Return [(bbox, conf, track_id), ...] for person class."""
        persons, _ = self._detect_persons_and_animals(frame_bgr)
        return persons

    def _detect_animals(self, frame_bgr: np.ndarray) -> List[tuple]:
        """Return [(bbox, conf, label), ...] for cat/dog."""
        _, animals = self._detect_persons_and_animals(frame_bgr)
        return animals

    # ── Cache ─────────────────────────────────────────────────────────
    def _get_cached(self, track_id: int, now: float) -> Optional[IdentityMatch]:
        if track_id in self._track_cache:
            match, expire = self._track_cache[track_id]
            if now < expire:
                return match
            del self._track_cache[track_id]
        return None

    def _set_cache(self, track_id: int, match: IdentityMatch, now: float):
        self._track_cache[track_id] = (match, now + self._track_cache_s)
        # Evict expired entries periodically
        if len(self._track_cache) > 100:
            self._track_cache = {
                k: (m, e) for k, (m, e) in self._track_cache.items() if e > now
            }

    # ── Helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _match_to_dict(match: IdentityMatch, track_id: Optional[int],
                       bbox: List[int]) -> Dict[str, Any]:
        d = match.to_dict()
        d["track_id"] = track_id
        d["bbox"] = bbox
        return d

    @staticmethod
    def _safe_crop(frame: np.ndarray, bbox: List[int]) -> Optional[np.ndarray]:
        h, w = frame.shape[:2]
        x1 = max(0, bbox[0])
        y1 = max(0, bbox[1])
        x2 = min(w, bbox[2])
        y2 = min(h, bbox[3])
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2].copy()
