"""Option labels in inventory forms: readable, with ids only where needed."""

from __future__ import annotations

import uuid

import pytest

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.forms import QuantityReceiptForm
from locations.models import Location

pytestmark = pytest.mark.django_db


def _labels(field):
    return {str(value): label for value, label in field.field.choices if value}


def test_receipt_option_labels_omit_uuid_for_unique_codes():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"CL-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"CL kategori {suffix}")
    material = Material.objects.create(
        material_code=f"CL-M-{suffix}",
        name="Etiket malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    location = Location.objects.create(code=f"CL-L-{suffix}", name="Raf", can_hold_stock=True)

    form = QuantityReceiptForm()

    material_label = _labels(form["material"])[str(material.pk)]
    location_label = _labels(form["target_location"])[str(location.pk)]
    assert material.material_code in material_label
    assert str(material.pk) not in material_label
    assert "#" not in material_label
    assert f"({location.code})" in location_label
    assert str(location.pk) not in location_label


def test_receipt_option_labels_disambiguate_repeated_legacy_codes():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"CD-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"CD kategori {suffix}")
    first, second = (
        Material.objects.create(
            material_code=f"LEGACY-{suffix}",
            name="Aynı kodlu malzeme",
            category=category,
            unit=unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        for _ in range(2)
    )
    first_condition, second_condition = (
        MaterialCondition.objects.create(code=f"CD-{n}-{suffix}", name=f"Aynı ad {suffix}", sort_order=300 + n)
        for n in range(2)
    )

    form = QuantityReceiptForm()

    material_labels = _labels(form["material"])
    assert material_labels[str(first.pk)] != material_labels[str(second.pk)]
    assert material_labels[str(first.pk)].endswith(f"#{str(first.pk)[:8]}")
    condition_labels = _labels(form["condition"])
    assert condition_labels[str(first_condition.pk)] != condition_labels[str(second_condition.pk)]
