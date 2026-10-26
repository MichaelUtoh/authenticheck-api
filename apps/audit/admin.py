from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: nobody edits or deletes the audit trail through the admin."""

    list_display = ("occurred_at", "action", "actor", "tenant", "target_type", "target_id")
    list_filter = ("action",)
    search_fields = ("action", "target_id", "request_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
