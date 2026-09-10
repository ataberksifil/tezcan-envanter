"""Canonical application role names and catalog permission matrix (Phase 2.3)."""

from __future__ import annotations

from collections.abc import Iterable

TECHNICIAN = "TECHNICIAN"
STOREKEEPER = "STOREKEEPER"
ADMIN_MANAGER = "ADMIN_MANAGER"

ROLE_NAMES: tuple[str, ...] = (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER)

CATALOG_MODELS: tuple[str, ...] = ("category", "unitofmeasure", "material")

CATALOG_ACTIONS: tuple[str, ...] = ("view", "add", "change", "delete")

MANAGED_CATALOG_CODENAMES: frozenset[str] = frozenset(
    f"{action}_{model}" for model in CATALOG_MODELS for action in CATALOG_ACTIONS
)

ROLE_CATALOG_CODENAMES: dict[str, frozenset[str]] = {
    TECHNICIAN: frozenset(f"view_{model}" for model in CATALOG_MODELS),
    STOREKEEPER: frozenset(f"view_{model}" for model in CATALOG_MODELS),
    ADMIN_MANAGER: frozenset(
        f"{action}_{model}"
        for model in CATALOG_MODELS
        for action in ("view", "add", "change")
    ),
}


def expected_catalog_codenames() -> frozenset[str]:
    return MANAGED_CATALOG_CODENAMES


def catalog_codenames_for_role(role_name: str) -> frozenset[str]:
    return ROLE_CATALOG_CODENAMES[role_name]


def is_managed_catalog_codename(codename: str) -> bool:
    return codename in MANAGED_CATALOG_CODENAMES


def managed_catalog_permissions(permissions: Iterable) -> list:
    return [perm for perm in permissions if is_managed_catalog_codename(perm.codename)]


def catalog_view_permission_labels() -> tuple[str, ...]:
    return tuple(f"catalog.view_{model}" for model in CATALOG_MODELS)


def user_has_catalog_view_permission(user) -> bool:
    """Return whether *user* has any catalog view permission.

    Navigation and future catalog views must use Django permissions, not
    group name strings. Hidden links are not authorization.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    return any(
        user.has_perm(permission) for permission in catalog_view_permission_labels()
    )
