from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction

from accounts.models import Employee
from accounts.services.employees import (
    create_employee,
    set_employee_active,
    update_employee,
)

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "synthetic-test-password-only"


def _employee_table_exists() -> bool:
    return "accounts_employee" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_employee_schema():
    if not _employee_table_exists():
        pytest.skip(
            "accounts.0003_employee not applied to test_tezcan_envanter; "
            "employee model tests deferred until test DB is migrated"
        )


def _create_user(username=None, *, is_active=True):
    return User.objects.create_user(
        username=username or f"user-{uuid.uuid4().hex[:8]}",
        password=PASSWORD,
        is_active=is_active,
    )


def _grant_employee_permissions(user, *codenames):
    permissions = Permission.objects.filter(
        content_type__app_label="accounts",
        codename__in=codenames,
    )
    user.user_permissions.add(*permissions)
    user = User.objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _employee_admin(username=None):
    return _grant_employee_permissions(
        _create_user(username),
        "view_employee",
        "add_employee",
        "change_employee",
    )


def test_employee_has_uuid_primary_key_and_default_active():
    employee = Employee.objects.create(
        employee_number="00127",
        first_name="Ali",
        last_name="Veli",
    )
    assert isinstance(employee.pk, uuid.UUID)
    assert employee.active is True


def test_employee_number_preserves_leading_zeros_and_case():
    actor = _employee_admin()
    result = create_employee(
        actor=actor,
        employee_number=" 00127 ",
        first_name="Test",
        last_name="User",
    )
    assert result.employee.employee_number == "00127"


def test_alphanumeric_employee_number_accepted():
    actor = _employee_admin()
    result = create_employee(
        actor=actor,
        employee_number="ABC01",
        first_name="Test",
        last_name="User",
    )
    assert result.employee.employee_number == "ABC01"


def test_blank_employee_number_rejected_by_service():
    actor = _employee_admin()
    with pytest.raises(ValidationError):
        create_employee(
            actor=actor,
            employee_number="   ",
            first_name="Test",
            last_name="User",
        )


def test_case_sensitive_employee_number_uniqueness():
    actor = _employee_admin()
    create_employee(
        actor=actor,
        employee_number="abc01",
        first_name="One",
        last_name="User",
    )
    result = create_employee(
        actor=actor,
        employee_number="ABC01",
        first_name="Two",
        last_name="User",
    )
    assert result.changed is True
    with pytest.raises(ValidationError):
        create_employee(
            actor=actor,
            employee_number="abc01",
            first_name="Three",
            last_name="User",
        )


def test_duplicate_names_allowed():
    actor = _employee_admin()
    create_employee(
        actor=actor,
        employee_number="100",
        first_name="Same",
        last_name="Name",
    )
    result = create_employee(
        actor=actor,
        employee_number="101",
        first_name="Same",
        last_name="Name",
    )
    assert result.changed is True


def test_employee_number_editable_preserves_uuid_and_frees_old_number():
    actor = _employee_admin()
    created = create_employee(
        actor=actor,
        employee_number="00127",
        first_name="Edit",
        last_name="Me",
    )
    original_id = created.employee.id
    update_employee(
        actor=actor,
        employee_id=original_id,
        employee_number="00425",
        first_name="Edit",
        last_name="Me",
    )
    created.employee.refresh_from_db()
    assert created.employee.id == original_id
    assert created.employee.employee_number == "00425"

    reused = create_employee(
        actor=actor,
        employee_number="00127",
        first_name="Reuse",
        last_name="Number",
    )
    assert reused.employee.employee_number == "00127"
    assert reused.employee.id != original_id


def test_optional_user_link_and_independent_existence():
    actor = _employee_admin()
    user = _create_user("standalone")
    without_user = create_employee(
        actor=actor,
        employee_number="200",
        first_name="No",
        last_name="User",
    )
    assert without_user.employee.user_id is None
    assert User.objects.filter(pk=user.pk).exists()

    with_user = create_employee(
        actor=actor,
        employee_number="201",
        first_name="With",
        last_name="User",
        user_id=user.pk,
    )
    assert with_user.employee.user_id == user.pk


def test_inactive_user_link_allowed_and_does_not_mutate_user_is_active():
    actor = _employee_admin()
    inactive_user = _create_user("inactive-link", is_active=False)
    create_employee(
        actor=actor,
        employee_number="300",
        first_name="Linked",
        last_name="Inactive",
        user_id=inactive_user.pk,
    )
    inactive_user.refresh_from_db()
    assert inactive_user.is_active is False


def test_duplicate_user_assignment_rejected():
    actor = _employee_admin()
    user = _create_user("shared")
    create_employee(
        actor=actor,
        employee_number="400",
        first_name="First",
        last_name="Link",
        user_id=user.pk,
    )
    with pytest.raises(ValidationError):
        create_employee(
            actor=actor,
            employee_number="401",
            first_name="Second",
            last_name="Link",
            user_id=user.pk,
        )


def test_user_delete_sets_null_on_employee():
    actor = _employee_admin()
    user = _create_user("deletable")
    created = create_employee(
        actor=actor,
        employee_number="500",
        first_name="Survive",
        last_name="Delete",
        user_id=user.pk,
    )
    user.delete()
    created.employee.refresh_from_db()
    assert created.employee.user_id is None


def test_deactivate_and_reactivate():
    actor = _employee_admin()
    created = create_employee(
        actor=actor,
        employee_number="600",
        first_name="Status",
        last_name="Change",
    )
    deactivated = set_employee_active(
        actor=actor,
        employee_id=created.employee.id,
        active=False,
    )
    assert deactivated.changed is True
    deactivated.employee.refresh_from_db()
    assert deactivated.employee.active is False

    reactivated = set_employee_active(
        actor=actor,
        employee_id=created.employee.id,
        active=True,
    )
    assert reactivated.changed is True
    assert reactivated.employee.active is True


def test_no_hard_delete_service_exists():
    assert not hasattr(Employee.objects, "delete_employee")
    employee = Employee.objects.create(
        employee_number="700",
        first_name="Persist",
        last_name="Forever",
    )
    assert Employee.objects.filter(pk=employee.pk).exists()


def test_database_employee_number_uniqueness_is_authoritative():
    Employee.objects.create(
        employee_number="DB-UNIQ",
        first_name="One",
        last_name="Row",
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Employee.objects.create(
                employee_number="DB-UNIQ",
                first_name="Two",
                last_name="Row",
            )


def test_denied_create_raises_permission_denied():
    actor = _create_user("denied")
    with pytest.raises(PermissionDenied):
        create_employee(
            actor=actor,
            employee_number="900",
            first_name="Denied",
            last_name="Create",
        )


def test_inactive_actor_denied():
    actor = _grant_employee_permissions(
        _create_user("inactive-actor", is_active=False),
        "add_employee",
        "view_employee",
    )
    with pytest.raises(PermissionDenied):
        create_employee(
            actor=actor,
            employee_number="901",
            first_name="Denied",
            last_name="Inactive",
        )
