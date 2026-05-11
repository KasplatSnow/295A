from django.db import models
from django.contrib.auth import get_user_model
import uuid
from pgvector.django import VectorField

User = get_user_model()
IDENTITY_EMBEDDING_DIM = 512


def default_instant_notification_levels():
    return ["critical", "severe", "moderate"]


def severity_level_for_value(severity: int) -> str:
    try:
        sev = int(severity)
    except (TypeError, ValueError):
        sev = 3
    if sev >= 5:
        return "critical"
    if sev >= 4:
        return "severe"
    if sev >= 3:
        return "moderate"
    if sev >= 2:
        return "low"
    return "info"


def normalize_instant_notification_levels(raw_levels) -> list[str]:
    allowed = ["critical", "severe", "moderate", "low", "info"]
    if not isinstance(raw_levels, list):
        return default_instant_notification_levels()
    normalized = []
    for value in raw_levels:
        text = str(value).strip().lower()
        if text in allowed and text not in normalized:
            normalized.append(text)
    return normalized or default_instant_notification_levels()

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    bio = models.TextField(blank=True)
    # Notification preferences
    notify_email = models.BooleanField(default=True)
    notify_push = models.BooleanField(default=True)
    notify_sms = models.BooleanField(default=False)
    instant_notification_levels = models.JSONField(default=default_instant_notification_levels, blank=True)
    # System preferences
    alert_sensitivity = models.CharField(max_length=16, default="medium")  # low|medium|high
    data_retention_days = models.IntegerField(default=60)
    audio_detection = models.BooleanField(default=True)
    blur_faces = models.BooleanField(default=True)
    consent_required = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.instant_notification_levels = normalize_instant_notification_levels(self.instant_notification_levels)
        super().save(*args, **kwargs)

    def allows_instant_notification(self, severity: int) -> bool:
        allowed_levels = set(normalize_instant_notification_levels(self.instant_notification_levels))
        return severity_level_for_value(severity) in allowed_levels

    def __str__(self):
        return f"Profile for {self.user.username}"

class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class Tenant(TimeStamped):
    name = models.CharField(max_length=200, unique=True)
    plan = models.CharField(max_length=50, default="free")

    def __str__(self):
        return self.name

class Membership(TimeStamped):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    class Meta:
        unique_together = [("tenant", "user")]
    
    def __str__(self):
        return f"{self.user.username} @ {self.tenant.name} ({self.role})"

class Camera(TimeStamped):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    class CameraType(models.TextChoices):
        BACKYARD = "backyard", "Backyard"
        FRONT_DOOR = "front_door", "Front Door"
        BEDROOM = "bedroom", "Bedroom"
        GARAGE = "garage", "Garage"
        LIVING_ROOM = "living_room", "Living Room"
        OTHER = "other", "Other"

    class SourceType(models.TextChoices):
        REGISTERED = "registered", "Registered"
        WEBCAM = "webcam", "Webcam"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="cameras")
    name = models.CharField(max_length=200)
    site = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    camera_type = models.CharField(max_length=20, choices=CameraType.choices, default=CameraType.OTHER)
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.REGISTERED)
    rtsp_url = models.CharField(max_length=512, blank=True)  # don't return this to clients
    ai_camera_id = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Identifier used by the AI module for this camera",
    )
    stream_path = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Stable camera stream identifier used for UI/AI mapping",
    )
    # Per-camera AI settings (§3 false-positive reduction)
    min_confidence = models.FloatField(default=0.35, help_text="Detection confidence threshold 0-1")
    min_bbox_area = models.IntegerField(default=400, help_text="Min bounding box area in pixels")
    k_of_n_k = models.IntegerField(default=3, help_text="K-of-N persistence: require K")
    k_of_n_n = models.IntegerField(default=5, help_text="K-of-N persistence: out of N")
    cooldown_s = models.IntegerField(default=45, help_text="Alert cooldown seconds")

    def _default_lanes():
        return ["rt_detr", "person_zone"]

    enabled_lanes = models.JSONField(
        default=_default_lanes,
        blank=True,
        help_text="Active detection lanes (e.g. ['rt_detr', 'person_zone', 'fire_smoke_yolo'])"
    )
    entity_detection_enabled = models.BooleanField(
        default=True,
        help_text="Enable identity matching lane for this camera",
    )
    audio_enabled = models.BooleanField(
        default=False,
        help_text="Enable audio anomaly detection (requires camera with microphone)",
    )
    # Phase 2: Multimodal Fusion Telemetry & Gating
    uncertainty_threshold = models.FloatField(
        default=0.6,
        help_text="Threshold above which audio uncertainty prevents synergy/standalone alerts",
    )
    normality_ema_alpha = models.FloatField(
        default=0.05,
        help_text="Smoothing factor for the background noise EMA baseline",
    )
    learned_fusion_mode = models.CharField(
        max_length=16,
        choices=[("off", "Off"), ("shadow", "Shadow"), ("active", "Active")],
        default="off",
        help_text="Mode for the AI Learned Fusion head",
    )
    source_kind = models.CharField(max_length=50, blank=True, default="", help_text="Derived or explicit source kind (e.g., rtsp, mjpeg, hls)")
    source_fingerprint = models.CharField(max_length=256, blank=True, default="", help_text="Optional TLS fingerprint for HTTPS IP cameras")
    url_hash = models.CharField(max_length=16, blank=True, default="", db_index=True, help_text="16-char hex hash of normalized URL for deduplication")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "url_hash"],
                name="unique_url_per_tenant",
                condition=~models.Q(url_hash=""),
                violation_error_message="This camera URL is already registered in your account."
            )
        ]

    def clean(self):
        """Validate that rtsp_url does not point back to our own relay.

        This is the input-boundary guard: data that would create a
        loopback is rejected *before* it reaches the database or
        the relay reconciler.
        """
        from django.core.exceptions import ValidationError
        from api.services.mediamtx_helpers import is_self_referential, sanitize_stream_url

        super().clean()

        url = sanitize_stream_url(self.rtsp_url)
        self.rtsp_url = url

        # Webcam / managed sources never need an external pull URL.
        # If one was set, silently clear it — the reconciler will
        # configure these as publisher-mode paths regardless.
        if self.source_type == self.SourceType.WEBCAM:
            if url and is_self_referential(url):
                self.rtsp_url = ""
            return

        # Registered cameras must not point at our own relay.
        if url and is_self_referential(url):
            raise ValidationError({
                "rtsp_url": (
                    "This URL points to VigilZone's own media relay. "
                    "Enter the external camera's real RTSP URL, or clear "
                    "the field to use publisher mode."
                ),
            })

        # Dedup check (Application level for better error UX)
        if url:
            from api.services.mediamtx_helpers import get_url_identity_hash
            target_hash = get_url_identity_hash(url)
            exists = Camera.objects.filter(tenant=self.tenant, url_hash=target_hash).exclude(pk=self.pk).exists()
            if exists:
                raise ValidationError({
                    "rtsp_url": "This camera URL is already registered in your account."
                })

    def save(self, *args, **kwargs):
        """Auto-derive stream_path if empty for consistent camera mapping."""
        from api.services.mediamtx_helpers import get_url_identity_hash
        
        # 1. Update identity hash for deduplication
        if self.rtsp_url:
            self.url_hash = get_url_identity_hash(self.rtsp_url)
        else:
            self.url_hash = ""

        # 2. Run validation (including duplicate check)
        self.clean()

        if not self.stream_path:
            from django.utils.text import slugify
            if self.ai_camera_id:
                self.stream_path = self.ai_camera_id
            elif self.name:
                self.stream_path = slugify(self.name)
        # Keep AI camera id aligned with stream_path when unset.
        # This avoids legacy cam_<pk> IDs and reduces snapshot/preview mismatches.
        if not self.ai_camera_id and self.stream_path:
            self.ai_camera_id = self.stream_path
        super().save(*args, **kwargs)


class CameraZone(TimeStamped):
    """Intrusion / monitoring zone polygon for a camera."""
    class ZoneType(models.TextChoices):
        RESTRICTED = "restricted", "Restricted"
        MONITOR = "monitor", "Monitor"

    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="zones")
    zone_name = models.CharField(max_length=100, default="restricted")
    zone_type = models.CharField(max_length=20, choices=ZoneType.choices, default=ZoneType.RESTRICTED)
    polygon_points = models.JSONField(default=list, help_text="List of [x,y] normalised coords")
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("camera", "zone_name")]

    def __str__(self):
        return f"{self.zone_name} ({self.zone_type}) on {self.camera.name}"

class Incident(TimeStamped):
    class Type(models.TextChoices):
        ROBBERY = "robbery", "Robbery"
        STRANGER = "stranger", "Stranger"
        FIRE = "fire", "Fire"
        INTRUSION = "intrusion", "Intrusion"
        AUDIO_ANOMALY = "audio_anomaly", "Audio Anomaly"
        WEAPON = "weapon", "Weapon"
        FALL = "fall", "Fall"
        VIOLENCE = "violence", "Violence"
        ACCIDENT = "accident", "Accident"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACK = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="incidents")
    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="incidents")
    type = models.CharField(max_length=24, choices=Type.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN)
    severity = models.IntegerField(default=1)  # 1..5
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)       # extra info
    media_key = models.CharField(max_length=512, blank=True)   # S3/MinIO key or path

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "started_at"]),
            models.Index(fields=["tenant", "status", "started_at"]),
            models.Index(fields=["tenant", "severity", "started_at"]),
            models.Index(fields=["camera", "type", "status", "started_at"]),
        ]

class Detection(TimeStamped):
    """
    Lightweight per-interval summary (e.g., 1s/5s) of model output.
    Use JSONField for flexibility during dev (SQLite ok). Partition later in Postgres.
    """
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="detections")
    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="detections")
    ts = models.DateTimeField(db_index=True)
    payload = models.JSONField(default=dict)  # boxes, scores, classes, counts, etc.

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "camera", "ts"]),
        ]

class Alert(TimeStamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="alerts", null=True, blank=True)
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="alerts")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="alerts", null=True, blank=True)
    channel = models.CharField(max_length=32, default="email")   # realtime|email|webhook|sms|push
    payload = models.JSONField(default=dict, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("incident", "user", "channel")]
        indexes = [
            models.Index(fields=["tenant", "user", "delivered_at", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "delivered_at"]),
        ]

class AuditLog(TimeStamped):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)       # e.g., incident.update_status
    target_type = models.CharField(max_length=64)  # e.g., incident, camera
    target_id = models.CharField(max_length=64, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
        ]

class KnownEntity(TimeStamped):
    """Source-of-truth for enrolled entities (persons / pets / vehicles)."""
    class Category(models.TextChoices):
        PERSON = "person", "Person"
        PET = "pet", "Pet"
        VEHICLE = "vehicle", "Vehicle"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        DISABLED = "disabled", "Disabled"
        DELETED = "deleted", "Deleted"

    class Group(models.TextChoices):
        HOUSEHOLD = "household", "Household"
        NEIGHBOR = "neighbor", "Neighbor"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="entities")
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    detection_enabled = models.BooleanField(default=False)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.PERSON)
    group = models.CharField(max_length=20, choices=Group.choices, default=Group.HOUSEHOLD)
    notes = models.TextField(blank=True)
    processing_error = models.TextField(blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    embedding_version = models.PositiveIntegerField(default=1)
    entity_detection_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_entities",
    )
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_entities",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    cameras = models.ManyToManyField(Camera, related_name="known_entities", blank=True)
    ai_entity_id = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Identifier returned by the AI module after enrollment",
    )
    thumbnail_url = models.CharField(max_length=512, blank=True, default="")
    last_seen = models.DateTimeField(null=True, blank=True)
    last_camera = models.ForeignKey(
        Camera,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="last_seen_entities",
    )

    def __str__(self):
        return f"{self.name} ({self.category})"

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "detection_enabled"]),
        ]


class KnownEntityEmbedding(TimeStamped):
    """Canonical pgvector storage for known-entity embeddings."""

    class Modality(models.TextChoices):
        FACE = "face", "Face"
        PET_CLIP = "pet_clip", "Pet CLIP"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="entity_embeddings")
    entity = models.ForeignKey(KnownEntity, on_delete=models.CASCADE, related_name="embeddings")
    modality = models.CharField(max_length=32, choices=Modality.choices)
    vector = VectorField(dimensions=IDENTITY_EMBEDDING_DIM)
    source_dim = models.PositiveIntegerField(default=IDENTITY_EMBEDDING_DIM)
    is_active = models.BooleanField(
        default=True,
        help_text="Mark stale embeddings inactive on re-enrollment rather than hard-deleting.",
    )
    quality_score = models.FloatField(
        null=True, blank=True,
        help_text="Optional quality metric from the embedding pipeline (0.0-1.0).",
    )
    embedding_model = models.CharField(
        max_length=128, blank=True, default="",
        help_text="Model used to generate this embedding (e.g. insightface/buffalo_l, clip/ViT-B-32).",
    )
    embedding_version = models.PositiveIntegerField(
        default=1,
        help_text="Tracks re-embedding generations for delta-sync.",
    )
    source_image_uri = models.CharField(
        max_length=1024, blank=True, default="",
        help_text="Reference back to the KnownEntityAsset used to generate this embedding.",
    )
    source_checksum = models.CharField(
        max_length=128, blank=True, default="",
        help_text="SHA-256 checksum of the source image for integrity verification.",
    )
    generated_by = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Origin of this embedding: 'ai_enrollment', 'backend_worker', etc.",
    )
    deleted_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Soft-delete timestamp for audit trail.",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "modality"], name="api_ke_tenant_mod_idx"),
            models.Index(fields=["entity", "modality"], name="api_ke_entity_mod_idx"),
            models.Index(fields=["entity", "is_active"], name="api_ke_entity_active_idx"),
        ]

    def __str__(self):
        return f"{self.entity_id}:{self.modality}"


class KnownEntityAsset(TimeStamped):
    """Enrollment assets uploaded for a known entity."""

    class AssetType(models.TextChoices):
        ENROLLMENT_IMAGE = "enrollment_image", "Enrollment image"
        THUMBNAIL = "thumbnail", "Thumbnail"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="entity_assets")
    entity = models.ForeignKey(KnownEntity, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(
        max_length=32,
        choices=AssetType.choices,
        default=AssetType.ENROLLMENT_IMAGE,
    )
    file = models.FileField(upload_to="entity_assets/%Y/%m/%d")
    storage_uri = models.CharField(max_length=1024, blank=True, default="")
    checksum = models.CharField(max_length=128, blank=True, default="")
    content_type = models.CharField(max_length=128, blank=True, default="")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_entity_assets",
    )
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "entity", "asset_type", "is_active"]),
            models.Index(fields=["tenant", "is_active"]),
        ]

    def __str__(self):
        return f"Asset {self.id} for entity {self.entity_id}"


class KnownEntityProcessingJob(TimeStamped):
    """Asynchronous processing lifecycle for embedding generation."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="entity_processing_jobs")
    entity = models.ForeignKey(KnownEntity, on_delete=models.CASCADE, related_name="processing_jobs")
    requested_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="entity_processing_jobs",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["entity", "status", "created_at"]),
        ]

    def __str__(self):
        return f"Entity job {self.id} ({self.status})"


import secrets
from django.utils import timezone
from datetime import timedelta

class Invitation(TimeStamped):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="invitations")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=Membership.Role.choices, default=Membership.Role.MEMBER)

    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="sent_invitations")
    token = models.CharField(max_length=64, unique=True, default="", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    expires_at = models.DateTimeField()

    accepted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="accepted_invitations")
    accepted_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)

    def is_valid(self):
        return self.status == self.Status.PENDING and timezone.now() < self.expires_at

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=models.Q(status="pending"),
                name="unique_pending_invitation_per_tenant_email"
            ),
        ]
        indexes = [
            models.Index(fields=["email", "status"]),
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self):
        return f"Invite {self.email} -> {self.tenant.name} ({self.role}) [{self.status}]"


class NotificationChannel(TimeStamped):
    """Tenant-level notification preferences (§4)."""
    class SeverityThreshold(models.TextChoices):
        HIGH_ONLY = "high", "High only (sev >= 4)"
        MEDIUM_AND_HIGH = "medium", "Medium & High (sev >= 3)"
        ALL = "all", "All severities"

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="notification_channel")
    email_enabled = models.BooleanField(default=False)
    push_enabled = models.BooleanField(default=False)
    email_recipients = models.JSONField(default=list, blank=True, help_text='["a@b.com"]')
    fcm_tokens = models.JSONField(default=list, blank=True, help_text="FCM device tokens")
    severity_threshold = models.CharField(
        max_length=10,
        choices=SeverityThreshold.choices,
        default=SeverityThreshold.HIGH_ONLY,
    )

    def min_severity_int(self) -> int:
        return {"high": 4, "medium": 3, "all": 1}.get(self.severity_threshold, 4)

    def __str__(self):
        return f"Notifications for {self.tenant.name}"


class TenantRuntimeSetting(TimeStamped):
    """Tenant-level runtime controls for AI integration behavior."""

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="runtime_settings")
    webcam_enabled = models.BooleanField(default=False)
    identity_runtime_enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"Runtime settings for {self.tenant.name}"


class IncidentEventReceipt(models.Model):
    """
    Idempotency ledger for processed AI incident events.

    Prevents duplicate Incident/Detection/notification creation when
    the same logical event arrives through both Redis Pub/Sub and webhook.

    The event_id is the stable identifier from the AI alert payload
    (e.g., alert session ID or unique alert ID).
    """
    class Source(models.TextChoices):
        REDIS = "redis", "Redis"
        WEBHOOK = "webhook", "Webhook"

    event_id = models.CharField(max_length=255, unique=True, db_index=True)
    source = models.CharField(max_length=16, choices=Source.choices)
    processed_at = models.DateTimeField(auto_now_add=True)
    incident = models.ForeignKey(
        Incident,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_receipts",
    )

    class Meta:
        indexes = [
            models.Index(fields=["event_id"]),
        ]

    def __str__(self):
        return f"Receipt {self.event_id} via {self.source}"


class ServiceWebhook(TimeStamped):
    """Canonical webhook registry row (replaces AI-local webhooks.json over time)."""

    class Source(models.TextChoices):
        AI = "ai", "AI"
        BACKEND = "backend", "Backend"

    webhook_id = models.CharField(max_length=64, unique=True)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="service_webhooks",
        null=True,
        blank=True,
    )
    url = models.CharField(max_length=1024)
    events = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    delivery_stats = models.JSONField(default=dict, blank=True)
    has_secret = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.AI)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "active"]),
            models.Index(fields=["source", "active"]),
        ]

    def __str__(self):
        return f"Webhook {self.webhook_id} ({'active' if self.active else 'inactive'})"


class AIRuntimeRegistration(TimeStamped):
    """Desired/observed AI runtime state for a camera."""

    camera = models.OneToOneField(
        Camera,
        on_delete=models.CASCADE,
        related_name="runtime_registration",
    )
    desired_enabled = models.BooleanField(default=False)
    desired_ingest_backend = models.CharField(max_length=64, default="opencv")
    desired_sample_hz = models.FloatField(default=2.0)
    desired_lanes = models.JSONField(default=list, blank=True)
    desired_policy_version = models.IntegerField(default=1)

    observed_enabled = models.BooleanField(null=True, blank=True)
    observed_ingest_backend = models.CharField(max_length=64, blank=True, default="")
    observed_sample_hz = models.FloatField(null=True, blank=True)
    observed_lanes = models.JSONField(default=list, blank=True)
    observed_last_seen_at = models.DateTimeField(null=True, blank=True)

    source_metadata = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["desired_enabled"]),
            models.Index(fields=["observed_last_seen_at"]),
        ]

    def __str__(self):
        return f"AI runtime for camera {self.camera_id}"


class MediaMTXDesiredPath(TimeStamped):
    """Canonical desired relay state (no runtime cutover yet)."""

    class RelayMode(models.TextChoices):
        RELAY_ONLY = "relay_only", "Relay only"
        REMUX = "remux", "Remux"
        TRANSCODE = "transcode", "Transcode"

    camera = models.OneToOneField(
        Camera,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mediamtx_desired_path",
    )
    stream_path = models.CharField(max_length=200, unique=True)
    desired_enabled = models.BooleanField(default=True)
    relay_mode = models.CharField(max_length=24, choices=RelayMode.choices, default=RelayMode.RELAY_ONLY)
    source_uri = models.CharField(max_length=1024, blank=True, default="")
    source_kind = models.CharField(max_length=64, blank=True, default="")
    transcode_required = models.BooleanField(default=False)
    preview_consumer_uri = models.CharField(max_length=1024, blank=True, default="")
    ai_consumer_uri = models.CharField(max_length=1024, blank=True, default="")
    evidence_consumer_uri = models.CharField(max_length=1024, blank=True, default="")
    path_generation = models.BigIntegerField(default=1)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    drift_detected = models.BooleanField(default=False)
    last_error = models.TextField(blank=True)

    # ── Applied-state tracking (stamp-based convergence) ────
    last_applied_payload_hash = models.CharField(max_length=64, blank=True, default="")
    last_applied_generation = models.BigIntegerField(default=0)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["desired_enabled"]),
            models.Index(fields=["last_reconciled_at"]),
            models.Index(fields=["drift_detected"]),
        ]

    def __str__(self):
        return f"Desired MediaMTX path {self.stream_path}"


class MediaMTXObservedPathState(TimeStamped):
    """Latest observed reconcile/health state for a desired relay path."""

    desired_path = models.OneToOneField(
        MediaMTXDesiredPath,
        on_delete=models.CASCADE,
        related_name="observed_state",
    )
    observed_enabled = models.BooleanField(null=True, blank=True)
    observed_source = models.CharField(max_length=1024, blank=True, default="")
    observed_payload = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["observed_at"]),
            models.Index(fields=["observed_enabled"]),
        ]

    def __str__(self):
        return f"Observed MediaMTX state for {self.desired_path.stream_path}"


class OutboxEvent(models.Model):
    """Transactional outbox row for config-change side effects."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    aggregate_type = models.CharField(max_length=64)
    aggregate_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    headers = models.JSONField(default=dict, blank=True)
    attempts = models.IntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["published_at", "created_at"]),
            models.Index(fields=["aggregate_type", "aggregate_id"]),
            models.Index(fields=["event_type", "created_at"]),
        ]


class ProjectionWatermark(models.Model):
    """Projector progress/version marker for derived caches/projections."""

    projection_name = models.CharField(max_length=64)
    scope_type = models.CharField(max_length=32, default="global")
    scope_id = models.CharField(max_length=128, blank=True, default="")
    version = models.BigIntegerField(default=0)
    last_event_id = models.CharField(max_length=128, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["projection_name", "scope_type", "scope_id"],
                name="uq_projection_watermark_scope",
            )
        ]
        indexes = [
            models.Index(fields=["projection_name", "updated_at"]),
        ]

    def __str__(self):
        return f"Projection watermark {self.projection_name}:{self.scope_type}:{self.scope_id}"


class ProjectionRepairRequest(models.Model):
    """Auditable projection repair/rebuild request queue."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    projection_name = models.CharField(max_length=64, default="route_projection")
    scope_type = models.CharField(max_length=64)
    scope_id = models.CharField(max_length=128)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "requested_at"]),
            models.Index(fields=["projection_name", "scope_type", "scope_id"]),
        ]


class SchemaBootstrapState(models.Model):
    """Idempotency ledger for bootstrap/seed operations."""

    key = models.CharField(max_length=128, unique=True)
    value = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key


class BackfillCheckpoint(models.Model):
    """Tracks backfill progress when importing legacy config sources."""

    source_key = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=32, default="pending")
    last_token = models.CharField(max_length=256, blank=True, default="")
    stats = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Backfill {self.source_key} ({self.status})"
