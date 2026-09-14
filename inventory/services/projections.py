from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from inventory.models import InventoryTransaction, InventoryTransactionLine, SerializedAsset, StockBalance


@dataclass(frozen=True)
class QuantityProjectionMismatch:
    material_id: uuid.UUID
    location_id: uuid.UUID
    condition_id: uuid.UUID
    expected_quantity: Decimal
    actual_quantity: Decimal


@dataclass(frozen=True)
class SerializedProjectionMismatch:
    asset_id: uuid.UUID
    internal_asset_code: str
    reasons: tuple[str, ...]
    expected_material_id: uuid.UUID | None
    actual_material_id: uuid.UUID
    expected_location_id: uuid.UUID | None
    actual_location_id: uuid.UUID
    expected_condition_id: uuid.UUID | None
    actual_condition_id: uuid.UUID
    expected_state: str | None
    actual_state: str


def verify_quantity_projection(
    *, using: str = "default"
) -> tuple[QuantityProjectionMismatch, ...]:
    """Compare all canonical target additions and source deductions with StockBalance."""
    inbound = {
        (row["material_id"], row["target_location_id"], row["condition_id"]): row[
            "quantity"
        ]
        for row in (
            InventoryTransactionLine.objects.using(using)
            .filter(
                transaction__transaction_type__in=(
                    "RECEIPT",
                    "RETURN",
                    "TRANSFER",
                    "CONTROLLED_CORRECTION",
                    "COUNT_RECONCILIATION",
                ),
                target_location__isnull=False,
                serialized_asset__isnull=True,
            )
            .values("material_id", "target_location_id", "condition_id")
            .annotate(quantity=Sum("quantity"))
        )
    }
    outbound = {
        (row["material_id"], row["source_location_id"], row["condition_id"]): row[
            "quantity"
        ]
        for row in (
            InventoryTransactionLine.objects.using(using)
            .filter(
                transaction__transaction_type__in=(
                    "ISSUE",
                    "TRANSFER",
                    "CONTROLLED_CORRECTION",
                    "COUNT_RECONCILIATION",
                ),
                source_location__isnull=False,
                serialized_asset__isnull=True,
            )
            .values("material_id", "source_location_id", "condition_id")
            .annotate(quantity=Sum("quantity"))
        )
    }
    zero = Decimal("0.000")
    expected = {
        identity: inbound.get(identity, zero) - outbound.get(identity, zero)
        for identity in set(inbound) | set(outbound)
    }
    actual = {
        (row["material_id"], row["location_id"], row["condition_id"]): row[
            "quantity"
        ]
        for row in StockBalance.objects.using(using).values(
            "material_id", "location_id", "condition_id", "quantity"
        )
    }

    mismatches = []
    for identity in sorted(set(expected) | set(actual), key=lambda key: tuple(map(str, key))):
        expected_quantity = expected.get(identity, zero)
        actual_quantity = actual.get(identity, zero)
        if expected_quantity != actual_quantity:
            mismatches.append(
                QuantityProjectionMismatch(
                    material_id=identity[0],
                    location_id=identity[1],
                    condition_id=identity[2],
                    expected_quantity=expected_quantity,
                    actual_quantity=actual_quantity,
                )
            )
    return tuple(mismatches)


def verify_serialized_projection(
    *, using: str = "default"
) -> tuple[SerializedProjectionMismatch, ...]:
    """Compare each asset projection with its one authoritative RECEIVE line.

    Phase 5.3 derives only the initial IN_STOCK state. Later serialized movement
    slices extend this ledger reducer; this verifier never repairs persisted data.
    """
    lines_by_asset: dict[uuid.UUID, list[InventoryTransactionLine]] = {}
    lines = (
        InventoryTransactionLine.objects.using(using)
        .filter(serialized_asset__isnull=False)
        .select_related("transaction")
        .order_by("serialized_asset_id", "transaction__occurred_at", "line_number", "id")
    )
    for line in lines:
        lines_by_asset.setdefault(line.serialized_asset_id, []).append(line)

    mismatches = []
    assets = SerializedAsset.objects.using(using).order_by("id")
    for asset in assets:
        asset_lines = lines_by_asset.get(asset.pk, [])
        reasons: list[str] = []
        canonical_line = asset_lines[0] if len(asset_lines) == 1 else None
        if len(asset_lines) != 1:
            reasons.append(f"receipt_count={len(asset_lines)}")

        if canonical_line is not None:
            if canonical_line.transaction.transaction_type != (
                InventoryTransaction.TransactionType.RECEIPT
            ):
                reasons.append("transaction_type")
            if (
                canonical_line.source_location_id is not None
                or canonical_line.target_location_id is None
                or canonical_line.quantity is not None
                or canonical_line.unit_id is not None
            ):
                reasons.append("line_shape")
            if canonical_line.material_id != asset.material_id:
                reasons.append("material")
            if canonical_line.target_location_id != asset.current_location_id:
                reasons.append("current_location")
            if canonical_line.condition_id != asset.current_condition_id:
                reasons.append("current_condition")
            if asset.current_state != SerializedAsset.CurrentState.IN_STOCK:
                reasons.append("current_state")

        expected_material_id = (
            canonical_line.material_id if canonical_line is not None else None
        )
        expected_location_id = (
            canonical_line.target_location_id if canonical_line is not None else None
        )
        expected_condition_id = (
            canonical_line.condition_id if canonical_line is not None else None
        )
        expected_state = (
            SerializedAsset.CurrentState.IN_STOCK
            if canonical_line is not None
            else None
        )
        if reasons:
            mismatches.append(
                SerializedProjectionMismatch(
                    asset_id=asset.pk,
                    internal_asset_code=asset.internal_asset_code,
                    reasons=tuple(reasons),
                    expected_material_id=expected_material_id,
                    actual_material_id=asset.material_id,
                    expected_location_id=expected_location_id,
                    actual_location_id=asset.current_location_id,
                    expected_condition_id=expected_condition_id,
                    actual_condition_id=asset.current_condition_id,
                    expected_state=expected_state,
                    actual_state=asset.current_state,
                )
            )
    return tuple(mismatches)
