from django.contrib import admin

from audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "event_type",
        "entity_type",
        "entity_id",
        "actor",
    )
    list_filter = ("event_type", "entity_type", "occurred_at", "actor")
    search_fields = ("event_type", "entity_type", "entity_id", "actor__username")
    readonly_fields = (
        "id",
        "event_type",
        "actor",
        "occurred_at",
        "entity_type",
        "entity_id",
        "before_data",
        "after_data",
        "metadata",
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
