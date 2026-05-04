from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject
from django.conf import settings
from django.db import connection

class ActiveTenantMiddleware(MiddlewareMixin):
    """
    Middleware that extracts the X-Tenant-ID header and provides
    lazy access to the Tenant and Membership objects on the request.
    """

    def process_request(self, request):
        # 1. Extract Tenant ID from headers
        tid = (
            request.headers.get("X-Tenant-ID")
            or request.headers.get("x-tenant-id")
            or request.META.get("HTTP_X_TENANT_ID")
        )
        request.tenant_id = tid

        # 2. Provide lazy membership resolution
        def get_membership():
            if not request.user.is_authenticated or not request.tenant_id:
                return None
            from api.models import Membership
            return Membership.objects.filter(
                user=request.user,
                tenant_id=request.tenant_id
            ).select_related("tenant").first()

        request.membership = SimpleLazyObject(get_membership)

        # 3. Provide lazy tenant resolution
        def get_tenant():
            # Try to get from membership first (efficient, uses select_related)
            m = request.membership
            if m:
                return m.tenant
            
            # Fallback to direct lookup if tid exists but no membership (e.g. superuser)
            if request.tenant_id:
                from api.models import Tenant
                try:
                    return Tenant.objects.get(pk=request.tenant_id)
                except Tenant.DoesNotExist:
                    pass
            return None

        request.tenant = SimpleLazyObject(get_tenant)

class QueryCountMiddleware(MiddlewareMixin):
    """
    Middleware that prints the total number of DB queries executed during a request
    when running in DEBUG mode. Helps track down N+1 problems.
    """
    def process_response(self, request, response):
        if getattr(settings, "DEBUG", False):
            queries = len(connection.queries)
            path = request.path
            if queries > 0:
                print(f"[QueryCount] {path} executed {queries} database queries")
        
        return response
