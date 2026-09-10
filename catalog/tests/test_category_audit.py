from __future__ import annotations

import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from accounts.roles import ADMIN_MANAGER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category
from catalog.services.categories import (
    create_category,
    set_category_active,
    update_category,
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


def _catalog_and_audit_tables_exist() -> bool:
    table_names = connection.introspection.table_names()
    return "catalog_category" in table_names and "audit_auditevent" in table_names


@pytest.fixture(autouse=True)
def require_catalog_and_audit_schema():
    if not _catalog_and_audit_tables_exist():
        pytest.skip(
            "catalog/audit migrations not applied to test_tezcan_envanter; "
            "category audit tests deferred until test DB is migrated"
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
    return _role_user(ADMIN_MANAGER, f"cat-audit-admin-{uuid.uuid4().hex[:8]}")


def _unauthorized_actor():
    return _create_ordinary_user(f"cat-audit-none-{uuid.uuid4().hex[:8]}")


def _events_for(entity_id):
    return AuditEvent.objects.filter(
        entity_type="catalog.category",
        entity_id=entity_id,
    ).order_by("occurred_at", "id")


def _assert_canonical_snapshot(payload, category):
    assert payload is not None
    assert set(payload.keys()) == CANONICAL_FIELDS
    for key in FORBIDDEN_SNAPSHOT_KEYS:
        assert key not in payload
    assert payload == {
        "id": str(category.id),
        "code": category.code,
        "name": category.name,
        "parent_id": str(category.parent_id) if category.parent_id is not None else None,
        "active": category.active,
    }


# ---------------------------------------------------------------------------
# CREATE database alias binding
# ---------------------------------------------------------------------------


def test_unsaved_category_has_none_db_until_explicitly_bound():
    category = Category(name="UNBOUND")
    assert category._state.db is None


def test_create_category_full_clean_sees_requested_db_alias():
    """Regression: unsaved Category must be bound to `using` before full_clean()."""
    actor = _admin_actor()
    parent = Category.objects.create(name="ALIAS-PARENT")
    using = "default"
    seen_db: list[str | None] = []
    original_full_clean = Category.full_clean

    def tracking_full_clean(self, *args, **kwargs):
        seen_db.append(self._state.db)
        assert self._state.db == using, (
            "full_clean() must run with the requested database alias; "
            f"expected {using!r}, got {self._state.db!r}"
        )
        return original_full_clean(self, *args, **kwargs)

    with patch.object(Category, "full_clean", tracking_full_clean):
        create_category(
            actor=actor,
            code="ALIAS-1",
            name="ALIAS-CHILD",
            parent_id=parent.pk,
            using=using,
        )

    assert seen_db == [using]


# ---------------------------------------------------------------------------
# CREATE event content
# ---------------------------------------------------------------------------


def test_successful_create_produces_exactly_one_canonical_event():
    actor = _admin_actor()
    parent = Category.objects.create(name="CREATE-PARENT", code="CP")
    before_events = AuditEvent.objects.count()

    result = create_category(
        actor=actor,
        code="CHILD-CODE",
        name="CREATE-CHILD",
        parent_id=parent.pk,
    )
    category = result.category
    events = list(_events_for(category.id))

    assert result.changed is True
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.category.created"
    assert event.actor_id == actor.pk
    assert event.entity_type == "catalog.category"
    assert event.entity_id == category.id
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, category)
    assert event.after_data["code"] == "CHILD-CODE"
    assert event.after_data["name"] == "CREATE-CHILD"
    assert event.after_data["parent_id"] == str(parent.pk)
    assert event.after_data["active"] is True


def test_create_top_level_snapshot_has_null_parent_and_code():
    actor = _admin_actor()
    result = create_category(actor=actor, code="   ", name=" TOP ", parent_id=None)
    category = result.category
    event = _events_for(category.id).get()
    assert category.code is None
    assert category.name == "TOP"
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, category)
    assert event.after_data["code"] is None
    assert event.after_data["parent_id"] is None


# ---------------------------------------------------------------------------
# UPDATE event content
# ---------------------------------------------------------------------------


def test_successful_update_produces_exactly_one_event_with_before_after():
    actor = _admin_actor()
    old_parent = Category.objects.create(name="OLD-PARENT")
    new_parent = Category.objects.create(name="NEW-PARENT")
    category = Category.objects.create(
        name="BEFORE-NAME",
        code="BEFORE-CODE",
        parent=old_parent,
        active=True,
    )
    before_state = {
        "id": str(category.id),
        "code": "BEFORE-CODE",
        "name": "BEFORE-NAME",
        "parent_id": str(old_parent.pk),
        "active": True,
    }
    before_events = AuditEvent.objects.count()

    result = update_category(
        actor=actor,
        category_id=category.pk,
        code="AFTER-CODE",
        name="AFTER-NAME",
        parent_id=new_parent.pk,
    )
    category.refresh_from_db()
    events = list(_events_for(category.id))

    assert result.changed is True
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.category.updated"
    assert event.actor_id == actor.pk
    assert event.entity_type == "catalog.category"
    assert event.entity_id == category.id
    assert event.before_data == before_state
    _assert_canonical_snapshot(event.after_data, category)
    assert event.after_data["name"] == "AFTER-NAME"
    assert event.after_data["code"] == "AFTER-CODE"
    assert event.after_data["parent_id"] == str(new_parent.pk)
    assert event.after_data["active"] is True
    for payload in (event.before_data, event.after_data):
        assert set(payload.keys()) == CANONICAL_FIELDS
        for key in FORBIDDEN_SNAPSHOT_KEYS:
            assert key not in payload


def test_update_does_not_change_active_or_created_at():
    actor = _admin_actor()
    category = Category.objects.create(name="KEEP-META", active=False)
    created_at = category.created_at
    update_category(
        actor=actor,
        category_id=category.pk,
        code="NEW",
        name="KEEP-META-2",
        parent_id=None,
    )
    category.refresh_from_db()
    assert category.active is False
    assert category.created_at == created_at
    event = _events_for(category.id).get()
    assert event.after_data["active"] is False


# ---------------------------------------------------------------------------
# STATUS event content
# ---------------------------------------------------------------------------


def test_deactivate_produces_exactly_one_event_with_active_transition():
    actor = _admin_actor()
    category = Category.objects.create(name="DEACTIVATE-ME", code="D1", active=True)
    before_events = AuditEvent.objects.count()

    result = set_category_active(
        actor=actor,
        category_id=category.pk,
        active=False,
    )
    category.refresh_from_db()
    events = list(_events_for(category.id))

    assert result.changed is True
    assert category.active is False
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.category.deactivated"
    assert event.actor_id == actor.pk
    assert event.entity_type == "catalog.category"
    assert event.entity_id == category.id
    _assert_canonical_snapshot(event.before_data, Category(
        id=category.id,
        code="D1",
        name="DEACTIVATE-ME",
        parent_id=None,
        active=True,
    ))
    assert event.before_data["active"] is True
    _assert_canonical_snapshot(event.after_data, category)
    assert event.after_data["active"] is False
    assert event.after_data["name"] == "DEACTIVATE-ME"
    assert event.after_data["code"] == "D1"


def test_reactivate_produces_exactly_one_event_with_active_transition():
    actor = _admin_actor()
    category = Category.objects.create(name="REACTIVATE-ME", active=False)
    before_events = AuditEvent.objects.count()

    result = set_category_active(
        actor=actor,
        category_id=category.pk,
        active=True,
    )
    category.refresh_from_db()
    events = list(_events_for(category.id))

    assert result.changed is True
    assert category.active is True
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.category.reactivated"
    assert event.actor_id == actor.pk
    assert event.before_data["active"] is False
    assert event.after_data["active"] is True
    _assert_canonical_snapshot(event.after_data, category)


# ---------------------------------------------------------------------------
# NO-OP / failure
# ---------------------------------------------------------------------------


def test_noop_update_does_not_save_or_audit():
    actor = _admin_actor()
    parent = Category.objects.create(name="NOOP-PARENT")
    category = Category.objects.create(
        name="NOOP-NAME",
        code=None,
        parent=parent,
        active=True,
    )
    original_updated_at = category.updated_at
    before_events = AuditEvent.objects.count()

    result = update_category(
        actor=actor,
        category_id=category.pk,
        code="",
        name="NOOP-NAME",
        parent_id=parent.pk,
    )
    category.refresh_from_db()

    assert result.changed is False
    assert category.updated_at == original_updated_at
    assert category.name == "NOOP-NAME"
    assert category.code is None
    assert category.parent_id == parent.pk
    assert AuditEvent.objects.count() == before_events
    assert _events_for(category.id).count() == 0


def test_repeated_deactivate_is_noop():
    actor = _admin_actor()
    category = Category.objects.create(name="ALREADY-OFF", active=True)
    first = set_category_active(actor=actor, category_id=category.pk, active=False)
    category.refresh_from_db()
    updated_at = category.updated_at
    event_count = AuditEvent.objects.count()

    second = set_category_active(actor=actor, category_id=category.pk, active=False)
    category.refresh_from_db()

    assert first.changed is True
    assert second.changed is False
    assert category.active is False
    assert category.updated_at == updated_at
    assert AuditEvent.objects.count() == event_count
    assert _events_for(category.id).count() == 1
    assert _events_for(category.id).get().event_type == "catalog.category.deactivated"


def test_repeated_reactivate_is_noop():
    actor = _admin_actor()
    category = Category.objects.create(name="ALREADY-ON", active=False)
    first = set_category_active(actor=actor, category_id=category.pk, active=True)
    category.refresh_from_db()
    updated_at = category.updated_at
    event_count = AuditEvent.objects.count()

    second = set_category_active(actor=actor, category_id=category.pk, active=True)
    category.refresh_from_db()

    assert first.changed is True
    assert second.changed is False
    assert category.active is True
    assert category.updated_at == updated_at
    assert AuditEvent.objects.count() == event_count
    assert _events_for(category.id).count() == 1


def test_validation_failure_does_not_mutate_or_audit():
    actor = _admin_actor()
    category = Category.objects.create(name="VALID-KEEP", code="KEEP")
    original_updated_at = category.updated_at
    before_count = Category.objects.count()
    before_events = AuditEvent.objects.count()

    with pytest.raises(ValidationError):
        update_category(
            actor=actor,
            category_id=category.pk,
            code="CHANGED",
            name="   ",
            parent_id=None,
        )
    with pytest.raises(ValidationError):
        create_category(actor=actor, code="X", name="   ", parent_id=None)
    with pytest.raises(ValidationError):
        update_category(
            actor=actor,
            category_id=category.pk,
            code="KEEP",
            name="VALID-KEEP",
            parent_id=category.pk,
        )

    category.refresh_from_db()
    assert category.name == "VALID-KEEP"
    assert category.code == "KEEP"
    assert category.parent_id is None
    assert category.updated_at == original_updated_at
    assert Category.objects.count() == before_count
    assert AuditEvent.objects.count() == before_events


def test_unauthorized_create_has_no_category_or_audit():
    actor = _unauthorized_actor()
    before_ids = set(Category.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        create_category(actor=actor, code="NOPE", name="UNAUTH-CREATE")

    assert set(Category.objects.values_list("pk", flat=True)) == before_ids
    assert not Category.objects.filter(name="UNAUTH-CREATE").exists()
    assert AuditEvent.objects.count() == before_events


def test_unauthorized_update_leaves_category_and_has_no_audit():
    actor = _unauthorized_actor()
    category = Category.objects.create(name="UNAUTH-TARGET", code="KEEP")
    updated_at = category.updated_at
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        update_category(
            actor=actor,
            category_id=category.pk,
            code="HACK",
            name="HACKED",
            parent_id=None,
        )

    category.refresh_from_db()
    assert category.name == "UNAUTH-TARGET"
    assert category.code == "KEEP"
    assert category.updated_at == updated_at
    assert AuditEvent.objects.count() == before_events
    assert _events_for(category.id).count() == 0


def test_unauthorized_status_leaves_category_and_has_no_audit():
    actor = _unauthorized_actor()
    category = Category.objects.create(name="UNAUTH-STATE", active=True)
    updated_at = category.updated_at
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        set_category_active(actor=actor, category_id=category.pk, active=False)

    category.refresh_from_db()
    assert category.active is True
    assert category.updated_at == updated_at
    assert AuditEvent.objects.count() == before_events
    assert _events_for(category.id).count() == 0


def test_technician_service_calls_are_unauthorized():
    actor = _role_user(TECHNICIAN, "tech-service-denied")
    category = Category.objects.create(name="TECH-DENIED", active=True)
    before_events = AuditEvent.objects.count()

    with pytest.raises(PermissionDenied):
        create_category(actor=actor, code=None, name="TECH-CREATE")
    with pytest.raises(PermissionDenied):
        update_category(
            actor=actor,
            category_id=category.pk,
            code=None,
            name="TECH-EDIT",
            parent_id=None,
        )
    with pytest.raises(PermissionDenied):
        set_category_active(actor=actor, category_id=category.pk, active=False)

    category.refresh_from_db()
    assert category.name == "TECH-DENIED"
    assert category.active is True
    assert AuditEvent.objects.count() == before_events
    assert not Category.objects.filter(name="TECH-CREATE").exists()


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
    before_ids = set(Category.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.categories.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            create_category(actor=actor, code="RB", name="ROLLBACK-CREATE")

    assert set(Category.objects.values_list("pk", flat=True)) == before_ids
    assert not Category.objects.filter(name="ROLLBACK-CREATE").exists()
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_update_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    category = Category.objects.create(name="ROLLBACK-OLD", code="OLD")
    original_updated_at = category.updated_at
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.categories.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            update_category(
                actor=actor,
                category_id=category.pk,
                code="NEW",
                name="ROLLBACK-NEW",
                parent_id=None,
            )

    category.refresh_from_db()
    assert category.name == "ROLLBACK-OLD"
    assert category.code == "OLD"
    assert category.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events
    assert _events_for(category.id).count() == 0


@pytest.mark.django_db(transaction=True)
def test_deactivate_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    category = Category.objects.create(name="ROLLBACK-ACTIVE", active=True)
    original_updated_at = category.updated_at
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.categories.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_category_active(actor=actor, category_id=category.pk, active=False)

    category.refresh_from_db()
    assert category.active is True
    assert category.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_reactivate_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    category = Category.objects.create(name="ROLLBACK-INACTIVE", active=False)
    original_updated_at = category.updated_at
    before_events = AuditEvent.objects.count()

    with patch(
        "catalog.services.categories.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_category_active(actor=actor, category_id=category.pk, active=True)

    category.refresh_from_db()
    assert category.active is False
    assert category.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


# ---------------------------------------------------------------------------
# HTTP integration / UI regression extras
# ---------------------------------------------------------------------------


def test_http_create_records_audit_event_for_request_user(app_client):
    user = _admin_actor()
    _login(app_client, user)
    before_events = AuditEvent.objects.count()
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "HTTP-CREATED", "code": "HTTP-1", "parent": ""},
        follow=True,
    )
    assert response.status_code == 200
    assert "Kategori oluşturuldu." in response.content.decode()
    category = Category.objects.get(name="HTTP-CREATED")
    assert AuditEvent.objects.count() == before_events + 1
    event = _events_for(category.id).get()
    assert event.event_type == "catalog.category.created"
    assert event.actor_id == user.pk
    assert event.entity_type == "catalog.category"
    assert event.entity_id == category.id
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, category)


def test_http_update_records_audit_event_for_request_user(app_client):
    user = _admin_actor()
    category = Category.objects.create(name="HTTP-BEFORE")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "HTTP-AFTER", "code": "HA", "parent": ""},
        follow=True,
    )
    assert response.status_code == 200
    assert "Kategori güncellendi." in response.content.decode()
    category.refresh_from_db()
    event = _events_for(category.id).get()
    assert event.event_type == "catalog.category.updated"
    assert event.actor_id == user.pk
    assert event.before_data["name"] == "HTTP-BEFORE"
    assert event.after_data["name"] == "HTTP-AFTER"
    assert event.after_data["code"] == "HA"


def test_http_deactivate_and_reactivate_record_expected_events(app_client):
    user = _admin_actor()
    category = Category.objects.create(name="HTTP-STATE", active=True)
    _login(app_client, user)

    deactivate = app_client.post(
        reverse("catalog:category-deactivate", args=[category.pk]),
        follow=True,
    )
    assert "Kategori pasifleştirildi." in deactivate.content.decode()
    category.refresh_from_db()
    deactivate_event = _events_for(category.id).get()
    assert deactivate_event.event_type == "catalog.category.deactivated"
    assert deactivate_event.actor_id == user.pk
    assert deactivate_event.before_data["active"] is True
    assert deactivate_event.after_data["active"] is False

    reactivate = app_client.post(
        reverse("catalog:category-reactivate", args=[category.pk]),
        follow=True,
    )
    assert "Kategori aktifleştirildi." in reactivate.content.decode()
    events = list(_events_for(category.id))
    assert len(events) == 2
    assert events[1].event_type == "catalog.category.reactivated"
    assert events[1].actor_id == user.pk
    assert events[1].before_data["active"] is False
    assert events[1].after_data["active"] is True


def test_http_noop_update_and_status_do_not_audit(app_client):
    user = _admin_actor()
    category = Category.objects.create(name="HTTP-NOOP", code="N", active=True)
    _login(app_client, user)

    update = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "HTTP-NOOP", "code": "N", "parent": ""},
        follow=True,
    )
    assert "Değişiklik yapılmadı." in update.content.decode()
    assert _events_for(category.id).count() == 0

    first_deactivate = app_client.post(
        reverse("catalog:category-deactivate", args=[category.pk]),
        follow=True,
    )
    assert "Kategori pasifleştirildi." in first_deactivate.content.decode()
    assert _events_for(category.id).count() == 1
    category.refresh_from_db()
    updated_at = category.updated_at

    second_deactivate = app_client.post(
        reverse("catalog:category-deactivate", args=[category.pk]),
        follow=True,
    )
    assert "Kategori zaten pasif." in second_deactivate.content.decode()
    category.refresh_from_db()
    assert category.updated_at == updated_at
    assert _events_for(category.id).count() == 1

    first_reactivate = app_client.post(
        reverse("catalog:category-reactivate", args=[category.pk]),
        follow=True,
    )
    assert "Kategori aktifleştirildi." in first_reactivate.content.decode()
    category.refresh_from_db()
    reactivated_at = category.updated_at
    assert _events_for(category.id).count() == 2

    second_reactivate = app_client.post(
        reverse("catalog:category-reactivate", args=[category.pk]),
        follow=True,
    )
    assert "Kategori zaten aktif." in second_reactivate.content.decode()
    category.refresh_from_db()
    assert category.updated_at == reactivated_at
    assert _events_for(category.id).count() == 2


def test_http_reads_and_validation_failures_are_not_audited(app_client):
    user = _admin_actor()
    category = Category.objects.create(name="READ-ONLY-TARGET", code="RO")
    _login(app_client, user)
    before_events = AuditEvent.objects.count()

    assert app_client.get("/catalog/categories/").status_code == 200
    assert app_client.get("/catalog/categories/", {"q": "READ", "status": "active"}).status_code == 200
    assert app_client.get("/catalog/categories/new/").status_code == 200
    assert app_client.get(reverse("catalog:category-update", args=[category.pk])).status_code == 200

    invalid_create = app_client.post(
        "/catalog/categories/new/",
        {"name": "   ", "code": "X", "parent": ""},
    )
    assert invalid_create.status_code == 200
    assert invalid_create.context["form"].errors

    invalid_update = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "   ", "code": "CHANGED", "parent": ""},
    )
    assert invalid_update.status_code == 200
    assert invalid_update.context["form"].errors
    category.refresh_from_db()
    assert category.name == "READ-ONLY-TARGET"
    assert category.code == "RO"
    assert AuditEvent.objects.count() == before_events


def test_http_denied_attempts_are_not_audited(app_client):
    category = Category.objects.create(name="DENIED-TARGET", active=True)
    user = _role_user(TECHNICIAN, "http-denied-tech")
    _login(app_client, user)
    before_events = AuditEvent.objects.count()

    create = app_client.post(
        "/catalog/categories/new/",
        {"name": "DENIED-CREATE", "code": "", "parent": ""},
    )
    update = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "DENIED-EDIT", "code": "", "parent": ""},
    )
    deactivate = app_client.post(
        reverse("catalog:category-deactivate", args=[category.pk])
    )
    assert create.status_code == 403
    assert update.status_code == 403
    assert deactivate.status_code == 403
    assert not Category.objects.filter(name="DENIED-CREATE").exists()
    category.refresh_from_db()
    assert category.name == "DENIED-TARGET"
    assert category.active is True
    assert AuditEvent.objects.count() == before_events


def test_unknown_uuid_remains_404_and_is_not_audited(app_client):
    user = _admin_actor()
    _login(app_client, user)
    missing = uuid.uuid4()
    before_events = AuditEvent.objects.count()

    assert app_client.get(reverse("catalog:category-update", args=[missing])).status_code == 404
    assert app_client.post(
        reverse("catalog:category-update", args=[missing]),
        {"name": "GONE", "code": "", "parent": ""},
    ).status_code == 404
    assert app_client.post(
        reverse("catalog:category-deactivate", args=[missing])
    ).status_code == 404
    assert app_client.post(
        reverse("catalog:category-reactivate", args=[missing])
    ).status_code == 404
    assert AuditEvent.objects.count() == before_events


def test_anonymous_mutations_redirect_to_login_without_audit(app_client):
    category = Category.objects.create(name="ANON-TARGET", active=True)
    before_events = AuditEvent.objects.count()
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "ANON-CREATE", "code": "", "parent": ""},
    )
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == ["/catalog/categories/new/"]
    assert not Category.objects.filter(name="ANON-CREATE").exists()
    assert AuditEvent.objects.count() == before_events
    assert category.active is True
