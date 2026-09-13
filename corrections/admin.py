from django.contrib import admin

from corrections.models import CorrectionRequest


@admin.register(CorrectionRequest)
class CorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "requester", "requested_at", "decided_by")
    readonly_fields = tuple(
        field.name for field in CorrectionRequest._meta.concrete_fields
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
