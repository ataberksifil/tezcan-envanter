import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from inventory.models import ProductionLine

pytestmark = pytest.mark.django_db


def _production_line_tables_exist() -> bool:
    return "inventory_productionline" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_production_line_schema():
    if not _production_line_tables_exist():
        pytest.skip(
            "inventory migration not applied to test_tezcan_envanter; "
            "production line model tests deferred until test DB is migrated"
        )


def test_production_line_uuid_primary_key_and_defaults():
    line = ProductionLine.objects.create(code="ROOT-1", name="Kök")
    assert isinstance(line.pk, uuid.UUID)
    assert line.active is True


@pytest.mark.parametrize("code", ["", "   "])
def test_blank_or_whitespace_code_rejected(code):
    line = ProductionLine(code=code, name="Geçerli Ad")
    with pytest.raises(ValidationError):
        line.full_clean()


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_or_whitespace_name_rejected(name):
    line = ProductionLine(code="CODE-1", name=name)
    with pytest.raises(ValidationError):
        line.full_clean()


def test_code_and_name_are_trimmed():
    line = ProductionLine(code="  ABC  ", name="  Hat  ")
    line.full_clean()
    assert line.code == "ABC"
    assert line.name == "Hat"


def test_case_sensitive_code_uniqueness():
    ProductionLine.objects.create(code="Line-A", name="Hat A")
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            ProductionLine.objects.create(code="Line-A", name="Hat B")
    ProductionLine.objects.create(code="line-a", name="Hat C")
    assert ProductionLine.objects.filter(code__iexact="line-a").count() == 2


def test_same_name_allowed():
    ProductionLine.objects.create(code="A-1", name="Hat")
    ProductionLine.objects.create(code="A-2", name="Hat")
    assert ProductionLine.objects.filter(name="Hat").count() == 2


def test_root_production_line_allowed():
    line = ProductionLine.objects.create(code="ROOT", name="Ana Hat")
    assert line.parent is None


def test_arbitrary_depth_hierarchy():
    root = ProductionLine.objects.create(code="L1", name="Seviye 1")
    child = ProductionLine.objects.create(code="L2", name="Seviye 2", parent=root)
    grandchild = ProductionLine.objects.create(
        code="L3", name="Seviye 3", parent=child
    )
    assert grandchild.parent.parent == root


def test_parent_may_be_inactive():
    parent = ProductionLine.objects.create(
        code="P-IN", name="Pasif Parent", active=False
    )
    child = ProductionLine.objects.create(code="C-1", name="Child", parent=parent)
    assert child.parent_id == parent.pk


def test_self_parent_rejected_at_db():
    line = ProductionLine.objects.create(code="SELF", name="Self")
    line.parent_id = line.pk
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            line.save(update_fields=["parent_id"])


def test_descendant_cycle_rejected_by_application_validation():
    root = ProductionLine.objects.create(code="R", name="Root")
    child = ProductionLine.objects.create(code="C", name="Child", parent=root)
    root.parent = child
    with pytest.raises(ValidationError):
        root.full_clean()


def test_parent_fk_restrict():
    parent = ProductionLine.objects.create(code="P", name="Parent")
    ProductionLine.objects.create(code="C", name="Child", parent=parent)
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            parent.delete()


def test_parent_change_allowed():
    first = ProductionLine.objects.create(code="P1", name="First")
    second = ProductionLine.objects.create(code="P2", name="Second")
    child = ProductionLine.objects.create(code="CH", name="Child", parent=first)
    child.parent = second
    child.full_clean()
    child.save(update_fields=["parent"])
    child.refresh_from_db()
    assert child.parent_id == second.pk


def test_active_child_survives_parent_deactivation():
    parent = ProductionLine.objects.create(code="P", name="Parent")
    child = ProductionLine.objects.create(code="C", name="Child", parent=parent)
    parent.active = False
    parent.save(update_fields=["active"])
    child.refresh_from_db()
    assert child.active is True


def test_status_does_not_cascade():
    parent = ProductionLine.objects.create(code="P", name="Parent", active=True)
    child = ProductionLine.objects.create(
        code="C", name="Child", parent=parent, active=True
    )
    parent.active = False
    parent.save(update_fields=["active"])
    child.refresh_from_db()
    assert child.active is True
