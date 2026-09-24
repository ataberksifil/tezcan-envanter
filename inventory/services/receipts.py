from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ReceiptMetadata,
    SerializedAsset,
    StockBalance,
)
from inventory.services.projections import next_serialized_asset_event_seq
from locations.models import Location

RECEIVE_STOCK_PERMISSION = "inventory.receive_stock"

INVALID_QUANTITY = "inventory.invalid_quantity"
OPERATION_CONFLICT = "inventory.operation_conflict"
INACTIVE_MATERIAL = "inventory.inactive_material"
TRACKING_MODE_MISMATCH = "inventory.tracking_mode_mismatch"
UNIT_MISMATCH = "inventory.unit_mismatch"
INACTIVE_CONDITION = "inventory.inactive_condition"
INVALID_DESTINATION = "inventory.invalid_destination"
INVALID_INTERNAL_ASSET_CODE = "inventory.invalid_internal_asset_code"
INTERNAL_ASSET_CODE_CONFLICT = "inventory.internal_asset_code_conflict"
SERIAL_NUMBER_CONFLICT = "inventory.serial_number_conflict"
INVALID_USAGE_PLACE = "inventory.invalid_usage_place"
INVALID_ARRIVAL_DATE = "inventory.invalid_arrival_date"
INVALID_SUPPLIER = "inventory.invalid_supplier"
INVALID_PACKAGING = "inventory.invalid_packaging"
PACKAGING_QUANTITY_MISMATCH = "inventory.packaging_quantity_mismatch"

OPERATION_ID_UNIQUE_CONSTRAINT = "inventory_tx_operation_id_uniq"
BALANCE_IDENTITY_UNIQUE_CONSTRAINT = "inventory_bal_identity_uniq"
ASSET_CODE_UNIQUE_CONSTRAINT = "inventory_asset_internal_code_uniq"
ASSET_MATERIAL_SERIAL_UNIQUE_CONSTRAINT = "inventory_asset_material_serial_uniq"

QUANTITY_QUANTUM = Decimal("0.001")
MAX_QUANTITY = Decimal("999999999999999.999")


@dataclass(frozen=True)
class ReceiptMetadataInput:
    usage_place: str
    arrived_on: date
    supplier_name: str | None = None
    package_count: int | None = None
    package_label: str | None = None
    contents_per_package: Decimal | None = None


@dataclass(frozen=True)
class InventoryMutationResult:
    transaction: InventoryTransaction
    lines: tuple[InventoryTransactionLine, ...]
    replayed: bool
    issue_context: IssueContext | None = None
    serialized_asset: SerializedAsset | None = None
    receipt_metadata: ReceiptMetadata | None = None


def receive_quantity(
    *,
    actor,
    operation_id,
    material_id,
    unit_id,
    condition_id,
    target_location_id,
    quantity,
    receipt_metadata: ReceiptMetadataInput | None = None,
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
    normalized_metadata = normalize_receipt_metadata(
        receipt_metadata,
        quantity=normalized_quantity,
        allow_packaging=True,
    )
    fingerprint = _request_fingerprint(
        acting_user_id=current_actor.pk,
        material_id=normalized_material_id,
        unit_id=normalized_unit_id,
        condition_id=normalized_condition_id,
        target_location_id=normalized_target_location_id,
        quantity=normalized_quantity,
        receipt_metadata=normalized_metadata,
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
        metadata_row = _create_receipt_metadata(
            header=header,
            material=material,
            normalized=normalized_metadata,
            using=using,
        )

        return InventoryMutationResult(
            transaction=header,
            lines=(line,),
            replayed=False,
            receipt_metadata=metadata_row,
        )


def receive_serialized(
    *,
    actor,
    operation_id,
    material_id,
    internal_asset_code,
    serial_number,
    condition_id,
    target_location_id,
    receipt_metadata: ReceiptMetadataInput | None = None,
    using: str = "default",
) -> InventoryMutationResult:
    """Atomically create one asset, append its RECEIVE, and establish projection.

    Lock order is the operation-id uniqueness reservation, Material, target
    Location, MaterialCondition, then the asset identifier uniqueness insert.
    PostgreSQL uniqueness is the final authority for concurrent code/serial
    collisions. Serialized assets never create or update StockBalance rows.
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
    normalized_internal_asset_code = normalize_internal_asset_code(
        internal_asset_code
    )
    normalized_serial_number = normalize_serial_number(serial_number)
    normalized_metadata = normalize_receipt_metadata(
        receipt_metadata,
        quantity=None,
        allow_packaging=False,
    )
    fingerprint = _serialized_request_fingerprint(
        acting_user_id=current_actor.pk,
        material_id=normalized_material_id,
        internal_asset_code=normalized_internal_asset_code,
        serial_number=normalized_serial_number,
        condition_id=normalized_condition_id,
        target_location_id=normalized_target_location_id,
        receipt_metadata=normalized_metadata,
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
        _validate_new_serialized_receipt_masters(
            material=material,
            condition=condition,
            location=location,
        )

        try:
            with transaction.atomic(using=using):
                asset = SerializedAsset.objects.using(using).create(
                    material=material,
                    internal_asset_code=normalized_internal_asset_code,
                    serial_number=normalized_serial_number,
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

        asset = _locked_serialized_asset(asset.pk, using)
        line = InventoryTransactionLine.objects.using(using).create(
            transaction=header,
            line_number=1,
            material=material,
            serialized_asset=asset,
            quantity=None,
            unit=None,
            condition=condition,
            source_location=None,
            target_location=location,
            asset_event_seq=next_serialized_asset_event_seq(asset.pk, using=using),
        )
        metadata_row = _create_receipt_metadata(
            header=header,
            material=material,
            normalized=normalized_metadata,
            using=using,
        )
        return InventoryMutationResult(
            transaction=header,
            lines=(line,),
            replayed=False,
            serialized_asset=asset,
            receipt_metadata=metadata_row,
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


def normalize_internal_asset_code(value) -> str:
    if not isinstance(value, str):
        _raise_validation(
            INVALID_INTERNAL_ASSET_CODE,
            "Dahili varlık kodu metin olmalıdır.",
        )
    normalized = value.strip()
    if not normalized:
        _raise_validation(
            INVALID_INTERNAL_ASSET_CODE,
            "Dahili varlık kodu boş olamaz.",
        )
    if len(normalized) > 64:
        _raise_validation(
            INVALID_INTERNAL_ASSET_CODE,
            "Dahili varlık kodu en fazla 64 karakter olabilir.",
        )
    return normalized


def normalize_serial_number(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _raise_validation(
            "inventory.invalid_serial_number",
            "Üretici seri numarası metin olmalıdır.",
        )
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 255:
        _raise_validation(
            "inventory.invalid_serial_number",
            "Üretici seri numarası en fazla 255 karakter olabilir.",
        )
    return normalized


def normalize_receipt_metadata(
    value,
    *,
    quantity: Decimal | None,
    allow_packaging: bool,
) -> ReceiptMetadataInput | None:
    if value is None:
        return None
    if not isinstance(value, ReceiptMetadataInput):
        _raise_validation(
            INVALID_USAGE_PLACE,
            "Mal kabul bilgisi geçersiz.",
        )
    usage_place = _normalize_required_text(
        value.usage_place,
        field="usage_place",
        code=INVALID_USAGE_PLACE,
        empty_message="Kullanıldığı yer boş olamaz.",
        max_length=255,
    )
    arrived_on = _normalize_arrival_date(value.arrived_on)
    supplier_name = _normalize_optional_text(
        value.supplier_name,
        field="supplier_name",
        code=INVALID_SUPPLIER,
        max_length=255,
    )
    package_count, package_label, contents_per_package = _normalize_packaging(
        package_count=value.package_count,
        package_label=value.package_label,
        contents_per_package=value.contents_per_package,
        quantity=quantity,
        allow_packaging=allow_packaging,
    )
    return ReceiptMetadataInput(
        usage_place=usage_place,
        arrived_on=arrived_on,
        supplier_name=supplier_name,
        package_count=package_count,
        package_label=package_label,
        contents_per_package=contents_per_package,
    )


def _normalize_required_text(value, *, field: str, code: str, empty_message: str, max_length: int) -> str:
    if not isinstance(value, str):
        _raise_validation(code, f"{field} metin olmalıdır.")
    normalized = value.strip()
    if not normalized:
        _raise_validation(code, empty_message)
    if len(normalized) > max_length:
        _raise_validation(code, f"{field} en fazla {max_length} karakter olabilir.")
    return normalized


def _normalize_optional_text(value, *, field: str, code: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _raise_validation(code, f"{field} metin olmalıdır.")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        _raise_validation(code, f"{field} en fazla {max_length} karakter olabilir.")
    return normalized


def _normalize_arrival_date(value) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        _raise_validation(INVALID_ARRIVAL_DATE, "Geliş tarihi geçerli bir tarih olmalıdır.")
    today = timezone.localdate()
    if value > today:
        _raise_validation(INVALID_ARRIVAL_DATE, "Geliş tarihi gelecek bir gün olamaz.")
    return value


def _normalize_packaging(
    *,
    package_count,
    package_label,
    contents_per_package,
    quantity: Decimal | None,
    allow_packaging: bool,
):
    label = _normalize_optional_text(
        package_label,
        field="package_label",
        code=INVALID_PACKAGING,
        max_length=64,
    )
    has_count = package_count not in (None, "")
    has_contents = contents_per_package not in (None, "")
    if not has_count and not has_contents and label is None:
        return None, None, None
    if not allow_packaging:
        _raise_validation(
            INVALID_PACKAGING,
            "Tekil varlık girişinde paket çarpımı kullanılmaz.",
        )
    if not has_count or not has_contents:
        _raise_validation(
            INVALID_PACKAGING,
            "Paket bilgisi için hem paket adedi hem paket içi miktar girilmelidir.",
        )
    if isinstance(package_count, bool) or not isinstance(package_count, int):
        _raise_validation(INVALID_PACKAGING, "Paket adedi tam sayı olmalıdır.")
    if package_count < 1:
        _raise_validation(INVALID_PACKAGING, "Paket adedi en az 1 olmalıdır.")
    contents = normalize_quantity(contents_per_package)
    if quantity is None:
        _raise_validation(
            INVALID_PACKAGING,
            "Paket çarpımı yalnız miktar girişinde kullanılır.",
        )
    expected = contents * package_count
    if expected != quantity:
        _raise_validation(
            PACKAGING_QUANTITY_MISMATCH,
            "Paket adedi × paket içi miktar, kaydedilen stok miktarına eşit olmalıdır. "
            "Stok birimi değişmez; dönüşüm yapılmaz.",
        )
    return package_count, label, contents


def _create_receipt_metadata(*, header, material, normalized, using: str):
    if normalized is None:
        return None
    unit = material.unit
    return ReceiptMetadata.objects.using(using).create(
        transaction=header,
        usage_place=normalized.usage_place,
        arrived_on=normalized.arrived_on,
        supplier_name=normalized.supplier_name,
        package_count=normalized.package_count,
        package_label=normalized.package_label,
        contents_per_package=normalized.contents_per_package,
        material_code_snapshot=material.material_code,
        material_name_snapshot=material.name,
        material_brand_snapshot=_blank_to_null(material.brand),
        material_model_snapshot=_blank_to_null(material.model),
        unit_code_snapshot=unit.code if unit is not None else None,
        unit_name_snapshot=unit.name if unit is not None else None,
    )


def _blank_to_null(value):
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


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
    receipt_metadata: ReceiptMetadataInput | None = None,
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
    payload.update(_receipt_metadata_fingerprint_fields(receipt_metadata))
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _serialized_request_fingerprint(
    *,
    acting_user_id,
    material_id: uuid.UUID,
    internal_asset_code: str,
    serial_number: str | None,
    condition_id: uuid.UUID,
    target_location_id: uuid.UUID,
    receipt_metadata: ReceiptMetadataInput | None = None,
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "condition_id": str(condition_id),
        "internal_asset_code": internal_asset_code,
        "material_id": str(material_id),
        "serial_number": serial_number,
        "target_location_id": str(target_location_id),
        "transaction_type": InventoryTransaction.TransactionType.RECEIPT,
        "tracking_mode": Material.TrackingMode.SERIALIZED,
    }
    payload.update(_receipt_metadata_fingerprint_fields(receipt_metadata))
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
    lines = tuple(
        transaction_record.lines.using(using)
        .select_related("serialized_asset")
        .order_by("line_number", "pk")
    )
    serialized_asset = next(
        (line.serialized_asset for line in lines if line.serialized_asset_id),
        None,
    )
    try:
        receipt_metadata = transaction_record.receipt_metadata
    except ReceiptMetadata.DoesNotExist:
        receipt_metadata = None
    return InventoryMutationResult(
        transaction=transaction_record,
        lines=lines,
        replayed=True,
        serialized_asset=serialized_asset,
        receipt_metadata=receipt_metadata,
    )


def _receipt_metadata_fingerprint_fields(
    receipt_metadata: ReceiptMetadataInput | None,
) -> dict:
    if receipt_metadata is None:
        return {}
    return {
        "receipt_arrived_on": receipt_metadata.arrived_on.isoformat(),
        "receipt_contents_per_package": (
            format(receipt_metadata.contents_per_package, ".3f")
            if receipt_metadata.contents_per_package is not None
            else None
        ),
        "receipt_package_count": receipt_metadata.package_count,
        "receipt_package_label": receipt_metadata.package_label,
        "receipt_supplier_name": receipt_metadata.supplier_name,
        "receipt_usage_place": receipt_metadata.usage_place,
    }


def _locked_material(material_id: uuid.UUID, using: str) -> Material:
    try:
        return Material.objects.using(using).select_for_update().get(pk=material_id)
    except Material.DoesNotExist:
        _raise_validation(INACTIVE_MATERIAL, "Malzeme bulunamadı veya aktif değil.")


def _locked_serialized_asset(asset_id: uuid.UUID, using: str) -> SerializedAsset:
    return (
        SerializedAsset.objects.using(using)
        .select_for_update()
        .get(pk=asset_id)
    )


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


def _validate_new_serialized_receipt_masters(
    *,
    material: Material,
    condition: MaterialCondition,
    location: Location,
) -> None:
    if not material.active:
        _raise_validation(INACTIVE_MATERIAL, "Pasif malzemeye stok girişi yapılamaz.")
    if material.tracking_mode != Material.TrackingMode.SERIALIZED:
        _raise_validation(
            TRACKING_MODE_MISMATCH,
            "Bu servis yalnız tekil takip edilen malzemeleri kabul eder.",
        )
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
    return _constraint_name(exc) == constraint_name


def _constraint_name(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None) if cause is not None else None
    return getattr(diag, "constraint_name", None)


def _raise_validation(code: str, message: str):
    raise ValidationError(message, code=code)
