You are the architect + senior computer vision engineer. Improve the AI module’s incident detection quality across ALL incident classes: intrusion, loitering, fall, violence, fire/smoke, weapon, optional accident, and unknown anomaly. Keep identity subsystem as-is (already working). No training.

1) Introduce a unified Incident Framework (mandatory)

Create src/incidents/ with:

base.py: IncidentDefinition (candidate sources, confirm policy, severity policy, suppression policy)

state.py: per-camera/per-track state machines with persistence counters

registry.py: registers incidents and their rules

All incidents must go through:
candidate → persistence → confirm (optional) → emit → cooldown

Add “reason codes” to every alert:

why fired

why suppressed

what confirmed it

2) Intrusion + Loitering

Intrusion = person enters restricted zone (boundary crossing).

Loitering = dwell time > threshold using track_id presence.(might "https://www.mdpi.com/2224-2708/12/1/9?utm_source=chatgpt.com" help)
Add config:

intrusion.enter_grace_s

loitering.threshold_s

loitering.escalate_unknown_only

Identity policy must adjust severity.

3) Fall: replace bbox heuristic with Pose-based fall candidate

Add YOLOv8 Pose lane (use Ultralytics pose weights).

Candidate logic:

torso angle + hip drop + lying persistence + post-fall stillness. ("https://github.com/haashi-r/Real-Time-Fall-Detection?utm_source=chatgpt.com" might help)

Verifier (X3D/VideoSwin) is confirmation only.

If verifier unavailable: require strong stillness+lying persistence to emit; else suppress.

4) Violence: person-centric candidate + 3D verifier

Candidate should require:

≥2 persons in proximity + high local motion around those bboxes

Extract person-centric clip around the interacting tracks.

Use X3D/SlowFast style 16-frame classifier as verifier pattern. (might "https://github.com/ShwetaNagapure/RWF-2000-X3D-Violence-Detection?utm_source=chatgpt.com" help)

If verifier unavailable: do NOT emit SEVERE; emit MED only if persistence is very strong.

5) Fire/Smoke: dedicated YOLO + quality gates + deterministic weights

Implement deterministic weights support:

Option A: git-clone luminous0219 repo and use weights/best.pt (fire/smoke). (might "https://github.com/luminous0219/fire-and-smoke-detection-yolov8/tree/main/weights" help)

Keep class-name mapping mandatory; expose model.names in diagnostics.

Gates:

bbox area/ratio

persistence (4/8)

optional flicker/texture confirm before SEVERE

6) Weapon: dedicated YOLO + proximity gating + deterministic weights

Default weights source: HF weapon repo with All_weapon.pt. ("https://huggingface.co/Shantanukadam/weapon_detection" might help)

Require weapon bbox to be near a person bbox (or near hands if pose is on).

Require persistence 3/5.

Severity HIGH/SEVERE only if unknown person or restricted zone.

7) Accident (optional)

Only enable “accident/crash” lane if camera is flagged as “traffic”.

For traffic mode:

allow YOLO crash models / traffic anomaly datasets (DoTA, TU-DAT) as future integration. (some helpful resource would be "https://github.com/MoonBlvd/Detection-of-Traffic-Anomaly?utm_source=chatgpt.com")

Otherwise keep accident off and rely on fall + unknown anomaly.

8) Unknown anomaly: replace motion-only spam with suppression + persistence

Unknown anomaly must be MED by default.

Suppress if explained by:

known person/pet presence

periodic motion

global illumination change

outside sensitive zones

Persistence: 4/8 + minimum interval between alerts.

9) Diagnostics + UI (mandatory)

Add /system/diagnostics to show:

which incidents are enabled

whether verifiers are available

per-incident suppression counters
UI must show:

incident type

severity

entity (already)

reason codes (why fired / why suppressed)

Deliverables:

Root-cause notes for current FP patterns.

Patch plan (file-by-file).

Implementation.