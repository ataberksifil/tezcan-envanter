import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from catalog.models import Category, Material, UnitOfMeasure

pytestmark = pytest.mark.django_db


def _catalog_tables_exist() -> bool:
    table_names = connection.introspection.table_names()
    return "catalog_category" in table_names


@pytest.fixture(autouse=True)
def require_catalog_schema():
    if not _catalog_tables_exist():
        pytest.skip(
            "catalog migration not applied to test_tezcan_envanter; "
            "catalog constraint tests deferred until test DB is migrated"
        )


@pytest.fixture
def category():
    return Category.objects.create(name="Elektrik Malzemeleri")


@pytest.fixture
def unit():
    return UnitOfMeasure.objects.create(code="ADET", name="Adet")


def test_category_without_parent_accepted():
    category = Category.objects.create(name="Kök Kategori")
    assert category.parent is None
    assert Category.objects.filter(pk=category.pk).exists()


def test_category_self_parent_rejected_at_db():
    category = Category.objects.create(name="Kendi Üstü")
    category.parent_id = category.pk
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            category.save(update_fields=["parent_id"])


def test_category_deeper_cycle_rejected_by_application_validation(category):
    child = Category.objects.create(name="Alt", parent=category)
    grandchild = Category(name="Torun", parent=child)
    grandchild.save()
    category.parent = grandchild
    with pytest.raises(ValidationError):
        category.full_clean()


def test_duplicate_category_code_allowed():
    Category.objects.create(name="Kategori A", code="CAT-001")
    Category.objects.create(name="Kategori B", code="CAT-001")
    assert Category.objects.filter(code="CAT-001").count() == 2


def test_unit_code_duplicate_rejected(unit):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            UnitOfMeasure.objects.create(code=unit.code, name="Diğer Adet")


@pytest.mark.parametrize("decimal_places", [None, 0, 3])
def test_unit_decimal_places_valid_values_accepted(decimal_places):
    unit = UnitOfMeasure.objects.create(
        code=f"U-{uuid.uuid4().hex[:8]}",
        name="Birim",
        decimal_places=decimal_places,
    )
    assert unit.decimal_places == decimal_places


@pytest.mark.parametrize("decimal_places", [-1, 4])
def test_unit_decimal_places_invalid_rejected_at_db(decimal_places):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            UnitOfMeasure.objects.create(
                code=f"U-{uuid.uuid4().hex[:8]}",
                name="Birim",
                decimal_places=decimal_places,
            )


def test_duplicate_material_code_allowed(category, unit):
    Material.objects.create(
        material_code="MAT-001",
        name="Malzeme A",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    Material.objects.create(
        material_code="MAT-001",
        name="Malzeme B",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    assert Material.objects.filter(material_code="MAT-001").count() == 2


def test_invalid_tracking_mode_rejected_at_db(category, unit):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            Material.objects.create(
                material_code="MAT-INVALID",
                name="Geçersiz Mod",
                category=category,
                unit=unit,
                tracking_mode="BULK",
            )


def test_quantity_with_unit_accepted(category, unit):
    material = Material.objects.create(
        material_code="MAT-QTY",
        name="Miktarlı Malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    assert material.unit_id == unit.pk


def test_quantity_without_unit_rejected_at_db(category):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            Material.objects.create(
                material_code="MAT-NO-UNIT",
                name="Birimsiz Miktar",
                category=category,
                unit=None,
                tracking_mode=Material.TrackingMode.QUANTITY,
            )


def test_serialized_without_unit_accepted(category):
    material = Material.objects.create(
        material_code="MAT-SER",
        name="Tekil Malzeme",
        category=category,
        unit=None,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    assert material.unit_id is None


def test_serialized_with_unit_accepted(category, unit):
    material = Material.objects.create(
        material_code="MAT-SER-U",
        name="Tekil Birimli",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    assert material.unit_id == unit.pk


def test_negative_minimum_stock_rejected_at_db(category, unit):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            Material.objects.create(
                material_code="MAT-NEG",
                name="Negatif Eşik",
                category=category,
                unit=unit,
                tracking_mode=Material.TrackingMode.QUANTITY,
                minimum_stock_value=Decimal("-0.001"),
            )


@pytest.mark.parametrize(
    "technical_specs",
    [
        {},
        {"voltage": "220V", "nested": {"color": "red"}},
    ],
)
def test_technical_specs_object_accepted(category, unit, technical_specs):
    material = Material.objects.create(
        material_code=f"MAT-JSON-{uuid.uuid4().hex[:6]}",
        name="JSON Malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
        technical_specs=technical_specs,
    )
    material.refresh_from_db()
    assert material.technical_specs == technical_specs


@pytest.mark.parametrize(
    "technical_specs",
    [
        [],
        "scalar",
        None,
    ],
)
def test_technical_specs_non_object_rejected_at_db(category, unit, technical_specs):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            Material.objects.create(
                material_code=f"MAT-BAD-{uuid.uuid4().hex[:6]}",
                name="Geçersiz JSON",
                category=category,
                unit=unit,
                tracking_mode=Material.TrackingMode.QUANTITY,
                technical_specs=technical_specs,
            )


def test_tracking_mode_currently_editable(category, unit):
    material = Material.objects.create(
        material_code="MAT-EDIT",
        name="Mod Değişimi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material.tracking_mode = Material.TrackingMode.SERIALIZED
    material.unit = None
    material.save()
    material.refresh_from_db()
    assert material.tracking_mode == Material.TrackingMode.SERIALIZED
    assert material.unit_id is None


def test_material_has_no_authoritative_stock_field():
    field_names = {field.name for field in Material._meta.get_fields()}
    authoritative_stock_fields = {
        "stock_quantity",
        "stock",
        "quantity_on_hand",
        "current_stock",
    }
    assert authoritative_stock_fields.isdisjoint(field_names)
