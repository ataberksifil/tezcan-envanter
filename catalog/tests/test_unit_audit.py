from __future__ import annotations

import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.urls import reverse

from accounts.roles import ADMIN_MANAGER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, UnitOfMeasure
from catalog.services.units_of_measure import (
    DUPLICATE_CODE_MESSAGE,
    create_unit_of_measure,
    set_unit_of_measure_active,
    update_unit_of_measure,
)

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
SEEDED_ADET_ID = uuid.UUID("b2022c02-0001-4001-8001-000000000001")
CANONICAL_FIELDS = {"id", "code", "name", "decimal_places", "active"}
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


def _catalog_and_audit_tables_exist() -> bool:
    table_names = connection.introspection.table_names()
    return "catalog_unitofmeasure" in table_names and "audit_auditevent" in table_names


@pytest.fixture(autouse=True)
def require_catalog_and_audit_schema():
    if not _catalog_and_audit_tables_exist():
        pytest.skip(
            "catalog/audit migrations not applied to test_tezcan_envanter; "
            "unit audit tests deferred until test DB is migrated"
        )


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _refresh_user_permissions(user):
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _create_ordinary_user(username):
    user_model = get_user_model()
    user = user_model.objects.create_user(username=username, password=PASSWORD)
    assert not user.is_superuser
    assert not user.is_staff
    return user


def _role_user(role_name, username=None):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(username or f"{role_name.lower()}-{uuid.uuid4().hex[:8]}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert not user.is_superuser
    return user


def _login(app_client, user):
    app_client.force_login(user)
    return user


def _admin_actor():
    return _role_user(ADMIN_MANAGER, f"uom-audit-admin-{uuid.uuid4().hex[:8]}")


def _unauthorized_actor():
    return _create_ordinary_user(f"uom-audit-none-{uuid.uuid4().hex[:8]}")


def _events_for(entity_id):
    return AuditEvent.objects.filter(
        entity_type="catalog.unit_of_measure",
        entity_id=entity_id,
    ).order_by("occurred_at", "id")


def _assert_canonical_snapshot(payload, unit):
    assert payload is not None
    assert set(payload.keys()) == CANONICAL_FIELDS
    for key in FORBIDDEN_SNAPSHOT_KEYS:
        assert key not in payload
    assert payload == {
        "id": str(unit.id),
        "code": unit.code,
        "name": unit.name,
        "decimal_places": unit.decimal_places,
        "active": unit.active,
    }


class _FakeUniqueViolationCause(Exception):
    def __init__(self) -> None:
        self.diag = type(
            "Diag",
            (),
            {"constraint_name": "catalog_uom_code_uniq"},
        )()


def _make_uom_code_unique_integrity_error() -> IntegrityError:
    exc = IntegrityError("duplicate key value violates unique constraint")
    exc.__cause__ = _FakeUniqueViolationCause()
    return exc


def _seeded_adet_unit() -> UnitOfMeasure:
    unit, _created = UnitOfMeasure.objects.get_or_create(
        pk=SEEDED_ADET_ID,
        defaults={
            "code": "ADET",
            "name": "Adet",
            "active": True,
            "decimal_places": None,
        },
    )
    return unit


# ---------------------------------------------------------------------------
# CREATE database alias binding
# ---------------------------------------------------------------------------


def test_create_unit_full_clean_sees_requested_db_alias():
    actor = _admin_actor()
    using = "default"
    seen_db: list[str | None] = []
    original_full_clean = UnitOfMeasure.full_clean

    def tracking_full_clean(self, *args, **kwargs):
        seen_db.append(self._state.db)
        assert self._state.db == using
        return original_full_clean(self, *args, **kwargs)

    with patch.object(UnitOfMeasure, "full_clean", tracking_full_clean):
        create_unit_of_measure(
            actor=actor,
            code=f"ALIAS-{uuid.uuid4().hex[:6]}",
            name="Alias Unit",
            using=using,
        )

    assert seen_db == [using]


def test_actor_database_alias_mismatch_rejected():
    actor = _admin_actor()
    actor._state.db = "other"
    with patch.object(actor, "has_perm", return_value=True):
        with pytest.raises(ValidationError):
            create_unit_of_measure(
                actor=actor,
                code=f"MISMATCH-{uuid.uuid4().hex[:6]}",
                name="Mismatch",
                using="default",
            )


# ---------------------------------------------------------------------------
# CREATE event content
# ---------------------------------------------------------------------------


def test_successful_create_produces_exactly_one_canonical_event():
    actor = _admin_actor()
    before_events = AuditEvent.objects.count()

    result = create_unit_of_measure(
        actor=actor,
        code=f"CREATE-{uuid.uuid4().hex[:6]}",
        name="Create Unit",
    )
    unit = result.unit
    events = list(_events_for(unit.id))

    assert result.changed is True
    assert unit.decimal_places is None
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.unit_of_measure.created"
    assert event.actor_id == actor.pk
    assert event.entity_type == "catalog.unit_of_measure"
    assert event.entity_id == unit.id
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, unit)


def test_create_strips_whitespace_in_snapshot():
    actor = _admin_actor()
    result = create_unit_of_measure(actor=actor, code=" ADET-X ", name=" Adet X ")
    unit = result.unit
    event = _events_for(unit.id).get()
    assert unit.code == "ADET-X"
    assert unit.name == "Adet X"
    assert event.after_data["code"] == "ADET-X"
    assert event.after_data["name"] == "Adet X"


# ---------------------------------------------------------------------------
# UPDATE event content
# ---------------------------------------------------------------------------


def test_successful_update_produces_exactly_one_event_with_before_after():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="BEFORE-CODE", name="Before Name")
    before_state = {
        "id": str(unit.id),
        "code": "BEFORE-CODE",
        "name": "Before Name",
        "decimal_places": None,
        "active": True,
    }
    before_events = AuditEvent.objects.count()

    result = update_unit_of_measure(
        actor=actor,
        unit_of_measure_id=unit.pk,
        code="AFTER-CODE",
        name="After Name",
    )
    unit.refresh_from_db()
    events = list(_events_for(unit.id))

    assert result.changed is True
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.unit_of_measure.updated"
    assert event.actor_id == actor.pk
    assert event.before_data == before_state
    _assert_canonical_snapshot(event.after_data, unit)
    assert event.after_data["code"] == "AFTER-CODE"
    assert event.after_data["name"] == "After Name"
    assert unit.id == uuid.UUID(before_state["id"])


def test_update_preserves_decimal_places():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(
        code=f"DP-{uuid.uuid4().hex[:6]}",
        name="Decimal Unit",
        decimal_places=2,
    )
    update_unit_of_measure(
        actor=actor,
        unit_of_measure_id=unit.pk,
        code=unit.code,
        name="Renamed",
    )
    unit.refresh_from_db()
    assert unit.decimal_places == 2
    event = _events_for(unit.id).get()
    assert event.after_data["decimal_places"] == 2


# ---------------------------------------------------------------------------
# STATUS event content
# ---------------------------------------------------------------------------


def test_deactivate_produces_exactly_one_event_with_active_transition():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="DEACT-1", name="Deactivate Me", active=True)
    before_events = AuditEvent.objects.count()

    result = set_unit_of_measure_active(
        actor=actor,
        unit_of_measure_id=unit.pk,
        active=False,
    )
    unit.refresh_from_db()
    events = list(_events_for(unit.id))

    assert result.changed is True
    assert unit.active is False
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.unit_of_measure.deactivated"
    assert event.before_data["active"] is True
    assert event.after_data["active"] is False


def test_reactivate_produces_exactly_one_event_with_active_transition():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="REACT-1", name="Reactivate Me", active=False)
    before_events = AuditEvent.objects.count()

    result = set_unit_of_measure_active(
        actor=actor,
        unit_of_measure_id=unit.pk,
        active=True,
    )
    unit.refresh_from_db()
    events = list(_events_for(unit.id))

    assert result.changed is True
    assert unit.active is True
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.unit_of_measure.reactivated"
    assert event.before_data["active"] is False
    assert event.after_data["active"] is True


# ---------------------------------------------------------------------------
# NO-OP / failure
# ---------------------------------------------------------------------------


def test_noop_update_does_not_save_or_audit():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="NOOP-1", name="Noop Name")
    original_updated_at = unit.updated_at
    before_events = AuditEvent.objects.count()

    result = update_unit_of_measure(
        actor=actor,
        unit_of_measure_id=unit.pk,
        code="NOOP-1",
        name="Noop Name",
    )
    unit.refresh_from_db()

    assert result.changed is False
    assert unit.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events
    assert _events_for(unit.id).count() == 0


def test_repeated_deactivate_is_noop():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="ALREADY-OFF", name="Off", active=True)
    first = set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)
    unit.refresh_from_db()
    updated_at = unit.updated_at
    event_count = AuditEvent.objects.count()

    second = set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)
    unit.refresh_from_db()

    assert first.changed is True
    assert second.changed is False
    assert unit.updated_at == updated_at
    assert AuditEvent.objects.count() == event_count


def test_repeated_reactivate_is_noop():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="ALREADY-ON", name="On", active=False)
    first = set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=True)
    unit.refresh_from_db()
    updated_at = unit.updated_at
    event_count = AuditEvent.objects.count()

    second = set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=True)
    unit.refresh_from_db()

    assert first.changed is True
    assert second.changed is False
    assert unit.updated_at == updated_at
    assert AuditEvent.objects.count() == event_count


def test_validation_failure_does_not_mutate_or_audit():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="VALID-KEEP", name="Valid Keep")
    original_updated_at = unit.updated_at
    before_count = UnitOfMeasure.objects.count()
    before_events = AuditEvent.objects.count()

    with pytest.raises(ValidationError):
        update_unit_of_measure(
            actor=actor,
            unit_of_measure_id=unit.pk,
            code="CHANGED",
            name="   ",
        )
    with pytest.raises(ValidationError):
        create_unit_of_measure(actor=actor, code="X", name="   ")

    unit.refresh_from_db()
    assert unit.name == "Valid Keep"
    assert unit.code == "VALID-KEEP"
    assert unit.updated_at == original_updated_at
    assert UnitOfMeasure.objects.count() == before_count
    assert AuditEvent.objects.count() == before_events


def test_unauthorized_create_has_no_unit_or_audit():
    actor = _unauthorized_actor()
    before_ids = set(UnitOfMeasure.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        create_unit_of_measure(actor=actor, code="NOPE", name="UNAUTH-CREATE")

    assert set(UnitOfMeasure.objects.values_list("pk", flat=True)) == before_ids
    assert AuditEvent.objects.count() == before_events


def test_unauthorized_update_leaves_unit_and_has_no_audit():
    actor = _unauthorized_actor()
    unit = UnitOfMeasure.objects.create(code="UNAUTH-TARGET", name="Keep")
    updated_at = unit.updated_at
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        update_unit_of_measure(
            actor=actor,
            unit_of_measure_id=unit.pk,
            code="HACK",
            name="HACKED",
        )

    unit.refresh_from_db()
    assert unit.name == "Keep"
    assert unit.code == "UNAUTH-TARGET"
    assert unit.updated_at == updated_at
    assert AuditEvent.objects.count() == before_events


def test_unauthorized_status_leaves_unit_and_has_no_audit():
    actor = _unauthorized_actor()
    unit = UnitOfMeasure.objects.create(code="UNAUTH-STATE", name="State", active=True)
    updated_at = unit.updated_at
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)

    unit.refresh_from_db()
    assert unit.active is True
    assert unit.updated_at == updated_at
    assert AuditEvent.objects.count() == before_events


def test_technician_service_calls_are_unauthorized():
    actor = _role_user(TECHNICIAN, "tech-uom-denied")
    unit = UnitOfMeasure.objects.create(code="TECH-DENIED", name="Tech", active=True)
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        create_unit_of_measure(actor=actor, code="TECH-NEW", name="Tech Create")
    with pytest.raises(PermissionDenied):
        update_unit_of_measure(
            actor=actor,
            unit_of_measure_id=unit.pk,
            code="TECH-EDIT",
            name="Tech Edit",
        )
    with pytest.raises(PermissionDenied):
        set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)

    unit.refresh_from_db()
    assert unit.active is True
    assert AuditEvent.objects.count() == before_events


# ---------------------------------------------------------------------------
# DUPLICATE CODE
# ---------------------------------------------------------------------------


def test_sequential_duplicate_create_rejected_cleanly():
    actor = _admin_actor()
    code = f"DUP-{uuid.uuid4().hex[:6]}"
    create_unit_of_measure(actor=actor, code=code, name="First")
    with pytest.raises(ValidationError) as exc_info:
        create_unit_of_measure(actor=actor, code=code, name="Second")
    assert "code" in exc_info.value.error_dict
    assert UnitOfMeasure.objects.filter(code=code).count() == 1


def test_sequential_duplicate_update_rejected_cleanly():
    actor = _admin_actor()
    first = UnitOfMeasure.objects.create(code="KEEP-1", name="First")
    second = UnitOfMeasure.objects.create(code="KEEP-2", name="Second")
    with pytest.raises(ValidationError) as exc_info:
        update_unit_of_measure(
            actor=actor,
            unit_of_measure_id=second.pk,
            code=first.code,
            name=second.name,
        )
    assert "code" in exc_info.value.error_dict
    second.refresh_from_db()
    assert second.code == "KEEP-2"


def test_named_constraint_integrity_error_translated_to_duplicate_code():
    actor = _admin_actor()
    with patch.object(
        UnitOfMeasure,
        "save",
        side_effect=_make_uom_code_unique_integrity_error(),
    ):
        with pytest.raises(ValidationError) as exc_info:
            create_unit_of_measure(
                actor=actor,
                code=f"RACE-{uuid.uuid4().hex[:6]}",
                name="Race",
            )
    assert DUPLICATE_CODE_MESSAGE in str(exc_info.value)
    assert not UnitOfMeasure.objects.filter(name="Race").exists()


def test_unrelated_integrity_error_is_not_translated():
    actor = _admin_actor()
    with patch.object(UnitOfMeasure, "save", side_effect=IntegrityError("other failure")):
        with pytest.raises(IntegrityError):
            create_unit_of_measure(
                actor=actor,
                code=f"OTHER-{uuid.uuid4().hex[:6]}",
                name="Other",
            )


def test_postgresql_unique_constraint_is_final_arbitrator():
    """If validate_unique is bypassed, DB unique constraint still maps to user error."""
    actor = _admin_actor()
    code = f"DB-UNIQ-{uuid.uuid4().hex[:6]}"
    UnitOfMeasure.objects.create(code=code, name="Existing")

    def strip_only_clean(self, *args, **kwargs):
        if self.code is not None:
            self.code = self.code.strip()
        if self.name is not None:
            self.name = self.name.strip()

    with patch.object(UnitOfMeasure, "full_clean", strip_only_clean):
        with pytest.raises(ValidationError) as exc_info:
            create_unit_of_measure(actor=actor, code=code, name="Concurrent")
    assert DUPLICATE_CODE_MESSAGE in str(exc_info.value)
    assert UnitOfMeasure.objects.filter(name="Concurrent").count() == 0


# ---------------------------------------------------------------------------
# ATOMIC ROLLBACK
# ---------------------------------------------------------------------------


class SimulatedAuditAppendFailure(Exception):
    pass


def _failing_audit_append(*args, **kwargs):
    raise SimulatedAuditAppendFailure("simulated audit append failure")


@pytest.mark.django_db(transaction=True)
def test_create_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    code = f"RB-CREATE-{uuid.uuid4().hex[:6]}"
    before_ids = set(UnitOfMeasure.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.units_of_measure.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            create_unit_of_measure(actor=actor, code=code, name="Rollback Create")

    assert set(UnitOfMeasure.objects.values_list("pk", flat=True)) == before_ids
    assert not UnitOfMeasure.objects.filter(code=code).exists()
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_update_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="RB-OLD", name="Rollback Old")
    original_updated_at = unit.updated_at
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.units_of_measure.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            update_unit_of_measure(
                actor=actor,
                unit_of_measure_id=unit.pk,
                code="RB-NEW",
                name="Rollback New",
            )

    unit.refresh_from_db()
    assert unit.name == "Rollback Old"
    assert unit.code == "RB-OLD"
    assert unit.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_deactivate_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="RB-ACT", name="Rollback Active", active=True)
    original_updated_at = unit.updated_at
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.units_of_measure.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)

    unit.refresh_from_db()
    assert unit.active is True
    assert unit.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_reactivate_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="RB-INACT", name="Rollback Inactive", active=False)
    original_updated_at = unit.updated_at
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.units_of_measure.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=True)

    unit.refresh_from_db()
    assert unit.active is False
    assert unit.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


# ---------------------------------------------------------------------------
# Referenced / seeded edge cases
# ---------------------------------------------------------------------------


def test_seeded_adet_can_be_renamed_and_deactivated():
    actor = _admin_actor()
    unit = _seeded_adet_unit()
    original_id = unit.id
    original_code = unit.code
    original_name = unit.name
    original_active = unit.active

    try:
        update_unit_of_measure(
            actor=actor,
            unit_of_measure_id=unit.pk,
            code="ADET-RENAMED",
            name="Adet (yeniden adlandırıldı)",
        )
        unit.refresh_from_db()
        assert unit.id == original_id
        assert unit.code == "ADET-RENAMED"

        set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)
        unit.refresh_from_db()
        assert unit.active is False

        set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=True)
        unit.refresh_from_db()
        assert unit.active is True
    finally:
        unit.code = original_code
        unit.name = original_name
        unit.active = original_active
        unit.save(update_fields=["code", "name", "active", "updated_at"])


def test_referenced_uom_deactivation_does_not_mutate_material():
    actor = _admin_actor()
    category = Category.objects.create(name="UOM-REF-CAT")
    unit = UnitOfMeasure.objects.create(code="REF-UOM", name="Referenced")
    material = Material.objects.create(
        material_code="REF-MAT",
        name="Referenced Material",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material_updated_at = material.updated_at
    unit_id = unit.pk

    set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)
    material.refresh_from_db()
    unit.refresh_from_db()

    assert unit.active is False
    assert material.unit_id == unit_id
    assert material.updated_at == material_updated_at


# ---------------------------------------------------------------------------
# HTTP integration
# ---------------------------------------------------------------------------


def test_http_create_records_audit_event_for_request_user(app_client):
    user = _admin_actor()
    _login(app_client, user)
    code = f"HTTP-{uuid.uuid4().hex[:6]}"
    before_events = AuditEvent.objects.count()
    response = app_client.post(
        "/catalog/units/new/",
        {"code": code, "name": "HTTP Created"},
        follow=True,
    )
    assert response.status_code == 200
    assert "Ölçü birimi oluşturuldu." in response.content.decode()
    unit = UnitOfMeasure.objects.get(code=code)
    assert unit.decimal_places is None
    assert AuditEvent.objects.count() == before_events + 1
    event = _events_for(unit.id).get()
    assert event.event_type == "catalog.unit_of_measure.created"
    assert event.actor_id == user.pk


def test_http_update_records_audit_event_for_request_user(app_client):
    user = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="HTTP-BEFORE", name="HTTP Before")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {"code": "HTTP-AFTER", "name": "HTTP After"},
        follow=True,
    )
    assert response.status_code == 200
    assert "Ölçü birimi güncellendi." in response.content.decode()
    unit.refresh_from_db()
    event = _events_for(unit.id).get()
    assert event.event_type == "catalog.unit_of_measure.updated"
    assert event.before_data["code"] == "HTTP-BEFORE"
    assert event.after_data["code"] == "HTTP-AFTER"
    assert unit.id == event.entity_id


def test_http_noop_update_and_status_do_not_audit(app_client):
    user = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="HTTP-NOOP", name="HTTP Noop", active=True)
    _login(app_client, user)

    update = app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {"code": "HTTP-NOOP", "name": "HTTP Noop"},
        follow=True,
    )
    assert "Değişiklik yapılmadı." in update.content.decode()
    assert _events_for(unit.id).count() == 0

    first_deactivate = app_client.post(
        reverse("catalog:unit-deactivate", args=[unit.pk]),
        follow=True,
    )
    assert "Ölçü birimi pasifleştirildi." in first_deactivate.content.decode()
    assert _events_for(unit.id).count() == 1


def test_http_reads_and_validation_failures_are_not_audited(app_client):
    user = _admin_actor()
    unit = UnitOfMeasure.objects.create(code="READ-ONLY", name="Read Only")
    _login(app_client, user)
    before_events = AuditEvent.objects.count()

    assert app_client.get("/catalog/units/").status_code == 200
    assert app_client.get("/catalog/units/new/").status_code == 200
    assert app_client.get(reverse("catalog:unit-update", args=[unit.pk])).status_code == 200

    invalid_create = app_client.post(
        "/catalog/units/new/",
        {"code": "X", "name": "   "},
    )
    assert invalid_create.status_code == 200
    assert invalid_create.context["form"].errors
    assert AuditEvent.objects.count() == before_events


def test_http_denied_attempts_are_not_audited(app_client):
    unit = UnitOfMeasure.objects.create(code="DENIED", name="Denied", active=True)
    user = _role_user(TECHNICIAN, "http-denied-tech-uom")
    _login(app_client, user)
    before_events = AuditEvent.objects.count()

    create = app_client.post(
        "/catalog/units/new/",
        {"code": "DENIED-NEW", "name": "Denied Create"},
    )
    update = app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {"code": "HACK", "name": "Hacked"},
    )
    deactivate = app_client.post(reverse("catalog:unit-deactivate", args=[unit.pk]))
    assert create.status_code == 403
    assert update.status_code == 403
    assert deactivate.status_code == 403
    assert AuditEvent.objects.count() == before_events


def test_anonymous_mutations_redirect_to_login_without_audit(app_client):
    before_events = AuditEvent.objects.count()
    response = app_client.post(
        "/catalog/units/new/",
        {"code": "ANON", "name": "Anon Create"},
    )
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == ["/catalog/units/new/"]
    assert AuditEvent.objects.count() == before_events
