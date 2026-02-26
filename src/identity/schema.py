"""
Identity schema — data classes for entity recognition subsystem.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List


# ── Category & Role enums (string-based for JSON compat) ──────────────
class EntityCategory:
    KNOWN_PERSON = "KNOWN_PERSON"
    UNKNOWN_PERSON = "UNKNOWN_PERSON"
    PET = "PET"
    UNKNOWN_ANIMAL = "UNKNOWN_ANIMAL"

    _ALL = {"KNOWN_PERSON", "UNKNOWN_PERSON", "PET", "UNKNOWN_ANIMAL"}

    @classmethod
    def validate(cls, value: str) -> str:
        """Return canonical category. Normalizes common variants."""
        if value in cls._ALL:
            return value
        upper = value.upper().replace(" ", "_")
        if upper in cls._ALL:
            return upper
        # Fallback mapping for safety
        if "PERSON" in upper and "UNKNOWN" not in upper:
            return cls.KNOWN_PERSON
        if "ANIMAL" in upper or "PET" in upper:
            return cls.PET
        return cls.UNKNOWN_PERSON


class EntityRole:
    OWNER = "OWNER"
    FAMILY = "FAMILY"
    FRIEND = "FRIEND"
    NEIGHBOR = "NEIGHBOR"
    VISITOR = "VISITOR"
    PET = "PET"


# ── Persisted entity record ──────────────────────────────────────────
@dataclass
class EntityRecord:
    """One enrolled entity (person or pet)."""
    entity_id: str
    name: str
    category: str                           # KNOWN_PERSON | PET
    role: str                               # OWNER / FAMILY / FRIEND / PET / …
    metadata: Dict[str, Any] = field(default_factory=dict)
    # metadata may contain: allowed_zones, access_level, time_rules, …

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Runtime identity match result ─────────────────────────────────────
@dataclass
class IdentityMatch:
    """Result of matching an embedding against the store."""
    entity_id: Optional[str]                # None if unknown
    name: Optional[str]                     # None if unknown
    category: str                           # KNOWN_PERSON | UNKNOWN_PERSON | PET | UNKNOWN_ANIMAL
    confidence: float                       # 0..1 (thresholded)
    score: float                            # raw cosine similarity (best_sim)
    best_sim: float = 0.0                   # best cosine similarity
    second_sim: float = 0.0                 # second-best similarity
    margin: float = 0.0                     # best_sim - second_sim
    quality_ok: bool = True                 # whether input quality passed gating

    def __post_init__(self):
        # Enforce canonical category
        self.category = EntityCategory.validate(self.category)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
