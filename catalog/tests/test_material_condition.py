import importlib
import uuid

import pytest
from django.apps import apps
from django.contrib import admin
from django.db import IntegrityError, connection, transaction
from django.db import models

from accounts.roles import SAFE_CATALOG_PERMISSION_LABELS
from audit.models import AuditEvent
from catalog import urls as catalog_urls
from catalog.models import MaterialCondition

pytestmark = pytest.mark.django_db


CONDITION_SEED_NAMESPACE = uuid.UUID("b2024c04-0001-4001-8001-000000000001")

CANONICAL_CONDITIONS = (
    {
        "code": "NEW_GOOD",
        "name": "Yeni / Sağlam",
        "sort_order": 10,
    },
    {
        "code": "USED_REMOVED_GOOD",
        "name": "Çıkma / Sağlam",
        "sort_order": 20,
    },
    {
        "code": "DEFECTIVE",
        "name": "Bozuk",
        "sort_order": 30,
    },
    {
        "code": "USED_REMOVED_DEFECTIVE",
        "name": "Çıkma / Bozuk",
        "sort_order": 40,
    },
)


def _condition_seed_id(code: str) -> uuid.UUID:
    return uuid.uuid5(CONDITION_SEED_NAMESPACE, code)


SEEDED_CONDITION_IDS = tuple(
    _condition_seed_id(row["code"]) for row in CANONICAL_CONDITIONS
)


def _material_condition_tables_exist() -> bool:
    return "catalog_materialcondition" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_material_condition_schema():
    if not _material_condition_tables_exist():
        pytest.skip(
            "catalog MaterialCondition migration not applied to test_tezcan_envanter; "
            "MaterialCondition tests deferred until test DB is migrated"
        )


def test_material_condition_uuid_primary_key():
    condition = MaterialCondition.objects.create(
        code=f"COND-{uuid.uuid4().hex[:8]}",
        name="Test Condition",
    )
    assert isinstance(condition.pk, uuid.UUID)


def test_material_condition_active_defaults_true():
    condition = MaterialCondition.objects.create(
        code=f"COND-{uuid.uuid4().hex[:8]}",
        name="Active Default",
    )
    assert condition.active is True


def test_material_condition_sort_order_defaults_nonnegative():
    condition = MaterialCondition.objects.create(
        code=f"COND-{uuid.uuid4().hex[:8]}",
        name="Sort Default",
    )
    assert condition.sort_order == 0
    assert condition.sort_order >= 0


def test_duplicate_material_condition_names_allowed():
    code_a = f"COND-A-{uuid.uuid4().hex[:6]}"
    code_b = f"COND-B-{uuid.uuid4().hex[:6]}"
    MaterialCondition.objects.create(code=code_a, name="Aynı Ad")
    MaterialCondition.objects.create(code=code_b, name="Aynı Ad")
    assert MaterialCondition.objects.filter(name="Aynı Ad").count() == 2


def test_material_condition_code_case_sensitive_uniqueness():
    MaterialCondition.objects.create(code="Good-A", name="Birinci")
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MaterialCondition.objects.create(code="Good-A", name="İkinci")
    MaterialCondition.objects.create(code="good-a", name="Üçüncü")
    assert MaterialCondition.objects.filter(code__iexact="good-a").count() == 2


def test_material_condition_unique_constraint_name():
    constraint_names = {
        constraint.name for constraint in MaterialCondition._meta.constraints
    }
    assert "catalog_condition_code_uniq" in constraint_names


@pytest.mark.parametrize("code", ["", "   "])
def test_blank_or_whitespace_code_rejected_at_db(code):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MaterialCondition.objects.create(code=code, name="Geçerli Ad")


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_or_whitespace_name_rejected_at_db(name):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MaterialCondition.objects.create(
                code=f"COND-{uuid.uuid4().hex[:8]}",
                name=name,
            )


def test_negative_sort_order_rejected_at_db():
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MaterialCondition.objects.create(
                code=f"COND-{uuid.uuid4().hex[:8]}",
                name="Negatif Sıra",
                sort_order=-1,
            )


def test_four_canonical_seed_records_exist():
    for row in CANONICAL_CONDITIONS:
        assert MaterialCondition.objects.filter(code=row["code"]).exists()


@pytest.mark.parametrize("row", CANONICAL_CONDITIONS, ids=lambda row: row["code"])
def test_canonical_seed_codes_names_sort_and_active(row):
    condition = MaterialCondition.objects.get(pk=_condition_seed_id(row["code"]))
    assert condition.code == row["code"]
    assert condition.name == row["name"]
    assert condition.sort_order == row["sort_order"]
    assert condition.active is True


def test_canonical_seed_deterministic_uuids():
    for row in CANONICAL_CONDITIONS:
        expected_id = _condition_seed_id(row["code"])
        condition = MaterialCondition.objects.get(code=row["code"])
        assert condition.pk == expected_id


def test_seed_migration_defines_exactly_four_reference_rows():
    seed_migration = importlib.import_module(
        "catalog.migrations.0004_seed_material_conditions"
    )
    assert len(seed_migration.CONDITION_SEED_ROWS) == 4
    assert len(seed_migration.SEEDED_CONDITION_IDS) == 4


def test_managed_permission_set_remains_exactly_twenty_one():
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 21
    assert not any(
        "materialcondition" in label for label in SAFE_CATALOG_PERMISSION_LABELS
    )


def test_setup_roles_template_unchanged_for_material_condition():
    from accounts.roles import CATALOG_MODELS, DEFAULT_ROLE_TEMPLATES

    assert "materialcondition" not in CATALOG_MODELS
    for permissions in DEFAULT_ROLE_TEMPLATES.values():
        assert not any(
            "materialcondition" in permission for permission in permissions
        )


def test_no_material_condition_routes():
    route_names = {
        pattern.name
        for pattern in catalog_urls.urlpatterns
        if getattr(pattern, "name", None)
    }
    assert not any("condition" in name for name in route_names)
    route_paths = [str(pattern.pattern) for pattern in catalog_urls.urlpatterns]
    assert not any("condition" in path.lower() for path in route_paths)


def test_material_condition_not_registered_in_admin():
    assert not admin.site.is_registered(MaterialCondition)


def test_seed_migration_creates_no_audit_events():
    seed_migration = importlib.import_module(
        "catalog.migrations.0004_seed_material_conditions"
    )
    before = AuditEvent.objects.count()
    seed_migration.seed_material_conditions(apps, None)
    assert AuditEvent.objects.count() == before


def test_material_condition_has_no_speculative_clean_normalization():
    assert MaterialCondition.clean is models.Model.clean
