from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection

from accounts.models import Employee
from accounts.services.employees import (
    canonical_employee_snapshot,
    create_employee,
    set_employee_active,
    update_employee,
)
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "synthetic-test-password-only"
CANONICAL_FIELDS = {
    "id",
    "employee_number",
    "first_name",
    "last_name",
    "user_id",
    "active",
}
FORBIDDEN_SNAPSHOT_KEYS = {
    "created_at",
    "updated_at",
    "username",
    "email",
    "password",
    "permissions",
    "groups",
}


def _employee_and_audit_tables_exist() -> bool:
    table_names = connection.introspection.table_names()
    return "accounts_employee" in table_names and "audit_auditevent" in table_names


@pytest.fixture(autouse=True)
def require_employee_and_audit_schema():
    if not _employee_and_audit_tables_exist():
        pytest.skip(
            "accounts/audit migrations not applied to test_tezcan_envanter; "
            "employee audit tests deferred until test DB is migrated"
        )


def _create_user(username=None):
    return User.objects.create_user(
        username=username or f"user-{uuid.uuid4().hex[:8]}",
        password=PASSWORD,
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


def _events_for(entity_id):
    return AuditEvent.objects.filter(
        entity_type="accounts.employee",
        entity_id=entity_id,
    ).order_by("occurred_at", "id")


def _assert_canonical_snapshot(payload, employee):
    assert payload is not None
    assert set(payload.keys()) == CANONICAL_FIELDS
    for key in FORBIDDEN_SNAPSHOT_KEYS:
        assert key not in payload
    assert payload == canonical_employee_snapshot(employee)


def test_create_produces_exactly_one_canonical_event():
    actor = _employee_admin()
    before = AuditEvent.objects.count()
    result = create_employee(
        actor=actor,
        employee_number="AUD-1",
        first_name="Audit",
        last_name="Create",
    )
    events = list(_events_for(result.employee.id))
    assert AuditEvent.objects.count() == before + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "accounts.employee.created"
    assert event.actor_id == actor.pk
    assert event.entity_type == "accounts.employee"
    assert event.entity_id == result.employee.id
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, result.employee)


def test_update_audits_full_before_after_with_user_link():
    actor = _employee_admin()
    user = _create_user("audit-user")
    created = create_employee(
        actor=actor,
        employee_number="AUD-2",
        first_name="Before",
        last_name="Update",
    )
    before_events = AuditEvent.objects.count()
    result = update_employee(
        actor=actor,
        employee_id=created.employee.id,
        employee_number="AUD-2",
        first_name="After",
        last_name="Update",
        user_id=user.pk,
    )
    assert AuditEvent.objects.count() == before_events + 1
    event = _events_for(created.employee.id).get(
        event_type="accounts.employee.updated"
    )
    assert result.changed is True
    assert event.before_data["first_name"] == "Before"
    assert event.before_data["user_id"] is None
    assert event.after_data["first_name"] == "After"
    assert event.after_data["user_id"] == user.pk


def test_deactivate_and_reactivate_events():
    actor = _employee_admin()
    created = create_employee(
        actor=actor,
        employee_number="AUD-3",
        first_name="Status",
        last_name="Audit",
    )
    set_employee_active(
        actor=actor,
        employee_id=created.employee.id,
        active=False,
    )
    deactivate = _events_for(created.employee.id).get(
        event_type="accounts.employee.deactivated"
    )
    assert deactivate.before_data["active"] is True
    assert deactivate.after_data["active"] is False

    set_employee_active(
        actor=actor,
        employee_id=created.employee.id,
        active=True,
    )
    reactivate = _events_for(created.employee.id).get(
        event_type="accounts.employee.reactivated"
    )
    assert reactivate.after_data["active"] is True


def test_noop_update_creates_no_audit_event():
    actor = _employee_admin()
    created = create_employee(
        actor=actor,
        employee_number="AUD-4",
        first_name="Same",
        last_name="Same",
    )
    before = AuditEvent.objects.count()
    result = update_employee(
        actor=actor,
        employee_id=created.employee.id,
        employee_number="AUD-4",
        first_name="Same",
        last_name="Same",
    )
    assert result.changed is False
    assert AuditEvent.objects.count() == before


def test_denied_create_leaves_no_successful_audit():
    actor = _create_user("denied-audit")
    before = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        create_employee(
            actor=actor,
            employee_number="AUD-DENY",
            first_name="Denied",
            last_name="Audit",
        )
    assert AuditEvent.objects.count() == before
    assert not Employee.objects.filter(employee_number="AUD-DENY").exists()


class SimulatedAuditAppendFailure(Exception):
    pass


def _failing_audit_append(*args, **kwargs):
    raise SimulatedAuditAppendFailure("simulated audit append failure")


@pytest.mark.django_db(transaction=True)
def test_create_rolls_back_when_audit_append_fails():
    actor = _employee_admin()
    number = f"RB-{uuid.uuid4().hex[:6]}"
    before_ids = set(Employee.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()
    with patch(
        "accounts.services.employees.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            create_employee(
                actor=actor,
                employee_number=number,
                first_name="Rollback",
                last_name="Create",
            )
    assert set(Employee.objects.values_list("pk", flat=True)) == before_ids
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_update_rolls_back_when_audit_append_fails():
    actor = _employee_admin()
    created = create_employee(
        actor=actor,
        employee_number="RB-U",
        first_name="Before",
        last_name="Rollback",
    )
    before_events = AuditEvent.objects.count()
    with patch(
        "accounts.services.employees.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            update_employee(
                actor=actor,
                employee_id=created.employee.id,
                employee_number="RB-U",
                first_name="After",
                last_name="Rollback",
            )
    created.employee.refresh_from_db()
    assert created.employee.first_name == "Before"
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_status_mutation_rolls_back_when_audit_append_fails():
    actor = _employee_admin()
    created = create_employee(
        actor=actor,
        employee_number="RB-S",
        first_name="Status",
        last_name="Rollback",
    )
    before_events = AuditEvent.objects.count()
    with patch(
        "accounts.services.employees.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_employee_active(
                actor=actor,
                employee_id=created.employee.id,
                active=False,
            )
    created.employee.refresh_from_db()
    assert created.employee.active is True
    assert AuditEvent.objects.count() == before_events
