from django.contrib import admin

from .models import Tenant, Membership, Camera, Incident, Detection, Alert, AuditLog

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "plan")
    search_fields = ("name",)

@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "tenant", "role", "created_at")
    list_filter = ("role", "tenant")
    search_fields = ("user__username", "tenant__name")
    
admin.site.register(Camera)
admin.site.register(Incident)
admin.site.register(Detection)
admin.site.register(Alert)
admin.site.register(AuditLog)
