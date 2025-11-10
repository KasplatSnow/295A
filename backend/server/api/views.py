from rest_framework import viewsets, permissions
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from django.db import transaction
from .models import Tenant, Membership, Camera, Incident, Detection, Alert, AuditLog, Profile
from .serializers import (
    TenantSerializer, MembershipSerializer, CameraSafeSerializer, CameraAdminSerializer,
    IncidentSerializer, DetectionSerializer, AlertSerializer, AuditLogSerializer, ProfileSerializer
)

class IsAuthenticatedOrReadOnly(permissions.IsAuthenticatedOrReadOnly):
    pass

def get_active_tenant(request):
    tid = (
        getattr(request, "tenant_id", None)
        or request.headers.get("X-Tenant-ID")
        or request.headers.get("x-tenant-id")
        or request.META.get("HTTP_X_TENANT_ID")
    )
    if not tid:
        raise PermissionDenied("Missing X-Tenant-ID header.")
    try:
        return Tenant.objects.get(pk=tid)
    except Tenant.DoesNotExist:
        raise PermissionDenied("Invalid tenant ID.")

def assert_member(request, tenant):
    if not request.user or not request.user.is_authenticated:
        raise NotAuthenticated()
    return Membership.objects.filter(user=request.user, tenant=tenant).exists()

class TenantScopedViewSet(viewsets.ModelViewSet):
    """
    Base ViewSet that filters by tenant={X-Tenant-ID} and sets tenant on create.
    """
    tenant_field = "tenant"   # override if different

    def get_queryset(self):
        tenant = get_active_tenant(self.request)
        if not assert_member(self.request, tenant):
            raise PermissionDenied("Not a member of this tenant.")
        return super().get_queryset().filter(**{self.tenant_field: tenant})

    def perform_create(self, serializer):
        tenant = get_active_tenant(self.request)
        if not assert_member(self.request, tenant):
            raise PermissionDenied("Not a member of this tenant.")
        serializer.save(**{self.tenant_field: tenant})

class TenantViewSet(viewsets.ModelViewSet):
    serializer_class = TenantSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Tenant.objects.all()
        # return only tenants where the user has a membership
        return Tenant.objects.filter(memberships__user=user).distinct()
    
    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    @transaction.atomic
    def perform_create(self, serializer):
        # no X-Tenant-ID usage here; this is global creation
        tenant = serializer.save()  # creates Tenant(name, plan)
        Membership.objects.get_or_create(
            user=self.request.user,
            tenant=tenant,
            defaults={"role": "owner"},
        )


class MembershipViewSet(TenantScopedViewSet):
    queryset = Membership.objects.all()
    serializer_class = MembershipSerializer
    permission_classes = [permissions.IsAuthenticated]

class CameraViewSet(TenantScopedViewSet):
    queryset = Camera.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        # Use admin serializer only for staff; otherwise hide rtsp_url
        return CameraAdminSerializer if (self.request.user and self.request.user.is_staff) else CameraSafeSerializer

class IncidentViewSet(TenantScopedViewSet):
    queryset = Incident.objects.all()
    serializer_class = IncidentSerializer
    permission_classes = [permissions.IsAuthenticated]

class DetectionViewSet(TenantScopedViewSet):
    queryset = Detection.objects.all()
    serializer_class = DetectionSerializer
    permission_classes = [permissions.IsAuthenticated]

class AlertViewSet(TenantScopedViewSet):
    queryset = Alert.objects.all()
    serializer_class = AlertSerializer
    permission_classes = [permissions.IsAuthenticated]

class AuditLogViewSet(TenantScopedViewSet):
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

class ProfileViewSet(viewsets.ModelViewSet):
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Users can only access their own profile
        return Profile.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)
