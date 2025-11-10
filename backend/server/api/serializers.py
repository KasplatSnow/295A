from rest_framework import serializers
from .models import Tenant, Membership, Camera, Incident, Detection, Alert, AuditLog, Profile

class TenantSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Tenant
        fields = ["id", "name", "plan", "role", "created_at", "updated_at"]

    def get_role(self, obj):
        user = self.context["request"].user
        if user.is_superuser:
            return "owner"
        m = Membership.objects.filter(user=user, tenant=obj).first()
        return m.role if m else None

class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = "__all__"

class CameraSafeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Camera
        exclude = ("rtsp_url",)  # don’t leak RTSP in public responses

class CameraAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Camera
        fields = "__all__"

class IncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Incident
        fields = "__all__"

class DetectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Detection
        fields = "__all__"

class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = "__all__"

class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = "__all__"

class ProfileSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Profile
        fields = ["id", "user", "tenant", "profile_pic", "created_at"]
        read_only_fields = ["id", "user", "created_at"]