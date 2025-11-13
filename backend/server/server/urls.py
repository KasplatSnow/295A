from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from api.views import (
    TenantViewSet, MembershipViewSet, CameraViewSet, IncidentViewSet,
    DetectionViewSet, AlertViewSet, AuditLogViewSet
)

from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from api.auth_views import RegisterView


router = DefaultRouter()
router.register(r"tenants", TenantViewSet, basename="tenant")
router.register(r"memberships", MembershipViewSet, basename="membership")
router.register(r"cameras", CameraViewSet, basename="camera")
router.register(r"incidents", IncidentViewSet, basename="incident")
router.register(r"detections", DetectionViewSet, basename="detection")
router.register(r"alerts", AlertViewSet, basename="alert")
router.register(r"audit", AuditLogViewSet, basename="audit")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    # keep your JWT endpoints here if you've added them
]

urlpatterns += [
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/register/", RegisterView.as_view(), name="register"),
]