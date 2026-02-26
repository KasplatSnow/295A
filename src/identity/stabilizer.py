"""
Per-track identity stabilizer — anti-flicker state machine.

Maintains identity state per (camera_id, track_id) using:
  • M-of-L confirmation:  a known identity is accepted only when the same
    entity_id appears >= M times in the last L observations, each passing
    threshold + margin, with at least one quality_ok sample.
  • Lock:  once accepted, identity is locked for ``lock_s`` seconds.
    Override requires a much stronger match.
  • Grace + decay:  if no usable signal arrives, confidence decays
    linearly; the track flips to UNKNOWN only after ``unknown_grace_s``
    seconds with confidence < 0.25.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ..common.log import setup_logger

logger = setup_logger("IdentityStabilizer")


@dataclass
class _MatchSample:
    """One observation fed to the stabilizer."""
    entity_id: Optional[str]
    best_sim: float
    margin: float
    quality_ok: bool
    ts: float  # monotonic clock


@dataclass
class _TrackState:
    """Per-track identity state."""
    current_entity_id: Optional[str] = None
    current_entity_name: Optional[str] = None
    current_category: str = "UNKNOWN_PERSON"
    confidence: float = 0.0
    last_update_ts: float = 0.0
    locked_until_ts: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=7))


class IdentityStabilizer:
    """
    Anti-flicker identity stabilizer.

    Parameters (from ``identity.runtime`` config):
      history_L          – sliding window length (default 7)
      accept_M           – minimum hits to accept (default 3)
      lock_s             – lock period in seconds (default 8)
      unknown_grace_s    – grace before flipping to unknown (default 6)
      decay_per_s        – confidence decay rate per second (default 0.06)
      reacquire_min_sim  – minimum similarity for re-acquisition (default 0.46)
      match_threshold_sim – per-modality match threshold
      top2_margin        – per-modality margin threshold
    """

    def __init__(self, cfg: Dict[str, Any], entity_store=None):
        self._history_L = cfg.get("history_L", 7)
        self._accept_M = cfg.get("accept_M", 3)
        self._lock_s = cfg.get("lock_s", 8.0)
        self._unknown_grace_s = cfg.get("unknown_grace_s", 6.0)
        self._decay_per_s = cfg.get("decay_per_s", 0.06)
        self._reacquire_min_sim = cfg.get("reacquire_min_sim", 0.46)
        self._match_threshold_sim = cfg.get("match_threshold_sim", 0.50)
        self._top2_margin = cfg.get("top2_margin", 0.08)

        # Override thresholds for lock-break
        self._override_sim_bonus = 0.10   # extra sim above threshold
        self._override_margin_bonus = 0.05  # extra margin

        # Entity store for name lookups
        self._entity_store = entity_store

        # State: (camera_id, track_id) → _TrackState
        self._tracks: Dict[Tuple[str, int], _TrackState] = {}

        logger.info(
            f"Stabilizer ready (L={self._history_L}, M={self._accept_M}, "
            f"lock={self._lock_s}s, grace={self._unknown_grace_s}s)"
        )

    # ── Public API ────────────────────────────────────────────────────

    def _resolve_name(self, entity_id: Optional[str], fallback_name: Optional[str] = None) -> Optional[str]:
        """Look up entity name from store, falling back to provided name."""
        if entity_id is None:
            return None
        if fallback_name:
            return fallback_name
        if self._entity_store is not None:
            try:
                rec = self._entity_store.get_entity(entity_id)
                if rec is not None:
                    return rec.get("name")
            except Exception:
                pass
        return None

    def update(
        self,
        camera_id: str,
        track_id: int,
        entity_id: Optional[str],
        best_sim: float,
        second_sim: float,
        margin: float,
        quality_ok: bool,
        category_hint: str = "UNKNOWN_PERSON",
        entity_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Feed one observation and receive the stabilized identity.

        Returns dict with:
          entity_id, name, category, confidence, locked, best_sim, margin
        """
        now = time.monotonic()
        key = (camera_id, track_id)
        state = self._tracks.get(key)
        if state is None:
            state = _TrackState(history=deque(maxlen=self._history_L))
            self._tracks[key] = state

        # Apply decay since last update
        dt = now - state.last_update_ts if state.last_update_ts > 0 else 0.0
        if dt > 0 and state.confidence > 0:
            state.confidence = max(0.0, state.confidence - self._decay_per_s * dt)
        state.last_update_ts = now

        sample = _MatchSample(
            entity_id=entity_id if best_sim >= self._match_threshold_sim and margin >= self._top2_margin else None,
            best_sim=best_sim,
            margin=margin,
            quality_ok=quality_ok,
            ts=now,
        )
        state.history.append(sample)

        is_locked = now < state.locked_until_ts

        # ── Case 1: We got a valid entity_id match ────────────────────
        if sample.entity_id is not None:
            # If currently locked to a different entity, check override
            if is_locked and state.current_entity_id is not None and sample.entity_id != state.current_entity_id:
                override_sim = self._match_threshold_sim + self._override_sim_bonus
                override_margin = self._top2_margin + self._override_margin_bonus
                if best_sim >= override_sim and margin >= override_margin:
                    # Strong override — break lock
                    logger.debug(
                        f"[{camera_id}/{track_id}] Lock override: "
                        f"{state.current_entity_id} → {sample.entity_id} "
                        f"(sim={best_sim:.3f}, margin={margin:.3f})"
                    )
                    state.current_entity_id = sample.entity_id
                    state.current_entity_name = self._resolve_name(sample.entity_id, entity_name)
                    state.confidence = min(best_sim, 1.0)
                    state.locked_until_ts = now + self._lock_s
                    state.current_category = self._known_category(category_hint)
                else:
                    # Ignore contradictory match during lock
                    pass
            elif sample.entity_id == state.current_entity_id:
                # Same entity — boost confidence
                state.confidence = min(best_sim, 1.0)
                if not is_locked:
                    state.locked_until_ts = now + self._lock_s
            else:
                # No lock or first assignment — apply M-of-L confirmation
                if self._check_m_of_l(state, sample.entity_id):
                    state.current_entity_id = sample.entity_id
                    state.current_entity_name = self._resolve_name(sample.entity_id, entity_name)
                    state.confidence = min(best_sim, 1.0)
                    state.locked_until_ts = now + self._lock_s
                    state.current_category = self._known_category(category_hint)
                    logger.debug(
                        f"[{camera_id}/{track_id}] Identity accepted: "
                        f"{sample.entity_id} (M-of-L, sim={best_sim:.3f})"
                    )
                # else: not enough evidence yet, keep current state

        # ── Case 2: No valid match — apply grace + decay ──────────────
        else:
            if state.current_entity_id is not None:
                # Grace period: don't flip to unknown immediately
                if not is_locked and state.confidence < 0.25:
                    # Check if grace has expired
                    last_good_ts = self._last_good_ts(state)
                    if last_good_ts > 0 and (now - last_good_ts) > self._unknown_grace_s:
                        state.current_entity_id = None
                        state.current_entity_name = None
                        state.confidence = 0.0
                        state.current_category = self._unknown_category(category_hint)
                        logger.debug(
                            f"[{camera_id}/{track_id}] Identity expired → UNKNOWN "
                            f"(grace={self._unknown_grace_s}s elapsed)"
                        )

        return {
            "entity_id": state.current_entity_id,
            "name": state.current_entity_name,
            "category": state.current_category,
            "confidence": round(state.confidence, 4),
            "locked": now < state.locked_until_ts,
            "best_sim": round(best_sim, 4),
            "margin": round(margin, 4),
        }

    def get_track_states(self, camera_id: str) -> list:
        """Return all tracked states for a camera (for debug endpoint)."""
        now = time.monotonic()
        results = []
        for (cid, tid), state in self._tracks.items():
            if cid != camera_id:
                continue
            results.append({
                "track_id": tid,
                "entity_id": state.current_entity_id,
                "name": state.current_entity_name,
                "category": state.current_category,
                "confidence": round(state.confidence, 4),
                "locked": now < state.locked_until_ts,
                "locked_until": round(max(0, state.locked_until_ts - now), 1),
                "history_len": len(state.history),
            })
        return results

    def cleanup(self, max_age_s: float = 120.0):
        """Remove stale tracks that haven't been updated recently."""
        now = time.monotonic()
        stale = [
            k for k, s in self._tracks.items()
            if (now - s.last_update_ts) > max_age_s
        ]
        for k in stale:
            del self._tracks[k]

    # ── Internal ──────────────────────────────────────────────────────

    def _check_m_of_l(self, state: _TrackState, entity_id: str) -> bool:
        """
        Check M-of-L confirmation: entity_id appears >= M times in the
        last L samples, each passing threshold+margin, with at least one
        quality_ok sample.
        """
        hits = 0
        any_quality_ok = False
        for sample in state.history:
            if sample.entity_id == entity_id:
                hits += 1
                if sample.quality_ok:
                    any_quality_ok = True
        return hits >= self._accept_M and any_quality_ok

    def _last_good_ts(self, state: _TrackState) -> float:
        """Timestamp of the last sample with a valid entity_id."""
        for sample in reversed(state.history):
            if sample.entity_id is not None:
                return sample.ts
        return state.last_update_ts

    @staticmethod
    def _known_category(hint: str) -> str:
        if "ANIMAL" in hint or hint == "PET":
            return "PET"
        return "KNOWN_PERSON"

    @staticmethod
    def _unknown_category(hint: str) -> str:
        if "ANIMAL" in hint or hint == "PET":
            return "UNKNOWN_ANIMAL"
        return "UNKNOWN_PERSON"
