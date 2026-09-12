from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    StockBalance,
)
from locations.models import Location

RECEIVE_STOCK_PERMISSION = "inventory.receive_stock"

INVALID_QUANTITY = "inventory.invalid_quantity"
OPERATION_CONFLICT = "inventory.operation_conflict"
INACTIVE_MATERIAL = "inventory.inactive_material"
TRACKING_MODE_MISMATCH = "inventory.tracking_mode_mismatch"
UNIT_MISMATCH = "inventory.unit_mismatch"
INACTIVE_CONDITION = "inventory.inactive_condition"
INVALID_DESTINATION = "inventory.invalid_destination"

OPERATION_ID_UNIQUE_CONSTRAINT = "inventory_tx_operation_id_uniq"
BALANCE_IDENTITY_UNIQUE_CONSTRAINT = "inventory_bal_identity_uniq"

QUANTITY_QUANTUM = Decimal("0.001")
MAX_QUANTITY = Decimal("999999999999999.999")


@dataclass(frozen=True)
class InventoryMutationResult:
    transaction: InventoryTransaction
    lines: tuple[InventoryTransactionLine, ...]
    replayed: bool


def receive_quantity(
    *,
    actor,
    operation_id,
    material_id,
    unit_id,
    condition_id,
    target_location_id,
    quantity,
    using: str = "default",
) -> InventoryMutationResult:
    """Atomically append one quantity RECEIPT and update its projection.

    Lock order for a new operation is the operation-id uniqueness reservation,
    then Material, Location, MaterialCondition, and StockBalance. All model row
    locks use PostgreSQL ``SELECT ... FOR UPDATE`` through Django.
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
    normalized_target_location_id = _normalize_uuid(
        target_location_id,
        field="target_location_id",
        code=INVALID_DESTINATION,
    )
    normalized_quantity = normalize_quantity(quantity)
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        material_id=normalized_material_id,
        unit_id=normalized_unit_id,
        condition_id=normalized_condition_id,
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
                    transaction_type=InventoryTransaction.TransactionType.RECEIPT,
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
        location = _locked_location(normalized_target_location_id, using)
        condition = _locked_condition(normalized_condition_id, using)
        _validate_new_receipt_masters(
            material=material,
            requested_unit_id=normalized_unit_id,
            condition=condition,
            location=location,
        )

        balance = _locked_or_created_balance(
            material_id=material.pk,
            location_id=location.pk,
            condition_id=condition.pk,
            using=using,
        )
        line = InventoryTransactionLine.objects.using(using).create(
            transaction=header,
            line_number=1,
            material=material,
            quantity=normalized_quantity,
            unit_id=material.unit_id,
            condition=condition,
            source_location=None,
            target_location=location,
        )
        balance.quantity = balance.quantity + normalized_quantity
        balance.save(using=using, update_fields=["quantity", "updated_at"])

        return InventoryMutationResult(
            transaction=header,
            lines=(line,),
            replayed=False,
        )


def normalize_quantity(value) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        _raise_validation(INVALID_QUANTITY, "Miktar tam bir ondalık değer olmalıdır.")
    if not isinstance(value, (Decimal, int, str)):
        _raise_validation(INVALID_QUANTITY, "Miktar tam bir ondalık değer olmalıdır.")
    if isinstance(value, str) and not value.strip():
        _raise_validation(INVALID_QUANTITY, "Miktar boş olamaz.")

    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        _raise_validation(INVALID_QUANTITY, "Miktar geçerli bir ondalık değer olmalıdır.")

    if not decimal_value.is_finite():
        _raise_validation(INVALID_QUANTITY, "Miktar sonlu olmalıdır.")
    if decimal_value <= 0:
        _raise_validation(INVALID_QUANTITY, "Miktar sıfırdan büyük olmalıdır.")

    try:
        with localcontext() as context:
            context.prec = max(28, len(decimal_value.as_tuple().digits) + 4)
            quantized = decimal_value.quantize(QUANTITY_QUANTUM)
    except InvalidOperation:
        _raise_validation(INVALID_QUANTITY, "Miktar NUMERIC(18,3) sınırını aşamaz.")

    if quantized != decimal_value:
        _raise_validation(INVALID_QUANTITY, "Miktar en fazla üç ondalık basamak içerebilir.")
    if quantized > MAX_QUANTITY:
        _raise_validation(INVALID_QUANTITY, "Miktar NUMERIC(18,3) sınırını aşamaz.")
    return quantized


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
        RECEIVE_STOCK_PERMISSION
    ):
        raise PermissionDenied
    return current_actor


def _normalize_uuid(value, *, field: str, code: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        _raise_validation(code, f"{field} geçerli bir UUID olmalıdır.")


def _request_fingerprint(
    *,
    acting_user_id,
    material_id: uuid.UUID,
    unit_id: uuid.UUID,
    condition_id: uuid.UUID,
    target_location_id: uuid.UUID,
    quantity: Decimal,
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "condition_id": str(condition_id),
        "material_id": str(material_id),
        "quantity": format(quantity, ".3f"),
        "target_location_id": str(target_location_id),
        "transaction_type": InventoryTransaction.TransactionType.RECEIPT,
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
    if transaction_record.request_fingerprint != fingerprint:
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
        _raise_validation(INACTIVE_CONDITION, "Malzeme kondisyonu bulunamadı veya aktif değil.")


def _validate_new_receipt_masters(
    *,
    material: Material,
    requested_unit_id: uuid.UUID,
    condition: MaterialCondition,
    location: Location,
) -> None:
    if not material.active:
        _raise_validation(INACTIVE_MATERIAL, "Pasif malzemeye stok girişi yapılamaz.")
    if material.tracking_mode != Material.TrackingMode.QUANTITY:
        _raise_validation(
            TRACKING_MODE_MISMATCH,
            "Bu servis yalnız miktar bazlı malzemeleri kabul eder.",
        )
    if material.unit_id is None or material.unit_id != requested_unit_id:
        _raise_validation(UNIT_MISMATCH, "İstenen birim malzemenin birimiyle eşleşmiyor.")
    if not condition.active:
        _raise_validation(INACTIVE_CONDITION, "Pasif kondisyon kullanılamaz.")
    if not location.active or not location.can_hold_stock:
        _raise_validation(
            INVALID_DESTINATION,
            "Hedef konum aktif ve stok tutabilir olmalıdır.",
        )


def _locked_or_created_balance(
    *,
    material_id: uuid.UUID,
    location_id: uuid.UUID,
    condition_id: uuid.UUID,
    using: str,
) -> StockBalance:
    identity = {
        "material_id": material_id,
        "location_id": location_id,
        "condition_id": condition_id,
    }
    balance = (
        StockBalance.objects.using(using)
        .select_for_update()
        .filter(**identity)
        .first()
    )
    if balance is None:
        try:
            with transaction.atomic(using=using):
                StockBalance.objects.using(using).create(
                    **identity,
                    quantity=Decimal("0.000"),
                )
        except IntegrityError as exc:
            if not _is_constraint_error(exc, BALANCE_IDENTITY_UNIQUE_CONSTRAINT):
                raise
        balance = (
            StockBalance.objects.using(using)
            .select_for_update()
            .get(**identity)
        )
    return balance


def _is_constraint_error(exc: IntegrityError, constraint_name: str) -> bool:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None) if cause is not None else None
    return getattr(diag, "constraint_name", None) == constraint_name


def _raise_validation(code: str, message: str):
    raise ValidationError(message, code=code)
