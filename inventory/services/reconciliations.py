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
from inventory.models import InventoryTransaction, InventoryTransactionLine, StockBalance
from inventory.services.receipts import (
    OPERATION_CONFLICT,
    OPERATION_ID_UNIQUE_CONSTRAINT,
    InventoryMutationResult,
    _is_constraint_error,
    _locked_or_created_balance,
    _normalize_uuid,
    _raise_validation,
    normalize_quantity,
)
from locations.models import Location


DECIDE_COUNT_PERMISSION = "counting.decide_discrepancy"
COUNT_DRIFT = "inventory.count_reconciliation_drift"
INVALID_COUNT_EFFECT = "inventory.invalid_count_reconciliation"
INSUFFICIENT_STOCK = "inventory.insufficient_stock"


def reconcile_quantity_count(
    *,
    actor,
    operation_id,
    count_session_id,
    count_line_id,
    material_id,
    location_id,
    condition_id,
    expected_quantity,
    counted_quantity,
    using: str = "default",
) -> InventoryMutationResult:
    """Append one idempotent COUNT_RECONCILIATION and update StockBalance.

    The counting application service owns the surrounding transaction and locks
    its session/line first. This inventory boundary then reserves operation_id and
    locks Material -> Location -> MaterialCondition -> StockBalance.
    """
    current_actor = _authorize_actor(actor, using)
    operation_uuid = _normalize_uuid(
        operation_id, field="operation_id", code="inventory.invalid_operation_id"
    )
    session_uuid = _normalize_uuid(
        count_session_id, field="count_session_id", code=INVALID_COUNT_EFFECT
    )
    line_uuid = _normalize_uuid(
        count_line_id, field="count_line_id", code=INVALID_COUNT_EFFECT
    )
    material_uuid = _normalize_uuid(
        material_id, field="material_id", code=INVALID_COUNT_EFFECT
    )
    location_uuid = _normalize_uuid(
        location_id, field="location_id", code=INVALID_COUNT_EFFECT
    )
    condition_uuid = _normalize_uuid(
        condition_id, field="condition_id", code=INVALID_COUNT_EFFECT
    )
    expected = _normalize_snapshot_quantity(expected_quantity)
    counted = _normalize_snapshot_quantity(counted_quantity)
    if counted == expected:
        _raise_validation(INVALID_COUNT_EFFECT, "Mutabakat için sıfır olmayan fark zorunludur.")

    direction = "INCREASE" if counted > expected else "DECREASE"
    effect = abs(counted - expected)
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        count_session_id=session_uuid,
        count_line_id=line_uuid,
        material_id=material_uuid,
        location_id=location_uuid,
        condition_id=condition_uuid,
        expected_quantity=expected,
        counted_quantity=counted,
        direction=direction,
        effect_quantity=effect,
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
                    transaction_type=InventoryTransaction.TransactionType.COUNT_RECONCILIATION,
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

        material = _locked_material(material_uuid, using)
        location = _locked_location(location_uuid, using)
        condition = _locked_condition(condition_uuid, using)
        _validate_masters(
            material=material,
            location=location,
            condition=condition,
            increasing=counted > expected,
        )

        if counted > expected or expected == 0:
            balance = _locked_or_created_balance(
                material_id=material.pk,
                location_id=location.pk,
                condition_id=condition.pk,
                using=using,
            )
            current = balance.quantity
        else:
            try:
                balance = (
                    StockBalance.objects.using(using)
                    .select_for_update()
                    .get(
                        material_id=material.pk,
                        location_id=location.pk,
                        condition_id=condition.pk,
                    )
                )
                current = balance.quantity
            except StockBalance.DoesNotExist:
                balance = None
                current = Decimal("0.000")

        if current != expected:
            _raise_validation(
                COUNT_DRIFT,
                "Güncel stok sayım başlangıç snapshot'ından farklı; yeniden sayım gerekir.",
            )
        if counted < expected and (balance is None or current < effect):
            _raise_validation(INSUFFICIENT_STOCK, "Mutabakat stoğu negatif yapamaz.")

        line = InventoryTransactionLine.objects.using(using).create(
            transaction=header,
            line_number=1,
            material=material,
            quantity=effect,
            unit_id=material.unit_id,
            condition=condition,
            source_location=location if counted < expected else None,
            target_location=location if counted > expected else None,
            original_issue_line=None,
            corrected_line=None,
            serialized_asset=None,
        )
        balance.quantity = counted
        balance.save(using=using, update_fields=["quantity", "updated_at"])
        return InventoryMutationResult(
            transaction=header,
            lines=(line,),
            replayed=False,
        )


def _authorize_actor(actor, using):
    if actor is None or getattr(actor, "pk", None) is None or actor._state.db != using:
        raise PermissionDenied
    user_model = get_user_model()
    try:
        current = user_model.objects.using(using).get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise PermissionDenied from exc
    if not current.is_active or not current.has_perm(DECIDE_COUNT_PERMISSION):
        raise PermissionDenied
    return current


def _normalize_snapshot_quantity(value):
    if value == 0 or value == Decimal("0") or value == "0" or value == "0.000":
        return Decimal("0.000")
    return normalize_quantity(value)


def _locked_material(pk, using):
    try:
        return Material.objects.using(using).select_for_update().get(pk=pk)
    except Material.DoesNotExist:
        _raise_validation(INVALID_COUNT_EFFECT, "Malzeme bulunamadı.")


def _locked_location(pk, using):
    try:
        return Location.objects.using(using).select_for_update().get(pk=pk)
    except Location.DoesNotExist:
        _raise_validation(INVALID_COUNT_EFFECT, "Lokasyon bulunamadı.")


def _locked_condition(pk, using):
    try:
        return MaterialCondition.objects.using(using).select_for_update().get(pk=pk)
    except MaterialCondition.DoesNotExist:
        _raise_validation(INVALID_COUNT_EFFECT, "Kondisyon bulunamadı.")


def _validate_masters(*, material, location, condition, increasing):
    if material.tracking_mode != Material.TrackingMode.QUANTITY or material.unit_id is None:
        _raise_validation(INVALID_COUNT_EFFECT, "Mutabakat yalnız QUANTITY malzeme içindir.")
    if increasing and not material.active:
        _raise_validation(INVALID_COUNT_EFFECT, "Pasif malzemeye stok eklenemez.")
    if increasing and (not location.active or not location.can_hold_stock):
        _raise_validation(INVALID_COUNT_EFFECT, "Hedef lokasyon aktif ve stok tutabilir olmalıdır.")
    if increasing and not condition.active:
        _raise_validation(INVALID_COUNT_EFFECT, "Pasif kondisyona stok eklenemez.")


def _request_fingerprint(**values):
    payload = {
        key: format(value, ".3f") if isinstance(value, Decimal) else str(value)
        for key, value in values.items()
    }
    payload["transaction_type"] = InventoryTransaction.TransactionType.COUNT_RECONCILIATION
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_or_conflict(existing, fingerprint, using):
    if (
        existing.transaction_type
        != InventoryTransaction.TransactionType.COUNT_RECONCILIATION
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
