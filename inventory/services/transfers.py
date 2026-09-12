from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    StockBalance,
)
from inventory.services.receipts import (
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    INVALID_DESTINATION,
    OPERATION_CONFLICT,
    OPERATION_ID_UNIQUE_CONSTRAINT,
    TRACKING_MODE_MISMATCH,
    UNIT_MISMATCH,
    InventoryMutationResult,
    _is_constraint_error,
    _locked_or_created_balance,
    _normalize_uuid,
    _raise_validation,
    normalize_quantity,
)
from locations.models import Location

TRANSFER_STOCK_PERMISSION = "inventory.transfer_stock"

INVALID_SOURCE = "inventory.invalid_source"
INSUFFICIENT_STOCK = "inventory.insufficient_stock"
SAME_SOURCE_DESTINATION = "inventory.same_source_destination"


def transfer_quantity(
    *,
    actor,
    operation_id,
    material_id,
    unit_id,
    condition_id,
    source_location_id,
    target_location_id,
    quantity,
    using: str = "default",
) -> InventoryMutationResult:
    """Atomically append one quantity TRANSFER and move its exact bucket.

    Authorization precedes replay. For a new operation the lock order is the
    operation-id reservation, Material, both Locations in deterministic UUID
    order, MaterialCondition, and both StockBalance identities in the same
    location order. Source rows are never created; missing targets are created
    at quantity zero before increment.
    """
    current_actor = _authorize_actor(actor, using)
    normalized_operation_id = _normalize_uuid(
        operation_id,
        field="operation_id",
        code="inventory.invalid_operation_id",
    )
    normalized_material_id = _normalize_uuid(
        material_id,
        field="material_id",
        code=INACTIVE_MATERIAL,
    )
    normalized_unit_id = _normalize_uuid(
        unit_id,
        field="unit_id",
        code=UNIT_MISMATCH,
    )
    normalized_condition_id = _normalize_uuid(
        condition_id,
        field="condition_id",
        code=INACTIVE_CONDITION,
    )
    normalized_source_location_id = _normalize_uuid(
        source_location_id,
        field="source_location_id",
        code=INVALID_SOURCE,
    )
    normalized_target_location_id = _normalize_uuid(
        target_location_id,
        field="target_location_id",
        code=INVALID_DESTINATION,
    )
    normalized_quantity = normalize_quantity(quantity)
    if normalized_source_location_id == normalized_target_location_id:
        _raise_validation(
            SAME_SOURCE_DESTINATION,
            "Kaynak ve hedef konum aynı olamaz.",
        )
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        material_id=normalized_material_id,
        unit_id=normalized_unit_id,
        condition_id=normalized_condition_id,
        source_location_id=normalized_source_location_id,
        target_location_id=normalized_target_location_id,
        quantity=normalized_quantity,
    )

    with transaction.atomic(using=using):
        existing = (
            InventoryTransaction.objects.using(using)
            .filter(operation_id=normalized_operation_id)
            .first()
        )
        if existing is not None:
            return _replay_or_conflict(existing, fingerprint, using)

        try:
            with transaction.atomic(using=using):
                header = InventoryTransaction.objects.using(using).create(
                    operation_id=normalized_operation_id,
                    request_fingerprint=fingerprint,
                    transaction_type=InventoryTransaction.TransactionType.TRANSFER,
                    acting_user=current_actor,
                    occurred_at=timezone.now(),
                )
        except IntegrityError as exc:
            if not _is_constraint_error(exc, OPERATION_ID_UNIQUE_CONSTRAINT):
                raise
            winner = InventoryTransaction.objects.using(using).get(
                operation_id=normalized_operation_id
            )
            return _replay_or_conflict(winner, fingerprint, using)

        material = _locked_material(normalized_material_id, using)
        source_location, target_location = _locked_locations(
            normalized_source_location_id,
            normalized_target_location_id,
            using,
        )
        condition = _locked_condition(normalized_condition_id, using)
        _validate_new_transfer_masters(
            material=material,
            requested_unit_id=normalized_unit_id,
            condition=condition,
            source_location=source_location,
            target_location=target_location,
        )
        source_balance, target_balance = _locked_transfer_balances(
            material_id=material.pk,
            source_location_id=source_location.pk,
            target_location_id=target_location.pk,
            condition_id=condition.pk,
            using=using,
        )
        if normalized_quantity > source_balance.quantity:
            _raise_validation(
                INSUFFICIENT_STOCK,
                "Seçilen malzeme, konum ve kondisyon için yeterli stok yok.",
            )

        line = InventoryTransactionLine.objects.using(using).create(
            transaction=header,
            line_number=1,
            material=material,
            quantity=normalized_quantity,
            unit_id=material.unit_id,
            condition=condition,
            source_location=source_location,
            target_location=target_location,
            original_issue_line=None,
        )
        source_balance.quantity = source_balance.quantity - normalized_quantity
        source_balance.save(using=using, update_fields=["quantity", "updated_at"])
        target_balance.quantity = target_balance.quantity + normalized_quantity
        target_balance.save(using=using, update_fields=["quantity", "updated_at"])

        return InventoryMutationResult(
            transaction=header,
            lines=(line,),
            replayed=False,
        )


def _authorize_actor(actor, using: str):
    if actor is None or getattr(actor, "pk", None) is None:
        raise PermissionDenied
    if actor._state.db != using:
        raise PermissionDenied

    user_model = get_user_model()
    try:
        current_actor = user_model.objects.using(using).get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise PermissionDenied from exc
    if not current_actor.is_active or not current_actor.has_perm(
        TRANSFER_STOCK_PERMISSION
    ):
        raise PermissionDenied
    return current_actor


def _request_fingerprint(
    *,
    acting_user_id,
    material_id: uuid.UUID,
    unit_id: uuid.UUID,
    condition_id: uuid.UUID,
    source_location_id: uuid.UUID,
    target_location_id: uuid.UUID,
    quantity: Decimal,
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "condition_id": str(condition_id),
        "material_id": str(material_id),
        "quantity": format(quantity, ".3f"),
        "source_location_id": str(source_location_id),
        "target_location_id": str(target_location_id),
        "transaction_type": InventoryTransaction.TransactionType.TRANSFER,
        "unit_id": str(unit_id),
    }
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _replay_or_conflict(
    transaction_record: InventoryTransaction,
    fingerprint: str,
    using: str,
) -> InventoryMutationResult:
    if (
        transaction_record.transaction_type
        != InventoryTransaction.TransactionType.TRANSFER
        or transaction_record.request_fingerprint != fingerprint
    ):
        _raise_validation(
            OPERATION_CONFLICT,
            "operation_id farklı bir envanter isteği için zaten kullanılmış.",
        )
    lines = tuple(transaction_record.lines.using(using).order_by("line_number", "pk"))
    return InventoryMutationResult(
        transaction=transaction_record,
        lines=lines,
        replayed=True,
    )


def _locked_material(material_id: uuid.UUID, using: str) -> Material:
    try:
        return Material.objects.using(using).select_for_update().get(pk=material_id)
    except Material.DoesNotExist:
        _raise_validation(INACTIVE_MATERIAL, "Malzeme bulunamadı veya aktif değil.")


def _locked_locations(
    source_location_id: uuid.UUID,
    target_location_id: uuid.UUID,
    using: str,
) -> tuple[Location, Location]:
    locked = {}
    for location_id in sorted((source_location_id, target_location_id)):
        try:
            locked[location_id] = (
                Location.objects.using(using).select_for_update().get(pk=location_id)
            )
        except Location.DoesNotExist:
            if location_id == source_location_id:
                _raise_validation(INVALID_SOURCE, "Kaynak konum geçersiz.")
            _raise_validation(INVALID_DESTINATION, "Hedef konum geçersiz.")
    return locked[source_location_id], locked[target_location_id]


def _locked_condition(condition_id: uuid.UUID, using: str) -> MaterialCondition:
    try:
        return (
            MaterialCondition.objects.using(using)
            .select_for_update()
            .get(pk=condition_id)
        )
    except MaterialCondition.DoesNotExist:
        _raise_validation(
            INACTIVE_CONDITION,
            "Malzeme kondisyonu bulunamadı veya aktif değil.",
        )


def _validate_new_transfer_masters(
    *,
    material: Material,
    requested_unit_id: uuid.UUID,
    condition: MaterialCondition,
    source_location: Location,
    target_location: Location,
) -> None:
    if not material.active:
        _raise_validation(INACTIVE_MATERIAL, "Pasif malzeme transfer edilemez.")
    if material.tracking_mode != Material.TrackingMode.QUANTITY:
        _raise_validation(
            TRACKING_MODE_MISMATCH,
            "Bu servis yalnız miktar bazlı malzemeleri kabul eder.",
        )
    if material.unit_id is None or material.unit_id != requested_unit_id:
        _raise_validation(UNIT_MISMATCH, "İstenen birim malzemenin birimiyle eşleşmiyor.")
    if not condition.active:
        _raise_validation(INACTIVE_CONDITION, "Pasif kondisyon kullanılamaz.")
    if not source_location.active or not source_location.can_hold_stock:
        _raise_validation(
            INVALID_SOURCE,
            "Kaynak konum aktif ve stok tutabilir olmalıdır.",
        )
    if not target_location.active or not target_location.can_hold_stock:
        _raise_validation(
            INVALID_DESTINATION,
            "Hedef konum aktif ve stok tutabilir olmalıdır.",
        )
    if source_location.pk == target_location.pk:
        _raise_validation(
            SAME_SOURCE_DESTINATION,
            "Kaynak ve hedef konum aynı olamaz.",
        )


def _locked_transfer_balances(
    *,
    material_id: uuid.UUID,
    source_location_id: uuid.UUID,
    target_location_id: uuid.UUID,
    condition_id: uuid.UUID,
    using: str,
) -> tuple[StockBalance, StockBalance]:
    locked = {}
    identities = (
        ("source", source_location_id),
        ("target", target_location_id),
    )
    for role, location_id in sorted(identities, key=lambda item: item[1]):
        if role == "source":
            locked["source"] = _locked_existing_balance(
                material_id=material_id,
                location_id=location_id,
                condition_id=condition_id,
                using=using,
            )
        else:
            locked["target"] = _locked_or_created_balance(
                material_id=material_id,
                location_id=location_id,
                condition_id=condition_id,
                using=using,
            )
    return locked["source"], locked["target"]


def _locked_existing_balance(
    *,
    material_id: uuid.UUID,
    location_id: uuid.UUID,
    condition_id: uuid.UUID,
    using: str,
) -> StockBalance:
    try:
        return (
            StockBalance.objects.using(using)
            .select_for_update()
            .get(
                material_id=material_id,
                location_id=location_id,
                condition_id=condition_id,
            )
        )
    except StockBalance.DoesNotExist:
        _raise_validation(
            INSUFFICIENT_STOCK,
            "Seçilen malzeme, konum ve kondisyon için yeterli stok yok.",
        )
