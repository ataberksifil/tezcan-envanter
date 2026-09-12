from __future__ import annotations

import importlib
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from accounts.models import Employee
from accounts.roles import SAFE_CATALOG_PERMISSION_LABELS
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
)
from locations.models import Location


pytestmark = pytest.mark.django_db

TRANSFER_KERNEL_MIGRATION = "0007_quantity_transfer_kernel"
RETURN_KERNEL_MIGRATION = "0006_quantity_return_kernel"


@pytest.fixture
def transfer_objects():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"TR-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"Transfer kategori {suffix}")
    material = Material.objects.create(
        material_code=f"TR-M-{suffix}",
        name="Transfer malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"TR-C-{suffix}", name="Transfer kondisyonu", sort_order=910
    )
    source = Location.objects.create(
        code=f"TR-S-{suffix}", name="Kaynak rafı", can_hold_stock=True
    )
    target = Location.objects.create(
        code=f"TR-T-{suffix}", name="Hedef rafı", can_hold_stock=True
    )
    user = get_user_model().objects.create_user(username=f"transfer-{suffix}")
    employee = Employee.objects.create(
        employee_number=f"TR-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
    )
    production_line = ProductionLine.objects.create(
        code=f"TR-PL-{suffix}", name="Transfer üretim hattı"
    )
    return {
        "unit": unit,
        "category": category,
        "material": material,
        "condition": condition,
        "source": source,
        "target": target,
        "user": user,
        "employee": employee,
        "production_line": production_line,
    }


def _header(objects, transaction_type):
    return InventoryTransaction.objects.create(
        operation_id=uuid.uuid4(),
        request_fingerprint=uuid.uuid4().hex * 2,
        transaction_type=transaction_type,
        acting_user=objects["user"],
        occurred_at=timezone.now(),
    )


def _context(objects, header):
    return IssueContext.objects.create(
        transaction=header,
        receiver_employee=objects["employee"],
        receiver_first_name_snapshot=objects["employee"].first_name,
        receiver_last_name_snapshot=objects["employee"].last_name,
        receiver_employee_number_snapshot=objects["employee"].employee_number,
        production_line=objects["production_line"],
        production_line_code_snapshot=objects["production_line"].code,
        production_line_name_snapshot=objects["production_line"].name,
        usage_location_text="Pano 7",
    )


def _force_constraints():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SET CONSTRAINTS
                inventory_tx_requires_line_trg,
                inventory_issue_line_cardinality_trg,
                inventory_issue_requires_context_trg
            IMMEDIATE
            """
        )
        cursor.execute(
            """
            SET CONSTRAINTS
                inventory_tx_requires_line_trg,
                inventory_issue_line_cardinality_trg,
                inventory_issue_requires_context_trg
            DEFERRED
            """
        )


def _issue(objects, quantity=Decimal("10.000")):
    with transaction.atomic():
        header = _header(objects, InventoryTransaction.TransactionType.ISSUE)
        line = InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=1,
            material=objects["material"],
            quantity=quantity,
            unit=objects["unit"],
            condition=objects["condition"],
            source_location=objects["source"],
            target_location=None,
        )
        context = _context(objects, header)
        _force_constraints()
    return header, line, context


def _receipt(objects, quantity=Decimal("10.000")):
    with transaction.atomic():
        header = _header(objects, InventoryTransaction.TransactionType.RECEIPT)
        line = InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=1,
            material=objects["material"],
            quantity=quantity,
            unit=objects["unit"],
            condition=objects["condition"],
            source_location=None,
            target_location=objects["target"],
        )
        _force_constraints()
    return header, line


def _return(objects, original_issue_line, quantity=Decimal("1.000"), **overrides):
    values = {
        "material": objects["material"],
        "unit": objects["unit"],
        "condition": objects["condition"],
        "source_location": None,
        "target_location": objects["target"],
        "original_issue_line": original_issue_line,
    }
    values.update(overrides)
    with transaction.atomic():
        header = _header(objects, InventoryTransaction.TransactionType.RETURN)
        line = InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=1,
            quantity=quantity,
            **values,
        )
        _force_constraints()
    return header, line


def _transfer(objects, quantity=Decimal("1.000"), **overrides):
    values = {
        "material": objects["material"],
        "unit": objects["unit"],
        "condition": objects["condition"],
        "source_location": objects["source"],
        "target_location": objects["target"],
        "original_issue_line": None,
    }
    values.update(overrides)
    with transaction.atomic():
        header = _header(objects, InventoryTransaction.TransactionType.TRANSFER)
        line = InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=1,
            quantity=quantity,
            **values,
        )
        _force_constraints()
    return header, line


def test_transfer_transaction_type_and_valid_line_are_accepted(transfer_objects):
    header, line = _transfer(transfer_objects)

    assert header.transaction_type == InventoryTransaction.TransactionType.TRANSFER
    assert line.source_location_id == transfer_objects["source"].pk
    assert line.target_location_id == transfer_objects["target"].pk
    assert line.original_issue_line_id is None
    assert header.lines.count() == 1


def test_unsupported_transaction_type_is_rejected(transfer_objects):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _header(transfer_objects, "CONTROLLED_CORRECTION")


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_location": None},
        {"target_location": None},
        {"original_issue_line": "issue"},
    ],
)
def test_invalid_transfer_shapes_are_rejected(transfer_objects, overrides):
    _issue_header, issue_line, _context_row = _issue(transfer_objects)
    normalized = dict(overrides)
    if normalized.get("original_issue_line") == "issue":
        normalized["original_issue_line"] = issue_line
    with pytest.raises(IntegrityError):
        _transfer(transfer_objects, **normalized)


def test_transfer_source_equal_target_is_rejected(transfer_objects):
    with pytest.raises(IntegrityError):
        _transfer(
            transfer_objects,
            source_location=transfer_objects["source"],
            target_location=transfer_objects["source"],
        )


def test_transfer_requires_exactly_one_line(transfer_objects):
    with pytest.raises(
        IntegrityError, match="TRANSFER transaction must contain exactly one line"
    ):
        with transaction.atomic():
            _header(transfer_objects, InventoryTransaction.TransactionType.TRANSFER)
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS inventory_tx_requires_line_trg IMMEDIATE")

    one_header, _one_line = _transfer(transfer_objects)
    assert one_header.lines.count() == 1

    with pytest.raises(
        IntegrityError, match="TRANSFER transaction must contain exactly one line"
    ):
        with transaction.atomic():
            header = _header(
                transfer_objects, InventoryTransaction.TransactionType.TRANSFER
            )
            for number in (1, 2):
                InventoryTransactionLine.objects.create(
                    transaction=header,
                    line_number=number,
                    material=transfer_objects["material"],
                    quantity=Decimal("1"),
                    unit=transfer_objects["unit"],
                    condition=transfer_objects["condition"],
                    source_location=transfer_objects["source"],
                    target_location=transfer_objects["target"],
                )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS inventory_issue_line_cardinality_trg IMMEDIATE"
                )


def test_transfer_cannot_own_issue_context(transfer_objects):
    header, _line = _transfer(transfer_objects)
    with pytest.raises(IntegrityError, match="requires an ISSUE transaction"):
        with transaction.atomic():
            _context(transfer_objects, header)


def test_return_lineage_and_cumulative_cap_still_work(transfer_objects):
    _issue_header, issue_line, _context_row = _issue(transfer_objects)
    for quantity in (Decimal("3"), Decimal("2"), Decimal("5")):
        _return(transfer_objects, issue_line, quantity)

    with pytest.raises(IntegrityError, match="cannot exceed original ISSUE quantity"):
        _return(transfer_objects, issue_line, Decimal("1"))

    assert issue_line.return_lines.count() == 3
    assert sum(issue_line.return_lines.values_list("quantity", flat=True)) == Decimal(
        "10"
    )


def test_transfer_history_blocks_tracking_mode_change(transfer_objects):
    _transfer(transfer_objects)

    with pytest.raises(IntegrityError, match="cannot change after inventory history"):
        with transaction.atomic():
            Material.objects.filter(pk=transfer_objects["material"].pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )


def test_transfer_ledger_is_immutable_through_orm_and_raw_sql(transfer_objects):
    header, line = _transfer(transfer_objects)

    header.transaction_type = InventoryTransaction.TransactionType.RECEIPT
    with pytest.raises(ValidationError):
        header.save()
    with pytest.raises(ValidationError):
        header.delete()
    line.quantity = Decimal("2")
    with pytest.raises(ValidationError):
        line.save()
    with pytest.raises(ValidationError):
        line.delete()

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            InventoryTransaction.objects.filter(pk=header.pk).update(
                transaction_type=InventoryTransaction.TransactionType.RECEIPT
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            InventoryTransactionLine.objects.filter(pk=line.pk).update(
                quantity=Decimal("2")
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            InventoryTransactionLine.objects.filter(pk=line.pk).delete()
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_inventorytransaction SET transaction_type = %s WHERE id = %s",
                [InventoryTransaction.TransactionType.RECEIPT, header.pk],
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM inventory_inventorytransaction WHERE id = %s",
                [header.pk],
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_inventorytransactionline SET quantity = %s WHERE id = %s",
                [Decimal("2"), line.pk],
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM inventory_inventorytransactionline WHERE id = %s",
                [line.pk],
            )


def test_transfer_permission_is_defined_and_rolled_out():
    permissions = dict(InventoryTransaction._meta.permissions)
    assert "transfer_stock" in permissions
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 23
    assert "inventory.return_stock" in SAFE_CATALOG_PERMISSION_LABELS
    assert "inventory.transfer_stock" in SAFE_CATALOG_PERMISSION_LABELS


def test_receipt_and_issue_kernel_shapes_remain_valid(transfer_objects):
    receipt_header, receipt_line = _receipt(transfer_objects)
    issue_header, issue_line, issue_context = _issue(transfer_objects)

    assert receipt_header.transaction_type == InventoryTransaction.TransactionType.RECEIPT
    assert receipt_line.source_location_id is None
    assert receipt_line.target_location_id == transfer_objects["target"].pk
    assert issue_header.transaction_type == InventoryTransaction.TransactionType.ISSUE
    assert issue_line.source_location_id == transfer_objects["source"].pk
    assert issue_line.target_location_id is None
    assert issue_header.issue_context == issue_context


def test_transfer_reverse_fails_closed_when_transfer_data_exists(transfer_objects):
    _transfer(transfer_objects)
    migration = importlib.import_module("inventory.migrations.0007_quantity_transfer_kernel")
    reverse_precheck = migration.TRANSFER_GUARD_REVERSE_SQL.split(
        "CREATE OR REPLACE FUNCTION", 1
    )[0]
    with pytest.raises(IntegrityError, match="cannot reverse quantity TRANSFER kernel"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(reverse_precheck)


def test_transfer_kernel_migration_reverses_without_transfer_data_and_forwards_again(
    transfer_objects,
):
    recorder = MigrationRecorder(connection)
    assert recorder.migration_qs.filter(
        app="inventory", name=TRANSFER_KERNEL_MIGRATION
    ).exists()
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()

    call_command("migrate", "inventory", RETURN_KERNEL_MIGRATION, verbosity=0)
    try:
        assert not recorder.migration_qs.filter(
            app="inventory", name=TRANSFER_KERNEL_MIGRATION
        ).exists()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _header(transfer_objects, "TRANSFER")
        call_command("migrate", "inventory", TRANSFER_KERNEL_MIGRATION, verbosity=0)
    finally:
        call_command("migrate", "inventory", TRANSFER_KERNEL_MIGRATION, verbosity=0)

    assert recorder.migration_qs.filter(
        app="inventory", name=TRANSFER_KERNEL_MIGRATION
    ).exists()
    header, line = _transfer(transfer_objects)
    assert header.transaction_type == InventoryTransaction.TransactionType.TRANSFER
    assert line.source_location_id == transfer_objects["source"].pk
    assert line.target_location_id == transfer_objects["target"].pk
