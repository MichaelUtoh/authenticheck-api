from django.contrib import admin

from .models import Membership, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "trading_name", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("legal_name", "trading_name")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "is_active", "joined_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__email", "tenant__legal_name")
    raw_id_fields = ("user", "tenant", "invited_by")
