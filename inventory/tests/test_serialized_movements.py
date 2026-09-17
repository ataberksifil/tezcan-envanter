from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

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
from inventory.services.issues import (
    CONDITION_MISMATCH,
    INVALID_ASSET_STATE,
    INVALID_SOURCE,
    issue_quantity,
    issue_serialized,
)
from inventory.services.projections import verify_serialized_projection
from inventory.services.receipts import (
    OPERATION_CONFLICT,
    TRACKING_MODE_MISMATCH,
    receive_quantity,
    receive_serialized,
)
from inventory.services.returns import (
    INVALID_ORIGINAL_ISSUE,
    SERIALIZED_ISSUE_ALREADY_RETURNED,
    return_quantity,
    return_serialized,
)
from inventory.services.transfers import (
    SAME_SOURCE_DESTINATION,
    transfer_quantity,
    transfer_serialized,
)
from locations.models import Location

pytestmark = pytest.mark.django_db
User = get_user_model()


def _grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                codename=codename,
            )
        )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def objects():
    suffix = uuid.uuid4().hex[:8]
    category = Category.objects.create(name=f"SM kategori {suffix}")
    unit = UnitOfMeasure.objects.create(code=f"SM-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"SM-S-{suffix}",
        name="Tekil hareket malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    quantity_material = Material.objects.create(
        material_code=f"SM-Q-{suffix}",
        name="Miktar hareket malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"SM-C-{suffix}", name="Yeni", sort_order=860
    )
    other_condition = MaterialCondition.objects.create(
        code=f"SM-C2-{suffix}", name="Diğer", sort_order=861
    )
    location = Location.objects.create(
        code=f"SM-L-{suffix}", name="Kaynak raf", active=True, can_hold_stock=True
    )
    other_location = Location.objects.create(
        code=f"SM-L2-{suffix}", name="Hedef raf", active=True, can_hold_stock=True
    )
    employee = Employee.objects.create(
        employee_number=f"SM-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
    )
    production_line = ProductionLine.objects.create(
        code=f"SM-PL-{suffix}", name="Montaj"
    )
    actor = _grant(
        User.objects.create_user(username=f"sm-{suffix}"),
        "receive_stock",
        "issue_stock",
        "return_stock",
        "transfer_stock",
        "view_inventorytransaction",
    )
    return {
        "unit": unit,
        "material": material,
        "quantity_material": quantity_material,
        "condition": condition,
        "other_condition": other_condition,
        "location": location,
        "other_location": other_location,
        "employee": employee,
        "production_line": production_line,
        "actor": actor,
    }


def _receive(objects, **overrides):
    values = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "material_id": objects["material"].pk,
        "internal_asset_code": f"SM-A-{uuid.uuid4().hex[:8]}",
        "serial_number": None,
        "condition_id": objects["condition"].pk,
        "target_location_id": objects["location"].pk,
    }
    values.update(overrides)
    return receive_serialized(**values)


def _issue_request(objects, asset, **overrides):
    values = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "serialized_asset_id": asset.pk,
        "source_location_id": objects["location"].pk,
        "condition_id": objects["condition"].pk,
        "receiver_employee_id": objects["employee"].pk,
        "production_line_id": objects["production_line"].pk,
        "usage_location_text": "Pano 7",
    }
    values.update(overrides)
    return values


def _assert_code(exc_info, code):
    assert exc_info.value.code == code


def _event_history(asset):
    return list(
        InventoryTransactionLine.objects.filter(serialized_asset=asset)
        .order_by("asset_event_seq")
        .values_list("asset_event_seq", "transaction__transaction_type")
    )


def test_serialized_issue_happy_path_projects_issued_and_keeps_context(objects):
    asset = _receive(objects).serialized_asset
    result = issue_serialized(**_issue_request(objects, asset))

    assert result.replayed is False
    line = result.lines[0]
    asset.refresh_from_db()
    assert line.transaction.transaction_type == InventoryTransaction.TransactionType.ISSUE
    assert line.serialized_asset_id == asset.pk
    assert line.quantity is None
    assert line.unit_id is None
    assert line.source_location_id == objects["location"].pk
    assert line.target_location_id is None
    assert line.condition_id == objects["condition"].pk
    assert asset.current_state == SerializedAsset.CurrentState.ISSUED
    assert asset.current_location_id is None
    assert asset.current_condition_id == objects["condition"].pk
    assert result.issue_context.receiver_employee_number_snapshot == (
        objects["employee"].employee_number
    )
    assert result.issue_context.usage_location_text == "Pano 7"
    assert StockBalance.objects.count() == 0
    assert verify_serialized_projection() == ()
    assert _event_history(asset) == [
        (1, InventoryTransaction.TransactionType.RECEIPT),
        (2, InventoryTransaction.TransactionType.ISSUE),
    ]


def test_serialized_issue_rejects_wrong_source_condition_state_and_quantity(objects):
    asset = _receive(objects).serialized_asset
    with pytest.raises(ValidationError) as exc_info:
        issue_serialized(
            **_issue_request(
                objects, asset, source_location_id=objects["other_location"].pk
            )
        )
    _assert_code(exc_info, INVALID_SOURCE)

    with pytest.raises(ValidationError) as exc_info:
        issue_serialized(
            **_issue_request(
                objects, asset, condition_id=objects["other_condition"].pk
            )
        )
    _assert_code(exc_info, CONDITION_MISMATCH)

    issue_serialized(**_issue_request(objects, asset))
    with pytest.raises(ValidationError) as exc_info:
        issue_serialized(**_issue_request(objects, asset))
    _assert_code(exc_info, INVALID_ASSET_STATE)

    with pytest.raises(ValidationError) as exc_info:
        issue_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=objects["quantity_material"].pk,
            source_location_id=objects["location"].pk,
            condition_id=objects["condition"].pk,
            receiver_employee_id=objects["employee"].pk,
            production_line_id=objects["production_line"].pk,
            usage_location_text="Pano 7",
        )
    _assert_code(exc_info, "inventory.invalid_serialized_asset")


def test_serialized_issue_rejects_quantity_material_via_receive_then_wrong_service(
    objects,
):
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            material_id=objects["material"].pk,
            unit_id=objects["unit"].pk,
            condition_id=objects["condition"].pk,
            source_location_id=objects["location"].pk,
            quantity=Decimal("1.000"),
            receiver_employee_id=objects["employee"].pk,
            production_line_id=objects["production_line"].pk,
            usage_location_text="Pano 7",
        )
    _assert_code(exc_info, TRACKING_MODE_MISMATCH)


def test_serialized_issue_replay_and_conflict(objects):
    asset = _receive(objects).serialized_asset
    request = _issue_request(objects, asset)
    first = issue_serialized(**request)
    replay = issue_serialized(**request)
    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert replay.serialized_asset.pk == asset.pk
    assert replay.lines[0].asset_event_seq == first.lines[0].asset_event_seq == 2
    assert InventoryTransactionLine.objects.filter(serialized_asset=asset).count() == 2
    assert InventoryTransaction.objects.filter(transaction_type="ISSUE").count() == 1
    assert IssueContext.objects.count() == 1

    changed = dict(request)
    changed["usage_location_text"] = "Başka yer"
    with pytest.raises(ValidationError) as exc_info:
        issue_serialized(**changed)
    _assert_code(exc_info, OPERATION_CONFLICT)


def test_serialized_return_happy_path_and_reissue(objects):
    asset = _receive(objects).serialized_asset
    issue = issue_serialized(**_issue_request(objects, asset))
    result = return_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=issue.lines[0].pk,
        serialized_asset_id=asset.pk,
        target_location_id=objects["other_location"].pk,
    )
    line = result.lines[0]
    asset.refresh_from_db()
    assert line.transaction.transaction_type == InventoryTransaction.TransactionType.RETURN
    assert line.serialized_asset_id == asset.pk
    assert line.quantity is None
    assert line.source_location_id is None
    assert line.target_location_id == objects["other_location"].pk
    assert line.original_issue_line_id == issue.lines[0].pk
    assert asset.current_state == SerializedAsset.CurrentState.IN_STOCK
    assert asset.current_location_id == objects["other_location"].pk
    assert asset.current_condition_id == objects["condition"].pk
    assert StockBalance.objects.count() == 0
    assert verify_serialized_projection() == ()

    reissue = issue_serialized(
        **_issue_request(
            objects, asset, source_location_id=objects["other_location"].pk
        )
    )
    assert reissue.replayed is False
    asset.refresh_from_db()
    assert asset.current_state == SerializedAsset.CurrentState.ISSUED
    later_return = return_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=reissue.lines[0].pk,
        serialized_asset_id=asset.pk,
        target_location_id=objects["location"].pk,
    )
    assert later_return.lines[0].original_issue_line_id == reissue.lines[0].pk
    assert verify_serialized_projection() == ()


def test_serialized_return_rejects_wrong_line_asset_condition_and_duplicate(objects):
    first_asset = _receive(objects).serialized_asset
    second_asset = _receive(objects, internal_asset_code=f"SM-B-{uuid.uuid4().hex[:8]}").serialized_asset
    first_issue = issue_serialized(**_issue_request(objects, first_asset))
    second_issue = issue_serialized(**_issue_request(objects, second_asset))

    with pytest.raises(ValidationError) as exc_info:
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=first_issue.lines[0].pk,
            serialized_asset_id=second_asset.pk,
            target_location_id=objects["other_location"].pk,
        )
    _assert_code(exc_info, "inventory.invalid_serialized_asset")

    with pytest.raises(ValidationError) as exc_info:
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=second_issue.lines[0].pk,
            serialized_asset_id=first_asset.pk,
            target_location_id=objects["other_location"].pk,
        )
    _assert_code(exc_info, "inventory.invalid_serialized_asset")

    return_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=first_issue.lines[0].pk,
        serialized_asset_id=first_asset.pk,
        target_location_id=objects["other_location"].pk,
    )
    with pytest.raises(ValidationError) as exc_info:
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=first_issue.lines[0].pk,
            serialized_asset_id=first_asset.pk,
            target_location_id=objects["location"].pk,
        )
    _assert_code(exc_info, SERIALIZED_ISSUE_ALREADY_RETURNED)

    first_asset.refresh_from_db()
    with pytest.raises(ValidationError) as exc_info:
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=second_issue.lines[0].pk,
            serialized_asset_id=first_asset.pk,
            target_location_id=objects["location"].pk,
        )
    _assert_code(exc_info, "inventory.invalid_serialized_asset")


def test_serialized_return_rejects_quantity_issue_and_currently_in_stock(objects):
    receive_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=objects["quantity_material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["location"].pk,
        quantity=Decimal("2.000"),
    )
    quantity_issue = issue_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=objects["quantity_material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        source_location_id=objects["location"].pk,
        quantity=Decimal("1.000"),
        receiver_employee_id=objects["employee"].pk,
        production_line_id=objects["production_line"].pk,
        usage_location_text="Pano 7",
    )
    asset = _receive(objects).serialized_asset
    with pytest.raises(ValidationError) as exc_info:
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=quantity_issue.lines[0].pk,
            serialized_asset_id=asset.pk,
            target_location_id=objects["other_location"].pk,
        )
    _assert_code(exc_info, INVALID_ORIGINAL_ISSUE)

    with pytest.raises(ValidationError) as exc_info:
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            target_location_id=objects["other_location"].pk,
        )
    _assert_code(exc_info, INVALID_ORIGINAL_ISSUE)

    issue = issue_serialized(**_issue_request(objects, asset))
    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_state=SerializedAsset.CurrentState.IN_STOCK,
        current_location=objects["location"],
    )
    with pytest.raises(ValidationError) as exc_info:
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=issue.lines[0].pk,
            serialized_asset_id=asset.pk,
            target_location_id=objects["other_location"].pk,
        )
    _assert_code(exc_info, INVALID_ASSET_STATE)


def test_quantity_return_behavior_is_unchanged_beside_serialized_history(objects):
    receive_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=objects["quantity_material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["location"].pk,
        quantity=Decimal("5.000"),
    )
    issue = issue_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=objects["quantity_material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        source_location_id=objects["location"].pk,
        quantity=Decimal("3.000"),
        receiver_employee_id=objects["employee"].pk,
        production_line_id=objects["production_line"].pk,
        usage_location_text="Pano 7",
    )
    first = return_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=issue.lines[0].pk,
        target_location_id=objects["other_location"].pk,
        quantity=Decimal("1.000"),
    )
    second = return_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=issue.lines[0].pk,
        target_location_id=objects["other_location"].pk,
        quantity=Decimal("2.000"),
    )
    assert first.replayed is False
    assert second.replayed is False
    balance = StockBalance.objects.get(
        material=objects["quantity_material"],
        location=objects["other_location"],
        condition=objects["condition"],
    )
    assert balance.quantity == Decimal("3.000")


def test_serialized_transfer_happy_path_and_rejections(objects):
    asset = _receive(objects).serialized_asset
    result = transfer_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        serialized_asset_id=asset.pk,
        source_location_id=objects["location"].pk,
        target_location_id=objects["other_location"].pk,
        condition_id=objects["condition"].pk,
    )
    line = result.lines[0]
    asset.refresh_from_db()
    assert line.transaction.transaction_type == (
        InventoryTransaction.TransactionType.TRANSFER
    )
    assert line.quantity is None
    assert line.unit_id is None
    assert line.source_location_id == objects["location"].pk
    assert line.target_location_id == objects["other_location"].pk
    assert line.original_issue_line_id is None
    assert asset.current_state == SerializedAsset.CurrentState.IN_STOCK
    assert asset.current_location_id == objects["other_location"].pk
    assert asset.current_condition_id == objects["condition"].pk
    assert StockBalance.objects.count() == 0
    assert verify_serialized_projection() == ()

    with pytest.raises(ValidationError) as exc_info:
        transfer_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=objects["other_location"].pk,
            target_location_id=objects["other_location"].pk,
            condition_id=objects["condition"].pk,
        )
    _assert_code(exc_info, SAME_SOURCE_DESTINATION)

    with pytest.raises(ValidationError) as exc_info:
        transfer_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=objects["location"].pk,
            target_location_id=objects["location"].pk
            if False
            else objects["location"].pk,
            condition_id=objects["condition"].pk,
        )
    _assert_code(exc_info, SAME_SOURCE_DESTINATION)

    with pytest.raises(ValidationError) as exc_info:
        transfer_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=objects["location"].pk,
            target_location_id=objects["location"].pk,
            condition_id=objects["condition"].pk,
        )
    _assert_code(exc_info, SAME_SOURCE_DESTINATION)

    with pytest.raises(ValidationError) as exc_info:
        transfer_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=objects["location"].pk,
            target_location_id=objects["other_location"].pk
            if asset.current_location_id == objects["other_location"].pk
            else objects["location"].pk,
            condition_id=objects["condition"].pk,
        )
    _assert_code(exc_info, INVALID_SOURCE)

    with pytest.raises(ValidationError) as exc_info:
        transfer_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=objects["other_location"].pk,
            target_location_id=objects["location"].pk,
            condition_id=objects["other_condition"].pk,
        )
    _assert_code(exc_info, CONDITION_MISMATCH)

    issue_serialized(
        **_issue_request(
            objects, asset, source_location_id=objects["other_location"].pk
        )
    )
    with pytest.raises(ValidationError) as exc_info:
        transfer_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=objects["other_location"].pk,
            target_location_id=objects["location"].pk,
            condition_id=objects["condition"].pk,
        )
    _assert_code(exc_info, INVALID_ASSET_STATE)


def test_serialized_transfer_replay_and_quantity_transfer_unchanged(objects):
    asset = _receive(objects).serialized_asset
    request = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "serialized_asset_id": asset.pk,
        "source_location_id": objects["location"].pk,
        "target_location_id": objects["other_location"].pk,
        "condition_id": objects["condition"].pk,
    }
    first = transfer_serialized(**request)
    replay = transfer_serialized(**request)
    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    changed = dict(request)
    changed["condition_id"] = objects["other_condition"].pk
    with pytest.raises(ValidationError) as exc_info:
        transfer_serialized(**changed)
    _assert_code(exc_info, OPERATION_CONFLICT)

    receive_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=objects["quantity_material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["location"].pk,
        quantity=Decimal("4.000"),
    )
    transfer_quantity(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=objects["quantity_material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        source_location_id=objects["location"].pk,
        target_location_id=objects["other_location"].pk,
        quantity=Decimal("1.500"),
    )
    source_balance = StockBalance.objects.get(
        material=objects["quantity_material"],
        location=objects["location"],
        condition=objects["condition"],
    )
    target_balance = StockBalance.objects.get(
        material=objects["quantity_material"],
        location=objects["other_location"],
        condition=objects["condition"],
    )
    assert source_balance.quantity == Decimal("2.500")
    assert target_balance.quantity == Decimal("1.500")


def test_serialized_return_replay(objects):
    asset = _receive(objects).serialized_asset
    issue = issue_serialized(**_issue_request(objects, asset))
    request = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "original_issue_line_id": issue.lines[0].pk,
        "serialized_asset_id": asset.pk,
        "target_location_id": objects["other_location"].pk,
    }
    first = return_serialized(**request)
    replay = return_serialized(**request)
    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert replay.lines[0].asset_event_seq == first.lines[0].asset_event_seq == 3
    assert InventoryTransactionLine.objects.filter(serialized_asset=asset).count() == 3
    changed = dict(request)
    changed["target_location_id"] = objects["location"].pk
    with pytest.raises(ValidationError) as exc_info:
        return_serialized(**changed)
    _assert_code(exc_info, OPERATION_CONFLICT)


def test_serialized_issue_and_return_roll_back_on_projection_failure(objects):
    asset = _receive(objects).serialized_asset
    with patch(
        "inventory.services.issues.assert_serialized_projection_for_asset",
        side_effect=ValidationError("fail", code="inventory.projection_mismatch"),
    ):
        with pytest.raises(ValidationError, match="fail"):
            issue_serialized(**_issue_request(objects, asset))
    asset.refresh_from_db()
    assert asset.current_state == SerializedAsset.CurrentState.IN_STOCK
    assert asset.current_location_id == objects["location"].pk
    assert InventoryTransaction.objects.filter(transaction_type="ISSUE").count() == 0
    assert IssueContext.objects.count() == 0
    assert _event_history(asset) == [
        (1, InventoryTransaction.TransactionType.RECEIPT),
    ]

    issue = issue_serialized(**_issue_request(objects, asset))
    assert issue.lines[0].asset_event_seq == 2
    with patch(
        "inventory.services.returns.assert_serialized_projection_for_asset",
        side_effect=ValidationError("fail", code="inventory.projection_mismatch"),
    ):
        with pytest.raises(ValidationError, match="fail"):
            return_serialized(
                actor=objects["actor"],
                operation_id=uuid.uuid4(),
                original_issue_line_id=issue.lines[0].pk,
                serialized_asset_id=asset.pk,
                target_location_id=objects["other_location"].pk,
            )
    asset.refresh_from_db()
    assert asset.current_state == SerializedAsset.CurrentState.ISSUED
    assert asset.current_location_id is None
    assert InventoryTransaction.objects.filter(transaction_type="RETURN").count() == 0
    recovered = return_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=issue.lines[0].pk,
        serialized_asset_id=asset.pk,
        target_location_id=objects["other_location"].pk,
    )
    assert recovered.lines[0].asset_event_seq == 3
    assert _event_history(asset) == [
        (1, InventoryTransaction.TransactionType.RECEIPT),
        (2, InventoryTransaction.TransactionType.ISSUE),
        (3, InventoryTransaction.TransactionType.RETURN),
    ]


def test_serialized_transfer_rolls_back_before_projection_completion(objects):
    asset = _receive(objects).serialized_asset
    with patch.object(
        SerializedAsset, "save", side_effect=RuntimeError("controlled projection failure")
    ):
        with pytest.raises(RuntimeError, match="controlled projection failure"):
            transfer_serialized(
                actor=objects["actor"],
                operation_id=uuid.uuid4(),
                serialized_asset_id=asset.pk,
                source_location_id=objects["location"].pk,
                target_location_id=objects["other_location"].pk,
                condition_id=objects["condition"].pk,
            )
    asset.refresh_from_db()
    assert asset.current_location_id == objects["location"].pk
    assert InventoryTransaction.objects.filter(transaction_type="TRANSFER").count() == 0
    recovered = transfer_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        serialized_asset_id=asset.pk,
        source_location_id=objects["location"].pk,
        target_location_id=objects["other_location"].pk,
        condition_id=objects["condition"].pk,
    )
    assert recovered.lines[0].asset_event_seq == 2
    assert _event_history(asset) == [
        (1, InventoryTransaction.TransactionType.RECEIPT),
        (2, InventoryTransaction.TransactionType.TRANSFER),
    ]


def test_verifier_accepts_valid_chain_and_detects_invalid_order_and_corruption(objects):
    asset = _receive(objects).serialized_asset
    transfer_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        serialized_asset_id=asset.pk,
        source_location_id=objects["location"].pk,
        target_location_id=objects["other_location"].pk,
        condition_id=objects["condition"].pk,
    )
    issue = issue_serialized(
        **_issue_request(
            objects, asset, source_location_id=objects["other_location"].pk
        )
    )
    return_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=issue.lines[0].pk,
        serialized_asset_id=asset.pk,
        target_location_id=objects["location"].pk,
    )
    transfer_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        serialized_asset_id=asset.pk,
        source_location_id=objects["location"].pk,
        target_location_id=objects["other_location"].pk,
        condition_id=objects["condition"].pk,
    )
    assert verify_serialized_projection() == ()
    assert _event_history(asset) == [
        (1, InventoryTransaction.TransactionType.RECEIPT),
        (2, InventoryTransaction.TransactionType.TRANSFER),
        (3, InventoryTransaction.TransactionType.ISSUE),
        (4, InventoryTransaction.TransactionType.RETURN),
        (5, InventoryTransaction.TransactionType.TRANSFER),
    ]

    InventoryTransactionLine.objects.filter(pk=issue.lines[0].pk).update()
    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_location=objects["location"]
    )
    mismatch = verify_serialized_projection()[0]
    assert "current_location" in mismatch.reasons
    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_location=objects["other_location"]
    )

    from types import SimpleNamespace

    from inventory.services.projections import _reduce_serialized_history

    genesis = SimpleNamespace(
        pk=uuid.uuid4(),
        transaction=SimpleNamespace(
            transaction_type=InventoryTransaction.TransactionType.RECEIPT
        ),
        material_id=asset.material_id,
        serialized_asset_id=asset.pk,
        quantity=None,
        unit_id=None,
        source_location_id=None,
        target_location_id=objects["location"].pk,
        condition_id=objects["condition"].pk,
        original_issue_line_id=None,
    )
    illegal_return = SimpleNamespace(
        pk=uuid.uuid4(),
        transaction=SimpleNamespace(
            transaction_type=InventoryTransaction.TransactionType.RETURN
        ),
        material_id=asset.material_id,
        serialized_asset_id=asset.pk,
        quantity=None,
        unit_id=None,
        source_location_id=None,
        target_location_id=objects["other_location"].pk,
        condition_id=objects["condition"].pk,
        original_issue_line_id=uuid.uuid4(),
    )
    reduced = _reduce_serialized_history(asset, [genesis, illegal_return])
    assert "invalid_event_ordering" in reduced.reasons


def test_transaction_history_and_material_detail_render_serialized_movements(objects):
    asset = _receive(objects).serialized_asset
    issue = issue_serialized(**_issue_request(objects, asset))
    returned = return_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=issue.lines[0].pk,
        serialized_asset_id=asset.pk,
        target_location_id=objects["other_location"].pk,
    )
    transferred = transfer_serialized(
        actor=objects["actor"],
        operation_id=uuid.uuid4(),
        serialized_asset_id=asset.pk,
        source_location_id=objects["other_location"].pk,
        target_location_id=objects["location"].pk,
        condition_id=objects["condition"].pk,
    )
    client = Client()
    client.force_login(objects["actor"])
    for transaction in (issue.transaction, returned.transaction, transferred.transaction):
        response = client.get(
            reverse("inventory:transaction-history-detail", args=[transaction.pk])
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert asset.internal_asset_code in content
        assert "None" not in content.split("Miktar", 1)[-1][:80] if "Miktar" in content else True

    objects["actor"].user_permissions.add(
        Permission.objects.get(content_type__app_label="catalog", codename="view_material"),
        Permission.objects.get(
            content_type__app_label="inventory", codename="view_stockbalance"
        ),
    )
    objects["actor"] = User.objects.get(pk=objects["actor"].pk)
    client.force_login(objects["actor"])
    response = client.get(reverse("catalog:material-detail", args=[objects["material"].pk]))
    assert response.status_code == 200
    assert asset.internal_asset_code in response.content.decode()


class _FrozenClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def test_frozen_clock_valid_chain_commits_by_event_sequence(objects):
    frozen = datetime(2026, 9, 17, 7, 0, tzinfo=dt_timezone.utc)
    clock = _FrozenClock(frozen)
    with patch("django.utils.timezone.now", clock):
        asset = _receive(objects).serialized_asset
        issue = issue_serialized(**_issue_request(objects, asset))
        returned = return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=issue.lines[0].pk,
            serialized_asset_id=asset.pk,
            target_location_id=objects["location"].pk,
        )
        reissue = issue_serialized(**_issue_request(objects, asset))

    lines = list(
        InventoryTransactionLine.objects.filter(serialized_asset=asset)
        .select_related("transaction")
        .order_by("asset_event_seq")
    )
    assert {line.transaction.occurred_at for line in lines} == {frozen}
    assert {line.created_at for line in lines} == {frozen}
    assert {line.transaction.created_at for line in lines} == {frozen}
    assert _event_history(asset) == [
        (1, InventoryTransaction.TransactionType.RECEIPT),
        (2, InventoryTransaction.TransactionType.ISSUE),
        (3, InventoryTransaction.TransactionType.RETURN),
        (4, InventoryTransaction.TransactionType.ISSUE),
    ]
    assert returned.lines[0].asset_event_seq == 3
    assert reissue.lines[0].asset_event_seq == 4
    assert verify_serialized_projection() == ()


def test_inverted_timestamps_do_not_control_serialized_replay_order(objects):
    frozen = datetime(2026, 9, 17, 12, 0, tzinfo=dt_timezone.utc)
    clock = _FrozenClock(frozen)
    with patch("django.utils.timezone.now", clock):
        asset = _receive(objects).serialized_asset
        clock.value = frozen - timedelta(hours=3)
        issue = issue_serialized(**_issue_request(objects, asset))
        clock.value = frozen - timedelta(hours=5)
        return_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=issue.lines[0].pk,
            serialized_asset_id=asset.pk,
            target_location_id=objects["location"].pk,
        )
        clock.value = frozen - timedelta(hours=1)
        transfer_serialized(
            actor=objects["actor"],
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=objects["location"].pk,
            target_location_id=objects["other_location"].pk,
            condition_id=objects["condition"].pk,
        )

    lines = list(
        InventoryTransactionLine.objects.filter(serialized_asset=asset)
        .select_related("transaction")
        .order_by("asset_event_seq")
    )
    occurred = [line.transaction.occurred_at for line in lines]
    assert occurred[0] == frozen
    assert occurred[1] == frozen - timedelta(hours=3)
    assert occurred[2] == frozen - timedelta(hours=5)
    assert occurred[3] == frozen - timedelta(hours=1)
    assert occurred != sorted(occurred)
    assert _event_history(asset) == [
        (1, InventoryTransaction.TransactionType.RECEIPT),
        (2, InventoryTransaction.TransactionType.ISSUE),
        (3, InventoryTransaction.TransactionType.RETURN),
        (4, InventoryTransaction.TransactionType.TRANSFER),
    ]
    assert verify_serialized_projection() == ()
