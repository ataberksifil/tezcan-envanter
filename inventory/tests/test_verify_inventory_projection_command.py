from __future__ import annotations

import uuid
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import InventoryTransaction, StockBalance
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import receive_quantity
from locations.models import Location

pytestmark = pytest.mark.django_db


def _inventory_tables_exist() -> bool:
    tables = connection.introspection.table_names()
    return "inventory_stockbalance" in tables


@pytest.fixture(autouse=True)
def require_inventory_schema():
    if not _inventory_tables_exist():
        pytest.skip(
            "inventory migration not applied to test DB; "
            "projection command tests deferred until test DB is migrated"
        )


@pytest.fixture
def receipt_objects(django_user_model):
    from django.contrib.auth.models import Permission

    suffix = uuid.uuid4().hex[:8]
    actor = django_user_model.objects.create_user(
        username=f"proj-cmd-{suffix}",
        password="synthetic-test-password-only",
    )
    actor.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory",
            codename="receive_stock",
        )
    )
    unit = UnitOfMeasure.objects.create(code=f"PC-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"PC kat {suffix}")
    material = Material.objects.create(
        material_code=f"PC-M-{suffix}",
        name="Projeksiyon malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"PC-C-{suffix}",
        name="Yeni",
        sort_order=1,
    )
    location = Location.objects.create(
        code=f"PC-L-{suffix}",
        name="Depo",
        active=True,
        can_hold_stock=True,
    )
    return {
        "actor": actor,
        "material": material,
        "condition": condition,
        "location": location,
        "unit": unit,
    }


def test_healthy_projection_exits_successfully(receipt_objects):
    receive_quantity(
        actor=receipt_objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=receipt_objects["material"].pk,
        unit_id=receipt_objects["unit"].pk,
        condition_id=receipt_objects["condition"].pk,
        target_location_id=receipt_objects["location"].pk,
        quantity=Decimal("2.500"),
    )
    stdout = StringIO()
    call_command("verify_inventory_projection", stdout=stdout)
    assert "consistent" in stdout.getvalue().lower()


def test_drift_produces_non_zero_status(receipt_objects):
    receive_quantity(
        actor=receipt_objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=receipt_objects["material"].pk,
        unit_id=receipt_objects["unit"].pk,
        condition_id=receipt_objects["condition"].pk,
        target_location_id=receipt_objects["location"].pk,
        quantity=Decimal("2.500"),
    )
    StockBalance.objects.update(quantity=Decimal("1.000"))
    stderr = StringIO()
    with pytest.raises(CommandError, match="verification failed"):
        call_command("verify_inventory_projection", stderr=stderr)
    output = stderr.getvalue()
    assert receipt_objects["material"].material_code in output
    assert receipt_objects["location"].code in output
    assert receipt_objects["condition"].name in output
    assert "expected=2.500" in output
    assert "actual=1.000" in output


def test_command_does_not_mutate_stock_balance(receipt_objects):
    receive_quantity(
        actor=receipt_objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=receipt_objects["material"].pk,
        unit_id=receipt_objects["unit"].pk,
        condition_id=receipt_objects["condition"].pk,
        target_location_id=receipt_objects["location"].pk,
        quantity=Decimal("2.500"),
    )
    balance = StockBalance.objects.get()
    before_count = StockBalance.objects.count()
    before_transactions = InventoryTransaction.objects.count()

    StockBalance.objects.filter(pk=balance.pk).update(quantity=Decimal("1.000"))
    corrupted_updated_at = StockBalance.objects.get(pk=balance.pk).updated_at
    with pytest.raises(CommandError):
        call_command("verify_inventory_projection")

    assert StockBalance.objects.count() == before_count
    assert InventoryTransaction.objects.count() == before_transactions
    refreshed = StockBalance.objects.get(pk=balance.pk)
    assert refreshed.quantity == Decimal("1.000")
    assert refreshed.updated_at == corrupted_updated_at


def test_command_uses_existing_verifier(receipt_objects):
    with patch(
        "inventory.management.commands.verify_inventory_projection.verify_quantity_projection",
        wraps=verify_quantity_projection,
    ) as verifier:
        call_command("verify_inventory_projection")
    verifier.assert_called_once_with(using="default")
