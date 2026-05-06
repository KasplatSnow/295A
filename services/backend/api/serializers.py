from rest_framework import serializers
from django.utils.text import slugify
from .models import (
    Tenant, Membership, Camera, CameraZone, Incident, Detection,
    Alert, AuditLog, Profile, Invitation, KnownEntity, NotificationChannel,
    normalize_instant_notification_levels,
)
from django.contrib.auth.models import User
from api.services.mediamtx_helpers import sanitize_stream_url
from api.services.audit_display import present_audit_log

class TenantSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ["id", "name", "plan", "role", "created_at", "updated_at"]

    def get_role(self, obj):
        user = self.context["request"].user
        # Avoid N+1 by iterating over prefetched memberships instead of querying
        for m in obj.memberships.all():
            if m.user_id == user.id:
                return m.role
        return None

class MyTenantSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    role = serializers.CharField()
    
    def to_representation(self, instance):
        return {
            "id": instance.tenant.id,
            "name": instance.tenant.name,
            "role": instance.role,
        }

class MemberUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email"]

class MembershipSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "tenant", "user", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "tenant", "created_at", "updated_at"]

    def to_representation(self, instance):
        print("USING NESTED MEMBERSHIP SERIALIZER")
        return super().to_representation(instance)
class CameraSafeSerializer(serializers.ModelSerializer):
    is_ai_synced = serializers.SerializerMethodField()

    class Meta:
        model = Camera
        fields = [
            "id", "name", "site", "status", "camera_type",
            "source_type",
            "ai_camera_id", "stream_path",
            "min_confidence", "min_bbox_area", "k_of_n_k", "k_of_n_n", "cooldown_s",
            "enabled_lanes",
            "entity_detection_enabled",
            "is_ai_synced",
            "created_at", "updated_at", "tenant",
        ]
        read_only_fields = ["created_at", "updated_at", "tenant"]

    def get_is_ai_synced(self, obj):
        # Prevent N+1 if ai_registration is prefetched, otherwise query
        if hasattr(obj, 'runtime_registration'):
            return obj.runtime_registration.desired_enabled
        from api.models import AIRuntimeRegistration
        reg = AIRuntimeRegistration.objects.filter(camera=obj).first()
        return reg.desired_enabled if reg else False


class CameraStreamSerializer(serializers.ModelSerializer):
    """Read-only serializer used by UI stream mapping."""

    class Meta:
        model = Camera
        fields = [
            "id", "name", "site", "status", "ai_camera_id", "stream_path",
            "camera_type", "source_type",
        ]

class CameraAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Camera
        fields = "__all__"

class CameraWriteSerializer(serializers.ModelSerializer):
    """
    Accepts both canonical fields AND legacy aliases from the UI:
      location  -> site
      streamUrl -> rtsp_url
      status "offline" -> "inactive"
    rtsp_url is write_only (never leaked in responses).
    """
    # Legacy aliases (write-only, optional)
    location = serializers.CharField(required=False, write_only=True)
    streamUrl = serializers.CharField(required=False, write_only=True)

    is_ai_synced = serializers.SerializerMethodField()

    class Meta:
        model = Camera
        fields = [
            "id", "name", "site", "rtsp_url", "ai_camera_id", "stream_path", "status",
            "camera_type", "source_type", "min_confidence", "min_bbox_area",
            "k_of_n_k", "k_of_n_n", "cooldown_s", "enabled_lanes", "entity_detection_enabled",
            "is_ai_synced",
            "created_at", "updated_at", "tenant",
            # legacy aliases
            "location", "streamUrl",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "tenant"]
        extra_kwargs = {
            "rtsp_url": {"write_only": True, "required": False},
            "site": {"required": False},
            "ai_camera_id": {"required": False},
            "status": {"required": False},
        }

    def validate_status(self, value):
        mapping = {"offline": "inactive", "online": "active"}
        return mapping.get(value, value)

    def get_is_ai_synced(self, obj):
        if hasattr(obj, 'runtime_registration'):
            return obj.runtime_registration.desired_enabled
        from api.models import AIRuntimeRegistration
        reg = AIRuntimeRegistration.objects.filter(camera=obj).first()
        return reg.desired_enabled if reg else False

    def validate(self, attrs):
        # Merge legacy aliases into canonical fields
        if "location" in attrs and not attrs.get("site"):
            attrs["site"] = attrs.pop("location")
        else:
            attrs.pop("location", None)
        if "streamUrl" in attrs and not attrs.get("rtsp_url"):
            attrs["rtsp_url"] = attrs.pop("streamUrl")
        else:
            attrs.pop("streamUrl", None)

        if "rtsp_url" in attrs:
            attrs["rtsp_url"] = sanitize_stream_url(attrs.get("rtsp_url", ""))

        # ── Auto-derive stream_path when empty ──────────────────────
        if not attrs.get("stream_path"):
            # Prefer ai_camera_id, then slugified name, then leave for
            # model.save() to handle with cam_{pk} fallback
            if attrs.get("ai_camera_id"):
                attrs["stream_path"] = attrs["ai_camera_id"]
            elif attrs.get("name"):
                attrs["stream_path"] = slugify(attrs["name"])

        # Keep ai_camera_id aligned with stream_path unless user explicitly set it.
        if not attrs.get("ai_camera_id") and attrs.get("stream_path"):
            attrs["ai_camera_id"] = attrs["stream_path"]

        return attrs

    def to_representation(self, instance):
        """Use CameraSafeSerializer for read (hides rtsp_url)."""
        return CameraSafeSerializer(instance).data

class IncidentSerializer(serializers.ModelSerializer):
    camera_name = serializers.CharField(source="camera.name", read_only=True, default="")
    camera_source_type = serializers.CharField(source="camera.source_type", read_only=True, default=Camera.SourceType.REGISTERED)
    camera_source_label = serializers.SerializerMethodField()

    class Meta:
        model = Incident
        fields = "__all__"
        extra_fields = ["camera_name"]

    def get_field_names(self, declared_fields, info):
        fields = super().get_field_names(declared_fields, info)
        if "camera_name" not in fields:
            fields = list(fields) + ["camera_name"]
        if "camera_source_type" not in fields:
            fields = list(fields) + ["camera_source_type"]
        if "camera_source_label" not in fields:
            fields = list(fields) + ["camera_source_label"]
        return fields

    def get_camera_source_label(self, obj):
        source_type = getattr(getattr(obj, "camera", None), "source_type", Camera.SourceType.REGISTERED)
        return "Webcam" if source_type == Camera.SourceType.WEBCAM else "Registered"

class DetectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Detection
        fields = "__all__"

class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = "__all__"

class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)
    display_title = serializers.SerializerMethodField()
    display_type = serializers.SerializerMethodField()
    display_description = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = "__all__"

    def get_display_title(self, obj):
        return present_audit_log(
            action=obj.action,
            target_type=obj.target_type,
            target_id=obj.target_id,
            meta=obj.meta,
        )["display_title"]

    def get_display_type(self, obj):
        return present_audit_log(
            action=obj.action,
            target_type=obj.target_type,
            target_id=obj.target_id,
            meta=obj.meta,
        )["display_type"]

    def get_display_description(self, obj):
        return present_audit_log(
            action=obj.action,
            target_type=obj.target_type,
            target_id=obj.target_id,
            meta=obj.meta,
        )["display_description"]

class ProfileSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)

    class Meta:
        model = Profile
        fields = [
            "id", "user", "username", "email", "bio",
            "notify_email", "notify_push", "notify_sms",
            "instant_notification_levels",
            "alert_sensitivity", "data_retention_days", "audio_detection",
            "blur_faces", "consent_required",
        ]
        read_only_fields = ["id", "user", "username", "email"]

    def validate_instant_notification_levels(self, value):
        return normalize_instant_notification_levels(value)

class KnownEntitySerializer(serializers.ModelSerializer):
    camera_ids = serializers.PrimaryKeyRelatedField(
        queryset=Camera.objects.all(),
        source="cameras",
        many=True,
        required=False,
        write_only=True,
    )
    cameras = serializers.SerializerMethodField(read_only=True)
    last_camera_id = serializers.IntegerField(source="last_camera.id", read_only=True)

    class Meta:
        model = KnownEntity
        fields = [
            "id", "name", "status", "detection_enabled", "category", "group", "notes",
            "processing_error", "processing_started_at", "processing_completed_at", "ready_at",
            "embedding_version", "entity_detection_notes", "deleted_at",
            "camera_ids", "cameras",
            "ai_entity_id", "thumbnail_url", "last_seen", "last_camera_id",
            "created_by", "updated_by",
            "created_at", "updated_at", "tenant",
        ]
        read_only_fields = [
            "id",
            "status",
            "processing_error",
            "processing_started_at",
            "processing_completed_at",
            "ready_at",
            "embedding_version",
            "deleted_at",
            "ai_entity_id",
            "thumbnail_url",
            "last_seen",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "tenant",
        ]

    def get_cameras(self, obj):
        # Sort in Python to preserve any prefetch_related cache
        cameras = sorted(list(obj.cameras.all()), key=lambda c: c.name)
        return [
            {
                "id": cam.id,
                "name": cam.name,
                "ai_camera_id": cam.ai_camera_id,
            }
            for cam in cameras
        ]

    def validate_camera_ids(self, value):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant is None and self.instance is not None:
            tenant = self.instance.tenant
        if tenant is None:
            return value
        invalid = [cam.id for cam in value if cam.tenant_id != tenant.id]
        if invalid:
            raise serializers.ValidationError(
                f"Cameras not in current tenant: {invalid}"
            )
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get("detection_enabled") is True:
            status_value = self.instance.status if self.instance is not None else KnownEntity.Status.PENDING
            if status_value != KnownEntity.Status.READY:
                raise serializers.ValidationError(
                    {"detection_enabled": "Detection can be enabled only when entity status is READY."}
                )
        return attrs


class InvitationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = ["id", "email", "role"]

    def validate_role(self, value):
        valid = {c[0] for c in Membership.Role.choices}
        if value not in valid:
            raise serializers.ValidationError("Invalid role.")
        return value


class PendingInvitationSerializer(serializers.ModelSerializer):
    tenant = serializers.SerializerMethodField()
    invited_by = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)

    class Meta:
        model = Invitation
        fields = ["id", "tenant", "email", "role", "invited_by", "expires_at"]

    def get_tenant(self, obj):
        return {"id": obj.tenant.id, "name": obj.tenant.name}

    def get_invited_by(self, obj):
        return obj.invited_by.username if obj.invited_by else None


class CameraZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = CameraZone
        fields = ["id", "camera", "zone_name", "zone_type", "polygon_points", "enabled", "created_at", "updated_at"]
        read_only_fields = ["id", "camera", "created_at", "updated_at"]


class NotificationChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationChannel
        fields = [
            "id", "email_enabled", "push_enabled",
            "email_recipients", "fcm_tokens", "severity_threshold",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
