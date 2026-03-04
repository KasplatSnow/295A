"""
Incident registry — central catalogue of all incident definitions.

Pre-registers all standard incident types.  The aggregator queries
the registry to determine pipeline behaviour for each alert type.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from .base import (
    IncidentDefinition,
    ConfirmPolicy,
    SeverityPolicy,
    SuppressionPolicy,
)
from .state import IncidentStateMachine


def _build_defaults() -> Dict[str, IncidentDefinition]:
    """Build the default set of incident definitions."""
    defs: Dict[str, IncidentDefinition] = {}

    # ── INTRUSION ─────────────────────────────────────────────────────
    defs["INTRUSION_PERSON_IN_ZONE"] = IncidentDefinition(
        incident_type="INTRUSION_PERSON_IN_ZONE",
        display_name="Intrusion",
        candidate_sources=["person_zone"],
        persistence_k=3, persistence_n=5,
        confirm=ConfirmPolicy(
            require_temporal_verifier=False,
            fallback_if_no_verifier="emit",
        ),
        severity=SeverityPolicy(
            base="HIGH",
            upgrade_if_unknown_person="SEVERE",
            downgrade_if_known_person="LOW",
        ),
        suppression=SuppressionPolicy(
            require_zone=True,
        ),
        cooldown_s=30,
    )

    # ── LOITERING ─────────────────────────────────────────────────────
    defs["LOITERING"] = IncidentDefinition(
        incident_type="LOITERING",
        display_name="Loitering",
        candidate_sources=["person_zone"],
        persistence_k=3, persistence_n=5,
        confirm=ConfirmPolicy(
            require_temporal_verifier=False,
            fallback_if_no_verifier="emit",
        ),
        severity=SeverityPolicy(
            base="MED",
            upgrade_if_unknown_person="HIGH",
        ),
        suppression=SuppressionPolicy(
            require_zone=False,  # loitering can happen anywhere
            suppress_if_known_person=False,
        ),
        cooldown_s=60,
    )

    # ── FALL ──────────────────────────────────────────────────────────
    defs["FALL"] = IncidentDefinition(
        incident_type="FALL",
        display_name="Fall",
        candidate_sources=["fall_candidate"],
        persistence_k=4, persistence_n=8,
        confirm=ConfirmPolicy(
            require_temporal_verifier=True,
            temporal_target_label="fall",
            fallback_if_no_verifier="emit_if_strong",
            strong_persistence_flags=["lying_persist", "post_fall_still"],
        ),
        severity=SeverityPolicy(
            base="MED",
            upgrade_on_temporal_confirm="SEVERE",
        ),
        suppression=SuppressionPolicy(
            min_pose_conf=0.35,
        ),
        cooldown_s=30,
    )

    # ── VIOLENCE ──────────────────────────────────────────────────────
    defs["VIOLENCE_FIGHT"] = IncidentDefinition(
        incident_type="VIOLENCE_FIGHT",
        display_name="Violence / Fight",
        candidate_sources=["violence_candidate"],
        persistence_k=3, persistence_n=5,
        confirm=ConfirmPolicy(
            require_temporal_verifier=True,
            temporal_target_label="violence",
            fallback_if_no_verifier="emit_if_strong",
            strong_persistence_flags=["multi_person_proximity", "high_local_motion"],
            persistence_gate_k=4, persistence_gate_n=5,
        ),
        severity=SeverityPolicy(
            base="MED",
            upgrade_on_temporal_confirm="SEVERE",
        ),
        suppression=SuppressionPolicy(),
        cooldown_s=30,
    )

    # ── FIRE / SMOKE ──────────────────────────────────────────────────
    defs["FIRE_SMOKE"] = IncidentDefinition(
        incident_type="FIRE_SMOKE",
        display_name="Fire / Smoke",
        candidate_sources=["fire_smoke_yolo", "fire_smoke"],
        persistence_k=4, persistence_n=8,
        confirm=ConfirmPolicy(
            require_temporal_verifier=False,      # uses two-stage instead
            require_secondary_signal=True,
            secondary_signal_lanes=["anomalyclip", "anyanomaly"],
            secondary_threshold=0.55,
            fallback_if_no_verifier="emit_if_strong",
            persistence_gate_k=6, persistence_gate_n=8,
        ),
        severity=SeverityPolicy(
            base="SEVERE",
        ),
        suppression=SuppressionPolicy(),
        cooldown_s=30,
    )

    # ── WEAPON ────────────────────────────────────────────────────────
    defs["WEAPON_DETECTED"] = IncidentDefinition(
        incident_type="WEAPON_DETECTED",
        display_name="Weapon Detected",
        candidate_sources=["weapon_yolo"],
        persistence_k=3, persistence_n=5,
        confirm=ConfirmPolicy(
            require_temporal_verifier=False,
            fallback_if_no_verifier="emit",
        ),
        severity=SeverityPolicy(
            base="HIGH",
            upgrade_if_unknown_person="SEVERE",
            upgrade_in_restricted_zone="SEVERE",
        ),
        suppression=SuppressionPolicy(
            require_proximity_to_person=True,
            proximity_threshold_px=200,
        ),
        cooldown_s=30,
    )

    # ── ACCIDENT ──────────────────────────────────────────────────────
    defs["ACCIDENT"] = IncidentDefinition(
        incident_type="ACCIDENT",
        display_name="Traffic Accident",
        enabled=False,  # only enabled for traffic cameras
        candidate_sources=["accident"],
        persistence_k=3, persistence_n=5,
        confirm=ConfirmPolicy(
            require_temporal_verifier=True,
            temporal_target_label="crash",
            fallback_if_no_verifier="suppress",
        ),
        severity=SeverityPolicy(
            base="SEVERE",
        ),
        suppression=SuppressionPolicy(),
        cooldown_s=60,
    )

    # ── UNKNOWN ANOMALY ───────────────────────────────────────────────
    defs["UNKNOWN_SEVERE_ANOMALY"] = IncidentDefinition(
        incident_type="UNKNOWN_SEVERE_ANOMALY",
        display_name="Unknown Anomaly",
        candidate_sources=["anyanomaly", "anomalyclip", "vad_generic"],
        persistence_k=4, persistence_n=8,
        confirm=ConfirmPolicy(
            require_temporal_verifier=False,
            fallback_if_no_verifier="emit",
        ),
        severity=SeverityPolicy(
            base="MED",
        ),
        suppression=SuppressionPolicy(
            suppress_if_known_person=True,
            suppress_if_known_pet=True,
            suppress_if_periodic_motion=True,
            suppress_if_global_illumination_change=True,
            suppress_outside_sensitive_zones=True,
            max_alerts_per_interval=2,
            interval_s=60.0,
        ),
        cooldown_s=30,
    )

    return defs


class IncidentRegistry:
    """
    Central registry of incident types + per-incident state machines.

    Usage:
        registry = IncidentRegistry()
        defn = registry.get("FALL")
        sm = registry.state_machine("FALL")
    """

    def __init__(self):
        self._definitions: Dict[str, IncidentDefinition] = _build_defaults()
        self._state_machines: Dict[str, IncidentStateMachine] = {}

        # Create state machines for each definition
        for itype, defn in self._definitions.items():
            self._state_machines[itype] = IncidentStateMachine(
                itype, cooldown_s=defn.cooldown_s,
            )

    # ------------------------------------------------------------------
    def get(self, incident_type: str) -> Optional[IncidentDefinition]:
        return self._definitions.get(incident_type)

    def state_machine(self, incident_type: str) -> Optional[IncidentStateMachine]:
        return self._state_machines.get(incident_type)

    def all_definitions(self) -> Dict[str, IncidentDefinition]:
        return dict(self._definitions)

    def enabled_types(self) -> List[str]:
        return [k for k, v in self._definitions.items() if v.enabled]

    # ------------------------------------------------------------------
    def register(self, defn: IncidentDefinition):
        """Register or override an incident definition."""
        self._definitions[defn.incident_type] = defn
        if defn.incident_type not in self._state_machines:
            self._state_machines[defn.incident_type] = IncidentStateMachine(
                defn.incident_type, cooldown_s=defn.cooldown_s,
            )

    def set_enabled(self, incident_type: str, enabled: bool):
        defn = self._definitions.get(incident_type)
        if defn:
            defn.enabled = enabled

    # ------------------------------------------------------------------
    def get_diagnostics(self) -> Dict[str, dict]:
        """Summarise all incidents for /system/diagnostics."""
        result = {}
        for itype, defn in self._definitions.items():
            sm = self._state_machines.get(itype)
            result[itype] = {
                **defn.to_dict(),
                "states": sm.get_all_states() if sm else {},
            }
        return result

    # ------------------------------------------------------------------
    def evict_stale(self):
        """Garbage-collect old state entries."""
        for sm in self._state_machines.values():
            sm.evict_stale()
