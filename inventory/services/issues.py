from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Employee
from catalog.models import Material, MaterialCondition
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    StockBalance,
)
from inventory.services.receipts import (
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    OPERATION_CONFLICT,
    OPERATION_ID_UNIQUE_CONSTRAINT,
    TRACKING_MODE_MISMATCH,
    UNIT_MISMATCH,
    InventoryMutationResult,
    _is_constraint_error,
    _normalize_uuid,
    _raise_validation,
    normalize_quantity,
)
from locations.models import Location

ISSUE_STOCK_PERMISSION = "inventory.issue_stock"

INVALID_SOURCE = "inventory.invalid_source"
INACTIVE_EMPLOYEE = "inventory.inactive_employee"
INACTIVE_PRODUCTION_LINE = "inventory.inactive_production_line"
INVALID_USAGE_LOCATION = "inventory.invalid_usage_location"
INSUFFICIENT_STOCK = "inventory.insufficient_stock"


def issue_quantity(
    *,
    actor,
    operation_id,
    material_id,
    unit_id,
    condition_id,
    source_location_id,
    quantity,
    receiver_employee_id,
    production_line_id,
    usage_location_text,
    using: str = "default",
) -> InventoryMutationResult:
    """Atomically append one quantity ISSUE and decrement its exact bucket.

    Authorization is deliberately checked before idempotent replay. For a new
    operation the lock order is Material, source Location, MaterialCondition,
    Employee, ProductionLine, and the exact existing StockBalance row.
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
    normalized_receiver_employee_id = _normalize_uuid(
        receiver_employee_id,
        field="receiver_employee_id",
        code=INACTIVE_EMPLOYEE,
    )
    normalized_production_line_id = _normalize_uuid(
        production_line_id,
        field="production_line_id",
        code=INACTIVE_PRODUCTION_LINE,
    )
    normalized_quantity = normalize_quantity(quantity)
    normalized_usage_location = _normalize_usage_location(usage_location_text)
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        material_id=normalized_material_id,
        unit_id=normalized_unit_id,
        condition_id=normalized_condition_id,
        source_location_id=normalized_source_location_id,
        quantity=normalized_quantity,
        receiver_employee_id=normalized_receiver_employee_id,
        production_line_id=normalized_production_line_id,
        usage_location_text=normalized_usage_location,
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
                    transaction_type=InventoryTransaction.TransactionType.ISSUE,
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
        location = _locked_location(normalized_source_location_id, using)
        condition = _locked_condition(normalized_condition_id, using)
        employee = _locked_employee(normalized_receiver_employee_id, using)
        production_line = _locked_production_line(
            normalized_production_line_id, using
        )
        _validate_new_issue_masters(
            material=material,
            requested_unit_id=normalized_unit_id,
            location=location,
            condition=condition,
            employee=employee,
            production_line=production_line,
        )
        balance = _locked_balance(
            material_id=material.pk,
            location_id=location.pk,
            condition_id=condition.pk,
            using=using,
        )
        if normalized_quantity > balance.quantity:
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
            source_location=location,
            target_location=None,
        )
        issue_context = IssueContext.objects.using(using).create(
            transaction=header,
            receiver_employee=employee,
            receiver_first_name_snapshot=employee.first_name,
            receiver_last_name_snapshot=employee.last_name,
            receiver_employee_number_snapshot=employee.employee_number,
            production_line=production_line,
            production_line_code_snapshot=production_line.code,
            production_line_name_snapshot=production_line.name,
            usage_location_text=normalized_usage_location,
        )
        balance.quantity = balance.quantity - normalized_quantity
        balance.save(using=using, update_fields=["quantity", "updated_at"])

        return InventoryMutationResult(
            transaction=header,
            lines=(line,),
            replayed=False,
            issue_context=issue_context,
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
        ISSUE_STOCK_PERMISSION
    ):
        raise PermissionDenied
    return current_actor


def _normalize_usage_location(value) -> str:
    if not isinstance(value, str):
        _raise_validation(
            INVALID_USAGE_LOCATION,
            "Kullanım yeri metin olmalıdır.",
        )
    normalized = value.strip()
    if not normalized:
        _raise_validation(
            INVALID_USAGE_LOCATION,
            "Kullanım yeri boş olamaz.",
        )
    return normalized


def _request_fingerprint(
    *,
    acting_user_id,
    material_id: uuid.UUID,
    unit_id: uuid.UUID,
    condition_id: uuid.UUID,
    source_location_id: uuid.UUID,
    quantity: Decimal,
    receiver_employee_id: uuid.UUID,
    production_line_id: uuid.UUID,
    usage_location_text: str,
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "condition_id": str(condition_id),
        "material_id": str(material_id),
        "production_line_id": str(production_line_id),
        "quantity": format(quantity, ".3f"),
        "receiver_employee_id": str(receiver_employee_id),
        "source_location_id": str(source_location_id),
        "transaction_type": InventoryTransaction.TransactionType.ISSUE,
        "unit_id": str(unit_id),
        "usage_location_text": usage_location_text,
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
    issue_context = IssueContext.objects.using(using).get(
        transaction_id=transaction_record.pk
    )
    return InventoryMutationResult(
        transaction=transaction_record,
        lines=lines,
        replayed=True,
        issue_context=issue_context,
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
        _raise_validation(INVALID_SOURCE, "Kaynak konum geçersiz.")


def _locked_condition(condition_id: uuid.UUID, using: str) -> MaterialCondition:
    try:
        return (
            MaterialCondition.objects.using(using)
            .select_for_update()
            .get(pk=condition_id)
        )
    except MaterialCondition.DoesNotExist:
        _raise_validation(INACTIVE_CONDITION, "Malzeme kondisyonu bulunamadı veya aktif değil.")


def _locked_employee(employee_id: uuid.UUID, using: str) -> Employee:
    try:
        return Employee.objects.using(using).select_for_update().get(pk=employee_id)
    except Employee.DoesNotExist:
        _raise_validation(INACTIVE_EMPLOYEE, "Çalışan bulunamadı veya aktif değil.")


def _locked_production_line(
    production_line_id: uuid.UUID, using: str
) -> ProductionLine:
    try:
        return (
            ProductionLine.objects.using(using)
            .select_for_update()
            .get(pk=production_line_id)
        )
    except ProductionLine.DoesNotExist:
        _raise_validation(
            INACTIVE_PRODUCTION_LINE,
            "Üretim hattı bulunamadı veya aktif değil.",
        )


def _validate_new_issue_masters(
    *,
    material: Material,
    requested_unit_id: uuid.UUID,
    location: Location,
    condition: MaterialCondition,
    employee: Employee,
    production_line: ProductionLine,
) -> None:
    if not material.active:
        _raise_validation(INACTIVE_MATERIAL, "Pasif malzemeden stok çıkışı yapılamaz.")
    if material.tracking_mode != Material.TrackingMode.QUANTITY:
        _raise_validation(
            TRACKING_MODE_MISMATCH,
            "Bu servis yalnız miktar bazlı malzemeleri kabul eder.",
        )
    if material.unit_id is None or material.unit_id != requested_unit_id:
        _raise_validation(UNIT_MISMATCH, "İstenen birim malzemenin birimiyle eşleşmiyor.")
    if not location.active or not location.can_hold_stock:
        _raise_validation(
            INVALID_SOURCE,
            "Kaynak konum aktif ve stok tutabilir olmalıdır.",
        )
    if not condition.active:
        _raise_validation(INACTIVE_CONDITION, "Pasif kondisyon kullanılamaz.")
    if not employee.active:
        _raise_validation(INACTIVE_EMPLOYEE, "Pasif çalışan alıcı seçilemez.")
    if not production_line.active:
        _raise_validation(
            INACTIVE_PRODUCTION_LINE,
            "Pasif üretim hattı seçilemez.",
        )


def _locked_balance(
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
