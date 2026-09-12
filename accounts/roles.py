"""Default role templates and managed permission helpers (Phase 2.5C / Phase 3.1)."""

from __future__ import annotations

from collections.abc import Iterable

TECHNICIAN = "TECHNICIAN"
STOREKEEPER = "STOREKEEPER"
ADMIN_MANAGER = "ADMIN_MANAGER"

CATALOG_MODELS: tuple[str, ...] = ("category", "unitofmeasure", "material")

CATALOG_ACTIONS: tuple[str, ...] = ("view", "add", "change", "delete")

MANAGED_CATALOG_CODENAMES: frozenset[str] = frozenset(
    f"{action}_{model}" for model in CATALOG_MODELS for action in CATALOG_ACTIONS
)

MANAGED_LOCATION_CODENAMES: frozenset[str] = frozenset(
    ("view_location", "add_location", "change_location")
)

MANAGED_PRODUCTIONLINE_CODENAMES: frozenset[str] = frozenset(
    ("view_productionline", "add_productionline", "change_productionline")
)

_VIEW_ONLY_CATALOG = frozenset(f"view_{model}" for model in CATALOG_MODELS)
_VIEW_ADD_CHANGE_CATALOG = frozenset(
    f"{action}_{model}"
    for model in CATALOG_MODELS
    for action in ("view", "add", "change")
)

_VIEW_ONLY_EMPLOYEE = frozenset({"view_employee"})
_VIEW_ADD_CHANGE_EMPLOYEE = frozenset(
    {"view_employee", "add_employee", "change_employee"}
)
_VIEW_ADD_CHANGE_PRODUCTIONLINE = frozenset(
    {"view_productionline", "add_productionline", "change_productionline"}
)
_RECEIVE_STOCK = frozenset({"receive_stock"})

DEFAULT_ROLE_TEMPLATES: dict[str, frozenset[str]] = {
    TECHNICIAN: _VIEW_ONLY_CATALOG | frozenset({"view_location"}) | _VIEW_ONLY_EMPLOYEE,
    STOREKEEPER: _VIEW_ONLY_CATALOG
    | frozenset({"view_location"})
    | _VIEW_ONLY_EMPLOYEE
    | _RECEIVE_STOCK,
    ADMIN_MANAGER: _VIEW_ADD_CHANGE_CATALOG
    | frozenset({"view_location", "add_location", "change_location"})
    | _VIEW_ADD_CHANGE_EMPLOYEE
    | _VIEW_ADD_CHANGE_PRODUCTIONLINE
    | _RECEIVE_STOCK,
}

DEFAULT_ROLE_NAMES: tuple[str, ...] = (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER)
RESERVED_ROLE_NAMES: frozenset[str] = frozenset(DEFAULT_ROLE_NAMES)

# DEC-022 / DEC-023: explicit allowlist. Must never expand merely because
# another permission is added to Django.
SAFE_CATALOG_PERMISSION_LABELS: tuple[str, ...] = (
    "catalog.view_category",
    "catalog.add_category",
    "catalog.change_category",
    "catalog.view_unitofmeasure",
    "catalog.add_unitofmeasure",
    "catalog.change_unitofmeasure",
    "catalog.view_material",
    "catalog.add_material",
    "catalog.change_material",
    "locations.view_location",
    "locations.add_location",
    "locations.change_location",
    "accounts.view_employee",
    "accounts.add_employee",
    "accounts.change_employee",
    "inventory.view_productionline",
    "inventory.add_productionline",
    "inventory.change_productionline",
    "inventory.receive_stock",
)
SAFE_CATALOG_PERMISSION_SET: frozenset[str] = frozenset(
    SAFE_CATALOG_PERMISSION_LABELS
)
MANAGE_ACCESS_PERMISSION = "accounts.manage_access"
SUPPORTED_ACCESS_PERMISSION_SET: frozenset[str] = (
    SAFE_CATALOG_PERMISSION_SET | {MANAGE_ACCESS_PERMISSION}
)

# Backward-compatible aliases. Runtime authorization uses permissions, not group names.
ROLE_NAMES = DEFAULT_ROLE_NAMES
ROLE_CATALOG_CODENAMES = DEFAULT_ROLE_TEMPLATES


def expected_catalog_codenames() -> frozenset[str]:
    return MANAGED_CATALOG_CODENAMES


def required_template_catalog_codenames() -> frozenset[str]:
    return frozenset().union(*DEFAULT_ROLE_TEMPLATES.values())


def catalog_codenames_for_role(role_name: str) -> frozenset[str]:
    return DEFAULT_ROLE_TEMPLATES[role_name]


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
