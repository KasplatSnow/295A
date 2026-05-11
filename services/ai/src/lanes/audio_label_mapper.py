"""
PR-03: AudioSet → VigilZone canonical label mapper.

Converts raw BEATs/AudioSet label strings into stable VigilZone product labels.

Design rules (plan Section 6.6):
  - Map only safety-relevant sounds to alert labels.
  - Never map music, speech, or ambiguous everyday sounds to danger categories.
  - Never infer gunshot from non-firearm wording ("Crash cymbal", "Pop music", etc.).
  - Never map every loud sound to audio_anomaly.
  - Keep raw labels in debug for operator review.

All mappings are keyword-based substring matches against the raw AudioSet label
(case-insensitive). The FIRST matching rule in each category wins.

Extending this mapper:
  - Add new patterns to the relevant category list.
  - Add a new canonical label to CANONICAL_LABELS if needed.
  - Never change existing canonical label strings — the backend maps them to DB types.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# ── Canonical product labels ────────────────────────────────────────────────
# These are the stable strings the rest of the system (aggregator, backend,
# incident serializer, frontend) relies on.  Do NOT rename them.
CANONICAL_LABELS = {
    "audio_scream",
    "audio_gunshot",
    "audio_explosion",
    "audio_glass_break",
    "audio_siren",
    "audio_alarm",
    "audio_vehicle_crash",
    "audio_shout",
}

# ── Mapping rules ───────────────────────────────────────────────────────────
# Each entry is (canonical_label, [list of raw-label substrings that trigger it]).
# Matching is case-insensitive substring match.
# Order matters within categories: more specific rules come first.
_RULES: List[Tuple[str, List[str]]] = [

    # ── Screaming / Shouting ───────────────────────────────────────────────
    # NOTE: "Shout" alone is less severe — maps to audio_shout, not audio_scream.
    # Use audio_scream for high-severity labels only.
    ("audio_scream", [
        "screaming",
        "scream",
        "crying",
        "shriek",
        "wail",
        "whimper",
        "groan",
        "child screaming",
        "woman screaming",
    ]),
    ("audio_shout", [
        "shout",
        "yell",
        "battle cry",
        "howl",
    ]),

    # ── Gunshots / Firearms ────────────────────────────────────────────────
    # STRICT: only map if wording explicitly refers to firearms.
    # "Crash cymbal", "Balloon pop", "Cap gun" → BLOCKED below.
    ("audio_gunshot", [
        "gunshot",
        "gunfire",
        "shot gun",
        "shotgun",
        "pistol shot",
        "rifle shot",
        "firearm",
        "machine gun",
        "submachine gun",
        "artillery",
        "cannon",
        "explosion (firearm)",
    ]),

    # ── Explosions ────────────────────────────────────────────────────────
    # Only map loud destructive explosion labels.
    ("audio_explosion", [
        "explosion",
        "bomb",
        "blast",
        "detonation",
        "grenade",
        "fireworks",       # debatable — kept for safety
    ]),

    # ── Glass break ───────────────────────────────────────────────────────
    ("audio_glass_break", [
        "glass",
        "breaking",
        "shatter",
        "smash",
        "crack",
    ]),

    # ── Emergency sirens / alarms ─────────────────────────────────────────
    ("audio_siren", [
        "siren",
        "emergency vehicle",
        "ambulance",
        "fire truck",
        "police car",
        "civil defense siren",
        "warning siren",
    ]),
    ("audio_alarm", [
        "alarm",
        "fire alarm",
        "smoke detector",
        "buzzer",
        "bell",
        "beep, bleep",
        "emergency alarm",
        "carbon monoxide detector",
        "air horn",
        "foghorn",
    ]),

    # ── Vehicle crash ─────────────────────────────────────────────────────
    # Strict: only map when combined with vehicle/crash wording.
    # Do NOT map "crash cymbal" here — it is blocked below.
    ("audio_vehicle_crash", [
        "car crash",
        "vehicle crash",
        "traffic collision",
        "accident (vehicle)",
        "tire squealing",
        "brakes squeal",
        "collision",
    ]),
]

# ── Explicit blocklist ──────────────────────────────────────────────────────
# Raw labels that must NEVER produce a canonical alert label.
# These are checked before any positive rule.
_BLOCKLIST_SUBSTRINGS: List[str] = [
    # Musical crash/collision sounds — not real events
    "crash cymbal",
    "hi-hat",
    "cymbal",
    "snare drum",
    "drum kit",
    "drum machine",
    "cap gun",            # toy — not a real firearm
    "balloon",            # pop sounds
    "fireworks (sparkler)",
    # Normal speech and music — high FP risk
    "music",
    "speech",
    "singing",
    "conversation",
    "narration",
    "talk radio",
    "laughter",
    "applause",
    "crowd noise",
    "background noise",
    "white noise",
    "pink noise",
    "static",
    # Animal sounds — not surveillance-relevant
    "dog",
    "cat",
    "bird",
    "animal",
    "insect",
    # Domestic non-alarm sounds
    "door bell",
    "telephone",
    "ringtone",
    "notification",
    "click",
    "keyboard",
    "typing",
]


def _is_blocked(raw_label: str) -> bool:
    """Return True if the raw label should never produce a canonical alert."""
    lower = raw_label.lower()
    return any(b in lower for b in _BLOCKLIST_SUBSTRINGS)


def map_audio_label(raw_label: str) -> Optional[str]:
    """
    Map a raw AudioSet label string to a canonical VigilZone product label.

    Returns:
        Canonical label string, or None if the label should not produce an alert.
    """
    if _is_blocked(raw_label):
        return None

    lower = raw_label.lower()
    for canonical, patterns in _RULES:
        if any(pattern in lower for pattern in patterns):
            return canonical

    return None   # no match → not a safety-relevant sound


def map_topk(
    raw_topk: List[Tuple[str, float]],
    min_score: float = 0.15,
) -> List[Dict]:
    """
    Map a list of (raw_label, score) top-k predictions to canonical label dicts.

    Args:
        raw_topk:  List of (raw_label, score) tuples ordered by descending score.
        min_score: Discard predictions below this score before mapping.

    Returns:
        List of dicts with keys: raw_label, canonical_label, score, rank.
        Entries where canonical_label is None are kept for debug visibility
        but will not produce alerts.
    """
    results = []
    for rank, (raw_label, score) in enumerate(raw_topk, start=1):
        if score < min_score:
            continue
        canonical = map_audio_label(raw_label)
        results.append({
            "raw_label": raw_label,
            "canonical_label": canonical,
            "score": round(float(score), 4),
            "rank": rank,
        })
    return results
