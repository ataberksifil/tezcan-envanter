from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from inventory.models import ProductionLine
from inventory.services.production_lines import (
    DUPLICATE_CODE_MESSAGE,
    PRODUCTION_LINE_CODE_UNIQUE_CONSTRAINT,
    PRODUCTION_LINE_HIERARCHY_ADVISORY_LOCK_KEY,
    create_production_line,
    update_production_line,
)
from locations.services.locations import LOCATION_HIERARCHY_ADVISORY_LOCK_KEY

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


def _production_line_tables_exist() -> bool:
    return "inventory_productionline" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_production_line_schema():
    if not _production_line_tables_exist():
        pytest.skip(
            "inventory migration not applied to test_tezcan_envanter; "
            "production line concurrency tests deferred until test DB is migrated"
        )


def _create_actor(username=None):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=username or f"pl-conc-{uuid.uuid4().hex[:8]}",
        password=PASSWORD,
    )
    perms = Permission.objects.filter(
        content_type__app_label="inventory",
        codename__in=(
            "view_productionline",
            "add_productionline",
            "change_productionline",
        ),
    )
    user.user_permissions.add(*perms)
    return user


class _FakeUniqueViolationCause(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.diag = type("Diag", (), {"constraint_name": constraint_name})()


def _make_production_line_code_unique_integrity_error() -> IntegrityError:
    exc = IntegrityError("duplicate key value violates unique constraint")
    exc.__cause__ = _FakeUniqueViolationCause(PRODUCTION_LINE_CODE_UNIQUE_CONSTRAINT)
    return exc


def test_named_code_unique_constraint_is_db_enforced():
    code = f"DB-UNIQ-{uuid.uuid4().hex[:6]}"
    ProductionLine.objects.create(code=code, name="First")
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            ProductionLine.objects.create(code=code, name="Second")


def test_service_translates_duplicate_code_integrity_error():
    actor = _create_actor()
    with patch.object(
        ProductionLine,
        "save",
        side_effect=_make_production_line_code_unique_integrity_error(),
    ):
        with pytest.raises(ValidationError) as exc_info:
            create_production_line(
                actor=actor,
                code=f"RACE-{uuid.uuid4().hex[:6]}",
                name="Race",
            )
    assert DUPLICATE_CODE_MESSAGE in str(exc_info.value)
    assert not ProductionLine.objects.filter(name="Race").exists()


def test_unrelated_integrity_error_is_not_translated():
    actor = _create_actor()
    with patch.object(
        ProductionLine, "save", side_effect=IntegrityError("other failure")
    ):
        with pytest.raises(IntegrityError):
            create_production_line(
                actor=actor,
                code=f"OTHER-{uuid.uuid4().hex[:6]}",
                name="Other",
            )


def test_hierarchy_mutation_acquires_advisory_lock():
    actor = _create_actor()
    parent = ProductionLine.objects.create(code="P-LOCK", name="Parent")
    executed: list = []

    def tracking_lock(using: str) -> None:
        executed.append((using, PRODUCTION_LINE_HIERARCHY_ADVISORY_LOCK_KEY))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [PRODUCTION_LINE_HIERARCHY_ADVISORY_LOCK_KEY],
            )

    with patch(
        "inventory.services.production_lines._acquire_hierarchy_lock",
        side_effect=tracking_lock,
    ):
        create_production_line(
            actor=actor,
            code="C-LOCK",
            name="Child",
            parent_id=parent.pk,
        )

    assert executed == [("default", PRODUCTION_LINE_HIERARCHY_ADVISORY_LOCK_KEY)]


def test_production_line_advisory_lock_key_differs_from_location():
    assert (
        PRODUCTION_LINE_HIERARCHY_ADVISORY_LOCK_KEY
        != LOCATION_HIERARCHY_ADVISORY_LOCK_KEY
    )


def test_parent_update_revalidates_cycle_after_hierarchy_lock():
    actor = _create_actor()
    a = ProductionLine.objects.create(code="A", name="A")
    b = ProductionLine.objects.create(code="B", name="B", parent=a)

    with patch(
        "inventory.services.production_lines._acquire_hierarchy_lock",
        wraps=__import__(
            "inventory.services.production_lines", fromlist=["_acquire_hierarchy_lock"]
        )._acquire_hierarchy_lock,
    ) as lock_mock:
        with pytest.raises(ValidationError) as exc_info:
            update_production_line(
                actor=actor,
                production_line_id=a.pk,
                code=a.code,
                name=a.name,
                parent_id=b.pk,
            )
        lock_mock.assert_called_once()

    assert "parent" in exc_info.value.error_dict
    a.refresh_from_db()
    assert a.parent_id is None


def test_postgresql_unique_constraint_is_final_arbitrator_for_service_create():
    actor = _create_actor()
    code = f"SRV-UNIQ-{uuid.uuid4().hex[:6]}"
    ProductionLine.objects.create(code=code, name="Existing")

    def strip_only_clean(self, *args, **kwargs):
        if self.code is not None:
            self.code = self.code.strip()
        if self.name is not None:
            self.name = self.name.strip()

    with patch.object(ProductionLine, "full_clean", strip_only_clean):
        with pytest.raises(ValidationError) as exc_info:
            create_production_line(actor=actor, code=code, name="Concurrent")
    assert DUPLICATE_CODE_MESSAGE in str(exc_info.value)
    assert ProductionLine.objects.filter(name="Concurrent").count() == 0
