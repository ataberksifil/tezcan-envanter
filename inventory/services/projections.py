from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from inventory.models import InventoryTransactionLine, StockBalance


@dataclass(frozen=True)
class QuantityProjectionMismatch:
    material_id: uuid.UUID
    location_id: uuid.UUID
    condition_id: uuid.UUID
    expected_quantity: Decimal
    actual_quantity: Decimal


def verify_quantity_projection(
    *, using: str = "default"
) -> tuple[QuantityProjectionMismatch, ...]:
    """Compare the RECEIPT ledger with StockBalance without writing state."""
    expected = {
        (row["material_id"], row["target_location_id"], row["condition_id"]): row[
            "quantity"
        ]
        for row in (
            InventoryTransactionLine.objects.using(using)
            .filter(
                transaction__transaction_type="RECEIPT",
            )
            .values("material_id", "target_location_id", "condition_id")
            .annotate(quantity=Sum("quantity"))
        )
    }
    actual = {
        (row["material_id"], row["location_id"], row["condition_id"]): row[
            "quantity"
        ]
        for row in StockBalance.objects.using(using).values(
            "material_id", "location_id", "condition_id", "quantity"
        )
    }

    zero = Decimal("0.000")
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
