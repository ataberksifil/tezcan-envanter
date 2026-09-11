from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.services import record_audit_event
from catalog.models import Category, Material, UnitOfMeasure

MATERIAL_ENTITY_TYPE = "catalog.material"
MATERIAL_CREATED = "catalog.material.created"
MATERIAL_UPDATED = "catalog.material.updated"
MATERIAL_DEACTIVATED = "catalog.material.deactivated"
MATERIAL_REACTIVATED = "catalog.material.reactivated"

ADD_MATERIAL_PERMISSION = "catalog.add_material"
CHANGE_MATERIAL_PERMISSION = "catalog.change_material"

INACTIVE_CATEGORY_MESSAGE = "Pasif kategori atanamaz."
INACTIVE_UNIT_MESSAGE = "Pasif ölçü birimi atanamaz."
INVALID_CATEGORY_MESSAGE = "Geçersiz kategori."
INVALID_UNIT_MESSAGE = "Geçersiz ölçü birimi."

MINIMUM_STOCK_VALUE_STORAGE_QUANTIZE = Decimal("0.001")


@dataclass(frozen=True)
class MaterialWriteResult:
    material: Material
    changed: bool


def canonical_material_snapshot(material: Material) -> dict[str, Any]:
    """Return the canonical Material audit payload."""
    return {
        "id": str(material.id),
        "material_code": material.material_code,
        "name": material.name,
        "category_id": str(material.category_id),
        "brand": material.brand,
        "model": material.model,
        "unit_id": str(material.unit_id) if material.unit_id is not None else None,
        "tracking_mode": material.tracking_mode,
        "minimum_stock_value": _serialize_minimum_stock_value(
            material.minimum_stock_value
        ),
        "technical_specs": material.technical_specs,
        "active": bool(material.active),
    }


def create_material(
    *,
    actor,
    material_code,
    name,
    category_id,
    brand=None,
    model=None,
    unit_id=None,
    tracking_mode,
    minimum_stock_value=None,
    using: str = "default",
) -> MaterialWriteResult:
    _require_permission(actor, ADD_MATERIAL_PERMISSION)
    _ensure_actor_database(actor, using)

    with transaction.atomic(using=using):
        _validate_category_assignment(
            category_id=category_id,
            previous_category_id=None,
            using=using,
        )
        _validate_unit_assignment(
            unit_id=unit_id,
            previous_unit_id=None,
            using=using,
        )

        material = Material(
            material_code=material_code,
            name=name,
            category_id=category_id,
            brand=brand,
            model=model,
            unit_id=_normalize_unit_id(unit_id),
            tracking_mode=tracking_mode,
            minimum_stock_value=minimum_stock_value,
            active=True,
        )
        material._state.db = using
        _validate_material(material)
        material.save(using=using)
        record_audit_event(
            actor=actor,
            event_type=MATERIAL_CREATED,
            entity_type=MATERIAL_ENTITY_TYPE,
            entity_id=material.id,
            before_data=None,
            after_data=canonical_material_snapshot(material),
            using=using,
        )
        return MaterialWriteResult(material=material, changed=True)


def update_material(
    *,
    actor,
    material_id,
    material_code,
    name,
    category_id,
    brand=None,
    model=None,
    unit_id=None,
    tracking_mode,
    minimum_stock_value=None,
    using: str = "default",
) -> MaterialWriteResult:
    _require_permission(actor, CHANGE_MATERIAL_PERMISSION)
    _ensure_actor_database(actor, using)

    with transaction.atomic(using=using):
        material = _locked_material(material_id, using)
        before = canonical_material_snapshot(material)
        previous_category_id = material.category_id
        previous_unit_id = material.unit_id

        _validate_category_assignment(
            category_id=category_id,
            previous_category_id=previous_category_id,
            using=using,
        )
        _validate_unit_assignment(
            unit_id=unit_id,
            previous_unit_id=previous_unit_id,
            using=using,
        )

        _apply_base_fields(
            material,
            material_code=material_code,
            name=name,
            category_id=category_id,
            brand=brand,
            model=model,
            unit_id=unit_id,
            tracking_mode=tracking_mode,
            minimum_stock_value=minimum_stock_value,
        )
        _validate_material(material)

        after = canonical_material_snapshot(material)
        if before == after:
            return MaterialWriteResult(material=material, changed=False)

        material.save(
            using=using,
            update_fields=[
                "material_code",
                "name",
                "category",
                "brand",
                "model",
                "unit",
                "tracking_mode",
                "minimum_stock_value",
                "updated_at",
            ],
        )
        record_audit_event(
            actor=actor,
            event_type=MATERIAL_UPDATED,
            entity_type=MATERIAL_ENTITY_TYPE,
            entity_id=material.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return MaterialWriteResult(material=material, changed=True)


def set_material_active(
    *,
    actor,
    material_id,
    active: bool,
    using: str = "default",
) -> MaterialWriteResult:
    _require_permission(actor, CHANGE_MATERIAL_PERMISSION)
    _ensure_actor_database(actor, using)

    target_active = bool(active)
    with transaction.atomic(using=using):
        material = _locked_material(material_id, using)
        if material.active == target_active:
            return MaterialWriteResult(material=material, changed=False)

        before = canonical_material_snapshot(material)
        material.active = target_active
        _validate_material(material)
        material.save(using=using, update_fields=["active", "updated_at"])
        after = canonical_material_snapshot(material)
        record_audit_event(
            actor=actor,
            event_type=(
                MATERIAL_REACTIVATED if target_active else MATERIAL_DEACTIVATED
            ),
            entity_type=MATERIAL_ENTITY_TYPE,
            entity_id=material.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return MaterialWriteResult(material=material, changed=True)


def _serialize_minimum_stock_value(value: Decimal | None) -> str | None:
    if value is None:
        return None
    quantized = value.quantize(MINIMUM_STOCK_VALUE_STORAGE_QUANTIZE)
    return format(quantized, "f")


def _apply_base_fields(
    material: Material,
    *,
    material_code,
    name,
    category_id,
    brand=None,
    model=None,
    unit_id=None,
    tracking_mode,
    minimum_stock_value=None,
) -> None:
    material.material_code = material_code
    material.name = name
    material.category_id = category_id
    material.brand = brand
    material.model = model
    material.unit_id = _normalize_unit_id(unit_id)
    material.tracking_mode = tracking_mode
    material.minimum_stock_value = minimum_stock_value


def _normalize_unit_id(unit_id):
    if unit_id in (None, ""):
        return None
    return unit_id


def _validate_category_assignment(
    *,
    category_id,
    previous_category_id,
    using: str,
) -> None:
    try:
        category = Category.objects.using(using).get(pk=category_id)
    except Category.DoesNotExist as exc:
        raise ValidationError({"category": INVALID_CATEGORY_MESSAGE}) from exc
    if category.active:
        return
    if previous_category_id is not None and category_id == previous_category_id:
        return
    raise ValidationError({"category": INACTIVE_CATEGORY_MESSAGE})


def _validate_unit_assignment(
    *,
    unit_id,
    previous_unit_id,
    using: str,
) -> None:
    normalized_unit_id = _normalize_unit_id(unit_id)
    if normalized_unit_id is None:
        return
    try:
        unit = UnitOfMeasure.objects.using(using).get(pk=normalized_unit_id)
    except UnitOfMeasure.DoesNotExist as exc:
        raise ValidationError({"unit": INVALID_UNIT_MESSAGE}) from exc
    if unit.active:
        return
    if previous_unit_id is not None and normalized_unit_id == previous_unit_id:
        return
    raise ValidationError({"unit": INACTIVE_UNIT_MESSAGE})


def _require_permission(actor, permission: str) -> None:
    if actor is None or not actor.has_perm(permission):
        raise PermissionDenied


def _ensure_actor_database(actor, using: str) -> None:
    if getattr(actor, "pk", None) is None:
        raise ValidationError("Actor must be saved before mutating materials.")
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise ValidationError(
            "Actor must belong to the same database alias as the Material mutation."
        )


def _locked_material(material_id, using: str) -> Material:
    return Material.objects.using(using).select_for_update().get(pk=material_id)


def _validate_material(material: Material) -> None:
    material.full_clean()
