from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from locations.models import Location
from locations.services.locations import (
    DUPLICATE_CODE_MESSAGE,
    LOCATION_CODE_UNIQUE_CONSTRAINT,
    LOCATION_HIERARCHY_ADVISORY_LOCK_KEY,
    create_location,
    update_location,
)

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


def _location_tables_exist() -> bool:
    return "locations_location" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_location_schema():
    if not _location_tables_exist():
        pytest.skip(
            "locations migration not applied to test_tezcan_envanter; "
            "location concurrency tests deferred until test DB is migrated"
        )


def _create_actor(username=None):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=username or f"loc-conc-{uuid.uuid4().hex[:8]}",
        password=PASSWORD,
    )
    perms = Permission.objects.filter(
        content_type__app_label="locations",
        codename__in=("view_location", "add_location", "change_location"),
    )
    user.user_permissions.add(*perms)
    return user


class _FakeUniqueViolationCause(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.diag = type("Diag", (), {"constraint_name": constraint_name})()


def _make_location_code_unique_integrity_error() -> IntegrityError:
    exc = IntegrityError("duplicate key value violates unique constraint")
    exc.__cause__ = _FakeUniqueViolationCause(LOCATION_CODE_UNIQUE_CONSTRAINT)
    return exc


def test_named_code_unique_constraint_is_db_enforced():
    code = f"DB-UNIQ-{uuid.uuid4().hex[:6]}"
    Location.objects.create(code=code, name="First")
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            Location.objects.create(code=code, name="Second")


def test_service_translates_duplicate_code_integrity_error():
    actor = _create_actor()
    with patch.object(
        Location,
        "save",
        side_effect=_make_location_code_unique_integrity_error(),
    ):
        with pytest.raises(ValidationError) as exc_info:
            create_location(
                actor=actor,
                code=f"RACE-{uuid.uuid4().hex[:6]}",
                name="Race",
            )
    assert DUPLICATE_CODE_MESSAGE in str(exc_info.value)
    assert not Location.objects.filter(name="Race").exists()


def test_unrelated_integrity_error_is_not_translated():
    actor = _create_actor()
    with patch.object(Location, "save", side_effect=IntegrityError("other failure")):
        with pytest.raises(IntegrityError):
            create_location(
                actor=actor,
                code=f"OTHER-{uuid.uuid4().hex[:6]}",
                name="Other",
            )


def test_hierarchy_mutation_acquires_advisory_lock():
    actor = _create_actor()
    parent = Location.objects.create(code="P-LOCK", name="Parent")
    executed: list = []

    def tracking_lock(using: str) -> None:
        executed.append((using, LOCATION_HIERARCHY_ADVISORY_LOCK_KEY))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [LOCATION_HIERARCHY_ADVISORY_LOCK_KEY],
            )

    with patch(
        "locations.services.locations._acquire_hierarchy_lock",
        side_effect=tracking_lock,
    ):
        create_location(
            actor=actor,
            code="C-LOCK",
            name="Child",
            parent_id=parent.pk,
        )

    assert executed == [("default", LOCATION_HIERARCHY_ADVISORY_LOCK_KEY)]


def test_parent_update_revalidates_cycle_after_hierarchy_lock():
    actor = _create_actor()
    a = Location.objects.create(code="A", name="A")
    b = Location.objects.create(code="B", name="B", parent=a)

    with patch(
        "locations.services.locations._acquire_hierarchy_lock",
        wraps=__import__(
            "locations.services.locations", fromlist=["_acquire_hierarchy_lock"]
        )._acquire_hierarchy_lock,
    ) as lock_mock:
        with pytest.raises(ValidationError) as exc_info:
            update_location(
                actor=actor,
                location_id=a.pk,
                code=a.code,
                name=a.name,
                parent_id=b.pk,
                can_hold_stock=False,
            )
        lock_mock.assert_called_once()

    assert "parent" in exc_info.value.error_dict
    a.refresh_from_db()
    assert a.parent_id is None


def test_postgresql_unique_constraint_is_final_arbitrator_for_service_create():
    actor = _create_actor()
    code = f"SRV-UNIQ-{uuid.uuid4().hex[:6]}"
    Location.objects.create(code=code, name="Existing")

    def strip_only_clean(self, *args, **kwargs):
        if self.code is not None:
            self.code = self.code.strip()
        if self.name is not None:
            self.name = self.name.strip()

    with patch.object(Location, "full_clean", strip_only_clean):
        with pytest.raises(ValidationError) as exc_info:
            create_location(actor=actor, code=code, name="Concurrent")
    assert DUPLICATE_CODE_MESSAGE in str(exc_info.value)
    assert Location.objects.filter(name="Concurrent").count() == 0
