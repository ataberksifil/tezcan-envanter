from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Max, Sum

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
                    "INITIAL_BALANCE",
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


def next_serialized_asset_event_seq(
    asset_id: uuid.UUID, *, using: str = "default"
) -> int:
    """Return the next causal ledger sequence for a locked SerializedAsset.

    Call this only while the caller already holds ``SELECT ... FOR UPDATE`` on
    the asset row. The value is server-assigned ledger metadata, not semantic
    command payload, and must not enter operation fingerprints.
    """
    current = (
        InventoryTransactionLine.objects.using(using)
        .filter(serialized_asset_id=asset_id)
        .aggregate(max_seq=Max("asset_event_seq"))["max_seq"]
    )
    return 1 if current is None else current + 1


def verify_serialized_projection(
    *, using: str = "default"
) -> tuple[SerializedProjectionMismatch, ...]:
    """Replay serialized ledger history and compare it with persisted projection.

    Genesis remains exactly one RECEIVE or INITIAL_BALANCE. Later ISSUE, linked
    unused RETURN, and in-stock TRANSFER events are reduced in
    ``asset_event_seq`` causal order. Wall-clock timestamps and line UUIDs are
    not causal. This verifier never repairs data.
    """
    lines_by_asset: dict[uuid.UUID, list[InventoryTransactionLine]] = {}
    lines = (
        InventoryTransactionLine.objects.using(using)
        .filter(serialized_asset__isnull=False)
        .select_related("transaction")
        .order_by("serialized_asset_id", "asset_event_seq")
    )
    for line in lines:
        lines_by_asset.setdefault(line.serialized_asset_id, []).append(line)

    mismatches = []
    assets = SerializedAsset.objects.using(using).order_by("id")
    for asset in assets:
        expected = _reduce_serialized_history(asset, lines_by_asset.get(asset.pk, []))
        if expected.reasons:
            mismatches.append(
                SerializedProjectionMismatch(
                    asset_id=asset.pk,
                    internal_asset_code=asset.internal_asset_code,
                    reasons=tuple(expected.reasons),
                    expected_material_id=expected.material_id,
                    actual_material_id=asset.material_id,
                    expected_location_id=expected.location_id,
                    actual_location_id=asset.current_location_id,
                    expected_condition_id=expected.condition_id,
                    actual_condition_id=asset.current_condition_id,
                    expected_state=expected.state,
                    actual_state=asset.current_state,
                )
            )
    return tuple(mismatches)


def assert_serialized_projection_for_asset(
    asset_id: uuid.UUID, *, using: str = "default"
) -> None:
    for mismatch in verify_serialized_projection(using=using):
        if mismatch.asset_id == asset_id:
            raise ValidationError(
                "Tekil varlık projection doğrulaması başarısız oldu.",
                code="inventory.projection_mismatch",
            )


@dataclass(frozen=True)
class _SerializedExpectation:
    material_id: uuid.UUID | None
    location_id: uuid.UUID | None
    condition_id: uuid.UUID | None
    state: str | None
    reasons: list[str]


def _reduce_serialized_history(
    asset: SerializedAsset, asset_lines: list[InventoryTransactionLine]
) -> _SerializedExpectation:
    reasons: list[str] = []
    if not asset_lines:
        return _SerializedExpectation(
            material_id=None,
            location_id=None,
            condition_id=None,
            state=None,
            reasons=[f"receipt_count={len(asset_lines)}"],
        )

    genesis = asset_lines[0]
    if genesis.transaction.transaction_type not in (
        InventoryTransaction.TransactionType.RECEIPT,
        InventoryTransaction.TransactionType.INITIAL_BALANCE,
    ) or not _is_serialized_genesis_shape(genesis):
        reasons.append("genesis")
        return _SerializedExpectation(
            material_id=genesis.material_id,
            location_id=genesis.target_location_id,
            condition_id=genesis.condition_id,
            state=None,
            reasons=reasons,
        )

    expected_material_id = genesis.material_id
    expected_location_id = genesis.target_location_id
    expected_condition_id = genesis.condition_id
    expected_state = SerializedAsset.CurrentState.IN_STOCK
    active_issue_line_id = None

    for line in asset_lines[1:]:
        transaction_type = line.transaction.transaction_type
        if not _is_serialized_line_shape(line):
            reasons.append("line_shape")
            break
        if line.material_id != expected_material_id:
            reasons.append("material")
            break
        if line.condition_id != expected_condition_id:
            reasons.append("current_condition")
            break
        if transaction_type in (
            InventoryTransaction.TransactionType.RECEIPT,
            InventoryTransaction.TransactionType.INITIAL_BALANCE,
        ):
            reasons.append("invalid_event_ordering")
            break
        if expected_state == SerializedAsset.CurrentState.IN_STOCK:
            if transaction_type == InventoryTransaction.TransactionType.TRANSFER:
                if (
                    line.source_location_id != expected_location_id
                    or line.target_location_id is None
                    or line.source_location_id == line.target_location_id
                    or line.original_issue_line_id is not None
                ):
                    reasons.append("invalid_event_ordering")
                    break
                expected_location_id = line.target_location_id
            elif transaction_type == InventoryTransaction.TransactionType.ISSUE:
                if (
                    line.source_location_id != expected_location_id
                    or line.target_location_id is not None
                    or line.original_issue_line_id is not None
                ):
                    reasons.append("invalid_event_ordering")
                    break
                expected_state = SerializedAsset.CurrentState.ISSUED
                expected_location_id = None
                active_issue_line_id = line.pk
            else:
                reasons.append("invalid_event_ordering")
                break
        elif expected_state == SerializedAsset.CurrentState.ISSUED:
            if transaction_type != InventoryTransaction.TransactionType.RETURN:
                reasons.append("invalid_event_ordering")
                break
            if (
                line.original_issue_line_id != active_issue_line_id
                or line.serialized_asset_id != genesis.serialized_asset_id
                or line.source_location_id is not None
                or line.target_location_id is None
            ):
                reasons.append("invalid_event_ordering")
                break
            expected_state = SerializedAsset.CurrentState.IN_STOCK
            expected_location_id = line.target_location_id
            active_issue_line_id = None
        else:
            reasons.append("invalid_event_ordering")
            break

    if expected_material_id != asset.material_id:
        reasons.append("material")
    if expected_location_id != asset.current_location_id:
        reasons.append("current_location")
    if expected_condition_id != asset.current_condition_id:
        reasons.append("current_condition")
    if expected_state != asset.current_state:
        reasons.append("current_state")

    return _SerializedExpectation(
        material_id=expected_material_id,
        location_id=expected_location_id,
        condition_id=expected_condition_id,
        state=expected_state,
        reasons=reasons,
    )


def _is_serialized_genesis_shape(line: InventoryTransactionLine) -> bool:
    return (
        line.serialized_asset_id is not None
        and line.quantity is None
        and line.unit_id is None
        and line.source_location_id is None
        and line.target_location_id is not None
        and line.original_issue_line_id is None
    )


def _is_serialized_line_shape(line: InventoryTransactionLine) -> bool:
    return (
        line.serialized_asset_id is not None
        and line.quantity is None
        and line.unit_id is None
    )
