"""Management hub access predicates (Phase 2.9A)."""

from __future__ import annotations


def _has_actionable_surface(
    user,
    *,
    view_permission: str,
    add_permission: str,
    change_permission: str,
) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return user.has_perm(view_permission) and (
        user.has_perm(add_permission) or user.has_perm(change_permission)
    )


def user_can_manage_categories(user) -> bool:
    return _has_actionable_surface(
        user,
        view_permission="catalog.view_category",
        add_permission="catalog.add_category",
        change_permission="catalog.change_category",
    )


def user_can_manage_units(user) -> bool:
    return _has_actionable_surface(
        user,
        view_permission="catalog.view_unitofmeasure",
        add_permission="catalog.add_unitofmeasure",
        change_permission="catalog.change_unitofmeasure",
    )


def user_can_manage_materials(user) -> bool:
    return _has_actionable_surface(
        user,
        view_permission="catalog.view_material",
        add_permission="catalog.add_material",
        change_permission="catalog.change_material",
    )


def user_has_management_access(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and user.has_perm("accounts.manage_access")
    ) or (
        user_can_manage_categories(user)
        or user_can_manage_units(user)
        or user_can_manage_materials(user)
    )
