from __future__ import annotations

import uuid

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.utils import DatabaseError

from audit.admin import AuditEventAdmin
from audit.exceptions import AuditImmutabilityError, AuditValidationError
from audit.models import AuditEvent
from audit.services import record_audit_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor():
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=f"audit-actor-{uuid.uuid4().hex[:8]}",
        password="synthetic-test-password-only",
    )
    assert not user.is_superuser
    assert not user.is_staff
    return user


@pytest.fixture
def entity_id():
    return uuid.uuid4()


def _audit_tables_exist() -> bool:
    return "audit_auditevent" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_audit_schema():
    if not _audit_tables_exist():
        pytest.skip(
            "audit migration not applied to test_tezcan_envanter; "
            "audit foundation tests deferred until test DB is migrated"
        )


def _create_event(actor, entity_id, **overrides):
    payload = {
        "actor": actor,
        "event_type": "category.created",
        "entity_type": "catalog.Category",
        "entity_id": entity_id,
        "before_data": None,
        "after_data": {"name": "Test"},
        "metadata": {"source": "test"},
    }
    payload.update(overrides)
    return record_audit_event(**payload)


# ---------------------------------------------------------------------------
# Model / API
# ---------------------------------------------------------------------------


def test_service_creates_one_valid_event(actor, entity_id):
    before = AuditEvent.objects.count()
    event = _create_event(actor, entity_id)
    assert AuditEvent.objects.count() == before + 1
    assert event.pk is not None
    assert event.event_type == "category.created"
    assert event.entity_type == "catalog.Category"
    assert event.entity_id == entity_id
    assert event.actor_id == actor.pk
    assert event.before_data is None
    assert event.after_data == {"name": "Test"}
    assert event.metadata == {"source": "test"}
    assert event.occurred_at is not None


def test_before_and_after_data_objects_accepted(actor, entity_id):
    event = record_audit_event(
        actor=actor,
        event_type="category.updated",
        entity_type="catalog.Category",
        entity_id=entity_id,
        before_data={"name": "Old"},
        after_data={"name": "New"},
        metadata={},
    )
    assert event.before_data == {"name": "Old"}
    assert event.after_data == {"name": "New"}


def test_none_accepted_for_nullable_json_fields(actor, entity_id):
    event = record_audit_event(
        actor=actor,
        event_type="category.viewed",
        entity_type="catalog.Category",
        entity_id=entity_id,
        before_data=None,
        after_data=None,
        metadata={},
    )
    assert event.before_data is None
    assert event.after_data is None


def test_metadata_object_accepted(actor, entity_id):
    event = record_audit_event(
        actor=actor,
        event_type="category.updated",
        entity_type="catalog.Category",
        entity_id=entity_id,
        before_data=None,
        after_data=None,
        metadata={"decision": "approved"},
    )
    assert event.metadata == {"decision": "approved"}


@pytest.mark.parametrize(
    ("field_name", "payload"),
    (
        ("before_data", []),
        ("before_data", "scalar"),
        ("before_data", 1),
        ("after_data", []),
        ("after_data", True),
        ("metadata", []),
        ("metadata", "scalar"),
    ),
)
def test_service_rejects_invalid_python_payload_types(
    actor, entity_id, field_name, payload
):
    kwargs = {
        "actor": actor,
        "event_type": "category.updated",
        "entity_type": "catalog.Category",
        "entity_id": entity_id,
        "before_data": None,
        "after_data": None,
        "metadata": {},
    }
    kwargs[field_name] = payload
    with pytest.raises(AuditValidationError):
        record_audit_event(**kwargs)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("before_data", ["not", "object"]),
        ("before_data", "scalar"),
        ("after_data", ["array"]),
        ("metadata", ["array"]),
        ("metadata", 1),
    ),
)
def test_db_rejects_non_object_json_where_required(actor, entity_id, field_name, value):
    event_kwargs = {
        "event_type": "category.updated",
        "entity_type": "catalog.Category",
        "entity_id": entity_id,
        "actor": actor,
        "before_data": None,
        "after_data": None,
        "metadata": {},
    }
    event_kwargs[field_name] = value
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            AuditEvent.objects.create(**event_kwargs)
        transaction.set_rollback(True)


def test_db_rejects_json_null_for_nullable_object_fields(actor, entity_id):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_auditevent (
                        id, event_type, occurred_at, entity_type, entity_id,
                        before_data, after_data, metadata, actor_id
                    )
                    VALUES (%s, %s, NOW(), %s, %s, %s::jsonb, NULL, %s::jsonb, %s)
                    """,
                    [
                        uuid.uuid4(),
                        "category.updated",
                        "catalog.Category",
                        entity_id,
                        "null",
                        "{}",
                        actor.pk,
                    ],
                )
        transaction.set_rollback(True)


def test_db_rejects_json_null_for_metadata(actor, entity_id):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_auditevent (
                        id, event_type, occurred_at, entity_type, entity_id,
                        before_data, after_data, metadata, actor_id
                    )
                    VALUES (%s, %s, NOW(), %s, %s, NULL, NULL, %s::jsonb, %s)
                    """,
                    [
                        uuid.uuid4(),
                        "category.updated",
                        "catalog.Category",
                        entity_id,
                        "null",
                        actor.pk,
                    ],
                )
        transaction.set_rollback(True)


def test_db_rejects_json_null_for_after_data(actor, entity_id):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_auditevent (
                        id, event_type, occurred_at, entity_type, entity_id,
                        before_data, after_data, metadata, actor_id
                    )
                    VALUES (%s, %s, NOW(), %s, %s, NULL, %s::jsonb, %s::jsonb, %s)
                    """,
                    [
                        uuid.uuid4(),
                        "category.updated",
                        "catalog.Category",
                        entity_id,
                        "null",
                        "{}",
                        actor.pk,
                    ],
                )
        transaction.set_rollback(True)


@pytest.mark.parametrize("field_name", ("event_type", "entity_type"))
def test_whitespace_text_rejected_at_db(actor, entity_id, field_name):
    values = {
        "event_type": "category.created",
        "entity_type": "catalog.Category",
    }
    values[field_name] = "   "
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            AuditEvent.objects.create(
                actor=actor,
                entity_id=entity_id,
                before_data=None,
                after_data=None,
                metadata={},
                **values,
            )
        transaction.set_rollback(True)


def test_unsaved_actor_rejected_by_service(entity_id):
    user_model = get_user_model()
    unsaved = user_model(username="unsaved-audit-user")
    with pytest.raises(AuditValidationError, match="saved"):
        record_audit_event(
            actor=unsaved,
            event_type="category.created",
            entity_type="catalog.Category",
            entity_id=entity_id,
            before_data=None,
            after_data=None,
        )


def test_invalid_entity_uuid_rejected_by_service(actor):
    with pytest.raises(AuditValidationError, match="UUID"):
        record_audit_event(
            actor=actor,
            event_type="category.created",
            entity_type="catalog.Category",
            entity_id="not-a-uuid",
            before_data=None,
            after_data=None,
        )


def test_entity_id_accepts_uuid_string(actor):
    entity_id = uuid.uuid4()
    event = record_audit_event(
        actor=actor,
        event_type="category.created",
        entity_type="catalog.Category",
        entity_id=str(entity_id),
        before_data=None,
        after_data=None,
    )
    assert event.entity_id == entity_id


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_persisted_instance_save_update_rejected(actor, entity_id):
    event = _create_event(actor, entity_id)
    event.event_type = "category.hacked"
    with pytest.raises(AuditImmutabilityError):
        event.save()


def test_instance_delete_rejected(actor, entity_id):
    event = _create_event(actor, entity_id)
    with pytest.raises(AuditImmutabilityError):
        event.delete()


def test_queryset_update_rejected_by_postgresql_trigger(actor, entity_id):
    event = _create_event(actor, entity_id)
    original = AuditEvent.objects.get(pk=event.pk)
    with transaction.atomic():
        with pytest.raises(DatabaseError, match="immutable"):
            AuditEvent.objects.filter(pk=event.pk).update(event_type="hacked")
        transaction.set_rollback(True)
    refreshed = AuditEvent.objects.get(pk=event.pk)
    assert refreshed.event_type == original.event_type
    assert refreshed.entity_id == original.entity_id


def test_queryset_delete_rejected_by_postgresql_trigger(actor, entity_id):
    event = _create_event(actor, entity_id)
    with transaction.atomic():
        with pytest.raises(DatabaseError, match="immutable"):
            AuditEvent.objects.filter(pk=event.pk).delete()
        transaction.set_rollback(True)
    assert AuditEvent.objects.filter(pk=event.pk).exists()


def test_raw_sql_update_rejected(actor, entity_id):
    event = _create_event(actor, entity_id)
    original = AuditEvent.objects.get(pk=event.pk)
    with transaction.atomic():
        with pytest.raises(DatabaseError, match="immutable"):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE audit_auditevent SET event_type = %s WHERE id = %s",
                    ["hacked", event.pk],
                )
        transaction.set_rollback(True)
    refreshed = AuditEvent.objects.get(pk=event.pk)
    assert refreshed.event_type == original.event_type


def test_raw_sql_delete_rejected(actor, entity_id):
    event = _create_event(actor, entity_id)
    with transaction.atomic():
        with pytest.raises(DatabaseError, match="immutable"):
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM audit_auditevent WHERE id = %s",
                    [event.pk],
                )
        transaction.set_rollback(True)
    assert AuditEvent.objects.filter(pk=event.pk).exists()


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------


def test_actor_deletion_rejected_once_referenced(actor, entity_id):
    _create_event(actor, entity_id)
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            actor.delete()
        transaction.set_rollback(True)
    assert get_user_model().objects.filter(pk=actor.pk).exists()


def test_record_audit_event_rejects_actor_database_alias_mismatch(actor, entity_id):
    actor._state.db = "other"
    before = AuditEvent.objects.count()
    with pytest.raises(AuditValidationError, match="database"):
        record_audit_event(
            actor=actor,
            event_type="category.created",
            entity_type="catalog.Category",
            entity_id=entity_id,
            before_data=None,
            after_data=None,
            using="default",
        )
    assert AuditEvent.objects.count() == before


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def test_admin_is_read_only(actor, entity_id):
    event = _create_event(actor, entity_id)
    model_admin = AuditEventAdmin(AuditEvent, admin.site)
    request = None

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request, event) is False
    assert model_admin.has_delete_permission(request, event) is False
    assert model_admin.get_actions(request) == {}
