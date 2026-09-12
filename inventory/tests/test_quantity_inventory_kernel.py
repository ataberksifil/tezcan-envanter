from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import RestrictedError
from django.utils import timezone

from accounts.models import Employee
from accounts.roles import SAFE_CATALOG_PERMISSION_LABELS
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory import forms as inventory_forms
from inventory import services as inventory_services
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    StockBalance,
)
from inventory.urls import urlpatterns as inventory_urlpatterns
from locations.models import Location


pytestmark = pytest.mark.django_db


@pytest.fixture
def kernel_objects():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(
        code=f"K-{suffix}",
        name="Kernel birimi",
    )
    category = Category.objects.create(name=f"Kernel kategori {suffix}")
    material = Material.objects.create(
        material_code=f"MAT-{suffix}",
        name="Kernel malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"COND-{suffix}",
        name="Kernel kondisyonu",
        sort_order=100,
    )
    location = Location.objects.create(
        code=f"LOC-{suffix}",
        name="Kernel lokasyonu",
        active=True,
        can_hold_stock=True,
    )
    other_location = Location.objects.create(
        code=f"OTHER-LOC-{suffix}",
        name="Diğer kernel lokasyonu",
        active=True,
        can_hold_stock=True,
    )
    user = get_user_model().objects.create_user(username=f"kernel-{suffix}")
    employee = Employee.objects.create(
        employee_number=f"EMP-{suffix}",
        first_name="Ayşe",
        last_name="Yılmaz",
    )
    production_line = ProductionLine.objects.create(
        code=f"PL-{suffix}",
        name="Kernel üretim hattı",
    )
    return {
        "unit": unit,
        "category": category,
        "material": material,
        "condition": condition,
        "location": location,
        "other_location": other_location,
        "user": user,
        "employee": employee,
        "production_line": production_line,
    }


_DEFAULT_LOCATION = object()


def create_ledger(
    objects,
    *,
    operation_id=None,
    fingerprint="a" * 64,
    transaction_type=InventoryTransaction.TransactionType.RECEIPT,
    material=None,
    unit=None,
    condition=None,
    source_location=None,
    target_location=_DEFAULT_LOCATION,
    line_number=1,
    quantity=Decimal("1.000"),
):
    with transaction.atomic():
        header = InventoryTransaction.objects.create(
            operation_id=operation_id or uuid.uuid4(),
            request_fingerprint=fingerprint,
            transaction_type=transaction_type,
            acting_user=objects["user"],
            occurred_at=timezone.now(),
        )
        line = InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=line_number,
            material=material or objects["material"],
            quantity=quantity,
            unit=unit or objects["unit"],
            condition=condition or objects["condition"],
            source_location=source_location,
            target_location=(
                objects["location"]
                if target_location is _DEFAULT_LOCATION
                else target_location
            ),
        )
        force_inventory_completeness_constraints()
    return header, line


def create_issue_context(objects, header, **overrides):
    values = {
        "transaction": header,
        "receiver_employee": objects["employee"],
        "receiver_first_name_snapshot": objects["employee"].first_name,
        "receiver_last_name_snapshot": objects["employee"].last_name,
        "receiver_employee_number_snapshot": objects["employee"].employee_number,
        "production_line": objects["production_line"],
        "production_line_code_snapshot": objects["production_line"].code,
        "production_line_name_snapshot": objects["production_line"].name,
        "usage_location_text": "Pano 7",
    }
    values.update(overrides)
    return IssueContext.objects.create(**values)


def force_inventory_completeness_constraints():
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


def create_issue_ledger(objects, *, operation_id=None, context_overrides=None):
    with transaction.atomic():
        header = create_header(
            objects,
            operation_id=operation_id or uuid.uuid4(),
            transaction_type=InventoryTransaction.TransactionType.ISSUE,
        )
        line = InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=1,
            material=objects["material"],
            quantity=Decimal("1.000"),
            unit=objects["unit"],
            condition=objects["condition"],
            source_location=objects["location"],
            target_location=None,
        )
        issue_context = create_issue_context(
            objects,
            header,
            **(context_overrides or {}),
        )
        force_inventory_completeness_constraints()
    return header, line, issue_context


def create_header(objects, **overrides):
    values = {
        "operation_id": uuid.uuid4(),
        "request_fingerprint": "b" * 64,
        "transaction_type": InventoryTransaction.TransactionType.RECEIPT,
        "acting_user": objects["user"],
        "occurred_at": timezone.now(),
    }
    values.update(overrides)
    return InventoryTransaction.objects.create(**values)


def test_valid_ledger_insert_and_uuid_defaults(kernel_objects):
    header, line = create_ledger(kernel_objects)

    assert isinstance(header.pk, uuid.UUID)
    assert isinstance(line.pk, uuid.UUID)
    assert header.lines.get() == line
    assert header.created_at is not None
    assert line.created_at is not None


def test_header_without_line_is_rejected_at_commit(kernel_objects):
    with pytest.raises(IntegrityError, match="must contain at least one line"):
        with transaction.atomic():
            create_header(kernel_objects)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS inventory_tx_requires_line_trg IMMEDIATE"
                )


def test_operation_id_is_globally_unique(kernel_objects):
    operation_id = uuid.uuid4()
    create_ledger(kernel_objects, operation_id=operation_id)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            create_header(kernel_objects, operation_id=operation_id)


@pytest.mark.parametrize(
    "fingerprint",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "0" * 63 + "-"],
)
def test_fingerprint_requires_64_lowercase_hex_characters(
    kernel_objects, fingerprint
):
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            create_header(kernel_objects, request_fingerprint=fingerprint)


def test_receipt_and_issue_transaction_types_are_allowed(kernel_objects):
    receipt, _receipt_line = create_ledger(kernel_objects)
    issue, _issue_line, _issue_context = create_issue_ledger(kernel_objects)

    assert receipt.transaction_type == InventoryTransaction.TransactionType.RECEIPT
    assert issue.transaction_type == InventoryTransaction.TransactionType.ISSUE


def test_unsupported_transaction_type_is_rejected_by_database(kernel_objects):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            create_header(kernel_objects, transaction_type="TRANSFER")


def test_operation_id_is_globally_unique_across_receipt_and_issue(kernel_objects):
    operation_id = uuid.uuid4()
    create_ledger(kernel_objects, operation_id=operation_id)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            create_header(
                kernel_objects,
                operation_id=operation_id,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )


def test_acting_user_is_restricted(kernel_objects):
    create_ledger(kernel_objects)

    with pytest.raises(RestrictedError):
        kernel_objects["user"].delete()


def test_line_number_is_unique_within_transaction(kernel_objects):
    header, _line = create_ledger(kernel_objects)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["material"],
                quantity=Decimal("2"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                target_location=kernel_objects["location"],
            )


@pytest.mark.parametrize("line_number", [0, -1])
def test_line_number_must_be_positive(kernel_objects, line_number):
    with pytest.raises(IntegrityError):
        create_ledger(kernel_objects, line_number=line_number)


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-0.001")])
def test_line_quantity_must_be_positive(kernel_objects, quantity):
    with pytest.raises(IntegrityError):
        create_ledger(kernel_objects, quantity=quantity)


def test_receipt_line_source_must_be_null(kernel_objects):
    with pytest.raises(IntegrityError, match="RECEIPT line source location must be null"):
        create_ledger(
            kernel_objects,
            source_location=kernel_objects["other_location"],
        )


def test_receipt_line_target_is_required(kernel_objects):
    with pytest.raises(IntegrityError, match="RECEIPT line target location is required"):
        with transaction.atomic():
            header = create_header(kernel_objects)
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["material"],
                quantity=Decimal("1"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                target_location=None,
            )


def test_issue_line_source_set_and_target_null_is_allowed(kernel_objects):
    _header, line, _context = create_issue_ledger(kernel_objects)

    assert line.source_location == kernel_objects["location"]
    assert line.target_location is None


def test_issue_line_source_is_required(kernel_objects):
    with pytest.raises(IntegrityError, match="ISSUE line source location is required"):
        with transaction.atomic():
            header = create_header(
                kernel_objects,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["material"],
                quantity=Decimal("1"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                source_location=None,
                target_location=None,
            )


def test_issue_line_target_must_be_null(kernel_objects):
    with pytest.raises(IntegrityError, match="ISSUE line target location must be null"):
        with transaction.atomic():
            header = create_header(
                kernel_objects,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["material"],
                quantity=Decimal("1"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                source_location=kernel_objects["location"],
                target_location=kernel_objects["other_location"],
            )


def test_generic_line_shape_rejects_same_source_and_target(kernel_objects):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            header = create_header(
                kernel_objects,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["material"],
                quantity=Decimal("1"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                source_location=kernel_objects["location"],
                target_location=kernel_objects["location"],
            )


@pytest.mark.parametrize("reference", ["material", "unit", "condition", "location"])
def test_line_master_references_are_restricted(kernel_objects, reference):
    create_ledger(kernel_objects)

    with pytest.raises(RestrictedError):
        kernel_objects[reference].delete()


def test_legal_issue_with_one_line_and_one_context_commits(kernel_objects):
    header, line, issue_context = create_issue_ledger(kernel_objects)

    assert header.lines.get() == line
    assert header.issue_context == issue_context
    assert issue_context.created_at is not None


def test_issue_without_context_is_rejected_by_deferred_guard(kernel_objects):
    with pytest.raises(IntegrityError, match="exactly one IssueContext"):
        with transaction.atomic():
            header = create_header(
                kernel_objects,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["material"],
                quantity=Decimal("1"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                source_location=kernel_objects["location"],
                target_location=None,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS inventory_issue_requires_context_trg IMMEDIATE"
                )


def test_second_issue_context_is_impossible(kernel_objects):
    header, _line, _issue_context = create_issue_ledger(kernel_objects)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            create_issue_context(kernel_objects, header)


def test_receipt_cannot_own_issue_context(kernel_objects):
    header, _line = create_ledger(kernel_objects)

    with pytest.raises(IntegrityError, match="requires an ISSUE transaction"):
        with transaction.atomic():
            create_issue_context(kernel_objects, header)


def test_issue_without_line_is_rejected_by_deferred_guard(kernel_objects):
    with pytest.raises(IntegrityError, match="exactly one line"):
        with transaction.atomic():
            header = create_header(
                kernel_objects,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            create_issue_context(kernel_objects, header)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS inventory_tx_requires_line_trg IMMEDIATE"
                )


def test_issue_with_two_lines_is_rejected_by_deferred_guard(kernel_objects):
    with pytest.raises(IntegrityError, match="exactly one line"):
        with transaction.atomic():
            header = create_header(
                kernel_objects,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            for line_number in (1, 2):
                InventoryTransactionLine.objects.create(
                    transaction=header,
                    line_number=line_number,
                    material=kernel_objects["material"],
                    quantity=Decimal("1"),
                    unit=kernel_objects["unit"],
                    condition=kernel_objects["condition"],
                    source_location=kernel_objects["location"],
                    target_location=None,
                )
            create_issue_context(kernel_objects, header)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS inventory_issue_line_cardinality_trg IMMEDIATE"
                )


@pytest.mark.parametrize(
    "field_name",
    [
        "receiver_first_name_snapshot",
        "receiver_last_name_snapshot",
        "receiver_employee_number_snapshot",
        "production_line_code_snapshot",
        "production_line_name_snapshot",
        "usage_location_text",
    ],
)
def test_issue_context_rejects_whitespace_only_text(kernel_objects, field_name):
    with pytest.raises(IntegrityError):
        create_issue_ledger(
            kernel_objects,
            context_overrides={field_name: " \t\n "},
        )


def test_issue_context_references_are_restricted(kernel_objects):
    create_issue_ledger(kernel_objects)

    with pytest.raises(RestrictedError):
        kernel_objects["employee"].delete()
    with pytest.raises(RestrictedError):
        kernel_objects["production_line"].delete()


def test_issue_context_snapshots_do_not_follow_master_edits(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)
    original_snapshots = (
        issue_context.receiver_first_name_snapshot,
        issue_context.receiver_last_name_snapshot,
        issue_context.receiver_employee_number_snapshot,
        issue_context.production_line_code_snapshot,
        issue_context.production_line_name_snapshot,
    )

    Employee.objects.filter(pk=kernel_objects["employee"].pk).update(
        first_name="Fatma",
        last_name="Kaya",
        employee_number=f"CHANGED-{uuid.uuid4().hex[:8]}",
        active=False,
    )
    ProductionLine.objects.filter(pk=kernel_objects["production_line"].pk).update(
        code=f"CHANGED-{uuid.uuid4().hex[:8]}",
        name="Değişen hat",
        active=False,
    )

    issue_context.refresh_from_db()
    assert (
        issue_context.receiver_first_name_snapshot,
        issue_context.receiver_last_name_snapshot,
        issue_context.receiver_employee_number_snapshot,
        issue_context.production_line_code_snapshot,
        issue_context.production_line_name_snapshot,
    ) == original_snapshots
    assert IssueContext.objects.filter(pk=issue_context.pk).exists()


def test_stock_balance_identity_is_unique(kernel_objects):
    values = {
        "material": kernel_objects["material"],
        "location": kernel_objects["location"],
        "condition": kernel_objects["condition"],
    }
    StockBalance.objects.create(**values)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            StockBalance.objects.create(**values)


def test_stock_balance_rejects_negative_quantity(kernel_objects):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            StockBalance.objects.create(
                material=kernel_objects["material"],
                location=kernel_objects["location"],
                condition=kernel_objects["condition"],
                quantity=Decimal("-0.001"),
            )


def test_zero_stock_balance_is_allowed_and_retained(kernel_objects):
    balance = StockBalance.objects.create(
        material=kernel_objects["material"],
        location=kernel_objects["location"],
        condition=kernel_objects["condition"],
    )

    assert balance.quantity == Decimal("0")
    assert StockBalance.objects.filter(pk=balance.pk, quantity=0).exists()


def test_stock_balance_references_are_restricted(kernel_objects):
    StockBalance.objects.create(
        material=kernel_objects["material"],
        location=kernel_objects["location"],
        condition=kernel_objects["condition"],
    )

    for key in ("material", "location", "condition"):
        with pytest.raises(RestrictedError):
            kernel_objects[key].delete()


@pytest.mark.parametrize("model_name", ["header", "line"])
def test_model_save_rejects_ledger_updates(kernel_objects, model_name):
    header, line = create_ledger(kernel_objects)
    ledger_row = header if model_name == "header" else line

    with pytest.raises(ValidationError):
        ledger_row.save()


@pytest.mark.parametrize("model_name", ["header", "line"])
def test_model_delete_rejects_ledger_deletes(kernel_objects, model_name):
    header, line = create_ledger(kernel_objects)
    ledger_row = header if model_name == "header" else line

    with pytest.raises(ValidationError):
        ledger_row.delete()


@pytest.mark.parametrize(
    ("model", "update_values"),
    [
        (InventoryTransaction, {"request_fingerprint": "c" * 64}),
        (InventoryTransactionLine, {"quantity": Decimal("2")}),
    ],
)
def test_queryset_update_is_rejected_by_database(
    kernel_objects, model, update_values
):
    header, line = create_ledger(kernel_objects)
    ledger_row = header if model is InventoryTransaction else line

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            model.objects.filter(pk=ledger_row.pk).update(**update_values)


@pytest.mark.parametrize("model", [InventoryTransaction, InventoryTransactionLine])
def test_bulk_update_is_rejected_by_database(kernel_objects, model):
    header, line = create_ledger(kernel_objects)
    ledger_row = header if model is InventoryTransaction else line
    field_name = (
        "request_fingerprint"
        if model is InventoryTransaction
        else "quantity"
    )
    setattr(
        ledger_row,
        field_name,
        "e" * 64 if model is InventoryTransaction else Decimal("4"),
    )

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            model.objects.bulk_update([ledger_row], [field_name])


@pytest.mark.parametrize("model", [InventoryTransaction, InventoryTransactionLine])
def test_queryset_delete_is_rejected_by_database(kernel_objects, model):
    header, line = create_ledger(kernel_objects)
    ledger_row = header if model is InventoryTransaction else line

    with pytest.raises((DatabaseError, RestrictedError)):
        with transaction.atomic():
            model.objects.filter(pk=ledger_row.pk).delete()

    assert model.objects.filter(pk=ledger_row.pk).exists()


@pytest.mark.parametrize(
    ("table_name", "column_name", "value"),
    [
        ("inventory_inventorytransaction", "request_fingerprint", "d" * 64),
        ("inventory_inventorytransactionline", "quantity", Decimal("3")),
    ],
)
def test_raw_sql_update_is_rejected(
    kernel_objects, table_name, column_name, value
):
    header, line = create_ledger(kernel_objects)
    row_id = header.pk if table_name.endswith("transaction") else line.pk

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE "{table_name}" SET "{column_name}" = %s WHERE id = %s',
                [value, row_id],
            )


@pytest.mark.parametrize(
    "table_name",
    ["inventory_inventorytransaction", "inventory_inventorytransactionline"],
)
def test_raw_sql_delete_is_rejected(kernel_objects, table_name):
    header, line = create_ledger(kernel_objects)
    row_id = header.pk if table_name.endswith("transaction") else line.pk

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM "{table_name}" WHERE id = %s', [row_id])


def test_model_save_rejects_issue_context_update(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)
    issue_context.usage_location_text = "Pano 8"

    with pytest.raises(ValidationError):
        issue_context.save()


def test_model_delete_rejects_issue_context_delete(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)

    with pytest.raises(ValidationError):
        issue_context.delete()


def test_queryset_update_rejects_issue_context_update(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            IssueContext.objects.filter(pk=issue_context.pk).update(
                usage_location_text="Pano 8"
            )


def test_bulk_update_rejects_issue_context_update(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)
    issue_context.usage_location_text = "Pano 8"

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            IssueContext.objects.bulk_update(
                [issue_context],
                ["usage_location_text"],
            )


def test_queryset_delete_rejects_issue_context_delete(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            IssueContext.objects.filter(pk=issue_context.pk).delete()

    assert IssueContext.objects.filter(pk=issue_context.pk).exists()


def test_raw_sql_update_rejects_issue_context_update(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE inventory_issuecontext
                SET usage_location_text = %s
                WHERE transaction_id = %s
                """,
                ["Pano 8", issue_context.pk],
            )


def test_raw_sql_delete_rejects_issue_context_delete(kernel_objects):
    _header, _line, issue_context = create_issue_ledger(kernel_objects)

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM inventory_issuecontext WHERE transaction_id = %s",
                [issue_context.pk],
            )


def test_serialized_material_cannot_enter_quantity_line(kernel_objects):
    material = Material.objects.create(
        material_code=f"SER-{uuid.uuid4().hex[:8]}",
        name="Tekil malzeme",
        category=kernel_objects["category"],
        unit=kernel_objects["unit"],
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )

    with pytest.raises(IntegrityError, match="QUANTITY material"):
        create_ledger(kernel_objects, material=material)


def test_serialized_material_cannot_have_stock_balance(kernel_objects):
    material = Material.objects.create(
        material_code=f"SER-{uuid.uuid4().hex[:8]}",
        name="Tekil malzeme",
        category=kernel_objects["category"],
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )

    with pytest.raises(IntegrityError, match="QUANTITY material"):
        with transaction.atomic():
            StockBalance.objects.create(
                material=material,
                location=kernel_objects["location"],
                condition=kernel_objects["condition"],
            )


def test_line_unit_must_match_current_material_unit(kernel_objects):
    other_unit = UnitOfMeasure.objects.create(
        code=f"OTHER-{uuid.uuid4().hex[:8]}",
        name="Başka birim",
    )

    with pytest.raises(IntegrityError, match="must match material unit"):
        create_ledger(kernel_objects, unit=other_unit)


def test_material_without_unit_cannot_enter_quantity_line(kernel_objects):
    material = Material.objects.create(
        material_code=f"SER-NULL-{uuid.uuid4().hex[:8]}",
        name="Birimsiz tekil malzeme",
        category=kernel_objects["category"],
        unit=None,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )

    with pytest.raises(IntegrityError):
        create_ledger(kernel_objects, material=material)


@pytest.mark.parametrize(
    ("missing_reference", "message"),
    [
        ("condition", "condition does not exist"),
        ("target", "target location does not exist"),
    ],
)
def test_line_guard_rejects_missing_condition_or_target_reference(
    kernel_objects, missing_reference, message
):
    condition_id = kernel_objects["condition"].pk
    target_location_id = kernel_objects["location"].pk
    if missing_reference == "condition":
        condition_id = uuid.uuid4()
    else:
        target_location_id = uuid.uuid4()

    with pytest.raises(IntegrityError, match=message):
        with transaction.atomic(), connection.cursor() as cursor:
            header = create_header(kernel_objects)
            cursor.execute(
                """
                INSERT INTO inventory_inventorytransactionline (
                    id, transaction_id, line_number, material_id, quantity,
                    unit_id, condition_id, source_location_id,
                    target_location_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
                """,
                [
                    uuid.uuid4(),
                    header.pk,
                    1,
                    kernel_objects["material"].pk,
                    Decimal("1"),
                    kernel_objects["unit"].pk,
                    condition_id,
                    target_location_id,
                    timezone.now(),
                ],
            )


def test_issue_line_guard_rejects_missing_source_reference(kernel_objects):
    with pytest.raises(IntegrityError, match="source location does not exist"):
        with transaction.atomic(), connection.cursor() as cursor:
            header = create_header(
                kernel_objects,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            cursor.execute(
                """
                INSERT INTO inventory_inventorytransactionline (
                    id, transaction_id, line_number, material_id, quantity,
                    unit_id, condition_id, source_location_id,
                    target_location_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
                """,
                [
                    uuid.uuid4(),
                    header.pk,
                    1,
                    kernel_objects["material"].pk,
                    Decimal("1"),
                    kernel_objects["unit"].pk,
                    kernel_objects["condition"].pk,
                    uuid.uuid4(),
                    timezone.now(),
                ],
            )


@pytest.mark.parametrize(
    ("active", "can_hold_stock"),
    [(False, True), (True, False), (False, False)],
)
def test_positive_balance_requires_active_stock_holding_location(
    kernel_objects, active, can_hold_stock
):
    location = Location.objects.create(
        code=f"INVALID-{uuid.uuid4().hex[:8]}",
        name="Pozitif stok için geçersiz",
        active=active,
        can_hold_stock=can_hold_stock,
    )

    with pytest.raises(IntegrityError, match="active stock-holding location"):
        with transaction.atomic():
            StockBalance.objects.create(
                material=kernel_objects["material"],
                location=location,
                condition=kernel_objects["condition"],
                quantity=Decimal("1"),
            )


@pytest.mark.parametrize(
    ("active", "can_hold_stock"),
    [(False, True), (True, False), (False, False)],
)
def test_zero_balance_is_allowed_at_inactive_or_nonholding_location(
    kernel_objects, active, can_hold_stock
):
    location = Location.objects.create(
        code=f"ZERO-{uuid.uuid4().hex[:8]}",
        name="Sıfır bakiye lokasyonu",
        active=active,
        can_hold_stock=can_hold_stock,
    )

    balance = StockBalance.objects.create(
        material=kernel_objects["material"],
        location=location,
        condition=kernel_objects["condition"],
        quantity=Decimal("0"),
    )

    assert StockBalance.objects.filter(pk=balance.pk).exists()


def test_zero_balance_cannot_be_updated_positive_at_inactive_location(
    kernel_objects,
):
    location = Location.objects.create(
        code=f"UPDATE-{uuid.uuid4().hex[:8]}",
        name="Update guard lokasyonu",
        active=False,
        can_hold_stock=True,
    )
    balance = StockBalance.objects.create(
        material=kernel_objects["material"],
        location=location,
        condition=kernel_objects["condition"],
        quantity=Decimal("0"),
    )

    with pytest.raises(IntegrityError, match="active stock-holding location"):
        with transaction.atomic():
            StockBalance.objects.filter(pk=balance.pk).update(quantity=Decimal("1"))


def test_material_tracking_mode_cannot_change_after_ledger_history(kernel_objects):
    create_ledger(kernel_objects)

    with pytest.raises(IntegrityError, match="cannot change after inventory history"):
        with transaction.atomic():
            Material.objects.filter(pk=kernel_objects["material"].pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )


def test_material_tracking_mode_cannot_change_after_issue_history(kernel_objects):
    create_issue_ledger(kernel_objects)

    with pytest.raises(IntegrityError, match="cannot change after inventory history"):
        with transaction.atomic():
            Material.objects.filter(pk=kernel_objects["material"].pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )


def test_receipt_still_allows_multiple_lines(kernel_objects):
    with transaction.atomic():
        header = create_header(kernel_objects)
        for line_number in (1, 2):
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=line_number,
                material=kernel_objects["material"],
                quantity=Decimal("1"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                source_location=None,
                target_location=kernel_objects["location"],
            )
        force_inventory_completeness_constraints()

    assert header.lines.count() == 2


def test_material_tracking_mode_cannot_change_after_zero_balance(kernel_objects):
    StockBalance.objects.create(
        material=kernel_objects["material"],
        location=kernel_objects["location"],
        condition=kernel_objects["condition"],
        quantity=Decimal("0"),
    )

    with pytest.raises(IntegrityError, match="cannot change after inventory history"):
        with transaction.atomic():
            Material.objects.filter(pk=kernel_objects["material"].pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )


def test_material_tracking_mode_can_change_without_inventory_history(kernel_objects):
    Material.objects.filter(pk=kernel_objects["material"].pk).update(
        tracking_mode=Material.TrackingMode.SERIALIZED
    )

    kernel_objects["material"].refresh_from_db()
    assert kernel_objects["material"].tracking_mode == Material.TrackingMode.SERIALIZED


@pytest.mark.parametrize(
    "update_values",
    [{"active": False}, {"can_hold_stock": False}],
)
def test_location_cannot_lose_stock_capability_with_positive_balance(
    kernel_objects, update_values
):
    StockBalance.objects.create(
        material=kernel_objects["material"],
        location=kernel_objects["location"],
        condition=kernel_objects["condition"],
        quantity=Decimal("1"),
    )

    with pytest.raises(IntegrityError, match="positive stock"):
        with transaction.atomic():
            Location.objects.filter(pk=kernel_objects["location"].pk).update(
                **update_values
            )


@pytest.mark.parametrize(
    "update_values",
    [{"active": False}, {"can_hold_stock": False}],
)
def test_location_capability_changes_are_allowed_when_balances_are_zero(
    kernel_objects, update_values
):
    StockBalance.objects.create(
        material=kernel_objects["material"],
        location=kernel_objects["location"],
        condition=kernel_objects["condition"],
        quantity=Decimal("0"),
    )

    assert (
        Location.objects.filter(pk=kernel_objects["location"].pk).update(
            **update_values
        )
        == 1
    )


def test_reactivation_and_enabling_stock_capability_remain_allowed(kernel_objects):
    location = Location.objects.create(
        code=f"ENABLE-{uuid.uuid4().hex[:8]}",
        name="Yeniden etkinleştirilecek",
        active=False,
        can_hold_stock=False,
    )
    StockBalance.objects.create(
        material=kernel_objects["material"],
        location=location,
        condition=kernel_objects["condition"],
        quantity=Decimal("0"),
    )

    Location.objects.filter(pk=location.pk).update(active=True, can_hold_stock=True)
    location.refresh_from_db()
    assert location.active is True
    assert location.can_hold_stock is True


def test_kernel_does_not_create_plain_stock_audit_event(kernel_objects):
    initial_count = AuditEvent.objects.count()

    create_ledger(kernel_objects)
    create_issue_ledger(kernel_objects)

    assert AuditEvent.objects.count() == initial_count


def test_receipt_routes_are_operational_not_management_and_admin_stays_readonly():
    assert hasattr(inventory_services, "receive_quantity")
    assert hasattr(inventory_forms, "QuantityReceiptForm")
    receipt_patterns = [
        pattern for pattern in inventory_urlpatterns if pattern.name.startswith("receipt-")
    ]
    management_patterns = [
        pattern
        for pattern in inventory_urlpatterns
        if pattern.name.startswith("production-line-")
    ]
    assert {pattern.name for pattern in receipt_patterns} == {
        "receipt-create",
        "receipt-detail",
    }
    assert management_patterns
    assert all(
        str(pattern.pattern).startswith("management/") for pattern in management_patterns
    )
    assert all(
        str(pattern.pattern).startswith("inventory/") for pattern in receipt_patterns
    )
    assert InventoryTransaction not in admin.site._registry
    assert InventoryTransactionLine not in admin.site._registry
    assert IssueContext not in admin.site._registry
    assert StockBalance not in admin.site._registry


def test_managed_permission_boundary_is_nineteen_with_receive_stock_only():
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 19
    assert "inventory.receive_stock" in SAFE_CATALOG_PERMISSION_LABELS
    assert "inventory.issue_stock" not in SAFE_CATALOG_PERMISSION_LABELS
    assert "issue_stock" not in {
        codename for codename, _name in InventoryTransaction._meta.permissions
    }
    assert not any(
        label.startswith(
            (
                "inventory.add_inventorytransaction",
                "inventory.change_inventorytransaction",
                "inventory.delete_inventorytransaction",
                "inventory.add_stockbalance",
                "inventory.change_stockbalance",
                "inventory.delete_stockbalance",
            )
        )
        for label in SAFE_CATALOG_PERMISSION_LABELS
    )
