"""
Alert aggregator – combines K-of-N voting + cooldown + deduper + temporal verifier.

Entity-aware workflow (runtime):
  1. Detector produces persons/animals + track_id.
  2. Identity lane tries face/pet crop → embedding.
  3. IdentityMatcher compares against enrolled vectors (cosine sim).
  4. IdentityStabilizer converts noisy matches into stable identity
     per track_id (M-of-L voting + lock + decay).
  5. Aggregator (this module) consumes identity for severity/suppression:
       UNKNOWN_PERSON in restricted zone → HIGH
       KNOWN_OWNER/FAMILY              → LOW/suppress (policy-driven)
       PET (enrolled)                  → suppress pet alerts
  6. Alert JSON includes entity{...} and debug identity stats.

Key policies (spec §1-§3):
  • FIRE_SMOKE uses TWO-STAGE confirm:
      Stage A: fire_smoke_yolo K-of-N (3/5)
      Stage B: one of:
        (a) AnomalyCLIP/AnyAnomaly score ≥ fire_secondary_threshold, OR
        (b) temporal verifier confirms fire-like behaviour, OR
        (c) persistence rule: hits ≥ 4 of 5 (stronger K-of-N) if no secondary.
  • VIOLENCE_FIGHT requires candidate (2/5) + temporal verifier confirm.
  • FALL requires candidate (2/5) + temporal verifier confirm.
  • WEAPON_DETECTED: direct K-of-N from weapon_yolo lane.
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from collections import defaultdict, deque
from ..common.types import Observation, Alert
from ..common.timeutil import now_iso_utc
from ..common.log import setup_logger
from .voting import KofNVoter
from .cooldown import CooldownManager
from .deduper import Deduper

# Incident framework (optional — graceful if missing)
try:
    from ..incidents import IncidentRegistry
    _INCIDENT_FRAMEWORK_AVAILABLE = True
except ImportError:
    _INCIDENT_FRAMEWORK_AVAILABLE = False

# Identity imports (optional — graceful if missing)
try:
    from ..identity.schema import IdentityMatch, EntityCategory
    from ..identity.policy import IdentityPolicy, PolicyDecision
    from ..identity.matcher import IdentityMatcher
    from ..identity.store import EntityStore
    _IDENTITY_AVAILABLE = True
except ImportError:
    _IDENTITY_AVAILABLE = False


# Lane → alert type mapping
LANE_TO_ALERT_TYPE = {
    "rt_detr": None,              # dynamic — depends on label
    "yolov8_fallback": None,      # dynamic
    "person_zone": "INTRUSION_PERSON_IN_ZONE",
    "fire_smoke": "FIRE_SMOKE",
    "fire_smoke_yolo": "FIRE_SMOKE",
    "violence": "VIOLENCE_FIGHT",
    "violence_candidate": "VIOLENCE_FIGHT",
    "fall_candidate": "FALL",
    "weapon_yolo": "WEAPON_DETECTED",
    "accident": "ACCIDENT",
    "vad_generic": "UNKNOWN_SEVERE_ANOMALY",
    "anyanomaly": "UNKNOWN_SEVERE_ANOMALY",
    "anomalyclip": "UNKNOWN_SEVERE_ANOMALY",
    "temporal_verifier": None,     # never fires directly
}

# Label → alert type for dynamic lanes
LABEL_TO_ALERT_TYPE = {
    "person": "INTRUSION_PERSON_IN_ZONE",
    "loitering": "LOITERING",
    "fire": "FIRE_SMOKE",
    "smoke": "FIRE_SMOKE",
    "fire_smoke": "FIRE_SMOKE",
    "weapon": "WEAPON_DETECTED",
    "knife": "WEAPON_DETECTED",
    "gun": "WEAPON_DETECTED",
    "violence": "VIOLENCE_FIGHT",
    "violence_candidate": "VIOLENCE_FIGHT",
    "fall": "FALL",
    "fall_candidate": "FALL",
    "accident": "ACCIDENT",
    "crash": "ACCIDENT",
    "unknown_anomaly": "UNKNOWN_SEVERE_ANOMALY",
    "anomaly": "UNKNOWN_SEVERE_ANOMALY",
}

# Alert type → base severity  (VIOLENCE/FALL start MED; only SEVERE after temporal confirm)
ALERT_SEVERITY = {
    "FIRE_SMOKE": "SEVERE",
    "VIOLENCE_FIGHT": "MED",       # upgraded to SEVERE by temporal verifier
    "FALL": "MED",                  # upgraded to SEVERE by temporal verifier
    "WEAPON_DETECTED": "SEVERE",
    "INTRUSION_PERSON_IN_ZONE": "HIGH",
    "LOITERING": "MED",
    "ACCIDENT": "SEVERE",
    "UNKNOWN_SEVERE_ANOMALY": "MED",  # was HIGH; tightened to reduce FPs
}


class AlertAggregator:
    """
    Aggregates observations into confirmed alerts using K-of-N voting,
    cooldown, session deduplication, and optional temporal verifier.
    """

    def __init__(self, k: int = 3, n: int = 5, cooldown_s: int = 45,
                 alerts_dir: str = "alerts",
                 fire_secondary_threshold: float = 0.55,
                 per_type_kn: Optional[Dict[str, tuple]] = None):
        self.k = k
        self.n = n
        self.cooldown_s = cooldown_s
        self.alerts_dir = Path(alerts_dir)
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

        # Fire two-stage threshold
        self.fire_secondary_threshold = fire_secondary_threshold

        # Per-alert-type K/N overrides (e.g. {"FALL": (4, 8)})
        self._per_type_kn: Dict[str, tuple] = per_type_kn or {}
        self._per_type_voter_set: set = set()  # tracks which keys already have override

        # Per camera, per lane voters
        self.voters: Dict[tuple, KofNVoter] = defaultdict(
            lambda: KofNVoter(k=self.k, n=self.n)
        )

        # Cooldown & deduper
        self.cooldown_mgr = CooldownManager(default_cooldown_s=cooldown_s)
        self.deduper = Deduper(session_window_s=float(cooldown_s))

        # Real-time callbacks
        self.alert_callbacks: List[Callable[[Alert], None]] = []

        # In-memory buffer
        self.alert_buffer: List[Alert] = []
        self.max_buffer_size = 200

        # Lane vote accumulator per (camera, alert_type) for enriching payload
        self._lane_votes: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)

        # Secondary signal cache: (camera_id) → latest AnomalyCLIP / AnyAnomaly score
        self._secondary_scores: Dict[str, float] = defaultdict(float)

        # §4.1 — recent detector observations for explainable motion suppression
        # camera_id → list of recent {ts, label, track_id, identity_category}
        self._recent_detections: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._recent_detections_max = 50

        # Temporal verifier reference (set externally)
        self.temporal_verifier = None  # type: ignore

        # AnomalyCLIP / AnyAnomaly lane reference for fire two-stage
        self._anomaly_lanes = {"anomalyclip", "anyanomaly"}

        # ── Incident framework registry ───────────────────────────────
        self._incident_registry = None
        if _INCIDENT_FRAMEWORK_AVAILABLE:
            try:
                self._incident_registry = IncidentRegistry()
            except Exception:
                pass

        # ── Unknown anomaly rate limiting ─────────────────────────────
        # camera_id → deque of emission timestamps
        self._unknown_anomaly_emit_times: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=20)
        )
        self._unknown_anomaly_max_per_interval = 2   # max alerts
        self._unknown_anomaly_interval_s = 60.0       # window

        # ── Periodic motion detector (per camera) ─────────────────────
        # camera_id → list of recent anomaly scores for periodicity analysis
        self._anomaly_score_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=60)
        )

        # ── Global illumination change detector ───────────────────────
        # camera_id → recent mean frame brightness values
        self._brightness_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=20)
        )

        # ── Loitering observations from person_zone lane ──────────────
        self._person_zone_lanes: Dict[str, Any] = {}  # camera_id → PersonZoneLane

        # ── Identity subsystem (§8-§9) ────────────────────────────────
        self._identity_enabled = False
        self._identity_policy = None      # IdentityPolicy
        self._identity_store = None       # EntityStore
        # Per-camera latest identity observations: cam_id → {track_id → identity_dict}
        self._identity_cache: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)

        # §4.3 — zone-aware anomaly: cameras with configured zones
        self._zone_cameras: set = set()  # camera_ids that have zones configured

        # §C5 — debug counters for tuning + diagnostics
        self._suppression_counters: Dict[str, int] = defaultdict(int)
        self._last_motion_stats: Dict[str, Dict[str, Any]] = {}  # per camera

        self.logger = setup_logger("AlertAggregator")
        self.alerts_log = self.alerts_dir / "alerts.jsonl"

    # ------------------------------------------------------------------
    def set_temporal_verifier(self, verifier):
        """Attach temporal verifier lane for on-demand clip verification."""
        self.temporal_verifier = verifier

    def set_fire_secondary_threshold(self, threshold: float):
        self.fire_secondary_threshold = threshold

    def add_alert_callback(self, callback: Callable[[Alert], None]):
        self.alert_callbacks.append(callback)

    def set_identity(self, policy, store):
        """Wire identity policy + store into the aggregator."""
        self._identity_policy = policy
        self._identity_store = store
        self._identity_enabled = policy is not None
        self.logger.info(f"Identity subsystem {'enabled' if self._identity_enabled else 'disabled'}")

    def set_zone_cameras(self, zones_cfg: Dict[str, Any]):
        """§4.3 — Record which cameras have zones configured."""
        for cam_id, zones in zones_cfg.items():
            if zones:
                self._zone_cameras.add(cam_id)

    def set_person_zone_lane(self, camera_id: str, person_zone_lane):
        """Wire person_zone lane for loitering observation polling."""
        self._person_zone_lanes[camera_id] = person_zone_lane

    def get_incident_registry(self):
        """Return incident registry for diagnostics."""
        return self._incident_registry

    # ------------------------------------------------------------------
    def process_loitering(self, camera_id: str, evidence_request_callback=None, ringbuffer=None):
        """
        Poll person_zone lane for pending loitering events and emit LOITERING alerts.
        Called by the camera processor after each processing cycle.
        """
        pz_lane = self._person_zone_lanes.get(camera_id)
        if pz_lane is None or not hasattr(pz_lane, "get_pending_loitering"):
            return

        pending = pz_lane.get_pending_loitering()
        for event in pending:
            # Create a synthetic observation for loitering
            obs = Observation(
                ts_utc=event.get("ts_utc", now_iso_utc()),
                camera_id=camera_id,
                lane="person_zone",
                score=min(1.0, event.get("dwell_s", 0) / max(event.get("threshold_s", 30), 1)),
                trigger=True,
                label="loitering",
                track_id=event.get("track_id"),
                zone_name=event.get("zone_name"),
                debug={
                    "reason_codes": [
                        f"loitering_dwell ({event.get('dwell_s', 0):.0f}s > {event.get('threshold_s', 0):.0f}s)",
                        f"zone={event.get('zone_name', 'global')}",
                    ],
                    "dwell_s": event.get("dwell_s", 0),
                },
            )
            alert = self.process_observation(
                obs,
                evidence_request_callback=evidence_request_callback,
                ringbuffer=ringbuffer,
            )
            if alert:
                self.logger.info(
                    f"LOITERING alert: cam={camera_id} track={event.get('track_id')} "
                    f"dwell={event.get('dwell_s', 0):.0f}s zone={event.get('zone_name')}"
                )

    # ------------------------------------------------------------------
    def _resolve_alert_type(self, obs: Observation) -> str:
        """Determine alert type from lane + label."""
        static = LANE_TO_ALERT_TYPE.get(obs.lane)
        if static is not None:
            return static
        # Dynamic: look at label
        if obs.label:
            return LABEL_TO_ALERT_TYPE.get(obs.label, "UNKNOWN_SEVERE_ANOMALY")
        return "UNKNOWN_SEVERE_ANOMALY"

    # ------------------------------------------------------------------
    def _track_secondary_signal(self, obs: Observation):
        """Cache recent anomaly scores from AnomalyCLIP / AnyAnomaly for fire two-stage."""
        if obs.lane in self._anomaly_lanes and obs.score > 0:
            self._secondary_scores[obs.camera_id] = max(
                self._secondary_scores.get(obs.camera_id, 0),
                obs.score,
            )
        # §C5: Track motion stats from anomalyclip for diagnostics
        if obs.lane == "anomalyclip" and obs.debug:
            self._last_motion_stats[obs.camera_id] = {
                "score": obs.score,
                "motion_area_ratio": obs.debug.get("motion_area_ratio", 0),
                "persistence_hits": obs.debug.get("persistence_hits", 0),
                "persistence_window": obs.debug.get("persistence_window", 0),
                "suppress_reason": obs.debug.get("suppress_reason"),
            }

    # ------------------------------------------------------------------
    def _fire_two_stage_check(self, obs: Observation, hits: int, ringbuffer=None) -> Dict[str, Any]:
        """
        FIRE_SMOKE two-stage confirmation (spec §1).

        Stage A: fire_smoke_yolo K-of-N passed (caller already verified).
        Stage B: one of:
          (a) AnomalyCLIP/AnyAnomaly >= fire_secondary_threshold
          (b) temporal verifier confirms (optional)
          (c) persistence: hits >= 4 of 5

        Returns {"confirmed": bool, "reason": str, "secondary_score": float|None}
        """
        # Check persistence-based info from the fire lane's debug
        persistence_hits = 0
        min_persistence = 2
        if obs.debug:
            persistence_hits = obs.debug.get("persistence_hits", 0)
            min_persistence = obs.debug.get("min_persistence_required", 2)

        # Persistence gate: need at least min_persistence_hits within window
        if persistence_hits < min_persistence:
            return {
                "confirmed": False,
                "reason": f"persistence_too_low ({persistence_hits}<{min_persistence})",
                "secondary_score": None,
            }

        # --- Stage B checks ---
        secondary_score = self._secondary_scores.get(obs.camera_id, 0.0)

        # (a) Secondary anomaly signal
        if secondary_score >= self.fire_secondary_threshold:
            return {
                "confirmed": True,
                "reason": f"secondary_anomaly ({secondary_score:.2f}>={self.fire_secondary_threshold})",
                "secondary_score": secondary_score,
            }

        # (b) Temporal verifier
        if self.temporal_verifier is not None and ringbuffer is not None:
            try:
                raw_frames = ringbuffer.get_raw_frames_before(obs.ts_utc, seconds=4.0, max_frames=16)
                if raw_frames:
                    tv_result = self.temporal_verifier.verify_clip(raw_frames, target_label="fire")
                    if tv_result.get("confirmed", False):
                        return {
                            "confirmed": True,
                            "reason": f"temporal_verified (score={tv_result.get('score', 0):.2f})",
                            "secondary_score": secondary_score,
                        }
            except Exception as e:
                self.logger.error(f"Fire temporal verify error: {e}")

        # (c) Strong persistence removed — no longer bypasses secondary
        # Callers must have at least secondary anomaly signal or temporal confirmation.

        return {
            "confirmed": False,
            "reason": f"no_secondary_confirmation (sec={secondary_score:.2f}, hits={hits}/5)",
            "secondary_score": secondary_score,
        }

    # ------------------------------------------------------------------
    def process_observation(
        self,
        obs: Observation,
        evidence_request_callback: Optional[Callable] = None,
        ringbuffer=None,
    ) -> Optional[Alert]:
        """
        Process an observation through K-of-N → cooldown → deduper → (two-stage / temporal) → fire.
        Identity-lane observations are cached rather than alert-producing.
        """
        # Track secondary anomaly signals for fire two-stage
        self._track_secondary_signal(obs)

        # §4.1 — track detector observations for explainable motion suppression
        self._track_detector_observation(obs)

        # ── Identity lane: cache identities, do NOT produce alerts ────
        if obs.lane == "entity_identity":
            self._cache_identity_observation(obs)
            return None

        # ── Pose lane: data-only, never produces alerts ───────────────
        if obs.lane == "yolo_pose":
            return None

        key = (obs.camera_id, obs.lane)

        # Use per-alert-type K/N override if configured (e.g. FALL → 4/8)
        alert_type = self._resolve_alert_type(obs)
        type_kn = self._per_type_kn.get(alert_type)
        if type_kn and key not in self._per_type_voter_set:
            k_override, n_override = type_kn
            self.voters[key] = KofNVoter(k=k_override, n=n_override)
            self._per_type_voter_set.add(key)

        voter = self.voters[key]
        confirmed, hits = voter.vote(obs.trigger)

        # Accumulate lane votes for payload
        vote_key = (obs.camera_id, alert_type)
        self._lane_votes[vote_key].append({
            "lane": obs.lane,
            "score": round(obs.score, 3),
            "trigger": obs.trigger,
        })
        # Keep only last N*2 votes
        if len(self._lane_votes[vote_key]) > self.n * 2:
            self._lane_votes[vote_key] = self._lane_votes[vote_key][-self.n * 2:]

        if not confirmed:
            return None

        # ---- FIRE_SMOKE: two-stage confirm ----
        fire_stage_b = None
        if alert_type == "FIRE_SMOKE":
            fire_stage_b = self._fire_two_stage_check(obs, hits, ringbuffer)
            if not fire_stage_b["confirmed"]:
                self._suppression_counters[f"fire_two_stage:{fire_stage_b['reason']}"] += 1
                self.logger.debug(
                    f"FIRE_SMOKE K-of-N passed but two-stage REJECTED: {fire_stage_b['reason']}"
                )
                return None

        # §4.1 — Explainable motion suppression for UNKNOWN_SEVERE_ANOMALY
        if alert_type == "UNKNOWN_SEVERE_ANOMALY":
            suppress_reason = self._should_suppress_unknown_anomaly(obs)
            if suppress_reason:
                self._suppression_counters[suppress_reason] += 1
                self.logger.debug(
                    f"UNKNOWN_SEVERE_ANOMALY suppressed: {suppress_reason}"
                )
                return None

        # Cooldown check
        if not self.cooldown_mgr.can_fire(obs.camera_id, alert_type, obs.ts_utc):
            return None

        self.logger.info(f"🚨 ALERT CONFIRMED: {alert_type} camera={obs.camera_id}")

        # Mark cooldown
        self.cooldown_mgr.mark_fired(obs.camera_id, alert_type, obs.ts_utc)

        # §8 — Rate limit tracking for unknown anomaly
        if alert_type == "UNKNOWN_SEVERE_ANOMALY":
            self._unknown_anomaly_emit_times[obs.camera_id].append(time.time())

        # Session deduplication
        session_id = self.deduper.get_session_id(
            obs.camera_id,
            obs.label or alert_type,
            obs.ts_utc,
            obs.track_id,
        )

        # ── Temporal verification for VIOLENCE / FALL (required for SEVERE) ──
        temporal_result = {"confirmed": None, "score": None}
        verifier_available = (
            self.temporal_verifier is not None
            and getattr(self.temporal_verifier, 'available', True)
            and ringbuffer is not None
        )

        if alert_type in ("VIOLENCE_FIGHT", "FALL"):
            if verifier_available:
                try:
                    raw_frames = ringbuffer.get_raw_frames_before(obs.ts_utc, seconds=5.0, max_frames=16)
                    target = "fall" if alert_type == "FALL" else "violence"
                    temporal_result = self.temporal_verifier.verify_clip(raw_frames, target_label=target)
                except Exception as e:
                    self.logger.error(f"Temporal verify error: {e}")

            # ── FALL hard gate when temporal verifier unavailable ──────
            if alert_type == "FALL" and not verifier_available:
                obs_debug = obs.debug or {}
                lying_persist = obs_debug.get("lying_persist", False)
                post_fall_still = obs_debug.get("post_fall_still", False)
                pose_conf = obs_debug.get("pose_conf", 0.0)

                if pose_conf < 0.35:
                    self._suppression_counters["fall_suppressed:low_pose_conf"] += 1
                    self.logger.debug(
                        f"FALL suppressed: low_pose_conf={pose_conf:.2f}"
                    )
                    return None

                if not (lying_persist and post_fall_still):
                    self._suppression_counters["fall_suppressed:no_verifier_and_not_strong"] += 1
                    self.logger.debug(
                        "FALL suppressed: temporal verifier unavailable and "
                        f"not strong (lying_persist={lying_persist}, post_fall_still={post_fall_still})"
                    )
                    return None

                self.logger.info(
                    "FALL proceeding without temporal verifier (lying_persist + post_fall_still confirmed)"
                )

            elif alert_type == "FALL" and verifier_available:
                # Temporal verifier available — require confirmation for HIGH/SEVERE
                # (severity assignment happens later, but log the result)
                self.logger.debug(
                    f"FALL temporal result: confirmed={temporal_result.get('confirmed')}, "
                    f"score={temporal_result.get('score')}"
                )

        # Evidence export
        evidence_paths = {"keyframe_path": "", "clip_path": "", "partial_clip": False}
        if evidence_request_callback:
            try:
                evidence_paths = evidence_request_callback(obs.camera_id, alert_type, obs.ts_utc)
            except Exception as e:
                self.logger.error(f"Evidence request failed: {e}")

        # Gather lane_votes for payload
        lane_votes = self._lane_votes.get(vote_key, [])[-self.n:]

        # Weighted confidence average
        scores = [v["score"] for v in lane_votes if v["trigger"]]
        avg_conf = sum(scores) / max(len(scores), 1)

        # Build debug info
        debug_info = dict(obs.debug or {})
        if fire_stage_b:
            debug_info["fire_two_stage"] = fire_stage_b

        # ── Ensure reason_codes from observation are propagated ───────
        if "reason_codes" not in debug_info:
            debug_info["reason_codes"] = []

        # ── Resolve entity identity for this alert ────────────────────
        entity_dict = {"id": None, "name": None, "category": None, "confidence": 0.0}
        identity_evidence = {}
        identity_match = None
        if self._identity_enabled:
            # For INTRUSION alerts, person_zone and entity_identity lanes use
            # different trackers, so track_id won't match.  Check camera-level
            # identity cache instead.
            if alert_type == "INTRUSION_PERSON_IN_ZONE":
                identity_match = self._any_known_entity_on_camera(obs.camera_id)
            elif obs.track_id is not None:
                identity_match = self._lookup_identity(obs.camera_id, obs.track_id)
            if identity_match is not None:
                eid = identity_match.get("entity_id")
                ename = identity_match.get("name")
                ecat = identity_match.get("category")

                # Fallback name resolution from entity store
                if eid and not ename and self._identity_store is not None:
                    try:
                        rec = self._identity_store.get_entity(eid)
                        if rec is not None:
                            ename = rec.get("name")
                    except Exception:
                        pass

                # Validate category
                if _IDENTITY_AVAILABLE and ecat:
                    ecat = EntityCategory.validate(ecat)

                entity_dict = {
                    "id": eid,
                    "name": ename,
                    "category": ecat,
                    "confidence": identity_match.get("confidence", 0.0),
                }
                # §6.1 — identity evidence in alert payload
                identity_evidence = {
                    "best_sim": identity_match.get("best_sim", 0.0),
                    "second_sim": identity_match.get("second_sim", 0.0),
                    "margin": identity_match.get("margin", 0.0),
                    "quality_ok": identity_match.get("quality_ok", True),
                    "locked": identity_match.get("locked", False),
                }

        if identity_evidence:
            debug_info["identity"] = identity_evidence

        # ── Determine severity ────────────────────────────────────────
        base_severity = ALERT_SEVERITY.get(alert_type, "HIGH")

        # §3 — Temporal-confirmed VIOLENCE/FALL upgrade to SEVERE
        reason_fired_parts = [f"k_of_n ({hits}/{self.n})"]
        if alert_type in ("VIOLENCE_FIGHT", "FALL"):
            if temporal_result.get("confirmed"):
                base_severity = "SEVERE"
                reason_fired_parts.append(
                    f"temporal_confirmed (score={temporal_result.get('score', 0):.2f})"
                )
            else:
                reason_fired_parts.append("temporal_not_confirmed — severity stays MED")
        elif alert_type == "FIRE_SMOKE" and fire_stage_b:
            reason_fired_parts.append(f"fire_two_stage: {fire_stage_b.get('reason', '')}")

        debug_info["reason_fired"] = " | ".join(reason_fired_parts)

        # ── Apply identity policy (severity override / suppression) ───
        if self._identity_enabled and self._identity_policy is not None and _IDENTITY_AVAILABLE:
            id_match_obj = None
            if identity_match is not None:
                id_match_obj = IdentityMatch(
                    entity_id=identity_match.get("entity_id"),
                    name=identity_match.get("name"),
                    category=identity_match.get("category", "UNKNOWN_PERSON"),
                    confidence=identity_match.get("confidence", 0.0),
                    score=identity_match.get("score", 0.0),
                )
            zone_name = obs.zone_name
            is_restricted = bool(zone_name)  # any zone is treated as restricted for MVP
            policy_decision = self._identity_policy.apply(
                alert_type, id_match_obj, zone_name, is_restricted,
            )
            if policy_decision.suppress:
                self._suppression_counters[f"identity_policy:{policy_decision.reason}"] += 1
                self.logger.info(
                    f"Alert SUPPRESSED by identity policy: {policy_decision.reason}"
                )
                debug_info["identity_policy"] = {
                    "action": "suppressed",
                    "reason": policy_decision.reason,
                }
                return None
            if policy_decision.severity is not None:
                base_severity = policy_decision.severity
                debug_info["identity_policy"] = {
                    "action": "severity_override",
                    "severity": policy_decision.severity,
                    "reason": policy_decision.reason,
                }

        alert = Alert(
            ts_utc=obs.ts_utc,
            camera_id=obs.camera_id,
            type=alert_type,
            label=obs.label or "",
            severity=base_severity,
            confidence=round(avg_conf, 3),
            k_of_n={"k": self.k, "n": self.n, "hits": hits},
            session_id=session_id,
            cooldown_s=self.cooldown_s,
            evidence=evidence_paths,
            payload={
                "bboxes": [obs.bbox] if obs.bbox else [],
                "zone_name": obs.zone_name,
                "track_id": obs.track_id,
                "lane_votes": lane_votes,
                "temporal_verifier": temporal_result,
            },
            entity=entity_dict,
            debug=debug_info,
        )

        # Persist + buffer
        self._write_alert_jsonl(alert)
        self.alert_buffer.append(alert)
        if len(self.alert_buffer) > self.max_buffer_size:
            self.alert_buffer = self.alert_buffer[-self.max_buffer_size:]

        # Reset secondary score cache after fire alert (to not reuse stale scores)
        if alert_type == "FIRE_SMOKE":
            self._secondary_scores[obs.camera_id] = 0.0

        # Callbacks
        for cb in self.alert_callbacks:
            try:
                cb(alert)
            except Exception as e:
                self.logger.error(f"Alert callback error: {e}")

        return alert

    # ------------------------------------------------------------------
    def _write_alert_jsonl(self, alert: Alert):
        try:
            with open(self.alerts_log, "a") as f:
                f.write(json.dumps(alert.to_dict()) + "\n")
        except Exception as e:
            self.logger.error(f"JSONL write error: {e}")

    def get_recent_alerts(self, limit: int = 200) -> List[Dict]:
        return [a.to_dict() for a in self.alert_buffer[-limit:]]

    # ------------------------------------------------------------------
    # §C5 — Diagnostics data for /system/diagnostics
    # ------------------------------------------------------------------
    def get_diagnostics(self) -> Dict[str, Any]:
        """Return suppression counters, motion stats, temporal verifier stats, incident registry."""
        tv_stats = {}
        if self.temporal_verifier is not None:
            if hasattr(self.temporal_verifier, "get_last_run_stats"):
                tv_stats = self.temporal_verifier.get_last_run_stats()
            # Always include availability status
            tv_stats["available"] = getattr(self.temporal_verifier, "available", True)
            tv_stats["reason_unavailable"] = getattr(
                self.temporal_verifier, "reason_unavailable", None
            )
        else:
            tv_stats = {"available": False, "reason_unavailable": "temporal_verifier not attached"}

        # Incident registry diagnostics
        incident_info = {}
        if self._incident_registry:
            try:
                incident_info = self._incident_registry.get_diagnostics()
            except Exception:
                pass

        return {
            "suppression_counters": dict(self._suppression_counters),
            "motion_stats": dict(self._last_motion_stats),
            "temporal_verifier_stats": tv_stats,
            "incident_registry": incident_info,
        }

    # ------------------------------------------------------------------
    # §4.1 — Explainable motion suppression helpers
    # ------------------------------------------------------------------
    def _track_detector_observation(self, obs: Observation):
        """Cache recent detector observations for motion-explainability check."""
        detector_lanes = {"rt_detr", "yolov8_fallback", "person_zone"}
        if obs.lane not in detector_lanes:
            return
        if not obs.trigger:
            return

        # Resolve identity for this detection if available
        identity_category = None
        if obs.track_id is not None:
            ident = self._lookup_identity(obs.camera_id, obs.track_id)
            if ident:
                identity_category = ident.get("category")

        entry = {
            "ts": obs.ts_utc,
            "label": obs.label,
            "track_id": obs.track_id,
            "identity_category": identity_category,
            "zone_name": obs.zone_name,
        }
        dets = self._recent_detections[obs.camera_id]
        dets.append(entry)
        if len(dets) > self._recent_detections_max:
            self._recent_detections[obs.camera_id] = dets[-self._recent_detections_max:]

    def _should_suppress_unknown_anomaly(self, obs: Observation) -> Optional[str]:
        """
        §4.1 + §4.3 + §8 — Enhanced suppression for UNKNOWN_SEVERE_ANOMALY.
        Checks:
          1. Zone-aware filtering (anomaly outside monitored zones)
          2. Low-confidence filter
          3. Motion explained by benign detections (known person/pet)
          4. Rate limiting (max N per interval)
          5. Periodic motion detection (repeating anomaly pattern)
          6. Global illumination change detection
        Returns a suppression reason string if should suppress, else None.
        """
        # §4.3 — Zone-aware: if camera has zones but anomaly has no zone_name,
        # suppress (anomaly outside any monitored zone is not actionable)
        if obs.camera_id in self._zone_cameras and not obs.zone_name:
            # Check if ANY recent detection was in a zone
            dets = self._recent_detections.get(obs.camera_id, [])
            has_zone_activity = False
            for d in dets[-10:]:
                if d.get("zone_name"):
                    has_zone_activity = True
                    break
            if not has_zone_activity:
                return "zone_aware_no_zone_activity"

        # Low-confidence anomaly signals: suppress scores < 0.4
        if obs.score < 0.4:
            return f"low_anomaly_score ({obs.score:.2f}<0.40)"

        # ── Rate limiting: max 2 alerts per 60s ──────────────────────
        emit_times = self._unknown_anomaly_emit_times[obs.camera_id]
        now = time.time()
        # Count recent emissions within window
        recent_count = sum(
            1 for t in emit_times
            if now - t < self._unknown_anomaly_interval_s
        )
        if recent_count >= self._unknown_anomaly_max_per_interval:
            return f"rate_limited ({recent_count}>={self._unknown_anomaly_max_per_interval} in {self._unknown_anomaly_interval_s}s)"

        # ── Periodic motion detection ─────────────────────────────────
        # Track anomaly scores over time; if they oscillate regularly,
        # it's likely a recurring/periodic source (e.g. rotating fan, tree)
        score_hist = self._anomaly_score_history[obs.camera_id]
        score_hist.append(obs.score)
        if len(score_hist) >= 12:
            scores_list = list(score_hist)[-12:]
            # Detect periodicity: count sign changes in score deltas
            deltas = [scores_list[i+1] - scores_list[i] for i in range(len(scores_list)-1)]
            sign_changes = sum(
                1 for i in range(len(deltas)-1)
                if (deltas[i] > 0) != (deltas[i+1] > 0)
            )
            # High number of sign changes = oscillation = periodic
            if sign_changes >= 7:  # 7+ reversals in 11 deltas → periodic
                return f"periodic_motion (sign_changes={sign_changes}/11)"

        # ── Global illumination change ────────────────────────────────
        brightness = (obs.debug or {}).get("frame_brightness")
        if brightness is not None:
            bright_hist = self._brightness_history[obs.camera_id]
            bright_hist.append(float(brightness))
            if len(bright_hist) >= 3:
                recent_vals = list(bright_hist)[-3:]
                delta = abs(recent_vals[-1] - recent_vals[0])
                avg = sum(recent_vals) / len(recent_vals)
                if avg > 0 and delta / avg > 0.25:
                    return f"global_illumination_change (delta={delta:.1f}, avg={avg:.1f})"

        # ── Benign motion explanation ─────────────────────────────────
        dets = self._recent_detections.get(obs.camera_id, [])
        if not dets:
            return None

        # Only look at recent detections (last 10 entries)
        recent = dets[-10:]

        # Benign categories that explain motion
        benign_categories = {"KNOWN_PERSON", "PET"}
        benign_labels = {"person", "cat", "dog", "car", "truck", "bus", "bicycle", "motorcycle"}

        benign_count = 0
        known_names = []
        for d in recent:
            label = (d.get("label") or "").lower()
            id_cat = d.get("identity_category") or ""

            # Known identity explains motion
            if id_cat in benign_categories:
                benign_count += 1
                known_names.append(id_cat)
                continue

            # Common benign labels (person detected but may not be identified yet)
            if label in benign_labels:
                benign_count += 1

        # Suppress if majority of recent detections are benign
        # §3 tightened: 40% instead of 50%, minimum 2
        if benign_count >= len(recent) * 0.4 and benign_count >= 2:
            return f"motion_explained_by_benign ({benign_count}/{len(recent)} benign: {','.join(set(known_names)) or 'common_objects'})"

        return None

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------
    def _cache_identity_observation(self, obs: Observation):
        """
        Cache identity results from the entity_identity lane.
        obs.debug["identities"] is a list of dicts with keys:
          entity_id, name, category, confidence, score, track_id, bbox
        """
        identities = (obs.debug or {}).get("identities", [])
        cam_cache = self._identity_cache[obs.camera_id]
        for ident in identities:
            tid = ident.get("track_id")
            if tid is not None:
                cam_cache[tid] = ident
        # Evict stale entries (keep last 50)
        if len(cam_cache) > 50:
            keys = list(cam_cache.keys())
            for k in keys[:-50]:
                del cam_cache[k]

    def _lookup_identity(self, camera_id: str, track_id: int) -> Optional[Dict[str, Any]]:
        """Look up cached identity for a track_id from the entity_identity lane."""
        cam_cache = self._identity_cache.get(camera_id, {})
        return cam_cache.get(track_id)

    def _any_known_entity_on_camera(self, camera_id: str) -> Optional[Dict[str, Any]]:
        """
        Check if ANY known (enrolled) entity is currently visible on a camera.
        Returns the best known identity dict if found, else None.

        This is needed because person_zone and entity_identity lanes use
        different trackers, so track_id matching will fail. Instead, for
        INTRUSION alerts, we check the identity cache at the camera level.
        """
        cam_cache = self._identity_cache.get(camera_id, {})
        if not cam_cache:
            return None
        known_categories = {"KNOWN_PERSON", "PET"}
        best = None
        best_conf = 0.0
        for _tid, ident in cam_cache.items():
            cat = ident.get("category", "")
            conf = ident.get("confidence", 0.0)
            if cat in known_categories and conf > best_conf:
                best = ident
                best_conf = conf
        return best

