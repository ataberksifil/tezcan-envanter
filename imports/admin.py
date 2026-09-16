from django.contrib import admin

from imports.models import (
    InventoryBaseline,
    InventoryBaselineCountSessionLink,
    InventoryBaselineTransactionLink,
)


class _ImmutableBaselineAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryBaseline)
class InventoryBaselineAdmin(_ImmutableBaselineAdmin):
    list_display = ("reference", "status", "created_by", "established_by", "established_at")
    readonly_fields = tuple(
        field.name for field in InventoryBaseline._meta.concrete_fields
    )


@admin.register(InventoryBaselineCountSessionLink)
class InventoryBaselineCountSessionLinkAdmin(_ImmutableBaselineAdmin):
    readonly_fields = tuple(
        field.name for field in InventoryBaselineCountSessionLink._meta.concrete_fields
    )


@admin.register(InventoryBaselineTransactionLink)
class InventoryBaselineTransactionLinkAdmin(_ImmutableBaselineAdmin):
    readonly_fields = tuple(
        field.name for field in InventoryBaselineTransactionLink._meta.concrete_fields
    )
