from __future__ import annotations

import importlib
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Max
from django.db.models.deletion import RestrictedError

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    SerializedAsset,
    StockBalance,
)
from locations.models import Location

pytestmark = pytest.mark.django_db


@pytest.fixture
def kernel_objects():
    suffix = uuid.uuid4().hex[:8]
    category = Category.objects.create(name=f"SK kategori {suffix}")
    unit = UnitOfMeasure.objects.create(code=f"SK-U-{suffix}", name="Adet")
    serialized_material = Material.objects.create(
        material_code=f"SK-S-{suffix}",
        name="Tekil",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    other_serialized_material = Material.objects.create(
        material_code=f"SK-S2-{suffix}",
        name="Başka tekil",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    quantity_material = Material.objects.create(
        material_code=f"SK-Q-{suffix}",
        name="Miktar",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"SK-C-{suffix}", name="Yeni", sort_order=820
    )
    location = Location.objects.create(
        code=f"SK-L-{suffix}", name="Raf", active=True, can_hold_stock=True
    )
    other_location = Location.objects.create(
        code=f"SK-L2-{suffix}", name="Diğer raf", active=True, can_hold_stock=True
    )
    user = get_user_model().objects.create_user(username=f"sk-{suffix}")
    employee = Employee.objects.create(
        employee_number=f"SK-E-{suffix}",
        first_name="Ayşe",
        last_name="Yılmaz",
    )
    production_line = ProductionLine.objects.create(
        code=f"SK-PL-{suffix}", name="Kernel hat"
    )
    return {
        "category": category,
        "unit": unit,
        "serialized_material": serialized_material,
        "other_serialized_material": other_serialized_material,
        "quantity_material": quantity_material,
        "condition": condition,
        "location": location,
        "other_location": other_location,
        "user": user,
        "employee": employee,
        "production_line": production_line,
    }


def _asset(objects, **overrides):
    values = {
        "material": objects["serialized_material"],
        "internal_asset_code": f"SK-A-{uuid.uuid4().hex[:8]}",
        "serial_number": None,
        "current_location": objects["location"],
        "current_condition": objects["condition"],
    }
    values.update(overrides)
    return SerializedAsset.objects.create(**values)


def _header(objects, transaction_type="RECEIPT"):
    return InventoryTransaction.objects.create(
        operation_id=uuid.uuid4(),
        request_fingerprint="a" * 64,
        transaction_type=transaction_type,
        acting_user=objects["user"],
        occurred_at="2026-09-13T12:00:00+03:00",
    )


def _next_asset_event_seq(asset):
    current = (
        InventoryTransactionLine.objects.filter(serialized_asset=asset)
        .aggregate(max_seq=Max("asset_event_seq"))["max_seq"]
    )
    return 1 if current is None else current + 1


def _serialized_line(objects, asset, **overrides):
    values = {
        "line_number": 1,
        "material": asset.material,
        "serialized_asset": asset,
        "quantity": None,
        "unit": None,
        "condition": asset.current_condition,
        "source_location": None,
        "target_location": asset.current_location,
        "asset_event_seq": _next_asset_event_seq(asset),
    }
    values.update(overrides)
    with transaction.atomic():
        values.setdefault("transaction", _header(objects))
        line = InventoryTransactionLine.objects.create(**values)
    return line


def _issue_context(objects, header):
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


def _serialized_issue_line(objects, asset, **overrides):
    values = {
        "line_number": 1,
        "material": asset.material,
        "serialized_asset": asset,
        "quantity": None,
        "unit": None,
        "condition": asset.current_condition or objects["condition"],
        "source_location": asset.current_location or objects["location"],
        "target_location": None,
        "asset_event_seq": _next_asset_event_seq(asset),
    }
    values.update(overrides)
    with transaction.atomic():
        header = values.get("transaction")
        if header is None:
            header = _header(objects, "ISSUE")
            values["transaction"] = header
        if not hasattr(header, "_issue_context"):
            header._issue_context = _issue_context(objects, header)
        line = InventoryTransactionLine.objects.create(**values)
    return line


def test_serialized_asset_model_normalizes_identifiers(kernel_objects):
    asset = SerializedAsset(
        material=kernel_objects["serialized_material"],
        internal_asset_code="  ASSET-X  ",
        serial_number="   ",
        current_location=kernel_objects["location"],
        current_condition=kernel_objects["condition"],
    )
    asset.full_clean()
    assert asset.internal_asset_code == "ASSET-X"
    assert asset.serial_number is None
    assert asset.current_state == SerializedAsset.CurrentState.IN_STOCK


def test_quantity_material_is_rejected_for_serialized_asset(kernel_objects):
    with pytest.raises(IntegrityError, match="SERIALIZED material"):
        with transaction.atomic():
            _asset(kernel_objects, material=kernel_objects["quantity_material"])


def test_internal_asset_code_is_required_and_unique(kernel_objects):
    _asset(kernel_objects, internal_asset_code="UNIQUE-ASSET")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _asset(kernel_objects, internal_asset_code="UNIQUE-ASSET")
    with pytest.raises((IntegrityError, ValueError)):
        with transaction.atomic():
            _asset(kernel_objects, internal_asset_code=None)


def test_serial_number_nullable_and_unique_only_per_material(kernel_objects):
    _asset(kernel_objects, internal_asset_code="S-1", serial_number=None)
    _asset(kernel_objects, internal_asset_code="S-2", serial_number=None)
    _asset(kernel_objects, internal_asset_code="S-3", serial_number="MFG-X")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _asset(kernel_objects, internal_asset_code="S-4", serial_number="MFG-X")
    other = _asset(
        kernel_objects,
        material=kernel_objects["other_serialized_material"],
        internal_asset_code="S-5",
        serial_number="MFG-X",
    )
    assert other.serial_number == "MFG-X"


@pytest.mark.parametrize(("active", "can_hold"), [(False, True), (True, False)])
def test_serialized_asset_requires_valid_current_location(
    kernel_objects, active, can_hold
):
    location = Location.objects.create(
        code=f"BAD-{uuid.uuid4().hex[:8]}",
        name="Geçersiz",
        active=active,
        can_hold_stock=can_hold,
    )
    with pytest.raises(IntegrityError, match="active stock-holding location"):
        with transaction.atomic():
            _asset(kernel_objects, current_location=location)


def test_serialized_asset_requires_active_condition(kernel_objects):
    condition = MaterialCondition.objects.create(
        code=f"BAD-C-{uuid.uuid4().hex[:8]}", name="Pasif", active=False
    )
    with pytest.raises(IntegrityError, match="active condition"):
        with transaction.atomic():
            _asset(kernel_objects, current_condition=condition)


def test_serialized_asset_state_shape_allows_issued_without_location(kernel_objects):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _asset(kernel_objects, current_state="SCRAP")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _asset(
                kernel_objects,
                current_state=SerializedAsset.CurrentState.ISSUED,
                current_location=kernel_objects["location"],
            )
    issued = _asset(
        kernel_objects,
        current_state=SerializedAsset.CurrentState.ISSUED,
        current_location=None,
    )
    assert issued.current_state == SerializedAsset.CurrentState.ISSUED
    assert issued.current_location_id is None
    assert issued.current_condition_id == kernel_objects["condition"].pk
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SerializedAsset.objects.filter(pk=issued.pk).update(
                current_state=SerializedAsset.CurrentState.IN_STOCK
            )


def test_valid_serialized_receive_line_shape(kernel_objects):
    asset = _asset(kernel_objects)
    line = _serialized_line(kernel_objects, asset)
    assert line.serialized_asset_id == asset.pk
    assert line.quantity is None
    assert line.unit_id is None


def test_serialized_line_material_must_match_asset(kernel_objects):
    asset = _asset(kernel_objects)
    with pytest.raises(IntegrityError, match="must match asset material"):
        with transaction.atomic():
            _serialized_line(
                kernel_objects,
                asset,
                material=kernel_objects["other_serialized_material"],
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"serialized_asset": None, "quantity": None, "unit": None},
        {"quantity": Decimal("1.000")},
        {"unit": "quantity-unit"},
    ],
)
def test_malformed_serialized_line_shapes_are_rejected(kernel_objects, overrides):
    asset = _asset(kernel_objects)
    if overrides.get("unit") == "quantity-unit":
        overrides = {**overrides, "unit": kernel_objects["unit"]}
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _serialized_line(kernel_objects, asset, **overrides)


def test_quantity_line_cannot_reference_serialized_asset(kernel_objects):
    asset = _asset(kernel_objects)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=_header(kernel_objects),
                line_number=1,
                material=kernel_objects["quantity_material"],
                serialized_asset=asset,
                quantity=Decimal("1.000"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                target_location=kernel_objects["location"],
            )


def test_serialized_stockbalance_remains_forbidden(kernel_objects):
    with pytest.raises(IntegrityError, match="QUANTITY material"):
        with transaction.atomic():
            StockBalance.objects.create(
                material=kernel_objects["serialized_material"],
                location=kernel_objects["location"],
                condition=kernel_objects["condition"],
                quantity=Decimal("0"),
            )


def test_asset_can_be_received_only_once(kernel_objects):
    asset = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    with pytest.raises(IntegrityError, match="exactly one RECEIVE"):
        with transaction.atomic():
            _serialized_line(kernel_objects, asset)


def test_quantity_and_serialized_lines_cannot_share_receipt(kernel_objects):
    asset = _asset(kernel_objects)
    header = _header(kernel_objects)
    with transaction.atomic():
        InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=1,
            material=asset.material,
            serialized_asset=asset,
            quantity=None,
            unit=None,
            condition=asset.current_condition,
            target_location=asset.current_location,
            asset_event_seq=1,
        )

    with pytest.raises(IntegrityError, match="cannot share a RECEIVE"):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=2,
                material=kernel_objects["quantity_material"],
                quantity=Decimal("1.000"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                target_location=kernel_objects["location"],
            )


def test_valid_serialized_issue_return_and_transfer_line_shapes(kernel_objects):
    asset = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    issue_line = _serialized_issue_line(kernel_objects, asset)
    assert issue_line.quantity is None
    assert issue_line.unit_id is None
    assert issue_line.source_location_id == asset.current_location_id
    assert issue_line.target_location_id is None

    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_state=SerializedAsset.CurrentState.ISSUED,
        current_location=None,
    )
    asset.refresh_from_db()
    return_header = _header(kernel_objects, "RETURN")
    with transaction.atomic():
        return_line = InventoryTransactionLine.objects.create(
            transaction=return_header,
            line_number=1,
            material=asset.material,
            serialized_asset=asset,
            quantity=None,
            unit=None,
            condition=asset.current_condition,
            source_location=None,
            target_location=kernel_objects["other_location"],
            original_issue_line=issue_line,
            asset_event_seq=_next_asset_event_seq(asset),
        )
    assert return_line.original_issue_line_id == issue_line.pk

    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_state=SerializedAsset.CurrentState.IN_STOCK,
        current_location=kernel_objects["other_location"],
    )
    asset.refresh_from_db()
    transfer_header = _header(kernel_objects, "TRANSFER")
    with transaction.atomic():
        transfer_line = InventoryTransactionLine.objects.create(
            transaction=transfer_header,
            line_number=1,
            material=asset.material,
            serialized_asset=asset,
            quantity=None,
            unit=None,
            condition=asset.current_condition,
            source_location=kernel_objects["other_location"],
            target_location=kernel_objects["location"],
            asset_event_seq=_next_asset_event_seq(asset),
        )
    assert transfer_line.source_location_id == kernel_objects["other_location"].pk
    assert transfer_line.target_location_id == kernel_objects["location"].pk


def test_serialized_issue_without_genesis_is_rejected(kernel_objects):
    asset = _asset(kernel_objects)
    with pytest.raises(IntegrityError, match="established RECEIVE or INITIAL_BALANCE"):
        with transaction.atomic():
            _serialized_issue_line(kernel_objects, asset)


def test_malformed_serialized_issue_and_transfer_shapes_are_rejected(kernel_objects):
    asset = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    with pytest.raises(IntegrityError, match="serialized ISSUE"):
        with transaction.atomic():
            _serialized_issue_line(
                kernel_objects,
                asset,
                target_location=kernel_objects["other_location"],
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=_header(kernel_objects, "TRANSFER"),
                line_number=1,
                material=asset.material,
                serialized_asset=asset,
                quantity=None,
                unit=None,
                condition=asset.current_condition,
                source_location=kernel_objects["location"],
                target_location=kernel_objects["location"],
                asset_event_seq=_next_asset_event_seq(asset),
            )


def test_serialized_return_requires_same_asset_and_rejects_duplicates(kernel_objects):
    asset = _asset(kernel_objects)
    other = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    _serialized_line(kernel_objects, other)
    issue_line = _serialized_issue_line(kernel_objects, asset)
    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_state=SerializedAsset.CurrentState.ISSUED,
        current_location=None,
    )
    asset.refresh_from_db()
    with pytest.raises(IntegrityError, match="original ISSUE asset"):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=_header(kernel_objects, "RETURN"),
                line_number=1,
                material=other.material,
                serialized_asset=other,
                quantity=None,
                unit=None,
                condition=asset.current_condition,
                source_location=None,
                target_location=kernel_objects["location"],
                original_issue_line=issue_line,
                asset_event_seq=_next_asset_event_seq(other),
            )
    with transaction.atomic():
        InventoryTransactionLine.objects.create(
            transaction=_header(kernel_objects, "RETURN"),
            line_number=1,
            material=asset.material,
            serialized_asset=asset,
            quantity=None,
            unit=None,
            condition=asset.current_condition,
            source_location=None,
            target_location=kernel_objects["location"],
            original_issue_line=issue_line,
            asset_event_seq=_next_asset_event_seq(asset),
        )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=_header(kernel_objects, "RETURN"),
                line_number=1,
                material=asset.material,
                serialized_asset=asset,
                quantity=None,
                unit=None,
                condition=asset.current_condition,
                source_location=None,
                target_location=kernel_objects["other_location"],
                original_issue_line=issue_line,
                asset_event_seq=_next_asset_event_seq(asset),
            )


def test_asset_identity_and_hard_delete_are_protected_after_history(kernel_objects):
    asset = _asset(kernel_objects, internal_asset_code="LOCKED-ASSET")
    _serialized_line(kernel_objects, asset)

    asset.internal_asset_code = "RENAMED"
    with pytest.raises(ValidationError, match="kimliği değiştirilemez"):
        asset.save()
    with pytest.raises(ValidationError, match="silinemez"):
        asset.delete()
    with pytest.raises(DatabaseError, match="identity with inventory history"):
        with transaction.atomic():
            SerializedAsset.objects.filter(pk=asset.pk).update(
                internal_asset_code="RAW-RENAME"
            )
    with pytest.raises((DatabaseError, RestrictedError)):
        with transaction.atomic():
            SerializedAsset.objects.filter(pk=asset.pk).delete()


def test_location_condition_and_tracking_mode_cannot_invalidate_current_asset(
    kernel_objects,
):
    asset = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    with pytest.raises(IntegrityError, match="serialized inventory"):
        with transaction.atomic():
            Location.objects.filter(pk=kernel_objects["location"].pk).update(active=False)
    with pytest.raises(IntegrityError, match="cannot be deactivated"):
        with transaction.atomic():
            MaterialCondition.objects.filter(pk=kernel_objects["condition"].pk).update(
                active=False
            )
    with pytest.raises(IntegrityError, match="cannot change after inventory history"):
        with transaction.atomic():
            Material.objects.filter(pk=kernel_objects["serialized_material"].pk).update(
                tracking_mode=Material.TrackingMode.QUANTITY
            )


def test_serialized_line_helpers_assign_monotonic_event_seq(kernel_objects):
    asset = _asset(kernel_objects)
    genesis = _serialized_line(kernel_objects, asset)
    assert genesis.asset_event_seq == 1
    issue = _serialized_issue_line(kernel_objects, asset)
    assert issue.asset_event_seq == 2


def test_serialized_line_null_event_seq_is_rejected(kernel_objects):
    asset = _asset(kernel_objects)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=_header(kernel_objects),
                line_number=1,
                material=asset.material,
                serialized_asset=asset,
                quantity=None,
                unit=None,
                condition=asset.current_condition,
                target_location=asset.current_location,
                asset_event_seq=None,
            )


def test_quantity_line_non_null_event_seq_is_rejected(kernel_objects):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=_header(kernel_objects),
                line_number=1,
                material=kernel_objects["quantity_material"],
                serialized_asset=None,
                quantity=Decimal("1.000"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                target_location=kernel_objects["location"],
                asset_event_seq=1,
            )


@pytest.mark.django_db(transaction=True)
def test_duplicate_asset_event_seq_is_rejected(kernel_objects):
    asset = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE inventory_inventorytransactionline "
            "DISABLE TRIGGER inventory_line_quantity_guard_trg"
        )
    try:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                InventoryTransactionLine.objects.create(
                    transaction=_header(kernel_objects, "TRANSFER"),
                    line_number=1,
                    material=asset.material,
                    serialized_asset=asset,
                    quantity=None,
                    unit=None,
                    condition=asset.current_condition,
                    source_location=kernel_objects["location"],
                    target_location=kernel_objects["other_location"],
                    asset_event_seq=1,
                )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE inventory_inventorytransactionline "
                "ENABLE TRIGGER inventory_line_quantity_guard_trg"
            )


def test_gap_and_out_of_order_event_seq_are_rejected(kernel_objects):
    asset = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    with pytest.raises(IntegrityError, match="next causal event"):
        with transaction.atomic():
            _serialized_issue_line(kernel_objects, asset, asset_event_seq=3)
    with pytest.raises(IntegrityError, match="next causal event"):
        with transaction.atomic():
            _serialized_issue_line(kernel_objects, asset, asset_event_seq=1)


def test_serialized_migration_reverse_fails_closed_when_history_exists(
    kernel_objects,
):
    asset = _asset(kernel_objects)
    _serialized_line(kernel_objects, asset)
    _serialized_issue_line(kernel_objects, asset)
    migration = importlib.import_module(
        "inventory.migrations.0012_serialized_inventory_movements"
    )
    reverse_precondition = migration.REVERSE_PRECHECK_SQL

    with pytest.raises(IntegrityError, match="cannot reverse serialized movements"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(reverse_precondition)
