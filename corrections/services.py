from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.services import record_audit_event
from catalog.models import Material
from corrections.models import CorrectionRequest
from inventory.models import InventoryTransaction, InventoryTransactionLine
from inventory.services.corrections import apply_controlled_correction

ADD_CORRECTION_PERMISSION = "corrections.add_correctionrequest"
DECIDE_CORRECTION_PERMISSION = "corrections.decide_correctionrequest"

INVALID_REQUEST = "corrections.invalid_request"
INVALID_ORIGINAL = "corrections.invalid_original"
PENDING_CONFLICT = "corrections.pending_conflict"
TERMINAL_REQUEST = "corrections.terminal_request"
SELF_DECISION = "corrections.self_decision"

CORRECTION_OPERATION_NAMESPACE = uuid.UUID("716c62fe-3f67-45d4-abe8-054ba5910d58")


@dataclass(frozen=True)
class CorrectionDecisionResult:
    correction_request: CorrectionRequest
    transaction: InventoryTransaction | None
    replayed: bool


def create_correction_request(
    *,
    actor,
    original_transaction_id,
    original_line_id,
    original_location_id,
    explanation,
    effect_type,
    quantity_effect,
    corrected_material_id=None,
    corrected_location_id=None,
    corrected_condition_id=None,
    using: str = "default",
) -> CorrectionRequest:
    current_actor = _authorize(actor, ADD_CORRECTION_PERMISSION, using)
    normalized_explanation = _normalize_explanation(explanation)

    with transaction.atomic(using=using):
        try:
            original_line = (
                InventoryTransactionLine.objects.using(using)
                .select_related("transaction", "material")
                .get(pk=original_line_id, transaction_id=original_transaction_id)
            )
        except (InventoryTransactionLine.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(
                "Özgün envanter satırı bulunamadı.", code=INVALID_ORIGINAL
            ) from exc
        if (
            original_line.transaction.transaction_type
            == InventoryTransaction.TransactionType.CONTROLLED_CORRECTION
            or original_line.corrected_line_id is not None
            or original_line.material.tracking_mode != Material.TrackingMode.QUANTITY
        ):
            raise ValidationError(
                "Yalnız canonical miktar envanter satırı düzeltilebilir.",
                code=INVALID_ORIGINAL,
            )
        try:
            normalized_location_id = uuid.UUID(str(original_location_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError(
                "Özgün bucket konumu geçersiz.", code=INVALID_ORIGINAL
            ) from exc
        if normalized_location_id not in {
            original_line.source_location_id,
            original_line.target_location_id,
        }:
            raise ValidationError(
                "Etkilenen konum özgün satırın source veya target konumu olmalıdır.",
                code=INVALID_ORIGINAL,
            )

        request_record = CorrectionRequest(
            original_transaction=original_line.transaction,
            original_line=original_line,
            original_location_id=normalized_location_id,
            requester=current_actor,
            explanation=normalized_explanation,
            effect_type=effect_type,
            quantity_effect=quantity_effect,
            corrected_material_id=corrected_material_id,
            corrected_location_id=corrected_location_id,
            corrected_condition_id=corrected_condition_id,
            status=CorrectionRequest.Status.PENDING,
        )
        request_record.full_clean(validate_constraints=False)
        try:
            with transaction.atomic(using=using):
                request_record.save(using=using, force_insert=True)
        except IntegrityError as exc:
            if _constraint_name(exc) == "correction_one_pending_per_tx":
                raise ValidationError(
                    "Bu işlem için zaten bekleyen bir düzeltme talebi var.",
                    code=PENDING_CONFLICT,
                ) from exc
            raise
        return request_record


def approve_correction_request(
    *, actor, correction_request_id, using: str = "default"
) -> CorrectionDecisionResult:
    current_actor = _authorize(actor, DECIDE_CORRECTION_PERMISSION, using)
    request_id = _normalize_request_id(correction_request_id)

    with transaction.atomic(using=using):
        request_record = _locked_request(request_id, using)
        if request_record.status == CorrectionRequest.Status.APPROVED:
            if request_record.decided_by_id == current_actor.pk:
                return CorrectionDecisionResult(
                    correction_request=request_record,
                    transaction=request_record.resulting_transaction,
                    replayed=True,
                )
            _raise_terminal()
        if request_record.status != CorrectionRequest.Status.PENDING:
            _raise_terminal()
        _reject_self_decision(request_record, current_actor)

        mutation = apply_controlled_correction(
            actor=current_actor,
            operation_id=correction_operation_id(request_record.pk),
            original_line_id=request_record.original_line_id,
            original_location_id=request_record.original_location_id,
            effect_type=request_record.effect_type,
            quantity_effect=request_record.quantity_effect,
            corrected_material_id=request_record.corrected_material_id,
            corrected_location_id=request_record.corrected_location_id,
            corrected_condition_id=request_record.corrected_condition_id,
            using=using,
        )
        decided_at = timezone.now()
        request_record.status = CorrectionRequest.Status.APPROVED
        request_record.decided_by = current_actor
        request_record.decided_at = decided_at
        request_record.resulting_transaction = mutation.transaction
        request_record.save(
            using=using,
            update_fields=[
                "status",
                "decided_by",
                "decided_at",
                "resulting_transaction",
                "updated_at",
            ],
        )
        record_audit_event(
            actor=current_actor,
            event_type="corrections.request.approved",
            entity_type="corrections.CorrectionRequest",
            entity_id=request_record.pk,
            before_data={"status": CorrectionRequest.Status.PENDING},
            after_data={
                "status": CorrectionRequest.Status.APPROVED,
                "decided_at": decided_at.isoformat(),
            },
            metadata=_decision_metadata(request_record, mutation.transaction),
            using=using,
        )
        return CorrectionDecisionResult(
            correction_request=request_record,
            transaction=mutation.transaction,
            replayed=mutation.replayed,
        )


def reject_correction_request(
    *,
    actor,
    correction_request_id,
    rejection_reason=None,
    using: str = "default",
) -> CorrectionDecisionResult:
    current_actor = _authorize(actor, DECIDE_CORRECTION_PERMISSION, using)
    request_id = _normalize_request_id(correction_request_id)
    reason = _normalize_rejection_reason(rejection_reason)

    with transaction.atomic(using=using):
        request_record = _locked_request(request_id, using)
        if request_record.status != CorrectionRequest.Status.PENDING:
            _raise_terminal()
        _reject_self_decision(request_record, current_actor)
        decided_at = timezone.now()
        request_record.status = CorrectionRequest.Status.REJECTED
        request_record.decided_by = current_actor
        request_record.decided_at = decided_at
        request_record.rejection_reason = reason
        request_record.save(
            using=using,
            update_fields=[
                "status",
                "decided_by",
                "decided_at",
                "rejection_reason",
                "updated_at",
            ],
        )
        record_audit_event(
            actor=current_actor,
            event_type="corrections.request.rejected",
            entity_type="corrections.CorrectionRequest",
            entity_id=request_record.pk,
            before_data={"status": CorrectionRequest.Status.PENDING},
            after_data={
                "status": CorrectionRequest.Status.REJECTED,
                "decided_at": decided_at.isoformat(),
            },
            metadata=_decision_metadata(request_record, None),
            using=using,
        )
        return CorrectionDecisionResult(
            correction_request=request_record,
            transaction=None,
            replayed=False,
        )


def correction_operation_id(request_id) -> uuid.UUID:
    return uuid.uuid5(CORRECTION_OPERATION_NAMESPACE, str(request_id))


def _authorize(actor, permission, using):
    if actor is None or getattr(actor, "pk", None) is None or actor._state.db != using:
        raise PermissionDenied
    user_model = get_user_model()
    try:
        current_actor = user_model.objects.using(using).get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise PermissionDenied from exc
    if not current_actor.is_active or not current_actor.has_perm(permission):
        raise PermissionDenied
    return current_actor


def _normalize_explanation(value):
    if not isinstance(value, str):
        raise ValidationError("Açıklama zorunludur.", code=INVALID_REQUEST)
    normalized = value.strip()
    if not 10 <= len(normalized) <= 2000:
        raise ValidationError(
            "Açıklama 10 ile 2000 karakter arasında olmalıdır.",
            code=INVALID_REQUEST,
        )
    return normalized


def _normalize_rejection_reason(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Ret gerekçesi geçersiz.", code=INVALID_REQUEST)
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 2000:
        raise ValidationError(
            "Ret gerekçesi en fazla 2000 karakter olabilir.", code=INVALID_REQUEST
        )
    return normalized


def _normalize_request_id(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Düzeltme talebi geçersiz.", code=INVALID_REQUEST) from exc


def _locked_request(request_id, using):
    try:
        return (
            CorrectionRequest.objects.using(using)
            .select_for_update()
            .get(pk=request_id)
        )
    except CorrectionRequest.DoesNotExist as exc:
        raise ValidationError(
            "Düzeltme talebi bulunamadı.", code=INVALID_REQUEST
        ) from exc


def _reject_self_decision(request_record, actor):
    if request_record.requester_id == actor.pk:
        raise ValidationError(
            "Talep eden kullanıcı kendi talebinde karar veremez.",
            code=SELF_DECISION,
        )


def _raise_terminal():
    raise ValidationError(
        "Düzeltme talebi artık beklemede değil.", code=TERMINAL_REQUEST
    )


def _decision_metadata(request_record, resulting_transaction):
    return {
        "correction_request_id": str(request_record.pk),
        "original_transaction_id": str(request_record.original_transaction_id),
        "original_line_id": str(request_record.original_line_id),
        "requester_id": request_record.requester_id,
        "decision_actor_id": request_record.decided_by_id,
        "status": request_record.status,
        "resulting_transaction_id": (
            str(resulting_transaction.pk) if resulting_transaction is not None else None
        ),
    }


def _constraint_name(exc):
    cause = exc.__cause__
    diag = getattr(cause, "diag", None) if cause is not None else None
    return getattr(diag, "constraint_name", None)
