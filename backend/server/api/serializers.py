from rest_framework import serializers
from .models import Tenant, Membership, Camera, Incident, Detection, Alert, AuditLog

class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = "__all__"

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
