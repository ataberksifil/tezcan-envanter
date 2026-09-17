from __future__ import annotations

import uuid
from copy import copy
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command

from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.services.projections import verify_serialized_projection
from inventory.services.receipts import (
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    INTERNAL_ASSET_CODE_CONFLICT,
    INVALID_DESTINATION,
    OPERATION_CONFLICT,
    SERIAL_NUMBER_CONFLICT,
    TRACKING_MODE_MISMATCH,
    normalize_internal_asset_code,
    normalize_serial_number,
    receive_serialized,
)
from locations.models import Location

pytestmark = pytest.mark.django_db
User = get_user_model()


def _grant_receive(user):
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory",
            codename="receive_stock",
        )
    )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def serialized_objects():
    suffix = uuid.uuid4().hex[:8]
    category = Category.objects.create(name=f"Tekil kategori {suffix}")
    unit = UnitOfMeasure.objects.create(code=f"SR-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"SR-M-{suffix}",
        name="Tekil malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    other_material = Material.objects.create(
        material_code=f"SR-M2-{suffix}",
        name="Diğer tekil malzeme",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    quantity_material = Material.objects.create(
        material_code=f"SR-Q-{suffix}",
        name="Miktar malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"SR-C-{suffix}", name="Yeni", sort_order=810
    )
    other_condition = MaterialCondition.objects.create(
        code=f"SR-C2-{suffix}", name="Diğer", sort_order=811
    )
    location = Location.objects.create(
        code=f"SR-L-{suffix}",
        name="Tekil raf",
        active=True,
        can_hold_stock=True,
    )
    other_location = Location.objects.create(
        code=f"SR-L2-{suffix}",
        name="Diğer tekil raf",
        active=True,
        can_hold_stock=True,
    )
    actor = _grant_receive(User.objects.create_user(username=f"sr-{suffix}"))
    return {
        "category": category,
        "unit": unit,
        "material": material,
        "other_material": other_material,
        "quantity_material": quantity_material,
        "condition": condition,
        "other_condition": other_condition,
        "location": location,
        "other_location": other_location,
        "actor": actor,
    }


def _request(objects, **overrides):
    values = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "material_id": objects["material"].pk,
        "internal_asset_code": f"ASSET-{uuid.uuid4().hex[:10]}",
        "serial_number": f"SER-{uuid.uuid4().hex[:10]}",
        "condition_id": objects["condition"].pk,
        "target_location_id": objects["location"].pk,
    }
    values.update(overrides)
    return values


def test_identifier_normalization():
    assert normalize_internal_asset_code("  A-001  ") == "A-001"
    assert normalize_serial_number(None) is None
    assert normalize_serial_number("") is None
    assert normalize_serial_number("   ") is None
    assert normalize_serial_number("  SN-7  ") == "SN-7"


def test_successful_serialized_receive_is_atomic_ledger_and_projection(
    serialized_objects,
):
    audit_count = AuditEvent.objects.count()
    result = receive_serialized(
        **_request(
            serialized_objects,
            internal_asset_code="  TEZ-0001  ",
            serial_number="  MFG-100  ",
        )
    )

    assert result.replayed is False
    assert result.serialized_asset is not None
    asset = result.serialized_asset
    assert asset.internal_asset_code == "TEZ-0001"
    assert asset.serial_number == "MFG-100"
    assert asset.material_id == serialized_objects["material"].pk
    assert asset.current_location_id == serialized_objects["location"].pk
    assert asset.current_condition_id == serialized_objects["condition"].pk
    assert asset.current_state == SerializedAsset.CurrentState.IN_STOCK
    assert asset.created_at is not None
    assert asset.updated_at is not None

    line = result.lines[0]
    assert line.transaction.transaction_type == InventoryTransaction.TransactionType.RECEIPT
    assert line.serialized_asset_id == asset.pk
    assert line.material_id == asset.material_id
    assert line.quantity is None
    assert line.unit_id is None
    assert line.source_location_id is None
    assert line.target_location_id == asset.current_location_id
    assert line.condition_id == asset.current_condition_id
    assert line.asset_event_seq == 1
    assert StockBalance.objects.count() == 0
    assert AuditEvent.objects.count() == audit_count
    assert verify_serialized_projection() == ()


def test_blank_serial_is_stored_as_null(serialized_objects):
    result = receive_serialized(
        **_request(serialized_objects, serial_number="   ")
    )
    assert result.serialized_asset.serial_number is None


def test_same_operation_and_semantic_input_replays_exact_asset(serialized_objects):
    request = _request(serialized_objects)
    first = receive_serialized(**request)
    replay = receive_serialized(**request)

    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert replay.serialized_asset.pk == first.serialized_asset.pk
    assert first.lines[0].asset_event_seq == 1
    assert replay.lines[0].asset_event_seq == 1
    assert SerializedAsset.objects.count() == 1
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("internal_asset_code", "DIFFERENT"),
        ("serial_number", "DIFFERENT-SERIAL"),
    ],
)
def test_same_operation_changed_identifier_is_conflict(
    serialized_objects, field, value
):
    request = _request(serialized_objects)
    receive_serialized(**request)
    changed = dict(request)
    changed[field] = value
    with pytest.raises(ValidationError) as exc_info:
        receive_serialized(**changed)
    assert exc_info.value.code == OPERATION_CONFLICT
    assert SerializedAsset.objects.count() == 1
    assert InventoryTransaction.objects.count() == 1


def test_duplicate_internal_code_fails_without_second_ledger(serialized_objects):
    receive_serialized(
        **_request(serialized_objects, internal_asset_code="GLOBAL-1")
    )
    with pytest.raises(ValidationError) as exc_info:
        receive_serialized(
            **_request(
                serialized_objects,
                material_id=serialized_objects["other_material"].pk,
                internal_asset_code="GLOBAL-1",
            )
        )
    assert exc_info.value.code == INTERNAL_ASSET_CODE_CONFLICT
    assert SerializedAsset.objects.count() == 1
    assert InventoryTransaction.objects.count() == 1


def test_serial_is_unique_per_material_but_not_globally(serialized_objects):
    receive_serialized(
        **_request(
            serialized_objects,
            internal_asset_code="PER-MAT-1",
            serial_number="SHARED-SERIAL",
        )
    )
    with pytest.raises(ValidationError) as exc_info:
        receive_serialized(
            **_request(
                serialized_objects,
                internal_asset_code="PER-MAT-2",
                serial_number="SHARED-SERIAL",
            )
        )
    assert exc_info.value.code == SERIAL_NUMBER_CONFLICT

    second_material = receive_serialized(
        **_request(
            serialized_objects,
            material_id=serialized_objects["other_material"].pk,
            internal_asset_code="PER-MAT-3",
            serial_number="SHARED-SERIAL",
        )
    )
    assert second_material.serialized_asset.serial_number == "SHARED-SERIAL"
    assert SerializedAsset.objects.count() == 2
    assert InventoryTransaction.objects.count() == 2


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("quantity_material", TRACKING_MODE_MISMATCH),
        ("inactive_material", INACTIVE_MATERIAL),
        ("inactive_condition", INACTIVE_CONDITION),
        ("inactive_location", INVALID_DESTINATION),
        ("nonholding_location", INVALID_DESTINATION),
    ],
)
def test_serialized_receive_revalidates_locked_masters(
    serialized_objects, mutation, error_code
):
    request = _request(serialized_objects)
    if mutation == "quantity_material":
        request["material_id"] = serialized_objects["quantity_material"].pk
    elif mutation == "inactive_material":
        Material.objects.filter(pk=serialized_objects["material"].pk).update(active=False)
    elif mutation == "inactive_condition":
        MaterialCondition.objects.filter(pk=serialized_objects["condition"].pk).update(
            active=False
        )
    elif mutation == "inactive_location":
        Location.objects.filter(pk=serialized_objects["location"].pk).update(active=False)
    else:
        Location.objects.filter(pk=serialized_objects["location"].pk).update(
            can_hold_stock=False
        )

    with pytest.raises(ValidationError) as exc_info:
        receive_serialized(**request)
    assert exc_info.value.code == error_code
    assert SerializedAsset.objects.count() == 0
    assert InventoryTransaction.objects.count() == 0


def test_failure_after_asset_creation_leaves_no_orphan(serialized_objects):
    with patch.object(
        InventoryTransactionLine,
        "save",
        side_effect=RuntimeError("controlled line failure"),
    ):
        with pytest.raises(RuntimeError, match="controlled line failure"):
            receive_serialized(**_request(serialized_objects))

    assert SerializedAsset.objects.count() == 0
    assert InventoryTransaction.objects.count() == 0
    assert InventoryTransactionLine.objects.count() == 0


def test_projection_verifier_detects_location_and_condition_drift(serialized_objects):
    result = receive_serialized(**_request(serialized_objects))
    asset = result.serialized_asset
    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_location=serialized_objects["other_location"],
        current_condition=serialized_objects["other_condition"],
    )

    mismatch = verify_serialized_projection()[0]
    assert mismatch.asset_id == asset.pk
    assert mismatch.reasons == ("current_location", "current_condition")
    assert mismatch.expected_location_id == serialized_objects["location"].pk
    assert mismatch.actual_location_id == serialized_objects["other_location"].pk
    assert mismatch.expected_condition_id == serialized_objects["condition"].pk
    assert mismatch.actual_condition_id == serialized_objects["other_condition"].pk


def test_projection_verifier_reports_asset_without_ledger(serialized_objects):
    asset = SerializedAsset.objects.create(
        material=serialized_objects["material"],
        internal_asset_code="ORPHAN-1",
        serial_number=None,
        current_location=serialized_objects["location"],
        current_condition=serialized_objects["condition"],
    )
    mismatch = verify_serialized_projection()[0]
    assert mismatch.asset_id == asset.pk
    assert mismatch.reasons == ("receipt_count=0",)


def test_projection_verifier_detects_state_drift_in_reducer(serialized_objects):
    result = receive_serialized(**_request(serialized_objects))
    corrupted = copy(result.serialized_asset)
    corrupted.current_state = "CORRUPT"

    with patch.object(SerializedAsset.objects, "using") as using:
        using.return_value.order_by.return_value = [corrupted]
        mismatch = verify_serialized_projection()[0]

    assert mismatch.asset_id == corrupted.pk
    assert mismatch.reasons == ("current_state",)
    assert mismatch.expected_state == SerializedAsset.CurrentState.IN_STOCK
    assert mismatch.actual_state == "CORRUPT"


def test_projection_command_reports_serialized_drift_read_only(serialized_objects):
    result = receive_serialized(**_request(serialized_objects))
    asset = result.serialized_asset
    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_location=serialized_objects["other_location"]
    )
    stderr = StringIO()

    with pytest.raises(CommandError, match="verification failed"):
        call_command("verify_inventory_projection", stderr=stderr)

    output = stderr.getvalue()
    assert "Serialized projection drift" in output
    assert asset.internal_asset_code in output
    assert "current_location" in output
    assert SerializedAsset.objects.get(pk=asset.pk).current_location_id == (
        serialized_objects["other_location"].pk
    )
