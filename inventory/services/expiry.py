from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import ExpiryInspection, ReceiptExpiry, SKT_APPROACHING_WINDOW_DAYS
from inventory.services.receipts import RECEIVE_STOCK_PERMISSION

INVALID_INSPECTION_NOTE = "inventory.invalid_inspection_note"
INVALID_INSPECTION_OUTCOME = "inventory.invalid_inspection_outcome"
EXPIRY_NOT_FOUND = "inventory.expiry_not_found"


def expiry_warning_queryset():
    today = timezone.localdate()
    horizon = today + timedelta(days=SKT_APPROACHING_WINDOW_DAYS)
    return (
        ReceiptExpiry.objects.filter(expires_on__lte=horizon)
        .select_related("transaction", "transaction__acting_user")
        .prefetch_related(
            "inspections__actor",
            "transaction__lines__material",
            "transaction__lines__target_location",
        )
        .order_by("expires_on", "transaction_id")
    )


def expiry_warning_label(expires_on) -> str | None:
    """Presentation label for an optional receipt SKT: expired, approaching or none."""
    if expires_on is None:
        return None
    today = timezone.localdate()
    if expires_on < today:
        return "Süresi geçmiş"
    if (expires_on - today).days <= SKT_APPROACHING_WINDOW_DAYS:
        return "Yaklaşıyor"
    return None


def record_physical_inspection(
    *,
    actor,
    receipt_id,
    note: str,
    outcome: str,
    using: str = "default",
):
    """Record a physical inspection. Does not write the ledger or projections."""
    current_actor = _authorize_actor(actor, using)
    normalized_note = _normalize_note(note)
    normalized_outcome = _normalize_outcome(outcome)
    with transaction.atomic(using=using):
        expiry = (
            ReceiptExpiry.objects.using(using)
            .select_for_update()
            .filter(pk=receipt_id)
            .first()
        )
        if expiry is None:
            raise ValidationError(
                "Bu giriş için SKT kaydı yok.",
                code=EXPIRY_NOT_FOUND,
            )
        return ExpiryInspection.objects.using(using).create(
            receipt_expiry=expiry,
            actor=current_actor,
            outcome=normalized_outcome,
            note=normalized_note,
            recorded_at=timezone.now(),
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
        RECEIVE_STOCK_PERMISSION
    ):
        raise PermissionDenied
    return current_actor


def _normalize_outcome(value) -> str:
    allowed = {choice.value for choice in ExpiryInspection.SELECTABLE_OUTCOMES}
    if value not in allowed:
        raise ValidationError(
            "Fiziksel kontrol sonucu geçersiz.",
            code=INVALID_INSPECTION_OUTCOME,
        )
    return value


def _normalize_note(value) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            "Fiziksel kontrol notu metin olmalıdır.",
            code=INVALID_INSPECTION_NOTE,
        )
    normalized = value.strip()
    if len(normalized) < 10 or len(normalized) > 2000:
        raise ValidationError(
            "Fiziksel kontrol notu 10 ile 2000 karakter arasında olmalıdır.",
            code=INVALID_INSPECTION_NOTE,
        )
    return normalized
