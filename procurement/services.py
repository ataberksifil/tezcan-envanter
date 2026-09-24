from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.services import record_audit_event
from catalog.models import Material, UnitOfMeasure, turkish_casefold, turkish_fold_expr
from inventory.models import InventoryTransaction
from inventory.services.receipts import (
    ReceiptMetadataInput,
    normalize_quantity,
    receive_quantity,
    receive_serialized,
)
from procurement.models import (
    FulfillmentState,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseRequestReceipt,
)

VIEW_PERMISSION = "procurement.view_purchaserequest"
ADD_PERMISSION = "procurement.add_purchaserequest"
CHANGE_PERMISSION = "procurement.change_purchaserequest"

REQUEST_ENTITY = "procurement.purchaserequest"
LINE_ENTITY = "procurement.purchaserequestline"
REQUEST_CREATED = "procurement.purchaserequest.created"
REQUEST_UPDATED = "procurement.purchaserequest.updated"
LINE_CREATED = "procurement.purchaserequestline.created"
LINE_UPDATED = "procurement.purchaserequestline.updated"
LINE_MATERIAL_LINKED = "procurement.purchaserequestline.material_linked"

DUPLICATE_REQUEST_NO = "procurement.duplicate_request_no"
INVALID_REQUEST_NO = "procurement.invalid_request_no"
INVALID_DATES = "procurement.invalid_dates"
INVALID_QUANTITY = "procurement.invalid_quantity"
INVALID_PRICE = "procurement.invalid_price"
INVALID_CURRENCY = "procurement.invalid_currency"
UNIT_INCOMPATIBLE = "procurement.unit_incompatible"
MATERIAL_REQUIRED = "procurement.material_required"
MATERIAL_MISMATCH = "procurement.material_mismatch"
TRACKING_MISMATCH = "procurement.tracking_mismatch"
RECEIPT_ALREADY_LINKED = "procurement.receipt_already_linked"
UNLINKED_RECEIPT = "procurement.unlinked_receipt"
LINE_FROZEN = "procurement.line_frozen"
REQUEST_NO_UNIQUE = "proc_req_no_uniq"
RECEIPT_TX_UNIQUE = "proc_rcpt_tx_uniq"

PRICE_QUANTUM = Decimal("0.0001")
_WHITESPACE = re.compile(r"\s+")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class PurchaseRequestWriteResult:
    purchase_request: PurchaseRequest
    changed: bool


@dataclass(frozen=True)
class PurchaseRequestLineWriteResult:
    line: PurchaseRequestLine
    changed: bool


@dataclass(frozen=True)
class RequestReceiptResult:
    purchase_request: PurchaseRequest
    line: PurchaseRequestLine
    receipt: PurchaseRequestReceipt
    inventory_result: Any
    replayed: bool


def normalize_request_no(value) -> str:
    if not isinstance(value, str):
        _raise(INVALID_REQUEST_NO, "Talep no metin olmalıdır.")
    normalized = _WHITESPACE.sub(" ", value).strip()
    if not normalized:
        _raise(INVALID_REQUEST_NO, "Talep no boş olamaz.")
    if len(normalized) > 64:
        _raise(INVALID_REQUEST_NO, "Talep no en fazla 64 karakter olabilir.")
    return normalized


def normalize_description(value) -> str:
    if not isinstance(value, str):
        _raise("procurement.invalid_description", "Talep edilen kalem metin olmalıdır.")
    normalized = _WHITESPACE.sub(" ", value).strip()
    if not normalized:
        _raise("procurement.invalid_description", "Talep edilen kalem boş olamaz.")
    if len(normalized) > 500:
        _raise(
            "procurement.invalid_description",
            "Talep edilen kalem en fazla 500 karakter olabilir.",
        )
    return normalized


def normalize_optional_text(value, *, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _raise(f"procurement.invalid_{field}", "Metin alanı geçersiz.")
    normalized = _WHITESPACE.sub(" ", value).strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        _raise(
            f"procurement.invalid_{field}",
            f"Metin en fazla {max_length} karakter olabilir.",
        )
    return normalized


def normalize_unit_price(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or isinstance(value, float):
        _raise(INVALID_PRICE, "Birim fiyat ondalık olmalıdır; kayan nokta kullanılamaz.")
    if not isinstance(value, (Decimal, int, str)):
        _raise(INVALID_PRICE, "Birim fiyat ondalık olmalıdır.")
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        _raise(INVALID_PRICE, "Birim fiyat geçerli bir ondalık değer olmalıdır.")
    if not decimal_value.is_finite():
        _raise(INVALID_PRICE, "Birim fiyat sonlu olmalıdır.")
    if decimal_value < 0:
        _raise(INVALID_PRICE, "Birim fiyat negatif olamaz.")
    try:
        with localcontext() as context:
            context.prec = max(28, len(decimal_value.as_tuple().digits) + 4)
            quantized = decimal_value.quantize(PRICE_QUANTUM)
    except InvalidOperation:
        _raise(INVALID_PRICE, "Birim fiyat sınırını aşamaz.")
    if quantized != decimal_value:
        _raise(INVALID_PRICE, "Birim fiyat en fazla dört ondalık basamak içerebilir.")
    return quantized


def normalize_currency_code(value, *, price: Decimal | None) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if price is not None:
            _raise(INVALID_CURRENCY, "Birim fiyat girildiyse para birimi zorunludur.")
        return None
    if not isinstance(value, str):
        _raise(INVALID_CURRENCY, "Para birimi üç harfli kod olmalıdır.")
    code = value.strip().upper()
    if not _CURRENCY.fullmatch(code):
        _raise(INVALID_CURRENCY, "Para birimi üç büyük harften oluşmalıdır.")
    if price is None:
        _raise(INVALID_CURRENCY, "Para birimi yalnız birim fiyat ile birlikte girilir.")
    return code


def line_fulfillment_state(requested: Decimal, received: Decimal) -> str:
    if received <= 0:
        return FulfillmentState.OPEN
    if received < requested:
        return FulfillmentState.PARTIAL
    return FulfillmentState.RECEIVED


def header_fulfillment_state(line_states: list[str]) -> str:
    if not line_states or all(state == FulfillmentState.OPEN for state in line_states):
        return FulfillmentState.OPEN
    if all(state == FulfillmentState.RECEIVED for state in line_states):
        return FulfillmentState.RECEIVED
    return FulfillmentState.PARTIAL


def annotate_requests(queryset):
    return queryset.annotate(line_count=Count("lines", distinct=True))


def search_purchase_requests(queryset, query: str):
    normalized = (query or "").strip()
    if not normalized:
        return queryset
    queryset = queryset.annotate(
        _request_no_fold=turkish_fold_expr("request_no"),
        _description_fold=turkish_fold_expr("lines__requested_description"),
        _supplier_fold=turkish_fold_expr("lines__supplier_name"),
        _material_code_fold=turkish_fold_expr("lines__material__material_code"),
        _material_name_fold=turkish_fold_expr("lines__material__name"),
    )
    for token in normalized.split():
        folded = turkish_casefold(token)
        queryset = queryset.filter(
            Q(_request_no_fold__contains=folded)
            | Q(_description_fold__contains=folded)
            | Q(_supplier_fold__contains=folded)
            | Q(_material_code_fold__contains=folded)
            | Q(_material_name_fold__contains=folded)
        )
    return queryset.distinct()


def request_is_late(purchase_request: PurchaseRequest, *, today: date | None = None) -> bool:
    current = today or timezone.localdate()
    for line in purchase_request.lines.all():
        if line.expected_arrival_date is None:
            continue
        if line.expected_arrival_date >= current:
            continue
        if line.fulfillment_state() != FulfillmentState.RECEIVED:
            return True
    return False


def arrival_bounds(line: PurchaseRequestLine):
    from inventory.models import InventoryTransaction

    transaction_ids = list(line.receipts.values_list("inventory_transaction_id", flat=True))
    if not transaction_ids:
        return None, None
    times = list(
        InventoryTransaction.objects.filter(pk__in=transaction_ids).values_list(
            "occurred_at", flat=True
        )
    )
    if not times:
        return None, None
    return min(times), max(times)


def create_purchase_request(
    *,
    actor,
    request_no,
    request_date,
    approval_date=None,
    note="",
    using: str = "default",
) -> PurchaseRequestWriteResult:
    _authorize(actor, ADD_PERMISSION)
    normalized_no = normalize_request_no(request_no)
    normalized_note = normalize_optional_text(note or "", field="note", max_length=2000) or ""
    _validate_dates(request_date, approval_date)
    with transaction.atomic(using=using):
        try:
            purchase_request = PurchaseRequest.objects.using(using).create(
                request_no=normalized_no,
                request_date=request_date,
                approval_date=approval_date,
                note=normalized_note,
                created_by=actor,
            )
        except IntegrityError as exc:
            if _is_constraint(exc, REQUEST_NO_UNIQUE):
                _raise(DUPLICATE_REQUEST_NO, "Bu talep no zaten kayıtlı.")
            raise
        record_audit_event(
            actor=actor,
            event_type=REQUEST_CREATED,
            entity_type=REQUEST_ENTITY,
            entity_id=purchase_request.pk,
            before_data=None,
            after_data=_request_snapshot(purchase_request),
            using=using,
        )
    return PurchaseRequestWriteResult(purchase_request=purchase_request, changed=True)


def update_purchase_request(
    *,
    actor,
    purchase_request_id,
    request_no,
    request_date,
    approval_date=None,
    note="",
    using: str = "default",
) -> PurchaseRequestWriteResult:
    _authorize(actor, CHANGE_PERMISSION)
    normalized_no = normalize_request_no(request_no)
    normalized_note = normalize_optional_text(note or "", field="note", max_length=2000) or ""
    _validate_dates(request_date, approval_date)
    with transaction.atomic(using=using):
        purchase_request = _locked_request(purchase_request_id, using)
        before = _request_snapshot(purchase_request)
        purchase_request.request_no = normalized_no
        purchase_request.request_date = request_date
        purchase_request.approval_date = approval_date
        purchase_request.note = normalized_note
        after = _request_snapshot(purchase_request)
        if before == after:
            return PurchaseRequestWriteResult(purchase_request=purchase_request, changed=False)
        try:
            purchase_request.save(
                using=using,
                update_fields=["request_no", "request_date", "approval_date", "note", "updated_at"],
            )
        except IntegrityError as exc:
            if _is_constraint(exc, REQUEST_NO_UNIQUE):
                _raise(DUPLICATE_REQUEST_NO, "Bu talep no zaten kayıtlı.")
            raise
        record_audit_event(
            actor=actor,
            event_type=REQUEST_UPDATED,
            entity_type=REQUEST_ENTITY,
            entity_id=purchase_request.pk,
            before_data=before,
            after_data=after,
            using=using,
        )
    return PurchaseRequestWriteResult(purchase_request=purchase_request, changed=True)


def add_purchase_request_line(
    *,
    actor,
    purchase_request_id,
    requested_description,
    unit_id,
    requested_quantity,
    material_id=None,
    lead_time_days=None,
    expected_arrival_date=None,
    supplier_name=None,
    unit_price=None,
    currency_code=None,
    using: str = "default",
) -> PurchaseRequestLineWriteResult:
    _authorize(actor, CHANGE_PERMISSION)
    description = normalize_description(requested_description)
    quantity = _requested_quantity(requested_quantity)
    price = normalize_unit_price(unit_price)
    currency = normalize_currency_code(currency_code, price=price)
    supplier = normalize_optional_text(supplier_name, field="supplier", max_length=255)
    lead = _lead_time(lead_time_days)
    with transaction.atomic(using=using):
        purchase_request = _locked_request(purchase_request_id, using)
        _validate_expected_date(purchase_request.request_date, expected_arrival_date)
        material = _optional_material(material_id, using)
        unit = _required_unit(unit_id, using)
        _validate_material_unit(material, unit, quantity)
        line = PurchaseRequestLine.objects.using(using).create(
            purchase_request=purchase_request,
            requested_description=description,
            material=material,
            unit=unit,
            unit_code_snapshot=unit.code,
            unit_name_snapshot=unit.name,
            requested_quantity=quantity,
            lead_time_days=lead,
            expected_arrival_date=expected_arrival_date,
            supplier_name=supplier,
            unit_price=price,
            currency_code=currency,
        )
        record_audit_event(
            actor=actor,
            event_type=LINE_CREATED,
            entity_type=LINE_ENTITY,
            entity_id=line.pk,
            before_data=None,
            after_data=_line_snapshot(line),
            using=using,
        )
    return PurchaseRequestLineWriteResult(line=line, changed=True)


def update_purchase_request_line(
    *,
    actor,
    line_id,
    requested_description,
    unit_id,
    requested_quantity,
    material_id=None,
    lead_time_days=None,
    expected_arrival_date=None,
    supplier_name=None,
    unit_price=None,
    currency_code=None,
    using: str = "default",
) -> PurchaseRequestLineWriteResult:
    _authorize(actor, CHANGE_PERMISSION)
    description = normalize_description(requested_description)
    quantity = _requested_quantity(requested_quantity)
    price = normalize_unit_price(unit_price)
    currency = normalize_currency_code(currency_code, price=price)
    supplier = normalize_optional_text(supplier_name, field="supplier", max_length=255)
    lead = _lead_time(lead_time_days)
    with transaction.atomic(using=using):
        line = _locked_line(line_id, using)
        before = _line_snapshot(line)
        fulfilled = line.receipts.exists()
        if fulfilled and (
            description != line.requested_description
            or str(unit_id) != str(line.unit_id)
            or _material_id(material_id) != _material_id(line.material_id)
        ):
            _raise(
                LINE_FROZEN,
                "Karşılanmış kalemin açıklaması, birimi ve malzeme bağlantısı değiştirilemez.",
            )
        material = line.material if fulfilled else _optional_material(material_id, using)
        unit = line.unit if fulfilled else _required_unit(unit_id, using)
        if not fulfilled:
            _validate_material_unit(material, unit, quantity)
        _validate_expected_date(line.purchase_request.request_date, expected_arrival_date)
        if fulfilled and quantity != line.requested_quantity:
            _validate_material_unit(line.material, line.unit, quantity)
        line.requested_description = description
        line.requested_quantity = quantity
        line.lead_time_days = lead
        line.expected_arrival_date = expected_arrival_date
        line.supplier_name = supplier
        line.unit_price = price
        line.currency_code = currency
        if not fulfilled:
            line.material = material
            line.unit = unit
            line.unit_code_snapshot = unit.code
            line.unit_name_snapshot = unit.name
        after = _line_snapshot(line)
        if before == after:
            return PurchaseRequestLineWriteResult(line=line, changed=False)
        line.save(using=using)
        record_audit_event(
            actor=actor,
            event_type=LINE_UPDATED,
            entity_type=LINE_ENTITY,
            entity_id=line.pk,
            before_data=before,
            after_data=after,
            using=using,
        )
    return PurchaseRequestLineWriteResult(line=line, changed=True)


def link_line_material(
    *,
    actor,
    line_id,
    material_id,
    using: str = "default",
) -> PurchaseRequestLineWriteResult:
    _authorize(actor, CHANGE_PERMISSION)
    with transaction.atomic(using=using):
        line = _locked_line(line_id, using)
        material = _optional_material(material_id, using)
        if material is None:
            _raise(MATERIAL_REQUIRED, "Bağlanacak malzeme seçilmelidir.")
        if line.receipts.exists() and line.material_id != material.pk:
            _raise(
                LINE_FROZEN,
                "Karşılanmış kalemin malzeme bağlantısı değiştirilemez.",
            )
        if line.material_id == material.pk:
            return PurchaseRequestLineWriteResult(line=line, changed=False)
        before = _line_snapshot(line)
        description = line.requested_description
        _validate_material_unit(material, line.unit, line.requested_quantity)
        line.material = material
        line.requested_description = description
        line.save(using=using, update_fields=["material", "updated_at"])
        line.refresh_from_db()
        if line.requested_description != description:
            _raise(LINE_FROZEN, "Talep açıklaması malzeme bağlantısında değişemez.")
        record_audit_event(
            actor=actor,
            event_type=LINE_MATERIAL_LINKED,
            entity_type=LINE_ENTITY,
            entity_id=line.pk,
            before_data=before,
            after_data=_line_snapshot(line),
            using=using,
        )
    return PurchaseRequestLineWriteResult(line=line, changed=True)


def receive_quantity_for_line(
    *,
    actor,
    line_id,
    operation_id,
    condition_id,
    target_location_id,
    quantity,
    receipt_metadata: ReceiptMetadataInput | None = None,
    expires_on: date | None = None,
    using: str = "default",
) -> RequestReceiptResult:
    _authorize(actor, CHANGE_PERMISSION)
    normalized_quantity = normalize_quantity(quantity)
    with transaction.atomic(using=using):
        line = _locked_line(line_id, using)
        _require_quantity_line(line)
        result = receive_quantity(
            actor=actor,
            operation_id=operation_id,
            material_id=line.material_id,
            unit_id=line.material.unit_id,
            condition_id=condition_id,
            target_location_id=target_location_id,
            quantity=normalized_quantity,
            receipt_metadata=receipt_metadata,
            expires_on=expires_on,
            using=using,
        )
        receipt, replayed = _attach_receipt(
            actor=actor,
            line=line,
            inventory_result=result,
            fulfilled_quantity=normalized_quantity,
            using=using,
        )
    return RequestReceiptResult(
        purchase_request=line.purchase_request,
        line=line,
        receipt=receipt,
        inventory_result=result,
        replayed=replayed,
    )


def receive_serialized_for_line(
    *,
    actor,
    line_id,
    operation_id,
    internal_asset_code,
    serial_number,
    condition_id,
    target_location_id,
    receipt_metadata: ReceiptMetadataInput | None = None,
    expires_on: date | None = None,
    using: str = "default",
) -> RequestReceiptResult:
    _authorize(actor, CHANGE_PERMISSION)
    with transaction.atomic(using=using):
        line = _locked_line(line_id, using)
        _require_serialized_line(line)
        result = receive_serialized(
            actor=actor,
            operation_id=operation_id,
            material_id=line.material_id,
            internal_asset_code=internal_asset_code,
            serial_number=serial_number,
            condition_id=condition_id,
            target_location_id=target_location_id,
            receipt_metadata=receipt_metadata,
            expires_on=expires_on,
            using=using,
        )
        receipt, replayed = _attach_receipt(
            actor=actor,
            line=line,
            inventory_result=result,
            fulfilled_quantity=Decimal("1.000"),
            using=using,
        )
    return RequestReceiptResult(
        purchase_request=line.purchase_request,
        line=line,
        receipt=receipt,
        inventory_result=result,
        replayed=replayed,
    )


def _attach_receipt(*, actor, line, inventory_result, fulfilled_quantity, using: str):
    transaction_record = inventory_result.transaction
    if transaction_record.transaction_type != InventoryTransaction.TransactionType.RECEIPT:
        _raise(TRACKING_MISMATCH, "Yalnız stok girişi talep kalemine bağlanabilir.")
    existing = (
        PurchaseRequestReceipt.objects.using(using)
        .filter(inventory_transaction_id=transaction_record.pk)
        .first()
    )
    if existing is not None:
        if existing.line_id != line.pk or existing.fulfilled_quantity != fulfilled_quantity:
            _raise(
                RECEIPT_ALREADY_LINKED,
                "Bu stok girişi başka bir talep karşılığında kayıtlı.",
            )
        return existing, True
    if inventory_result.replayed:
        _raise(
            UNLINKED_RECEIPT,
            "Bu stok girişi talep bağlantısı olmadan tamamlanmış. Yeni bir kayıt deneyin.",
        )
    try:
        with transaction.atomic(using=using):
            receipt = PurchaseRequestReceipt.objects.using(using).create(
                line=line,
                inventory_transaction_id=transaction_record.pk,
                fulfilled_quantity=fulfilled_quantity,
                created_by=actor,
            )
    except IntegrityError as exc:
        if not _is_constraint(exc, RECEIPT_TX_UNIQUE):
            raise
        winner = PurchaseRequestReceipt.objects.using(using).get(
            inventory_transaction_id=transaction_record.pk
        )
        if winner.line_id != line.pk or winner.fulfilled_quantity != fulfilled_quantity:
            _raise(
                RECEIPT_ALREADY_LINKED,
                "Bu stok girişi başka bir talep karşılığında kayıtlı.",
            )
        return winner, True
    return receipt, False


def _require_quantity_line(line: PurchaseRequestLine) -> None:
    if line.material_id is None:
        _raise(MATERIAL_REQUIRED, "Mal kabul için kalem bir malzemeye bağlanmalıdır.")
    material = line.material
    if material.tracking_mode != Material.TrackingMode.QUANTITY:
        _raise(TRACKING_MISMATCH, "Bu kalem miktar malı değildir.")
    if material.unit_id != line.unit_id:
        _raise(
            UNIT_INCOMPATIBLE,
            "Malzemenin stok birimi talep kaleminin biriminden farklı. Dönüşüm yapılmaz.",
        )


def _require_serialized_line(line: PurchaseRequestLine) -> None:
    if line.material_id is None:
        _raise(MATERIAL_REQUIRED, "Mal kabul için kalem bir malzemeye bağlanmalıdır.")
    material = line.material
    if material.tracking_mode != Material.TrackingMode.SERIALIZED:
        _raise(TRACKING_MISMATCH, "Bu kalem tekil malzeme değildir.")
    if material.unit_id != line.unit_id:
        _raise(
            UNIT_INCOMPATIBLE,
            "Malzemenin birimi talep kaleminin biriminden farklı. Dönüşüm yapılmaz.",
        )
    _require_whole_quantity(line.requested_quantity)


def _validate_material_unit(material, unit, quantity: Decimal) -> None:
    if material is None:
        return
    if not material.active:
        _raise(MATERIAL_MISMATCH, "Pasif malzeme bağlanamaz.")
    if material.unit_id != unit.pk:
        _raise(
            UNIT_INCOMPATIBLE,
            "Malzeme birimi talep birimiyle aynı olmalıdır. Birim dönüşümü yapılmaz.",
        )
    if material.tracking_mode == Material.TrackingMode.SERIALIZED:
        _require_whole_quantity(quantity)


def _require_whole_quantity(quantity: Decimal) -> None:
    if quantity != quantity.to_integral_value():
        _raise(
            INVALID_QUANTITY,
            "Tekil malzeme talebi tam sayı adet olmalıdır.",
        )


def _requested_quantity(value) -> Decimal:
    try:
        return normalize_quantity(value)
    except ValidationError as exc:
        message = _message(exc) or "Talep miktarı sıfırdan büyük olmalıdır."
        _raise(INVALID_QUANTITY, message)


def _lead_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        else:
            _raise("procurement.invalid_lead_time", "Termin gün sayısı tam sayı olmalıdır.")
    if value < 0:
        _raise("procurement.invalid_lead_time", "Termin negatif olamaz.")
    return value


def _validate_dates(request_date, approval_date) -> None:
    if not isinstance(request_date, date):
        _raise(INVALID_DATES, "Talep tarihi geçerli bir tarih olmalıdır.")
    if approval_date is None:
        return
    if not isinstance(approval_date, date):
        _raise(INVALID_DATES, "Onay tarihi geçerli bir tarih olmalıdır.")
    if approval_date < request_date:
        _raise(INVALID_DATES, "Onay tarihi talep tarihinden önce olamaz.")


def _validate_expected_date(request_date, expected) -> None:
    if expected is None:
        return
    if not isinstance(expected, date):
        _raise(INVALID_DATES, "Beklenen geliş tarihi geçerli bir tarih olmalıdır.")
    if expected < request_date:
        _raise(INVALID_DATES, "Beklenen geliş tarihi talep tarihinden önce olamaz.")


def _optional_material(material_id, using: str):
    if material_id in (None, ""):
        return None
    material = (
        Material.objects.using(using).select_related("unit").filter(pk=material_id).first()
    )
    if material is None:
        _raise(MATERIAL_MISMATCH, "Malzeme bulunamadı.")
    return material


def _required_unit(unit_id, using: str) -> UnitOfMeasure:
    unit = UnitOfMeasure.objects.using(using).filter(pk=unit_id, active=True).first()
    if unit is None:
        _raise(UNIT_INCOMPATIBLE, "Aktif ölçü birimi seçilmelidir.")
    return unit


def _locked_request(purchase_request_id, using: str) -> PurchaseRequest:
    purchase_request = (
        PurchaseRequest.objects.using(using)
        .select_for_update()
        .filter(pk=purchase_request_id)
        .first()
    )
    if purchase_request is None:
        _raise("procurement.not_found", "Talep bulunamadı.")
    return purchase_request


def _locked_line(line_id, using: str) -> PurchaseRequestLine:
    line = (
        PurchaseRequestLine.objects.using(using)
        .select_for_update(of=("self",))
        .filter(pk=line_id)
        .first()
    )
    if line is None:
        _raise("procurement.not_found", "Talep kalemi bulunamadı.")
    return (
        PurchaseRequestLine.objects.using(using)
        .select_related("purchase_request", "material", "unit")
        .get(pk=line.pk)
    )


def _authorize(actor, permission: str) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied
    if not actor.has_perm(permission):
        raise PermissionDenied


def _request_snapshot(purchase_request: PurchaseRequest) -> dict[str, Any]:
    return {
        "id": str(purchase_request.pk),
        "request_no": purchase_request.request_no,
        "request_date": purchase_request.request_date.isoformat(),
        "approval_date": (
            purchase_request.approval_date.isoformat()
            if purchase_request.approval_date
            else None
        ),
        "note": purchase_request.note,
    }


def _line_snapshot(line: PurchaseRequestLine) -> dict[str, Any]:
    return {
        "id": str(line.pk),
        "purchase_request_id": str(line.purchase_request_id),
        "requested_description": line.requested_description,
        "material_id": str(line.material_id) if line.material_id else None,
        "unit_id": str(line.unit_id),
        "unit_code_snapshot": line.unit_code_snapshot,
        "unit_name_snapshot": line.unit_name_snapshot,
        "requested_quantity": format(line.requested_quantity, "f"),
        "lead_time_days": line.lead_time_days,
        "expected_arrival_date": (
            line.expected_arrival_date.isoformat() if line.expected_arrival_date else None
        ),
        "supplier_name": line.supplier_name,
        "unit_price": format(line.unit_price, "f") if line.unit_price is not None else None,
        "currency_code": line.currency_code,
    }


def _material_id(value):
    if value in (None, ""):
        return None
    return str(value)


def _message(exc: ValidationError) -> str:
    if hasattr(exc, "messages") and exc.messages:
        return str(exc.messages[0])
    return str(exc)


def _raise(code: str, message: str) -> None:
    raise ValidationError(message, code=code)


def _is_constraint(exc: IntegrityError, name: str) -> bool:
    cause = getattr(exc, "__cause__", None)
    diag = getattr(cause, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    if constraint == name:
        return True
    return name in str(exc)
