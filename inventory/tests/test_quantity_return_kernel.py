from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
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


@pytest.fixture
def return_objects():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"RET-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"İade kategori {suffix}")
    material = Material.objects.create(
        material_code=f"RET-M-{suffix}",
        name="İade malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"RET-C-{suffix}", name="İade kondisyonu", sort_order=900
    )
    source = Location.objects.create(
        code=f"RET-S-{suffix}", name="Çıkış rafı", can_hold_stock=True
    )
    target = Location.objects.create(
        code=f"RET-T-{suffix}", name="İade rafı", can_hold_stock=True
    )
    user = get_user_model().objects.create_user(username=f"return-{suffix}")
    employee = Employee.objects.create(
        employee_number=f"RET-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
    )
    production_line = ProductionLine.objects.create(
        code=f"RET-PL-{suffix}", name="İade üretim hattı"
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


def test_return_transaction_type_and_valid_line_are_accepted(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    return_header, return_line = _return(return_objects, issue_line)

    assert return_header.transaction_type == InventoryTransaction.TransactionType.RETURN
    assert return_line.source_location_id is None
    assert return_line.target_location_id == return_objects["target"].pk
    assert return_line.original_issue_line_id == issue_line.pk
    assert issue_line.return_lines.get() == return_line


def test_unsupported_transaction_type_remains_rejected(return_objects):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _header(return_objects, "UNSUPPORTED")


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_location": "source"},
        {"target_location": None},
        {"original_issue_line": None},
    ],
)
def test_invalid_return_shapes_are_rejected(return_objects, overrides):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    normalized = {
        key: return_objects[value] if isinstance(value, str) else value
        for key, value in overrides.items()
    }
    original_issue_line = normalized.pop("original_issue_line", issue_line)
    with pytest.raises(IntegrityError):
        _return(return_objects, original_issue_line, **normalized)


def test_receipt_with_original_issue_line_is_rejected(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    with pytest.raises(IntegrityError, match="RECEIPT line"):
        with transaction.atomic():
            header = _header(return_objects, InventoryTransaction.TransactionType.RECEIPT)
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=return_objects["material"],
                quantity=Decimal("1"),
                unit=return_objects["unit"],
                condition=return_objects["condition"],
                target_location=return_objects["target"],
                original_issue_line=issue_line,
            )


def test_issue_with_original_issue_line_is_rejected(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    with pytest.raises(IntegrityError, match="ISSUE line"):
        with transaction.atomic():
            header = _header(return_objects, InventoryTransaction.TransactionType.ISSUE)
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=return_objects["material"],
                quantity=Decimal("1"),
                unit=return_objects["unit"],
                condition=return_objects["condition"],
                source_location=return_objects["source"],
                original_issue_line=issue_line,
            )


def test_return_lineage_rejects_receipt_and_return_lines(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    _receipt_header, receipt_line = _receipt(return_objects)
    with pytest.raises(IntegrityError, match="must belong to an ISSUE"):
        _return(return_objects, receipt_line)

    _return_header, first_return_line = _return(return_objects, issue_line)
    with pytest.raises(IntegrityError, match="must belong to an ISSUE"):
        _return(return_objects, first_return_line)


def _raw_return_insert(objects, original_issue_line, **overrides):
    values = {
        "material_id": objects["material"].pk,
        "quantity": Decimal("1.000"),
        "unit_id": objects["unit"].pk,
        "condition_id": objects["condition"].pk,
        "source_location_id": None,
        "target_location_id": objects["target"].pk,
        "original_issue_line_id": original_issue_line.pk,
    }
    values.update(overrides)
    header = _header(objects, InventoryTransaction.TransactionType.RETURN)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO inventory_inventorytransactionline (
                id, transaction_id, line_number, material_id, quantity,
                unit_id, condition_id, source_location_id, target_location_id,
                original_issue_line_id, created_at
            ) VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                uuid.uuid4(),
                header.pk,
                values["material_id"],
                values["quantity"],
                values["unit_id"],
                values["condition_id"],
                values["source_location_id"],
                values["target_location_id"],
                values["original_issue_line_id"],
                timezone.now(),
            ],
        )


def test_raw_sql_return_material_mismatch_is_rejected(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    other_material = Material.objects.create(
        material_code=f"OTHER-{uuid.uuid4().hex[:8]}",
        name="Başka malzeme",
        category=return_objects["category"],
        unit=return_objects["unit"],
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    with pytest.raises(IntegrityError, match="material must match"):
        with transaction.atomic():
            _raw_return_insert(
                return_objects, issue_line, material_id=other_material.pk
            )


def test_raw_sql_return_unit_mismatch_is_rejected(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    other_unit = UnitOfMeasure.objects.create(
        code=f"OTHER-{uuid.uuid4().hex[:8]}", name="Başka birim"
    )
    Material.objects.filter(pk=return_objects["material"].pk).update(unit=other_unit)
    with pytest.raises(IntegrityError, match="unit must match"):
        with transaction.atomic():
            _raw_return_insert(return_objects, issue_line, unit_id=other_unit.pk)


def test_raw_sql_return_condition_mismatch_is_rejected(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    other_condition = MaterialCondition.objects.create(
        code=f"OTHER-{uuid.uuid4().hex[:8]}", name="Başka kondisyon", sort_order=901
    )
    with pytest.raises(IntegrityError, match="condition must match"):
        with transaction.atomic():
            _raw_return_insert(
                return_objects, issue_line, condition_id=other_condition.pk
            )


def test_return_requires_exactly_one_line(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    with pytest.raises(IntegrityError, match="RETURN transaction must contain exactly one line"):
        with transaction.atomic():
            _header(return_objects, InventoryTransaction.TransactionType.RETURN)
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS inventory_tx_requires_line_trg IMMEDIATE")

    one_header, _one_line = _return(return_objects, issue_line)
    assert one_header.lines.count() == 1

    with pytest.raises(IntegrityError, match="RETURN transaction must contain exactly one line"):
        with transaction.atomic():
            header = _header(return_objects, InventoryTransaction.TransactionType.RETURN)
            for number in (1, 2):
                InventoryTransactionLine.objects.create(
                    transaction=header,
                    line_number=number,
                    material=return_objects["material"],
                    quantity=Decimal("1"),
                    unit=return_objects["unit"],
                    condition=return_objects["condition"],
                    target_location=return_objects["target"],
                    original_issue_line=issue_line,
                )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS inventory_issue_line_cardinality_trg IMMEDIATE"
                )


def test_sequential_cumulative_return_cap(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    for quantity in (Decimal("3"), Decimal("2"), Decimal("5")):
        _return(return_objects, issue_line, quantity)

    with pytest.raises(IntegrityError, match="cannot exceed original ISSUE quantity"):
        _return(return_objects, issue_line, Decimal("1"))

    assert issue_line.return_lines.count() == 3
    assert sum(issue_line.return_lines.values_list("quantity", flat=True)) == Decimal("10")


def test_raw_sql_cumulative_return_cap_cannot_be_bypassed(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    _return(return_objects, issue_line, Decimal("9"))

    with pytest.raises(IntegrityError, match="cannot exceed original ISSUE quantity"):
        with transaction.atomic():
            _raw_return_insert(return_objects, issue_line, quantity=Decimal("2"))


def test_return_history_blocks_tracking_mode_change(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    _return(return_objects, issue_line)

    with pytest.raises(IntegrityError, match="cannot change after inventory history"):
        with transaction.atomic():
            Material.objects.filter(pk=return_objects["material"].pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )


def test_return_ledger_is_immutable_through_orm_and_raw_sql(return_objects):
    _issue_header, issue_line, _context_row = _issue(return_objects)
    _return_header, return_line = _return(return_objects, issue_line)

    return_line.quantity = Decimal("2")
    with pytest.raises(ValidationError):
        return_line.save()
    with pytest.raises(ValidationError):
        return_line.delete()

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            InventoryTransactionLine.objects.filter(pk=return_line.pk).update(
                quantity=Decimal("2")
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            InventoryTransactionLine.objects.filter(pk=return_line.pk).delete()
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_inventorytransactionline SET quantity = %s WHERE id = %s",
                [Decimal("2"), return_line.pk],
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM inventory_inventorytransactionline WHERE id = %s",
                [return_line.pk],
            )


def test_return_cannot_own_issue_context_and_issue_behavior_is_unchanged(return_objects):
    issue_header, issue_line, issue_context = _issue(return_objects)
    return_header, _return_line = _return(return_objects, issue_line)
    assert issue_header.issue_context == issue_context

    with pytest.raises(IntegrityError, match="requires an ISSUE transaction"):
        with transaction.atomic():
            _context(return_objects, return_header)


def test_return_permission_definition_is_in_managed_rollout():
    permissions = dict(InventoryTransaction._meta.permissions)
    assert "return_stock" in permissions
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 28
    assert "inventory.return_stock" in SAFE_CATALOG_PERMISSION_LABELS
