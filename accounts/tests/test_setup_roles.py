from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.management.commands.setup_roles import Command as SetupRolesCommand

from accounts.roles import (
    ADMIN_MANAGER,
    MANAGED_CATALOG_CODENAMES,
    ROLE_CATALOG_CODENAMES,
    ROLE_NAMES,
    STOREKEEPER,
    TECHNICIAN,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog_permissions():
    content_types = ContentType.objects.filter(
        app_label="catalog",
        model__in=("category", "unitofmeasure", "material"),
    )
    permissions = Permission.objects.filter(
        content_type__in=content_types,
        codename__in=MANAGED_CATALOG_CODENAMES,
    )
    return {permission.codename: permission for permission in permissions}


def _catalog_codenames_for_group(group_name: str) -> set[str]:
    group = Group.objects.get(name=group_name)
    return {
        permission.codename
        for permission in group.permissions.filter(content_type__app_label="catalog")
        if permission.codename in MANAGED_CATALOG_CODENAMES
    }


def _run_setup_roles():
    call_command("setup_roles", verbosity=0)


def _create_user(username: str, group_name: str):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=username,
        password="synthetic-test-password-only",
    )
    user.groups.add(Group.objects.get(name=group_name))
    return user


def _refresh_user_permissions(user):
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def test_setup_roles_creates_exactly_three_groups():
    _run_setup_roles()
    group_names = set(Group.objects.values_list("name", flat=True))
    assert {TECHNICIAN, STOREKEEPER, ADMIN_MANAGER}.issubset(group_names)


def test_setup_roles_is_idempotent(catalog_permissions):
    _run_setup_roles()
    first_state = {
        role: _catalog_codenames_for_group(role) for role in ROLE_NAMES
    }

    _run_setup_roles()
    second_state = {
        role: _catalog_codenames_for_group(role) for role in ROLE_NAMES
    }

    assert first_state == second_state


@pytest.mark.parametrize("role_name", ROLE_NAMES)
def test_all_groups_receive_catalog_view_permissions(role_name):
    _run_setup_roles()
    codenames = _catalog_codenames_for_group(role_name)
    assert codenames >= {
        "view_category",
        "view_unitofmeasure",
        "view_material",
    }


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_roles_receive_no_catalog_write_or_delete_permissions(role_name):
    _run_setup_roles()
    codenames = _catalog_codenames_for_group(role_name)
    assert not any(codename.startswith("add_") for codename in codenames)
    assert not any(codename.startswith("change_") for codename in codenames)
    assert not any(codename.startswith("delete_") for codename in codenames)


def test_admin_manager_receives_catalog_view_add_change_permissions():
    _run_setup_roles()
    codenames = _catalog_codenames_for_group(ADMIN_MANAGER)
    assert codenames == set(ROLE_CATALOG_CODENAMES[ADMIN_MANAGER])


def test_admin_manager_receives_no_catalog_delete_permissions():
    _run_setup_roles()
    codenames = _catalog_codenames_for_group(ADMIN_MANAGER)
    assert not any(codename.startswith("delete_") for codename in codenames)


def test_rerun_removes_accidental_catalog_write_permissions_from_view_only_roles(
    catalog_permissions,
):
    _run_setup_roles()

    technician = Group.objects.get(name=TECHNICIAN)
    storekeeper = Group.objects.get(name=STOREKEEPER)
    technician.permissions.add(
        catalog_permissions["add_category"],
        catalog_permissions["change_material"],
    )
    storekeeper.permissions.add(
        catalog_permissions["add_unitofmeasure"],
        catalog_permissions["change_category"],
    )

    _run_setup_roles()

    assert _catalog_codenames_for_group(TECHNICIAN) == set(
        ROLE_CATALOG_CODENAMES[TECHNICIAN]
    )
    assert _catalog_codenames_for_group(STOREKEEPER) == set(
        ROLE_CATALOG_CODENAMES[STOREKEEPER]
    )


def test_rerun_removes_managed_catalog_delete_permissions_from_every_group(
    catalog_permissions,
):
    _run_setup_roles()

    for role_name in ROLE_NAMES:
        group = Group.objects.get(name=role_name)
        group.permissions.add(
            catalog_permissions["delete_category"],
            catalog_permissions["delete_unitofmeasure"],
            catalog_permissions["delete_material"],
        )

    _run_setup_roles()

    for role_name in ROLE_NAMES:
        codenames = _catalog_codenames_for_group(role_name)
        assert not any(codename.startswith("delete_") for codename in codenames)


def test_non_catalog_permissions_remain_untouched(catalog_permissions):
    auth_content_type = ContentType.objects.get_for_model(get_user_model())
    auth_permission = Permission.objects.filter(content_type=auth_content_type).first()
    assert auth_permission is not None

    technician = Group.objects.create(name=TECHNICIAN)
    technician.permissions.add(auth_permission)

    _run_setup_roles()

    technician.refresh_from_db()
    assert technician.permissions.filter(pk=auth_permission.pk).exists()
    assert _catalog_codenames_for_group(TECHNICIAN) == set(
        ROLE_CATALOG_CODENAMES[TECHNICIAN]
    )


def test_missing_catalog_permission_raises_command_error_without_partial_changes(
    catalog_permissions,
):
    _run_setup_roles()

    technician = Group.objects.get(name=TECHNICIAN)
    technician.permissions.add(
        catalog_permissions["add_category"],
        catalog_permissions["change_material"],
    )
    accidental_codenames = _catalog_codenames_for_group(TECHNICIAN)

    with patch.object(
        SetupRolesCommand,
        "_load_catalog_permissions",
        side_effect=CommandError("Missing expected catalog permissions"),
    ):
        with pytest.raises(CommandError, match="Missing expected catalog permissions"):
            call_command("setup_roles", verbosity=0)

    assert _catalog_codenames_for_group(TECHNICIAN) == accidental_codenames


@pytest.mark.parametrize(
    ("role_name", "permission_codename", "expected"),
    [
        (TECHNICIAN, "catalog.view_category", True),
        (TECHNICIAN, "catalog.add_category", False),
        (TECHNICIAN, "catalog.change_category", False),
        (TECHNICIAN, "catalog.delete_category", False),
        (STOREKEEPER, "catalog.view_material", True),
        (STOREKEEPER, "catalog.add_material", False),
        (STOREKEEPER, "catalog.change_material", False),
        (STOREKEEPER, "catalog.delete_material", False),
        (ADMIN_MANAGER, "catalog.view_unitofmeasure", True),
        (ADMIN_MANAGER, "catalog.add_unitofmeasure", True),
        (ADMIN_MANAGER, "catalog.change_unitofmeasure", True),
        (ADMIN_MANAGER, "catalog.delete_unitofmeasure", False),
    ],
)
def test_group_membership_resolves_has_perm(role_name, permission_codename, expected):
    _run_setup_roles()
    user = _create_user(f"user-{role_name.lower()}", role_name)
    user = _refresh_user_permissions(user)
    assert user.has_perm(permission_codename) is expected
