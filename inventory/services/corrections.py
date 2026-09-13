from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from inventory.models import InventoryTransaction, InventoryTransactionLine, StockBalance
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

DECIDE_CORRECTION_PERMISSION = "corrections.decide_correctionrequest"

INVALID_ORIGINAL_LINE = "inventory.invalid_original_correction_line"
INVALID_ORIGINAL_BUCKET = "inventory.invalid_original_correction_bucket"
INVALID_CORRECTION_EFFECT = "inventory.invalid_correction_effect"
CORRECTION_CUMULATIVE_FLOOR = "inventory.correction_cumulative_floor"
INSUFFICIENT_STOCK = "inventory.insufficient_stock"


@dataclass(frozen=True)
class _BucketEffect:
    material_id: uuid.UUID
    location_id: uuid.UUID
    condition_id: uuid.UUID
    quantity_delta: Decimal

    @property
    def identity(self):
        return (self.material_id, self.location_id, self.condition_id)


def apply_controlled_correction(
    *,
    actor,
    operation_id,
    original_line_id,
    original_location_id,
    effect_type,
    quantity_effect,
    corrected_material_id=None,
    corrected_location_id=None,
    corrected_condition_id=None,
    using: str = "default",
) -> InventoryMutationResult:
    """Append an idempotent quantity CONTROLLED_CORRECTION and update projection."""
    current_actor = _authorize_actor(actor, using)
    normalized_operation_id = _normalize_uuid(
        operation_id,
        field="operation_id",
        code="inventory.invalid_operation_id",
    )
    normalized_original_line_id = _normalize_uuid(
        original_line_id,
        field="original_line_id",
        code=INVALID_ORIGINAL_LINE,
    )
    normalized_original_location_id = _normalize_uuid(
        original_location_id,
        field="original_location_id",
        code=INVALID_ORIGINAL_BUCKET,
    )
    normalized_effect_type = _normalize_effect_type(effect_type)
    normalized_quantity_effect = _normalize_effect_quantity(
        normalized_effect_type, quantity_effect
    )

    corrected_ids = _normalize_corrected_identity(
        effect_type=normalized_effect_type,
        material_id=corrected_material_id,
        location_id=corrected_location_id,
        condition_id=corrected_condition_id,
    )
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        original_line_id=normalized_original_line_id,
        original_location_id=normalized_original_location_id,
        effect_type=normalized_effect_type,
        quantity_effect=normalized_quantity_effect,
        corrected_ids=corrected_ids,
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
                    transaction_type=(
                        InventoryTransaction.TransactionType.CONTROLLED_CORRECTION
                    ),
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

        original_line = _locked_original_line(normalized_original_line_id, using)
        _validate_original_bucket(original_line, normalized_original_location_id)
        _validate_cumulative_equivalent(
            original_line=original_line,
            effect_type=normalized_effect_type,
            quantity_effect=normalized_quantity_effect,
            using=using,
        )

        effects = _build_effects(
            original_line=original_line,
            original_location_id=normalized_original_location_id,
            effect_type=normalized_effect_type,
            quantity_effect=normalized_quantity_effect,
            corrected_ids=corrected_ids,
        )
        materials = _locked_materials(
            {effect.material_id for effect in effects}, using
        )
        locations = _locked_locations(
            {effect.location_id for effect in effects}, using
        )
        conditions = _locked_conditions(
            {effect.condition_id for effect in effects}, using
        )
        _validate_effect_masters(
            effects=effects,
            materials=materials,
            locations=locations,
            conditions=conditions,
        )
        balances = _locked_balances(effects, using)
        for effect in effects:
            balance = balances[effect.identity]
            if effect.quantity_delta < 0 and balance.quantity < -effect.quantity_delta:
                _raise_validation(
                    INSUFFICIENT_STOCK,
                    "Düzeltmenin gerektirdiği azalış için güncel stok yetersiz.",
                )

        lines = []
        for number, effect in enumerate(effects, start=1):
            material = materials[effect.material_id]
            line = InventoryTransactionLine.objects.using(using).create(
                transaction=header,
                line_number=number,
                material=material,
                quantity=abs(effect.quantity_delta),
                unit_id=material.unit_id,
                condition=conditions[effect.condition_id],
                source_location=(
                    locations[effect.location_id]
                    if effect.quantity_delta < 0
                    else None
                ),
                target_location=(
                    locations[effect.location_id]
                    if effect.quantity_delta > 0
                    else None
                ),
                original_issue_line=None,
                corrected_line=original_line,
            )
            lines.append(line)

        for effect in effects:
            balance = balances[effect.identity]
            balance.quantity = balance.quantity + effect.quantity_delta
            balance.save(using=using, update_fields=["quantity", "updated_at"])

        return InventoryMutationResult(
            transaction=header,
            lines=tuple(lines),
            replayed=False,
        )


def _authorize_actor(actor, using: str):
    if actor is None or getattr(actor, "pk", None) is None or actor._state.db != using:
        raise PermissionDenied
    user_model = get_user_model()
    try:
        current_actor = user_model.objects.using(using).get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise PermissionDenied from exc
    if not current_actor.is_active or not current_actor.has_perm(
        DECIDE_CORRECTION_PERMISSION
    ):
        raise PermissionDenied
    return current_actor


def _normalize_effect_type(value) -> str:
    if value not in ("QUANTITY", "IDENTITY"):
        _raise_validation(INVALID_CORRECTION_EFFECT, "Düzeltme etkisi geçersiz.")
    return value


def _normalize_effect_quantity(effect_type: str, value) -> Decimal:
    if effect_type == "IDENTITY":
        return normalize_quantity(value)
    if isinstance(value, str) and value.strip().startswith("-"):
        return -normalize_quantity(value.strip()[1:])
    if isinstance(value, Decimal) and value < 0:
        return -normalize_quantity(-value)
    if isinstance(value, int) and not isinstance(value, bool) and value < 0:
        return -normalize_quantity(-value)
    return normalize_quantity(value)


def _normalize_corrected_identity(
    *, effect_type, material_id, location_id, condition_id
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID] | None:
    values = (material_id, location_id, condition_id)
    if effect_type == "QUANTITY":
        if any(value is not None for value in values):
            _raise_validation(
                INVALID_CORRECTION_EFFECT,
                "Miktar düzeltmesi doğru kimlik alanları içeremez.",
            )
        return None
    if any(value is None for value in values):
        _raise_validation(
            INVALID_CORRECTION_EFFECT,
            "Kimlik düzeltmesi için doğru malzeme, konum ve kondisyon zorunludur.",
        )
    return (
        _normalize_uuid(material_id, field="corrected_material_id", code=INACTIVE_MATERIAL),
        _normalize_uuid(location_id, field="corrected_location_id", code=INVALID_DESTINATION),
        _normalize_uuid(condition_id, field="corrected_condition_id", code=INACTIVE_CONDITION),
    )


def _request_fingerprint(
    *,
    acting_user_id,
    original_line_id,
    original_location_id,
    effect_type,
    quantity_effect,
    corrected_ids,
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "effect_type": effect_type,
        "original_line_id": str(original_line_id),
        "original_location_id": str(original_location_id),
        "quantity_effect": format(quantity_effect, ".3f"),
        "transaction_type": (
            InventoryTransaction.TransactionType.CONTROLLED_CORRECTION
        ),
    }
    if corrected_ids is not None:
        payload.update(
            corrected_material_id=str(corrected_ids[0]),
            corrected_location_id=str(corrected_ids[1]),
            corrected_condition_id=str(corrected_ids[2]),
        )
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_or_conflict(existing, fingerprint, using):
    if (
        existing.transaction_type
        != InventoryTransaction.TransactionType.CONTROLLED_CORRECTION
        or existing.request_fingerprint != fingerprint
    ):
        _raise_validation(
            OPERATION_CONFLICT,
            "operation_id farklı bir envanter isteği için zaten kullanılmış.",
        )
    return InventoryMutationResult(
        transaction=existing,
        lines=tuple(existing.lines.using(using).order_by("line_number", "pk")),
        replayed=True,
    )


def _locked_original_line(line_id, using):
    try:
        line = (
            InventoryTransactionLine.objects.using(using)
            .select_for_update()
            .select_related("transaction", "material")
            .get(pk=line_id)
        )
    except InventoryTransactionLine.DoesNotExist:
        _raise_validation(INVALID_ORIGINAL_LINE, "Özgün envanter satırı bulunamadı.")
    if (
        line.transaction.transaction_type
        == InventoryTransaction.TransactionType.CONTROLLED_CORRECTION
        or line.corrected_line_id is not None
        or line.material.tracking_mode != Material.TrackingMode.QUANTITY
    ):
        _raise_validation(
            INVALID_ORIGINAL_LINE,
            "Yalnız canonical miktar envanter satırı düzeltilebilir.",
        )
    return line


def _validate_original_bucket(line, location_id):
    if location_id not in {line.source_location_id, line.target_location_id}:
        _raise_validation(
            INVALID_ORIGINAL_BUCKET,
            "Etkilenen konum özgün satırın source veya target konumu olmalıdır.",
        )


def _validate_cumulative_equivalent(
    *, original_line, effect_type, quantity_effect, using
):
    grouped = defaultdict(list)
    for line in (
        InventoryTransactionLine.objects.using(using)
        .filter(
            corrected_line_id=original_line.pk,
            transaction__transaction_type=(
                InventoryTransaction.TransactionType.CONTROLLED_CORRECTION
            ),
        )
        .order_by("transaction_id", "line_number")
    ):
        grouped[line.transaction_id].append(line)
    equivalent = original_line.quantity
    for lines in grouped.values():
        equivalent -= sum(
            (line.quantity for line in lines if line.source_location_id is not None),
            Decimal("0.000"),
        )
        if len(lines) == 1 and lines[0].target_location_id is not None:
            equivalent += lines[0].quantity
    proposed = (
        quantity_effect
        if effect_type == "QUANTITY"
        else -quantity_effect
    )
    if equivalent + proposed < 0:
        _raise_validation(
            CORRECTION_CUMULATIVE_FLOOR,
            "Kümülatif düzeltme özgün miktar eşdeğerini negatif yapamaz.",
        )


def _build_effects(
    *,
    original_line,
    original_location_id,
    effect_type,
    quantity_effect,
    corrected_ids,
):
    original = _BucketEffect(
        material_id=original_line.material_id,
        location_id=original_location_id,
        condition_id=original_line.condition_id,
        quantity_delta=quantity_effect if effect_type == "QUANTITY" else -quantity_effect,
    )
    if effect_type == "QUANTITY":
        return (original,)
    corrected = _BucketEffect(
        material_id=corrected_ids[0],
        location_id=corrected_ids[1],
        condition_id=corrected_ids[2],
        quantity_delta=quantity_effect,
    )
    if corrected.identity == original.identity:
        _raise_validation(
            INVALID_CORRECTION_EFFECT,
            "Kimlik düzeltmesi material, konum veya kondisyonu değiştirmelidir.",
        )
    return (original, corrected)


def _locked_materials(ids, using):
    result = {}
    for pk in sorted(ids):
        try:
            result[pk] = Material.objects.using(using).select_for_update().get(pk=pk)
        except Material.DoesNotExist:
            _raise_validation(INACTIVE_MATERIAL, "Malzeme bulunamadı.")
    return result


def _locked_locations(ids, using):
    result = {}
    for pk in sorted(ids):
        try:
            result[pk] = Location.objects.using(using).select_for_update().get(pk=pk)
        except Location.DoesNotExist:
            _raise_validation(INVALID_DESTINATION, "Konum bulunamadı.")
    return result


def _locked_conditions(ids, using):
    result = {}
    for pk in sorted(ids):
        try:
            result[pk] = (
                MaterialCondition.objects.using(using)
                .select_for_update()
                .get(pk=pk)
            )
        except MaterialCondition.DoesNotExist:
            _raise_validation(INACTIVE_CONDITION, "Kondisyon bulunamadı.")
    return result


def _validate_effect_masters(*, effects, materials, locations, conditions):
    for effect in effects:
        material = materials[effect.material_id]
        condition = conditions[effect.condition_id]
        location = locations[effect.location_id]
        if material.tracking_mode != Material.TrackingMode.QUANTITY:
            _raise_validation(
                TRACKING_MODE_MISMATCH,
                "Bu servis yalnız miktar bazlı malzemeleri kabul eder.",
            )
        if material.unit_id is None:
            _raise_validation(INACTIVE_MATERIAL, "Malzemenin birimi bulunmalıdır.")
        if effect.quantity_delta > 0:
            if not material.active:
                _raise_validation(INACTIVE_MATERIAL, "Pasif malzemeye stok eklenemez.")
            if not condition.active:
                _raise_validation(INACTIVE_CONDITION, "Pasif kondisyona stok eklenemez.")
            if not location.active or not location.can_hold_stock:
                _raise_validation(
                    INVALID_DESTINATION,
                    "Düzeltme hedefi aktif ve stok tutabilir olmalıdır.",
                )


def _locked_balances(effects, using):
    effect_by_identity = {effect.identity: effect for effect in effects}
    result = {}
    for identity in sorted(effect_by_identity):
        effect = effect_by_identity[identity]
        if effect.quantity_delta > 0:
            result[identity] = _locked_or_created_balance(
                material_id=identity[0],
                location_id=identity[1],
                condition_id=identity[2],
                using=using,
            )
        else:
            try:
                result[identity] = (
                    StockBalance.objects.using(using)
                    .select_for_update()
                    .get(
                        material_id=identity[0],
                        location_id=identity[1],
                        condition_id=identity[2],
                    )
                )
            except StockBalance.DoesNotExist:
                _raise_validation(
                    INSUFFICIENT_STOCK,
                    "Düzeltmenin gerektirdiği azalış için güncel stok yetersiz.",
                )
    return result
