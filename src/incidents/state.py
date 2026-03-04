"""
Incident state machine — per-camera / per-track progression tracking.

States:
  IDLE → CANDIDATE → PERSISTING → CONFIRMED → EMITTED → COOLDOWN → IDLE

Each incident type × camera × track has its own state machine instance.
Reason codes are accumulated at every transition.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from collections import defaultdict


class IncidentState(str, Enum):
    IDLE = "IDLE"
    CANDIDATE = "CANDIDATE"
    PERSISTING = "PERSISTING"
    CONFIRMED = "CONFIRMED"
    EMITTED = "EMITTED"
    COOLDOWN = "COOLDOWN"


@dataclass
class ReasonCode:
    """Single reason code entry."""
    code: str           # machine-readable, e.g. "k_of_n_passed"
    detail: str = ""    # human-readable detail
    ts: float = 0.0     # monotonic time

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


@dataclass
class _TrackState:
    """Per-track incident state."""
    state: IncidentState = IncidentState.IDLE
    candidate_count: int = 0        # consecutive candidate hits
    persist_hits: int = 0           # K-of-N hits
    confirmed: bool = False
    last_emit_ts: float = 0.0
    cooldown_until: float = 0.0
    reason_codes: List[ReasonCode] = field(default_factory=list)
    suppression_reasons: List[ReasonCode] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def add_reason(self, code: str, detail: str = ""):
        self.reason_codes.append(ReasonCode(code=code, detail=detail, ts=time.monotonic()))
        # Keep bounded
        if len(self.reason_codes) > 20:
            self.reason_codes = self.reason_codes[-20:]

    def add_suppression(self, code: str, detail: str = ""):
        self.suppression_reasons.append(ReasonCode(code=code, detail=detail, ts=time.monotonic()))
        if len(self.suppression_reasons) > 20:
            self.suppression_reasons = self.suppression_reasons[-20:]

    def reset(self):
        self.state = IncidentState.IDLE
        self.candidate_count = 0
        self.persist_hits = 0
        self.confirmed = False
        self.reason_codes.clear()
        self.suppression_reasons.clear()
        self.extra.clear()


class IncidentStateMachine:
    """
    Manages per-camera / per-track state for one incident type.

    Usage:
        sm = IncidentStateMachine("FALL", cooldown_s=30)
        sm.on_candidate("cam1", track_id=5, hit=True, debug={...})
        state = sm.get_state("cam1", track_id=5)
    """

    def __init__(self, incident_type: str, cooldown_s: float = 30.0):
        self.incident_type = incident_type
        self.cooldown_s = cooldown_s
        # (camera_id, track_key) → _TrackState
        # track_key = track_id or "_global" for incidents without per-track state
        self._states: Dict[tuple, _TrackState] = defaultdict(_TrackState)

    # ------------------------------------------------------------------
    def _key(self, camera_id: str, track_id: Optional[int] = None) -> tuple:
        return (camera_id, track_id if track_id is not None else "_global")

    # ------------------------------------------------------------------
    def on_candidate(
        self,
        camera_id: str,
        track_id: Optional[int],
        hit: bool,
        reason: str = "",
        debug: Optional[Dict[str, Any]] = None,
    ) -> _TrackState:
        """Record a candidate observation (hit or miss)."""
        key = self._key(camera_id, track_id)
        st = self._states[key]
        now = time.monotonic()

        # Check cooldown
        if st.state == IncidentState.COOLDOWN:
            if now < st.cooldown_until:
                return st
            st.reset()

        if hit:
            st.candidate_count += 1
            st.persist_hits += 1
            if st.state == IncidentState.IDLE:
                st.state = IncidentState.CANDIDATE
                st.add_reason("candidate_detected", reason)
            elif st.state == IncidentState.CANDIDATE:
                st.state = IncidentState.PERSISTING
                st.add_reason("persisting", f"hits={st.persist_hits}")
        else:
            # Decay candidate count on miss
            st.candidate_count = max(0, st.candidate_count - 1)

        if debug:
            st.extra.update(debug)

        return st

    # ------------------------------------------------------------------
    def mark_confirmed(
        self,
        camera_id: str,
        track_id: Optional[int],
        reason: str = "",
    ) -> _TrackState:
        key = self._key(camera_id, track_id)
        st = self._states[key]
        st.confirmed = True
        st.state = IncidentState.CONFIRMED
        st.add_reason("confirmed", reason)
        return st

    # ------------------------------------------------------------------
    def mark_emitted(
        self,
        camera_id: str,
        track_id: Optional[int],
    ) -> _TrackState:
        key = self._key(camera_id, track_id)
        st = self._states[key]
        st.state = IncidentState.EMITTED
        st.last_emit_ts = time.monotonic()
        st.add_reason("emitted")
        return st

    # ------------------------------------------------------------------
    def enter_cooldown(
        self,
        camera_id: str,
        track_id: Optional[int],
    ) -> _TrackState:
        key = self._key(camera_id, track_id)
        st = self._states[key]
        st.state = IncidentState.COOLDOWN
        st.cooldown_until = time.monotonic() + self.cooldown_s
        st.add_reason("cooldown", f"{self.cooldown_s}s")
        return st

    # ------------------------------------------------------------------
    def mark_suppressed(
        self,
        camera_id: str,
        track_id: Optional[int],
        reason: str = "",
    ) -> _TrackState:
        key = self._key(camera_id, track_id)
        st = self._states[key]
        st.add_suppression("suppressed", reason)
        return st

    # ------------------------------------------------------------------
    def get_state(
        self, camera_id: str, track_id: Optional[int] = None,
    ) -> _TrackState:
        return self._states[self._key(camera_id, track_id)]

    # ------------------------------------------------------------------
    def get_all_states(self) -> Dict[str, Any]:
        """Serialise all states for diagnostics."""
        result = {}
        for (cam, trk), st in self._states.items():
            k = f"{cam}/{trk}"
            result[k] = {
                "state": st.state.value,
                "persist_hits": st.persist_hits,
                "confirmed": st.confirmed,
                "reason_codes": [r.to_dict() for r in st.reason_codes[-5:]],
                "suppression_reasons": [r.to_dict() for r in st.suppression_reasons[-5:]],
            }
        return result

    # ------------------------------------------------------------------
    def evict_stale(self, max_age_s: float = 120.0):
        """Remove track states that haven't been updated recently."""
        now = time.monotonic()
        to_remove = []
        for key, st in self._states.items():
            if st.reason_codes:
                last_ts = st.reason_codes[-1].ts
            else:
                last_ts = 0.0
            if now - last_ts > max_age_s and st.state in (IncidentState.IDLE, IncidentState.COOLDOWN):
                to_remove.append(key)
        for key in to_remove:
            del self._states[key]
