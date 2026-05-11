"""
PR-04: Deterministic multimodal fusion layer.

Correlates audio Observations (lane="audio_anomaly") with video Observations
within a configurable time window and produces fused Observations that are then
routed to the existing AlertAggregator.

Design rules (plan Section 7):
  - DETERMINISTIC only — no learnable fusion model, no neural net.
  - Modality independence: video-only cameras still work with zero changes.
  - Audio-only high-risk labels (gunshot, explosion) CAN emit alerts without
    video confirmation (allow_audio_only_high_risk=true in config).
  - Video alerts pass through unchanged when audio is absent (audio degraded/absent).
  - Synergy bonus: when both modalities agree on semantically related events,
    the fused confidence is boosted.
  - Conflict penalty: when modalities contradict, confidence is penalised.
  - Per-type cooldown: independent from the video-only aggregator cooldown.
  - Emitted observation always uses lane="audio_video_fusion" so the aggregator
    routes it to AUDIO_ANOMALY alert type.

Fusion is called per AudioChunk cycle:
    fusion.feed_audio(audio_obs)      # called from audio_loop
    fusion.feed_video(video_obs_list) # called at end of each frame cycle
    fused_obs = fusion.flush()        # drain any ready fused observations

Fused Observation layout (passed to aggregator.process_observation):
    lane      = "audio_video_fusion"
    label     = canonical fused label (e.g. "audio_scream_person_present")
    score     = fused confidence
    trigger   = True (always — fusion only emits when confident)
    debug     = {modality:"fusion", audio_label, video_labels, fusion_reason, ...}
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..common.log import setup_logger
from ..common.types import Observation
from ..common.timeutil import now_iso_utc

# ── Audio labels that can fire WITHOUT video confirmation ──────────────────
_HIGH_RISK_AUDIO_LABELS = {
    "audio_gunshot",
    "audio_explosion",
}

# ── Semantic synergy map ───────────────────────────────────────────────────
# (audio_canonical_label, video_alert_type_or_label) → fused_label
# When BOTH are present in the window, a synergy bonus is applied and the
# fused label describes the combined event.
_SYNERGY_PAIRS: List[Tuple[str, str, str]] = [
    # audio label           video label/type          fused label
    ("audio_scream",        "person",                 "audio_scream_person_present"),
    ("audio_scream",        "INTRUSION_PERSON_IN_ZONE","audio_scream_intrusion"),
    ("audio_gunshot",       "person",                 "audio_gunshot_person_present"),
    ("audio_gunshot",       "WEAPON_DETECTED",        "audio_gunshot_weapon_confirmed"),
    ("audio_explosion",     "FIRE_SMOKE",             "audio_explosion_fire_smoke"),
    ("audio_explosion",     "ACCIDENT",               "audio_explosion_vehicle_crash"),
    ("audio_glass_break",   "INTRUSION_PERSON_IN_ZONE","audio_glass_break_intrusion"),
    ("audio_glass_break",   "person",                 "audio_glass_break_person_present"),
    ("audio_alarm",         "FIRE_SMOKE",             "audio_alarm_fire_confirmed"),
    ("audio_siren",         "ACCIDENT",               "audio_siren_vehicle_incident"),
    ("audio_vehicle_crash", "ACCIDENT",               "audio_vehicle_crash_confirmed"),
    ("audio_shout",         "VIOLENCE_FIGHT",         "audio_shout_violence_confirmed"),
    ("audio_shout",         "person",                 "audio_shout_person_present"),
]

# Build lookup: (audio_label, video_label) → fused_label
_SYNERGY_MAP: Dict[Tuple[str, str], str] = {
    (a, v): f for (a, v, f) in _SYNERGY_PAIRS
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_ts(ts: str) -> float:
    """Return unix timestamp from ISO UTC string."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.timestamp()


class MultimodalFusion:
    """
    Deterministic rule-based multimodal fusion engine.

    Maintains a short rolling window of recent audio and video Observations,
    applies fusion rules, and emits fused Observations ready for the aggregator.
    """

    def __init__(self, camera_id: str, cfg: Dict[str, Any], logger=None):
        """
        Args:
            camera_id: Camera this fusion instance belongs to.
            cfg:       models.video_audio_fusion sub-dict from models.yaml.
        """
        self.camera_id = camera_id
        self.logger = logger or setup_logger(f"MultimodalFusion-{camera_id}")

        # Config
        self._enabled                  = cfg.get("enabled", False)
        self._window_s                 = float(cfg.get("window_s", 10.0))
        self._audio_weight             = float(cfg.get("audio_weight", 0.45))
        self._video_weight             = float(cfg.get("video_weight", 0.55))
        self._synergy_bonus            = float(cfg.get("synergy_bonus_confirmed", 0.12))
        self._conflict_penalty         = float(cfg.get("conflict_penalty", 0.10))
        self._fused_threshold          = float(cfg.get("fused_alert_threshold", 0.72))
        self._high_severity_threshold  = float(cfg.get("high_severity_threshold", 0.85))
        self._allow_audio_only_high    = bool(cfg.get("allow_audio_only_high_risk", True))
        self._allow_video_passthrough  = bool(cfg.get("allow_video_only_passthrough", True))

        cooldown_cfg = cfg.get("cooldown_s", {})
        self._cooldowns: Dict[str, float] = {
            "audio_scream_person":    float(cooldown_cfg.get("audio_scream_person", 30)),
            "gunshot_audio_only":     float(cooldown_cfg.get("gunshot_audio_only", 60)),
            "explosion_audio_only":   float(cooldown_cfg.get("explosion_audio_only", 60)),
            "glass_break_intrusion":  float(cooldown_cfg.get("glass_break_intrusion", 60)),
            "alarm_fire":             float(cooldown_cfg.get("alarm_fire", 60)),
            "generic_multimodal":     float(cooldown_cfg.get("generic_multimodal", 30)),
        }

        # Rolling buffers: keep last window_s seconds of observations
        self._audio_buf: deque = deque()
        self._video_buf: deque = deque()

        # Ready-to-consume fused observations
        self._output_queue: deque = deque()

        # Cooldown tracking: fused_label → last_fire_unix_ts
        self._last_fire: Dict[str, float] = {}

        # Stats
        self._fusions_attempted = 0
        self._fusions_emitted   = 0
        self._audio_only_emitted = 0

        # Phase 2: Normality Store and Gating
        from .normality_store import NormalityStore
        self._normality_store = NormalityStore(
            persist_path="/app/data/normality/normality_profiles_v1.json",
            ema_alpha=cfg.get("normality_ema_alpha", 0.05),
            logger=self.logger
        )
        self._uncertainty_threshold = float(cfg.get("uncertainty_threshold", 0.6))
        
        # We will add learned fusion later, setting up config for it
        self._learned_fusion_mode = cfg.get("learned_fusion_mode", "shadow") # off | shadow
        
        if self._learned_fusion_mode != "off":
            from .learned_fusion import LearnedFusionHead
            # For MVP, we pass None as checkpoint_path to use initialized weights in shadow mode
            self._learned_fusion = LearnedFusionHead(checkpoint_path=cfg.get("learned_fusion_checkpoint"))
        else:
            self._learned_fusion = None

    # ── Feed ──────────────────────────────────────────────────────────────────

    def feed_audio(self, obs: Observation) -> None:
        """
        Feed an audio Observation (from AudioAnomalyLane) into the fusion window.
        Must be called from the audio_loop with lane="audio_anomaly" observations.
        """
        if not self._enabled:
            return
        if obs.lane != "audio_anomaly":
            return
        self._audio_buf.append(obs)
        self._evict_old(self._audio_buf)

    def feed_video(self, observations: List[Observation]) -> None:
        """
        Feed video Observations from the current frame cycle.
        Must be called once per frame with all triggering video observations.
        """
        if not self._enabled:
            return
        for obs in observations:
            if obs.trigger and obs.lane != "audio_anomaly":
                self._video_buf.append(obs)
        self._evict_old(self._video_buf)
        # Attempt fusion after every video update
        self._run_fusion()

    def flush(self) -> List[Observation]:
        """
        Return all fused Observations accumulated since last flush.
        Call once per frame cycle after feed_video().
        """
        result = []
        while self._output_queue:
            result.append(self._output_queue.popleft())
        return result

    # ── Eviction ──────────────────────────────────────────────────────────────

    def _evict_old(self, buf: deque) -> None:
        """Remove entries older than window_s from the left of the deque."""
        cutoff = time.time() - self._window_s
        while buf:
            obs = buf[0]
            try:
                ts = _parse_utc_ts(obs.ts_utc)
            except Exception:
                ts = 0.0
            if ts < cutoff:
                buf.popleft()
            else:
                break

    # ── Core fusion logic ──────────────────────────────────────────────────────

    def _run_fusion(self) -> None:
        """
        Main fusion step — called after every video update.

        For each audio observation in the window, attempt to fuse with any
        co-occurring video observations.  Emit at most one fused observation per
        audio label per cooldown window.
        """
        if not self._audio_buf:
            return

        now_ts = time.time()

        # Snapshot current windows (newest-first for priority)
        audio_obs_list = list(self._audio_buf)
        video_obs_list = list(self._video_buf)

        # Collect unique video alert types/labels in window
        video_types: Dict[str, float] = {}   # type_or_label → max_score
        for vobs in video_obs_list:
            if vobs.trigger:
                atype = self._resolve_video_type(vobs)
                video_types[atype] = max(video_types.get(atype, 0.0), vobs.score)
                # Also index by raw label for synergy
                if vobs.label:
                    video_types[vobs.label] = max(video_types.get(vobs.label, 0.0), vobs.score)

        # Process each audio observation
        for aobs in audio_obs_list:
            audio_label = aobs.label or ""
            if not audio_label:
                continue

            self._fusions_attempted += 1

            # --- Phase 2: Normality Store & Uncertainty Gating ---
            self._normality_store.update_baseline(self.camera_id, audio_label, aobs.score)
            
            adjusted_score, n_mean, n_std, n_z = self._normality_store.get_adjusted_score(
                self.camera_id, audio_label, aobs.score
            )
            
            # Extract composite uncertainty
            unc_dict = aobs.debug.get("audio_uncertainty", {})
            if isinstance(unc_dict, dict):
                audio_uncertainty = unc_dict.get("composite", 0.0)
            else:
                audio_uncertainty = float(unc_dict)
                
            is_too_uncertain = audio_uncertainty > self._uncertainty_threshold
            
            # If the adjusted score is effectively zero, it is fully suppressed by normality.
            if adjusted_score < 0.01 and audio_label not in _HIGH_RISK_AUDIO_LABELS:
                continue
                
            # Update aobs score conceptually for fusion (though we don't mutate aobs directly)
            effective_audio_score = adjusted_score

            # ── Case 1: Synergy pair found (audio + video both present) ────────
            fused_label = None
            synergy_video_score = 0.0

            for (a_lbl, v_lbl), f_lbl in _SYNERGY_MAP.items():
                if a_lbl == audio_label and v_lbl in video_types:
                    fused_label = f_lbl
                    synergy_video_score = video_types[v_lbl]
                    break

            if fused_label is not None:
                # Gate synergy if uncertainty is too high
                synergy_allowed = not is_too_uncertain
                
                fused_score = self._compute_fused_score(
                    audio_score=effective_audio_score,
                    video_score=synergy_video_score,
                    synergy=synergy_allowed,
                )
                if fused_score >= self._fused_threshold:
                    if self._check_cooldown(fused_label, now_ts):
                        self._emit_fused(
                            audio_obs=aobs,
                            video_types=video_types,
                            fused_label=fused_label,
                            fused_score=fused_score,
                            fusion_reason=f"synergy({audio_label}+{fused_label})",
                        )
                continue  # consumed this audio obs

            # ── Case 2: Audio-only high-risk (no video required) ──────────────
            if audio_label in _HIGH_RISK_AUDIO_LABELS and self._allow_audio_only_high:
                if is_too_uncertain:
                    # Do not emit audio-only high risk if highly uncertain
                    pass
                else:
                    fused_score = effective_audio_score   # no video to blend
                    if fused_score >= self._fused_threshold:
                        cooldown_key = f"{audio_label}_audio_only"
                        if self._check_cooldown(cooldown_key, now_ts):
                            self._emit_fused(
                                audio_obs=aobs,
                                video_types=video_types,
                                fused_label=f"{audio_label}_audio_only",
                                fused_score=fused_score,
                                fusion_reason=f"audio_only_high_risk({audio_label})",
                            )
                            self._audio_only_emitted += 1
                    continue

            # ── Case 3: Generic audio + video co-occurrence ────────────────────
            if video_types:
                # Take max video score as evidence of activity
                max_video_score = max(video_types.values())
                fused_score = self._compute_fused_score(
                    audio_score=effective_audio_score,
                    video_score=max_video_score,
                    synergy=False,
                )
                if fused_score >= self._fused_threshold:
                    generic_label = f"{audio_label}_generic"
                    if self._check_cooldown(generic_label, now_ts):
                        self._emit_fused(
                            audio_obs=aobs,
                            video_types=video_types,
                            fused_label=generic_label,
                            fused_score=fused_score,
                            fusion_reason=f"generic_cooccurrence({audio_label})",
                        )

    # ── Score computation ──────────────────────────────────────────────────────

    def _compute_fused_score(
        self,
        audio_score: float,
        video_score: float,
        synergy: bool,
    ) -> float:
        """
        Weighted fusion with optional synergy bonus.

        score = audio_weight * audio + video_weight * video ± adjustments
        """
        if video_score == 0.0:
            # Audio-only path — no blending
            base = audio_score
        else:
            base = (
                self._audio_weight * audio_score
                + self._video_weight * video_score
            )

        if synergy:
            base = min(1.0, base + self._synergy_bonus)

        return float(round(min(1.0, max(0.0, base)), 4))

    # ── Cooldown ───────────────────────────────────────────────────────────────

    def _check_cooldown(self, label: str, now_ts: float) -> bool:
        """
        Returns True if the fused label is NOT in cooldown (can fire).
        Updates last_fire timestamp if allowed.
        """
        last = self._last_fire.get(label, 0.0)
        cooldown = self._cooldowns.get(label, self._cooldowns["generic_multimodal"])

        if now_ts - last < cooldown:
            return False

        self._last_fire[label] = now_ts
        return True

    # ── Emit ───────────────────────────────────────────────────────────────────

    def _emit_fused(
        self,
        audio_obs: Observation,
        video_types: Dict[str, float],
        fused_label: str,
        fused_score: float,
        fusion_reason: str,
    ) -> None:
        """Construct a fused Observation and push to the output queue."""
        severity = "SEVERE" if fused_score >= self._high_severity_threshold else "HIGH"

        debug = {
            "modality": "fusion",
            "fusion_reason": fusion_reason,
            "fused_label": fused_label,
            "fused_score": fused_score,
            "severity_hint": severity,
            "audio_label": audio_obs.label,
            "audio_score": round(audio_obs.score, 4),
            "audio_ts": audio_obs.ts_utc,
            "video_context": {k: round(v, 4) for k, v in video_types.items() if v > 0.01},
            "audio_debug": audio_obs.debug or {},
            "weights": {
                "audio": self._audio_weight,
                "video": self._video_weight,
                "synergy_bonus": self._synergy_bonus,
            },
        }
        
        # --- Phase 2: Learned Fusion Shadow Mode ---
        if self._learned_fusion:
            import datetime
            import math
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            
            # Extract basic normality if available (if not, we could default to 0.0)
            # To be precise, we need the values from earlier, but we can recompute
            adj, n_mean, n_std, n_z = self._normality_store.get_adjusted_score(
                self.camera_id, audio_obs.label or "", audio_obs.score
            )
            
            unc_dict = audio_obs.debug.get("audio_uncertainty", {}) if audio_obs.debug else {}
            comp_unc = unc_dict.get("composite", 0.0) if isinstance(unc_dict, dict) else float(unc_dict) if unc_dict else 0.0
            
            features = {
                "audio_score_raw": audio_obs.score,
                "audio_score_adjusted": adj,
                "audio_uncertainty": comp_unc,
                "audio_label": audio_obs.label or "",
                "video_score": max(video_types.values()) if video_types else 0.0,
                "video_label": list(video_types.keys())[0] if video_types else "",
                "video_lane": "weapon_yolo", # generic proxy
                "time_delta_ms": 0.0, # simplified MVP since flush is near-instant
                "normality_mean": n_mean,
                "normality_std": n_std,
                "normality_z": n_z,
                "hour_sin": math.sin(2 * math.pi * now_dt.hour / 24.0),
                "hour_cos": math.cos(2 * math.pi * now_dt.hour / 24.0),
            }
            
            shadow_score = self._learned_fusion.predict(features)
            debug["learned_fusion_shadow_score"] = shadow_score
            
            # Explicitly log shadow score for data collection
            self.logger.info(
                f"ShadowMode: camera={self.camera_id} fused_label={fused_label} "
                f"deterministic_score={fused_score:.3f} shadow_score={shadow_score:.3f}"
            )

        fused_obs = Observation(
            ts_utc=now_iso_utc(),
            camera_id=self.camera_id,
            lane="audio_video_fusion",
            score=fused_score,
            trigger=True,
            bbox=None,
            label=fused_label,
            debug=debug,
        )

        self._output_queue.append(fused_obs)
        self._fusions_emitted += 1

        self.logger.info(
            f"FusedAlert: camera={self.camera_id} label={fused_label} "
            f"score={fused_score:.3f} reason={fusion_reason}"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_video_type(self, obs: Observation) -> str:
        """Get the alert-type-like string for a video observation."""
        from ..logic.aggregator import LANE_TO_ALERT_TYPE, LABEL_TO_ALERT_TYPE
        static = LANE_TO_ALERT_TYPE.get(obs.lane)
        if static:
            return static
        if obs.label:
            return LABEL_TO_ALERT_TYPE.get(obs.label, obs.label)
        return obs.lane

    # ── Status ────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return stats dict for the AI health endpoint."""
        return {
            "enabled": self._enabled,
            "camera_id": self.camera_id,
            "window_s": self._window_s,
            "audio_buf_size": len(self._audio_buf),
            "video_buf_size": len(self._video_buf),
            "output_queue_size": len(self._output_queue),
            "fusions_attempted": self._fusions_attempted,
            "fusions_emitted": self._fusions_emitted,
            "audio_only_emitted": self._audio_only_emitted,
            "cooldown_state": {k: round(v, 1) for k, v in self._last_fire.items()},
        }


# ── Aggregator integration ─────────────────────────────────────────────────
# Register audio_video_fusion lane in the aggregator's lookup tables.
# Called at startup by app.py or engine_loader.py.

def register_fusion_lane(aggregator) -> None:
    """
    Wire the fusion lane into the aggregator's alert-type and severity tables.

    Call ONCE after aggregator construction, before any camera is started.
    """
    from ..logic.aggregator import LANE_TO_ALERT_TYPE, LABEL_TO_ALERT_TYPE, ALERT_SEVERITY

    LANE_TO_ALERT_TYPE["audio_video_fusion"] = "AUDIO_ANOMALY"

    # Register all canonical fused labels
    for _, _, fused_label in _SYNERGY_PAIRS:
        LABEL_TO_ALERT_TYPE[fused_label] = "AUDIO_ANOMALY"

    # High-risk audio-only labels also map to AUDIO_ANOMALY
    for lbl in _HIGH_RISK_AUDIO_LABELS:
        LABEL_TO_ALERT_TYPE[f"{lbl}_audio_only"] = "AUDIO_ANOMALY"

    # Generic fallback
    LABEL_TO_ALERT_TYPE["audio_anomaly"] = "AUDIO_ANOMALY"

    # AUDIO_ANOMALY severity: HIGH by default (SEVERE when score >= high_severity_threshold)
    ALERT_SEVERITY["AUDIO_ANOMALY"] = "HIGH"

    aggregator.logger.info("MultimodalFusion: AUDIO_ANOMALY lane registered in aggregator")
