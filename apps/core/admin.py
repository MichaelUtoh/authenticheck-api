from django.contrib import admin


class TenantScopedAdmin(admin.ModelAdmin):
    """Admin base for tenant-scoped models. Django admin is platform-staff only, so it uses `unscoped`."""

    def get_queryset(self, request):
        qs = self.model.unscoped.get_queryset()
        ordering = self.get_ordering(request)
        return qs.order_by(*ordering) if ordering else qs
