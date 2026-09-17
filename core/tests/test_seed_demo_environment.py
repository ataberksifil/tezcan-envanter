from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import InventoryTransaction, StockBalance
from locations.models import Location

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def ensure_catalog_reference_data(db):
    if not UnitOfMeasure.objects.filter(code="ADET").exists():
        UnitOfMeasure.objects.create(code="ADET", name="Adet")
    if not Category.objects.filter(name="Otomasyon").exists():
        Category.objects.create(name="Otomasyon")
    if not MaterialCondition.objects.filter(code="NEW_GOOD").exists():
        MaterialCondition.objects.create(
            code="NEW_GOOD",
            name="Yeni / Sağlam",
            sort_order=10,
        )


def test_seed_demo_environment_is_idempotent():
    call_command("setup_roles", verbosity=0)
    call_command("seed_demo_environment", verbosity=0)
    tx_count_after_first = InventoryTransaction.objects.count()

    call_command("seed_demo_environment", verbosity=0)
    assert InventoryTransaction.objects.count() == tx_count_after_first

    depot_a = Location.objects.get(code="G1")
    depot_b = Location.objects.get(code="G2")
    material = Material.objects.get(material_code="DEMO-KLEMENS")

    balance_a = StockBalance.objects.get(
        material=material, location=depot_a, condition__code="NEW_GOOD"
    )
    balance_b = StockBalance.objects.get(
        material=material, location=depot_b, condition__code="NEW_GOOD"
    )
    assert balance_a.quantity == Decimal("80.000")
    assert balance_b.quantity == Decimal("10.000")

    assert User.objects.filter(username="demo.yonetici").exists()
    assert User.objects.filter(username="demo.depocu").exists()
    assert User.objects.filter(username="demo.teknisyen").exists()
