from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.roles import (
    ADMIN_MANAGER,
    DEFAULT_ROLE_NAMES,
    DEFAULT_ROLE_TEMPLATES,
    STOREKEEPER,
    TECHNICIAN,
    MANAGE_ACCESS_PERMISSION,
    catalog_codenames_for_role,
    required_template_catalog_codenames,
)
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog_permissions():
    content_types = ContentType.objects.filter(
        app_label__in=("catalog", "locations", "accounts", "inventory"),
        model__in=(
            "category",
            "unitofmeasure",
            "material",
            "location",
            "employee",
            "productionline",
            "inventorytransaction",
        ),
    )
    permissions = Permission.objects.filter(
        content_type__in=content_types,
        codename__in=required_template_catalog_codenames(),
    )
    return {permission.codename: permission for permission in permissions}


def _template_codenames_for_group(group_name: str) -> set[str]:
    group = Group.objects.get(name=group_name)
    allowed = required_template_catalog_codenames()
    return {
        permission.codename
        for permission in group.permissions.filter(
            content_type__app_label__in=(
                "catalog",
                "locations",
                "accounts",
                "inventory",
            )
        )
        if permission.codename in allowed
    }


def _permission_pks_for_group(group_name: str) -> set[int]:
    group = Group.objects.get(name=group_name)
    return set(group.permissions.values_list("pk", flat=True))


def _run_setup_roles(*, verbosity: int = 0) -> str:
    stdout = StringIO()
    call_command("setup_roles", verbosity=verbosity, stdout=stdout)
    return stdout.getvalue()


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


def test_empty_db_creates_exactly_the_three_default_groups():
    assert Group.objects.count() == 0

    _run_setup_roles()

    assert set(Group.objects.values_list("name", flat=True)) == set(DEFAULT_ROLE_NAMES)
    assert Group.objects.count() == 3


@pytest.mark.parametrize("role_name", DEFAULT_ROLE_NAMES)
def test_newly_created_groups_receive_initial_template_permissions(role_name):
    _run_setup_roles()
    assert _template_codenames_for_group(role_name) == set(
        catalog_codenames_for_role(role_name)
    )


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_new_view_only_templates_receive_no_catalog_write_or_delete_permissions(
    role_name,
):
    _run_setup_roles()
    codenames = _template_codenames_for_group(role_name)
    assert not any(codename.startswith("add_") for codename in codenames)
    assert not any(codename.startswith("change_") for codename in codenames)
    assert not any(codename.startswith("delete_") for codename in codenames)
    assert "view_location" in codenames
    assert "view_employee" in codenames
    assert "add_employee" not in codenames
    assert "change_employee" not in codenames


def test_new_admin_manager_receives_catalog_view_add_change_not_delete():
    _run_setup_roles()
    codenames = _template_codenames_for_group(ADMIN_MANAGER)
    assert codenames == set(DEFAULT_ROLE_TEMPLATES[ADMIN_MANAGER])
    assert not any(codename.startswith("delete_") for codename in codenames)
    assert {"view_location", "add_location", "change_location"} <= codenames
    assert {
        "view_employee",
        "add_employee",
        "change_employee",
    } <= codenames
    assert "delete_employee" not in codenames
    assert {
        "view_productionline",
        "add_productionline",
        "change_productionline",
    } <= codenames
    assert "delete_productionline" not in codenames


def test_running_twice_creates_no_duplicate_groups_and_no_permission_changes():
    first_output = _run_setup_roles(verbosity=1)
    first_state = {
        role: _permission_pks_for_group(role) for role in DEFAULT_ROLE_NAMES
    }
    first_count = Group.objects.count()

    second_output = _run_setup_roles(verbosity=1)
    second_state = {
        role: _permission_pks_for_group(role) for role in DEFAULT_ROLE_NAMES
    }

    assert first_count == 3
    assert Group.objects.count() == 3
    assert first_state == second_state
    assert "created: ADMIN_MANAGER, STOREKEEPER, TECHNICIAN" in first_output
    assert "preserved existing: (none)" in first_output
    assert "created: (none)" in second_output
    assert "preserved existing: ADMIN_MANAGER, STOREKEEPER, TECHNICIAN" in second_output


def test_existing_technician_extra_catalog_write_permission_is_preserved(
    catalog_permissions,
):
    _run_setup_roles()
    technician = Group.objects.get(name=TECHNICIAN)
    technician.permissions.add(catalog_permissions["add_category"])
    before = _permission_pks_for_group(TECHNICIAN)

    _run_setup_roles()

    assert _permission_pks_for_group(TECHNICIAN) == before
    assert "add_category" in _template_codenames_for_group(TECHNICIAN)


def test_existing_technician_removed_view_permission_is_not_restored(
    catalog_permissions,
):
    _run_setup_roles()
    technician = Group.objects.get(name=TECHNICIAN)
    technician.permissions.remove(catalog_permissions["view_material"])
    before = _permission_pks_for_group(TECHNICIAN)

    _run_setup_roles()

    assert _permission_pks_for_group(TECHNICIAN) == before
    assert "view_material" not in _template_codenames_for_group(TECHNICIAN)


def test_existing_storekeeper_customized_catalog_permissions_are_preserved(
    catalog_permissions,
):
    _run_setup_roles()
    storekeeper = Group.objects.get(name=STOREKEEPER)
    storekeeper.permissions.remove(catalog_permissions["view_category"])
    storekeeper.permissions.add(
        catalog_permissions["add_material"],
        catalog_permissions["change_unitofmeasure"],
    )
    before = _permission_pks_for_group(STOREKEEPER)

    _run_setup_roles()

    assert _permission_pks_for_group(STOREKEEPER) == before
    assert _template_codenames_for_group(STOREKEEPER) == {
        "view_unitofmeasure",
        "view_material",
        "view_location",
        "view_employee",
        "add_material",
        "change_unitofmeasure",
        "receive_stock",
        "issue_stock",
    }


def test_existing_admin_manager_customized_catalog_permissions_are_preserved(
    catalog_permissions,
):
    _run_setup_roles()
    admin_manager = Group.objects.get(name=ADMIN_MANAGER)
    admin_manager.permissions.remove(catalog_permissions["change_material"])
    delete_category = Permission.objects.get(
        content_type__app_label="catalog",
        codename="delete_category",
    )
    admin_manager.permissions.add(delete_category)
    before = _permission_pks_for_group(ADMIN_MANAGER)

    _run_setup_roles()

    assert _permission_pks_for_group(ADMIN_MANAGER) == before
    assert "change_material" not in _template_codenames_for_group(ADMIN_MANAGER)
    assert admin_manager.permissions.filter(codename="delete_category").exists()


def test_existing_group_non_catalog_permission_is_preserved_and_not_filled():
    auth_content_type = ContentType.objects.get_for_model(get_user_model())
    auth_permission = Permission.objects.filter(content_type=auth_content_type).first()
    assert auth_permission is not None

    technician = Group.objects.create(name=TECHNICIAN)
    technician.permissions.add(auth_permission)
    before = _permission_pks_for_group(TECHNICIAN)

    _run_setup_roles()

    assert _permission_pks_for_group(TECHNICIAN) == before
    assert technician.permissions.filter(pk=auth_permission.pk).exists()
    assert _template_codenames_for_group(TECHNICIAN) == set()


def test_mixed_state_creates_only_missing_default_group_with_template_permissions(
    catalog_permissions,
):
    technician = Group.objects.create(name=TECHNICIAN)
    technician.permissions.add(
        catalog_permissions["view_category"],
        catalog_permissions["add_category"],
    )
    admin_manager = Group.objects.create(name=ADMIN_MANAGER)
    admin_manager.permissions.add(catalog_permissions["view_material"])
    technician_before = _permission_pks_for_group(TECHNICIAN)
    admin_before = _permission_pks_for_group(ADMIN_MANAGER)

    output = _run_setup_roles(verbosity=1)

    assert _permission_pks_for_group(TECHNICIAN) == technician_before
    assert _permission_pks_for_group(ADMIN_MANAGER) == admin_before
    assert Group.objects.filter(name=STOREKEEPER).exists()
    assert _template_codenames_for_group(STOREKEEPER) == set(
        catalog_codenames_for_role(STOREKEEPER)
    )
    assert "created: STOREKEEPER" in output
    assert "preserved existing: ADMIN_MANAGER, TECHNICIAN" in output


def test_missing_expected_permission_raises_before_any_partial_group_creation(
    catalog_permissions,
):
    technician = Group.objects.create(name=TECHNICIAN)
    technician.permissions.add(catalog_permissions["view_category"])
    technician_before = _permission_pks_for_group(TECHNICIAN)
    Permission.objects.filter(
        content_type__app_label="catalog",
        codename="view_material",
    ).delete()

    with pytest.raises(CommandError, match="Missing expected template permissions"):
        _run_setup_roles()

    assert _permission_pks_for_group(TECHNICIAN) == technician_before
    assert not Group.objects.filter(name=STOREKEEPER).exists()
    assert not Group.objects.filter(name=ADMIN_MANAGER).exists()
    assert set(Group.objects.values_list("name", flat=True)) == {TECHNICIAN}


def test_existing_admin_manager_without_production_line_permissions_is_not_reconciled(
    catalog_permissions,
):
    _run_setup_roles()
    admin_manager = Group.objects.get(name=ADMIN_MANAGER)
    admin_manager.permissions.remove(catalog_permissions["view_productionline"])
    admin_manager.permissions.remove(catalog_permissions["add_productionline"])
    admin_manager.permissions.remove(catalog_permissions["change_productionline"])
    before = _permission_pks_for_group(ADMIN_MANAGER)

    _run_setup_roles()

    assert _permission_pks_for_group(ADMIN_MANAGER) == before
    assert "view_productionline" not in _template_codenames_for_group(ADMIN_MANAGER)


def test_existing_admin_manager_without_location_permissions_is_not_reconciled(
    catalog_permissions,
):
    _run_setup_roles()
    admin_manager = Group.objects.get(name=ADMIN_MANAGER)
    admin_manager.permissions.remove(catalog_permissions["view_location"])
    admin_manager.permissions.remove(catalog_permissions["add_location"])
    admin_manager.permissions.remove(catalog_permissions["change_location"])
    before = _permission_pks_for_group(ADMIN_MANAGER)

    _run_setup_roles()

    assert _permission_pks_for_group(ADMIN_MANAGER) == before
    assert "view_location" not in _template_codenames_for_group(ADMIN_MANAGER)


def test_setup_roles_does_not_create_audit_events():
    assert AuditEvent.objects.count() == 0


def test_setup_roles_never_injects_manage_access_and_leaves_custom_roles_untouched():
    custom = Group.objects.create(name="CUSTOM")
    manage_access = Permission.objects.get(
        content_type__app_label="accounts", codename="manage_access"
    )
    custom.permissions.add(manage_access)
    before = _permission_pks_for_group(custom.name)

    _run_setup_roles()

    assert _permission_pks_for_group(custom.name) == before
    for role_name in DEFAULT_ROLE_NAMES:
        assert not Group.objects.get(name=role_name).permissions.filter(
            content_type__app_label="accounts", codename="manage_access"
        ).exists()
    assert AuditEvent.objects.count() == 0
    _run_setup_roles()
    assert AuditEvent.objects.count() == 0


def test_new_technician_template_does_not_receive_receive_stock():
    _run_setup_roles()
    assert "receive_stock" not in _template_codenames_for_group(TECHNICIAN)


def test_new_storekeeper_and_admin_manager_templates_receive_receive_stock():
    _run_setup_roles()
    assert "receive_stock" in _template_codenames_for_group(STOREKEEPER)
    assert "receive_stock" in _template_codenames_for_group(ADMIN_MANAGER)


def test_existing_storekeeper_does_not_gain_receive_stock_on_rerun(catalog_permissions):
    _run_setup_roles()
    storekeeper = Group.objects.get(name=STOREKEEPER)
    storekeeper.permissions.remove(catalog_permissions["receive_stock"])
    before = _permission_pks_for_group(STOREKEEPER)

    _run_setup_roles()

    assert _permission_pks_for_group(STOREKEEPER) == before
    assert "receive_stock" not in _template_codenames_for_group(STOREKEEPER)


def test_existing_admin_manager_does_not_gain_receive_stock_on_rerun(catalog_permissions):
    _run_setup_roles()
    admin_manager = Group.objects.get(name=ADMIN_MANAGER)
    admin_manager.permissions.remove(catalog_permissions["receive_stock"])
    before = _permission_pks_for_group(ADMIN_MANAGER)

    _run_setup_roles()

    assert _permission_pks_for_group(ADMIN_MANAGER) == before
    assert "receive_stock" not in _template_codenames_for_group(ADMIN_MANAGER)


def test_new_technician_storekeeper_and_admin_manager_templates_receive_issue_stock():
    _run_setup_roles()
    assert "issue_stock" in _template_codenames_for_group(TECHNICIAN)
    assert "issue_stock" in _template_codenames_for_group(STOREKEEPER)
    assert "issue_stock" in _template_codenames_for_group(ADMIN_MANAGER)


def test_existing_technician_does_not_gain_issue_stock_on_rerun(catalog_permissions):
    _run_setup_roles()
    technician = Group.objects.get(name=TECHNICIAN)
    technician.permissions.remove(catalog_permissions["issue_stock"])
    before = _permission_pks_for_group(TECHNICIAN)

    _run_setup_roles()

    assert _permission_pks_for_group(TECHNICIAN) == before
    assert "issue_stock" not in _template_codenames_for_group(TECHNICIAN)


def test_existing_storekeeper_does_not_gain_issue_stock_on_rerun(catalog_permissions):
    _run_setup_roles()
    storekeeper = Group.objects.get(name=STOREKEEPER)
    storekeeper.permissions.remove(catalog_permissions["issue_stock"])
    before = _permission_pks_for_group(STOREKEEPER)

    _run_setup_roles()

    assert _permission_pks_for_group(STOREKEEPER) == before
    assert "issue_stock" not in _template_codenames_for_group(STOREKEEPER)


def test_existing_admin_manager_does_not_gain_issue_stock_on_rerun(catalog_permissions):
    _run_setup_roles()
    admin_manager = Group.objects.get(name=ADMIN_MANAGER)
    admin_manager.permissions.remove(catalog_permissions["issue_stock"])
    before = _permission_pks_for_group(ADMIN_MANAGER)

    _run_setup_roles()

    assert _permission_pks_for_group(ADMIN_MANAGER) == before
    assert "issue_stock" not in _template_codenames_for_group(ADMIN_MANAGER)


def test_ordinary_user_has_perm_follows_actual_group_permissions_after_customization(
    catalog_permissions,
):
    _run_setup_roles()
    technician = Group.objects.get(name=TECHNICIAN)
    technician.permissions.add(catalog_permissions["add_category"])
    technician.permissions.remove(catalog_permissions["view_unitofmeasure"])

    _run_setup_roles()
    user = _create_user("custom-technician", TECHNICIAN)
    user = _refresh_user_permissions(user)

    assert user.has_perm("catalog.view_category") is True
    assert user.has_perm("catalog.add_category") is True
    assert user.has_perm("catalog.view_unitofmeasure") is False
    assert user.has_perm("catalog.change_category") is False


@pytest.mark.parametrize(
    ("role_name", "permission_codename", "expected"),
    [
        (TECHNICIAN, "catalog.view_category", True),
        (TECHNICIAN, "catalog.add_category", False),
        (STOREKEEPER, "catalog.view_material", True),
        (STOREKEEPER, "catalog.add_material", False),
        (ADMIN_MANAGER, "catalog.view_unitofmeasure", True),
        (ADMIN_MANAGER, "catalog.add_unitofmeasure", True),
        (ADMIN_MANAGER, "catalog.change_unitofmeasure", True),
        (ADMIN_MANAGER, "catalog.delete_unitofmeasure", False),
    ],
)
def test_new_template_group_membership_resolves_has_perm(
    role_name, permission_codename, expected
):
    _run_setup_roles()
    user = _create_user(f"user-{role_name.lower()}", role_name)
    user = _refresh_user_permissions(user)
    assert user.has_perm(permission_codename) is expected
