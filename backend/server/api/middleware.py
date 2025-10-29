from django.utils.deprecation import MiddlewareMixin

class ActiveTenantMiddleware(MiddlewareMixin):
    """
    Middleware that extracts the X-Tenant-ID header from every request
    and stores it on the request object for easy access throughout the app.
    """

    def process_request(self, request):
        # Look for the header in both common cases and META fallback
        tid = (
            request.headers.get("X-Tenant-ID")
            or request.headers.get("x-tenant-id")
            or request.META.get("HTTP_X_TENANT_ID")
        )
        request.tenant_id = tid
