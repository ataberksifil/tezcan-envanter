from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.services.receipts import (
    ASSET_CODE_UNIQUE_CONSTRAINT,
    ASSET_MATERIAL_SERIAL_UNIQUE_CONSTRAINT,
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    INTERNAL_ASSET_CODE_CONFLICT,
    INVALID_DESTINATION,
    OPERATION_CONFLICT,
    OPERATION_ID_UNIQUE_CONSTRAINT,
    SERIAL_NUMBER_CONFLICT,
    InventoryMutationResult,
    _is_constraint_error,
    _locked_condition,
    _locked_location,
    _locked_material,
    _locked_or_created_balance,
    _normalize_uuid,
    _raise_validation,
    _validate_new_receipt_masters,
    _validate_new_serialized_receipt_masters,
    normalize_internal_asset_code,
    normalize_quantity,
    normalize_serial_number,
)


ESTABLISH_BASELINE_PERMISSION = "imports.establish_baseline"
INVALID_INITIAL_BALANCE = "inventory.invalid_initial_balance"
PRIOR_LEDGER_HISTORY = "inventory.prior_ledger_history"
PROJECTION_INTEGRITY = "inventory.projection_integrity"

INITIAL_BALANCE_OPERATION_NAMESPACE = uuid.UUID("a3e1c4d2-8b17-4f6a-9c20-5d8e7f1a2b30")


@dataclass(frozen=True)
class QuantityOpening:
    material_id: uuid.UUID
    location_id: uuid.UUID
    condition_id: uuid.UUID
    quantity: Decimal


@dataclass(frozen=True)
class SerializedOpening:
    material_id: uuid.UUID
    internal_asset_code: str
    serial_number: str | None
    location_id: uuid.UUID
    condition_id: uuid.UUID


def derive_scoped_operation_id(establishment_operation_id, session_id) -> uuid.UUID:
    """Derive a deterministic child INITIAL_BALANCE operation_id for one scope."""
    establishment_uuid = _normalize_uuid(
        establishment_operation_id,
        field="operation_id",
        code="inventory.invalid_operation_id",
    )
    session_uuid = _normalize_uuid(
        session_id, field="session_id", code=INVALID_INITIAL_BALANCE
    )
    return uuid.uuid5(
        INITIAL_BALANCE_OPERATION_NAMESPACE,
        f"{establishment_uuid}:session:{session_uuid}",
    )


def bucket_has_ledger_history(
    *,
    material_id,
    location_id,
    condition_id,
    using: str = "default",
) -> bool:
    """True when any ledger line historically affected this quantity bucket."""
    return (
        InventoryTransactionLine.objects.using(using)
        .filter(material_id=material_id, condition_id=condition_id)
        .filter(Q(source_location_id=location_id) | Q(target_location_id=location_id))
        .exists()
    )


def establish_initial_balance(
    *,
    actor,
    operation_id,
    quantity_openings: Sequence[QuantityOpening] = (),
    serialized_openings: Sequence[SerializedOpening] = (),
    using: str = "default",
) -> InventoryMutationResult:
    """Create one scoped INITIAL_BALANCE transaction and its opening projections.

    Production caller is controlled baseline orchestration. Lock order after the
    operation-id reservation is Material -> Location -> MaterialCondition ->
    StockBalance, then SerializedAsset uniqueness inserts.
    """
    current_actor = _authorize_actor(actor, using)
    operation_uuid = _normalize_uuid(
        operation_id, field="operation_id", code="inventory.invalid_operation_id"
    )
    quantity_rows = _normalize_quantity_openings(quantity_openings)
    serialized_rows = _normalize_serialized_openings(serialized_openings)
    if not quantity_rows and not serialized_rows:
        _raise_validation(
            INVALID_INITIAL_BALANCE,
            "INITIAL_BALANCE işlemi en az bir açılış satırı gerektirir.",
        )
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        quantity_openings=quantity_rows,
        serialized_openings=serialized_rows,
    )

    with transaction.atomic(using=using):
        existing = (
            InventoryTransaction.objects.using(using)
            .filter(operation_id=operation_uuid)
            .first()
        )
        if existing is not None:
            return _replay_or_conflict(existing, fingerprint, using)

        try:
            with transaction.atomic(using=using):
                header = InventoryTransaction.objects.using(using).create(
                    operation_id=operation_uuid,
                    request_fingerprint=fingerprint,
                    transaction_type=InventoryTransaction.TransactionType.INITIAL_BALANCE,
                    acting_user=current_actor,
                    occurred_at=timezone.now(),
                )
        except IntegrityError as exc:
            if not _is_constraint_error(exc, OPERATION_ID_UNIQUE_CONSTRAINT):
                raise
            winner = InventoryTransaction.objects.using(using).get(
                operation_id=operation_uuid
            )
            return _replay_or_conflict(winner, fingerprint, using)

        material_ids = sorted(
            {
                *(row.material_id for row in quantity_rows),
                *(row.material_id for row in serialized_rows),
            },
            key=str,
        )
        location_ids = sorted(
            {
                *(row.location_id for row in quantity_rows),
                *(row.location_id for row in serialized_rows),
            },
            key=str,
        )
        condition_ids = sorted(
            {
                *(row.condition_id for row in quantity_rows),
                *(row.condition_id for row in serialized_rows),
            },
            key=str,
        )
        materials = {
            material_id: _locked_material(material_id, using)
            for material_id in material_ids
        }
        locations = {
            location_id: _locked_location(location_id, using)
            for location_id in location_ids
        }
        conditions = {
            condition_id: _locked_condition(condition_id, using)
            for condition_id in condition_ids
        }

        created_lines: list[InventoryTransactionLine] = []
        created_assets: list[SerializedAsset] = []
        line_number = 1
        for row in quantity_rows:
            material = materials[row.material_id]
            location = locations[row.location_id]
            condition = conditions[row.condition_id]
            _validate_new_receipt_masters(
                material=material,
                requested_unit_id=material.unit_id,
                condition=condition,
                location=location,
            )
            if bucket_has_ledger_history(
                material_id=material.pk,
                location_id=location.pk,
                condition_id=condition.pk,
                using=using,
            ):
                _raise_validation(
                    PRIOR_LEDGER_HISTORY,
                    "Ledger geçmişi olan kova açılış bakiyesi alamaz.",
                )
            balance = _locked_or_created_balance(
                material_id=material.pk,
                location_id=location.pk,
                condition_id=condition.pk,
                using=using,
            )
            if balance.quantity != Decimal("0.000"):
                _raise_validation(
                    PROJECTION_INTEGRITY,
                    "Ledger geçmişi olmayan pozitif bakiye açılış bakiyesi olamaz.",
                )
            line = InventoryTransactionLine.objects.using(using).create(
                transaction=header,
                line_number=line_number,
                material=material,
                serialized_asset=None,
                quantity=row.quantity,
                unit_id=material.unit_id,
                condition=condition,
                source_location=None,
                target_location=location,
                original_issue_line=None,
                corrected_line=None,
            )
            balance.quantity = row.quantity
            balance.save(using=using, update_fields=["quantity", "updated_at"])
            created_lines.append(line)
            line_number += 1

        for row in serialized_rows:
            material = materials[row.material_id]
            location = locations[row.location_id]
            condition = conditions[row.condition_id]
            _validate_new_serialized_receipt_masters(
                material=material,
                condition=condition,
                location=location,
            )
            try:
                with transaction.atomic(using=using):
                    asset = SerializedAsset.objects.using(using).create(
                        material=material,
                        internal_asset_code=row.internal_asset_code,
                        serial_number=row.serial_number,
                        current_location=location,
                        current_condition=condition,
                        current_state=SerializedAsset.CurrentState.IN_STOCK,
                    )
            except IntegrityError as exc:
                constraint_name = _constraint_name(exc)
                if constraint_name == ASSET_CODE_UNIQUE_CONSTRAINT:
                    _raise_validation(
                        INTERNAL_ASSET_CODE_CONFLICT,
                        "Dahili varlık kodu başka bir tekil varlıkta kullanılıyor.",
                    )
                if constraint_name == ASSET_MATERIAL_SERIAL_UNIQUE_CONSTRAINT:
                    _raise_validation(
                        SERIAL_NUMBER_CONFLICT,
                        "Üretici seri numarası bu malzeme için zaten kullanılıyor.",
                    )
                raise
            line = InventoryTransactionLine.objects.using(using).create(
                transaction=header,
                line_number=line_number,
                material=material,
                serialized_asset=asset,
                quantity=None,
                unit=None,
                condition=condition,
                source_location=None,
                target_location=location,
                original_issue_line=None,
                corrected_line=None,
            )
            created_lines.append(line)
            created_assets.append(asset)
            line_number += 1

        return InventoryMutationResult(
            transaction=header,
            lines=tuple(created_lines),
            replayed=False,
            serialized_asset=created_assets[0] if created_assets else None,
        )


def _authorize_actor(actor, using):
    if actor is None or getattr(actor, "pk", None) is None or actor._state.db != using:
        raise PermissionDenied
    user_model = get_user_model()
    try:
        current = user_model.objects.using(using).get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise PermissionDenied from exc
    if not current.is_active or not current.has_perm(ESTABLISH_BASELINE_PERMISSION):
        raise PermissionDenied
    return current


def _normalize_quantity_openings(openings: Sequence[QuantityOpening]) -> tuple[QuantityOpening, ...]:
    normalized: list[QuantityOpening] = []
    seen: set[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = set()
    for opening in openings:
        material_id = _normalize_uuid(
            opening.material_id, field="material_id", code=INACTIVE_MATERIAL
        )
        location_id = _normalize_uuid(
            opening.location_id, field="location_id", code=INVALID_DESTINATION
        )
        condition_id = _normalize_uuid(
            opening.condition_id, field="condition_id", code=INACTIVE_CONDITION
        )
        identity = (material_id, location_id, condition_id)
        if identity in seen:
            _raise_validation(
                INVALID_INITIAL_BALANCE,
                "Aynı miktar kovası bir INITIAL_BALANCE içinde yinelenemez.",
            )
        seen.add(identity)
        quantity = normalize_quantity(opening.quantity)
        normalized.append(
            QuantityOpening(
                material_id=material_id,
                location_id=location_id,
                condition_id=condition_id,
                quantity=quantity,
            )
        )
    normalized.sort(key=lambda row: (str(row.material_id), str(row.location_id), str(row.condition_id)))
    return tuple(normalized)


def _normalize_serialized_openings(
    openings: Sequence[SerializedOpening],
) -> tuple[SerializedOpening, ...]:
    normalized: list[SerializedOpening] = []
    seen_codes: set[str] = set()
    seen_serials: set[tuple[uuid.UUID, str]] = set()
    for opening in openings:
        material_id = _normalize_uuid(
            opening.material_id, field="material_id", code=INACTIVE_MATERIAL
        )
        location_id = _normalize_uuid(
            opening.location_id, field="location_id", code=INVALID_DESTINATION
        )
        condition_id = _normalize_uuid(
            opening.condition_id, field="condition_id", code=INACTIVE_CONDITION
        )
        code = normalize_internal_asset_code(opening.internal_asset_code)
        serial = normalize_serial_number(opening.serial_number)
        if code in seen_codes:
            _raise_validation(
                INVALID_INITIAL_BALANCE,
                "Aynı dahili varlık kodu bir INITIAL_BALANCE içinde yinelenemez.",
            )
        seen_codes.add(code)
        if serial is not None:
            serial_key = (material_id, serial)
            if serial_key in seen_serials:
                _raise_validation(
                    INVALID_INITIAL_BALANCE,
                    "Aynı üretici seri numarası bir INITIAL_BALANCE içinde yinelenemez.",
                )
            seen_serials.add(serial_key)
        normalized.append(
            SerializedOpening(
                material_id=material_id,
                internal_asset_code=code,
                serial_number=serial,
                location_id=location_id,
                condition_id=condition_id,
            )
        )
    normalized.sort(
        key=lambda row: (
            row.internal_asset_code,
            str(row.material_id),
            row.serial_number or "",
        )
    )
    return tuple(normalized)


def _request_fingerprint(
    *,
    acting_user_id,
    quantity_openings: tuple[QuantityOpening, ...],
    serialized_openings: tuple[SerializedOpening, ...],
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "quantity_openings": [
            {
                "condition_id": str(row.condition_id),
                "location_id": str(row.location_id),
                "material_id": str(row.material_id),
                "quantity": format(row.quantity, ".3f"),
            }
            for row in quantity_openings
        ],
        "serialized_openings": [
            {
                "condition_id": str(row.condition_id),
                "internal_asset_code": row.internal_asset_code,
                "location_id": str(row.location_id),
                "material_id": str(row.material_id),
                "serial_number": row.serial_number,
            }
            for row in serialized_openings
        ],
        "transaction_type": InventoryTransaction.TransactionType.INITIAL_BALANCE,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_or_conflict(existing, fingerprint, using) -> InventoryMutationResult:
    if (
        existing.transaction_type
        != InventoryTransaction.TransactionType.INITIAL_BALANCE
        or existing.request_fingerprint != fingerprint
    ):
        _raise_validation(
            OPERATION_CONFLICT,
            "operation_id farklı bir envanter isteği için zaten kullanılmış.",
        )
    lines = tuple(existing.lines.using(using).order_by("line_number", "pk"))
    serialized_asset = next(
        (line.serialized_asset for line in lines if line.serialized_asset_id),
        None,
    )
    return InventoryMutationResult(
        transaction=existing,
        lines=lines,
        replayed=True,
        serialized_asset=serialized_asset,
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None) if cause is not None else None
    return getattr(diag, "constraint_name", None)
