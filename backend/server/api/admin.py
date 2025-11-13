from django.contrib import admin

from .models import Tenant, Membership, Camera, Incident, Detection, Alert, AuditLog

admin.site.register(Tenant)
admin.site.register(Membership)
admin.site.register(Camera)
admin.site.register(Incident)
admin.site.register(Detection)
admin.site.register(Alert)
admin.site.register(AuditLog)
