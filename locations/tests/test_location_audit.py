from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection

from audit.models import AuditEvent
from locations.models import Location
from locations.services.locations import (
    create_location,
    set_location_active,
    update_location,
)

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
CANONICAL_FIELDS = {
    "id",
    "code",
    "name",
    "parent_id",
    "active",
    "can_hold_stock",
}
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


def _location_and_audit_tables_exist() -> bool:
    table_names = connection.introspection.table_names()
    return "locations_location" in table_names and "audit_auditevent" in table_names


@pytest.fixture(autouse=True)
def require_location_and_audit_schema():
    if not _location_and_audit_tables_exist():
        pytest.skip(
            "locations/audit migrations not applied to test_tezcan_envanter; "
            "location audit tests deferred until test DB is migrated"
        )


def _create_ordinary_user(username):
    user_model = get_user_model()
    return user_model.objects.create_user(username=username, password=PASSWORD)


def _grant_location_permissions(user, *codenames):
    permissions = Permission.objects.filter(
        content_type__app_label="locations",
        codename__in=codenames,
    )
    user.user_permissions.add(*permissions)
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _location_admin(username=None):
    return _grant_location_permissions(
        _create_ordinary_user(username or f"loc-admin-{uuid.uuid4().hex[:8]}"),
        "view_location",
        "add_location",
        "change_location",
    )


def _events_for(entity_id):
    return AuditEvent.objects.filter(
        entity_type="locations.location",
        entity_id=entity_id,
    ).order_by("occurred_at", "id")


def _assert_canonical_snapshot(payload, location):
    assert payload is not None
    assert set(payload.keys()) == CANONICAL_FIELDS
    for key in FORBIDDEN_SNAPSHOT_KEYS:
        assert key not in payload
    assert payload == {
        "id": str(location.id),
        "code": location.code,
        "name": location.name,
        "parent_id": str(location.parent_id) if location.parent_id else None,
        "active": bool(location.active),
        "can_hold_stock": bool(location.can_hold_stock),
    }


def test_create_produces_exactly_one_canonical_event():
    actor = _location_admin()
    parent = Location.objects.create(code="P-1", name="Parent")
    before = AuditEvent.objects.count()

    result = create_location(
        actor=actor,
        code="C-1",
        name="Child",
        parent_id=parent.pk,
        can_hold_stock=True,
    )
    events = list(_events_for(result.location.id))

    assert result.changed is True
    assert AuditEvent.objects.count() == before + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "locations.location.created"
    assert event.actor_id == actor.pk
    assert event.entity_type == "locations.location"
    assert event.entity_id == result.location.id
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, result.location)


def test_update_audits_full_before_after():
    actor = _location_admin()
    location = Location.objects.create(code="OLD", name="Old Name")
    result = update_location(
        actor=actor,
        location_id=location.pk,
        code="NEW",
        name="New Name",
        parent_id=None,
        can_hold_stock=True,
    )
    event = _events_for(location.id).get(event_type="locations.location.updated")
    assert result.changed is True
    assert event.before_data == {
        "id": str(location.id),
        "code": "OLD",
        "name": "Old Name",
        "parent_id": None,
        "active": True,
        "can_hold_stock": False,
    }
    _assert_canonical_snapshot(event.after_data, result.location)


def test_deactivate_and_reactivate_events():
    actor = _location_admin()
    location = Location.objects.create(code="ST", name="Status")
    set_location_active(actor=actor, location_id=location.pk, active=False)
    deactivate = _events_for(location.id).get(event_type="locations.location.deactivated")
    assert deactivate.before_data["active"] is True
    assert deactivate.after_data["active"] is False

    set_location_active(actor=actor, location_id=location.pk, active=True)
    reactivate = _events_for(location.id).get(event_type="locations.location.reactivated")
    assert reactivate.after_data["active"] is True


def test_noop_update_creates_no_audit_event():
    actor = _location_admin()
    location = Location.objects.create(code="SAME", name="Same")
    before = AuditEvent.objects.count()
    result = update_location(
        actor=actor,
        location_id=location.pk,
        code="SAME",
        name="Same",
        parent_id=None,
        can_hold_stock=False,
    )
    assert result.changed is False
    assert AuditEvent.objects.count() == before


def test_noop_status_change_creates_no_audit_event():
    actor = _location_admin()
    location = Location.objects.create(code="ACT", name="Active")
    before = AuditEvent.objects.count()
    result = set_location_active(actor=actor, location_id=location.pk, active=True)
    assert result.changed is False
    assert AuditEvent.objects.count() == before


def test_denied_create_leaves_no_successful_audit():
    actor = _create_ordinary_user("denied-create")
    before = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        create_location(actor=actor, code="X", name="Denied")
    assert AuditEvent.objects.count() == before
    assert not Location.objects.filter(code="X").exists()


def test_denied_update_leaves_no_successful_audit():
    actor = _create_ordinary_user("denied-update")
    location = Location.objects.create(code="KEEP", name="Keep")
    before = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        update_location(
            actor=actor,
            location_id=location.pk,
            code="KEEP",
            name="Changed",
            parent_id=None,
            can_hold_stock=False,
        )
    assert AuditEvent.objects.count() == before
    location.refresh_from_db()
    assert location.name == "Keep"


class SimulatedAuditAppendFailure(Exception):
    pass


def _failing_audit_append(*args, **kwargs):
    raise SimulatedAuditAppendFailure("simulated audit append failure")


@pytest.mark.django_db(transaction=True)
def test_create_rolls_back_when_audit_append_fails():
    actor = _location_admin()
    code = f"RB-{uuid.uuid4().hex[:6]}"
    before_ids = set(Location.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()
    with patch(
        "locations.services.locations.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            create_location(actor=actor, code=code, name="Rollback")
    assert set(Location.objects.values_list("pk", flat=True)) == before_ids
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_update_rolls_back_when_audit_append_fails():
    actor = _location_admin()
    location = Location.objects.create(code="RB-U", name="Before")
    before_events = AuditEvent.objects.count()
    with patch(
        "locations.services.locations.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            update_location(
                actor=actor,
                location_id=location.pk,
                code="RB-U",
                name="After",
                parent_id=None,
                can_hold_stock=False,
            )
    location.refresh_from_db()
    assert location.name == "Before"
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_status_mutation_rolls_back_when_audit_append_fails():
    actor = _location_admin()
    location = Location.objects.create(code="RB-S", name="Status")
    before_events = AuditEvent.objects.count()
    with patch(
        "locations.services.locations.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_location_active(actor=actor, location_id=location.pk, active=False)
    location.refresh_from_db()
    assert location.active is True
    assert AuditEvent.objects.count() == before_events
