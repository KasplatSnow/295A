"""
Incident framework — base definitions.

Every incident type (intrusion, loitering, fall, violence, fire/smoke, weapon,
accident, unknown anomaly) is described by an ``IncidentDefinition`` that
specifies:
  • candidate sources (which lanes can produce candidates)
  • confirm policy (temporal verifier, secondary signal, or strong persistence)
  • severity policy (base severity + upgrade/downgrade rules)
  • suppression policy (when to drop the alert)
  • reason_codes attached to every emitted or suppressed alert

All incidents flow through:
  candidate → persistence (K-of-N) → confirm (optional) → emit → cooldown
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ─── Confirm policy ───────────────────────────────────────────────────
@dataclass
class ConfirmPolicy:
    """How an incident is confirmed after K-of-N persistence passes."""

    require_temporal_verifier: bool = False
    temporal_target_label: str = ""

    require_secondary_signal: bool = False
    secondary_signal_lanes: List[str] = field(default_factory=list)
    secondary_threshold: float = 0.50

    # What to do when temporal verifier is unavailable
    # "suppress" → drop alert
    # "emit_if_strong" → emit if extra strong-persistence check passes
    # "emit" → always emit at base severity
    fallback_if_no_verifier: str = "suppress"

    # For "emit_if_strong": require these debug flags to be True
    strong_persistence_flags: List[str] = field(default_factory=list)

    # Extra persistence gate (beyond the main K/N)
    persistence_gate_k: int = 0  # 0 = disabled
    persistence_gate_n: int = 0


# ─── Severity policy ─────────────────────────────────────────────────
@dataclass
class SeverityPolicy:
    """How severity is determined and adjusted."""

    base: str = "MED"

    upgrade_on_temporal_confirm: str = ""   # e.g. "SEVERE"
    upgrade_if_unknown_person: str = ""     # e.g. "HIGH"
    upgrade_in_restricted_zone: str = ""    # e.g. "SEVERE"

    downgrade_if_known_person: str = ""     # e.g. "LOW"


# ─── Suppression policy ──────────────────────────────────────────────
@dataclass
class SuppressionPolicy:
    """Conditions under which the alert is suppressed entirely."""

    min_pose_conf: float = 0.0
    require_zone: bool = False
    require_proximity_to_person: bool = False
    proximity_threshold_px: int = 200

    suppress_if_known_person: bool = False
    suppress_if_known_pet: bool = False
    suppress_if_periodic_motion: bool = False
    suppress_if_global_illumination_change: bool = False
    suppress_outside_sensitive_zones: bool = False

    max_alerts_per_interval: int = 0  # 0 = unlimited
    interval_s: float = 0.0


# ─── Incident definition ─────────────────────────────────────────────
@dataclass
class IncidentDefinition:
    """Full specification for one incident type."""

    incident_type: str          # e.g. "INTRUSION_PERSON_IN_ZONE"
    display_name: str           # e.g. "Intrusion"
    enabled: bool = True

    # Which lanes can produce candidates for this incident
    candidate_sources: List[str] = field(default_factory=list)

    # Persistence (K-of-N voting) parameters
    persistence_k: int = 3
    persistence_n: int = 5

    confirm: ConfirmPolicy = field(default_factory=ConfirmPolicy)
    severity: SeverityPolicy = field(default_factory=SeverityPolicy)
    suppression: SuppressionPolicy = field(default_factory=SuppressionPolicy)

    cooldown_s: int = 30

    def to_dict(self) -> dict:
        """Serialise for diagnostics / API."""
        return {
            "incident_type": self.incident_type,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "candidate_sources": self.candidate_sources,
            "persistence": f"{self.persistence_k}/{self.persistence_n}",
            "confirm": {
                "require_temporal_verifier": self.confirm.require_temporal_verifier,
                "fallback_if_no_verifier": self.confirm.fallback_if_no_verifier,
                "require_secondary_signal": self.confirm.require_secondary_signal,
            },
            "severity_base": self.severity.base,
            "cooldown_s": self.cooldown_s,
        }
