from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection

from audit.models import AuditEvent
from inventory.models import ProductionLine
from inventory.services.production_lines import (
    create_production_line,
    set_production_line_active,
    update_production_line,
)

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
CANONICAL_FIELDS = {"id", "code", "name", "parent_id", "active"}
FORBIDDEN_SNAPSHOT_KEYS = {
    "created_at",
    "updated_at",
    "username",
    "session",
    "csrf",
    "ip",
    "device",
    "request",
}


def _production_line_and_audit_tables_exist() -> bool:
    table_names = connection.introspection.table_names()
    return (
        "inventory_productionline" in table_names
        and "audit_auditevent" in table_names
    )


@pytest.fixture(autouse=True)
def require_production_line_and_audit_schema():
    if not _production_line_and_audit_tables_exist():
        pytest.skip(
            "inventory/audit migrations not applied to test_tezcan_envanter; "
            "production line audit tests deferred until test DB is migrated"
        )


def _create_ordinary_user(username):
    user_model = get_user_model()
    return user_model.objects.create_user(username=username, password=PASSWORD)


def _grant_production_line_permissions(user, *codenames):
    permissions = Permission.objects.filter(
        content_type__app_label="inventory",
        codename__in=codenames,
    )
    user.user_permissions.add(*permissions)
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _production_line_admin(username=None):
    return _grant_production_line_permissions(
        _create_ordinary_user(username or f"pl-admin-{uuid.uuid4().hex[:8]}"),
        "view_productionline",
        "add_productionline",
        "change_productionline",
    )


def _events_for(entity_id):
    return AuditEvent.objects.filter(
        entity_type="inventory.production_line",
        entity_id=entity_id,
    ).order_by("occurred_at", "id")


def _assert_canonical_snapshot(payload, production_line):
    assert payload is not None
    assert set(payload.keys()) == CANONICAL_FIELDS
    for key in FORBIDDEN_SNAPSHOT_KEYS:
        assert key not in payload
    assert payload == {
        "id": str(production_line.id),
        "code": production_line.code,
        "name": production_line.name,
        "parent_id": (
            str(production_line.parent_id) if production_line.parent_id else None
        ),
        "active": bool(production_line.active),
    }


def test_create_produces_exactly_one_canonical_event():
    actor = _production_line_admin()
    parent = ProductionLine.objects.create(code="P-1", name="Parent")
    before = AuditEvent.objects.count()

    result = create_production_line(
        actor=actor,
        code="C-1",
        name="Child",
        parent_id=parent.pk,
    )
    events = list(_events_for(result.production_line.id))

    assert result.changed is True
    assert AuditEvent.objects.count() == before + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "inventory.production_line.created"
    assert event.actor_id == actor.pk
    assert event.entity_type == "inventory.production_line"
    assert event.entity_id == result.production_line.id
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, result.production_line)


def test_update_audits_full_before_after():
    actor = _production_line_admin()
    line = ProductionLine.objects.create(code="OLD", name="Old Name")
    result = update_production_line(
        actor=actor,
        production_line_id=line.pk,
        code="NEW",
        name="New Name",
        parent_id=None,
    )
    event = _events_for(line.id).get(
        event_type="inventory.production_line.updated"
    )
    assert result.changed is True
    assert event.before_data == {
        "id": str(line.id),
        "code": "OLD",
        "name": "Old Name",
        "parent_id": None,
        "active": True,
    }
    _assert_canonical_snapshot(event.after_data, result.production_line)


def test_deactivate_and_reactivate_events():
    actor = _production_line_admin()
    line = ProductionLine.objects.create(code="ST", name="Status")
    set_production_line_active(
        actor=actor, production_line_id=line.pk, active=False
    )
    deactivate = _events_for(line.id).get(
        event_type="inventory.production_line.deactivated"
    )
    assert deactivate.before_data["active"] is True
    assert deactivate.after_data["active"] is False

    set_production_line_active(
        actor=actor, production_line_id=line.pk, active=True
    )
    reactivate = _events_for(line.id).get(
        event_type="inventory.production_line.reactivated"
    )
    assert reactivate.after_data["active"] is True


def test_noop_update_creates_no_audit_event():
    actor = _production_line_admin()
    line = ProductionLine.objects.create(code="SAME", name="Same")
    before = AuditEvent.objects.count()
    result = update_production_line(
        actor=actor,
        production_line_id=line.pk,
        code="SAME",
        name="Same",
        parent_id=None,
    )
    assert result.changed is False
    assert AuditEvent.objects.count() == before


def test_noop_status_change_creates_no_audit_event():
    actor = _production_line_admin()
    line = ProductionLine.objects.create(code="ACT", name="Active")
    before = AuditEvent.objects.count()
    result = set_production_line_active(
        actor=actor, production_line_id=line.pk, active=True
    )
    assert result.changed is False
    assert AuditEvent.objects.count() == before


def test_denied_create_leaves_no_successful_audit():
    actor = _create_ordinary_user("denied-create")
    before = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        create_production_line(actor=actor, code="X", name="Denied")
    assert AuditEvent.objects.count() == before
    assert not ProductionLine.objects.filter(code="X").exists()


def test_denied_update_leaves_no_successful_audit():
    actor = _create_ordinary_user("denied-update")
    line = ProductionLine.objects.create(code="KEEP", name="Keep")
    before = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        update_production_line(
            actor=actor,
            production_line_id=line.pk,
            code="KEEP",
            name="Changed",
            parent_id=None,
        )
    assert AuditEvent.objects.count() == before
    line.refresh_from_db()
    assert line.name == "Keep"


class SimulatedAuditAppendFailure(Exception):
    pass


def _failing_audit_append(*args, **kwargs):
    raise SimulatedAuditAppendFailure("simulated audit append failure")


@pytest.mark.django_db(transaction=True)
def test_create_rolls_back_when_audit_append_fails():
    actor = _production_line_admin()
    code = f"RB-{uuid.uuid4().hex[:6]}"
    before_ids = set(ProductionLine.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()
    with patch(
        "inventory.services.production_lines.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            create_production_line(actor=actor, code=code, name="Rollback")
    assert set(ProductionLine.objects.values_list("pk", flat=True)) == before_ids
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_update_rolls_back_when_audit_append_fails():
    actor = _production_line_admin()
    line = ProductionLine.objects.create(code="RB-U", name="Before")
    before_events = AuditEvent.objects.count()
    with patch(
        "inventory.services.production_lines.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            update_production_line(
                actor=actor,
                production_line_id=line.pk,
                code="RB-U",
                name="After",
                parent_id=None,
            )
    line.refresh_from_db()
    assert line.name == "Before"
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_status_mutation_rolls_back_when_audit_append_fails():
    actor = _production_line_admin()
    line = ProductionLine.objects.create(code="RB-S", name="Status")
    before_events = AuditEvent.objects.count()
    with patch(
        "inventory.services.production_lines.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_production_line_active(
                actor=actor, production_line_id=line.pk, active=False
            )
    line.refresh_from_db()
    assert line.active is True
    assert AuditEvent.objects.count() == before_events
