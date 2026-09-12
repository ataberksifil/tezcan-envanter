from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
)
from inventory.services.receipts import (
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    INVALID_DESTINATION,
    OPERATION_CONFLICT,
    OPERATION_ID_UNIQUE_CONSTRAINT,
    TRACKING_MODE_MISMATCH,
    InventoryMutationResult,
    _is_constraint_error,
    _locked_or_created_balance,
    _normalize_uuid,
    _raise_validation,
    normalize_quantity,
)
from locations.models import Location

RETURN_STOCK_PERMISSION = "inventory.return_stock"

INVALID_ORIGINAL_ISSUE = "inventory.invalid_original_issue"
RETURN_EXCEEDS_ISSUE_QUANTITY = "inventory.return_exceeds_issue_quantity"

RETURN_CAP_CONSTRAINT = "inventory_return_cumulative_quantity_cap"


def return_quantity(
    *,
    actor,
    operation_id,
    original_issue_line_id,
    target_location_id,
    quantity,
    using: str = "default",
) -> InventoryMutationResult:
    """Append one linked quantity RETURN and increment its target projection.

    Authorization precedes replay. For a new operation, lock order is the
    operation-id reservation, original ISSUE line, Material, target Location,
    MaterialCondition, and the exact target StockBalance row.
    """
    current_actor = _authorize_actor(actor, using)
    normalized_operation_id = _normalize_uuid(
        operation_id,
        field="operation_id",
        code="inventory.invalid_operation_id",
    )
    normalized_original_issue_line_id = _normalize_uuid(
        original_issue_line_id,
        field="original_issue_line_id",
        code=INVALID_ORIGINAL_ISSUE,
    )
    normalized_target_location_id = _normalize_uuid(
        target_location_id,
        field="target_location_id",
        code=INVALID_DESTINATION,
    )
    normalized_quantity = normalize_quantity(quantity)
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        original_issue_line_id=normalized_original_issue_line_id,
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
                    transaction_type=InventoryTransaction.TransactionType.RETURN,
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

        original_issue_line = _locked_original_issue_line(
            normalized_original_issue_line_id,
            using,
        )
        returned_quantity = (
            InventoryTransactionLine.objects.using(using)
            .filter(
                original_issue_line_id=original_issue_line.pk,
                transaction__transaction_type=(
                    InventoryTransaction.TransactionType.RETURN
                ),
            )
            .aggregate(total=Sum("quantity"))["total"]
            or Decimal("0.000")
        )
        if returned_quantity + normalized_quantity > original_issue_line.quantity:
            _raise_return_cap_error()

        material = _locked_material(original_issue_line.material_id, using)
        location = _locked_location(normalized_target_location_id, using)
        condition = _locked_condition(original_issue_line.condition_id, using)
        _validate_new_return_masters(
            material=material,
            location=location,
            condition=condition,
        )

        balance = _locked_or_created_balance(
            material_id=material.pk,
            location_id=location.pk,
            condition_id=condition.pk,
            using=using,
        )
        try:
            with transaction.atomic(using=using):
                line = InventoryTransactionLine.objects.using(using).create(
                    transaction=header,
                    line_number=1,
                    material=material,
                    quantity=normalized_quantity,
                    unit_id=original_issue_line.unit_id,
                    condition=condition,
                    source_location=None,
                    target_location=location,
                    original_issue_line=original_issue_line,
                )
        except IntegrityError as exc:
            if _is_constraint_error(exc, RETURN_CAP_CONSTRAINT):
                _raise_return_cap_error()
            raise

        balance.quantity = balance.quantity + normalized_quantity
        balance.save(using=using, update_fields=["quantity", "updated_at"])

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
        RETURN_STOCK_PERMISSION
    ):
        raise PermissionDenied
    return current_actor


def _request_fingerprint(
    *,
    acting_user_id,
    original_issue_line_id: uuid.UUID,
    target_location_id: uuid.UUID,
    quantity: Decimal,
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "original_issue_line_id": str(original_issue_line_id),
        "quantity": format(quantity, ".3f"),
        "target_location_id": str(target_location_id),
        "transaction_type": InventoryTransaction.TransactionType.RETURN,
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
        != InventoryTransaction.TransactionType.RETURN
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


def _locked_original_issue_line(
    original_issue_line_id: uuid.UUID,
    using: str,
) -> InventoryTransactionLine:
    try:
        original_issue_line = (
            InventoryTransactionLine.objects.using(using)
            .select_related("transaction")
            .select_for_update(of=("self",))
            .get(pk=original_issue_line_id)
        )
    except InventoryTransactionLine.DoesNotExist:
        _raise_validation(
            INVALID_ORIGINAL_ISSUE,
            "Orijinal stok çıkış satırı geçersiz.",
        )

    if (
        original_issue_line.transaction.transaction_type
        != InventoryTransaction.TransactionType.ISSUE
        or original_issue_line.source_location_id is None
        or original_issue_line.target_location_id is not None
        or original_issue_line.original_issue_line_id is not None
    ):
        _raise_validation(
            INVALID_ORIGINAL_ISSUE,
            "Orijinal stok çıkış satırı geçersiz.",
        )
    return original_issue_line


def _locked_material(material_id: uuid.UUID, using: str) -> Material:
    try:
        return Material.objects.using(using).select_for_update().get(pk=material_id)
    except Material.DoesNotExist:
        _raise_validation(INACTIVE_MATERIAL, "Malzeme bulunamadı veya aktif değil.")


def _locked_location(location_id: uuid.UUID, using: str) -> Location:
    try:
        return Location.objects.using(using).select_for_update().get(pk=location_id)
    except Location.DoesNotExist:
        _raise_validation(INVALID_DESTINATION, "Hedef konum geçersiz.")


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


def _validate_new_return_masters(
    *,
    material: Material,
    location: Location,
    condition: MaterialCondition,
) -> None:
    if not material.active:
        _raise_validation(INACTIVE_MATERIAL, "Pasif malzeme iade edilemez.")
    if material.tracking_mode != Material.TrackingMode.QUANTITY:
        _raise_validation(
            TRACKING_MODE_MISMATCH,
            "Bu servis yalnız miktar bazlı malzemeleri kabul eder.",
        )
    if not location.active or not location.can_hold_stock:
        _raise_validation(
            INVALID_DESTINATION,
            "Hedef konum aktif ve stok tutabilir olmalıdır.",
        )
    if not condition.active:
        _raise_validation(INACTIVE_CONDITION, "Pasif kondisyon kullanılamaz.")


def _raise_return_cap_error():
    _raise_validation(
        RETURN_EXCEEDS_ISSUE_QUANTITY,
        "İade miktarı orijinal stok çıkışında kalan iade edilebilir miktarı aşamaz.",
    )
