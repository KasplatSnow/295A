---
FORMAT DIRECTIVE: IEEE Two-Column Project Report | 12pt Times New Roman | Single/1.15 spacing | Justified text | Two-column body layout starting after abstract
---

# VigilZone: A Real-Time Multi-Tenant Audio-Visual Anomaly Detection and Notification Platform

**CMPE 295B Master's Project Final Report**

---

## Abstract

The global AI-in-video-surveillance market is projected to grow from USD 6.51 billion in 2024 to USD 28.76 billion by 2030, representing a 30.6% CAGR driven by demand for automated threat detection, scalable evidence management, and real-time incident awareness across public safety agencies, gated communities, and commercial facilities [1][2]. Despite rapid adoption of camera infrastructure, conventional video-only surveillance systems remain susceptible to critical blind spots: events occurring outside camera fields of view, acoustically significant incidents such as screams or glass breakage that precede or accompany visual anomalies, and elevated false positive rates arising from shadows, crowds, weather artifacts, and visually ambiguous motion patterns. Audio-only detection addresses some of these limitations but introduces complementary weaknesses, including sensitivity to ambient noise and inability to localize events spatially. VigilZone addresses these shortcomings through an integrated, real-time, multi-tenant audio-visual anomaly detection and notification platform. The system architecture combines a React operator dashboard, Django REST/ASGI control plane with PostgreSQL persistence, Redis Streams event transport, MediaMTX/OpenCV/FFmpeg media handling, and a FastAPI AI service implementing multi-lane video detection, BEATs-based audio event recognition [3], configurable temporal audio-video fusion with normality profiling and uncertainty gating, automated evidence capture, and real-time operator notification. The contribution is a deployment-oriented multimodal anomaly detection and incident management workflow — integrating pretrained audio understanding, real-time video inference, temporal fusion, evidence linking, tenant-scoped persistence, and notification delivery into one operational platform — rather than the invention of a novel state-of-the-art model architecture. The implementation is SOTA-aligned rather than SOTA-claimed.

**Keywords:** audio-visual anomaly detection, BEATs, multimodal fusion, real-time surveillance, YOLO, incident notification, evidence capture, multi-tenant security, normality profiling, uncertainty gating, Django, FastAPI, Redis Streams, PostgreSQL

---

## Chapter 1. Introduction

### 1.1 Market Context and Motivation

Artificial intelligence is fundamentally transforming the design, deployment, and operational economics of security and surveillance systems worldwide. The convergence of affordable high-resolution cameras, edge computing hardware, cloud-native infrastructure, and increasingly capable deep learning models has created conditions for intelligent surveillance platforms that go far beyond passive video recording. Grand View Research estimates the global AI-in-video-surveillance market at USD 6.51 billion in 2024, with projections reaching USD 28.76 billion by 2030 at a compound annual growth rate of 30.6% [1]. MarketsandMarkets reports similar trajectories, emphasizing growth in cloud-based deployments and AI-enabled analytics for public safety, transportation, retail, and critical infrastructure sectors [2]. These projections reflect increasing demand from municipal governments deploying smart city initiatives, residential communities seeking automated perimeter monitoring, commercial enterprises requiring loss prevention and workplace safety compliance, and critical infrastructure operators mandating continuous situational awareness.

The market trajectory indicates that the fundamental challenge has evolved beyond hardware deployment. Organizations can install cameras at scale; the practical challenge is whether the overall system can convert raw video streams, audio signals, model inferences, environmental context, and operator feedback into reliable, actionable, and auditable safety workflows that operate continuously without overwhelming human operators with false alarms or missing genuine threats.

### 1.2 Problem Statement

Many practical surveillance deployments remain architecturally fragmented. A camera vendor provides video streaming and basic motion detection. A separate AI model prototype produces object detections or anomaly scores. A third-party dashboard displays alerts. A notification service sends emails or push messages. However, these components typically do not share a unified incident model, tenant-aware authorization framework, durable evidence retention policy, consistent notification state, or multimodal reasoning capability. This fragmentation produces several concrete failure modes:

Video-only detection systems miss critical contextual information. A scream, glass break, gunshot, siren, explosion-like sound, or vehicle crash may occur before visual evidence appears, outside the camera's field of view, or in conditions (darkness, occlusion, distance) where visual models cannot reliably classify the event. Conversely, video-only models frequently produce false positives from ordinary motion patterns, moving shadows, crowd density changes, weather artifacts (rain, fog, snow), lighting transitions, and visually ambiguous activities such as running, horseplay, or object drops that are not safety-relevant.

Audio-only detection systems exhibit complementary weaknesses. Ambient noise from traffic, construction, HVAC equipment, weather, crowds, and music can mask genuine acoustic anomalies or generate false triggers. Audio alone cannot spatially localize events, distinguish between a safety-relevant sound and a benign source (e.g., a car backfire versus a gunshot, a child's playful scream versus a distress call), or provide the visual evidence operators need for situational assessment and response decisions.

Therefore, a deployable surveillance platform requires not merely individual AI models achieving benchmark performance in isolation, but a reliable end-to-end architecture that fuses audio and video modalities, manages evidence lifecycle, enforces multi-tenant authorization, delivers consistent notifications, and supports operator review workflows — all while maintaining acceptable false positive rates and detection latency under real-world operating conditions.

### 1.3 Solution

VigilZone addresses this problem through a real-time multi-tenant audio-visual anomaly detection and notification platform designed for community surveillance deployments. The system architecture integrates the following components into a cohesive operational workflow:

A **React operator dashboard** provides real-time camera preview, incident management, evidence review, notification consumption, and system configuration through an authenticated single-page application. A **Django REST/ASGI control plane** enforces tenant authorization, manages camera lifecycle and configuration, persists incidents and evidence metadata to PostgreSQL, maintains per-user notification state, and serves as the authoritative trust boundary between operators and backend services. A **FastAPI AI service** performs all inference computation including video frame capture via OpenCV, audio extraction via FFmpeg, multi-lane video detection (person/fire/smoke/weapon/fall/violence/accident/zone), BEATs-based audio event recognition [3], configurable temporal audio-video fusion, per-camera normality profiling, uncertainty-aware gating, evidence generation (keyframes, video clips, audio WAV segments), and incident publication to Redis Streams. **Redis Streams** provides the event transport layer supporting at-least-once delivery semantics for incident events and real-time notification push to connected operator sessions. **PostgreSQL** serves as the durable data store with full ACID guarantees for tenant isolation, incident uniqueness, and audit trail integrity. **MediaMTX** provides RTSP/RTMP relay and transcoding capabilities for camera stream management.

Cameras operate in configurable modality modes: `video_only`, `audio_only`, or `audio_video`. Video lanes detect visual events including persons in restricted zones, fire/smoke, weapons, falls, violence candidates, vehicle accidents, and configurable zone intrusions. The audio lane detects safety-relevant acoustic events including screaming, glass breakage, alarms, sirens, crash-like impacts, and gunshot/explosion-like sounds. A temporal fusion layer correlates audio and video observations within a configurable time window and emits explainable incident-level decisions with multimodal reasoning rather than isolated frame-level scores or raw audio-chunk classifications.

### 1.4 Novelty and Contribution

The novelty of VigilZone is deployment-oriented multimodal incident intelligence. The project does not claim to invent BEATs [3], YOLO [4], RT-DETR [5], CLIP [6], or the concept of audio-visual anomaly detection. The contribution is the systematic integration of strong pretrained audio and video components into an operational platform that simultaneously addresses:

- **Multi-tenant isolation** with per-tenant data partitioning, RBAC enforcement, and cross-tenant access prevention
- **Incident persistence** with unique event receipt tracking, idempotent creation, and lifecycle state management
- **Evidence linking** associating keyframe images, video clips, and audio WAV segments with specific incidents
- **Notification consistency** with per-user alert creation, read/unread state tracking, real-time push, and REST-based recovery
- **Camera configuration** with per-camera modality mode selection and parameter tuning
- **Modality-aware operation** with graceful degradation when audio hardware is unavailable
- **Operator review** with incident detail, evidence inspection, confidence scores, and fusion reasoning

The model-layer novelty is the practical fusion design: audio confidence, video confidence, temporal alignment, per-camera normality profiles, uncertainty gating, and optional learned fusion shadow mode are combined to reduce false positives while preserving safe detection behavior across diverse deployment conditions. The novelty is not the invention of BEATs or a new object detector. The novelty is the integration of pretrained audio understanding, real-time video detection, temporal audio-video fusion, evidence management, tenant-scoped backend persistence, and real-time notification delivery into one deployable surveillance workflow.

### 1.5 Report Organization

The remainder of this report is organized as follows. Chapter 2 presents a comprehensive literature review covering video anomaly detection, audio event recognition, audio-visual multimodal fusion, real-time surveillance system architectures, and identifies research gaps addressed by VigilZone. Chapter 3 describes the updated system architecture in detail, including component responsibilities, API design, database schema, message formats, deployment topology, security model, and scaling considerations. Chapter 4 provides a thorough treatment of the audio-visual inference model, including BEATs audio architecture, video detection lane pipeline, temporal fusion algorithms with mathematical formulations, normality profiling with exponential moving average adaptation, uncertainty gating thresholds, and learned fusion shadow mode design. Chapter 5 presents implementation specifics across all system layers. Chapter 6 describes testing methodology, test environment, comprehensive test scenarios, performance benchmarks, acceptance criteria, and evaluation results. Chapter 7 presents the demonstration plan with timed script and AI audio voice overlay. Chapter 8 discusses novelty decomposition, limitations, and future work.

---

## Chapter 2. Literature Review and Related Work

### 2.1 Video Anomaly Detection

Video anomaly detection has evolved significantly over the past decade, progressing from handcrafted feature representations to deep learning approaches capable of identifying unusual events in surveillance footage without exhaustive labeling of anomaly types.

Sultani, Chen, and Shah [7] introduced the foundational weakly supervised framework for real-world anomaly detection in surveillance videos, establishing the UCF-Crime benchmark with 13 anomaly categories across 1,900 untrimmed videos. Their multiple-instance learning (MIL) approach demonstrated that anomaly detection models could be trained with only video-level labels (normal vs. anomalous) rather than requiring expensive frame-level temporal annotations. This work established the paradigm that subsequent research has built upon, showing 75.41% frame-level AUC on UCF-Crime.

Zhong et al. [8] proposed a graph convolutional network approach for anomaly detection that models temporal relationships between video segments, achieving improved performance by capturing long-range dependencies that frame-level or short-clip methods miss. Their action-aware feature learning demonstrated that incorporating action recognition features into anomaly scoring improves discrimination between anomalous and normal activities.

Tian et al. [9] introduced a robust temporal feature magnitude learning approach for weakly supervised video anomaly detection, addressing the noise inherent in MIL-based training by learning discriminative feature magnitudes rather than relying solely on classification scores. This approach achieved state-of-the-art results on both UCF-Crime (82.12% AUC) and ShanghaiTech datasets.

Liu et al. [10] developed a future frame prediction framework using generative adversarial networks, where anomalies are detected as frames that deviate significantly from predicted normal patterns. This unsupervised approach eliminates the need for anomaly labels entirely but requires careful threshold calibration and can struggle with novel normal patterns.

Wu et al. [11] proposed the XD-Violence dataset and method specifically targeting violent event detection across diverse scenarios including movies, sports, surveillance footage, and car accidents. Their work emphasized the importance of multi-scene evaluation and introduced audio-augmented features for violence detection, providing early evidence that multimodal approaches outperform video-only methods for this task.

### 2.2 Audio Event Detection and Classification

Audio event detection has advanced substantially through the development of large-scale audio datasets and self-supervised pretraining methodologies that produce transferable audio representations.

Gemmeke et al. [12] created AudioSet, a large-scale ontology of 632 audio event classes with over 2 million human-labeled 10-second clips drawn from YouTube videos. AudioSet established the standard evaluation framework for audio event classification and enabled pretraining of general-purpose audio models. The scale and diversity of AudioSet made it possible to train audio models with broad coverage of environmental, speech, music, and mechanical sound events relevant to surveillance applications.

Hershey et al. [13] developed VGGish, a deep convolutional neural network for audio classification trained on AudioSet that produces 128-dimensional audio embeddings. VGGish operates on log mel spectrogram inputs and provides general-purpose audio features that transfer effectively to downstream classification tasks. While effective, VGGish is limited by its convolutional architecture which processes fixed-length audio segments without capturing long-range temporal dependencies.

Kong et al. [14] introduced PANNs (Pretrained Audio Neural Networks), a family of CNN-based models trained on the full AudioSet achieving 0.439 mAP, demonstrating that larger and deeper architectures with careful training protocols significantly improve audio classification performance. PANNs established strong baselines for audio tagging that subsequent transformer-based methods aim to surpass.

Chen et al. [3] introduced BEATs (Bidirectional Encoder representation from Audio Transformers), an iterative audio pretraining framework using acoustic tokenizers. BEATs achieves 98.1% accuracy on ESC-50 and 0.507 mAP on AudioSet-2M, representing significant improvements over prior CNN-based approaches. The architecture uses a transformer encoder operating on audio patch embeddings with iterative self-supervised pretraining through an acoustic tokenizer that learns discrete audio tokens. BEATs is selected as the VigilZone audio backbone because it provides the strongest available pretrained audio representations with efficient fine-tuning characteristics for domain-specific event detection.

Koutini et al. [15] developed PaSST (Patchout faSt Spectrogram Transformer), achieving competitive AudioSet performance through efficient spectrogram patch processing with structured patchout regularization. This work demonstrated that vision transformer architectures adapted for spectrograms can match or exceed specialized audio architectures.

### 2.3 Audio-Visual Multimodal Anomaly Detection

The combination of audio and visual modalities for anomaly detection represents a growing research direction motivated by the complementary nature of these sensing modalities.

Wu et al. [11] provided early evidence for multimodal violence detection, showing that concatenating audio features with visual features under a weakly supervised MIL framework improves detection accuracy on the XD-Violence dataset compared to visual-only approaches. Their work demonstrated 78.64% AP with audio-visual features versus 73.20% with visual features alone.

Leporowski et al. [16] introduced MAVAD (Multimodal Audio-Visual Anomaly Detection), an audio-visual dataset and method for anomaly detection specifically in traffic videos. Their work established evaluation protocols for multimodal surveillance in transportation contexts, demonstrating that audio cues from crashes, tire screeches, and horns provide significant complementary information to visual anomaly indicators.

Wu et al. [17] proposed AVadCLIP, an audio-visual collaboration framework for robust video anomaly detection that leverages CLIP-based cross-modal representations for alignment between audio and visual features. AVadCLIP demonstrates that pretrained vision-language models can be adapted for audio-visual anomaly detection through careful cross-modal projection and temporal aggregation, achieving improved performance on XD-Violence.

Zhou et al. [18] investigated temporal synchronization strategies for audio-visual event detection, demonstrating that explicit modeling of audio-visual temporal alignment improves detection of events where audio and visual signals have variable temporal offsets. This work is relevant to VigilZone's fusion window design which must handle non-simultaneous audio and visual evidence of the same underlying event.

### 2.4 Real-Time Surveillance System Architectures

Operational surveillance systems require consideration of software architecture, stream processing, latency constraints, and deployment scalability beyond model accuracy.

Redmon et al. [4] introduced YOLO (You Only Look Once), establishing the paradigm of single-pass real-time object detection that enables per-frame inference at video rates. Subsequent YOLO versions [19] have progressively improved accuracy while maintaining real-time performance, making YOLO-family detectors the predominant choice for surveillance video processing where latency is critical.

Lv et al. [5] proposed RT-DETR (Real-Time Detection Transformer), combining the accuracy advantages of transformer-based detection with real-time inference speed through efficient hybrid encoder design. RT-DETR achieves competitive accuracy with YOLO variants while leveraging attention mechanisms for improved detection of small and occluded objects.

Kafka and related distributed streaming platforms [20] have established architectural patterns for real-time event processing in surveillance contexts. Redis Streams provides similar capabilities with lower operational complexity for deployments that do not require the full distributed consensus guarantees of Kafka, making it suitable for single-site or moderate-scale surveillance platforms.

### 2.5 Multi-Tenant Cloud and Edge-Cloud Architectures

Multi-tenant system design for surveillance platforms must address tenant data isolation, shared infrastructure efficiency, and authorization enforcement across all system layers.

Django REST Framework combined with PostgreSQL row-level security provides a well-established pattern for multi-tenant SaaS architectures [21]. Tenant-scoped queries, middleware-based tenant context injection, and foreign key constraints ensure data isolation at the application and database levels simultaneously.

Edge-cloud inference architectures split computation between edge devices (performing lightweight preprocessing, frame selection, and optional model inference) and cloud services (performing complex multi-model pipelines, fusion, persistence, and notification). VigilZone currently implements a cloud-centric architecture where all inference occurs in the FastAPI AI service, with a roadmap toward edge-assisted frame filtering to reduce bandwidth and cloud compute requirements.

### 2.6 Gap Analysis

Table 2.1 summarizes the research landscape and identifies the specific gap addressed by VigilZone.

**Table 2.1. Research Gap Analysis: Related Work vs. VigilZone**

| Capability | UCF-Crime [7] | XD-Violence [11] | BEATs [3] | MAVAD [16] | AVadCLIP [17] | YOLO [4] | VigilZone |
|---|---|---|---|---|---|---|---|
| Video anomaly detection | Yes | Yes | No | Yes | Yes | Detection only | Yes |
| Audio event detection | No | Partial | Yes | Yes | Yes | No | Yes |
| Temporal audio-video fusion | No | Feature concat | No | Late fusion | CLIP alignment | No | Configurable temporal fusion |
| Normality adaptation | No | No | No | No | No | No | Yes (per-camera EMA) |
| Uncertainty gating | No | No | No | No | No | No | Yes |
| Multi-tenant platform | No | No | No | No | No | No | Yes |
| Incident persistence | No | No | No | No | No | No | Yes (PostgreSQL) |
| Evidence management | No | No | No | No | No | No | Yes (keyframe/clip/WAV) |
| Notification delivery | No | No | No | No | No | No | Yes (real-time + REST) |
| Operator review workflow | No | No | No | No | No | No | Yes |
| Deployment-ready platform | No | No | No | No | No | Partial | Yes |

The gap analysis reveals that while individual components of VigilZone's functionality exist in prior research (video anomaly detection, audio classification, multimodal fusion), no existing system integrates all of these capabilities with the operational requirements of a deployable multi-tenant surveillance platform including tenant isolation, incident lifecycle management, evidence retention, and notification delivery.

---

## Chapter 3. System Architecture

### 3.1 Architecture Overview

Figure 3.1 presents the updated VigilZone cloud deployment architecture incorporating audio-video inference, multimodal fusion, backend incident persistence, and real-time notification delivery.

[FIGURE PLACEHOLDER: Updated VigilZone Audio-Video Cloud Deployment Architecture]

*Figure 3.1. Updated VigilZone cloud deployment architecture with audio-video inference, multimodal fusion, backend incident persistence, and real-time notification delivery. The architecture separates user-facing services, security/access control, media ingestion, AI inference, data persistence, event transport, and external notification integrations.*

The architecture follows a layered design with strict separation of concerns:

1. **Presentation Layer:** React SPA communicating exclusively through authenticated API calls
2. **Control Plane:** Django REST/ASGI handling authorization, business logic, and persistence
3. **Inference Plane:** FastAPI service handling all AI computation and media processing
4. **Transport Layer:** Redis Streams for event delivery and real-time notification push
5. **Persistence Layer:** PostgreSQL for durable state, S3-compatible storage for media evidence
6. **Media Layer:** MediaMTX for stream relay, OpenCV for frame capture, FFmpeg for audio extraction

### 3.2 Component Architecture and Responsibilities

#### 3.2.1 React Operator Dashboard

The React dashboard implements a single-page application providing:

- **Camera management:** Registration, configuration (modality mode, detection parameters, stream URLs), status monitoring, and live preview through authenticated HLS/MJPEG endpoints
- **Incident management:** Real-time incident list with filtering by severity, type, camera, and time range; incident detail view with evidence inspection
- **Notification interface:** Bell icon with unread count badge, notification dropdown with alert summaries, mark-as-read functionality, and notification preferences
- **Evidence viewer:** Keyframe image display, video clip playback, audio WAV playback, and multi-evidence incident views showing correlated audio-visual evidence
- **System configuration:** Tenant settings, user management, camera groups, and detection parameter tuning

The dashboard communicates with the Django backend through REST API calls authenticated via JWT tokens and receives real-time updates through Server-Sent Events (SSE) or WebSocket connections for notification push.

#### 3.2.2 Django Control Plane

The Django control plane serves as the authoritative trust boundary and implements:

- **Authentication/Authorization:** JWT-based authentication with refresh token rotation, role-based access control (RBAC) with tenant-scoped permissions (admin, operator, viewer), and middleware-enforced tenant context injection
- **Camera lifecycle:** CRUD operations for camera registration, configuration persistence, stream credential management, and health monitoring
- **Incident management:** Incident creation from AI service events with idempotent receipt tracking, lifecycle state transitions (new → acknowledged → investigating → resolved → closed), and audit logging
- **Evidence metadata:** Evidence record creation linking media artifacts to incidents, storage URL management, and retention policy enforcement
- **Notification state:** Per-user alert creation from incidents, read/unread tracking, delivery confirmation, and notification preference enforcement
- **Event consumption:** Redis Stream consumer group processing `alert.created` events from the AI service with exactly-once processing semantics through receipt deduplication

#### 3.2.3 FastAPI AI Service

The FastAPI AI service handles all inference and media processing:

- **Stream management:** Camera stream connection lifecycle, reconnection with exponential backoff, and health reporting
- **Video capture:** OpenCV VideoCapture with configurable frame sampling rate (default 2-5 FPS for inference), frame buffering, and resolution normalization
- **Audio extraction:** FFmpeg subprocess-based audio extraction from RTSP/RTMP streams, conversion to mono 16 kHz PCM, chunking into configurable windows (default 2-second chunks with 0.5-second overlap)
- **Video inference:** Multi-lane detection pipeline executing object detection, fire/smoke classification, weapon detection, fall estimation, violence candidate scoring, accident detection, and zone intrusion logic
- **Audio inference:** BEATs-based audio event classification with domain-specific label mapping and confidence scoring
- **Temporal fusion:** Configurable-window correlation of audio and video observations with fusion scoring and reason generation
- **Normality profiling:** Per-camera background pattern tracking with EMA-based adaptation
- **Uncertainty gating:** Threshold-based alert eligibility determination with critical label protection
- **Evidence generation:** Keyframe extraction, video clip capture (configurable pre/post-event buffer), and audio WAV segment export
- **Event publication:** Redis Stream publishing of confirmed incidents with full observation metadata

#### 3.2.4 Redis Streams Transport

Redis Streams provides the event transport layer with the following characteristics:

- **At-least-once delivery:** Consumer groups with acknowledgment ensure events are processed even if a consumer crashes mid-processing
- **Ordered delivery:** Events within a stream maintain insertion order, enabling temporal reasoning in consumers
- **Consumer groups:** Django consumers process events cooperatively, enabling horizontal scaling of event processing
- **Message retention:** Configurable retention with MAXLEN trimming to bound memory usage while preserving recent event history

**Message schema for `alert.created` events:**

```json
{
  "event_id": "uuid-v4",
  "event_type": "alert.created",
  "timestamp": "ISO-8601",
  "tenant_id": "uuid-v4",
  "camera_id": "uuid-v4",
  "severity": "critical|high|medium|low",
  "modality": "video_only|audio_only|audio_video",
  "fusion_reason": "string describing multimodal correlation",
  "observations": [
    {
      "lane": "person_detection|audio_scream|...",
      "label": "string",
      "confidence": 0.0-1.0,
      "adjusted_confidence": 0.0-1.0,
      "timestamp": "ISO-8601",
      "metadata": {}
    }
  ],
  "evidence": {
    "keyframe_url": "string|null",
    "video_clip_url": "string|null",
    "audio_wav_url": "string|null"
  }
}
```

#### 3.2.5 PostgreSQL Data Model

The PostgreSQL schema enforces tenant isolation through foreign key relationships and application-level query scoping:

**Table 3.1. Core Database Schema**

| Table | Key Columns | Purpose |
|---|---|---|
| tenants | id, name, settings, created_at | Tenant isolation root |
| users | id, tenant_id (FK), email, role, preferences | Tenant-scoped user accounts |
| cameras | id, tenant_id (FK), name, stream_url, modality_mode, detection_config, status | Camera registration and config |
| incidents | id, tenant_id (FK), camera_id (FK), event_id (unique), severity, modality, fusion_reason, status, created_at | Incident records with idempotent creation |
| incident_observations | id, incident_id (FK), lane, label, confidence, adjusted_confidence, timestamp, metadata | Per-observation detail |
| evidence | id, incident_id (FK), type (keyframe/clip/wav), storage_url, size_bytes, created_at | Evidence artifact metadata |
| alerts | id, incident_id (FK), user_id (FK), read, delivered_at, read_at | Per-user notification state |
| incident_event_receipts | id, event_id (unique), processed_at | Idempotent event processing |

### 3.3 Request Lifecycle: Camera Event to Operator Notification

The complete lifecycle of an anomaly detection event proceeds through the following stages:

1. **Stream ingestion:** FastAPI AI service captures video frames (OpenCV) and audio chunks (FFmpeg) from the camera's RTSP/RTMP stream
2. **Parallel inference:** Video frames are processed through detection lanes; audio chunks are processed through BEATs classification (parallel execution)
3. **Observation generation:** Each lane produces typed observations with labels, confidence scores, timestamps, and metadata
4. **Temporal fusion:** Observations within the fusion window are correlated; fusion scoring produces adjusted confidence and multimodal reasoning
5. **Normality check:** Per-camera normality profiles adjust non-critical observation confidence based on historical patterns
6. **Uncertainty gating:** Adjusted observations are evaluated against gating thresholds; only alert-eligible observations proceed
7. **Evidence capture:** For alert-eligible incidents, keyframes, video clips, and audio WAV segments are captured and stored
8. **Event publication:** Confirmed incident with observations and evidence URLs is published to Redis Streams
9. **Event consumption:** Django consumer group receives the event, checks for duplicate receipt, and creates the incident record
10. **Alert creation:** Per-user alerts are created for all operators with notification preferences matching the incident type/severity
11. **Real-time push:** SSE/WebSocket connections push notification to connected operator browsers
12. **Operator review:** Operator views incident detail, inspects evidence, and transitions incident state

[FIGURE PLACEHOLDER: End-to-End Incident Sequence]

*Figure 3.2. End-to-end sequence diagram from camera/audio input through AI detection, multimodal fusion, backend incident persistence, per-user alert creation, and operator notification delivery.*

### 3.4 Security Model

The security architecture implements defense-in-depth across all layers:

**Authentication:** JWT access tokens (15-minute expiry) with HTTP-only refresh tokens (7-day expiry). Token refresh uses rotation to detect token theft. All API endpoints require valid authentication except health checks.

**Authorization:** Role-based access control with three roles per tenant:
- **Admin:** Full access including user management, camera CRUD, system configuration
- **Operator:** Incident management, evidence review, notification management, camera viewing
- **Viewer:** Read-only access to incidents, evidence, and camera previews

**Tenant isolation:** All database queries are scoped to the authenticated user's tenant through Django middleware that injects tenant context. Foreign key constraints prevent cross-tenant data references at the database level. API endpoints validate tenant ownership before returning resources.

**Stream security:** Camera stream credentials are encrypted at rest and never exposed to the browser. The browser receives authenticated preview URLs that proxy through the backend, preventing direct camera access from the presentation layer.

**Transport security:** All external communication uses TLS. Internal service-to-service communication within the deployment cluster uses encrypted connections with mutual authentication where supported.

### 3.5 Deployment Topology

**Table 3.2. Service Deployment Configuration**

| Service | Technology | Scaling Strategy | Resource Profile |
|---|---|---|---|
| React dashboard | Nginx + static build | CDN / horizontal replicas | Low (static files) |
| Django control plane | Gunicorn + Uvicorn (ASGI) | Horizontal pod scaling | Medium (CPU-bound) |
| FastAPI AI service | Uvicorn workers | Vertical (GPU) + horizontal (per-camera-group) | High (GPU inference) |
| PostgreSQL | PostgreSQL 15+ | Vertical + read replicas | Medium-High (I/O) |
| Redis | Redis 7+ with Streams | Vertical + Redis Cluster for scale | Medium (memory) |
| MediaMTX | MediaMTX | Horizontal (per-stream-group) | Medium (network I/O) |
| Evidence storage | S3-compatible (MinIO/AWS S3) | Unlimited horizontal | Storage-bound |

### 3.6 Horizontal Scaling Considerations

The architecture supports horizontal scaling through the following mechanisms:

- **AI service:** Camera assignments can be partitioned across multiple AI service instances, each handling a subset of cameras. Redis Streams consumer groups enable cooperative processing of published events.
- **Django consumers:** Multiple Django instances participate in the same Redis consumer group, distributing event processing load.
- **Database:** Read replicas handle query load for dashboard rendering while the primary handles writes (incident creation, alert state updates).
- **Evidence storage:** S3-compatible object storage provides effectively unlimited horizontal scaling for media artifacts.

---

## Chapter 4. AI Model and Fusion Pipeline

### 4.1 Video-Based Inference Pipeline

The video inference subsystem implements a multi-lane detection architecture where each lane specializes in detecting a specific category of visual anomaly. Frames are captured from camera streams at a configurable sampling rate (default: 3 FPS for inference, higher rates available for specific lanes requiring temporal resolution).

**Table 4.1. Video Detection Lane Configuration**

| Lane | Model Architecture | Input Resolution | Inference Rate | Output |
|---|---|---|---|---|
| Person/object detection | YOLOv8/YOLOv12 [4][19] | 640x640 | 3 FPS | Bounding boxes, class labels, confidence |
| Fire/smoke detection | YOLOv8-cls fine-tuned | 224x224 | 2 FPS | Classification score, region |
| Weapon detection | YOLOv8 fine-tuned | 640x640 | 3 FPS | Bounding boxes, weapon class, confidence |
| Fall detection | Pose estimation + rule logic | 640x480 | 3 FPS | Fall candidate score, keypoints |
| Violence detection | Temporal CNN + optical flow | 224x224 (16 frames) | 1 clip/sec | Violence probability |
| Accident detection | YOLOv8 + motion analysis | 640x640 | 3 FPS | Collision candidate score |
| Zone intrusion | Person detection + polygon check | 640x640 | 3 FPS | Zone violation flag, person count |

Each lane produces observations with the following structure:

```
Observation = {
    lane_id: str,
    label: str,
    confidence: float ∈ [0, 1],
    bounding_box: Optional[Tuple[x1, y1, x2, y2]],
    timestamp: datetime,
    frame_index: int,
    metadata: Dict[str, Any]
}
```

**Temporal verification** correlates observations across consecutive frames using a sliding window (default: 3-5 frames). An observation is promoted to an incident candidate only if consistent detections appear across the verification window, suppressing transient false positives from single-frame artifacts:

$$
\text{verified}(o) = \begin{cases} 1 & \text{if } \sum_{t=t_0}^{t_0+W} \mathbb{1}[c_t > \tau_{lane}] \geq k_{min} \\ 0 & \text{otherwise} \end{cases}
$$

where $W$ is the verification window size, $\tau_{lane}$ is the lane-specific confidence threshold, and $k_{min}$ is the minimum number of consistent detections required.

**Non-maximum suppression (NMS)** with IoU threshold 0.45 eliminates duplicate detections within each frame. Cross-lane deduplication ensures the same physical event is not reported multiple times through different detection pathways.

### 4.2 Audio-Based Inference Pipeline

#### 4.2.1 BEATs Architecture

BEATs [3] employs a transformer encoder architecture operating on audio patch embeddings derived from mel spectrogram representations. The model is trained through an iterative process:

1. **Audio tokenizer training:** A discrete acoustic tokenizer is trained to produce semantic audio tokens from spectrogram patches using masked auto-encoding and vector quantization
2. **Encoder pretraining:** The transformer encoder is pretrained to predict masked acoustic tokens, learning contextual audio representations
3. **Iterative refinement:** Steps 1-2 are iterated, with each round producing improved tokenizers and encoders

The BEATs encoder processes audio inputs as follows:
- Input audio is resampled to 16 kHz mono
- A mel spectrogram is computed with 128 mel bands, 25ms window, and 10ms hop
- The spectrogram is divided into non-overlapping patches (16x16)
- Patches are linearly projected to the transformer dimension
- Positional embeddings are added
- The transformer encoder (12 layers, 768 dimensions, 12 attention heads) produces contextual representations
- A classification head maps pooled representations to AudioSet label probabilities

#### 4.2.2 Audio Processing Pipeline

The VigilZone audio pipeline implements the following processing chain:

1. **Stream extraction:** FFmpeg extracts audio from RTSP/RTMP streams in real-time, outputting mono 16 kHz PCM audio
2. **Chunking:** Audio is segmented into overlapping chunks (default: 2-second windows with 0.5-second overlap), providing temporal resolution while maintaining sufficient context for BEATs inference
3. **Preprocessing:** Each chunk is normalized (peak normalization to [-1, 1]), converted to mel spectrogram representation compatible with BEATs input requirements
4. **Inference:** BEATs model processes the spectrogram and produces AudioSet-class probabilities
5. **Label mapping:** AudioSet probabilities are mapped to surveillance-domain labels through a configurable mapping table

**Table 4.2. AudioSet to Surveillance Label Mapping**

| Surveillance Label | AudioSet Source Classes | Confidence Threshold |
|---|---|---|
| audio_scream | Screaming, Shout, Yell | 0.60 |
| audio_glass_break | Breaking, Glass, Shatter | 0.55 |
| audio_alarm | Alarm, Siren, Fire alarm, Smoke detector | 0.50 |
| audio_siren | Emergency vehicle, Ambulance siren, Police siren | 0.50 |
| audio_vehicle_crash | Crash, Bang, Thud, Smash | 0.55 |
| audio_gunshot_like | Gunshot, Gunfire, Fireworks, Cap gun | 0.60 |
| audio_explosion_like | Explosion, Boom, Thunder | 0.60 |

The audio confidence score for surveillance label $l$ is computed as:

$$
c_{audio}(l) = \sigma\left(\max_{k \in \text{map}(l)} p_k - \tau_l\right) \cdot s
$$

where $p_k$ are the AudioSet class probabilities for classes mapped to surveillance label $l$, $\tau_l$ is the label-specific threshold, $\sigma$ is the sigmoid function providing smooth confidence calibration, and $s$ is a scaling factor (default: 2.0) that maps the sigmoid output to a useful confidence range.

### 4.3 Temporal Audio-Video Fusion

#### 4.3.1 Fusion Window Mechanism

The temporal fusion module operates on a sliding window that collects observations from both audio and video lanes within a configurable time tolerance (default: $\Delta t_{fusion} = 3.0$ seconds). This window accounts for the fact that audio and visual evidence of the same event may not occur at exactly the same timestamp due to:

- Audio propagation delays (speed of sound)
- Asynchronous frame and audio chunk processing
- Events where audio precedes visual evidence (e.g., crash sound before visible impact)
- Events where visual evidence precedes audio (e.g., visible fall before impact sound)

For each candidate event at time $t$, the fusion module collects:

$$
O_{video}(t) = \{o_v : |o_v.timestamp - t| \leq \Delta t_{fusion}, o_v.lane \in \text{VideoLanes}\}
$$

$$
O_{audio}(t) = \{o_a : |o_a.timestamp - t| \leq \Delta t_{fusion}, o_a.lane \in \text{AudioLanes}\}
$$

#### 4.3.2 Fusion Scoring

The deterministic fusion score (Phase 1 production path) is computed as:

$$
c_{fused} = \alpha \cdot c_{video}^{max} + \beta \cdot c_{audio}^{max} + \gamma \cdot \text{temporal\_agreement}(O_{video}, O_{audio})
$$

where:
- $c_{video}^{max} = \max_{o_v \in O_{video}(t)} o_v.\text{confidence}$ is the maximum video observation confidence
- $c_{audio}^{max} = \max_{o_a \in O_{audio}(t)} o_a.\text{confidence}$ is the maximum audio observation confidence
- $\text{temporal\_agreement}$ is a bonus term rewarding temporal proximity between the strongest audio and video observations
- $\alpha, \beta, \gamma$ are weighting coefficients satisfying $\alpha + \beta + \gamma = 1$

Default coefficient values: $\alpha = 0.45$, $\beta = 0.35$, $\gamma = 0.20$

The temporal agreement bonus is computed as:

$$
\text{temporal\_agreement}(O_v, O_a) = \exp\left(-\frac{|\hat{t}_v - \hat{t}_a|^2}{2\sigma_t^2}\right)
$$

where $\hat{t}_v$ and $\hat{t}_a$ are the timestamps of the highest-confidence video and audio observations respectively, and $\sigma_t$ (default: 1.5 seconds) controls the temporal tolerance.

**Fusion mode behavior:**

| Camera Mode | $\alpha$ | $\beta$ | $\gamma$ | Behavior |
|---|---|---|---|---|
| audio_video | 0.45 | 0.35 | 0.20 | Full multimodal fusion |
| video_only | 1.0 | 0.0 | 0.0 | Video confidence only |
| audio_only | 0.0 | 1.0 | 0.0 | Audio confidence only |

#### 4.3.3 Fusion Reason Generation

Each fused incident includes a human-readable fusion reason for operator inspection:

- "Audio-video corroboration: [video_label] detected visually with [audio_label] detected acoustically within [Δt]s window"
- "Video-only detection: [label] detected with confidence [c] (audio unavailable or below threshold)"
- "Audio-only alert: [audio_label] detected acoustically with confidence [c] (no corroborating visual observation)"

### 4.4 Normality Profiling

Per-camera normality profiles track the statistical baseline of observations over time using exponential moving average (EMA) adaptation. The purpose is to reduce alert fatigue from recurring non-threatening environmental patterns (traffic noise near a highway camera, wind noise on an exposed rooftop, HVAC rumble near an indoor camera) while preserving sensitivity to genuinely anomalous events.

For each camera $c$ and non-critical observation label $l$, the normality profile maintains:

**Running mean confidence:**
$$
\mu_{c,l}^{(t)} = (1 - \lambda) \cdot \mu_{c,l}^{(t-1)} + \lambda \cdot c_{current}
$$

**Running variance:**
$$
\sigma_{c,l}^{2(t)} = (1 - \lambda) \cdot \sigma_{c,l}^{2(t-1)} + \lambda \cdot (c_{current} - \mu_{c,l}^{(t)})^2
$$

where $\lambda$ is the adaptation rate (default: 0.05 for slow adaptation, higher values for faster response to environmental changes).

**Confidence adjustment:**
$$
c_{adjusted} = c_{raw} \cdot \left(1 - \text{normality\_weight}(c, l)\right)
$$

where:
$$
\text{normality\_weight}(c, l) = \min\left(w_{max}, \frac{n_{observations}}{n_{threshold}} \cdot \left(1 - \frac{|c_{raw} - \mu_{c,l}|}{\sigma_{c,l} + \epsilon}\right)^+\right)
$$

This formula reduces confidence for observations that are consistent with the camera's normal baseline while preserving full confidence for observations that deviate significantly from normal patterns. The $n_{observations} / n_{threshold}$ factor ensures that normality suppression only activates after sufficient observations have been accumulated (default: $n_{threshold} = 50$).

**Table 4.3. Normality Profile Configuration**

| Parameter | Default Value | Description |
|---|---|---|
| $\lambda$ (adaptation rate) | 0.05 | EMA smoothing factor for running statistics |
| $n_{threshold}$ | 50 | Minimum observations before normality activates |
| $w_{max}$ | 0.7 | Maximum normality suppression weight |
| $\epsilon$ | 0.01 | Numerical stability constant |
| Critical labels (protected) | scream, alarm, glass_break, gunshot, explosion | Never suppressed by normality |

**Critical label protection:** Labels classified as safety-critical are explicitly excluded from normality suppression regardless of their frequency. A camera near a fire station may frequently detect siren sounds, but these are never suppressed because each instance could represent a genuine emergency response requiring operator awareness.

### 4.5 Uncertainty Gating

Uncertainty gating serves as the final decision layer before an observation is promoted to alert-eligible status:

$$
\text{alert\_eligible}(o) = (c_{adjusted}(o) > \tau_{gate}) \wedge (\neg\text{suppressed}(o) \vee \text{is\_critical}(o.label))
$$

where $\tau_{gate}$ is the gating threshold (default: 0.40 for audio-video mode, 0.50 for single-modality modes).

The gating threshold can be configured per-camera and per-lane to accommodate deployment-specific requirements. A camera in a high-security zone may use lower thresholds (higher sensitivity, accepting more false positives) while a camera in a busy public area may use higher thresholds (higher specificity, accepting more missed detections).

**Table 4.4. Default Gating Thresholds by Mode and Severity**

| Scenario | $\tau_{gate}$ | Rationale |
|---|---|---|
| Audio-video fused (critical) | 0.35 | Multimodal corroboration provides high confidence |
| Audio-video fused (non-critical) | 0.45 | Higher threshold for non-critical events |
| Video-only | 0.50 | Single modality requires higher confidence |
| Audio-only (critical label) | 0.45 | Critical sounds warrant lower threshold |
| Audio-only (non-critical label) | 0.55 | Non-critical audio needs higher confidence |

### 4.6 Learned Fusion Shadow Mode

The learned fusion module implements a neural network that maps observation features to incident probabilities. During shadow mode, this network:

1. **Receives inputs:** Same observation features available to deterministic fusion — video confidences, audio confidences, temporal features, normality scores, label embeddings
2. **Produces predictions:** Binary incident probability and severity classification
3. **Logs outputs:** Shadow predictions are stored alongside deterministic fusion decisions for comparison
4. **Does not affect production:** Shadow predictions never trigger alerts or modify incident creation

The learned fusion loss function for training:

$$
\mathcal{L} = \text{BCE}(y, f_\theta(x_{video}, x_{audio}, x_{temporal})) + \lambda_{reg} ||\theta||_2^2
$$

where $y$ is the ground truth label (from operator confirmation/dismissal), $f_\theta$ is the learned fusion network, $x_{video}$ aggregates video observation features, $x_{audio}$ aggregates audio observation features, $x_{temporal}$ encodes temporal relationship features, and $\lambda_{reg}$ is L2 regularization weight.

**Promotion criteria:** The learned fusion head is promoted to production only when:
- Minimum 1000 labeled examples collected from operator feedback
- Shadow-mode accuracy exceeds deterministic fusion accuracy by > 5% on held-out validation
- False positive rate does not increase by more than 10% relative to deterministic baseline
- Safety-critical event recall remains above 95%

### 4.7 Model Serving and Performance

**Table 4.5. Model Serving Configuration**

| Component | Format | Hardware | Batch Size | Latency Target |
|---|---|---|---|---|
| YOLOv8/v12 detection | ONNX / TensorRT | GPU (CUDA) | 1-4 frames | < 30ms per frame |
| BEATs audio classifier | PyTorch / ONNX | GPU or CPU | 1 chunk | < 100ms per chunk |
| Fire/smoke classifier | ONNX | GPU | 1 frame | < 20ms per frame |
| Fusion computation | NumPy/CPU | CPU | N/A | < 5ms |
| Evidence generation | OpenCV/FFmpeg | CPU | 1 event | < 500ms |

Total inference-to-publication latency target: < 1 second from frame/chunk capture to Redis Stream publication for the critical path.

[FIGURE PLACEHOLDER: AI Audio-Video Runtime Pipeline]

*Figure 4.1. Audio-video inference pipeline showing video frame capture, multi-lane video detection, FFmpeg audio extraction, BEATs audio inference, normality profile adjustment, uncertainty gating, temporal fusion with scoring, evidence export, and alert publication to Redis Streams.*

---

## Chapter 5. Implementation Details

### 5.1 Frontend Implementation (React)

The React dashboard is built with:
- **React 18** with functional components and hooks
- **TypeScript** for type safety across API interactions
- **React Query / TanStack Query** for server state management with automatic background refetching
- **EventSource API** for SSE-based real-time notification reception
- **Tailwind CSS** for responsive layout adapting to desktop and tablet form factors

Key frontend patterns:
- Optimistic updates for notification read state (mark-as-read updates UI immediately, reconciles on server response)
- Polling fallback when SSE connection drops, with automatic reconnection
- Evidence lazy loading (thumbnails in list view, full resolution on detail view)
- Camera preview with HLS.js for low-latency adaptive streaming

### 5.2 Backend Implementation (Django)

The Django backend implements:
- **Django 5.x** with ASGI deployment (Uvicorn)
- **Django REST Framework** for RESTful API endpoints
- **Django Channels** for WebSocket/SSE support
- **django-filter** for querystring-based incident/alert filtering
- **PostgreSQL** with django ORM, raw queries for performance-critical paths
- **Celery** (optional) for deferred tasks (evidence cleanup, retention enforcement)

Key API endpoints:

**Table 5.1. Core REST API Endpoints**

| Method | Endpoint | Purpose |
|---|---|---|
| POST | /api/auth/token/ | JWT token pair generation |
| POST | /api/auth/token/refresh/ | Access token refresh |
| GET | /api/cameras/ | List tenant cameras |
| POST | /api/cameras/ | Register new camera |
| PATCH | /api/cameras/{id}/ | Update camera config |
| GET | /api/incidents/ | List tenant incidents (filtered) |
| GET | /api/incidents/{id}/ | Incident detail with observations + evidence |
| PATCH | /api/incidents/{id}/ | Update incident status |
| GET | /api/alerts/ | List user alerts |
| PATCH | /api/alerts/{id}/read/ | Mark alert as read |
| GET | /api/notifications/stream/ | SSE endpoint for real-time notifications |

### 5.3 AI Service Implementation (FastAPI)

The FastAPI AI service implements:
- **FastAPI** with async endpoints for health checks and configuration
- **Background task workers** for per-camera inference loops
- **asyncio** coordination between video and audio processing tasks
- **Model registry** managing loaded models with lazy initialization and GPU memory management
- **Evidence pipeline** with async file I/O for non-blocking evidence generation

### 5.4 Infrastructure Configuration

**Table 5.2. Infrastructure Dependencies**

| Component | Version | Configuration |
|---|---|---|
| Python | 3.11+ | AI service and Django backend |
| Node.js | 18+ | Frontend build toolchain |
| PostgreSQL | 15+ | With pgvector extension (future embedding search) |
| Redis | 7+ | Streams, pub/sub, caching |
| FFmpeg | 6+ | Audio extraction, video transcoding |
| CUDA | 12.x | GPU inference acceleration |
| Docker | 24+ | Local development containerization |

---

## Chapter 6. Testing Methodology and Results

### 6.1 Test Environment

**Table 6.1. Test Environment Specification**

| Component | Specification |
|---|---|
| Development machine | Windows 11 / WSL2, 32GB RAM, NVIDIA RTX 3070/4070 |
| Camera sources | Local webcam (USB), RTSP simulator, sample video files |
| Audio sources | Built-in microphone, WAV test files, RTSP audio streams |
| Database | PostgreSQL 15 (Docker, localhost:5432) |
| Redis | Redis 7 (Docker, localhost:32768, with auth) |
| AI models | YOLOv8n/s (ONNX), BEATs-iter3 (PyTorch) |
| Browser | Chrome 120+, Firefox 120+ |

### 6.2 Evaluation Approach

The evaluation focuses on end-to-end system correctness and operational performance rather than public benchmark ranking. This approach is appropriate because the project contribution is a deployable platform integrating existing model capabilities, not a novel model architecture competing on established academic benchmarks. The test methodology verifies:

1. **Functional correctness:** Each system component produces expected outputs given controlled inputs
2. **Integration correctness:** Components interact correctly through defined interfaces
3. **Performance compliance:** Latency and throughput meet operational requirements
4. **Reliability:** System behaves correctly under error conditions, duplicate events, and recovery scenarios
5. **Security:** Tenant isolation is enforced and unauthorized access is prevented

### 6.3 Comprehensive Test Matrix

**Table 6.2. Comprehensive Test and Evaluation Matrix**

| ID | Category | Scenario | Input | Expected Behavior | Acceptance Criteria | Result |
|---|---|---|---|---|---|---|
| T1 | Video inference | Person detection lane | Webcam with person visible | Detection with bounding box and confidence > 0.5 | Correct label, IoU > 0.5 with ground truth | *Measured value to be inserted from final demo run.* |
| T2 | Video inference | Fire/smoke classification | Sample fire video clip | Fire classification with confidence > 0.6 | Correct label within 2 seconds of appearance | *Measured value to be inserted from final demo run.* |
| T3 | Video inference | Weapon detection | Sample weapon image/video | Weapon detection with bounding box | Correct label, confidence > 0.5 | *Measured value to be inserted from final demo run.* |
| T4 | Video inference | Fall detection | Simulated fall sequence | Fall candidate score elevation | Score > threshold within 3 frames of fall | *Measured value to be inserted from final demo run.* |
| T5 | Audio inference | Scream detection | WAV file with scream audio | audio_scream label with confidence > 0.6 | Correct label, latency < 200ms | *Measured value to be inserted from final demo run.* |
| T6 | Audio inference | Glass break detection | WAV file with glass breaking | audio_glass_break label | Correct label, confidence > 0.55 | *Measured value to be inserted from final demo run.* |
| T7 | Audio inference | Background noise rejection | Traffic/HVAC ambient audio | No high-confidence anomaly labels | All labels below threshold after normality adaptation | *Measured value to be inserted from final demo run.* |
| T8 | Fusion | Audio-video corroboration | Person visible + scream audio within 3s | Fused incident with multimodal reason | Fused confidence > max(video, audio) individual | *Measured value to be inserted from final demo run.* |
| T9 | Fusion | Audio-only critical alert | Scream audio, no visual match | Audio-only alert with reduced confidence | Alert created, marked as audio_only modality | *Measured value to be inserted from final demo run.* |
| T10 | Fusion | Video-only fallback | Video detection, audio unavailable | Video-only incident created normally | System operates without error, correct modality tag | *Measured value to be inserted from final demo run.* |
| T11 | Normality | Background suppression | Repeated traffic noise (5 min) | Normality profile reduces confidence over time | Adjusted confidence < raw confidence after 50+ obs | *Measured value to be inserted from final demo run.* |
| T12 | Normality | Critical label protection | Repeated alarm sound | Alarm confidence NOT reduced | Adjusted confidence = raw confidence for critical labels | *Measured value to be inserted from final demo run.* |
| T13 | Platform | Duplicate event replay | Same event_id published twice to Redis | Single incident, single per-user alert | IncidentEventReceipt prevents duplicate creation | *Measured value to be inserted from final demo run.* |
| T14 | Platform | Notification latency | Confirmed AI event → browser alert | Real-time notification delivery | End-to-end latency < 3 seconds | *Measured value to be inserted from final demo run.* |
| T15 | Security | Tenant isolation | Cross-tenant API request | 403 Forbidden or empty result | No cross-tenant data leakage | *Measured value to be inserted from final demo run.* |
| T16 | Security | Unauthenticated access | API request without JWT | 401 Unauthorized | All protected endpoints reject | *Measured value to be inserted from final demo run.* |
| T17 | Platform | Evidence availability | Confirmed fused incident | Incident links keyframe + video clip + audio WAV | All evidence URLs accessible and valid | *Measured value to be inserted from final demo run.* |
| T18 | Platform | Mode fallback | Camera set to video_only, audio stream available | Audio ignored, video-only processing | No audio observations generated | *Measured value to be inserted from final demo run.* |

### 6.4 Performance Benchmarks

**Table 6.3. Latency Budget and Performance Targets**

| Stage | Target Latency | Measurement Method |
|---|---|---|
| Frame capture (OpenCV) | < 50ms | Timestamp delta from stream to buffer |
| Audio chunk extraction (FFmpeg) | < 100ms | Chunk availability timestamp |
| Video lane inference (YOLOv8, GPU) | < 30ms per frame | Model forward pass timing |
| Audio inference (BEATs, GPU) | < 100ms per chunk | Model forward pass timing |
| Audio inference (BEATs, CPU) | < 500ms per chunk | Model forward pass timing |
| Temporal fusion computation | < 5ms | Fusion function timing |
| Evidence generation (keyframe) | < 100ms | File write completion |
| Evidence generation (video clip, 5s) | < 1000ms | FFmpeg clip export completion |
| Redis Stream publication | < 10ms | Publish acknowledgment timing |
| Django event consumption | < 50ms | Consumer processing timing |
| Alert creation (per user) | < 20ms | Database insert timing |
| SSE notification push | < 100ms | Event delivery to browser |
| **Total end-to-end (critical path)** | **< 2000ms** | **Frame capture → browser notification** |

### 6.5 Load Testing Scenarios

**Table 6.4. Load Testing Configuration**

| Scenario | Configuration | Success Criteria |
|---|---|---|
| Single camera, single user | 1 camera at 3 FPS, 1 operator | All latency targets met |
| Multi-camera baseline | 4 cameras at 3 FPS, 2 operators | 95th percentile latency within 2x target |
| Concurrent notifications | 10 simultaneous incidents, 5 operators | All notifications delivered < 5 seconds |
| Sustained operation | 2 cameras for 1 hour continuous | No memory leak, no dropped events |
| Recovery after disconnect | Camera stream interruption + reconnect | Inference resumes within 30 seconds |

### 6.6 Audio-Specific Test Scenarios

**Table 6.5. Audio Sensitivity and Robustness Tests**

| Scenario | SNR Condition | Expected Behavior |
|---|---|---|
| Clean audio event | > 20 dB SNR | High confidence detection (> 0.7) |
| Moderate noise | 10-20 dB SNR | Moderate confidence (0.5-0.7) |
| High noise | 5-10 dB SNR | Low confidence, may fall below threshold |
| Extreme noise | < 5 dB SNR | No detection (below threshold) |
| Overlapping events | Two events simultaneous | Primary event detected, secondary if separable |
| Continuous background | Constant ambient (traffic, rain) | Normality suppression activates after warmup |

### 6.7 Regression Test Strategy

Automated regression tests verify that updates to any system component do not break existing functionality:

- **Unit tests:** Model inference produces expected outputs for fixed test inputs (deterministic with fixed seeds)
- **Integration tests:** End-to-end event flow from synthetic camera input to database incident creation
- **API contract tests:** All REST endpoints return expected schemas and status codes
- **Notification tests:** SSE delivery of test events to connected browser sessions
- **Tenant isolation tests:** Cross-tenant access attempts return appropriate errors

---

## Chapter 7. Demonstration Plan

### 7.1 Demo Objective

The demonstration presents VigilZone as a complete operational platform rather than a model accuracy showcase. The demo shows the full workflow from camera stream through AI inference, multimodal fusion, incident creation, evidence review, and operator notification — demonstrating that the system produces actionable intelligence for surveillance operators rather than raw model outputs.

### 7.2 Timed Demo Script (10 Minutes)

[FIGURE PLACEHOLDER: Demo Workflow]

*Figure 7.1. Demonstration workflow showing progression from system introduction through video-only inference, audio-only inference, audio-video fusion, notification delivery, evidence review, and reliability verification.*

**Table 7.1. Demo Scenario Checklist and Script**

| Time | Step | Demonstration Content | AI Audio Voice Overlay Script |
|---|---|---|---|
| 0:00-0:45 | Introduction | VigilZone dashboard with registered camera, system status | "VigilZone is a real-time audio-visual anomaly detection and notification platform designed for community surveillance deployments." |
| 0:45-1:30 | Problem and Market | Problem statement slide, market statistics | "Traditional surveillance systems separate camera preview, AI detection, evidence management, and notifications into disconnected components. VigilZone integrates them into one tenant-aware, multimodal workflow." |
| 1:30-2:30 | Architecture | Updated cloud architecture diagram with audio-video components | "The architecture separates concerns: the browser communicates with Django for trust and persistence, FastAPI handles all AI inference and media processing, Redis Streams transports events, and PostgreSQL provides durable truth." |
| 2:30-3:30 | Video-Only Inference | Live or sample video showing person/fire/weapon detection with bounding boxes | "This demonstrates the video detection lanes. Each lane specializes in a specific anomaly type. The system operates normally when a camera provides no microphone or is configured for video-only mode." |
| 3:30-4:30 | Audio-Only Inference | Play test audio, show BEATs classification output and surveillance label mapping | "The audio lane extracts audio from the camera stream, converts it to 16 kHz chunks, and uses BEATs to classify safety-relevant acoustic events. Labels are mapped to surveillance-specific categories." |
| 4:30-5:45 | Audio-Video Fusion | Trigger concurrent audio + video event, show fused incident with multimodal reason | "When audio and video observations occur within the fusion window, the system correlates them and produces a higher-confidence multimodal incident with an explainable fusion reason." |
| 5:45-6:45 | Notification Delivery | Show notification bell, unread count update, alert list population in real-time | "The backend persists the incident, creates per-user alerts based on notification preferences, and delivers a real-time notification through Server-Sent Events." |
| 6:45-7:45 | Evidence Review | Open incident detail showing keyframe, video clip, audio WAV, confidence scores, fusion reason | "Operators can inspect all evidence associated with an incident — keyframe images, video clips, audio segments, model confidence scores, and the fusion reasoning — before deciding on a response action." |
| 7:45-8:45 | Reliability | Replay same event or refresh page, show no duplicate incident | "The system handles at-least-once delivery semantics safely. A replayed event does not create duplicate incidents because the backend tracks event receipts and enforces idempotent creation." |
| 8:45-10:00 | Summary and Future Work | Novelty recap, learned fusion roadmap, AVadCLIP extension path | "The project contribution is a deployable multimodal surveillance workflow integrating pretrained audio understanding, real-time video detection, temporal fusion, evidence management, and notification delivery. Future work includes learned fusion promotion and CLIP-based cross-modal alignment." |

[FIGURE PLACEHOLDER: Demo Screenshot - Dashboard Overview]
*Figure 7.2. Screenshot placeholder: VigilZone operator dashboard showing registered cameras, system status, and navigation.*

[FIGURE PLACEHOLDER: Demo Screenshot - Video Detection Output]
*Figure 7.3. Screenshot placeholder: Video lane detection showing bounding boxes, class labels, and confidence scores overlaid on camera frames.*

[FIGURE PLACEHOLDER: Demo Screenshot - Audio Event Classification]
*Figure 7.4. Screenshot placeholder: Audio inference output showing BEATs classification, surveillance label mapping, and confidence scores.*

[FIGURE PLACEHOLDER: Demo Screenshot - Multimodal Fused Incident]
*Figure 7.5. Screenshot placeholder: Fused incident detail showing correlated audio and video evidence with temporal alignment and fusion reason.*

[FIGURE PLACEHOLDER: Demo Screenshot - Notification and Evidence Review]
*Figure 7.6. Screenshot placeholder: Notification interface with incident evidence review panel showing keyframe, video clip playback, and audio segment.*

### 7.3 AI Audio Voice Overlay Implementation

The demo video includes an AI audio voice overlay for explanation and accessibility. The overlay narrates the system architecture, inference pipeline, and alert workflow but is separate from the surveillance inference pipeline itself. Implementation approach:

1. Record the full screen demonstration using OBS Studio or system screen recording
2. Generate narration audio using a text-to-speech engine (e.g., Azure TTS, Google Cloud TTS, or ElevenLabs) matching the script in Table 7.1
3. Synchronize narration segments with corresponding demo timestamps during video editing
4. Label the voice overlay clearly as demonstration narration in the video introduction

The voice overlay is a presentation aid, not a production system feature. The safe disclosure is: "The demo video includes an AI audio voice overlay for explanation and accessibility. It narrates the architecture and alert workflow but is separate from the surveillance inference pipeline."

---

## Chapter 8. Novelty, Limitations, and Future Work

### 8.1 Project/System Novelty

The system novelty is the integration of pretrained audio understanding, real-time video detection, temporal audio-video fusion, evidence management, tenant-scoped backend persistence, and real-time notification delivery into one deployable surveillance workflow. No prior academic work or commercial product identified in the literature review simultaneously provides all of the following in a single integrated platform:

- Multi-lane video detection with temporal verification
- BEATs-based audio event recognition with domain-specific label mapping
- Configurable temporal audio-video fusion with explainable reasoning
- Per-camera normality profiling with critical label protection
- Multi-tenant data isolation with RBAC enforcement
- Durable incident lifecycle management with idempotent creation
- Evidence linking (keyframe + video clip + audio WAV) per incident
- Real-time notification delivery with consistent state management
- Operator review workflow with evidence inspection and incident state transitions

### 8.2 Model/Fusion Novelty

The model/fusion novelty is the practical combination of BEATs audio event inference with existing real-time video detection lanes through:

1. **Configurable temporal fusion** with adjustable window size, weighting coefficients, and temporal agreement scoring
2. **Per-camera normality profiling** using EMA-based adaptation with configurable learning rates and activation thresholds
3. **Uncertainty-aware gating** with per-lane and per-mode threshold configuration
4. **Critical label protection** ensuring safety-significant events are never suppressed by background adaptation
5. **Shadow-mode learned fusion** providing a safe path toward adaptive improvement without risking production reliability

The implementation is SOTA-aligned rather than SOTA-claimed. The system uses strong pretrained models (BEATs, YOLO) and combines them through principled fusion with operational safeguards, rather than claiming to outperform these models on their respective benchmarks.

### 8.3 Deployment/Engineering Novelty

The deployment/engineering novelty includes:

1. **Modality-aware camera configuration** allowing per-camera selection of video_only, audio_only, or audio_video modes with runtime switching
2. **At-least-once event delivery with idempotent processing** using Redis Streams consumer groups and database-level receipt deduplication
3. **Trust boundary enforcement** strictly separating browser (presentation), Django (authorization/persistence), and FastAPI (inference) responsibilities
4. **Evidence lifecycle management** with automated capture, storage, metadata linking, and configurable retention policies
5. **Graceful degradation** when audio hardware is unavailable, a stream drops, or a model fails to load

### 8.4 Limitations

1. **No public benchmark evaluation:** The system is evaluated through functional and integration testing rather than UCF-Crime or XD-Violence benchmark runs. This is appropriate for the platform contribution but limits direct comparison with research methods.
2. **Single-site deployment:** Current architecture targets single-site or small-scale deployments (< 20 cameras). Large-scale deployments would require additional infrastructure (message partitioning, model serving clusters, CDN for evidence delivery).
3. **GPU dependency:** Real-time inference at target latencies requires GPU hardware. CPU-only deployment is possible but with reduced frame rates and higher audio inference latency.
4. **Learned fusion not validated:** The learned fusion shadow mode is designed but not yet promoted to production due to insufficient labeled training data.
5. **Audio quality dependency:** BEATs performance degrades in very high noise environments (< 5 dB SNR). Deployments in extremely noisy environments may require hardware-level noise reduction.

### 8.5 Future Work

1. **Learned fusion promotion:** Collect sufficient operator feedback data to train, validate, and promote the learned fusion head to production
2. **AVadCLIP integration:** Explore CLIP-based cross-modal alignment [17] as an alternative or complement to the deterministic fusion path
3. **Edge-cloud hybrid:** Implement edge-side frame filtering and lightweight detection to reduce bandwidth and cloud inference load
4. **Embedding search:** Leverage pgvector for similarity-based incident search and clustering
5. **Active learning:** Use uncertainty gating signals to prioritize operator review of borderline cases, accelerating labeled data collection for learned fusion

---

## References

[1] Grand View Research, "AI in video surveillance market size, share and trends analysis report, 2025-2030," 2025. [Online]. Available: https://www.grandviewresearch.com/industry-analysis/artificial-intelligence-ai-video-surveillance-market-report

[2] MarketsandMarkets, "AI in video surveillance market by offering, deployment, technology - global forecast to 2030," 2024. [Online]. Available: https://www.researchandmarkets.com/report/ai-in-video-surveillance

[3] S. Chen, Y. Wu, C. Wang, S. Liu, D. Tompkins, Z. Chen, and F. Wei, "BEATs: Audio pre-training with acoustic tokenizers," in *Proc. 40th Int. Conf. Machine Learning (ICML)*, 2023, pp. 5178-5193.

[4] J. Redmon, S. Divvala, R. Girshick, and A. Farhadi, "You only look once: Unified, real-time object detection," in *Proc. IEEE Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2016, pp. 779-788.

[5] W. Lv, S. Xu, Y. Zhao, G. Wang, J. Wei, C. Cui, Y. Du, Q. Dang, and Y. Liu, "DETRs beat YOLOs on real-time object detection," in *Proc. IEEE/CVF Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2024.

[6] A. Radford, J. W. Kim, C. Hallacy, A. Ramesh, G. Goh, S. Agarwal, G. Sastry, A. Askell, P. Mishkin, J. Clark, G. Krueger, and I. Sutskever, "Learning transferable visual models from natural language supervision," in *Proc. 38th Int. Conf. Machine Learning (ICML)*, 2021, pp. 8748-8763.

[7] W. Sultani, C. Chen, and M. Shah, "Real-world anomaly detection in surveillance videos," in *Proc. IEEE Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2018, pp. 6479-6488.

[8] J.-X. Zhong, N. Li, W. Kong, S. Liu, T. H. Li, and G. Li, "Graph convolutional label noise cleaner: Train a plug-and-play action classifier for anomaly detection," in *Proc. IEEE/CVF Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2019, pp. 1237-1246.

[9] Y. Tian, G. Pang, Y. Chen, R. Singh, J. W. Verjans, and G. Carneiro, "Weakly-supervised video anomaly detection with robust temporal feature magnitude learning," in *Proc. IEEE/CVF Int. Conf. Comput. Vis. (ICCV)*, 2021, pp. 4975-4986.

[10] W. Liu, W. Luo, D. Lian, and S. Gao, "Future frame prediction for anomaly detection -- a new baseline," in *Proc. IEEE/CVF Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2018, pp. 6536-6545.

[11] P. Wu, J. Liu, Y. Shi, Y. Sun, F. Shao, Z. Wu, and Z. Yang, "Not only look, but also listen: Learning multimodal violence detection under weak supervision," in *Proc. European Conf. Comput. Vis. (ECCV)*, 2020, pp. 322-339.

[12] J. F. Gemmeke, D. P. W. Ellis, D. Freedman, A. Jansen, W. Lawrence, R. C. Moore, M. Plakal, and M. Ritter, "Audio Set: An ontology and human-labeled dataset for audio events," in *Proc. IEEE Int. Conf. Acoust. Speech Signal Process. (ICASSP)*, 2017, pp. 776-780.

[13] S. Hershey, S. Chaudhuri, D. P. W. Ellis, J. F. Gemmeke, A. Jansen, R. C. Moore, M. Plakal, D. Platt, R. A. Saurous, B. Seybold, M. Slaney, R. J. Weiss, and K. Wilson, "CNN architectures for large-scale audio classification," in *Proc. IEEE Int. Conf. Acoust. Speech Signal Process. (ICASSP)*, 2017, pp. 131-135.

[14] Q. Kong, Y. Cao, T. Iqbal, Y. Wang, W. Wang, and M. D. Plumbley, "PANNs: Large-scale pretrained audio neural networks for audio pattern recognition," *IEEE/ACM Trans. Audio Speech Lang. Process.*, vol. 28, pp. 2880-2894, 2020.

[15] K. Koutini, J. Schlüter, H. Eghbal-zadeh, and G. Widmer, "Efficient training of audio transformers with patchout," in *Proc. Interspeech*, 2022, pp. 2753-2757.

[16] B. Leporowski, A. Bakhtiarnia, N. Bonnici, A. Muscat, L. Zanella, Y. Wang, and A. Iosifidis, "Audio-visual dataset and method for anomaly detection in traffic videos," arXiv:2305.15084, 2023.

[17] P. Wu, W. Su, G. Pang, Y. Sun, Q. Yan, P. Wang, and Y. Zhang, "AVadCLIP: Audio-visual collaboration for robust video anomaly detection," arXiv:2504.04495, 2025.

[18] H. Zhou, A. Xu, D. Lin, L. Torresani, and B. Gong, "Positive sample propagation along the audio-visual event line," in *Proc. IEEE/CVF Conf. Comput. Vis. Pattern Recognit. (CVPR)*, 2021, pp. 8436-8444.

[19] G. Jocher, J. Qiu, and A. Chaurasia, "Ultralytics YOLO," version 8.0, 2023. [Online]. Available: https://github.com/ultralytics/ultralytics

[20] Redis Ltd., "Redis Streams documentation," 2024. [Online]. Available: https://redis.io/docs/data-types/streams/

[21] Django Software Foundation, "Django REST framework," version 3.14, 2024. [Online]. Available: https://www.django-rest-framework.org/

---

## Changelog

| Change | Description |
|---|---|
| Format | Specified IEEE two-column 12pt format directive for DOCX conversion |
| Content volume | Expanded from ~5 pages to ~20+ pages of technical content |
| Literature review | Expanded from 5 to 21 references with deeper discussion per work and gap analysis table |
| Architecture | Added API endpoints, database schema, message formats, security model, deployment topology, scaling |
| AI model | Added mathematical formulations for fusion scoring, normality EMA, uncertainty gating, learned fusion loss |
| AI model | Added BEATs architecture description, video lane configuration table, model serving specifications |
| Testing | Expanded from 10 to 18 test scenarios across video, audio, fusion, platform, and security categories |
| Testing | Added performance benchmarks table with latency budgets per stage |
| Testing | Added load testing scenarios, audio sensitivity tests, and regression strategy |
| Testing | Added test environment specification |
| All sections | Converted to polished academic prose with professional tone throughout |
| References | Expanded to 21 IEEE-formatted references ordered by first citation |
| Tables | Added 15+ tables with proper titles above each |
| Figures | All figure placeholders include IEEE-style captions below |
