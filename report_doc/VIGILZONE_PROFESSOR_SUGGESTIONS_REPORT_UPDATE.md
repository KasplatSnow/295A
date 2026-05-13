# VigilZone Report Update for Professor Suggestions

**Purpose.** This addendum updates the previous IEEE-style report package so it explicitly satisfies the professor's suggestions: introduction with market/problem/solution, AI model details, data and test results, 5-10 minute demo plan with AI audio voice overlay, literature review, architecture diagram, and IEEE-style AI references.

---

## 1. Quick Compliance Check Against Professor Suggestions

| Professor suggestion | Was it present in the last iteration? | Required update in this addendum |
|---|---|---|
| Introduction: market, problem, solution | Partially. The abstract and contribution text described the problem and solution, but market research was not explicit enough. | Add a dedicated market-research paragraph and a clearer problem-solution-novelty introduction. |
| AI Model | Partially. BEATs, existing video lanes, fusion, normality, and uncertainty were described. | Add a concise model subsection that can be inserted after the introduction or in the architecture/model chapter. |
| Data/test results | Partially. Verification and benchmarks were described, but audio-video test-result tables were not explicit. | Add a Data, Test Methodology, and Results section with a fill-in table plus acceptance metrics. |
| Demo: 5-10 minutes, AI Audio Voice Overlay | Not explicitly present. | Add a timed demo script and voice overlay/narration plan. |
| Introduction: market research | Missing/too light. | Add market paragraph with current AI video surveillance market numbers and safety-monitoring motivation. |
| What problem is addressed and solution | Present, but should be front-loaded. | Add an insert-ready Introduction section. |
| Novelty | Present. | Strengthen and separate project novelty vs. model/fusion novelty. |
| Next paragraph: how paper is organized | Present in concept, not in the exact professor-requested style. | Add an insert-ready paper-organization paragraph. |
| Literature review of other works | Present in the previous package. | Keep it and add an explicit Table I research comparison note. |
| Architecture diagram | Present in previous package and available cloud diagram. | Add updated audio-visual cloud architecture diagram placement and caption. |
| IEEE standard for AI referencing | Partially. References existed, but not enough guidance. | Add IEEE AI citation rules and a cleaned reference starter list. |

---

## 2. Replacement / Insert-Ready Introduction

### 2.1 Market, Problem, Solution, Novelty, and Paper Organization

Artificial intelligence is rapidly changing how security and surveillance systems are designed. Recent market reports estimate the global AI-in-video-surveillance market in the multi-billion-dollar range, with one forecast reporting USD 6.51 billion in 2024 and USD 28.76 billion by 2030, corresponding to a 30.6% CAGR from 2025 to 2030. Other forecasts similarly show strong growth for cloud-based and AI-enabled surveillance because public agencies, private communities, and businesses increasingly need automated monitoring, fast incident awareness, and scalable evidence review. These trends show that the core challenge is no longer only whether a camera can record video; the practical challenge is whether a system can convert video, audio, model output, and operator feedback into reliable and actionable safety workflows.

The problem addressed by VigilZone is that many practical surveillance systems remain fragmented. A camera vendor may provide video preview, a model prototype may produce detections, and a dashboard may display alerts, but these pieces often do not share a durable incident model, tenant-aware authorization, evidence retention, notification state, or multimodal reasoning. Video-only detection also misses important context. A scream, glass break, siren, alarm, explosion-like sound, or off-camera crash may occur before or outside the visible scene, while video-only models can produce false positives from ordinary motion, shadows, crowds, or visually ambiguous activity. Audio-only detection has the opposite weakness: it can hear events that are not actually safety incidents in the monitored area. Therefore, a deployable surveillance platform requires not only individual AI models, but also a reliable architecture that combines audio, video, evidence, authorization, and notification delivery.

VigilZone addresses this problem through a real-time multi-tenant audio-visual anomaly detection and notification platform. The system combines a React operator dashboard, Django REST/ASGI control plane, PostgreSQL data store, Redis event and notification transport, MediaMTX/OpenCV/FFmpeg media handling, and a FastAPI AI service. The updated AI service extends the original video-first detection pipeline with BEATs-based audio event recognition and audio-video fusion. Cameras can operate in `video_only`, `audio_only`, or `audio_video` modes. Video lanes detect events such as persons, fire/smoke, weapons, falls, violence candidates, accidents, and zone-related signals, while the audio lane detects safety-relevant acoustic evidence such as screaming, glass break, alarms, sirens, crash-like sounds, and gunshot/explosion-like audio. A fusion layer correlates observations within a temporal window and emits explainable incident-level decisions rather than isolated frame or audio-chunk scores.

The novelty of the project is deployment-oriented multimodal incident intelligence. VigilZone does not claim to invent BEATs, YOLO, RT-DETR, CLIP, or audio-visual anomaly detection itself. Instead, its contribution is the integration of strong audio and video components into an operational platform that supports tenant isolation, incident persistence, evidence linking, notification consistency, camera configuration, modality-aware operation, and operator review. The model-layer novelty is the practical fusion design: audio confidence, video confidence, temporal alignment, normality profiles, uncertainty gating, and optional learned fusion shadow mode are combined to reduce false positives while preserving safe behavior when a camera lacks audio or a microphone is noisy.

The remainder of this report is organized as follows. Section II reviews related work in video anomaly detection, audio event recognition, and audio-visual fusion. Section III presents the VigilZone system architecture, including the frontend, backend control plane, AI/media service, data layer, and deployment topology. Section IV describes the audio-visual inference model, including BEATs audio inference, video detection lanes, temporal fusion, normality profiles, and uncertainty gating. Section V describes implementation details across the React frontend, Django backend, FastAPI AI service, Redis transport, PostgreSQL persistence, and notification layer. Section VI presents testing methodology and results for model inference, audio-video fusion, incident ingestion, evidence generation, notification delivery, and end-to-end demo workflows. Section VII discusses deployment, operational constraints, limitations, and future work toward learned fusion and AVadCLIP/MAVAD-style research extensions.

---

## 3. AI Model Section to Add

### 3.1 Implemented AI Model Pipeline

The implemented VigilZone AI model pipeline is a multi-lane, multimodal inference system rather than a single monolithic classifier. The video side uses the existing surveillance lanes for frame-based detection and candidate generation, including object/person detection, fire and smoke detection, weapon detection, fall candidate logic, violence candidate logic, accident detection, zone or intrusion-related logic, temporal verification, and identity-aware context where available. These lanes produce observations with labels, confidence scores, bounding boxes, timestamps, lane names, and diagnostic metadata.

The audio side uses FFmpeg-based audio extraction from camera streams or supported audio sources. Audio is converted into mono 16 kHz chunks and passed to a BEATs-based audio anomaly lane. BEATs is selected because it is a transformer-based audio representation model trained for broad audio event understanding, and published results report strong AudioSet and ESC-50 classification performance. The system maps BEATs or AudioSet-style labels into stable surveillance labels such as `audio_scream`, `audio_glass_break`, `audio_alarm`, `audio_siren`, `audio_vehicle_crash`, `audio_gunshot_like`, and `audio_explosion_like`. This mapping prevents raw model labels from directly becoming product incidents without domain-specific filtering.

The fusion side receives audio observations and video observations within a configurable time window. In Phase 1, deterministic temporal fusion remains the production decision path. It can increase confidence when audio and video agree, lower confidence when a modality is uncertain, create lower-confidence audio-only alerts for critical sounds, and preserve video-only behavior when audio is unavailable. In Phase 2, the system adds per-camera normality profiles and uncertainty-aware gating. Normality profiles prevent recurring background sounds such as traffic, wind, rain, or HVAC noise from repeatedly generating high-severity alerts, while protected critical labels such as screams, alarms, glass break, and gunshot/explosion-like events are not automatically learned away. A learned fusion head exists only as a shadow-mode or future-trainable component until enough labeled data is collected and validated.

### 3.2 Model Novelty Statement

The model contribution is not a new foundation model. The model contribution is an applied audio-visual incident model that transforms independent audio and video detections into explainable, temporal, operator-facing incident decisions. The novelty is the combination of (i) BEATs audio event inference, (ii) existing real-time video detection lanes, (iii) configurable modality modes, (iv) time-windowed fusion, (v) uncertainty-aware gating, (vi) per-camera normality adaptation, and (vii) incident/evidence/notification integration. This makes the system SOTA-aligned for deployment even though it does not claim benchmark SOTA on public anomaly-detection datasets.

---

## 4. Data, Test Methodology, and Results Section

### 4.1 Data Used for Testing

The final report should separate three types of data:

1. **Functional demo data:** local webcam, RTSP camera, uploaded video, or controlled sample clips used to verify camera ingest, lane scheduling, preview, evidence export, incident creation, and notification delivery.
2. **Audio verification data:** controlled audio clips or live microphone/camera audio used to test speech, clap/knock, siren/alarm-like sounds, glass-break-like sounds, crowd/background noise, fan/wind/traffic noise, and off-camera abnormal sounds.
3. **System test data:** synthetic `alert.created` events and controlled Redis/backend payloads used to verify idempotency, duplicate replay, tenant isolation, notification read state, and frontend hydration.

If public benchmark datasets are not used for final evaluation, the report should state that the evaluation is a system-integration and deployment verification, not a benchmark-SOTA model comparison. This is more credible than claiming broad model accuracy from a small project demo.

### 4.2 Required Test Result Table

Add the following table to the testing chapter and fill the measured values from the final run.

| Test ID | Scenario | Input Source | Expected Behavior | Metric / Evidence | Result |
|---|---|---|---|---|---|
| T1 | Video-only person/fire/weapon/fall lane sanity test | Webcam, RTSP camera, or sample video | Video lane emits expected observation without audio dependency | AI logs, detection payload, keyframe/clip | Fill after run |
| T2 | Audio-only event test | Camera mic or WAV/MP4 test source | Audio lane emits stable label, score, uncertainty, and optional WAV evidence | Audio observation JSON, WAV evidence path | Fill after run |
| T3 | Audio-video fusion confirmation | Person/motion event plus abnormal sound within fusion window | Fusion emits higher-confidence multimodal incident with reason | Fusion payload, incident detail, evidence links | Fill after run |
| T4 | Background-noise suppression | Fan/traffic/rain/crowd repeated for several minutes | Normality profile reduces non-critical repeated background score | normality profile JSON, adjusted score trend | Fill after run |
| T5 | Critical sound protection | Scream/alarm/glass-break-like sound repeated | Critical labels are not learned away by normality store | Adjusted score remains eligible for alert | Fill after run |
| T6 | Duplicate event replay | Same event ID replayed through Redis/webhook | One incident and one per-user alert only | IncidentEventReceipt count, Alert count | Fill after run |
| T7 | Notification latency | Confirmed AI event to browser alert | Browser receives or hydrates alert within target latency | T0-T5 latency log | Fill after run |
| T8 | Tenant isolation | Cross-tenant API/SSE/preview attempt | Request denied or no data returned | HTTP status/log screenshot | Fill after run |
| T9 | Evidence availability | Confirmed video/audio/fused incident | Incident links keyframe, video clip, and/or WAV evidence | Incident detail screenshot | Fill after run |
| T10 | Mode fallback | Disable audio or use video-only camera | Video-only mode still functions normally | AI service logs and frontend incident | Fill after run |

### 4.3 Recommended Results Wording

Use this wording until final numeric measurements are available:

> The evaluation focuses on end-to-end system correctness rather than public benchmark ranking. The test scenarios verify that audio and video streams are ingested, model observations are produced, fusion rules generate explainable incident decisions, incidents are persisted once, notification state remains consistent after refresh or replay, and evidence is available for operator review. Final measured values should include inference readiness, audio chunk latency, fusion-window latency, incident-to-notification latency, duplicate replay outcome, and frontend incident visibility.

---

## 5. Demo Plan: 5-10 Minutes with AI Audio Voice Overlay

### 5.1 Demo Objective

The demo should show that VigilZone is not only detecting events, but converting multimodal observations into an operator workflow: camera preview, audio/video inference, incident creation, evidence review, and notification delivery.

### 5.2 Timed Demo Script

| Time | Demo Step | What to Show | AI Audio Voice Overlay Script |
|---|---|---|---|
| 0:00-0:45 | Opening | Dashboard and one registered camera | "VigilZone is a real-time audio-visual anomaly detection and notification platform for community surveillance." |
| 0:45-1:30 | Problem and market | One slide or dashboard intro | "Traditional systems often separate camera preview, AI detection, evidence, and notifications. VigilZone combines them into one tenant-aware workflow." |
| 1:30-2:30 | Architecture | Updated cloud/audio-visual architecture diagram | "The browser talks to Django, Django owns trust and persistence, FastAPI runs AI and media processing, Redis transports incidents, and PostgreSQL stores durable truth." |
| 2:30-3:30 | Video-only inference | Person/fire/weapon/fall or sample video detection | "This is the video lane output. The system can still operate when a camera has no microphone." |
| 3:30-4:30 | Audio-only inference | Play safe test sound or show audio event payload | "The audio lane converts audio to 16 kHz chunks and uses BEATs to identify safety-relevant sound events." |
| 4:30-5:45 | Audio-video fusion | Trigger or replay fused incident | "The fusion layer correlates audio and video within a time window and creates an explainable incident instead of isolated model scores." |
| 5:45-6:45 | Notification | Notification bell, alert list, unread count | "The backend persists the incident, creates per-user alerts, and delivers a real-time notification while REST remains the recovery path." |
| 6:45-7:45 | Evidence review | Incident detail with keyframe/video/audio evidence | "Operators can inspect the evidence, model labels, confidence scores, and fusion reason before taking action." |
| 7:45-8:45 | Reliability | Duplicate replay or refresh demonstration | "A replayed event does not create duplicate incidents because the backend records event receipts and treats Redis delivery as at-least-once." |
| 8:45-10:00 | Summary | Novelty and future work | "The project contribution is a deployable multimodal surveillance workflow with a path toward learned fusion and AVadCLIP-style research extensions." |

### 5.3 How to Implement the Voice Overlay

Recommended low-risk approach:

1. Record screen using OBS, Zoom, or system screen recording.
2. Use prerecorded narration generated by a TTS tool or recorded by a team member.
3. Keep the voice overlay as narration, not as a system feature, unless the actual UI implements speech.
4. If the UI includes a demo-only voice assistant, label it clearly as **demo narration overlay** or **operator explanation overlay**.

Do not claim the production system includes an autonomous voice assistant unless implemented. The safe wording is:

> The demo video includes an AI audio voice overlay for explanation and accessibility. It narrates the architecture and alert workflow but is separate from the surveillance inference pipeline.

---

## 6. Literature Review and Table I Placement

The last report package already included a research comparison table. It should be placed in the report immediately after the related-work overview, similar to the style of the target UAV survey paper. The table should compare work by objective, method, modality/domain, anomaly area, measurement criteria, and VigilZone fit/gap.

Recommended caption:

> **Table I. Representative Anomaly Detection and Audio-Visual Surveillance Research Work**

Recommended placement:

- IEEE paper version: Section II, after related-work paragraphs.
- MS project report version: Chapter 1 after Current State of the Art, or Chapter 2 before architecture.

---

## 7. Architecture Diagram Update

Use the updated cloud deployment diagram as the main architecture figure and update the caption to mention audio inference.

Recommended caption:

> **Fig. 1. Updated VigilZone cloud deployment and audio-visual runtime architecture.** The architecture separates users and cameras, security/access control, media ingestion, the Django control plane, FastAPI AI and vision/audio inference, data and messaging services, observability, and external notification integrations. Audio inference is introduced in the AI/media plane through FFmpeg audio extraction, BEATs-based audio event recognition, and audio-video fusion before durable incident and notification delivery.

Recommended callout paragraph:

> The diagram reflects the updated audio-visual system boundary. Cameras and edge devices provide RTSP/RTMP or related streams to the media plane. Video frames and optional audio chunks are processed by the AI service. Model observations are converted into incidents through the backend control plane rather than being delivered directly to users. This preserves tenant authorization, durable persistence, notification consistency, and evidence review.

---

## 8. IEEE Standard for AI Referencing

Use IEEE numeric citations in the order works appear. The final References section should not mix APA-style author-year with IEEE numbering. For papers, use: author initials, title in quotation marks, venue, year, pages if available, and DOI or URL if applicable. For software and online documentation, use organization/maintainer, title, version or access date, and URL.

### 8.1 Starter IEEE Reference List for AI/Anomaly Portion

[1] W. Sultani, C. Chen, and M. Shah, "Real-world anomaly detection in surveillance videos," in *Proc. IEEE Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2018, pp. 6479-6488.

[2] P. Wu, J. Liu, Y. Shi, Y. Sun, F. Shao, Z. Wu, and Z. Yang, "Not only look, but also listen: Learning multimodal violence detection under weak supervision," in *Proc. European Conf. Comput. Vis. (ECCV)*, 2020.

[3] S. Chen, Y. Wu, C. Wang, S. Liu, D. Tompkins, Z. Chen, and F. Wei, "BEATs: Audio pre-training with acoustic tokenizers," in *Proc. 40th Int. Conf. Machine Learning (ICML)*, 2023.

[4] B. Leporowski, A. Bakhtiarnia, N. Bonnici, A. Muscat, L. Zanella, Y. Wang, and A. Iosifidis, "Audio-visual dataset and method for anomaly detection in traffic videos," arXiv:2305.15084, 2023.

[5] P. Wu, W. Su, G. Pang, Y. Sun, Q. Yan, P. Wang, and Y. Zhang, "AVadCLIP: Audio-visual collaboration for robust video anomaly detection," arXiv:2504.04495, 2025.

[6] J. Redmon, S. Divvala, R. Girshick, and A. Farhadi, "You only look once: Unified, real-time object detection," in *Proc. IEEE Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2016, pp. 779-788.

[7] Microsoft Research, "BEATs: Audio pre-training with acoustic tokenizers official PyTorch implementation," GitHub repository. [Online]. Available: https://github.com/microsoft/unilm/tree/master/beats

[8] Grand View Research, "AI in video surveillance market size, share and trends analysis report," 2025. [Online]. Available: https://www.grandviewresearch.com/industry-analysis/artificial-intelligence-ai-video-surveillance-market-report

[9] MarketsandMarkets, "AI in video surveillance market by offering, deployment, technology - global forecast to 2030," 2024. [Online]. Available: https://www.researchandmarkets.com/report/ai-in-video-surveillance

[10] KasplatSnow, "295A: Smart community surveillance cloud-based," GitHub repository. [Online]. Available: https://github.com/KasplatSnow/295A

### 8.2 Citation Rules to Follow

- Cite BEATs when describing the audio model backbone.
- Cite UCF-Crime and XD-Violence when motivating video and audio-visual anomaly detection literature.
- Cite MAVAD/AVACA and AVadCLIP when motivating audio-video fusion and future SOTA-aligned extensions.
- Cite YOLO/RT-DETR-related sources when describing object-detection foundations.
- Cite market reports only in the introduction/market motivation, not as technical evidence.
- Cite the project repository or implementation appendix when describing implemented system components.

---

## 9. Updated Checklist for Final Report Merge

Before final submission, confirm the main report has all of the following:

- [ ] Abstract mentions audio-visual anomaly detection, BEATs, fusion, incident workflow, and notifications.
- [ ] Introduction includes market, problem, solution, novelty, and paper organization.
- [ ] Literature review includes a Table I comparing anomaly-detection works.
- [ ] Architecture section includes updated audio-visual architecture diagram and caption.
- [ ] AI model section explains video lanes, BEATs audio lane, fusion, normality, uncertainty, and shadow learned fusion.
- [ ] Testing section includes audio-only, video-only, audio-video, notification, evidence, tenant-isolation, and duplicate-replay results.
- [ ] Demo section or appendix includes the 5-10 minute walkthrough and voice overlay plan.
- [ ] References are converted to IEEE numeric style.
- [ ] Claims avoid saying benchmark SOTA unless public benchmark testing was actually performed.
- [ ] Final limitations clearly state any model weights, datasets, hardware, or camera/audio constraints.
