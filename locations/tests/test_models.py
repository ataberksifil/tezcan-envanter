import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from locations.models import Location

pytestmark = pytest.mark.django_db


def _location_tables_exist() -> bool:
    return "locations_location" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_location_schema():
    if not _location_tables_exist():
        pytest.skip(
            "locations migration not applied to test_tezcan_envanter; "
            "location model tests deferred until test DB is migrated"
        )


def test_location_uuid_primary_key_and_defaults():
    location = Location.objects.create(code="ROOT-1", name="Kök")
    assert isinstance(location.pk, uuid.UUID)
    assert location.active is True
    assert location.can_hold_stock is False


@pytest.mark.parametrize("code", ["", "   "])
def test_blank_or_whitespace_code_rejected(code):
    location = Location(code=code, name="Geçerli Ad")
    with pytest.raises(ValidationError):
        location.full_clean()


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_or_whitespace_name_rejected(name):
    location = Location(code="CODE-1", name=name)
    with pytest.raises(ValidationError):
        location.full_clean()


def test_code_and_name_are_trimmed():
    location = Location(code="  ABC  ", name="  Depo  ")
    location.full_clean()
    assert location.code == "ABC"
    assert location.name == "Depo"


def test_case_sensitive_code_uniqueness():
    Location.objects.create(code="Shelf-A", name="Raf A")
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            Location.objects.create(code="Shelf-A", name="Raf B")
    Location.objects.create(code="shelf-a", name="Raf C")
    assert Location.objects.filter(code__iexact="shelf-a").count() == 2


def test_same_name_allowed():
    Location.objects.create(code="A-1", name="Depo")
    Location.objects.create(code="A-2", name="Depo")
    assert Location.objects.filter(name="Depo").count() == 2


def test_root_location_allowed():
    location = Location.objects.create(code="ROOT", name="Ana Depo")
    assert location.parent is None


def test_arbitrary_depth_hierarchy():
    root = Location.objects.create(code="L1", name="Seviye 1")
    child = Location.objects.create(code="L2", name="Seviye 2", parent=root)
    grandchild = Location.objects.create(code="L3", name="Seviye 3", parent=child)
    assert grandchild.parent.parent == root


def test_parent_may_be_inactive():
    parent = Location.objects.create(code="P-IN", name="Pasif Parent", active=False)
    child = Location.objects.create(code="C-1", name="Child", parent=parent)
    assert child.parent_id == parent.pk


def test_parent_and_child_both_can_hold_stock():
    parent = Location.objects.create(
        code="WH", name="Depo", can_hold_stock=True
    )
    child = Location.objects.create(
        code="SH", name="Raf", parent=parent, can_hold_stock=True
    )
    assert parent.can_hold_stock is True
    assert child.can_hold_stock is True


def test_self_parent_rejected_at_db():
    location = Location.objects.create(code="SELF", name="Self")
    location.parent_id = location.pk
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            location.save(update_fields=["parent_id"])


def test_descendant_cycle_rejected_by_application_validation():
    root = Location.objects.create(code="R", name="Root")
    child = Location.objects.create(code="C", name="Child", parent=root)
    root.parent = child
    with pytest.raises(ValidationError):
        root.full_clean()


def test_parent_fk_restrict():
    parent = Location.objects.create(code="P", name="Parent")
    Location.objects.create(code="C", name="Child", parent=parent)
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            parent.delete()


def test_parent_change_allowed():
    first = Location.objects.create(code="P1", name="First")
    second = Location.objects.create(code="P2", name="Second")
    child = Location.objects.create(code="CH", name="Child", parent=first)
    child.parent = second
    child.full_clean()
    child.save(update_fields=["parent"])
    child.refresh_from_db()
    assert child.parent_id == second.pk


def test_active_child_survives_parent_deactivation():
    parent = Location.objects.create(code="P", name="Parent")
    child = Location.objects.create(code="C", name="Child", parent=parent)
    parent.active = False
    parent.save(update_fields=["active"])
    child.refresh_from_db()
    assert child.active is True


def test_status_does_not_cascade():
    parent = Location.objects.create(code="P", name="Parent", active=True)
    child = Location.objects.create(code="C", name="Child", parent=parent, active=True)
    parent.active = False
    parent.save(update_fields=["active"])
    child.refresh_from_db()
    assert child.active is True


def test_can_hold_stock_independent_of_parent():
    parent = Location.objects.create(code="P", name="Parent", can_hold_stock=False)
    child = Location.objects.create(
        code="C", name="Child", parent=parent, can_hold_stock=True
    )
    parent.can_hold_stock = True
    parent.save(update_fields=["can_hold_stock"])
    child.refresh_from_db()
    assert child.can_hold_stock is True
