from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from audit.services import record_audit_event
from catalog.models import UnitOfMeasure

UOM_ENTITY_TYPE = "catalog.unit_of_measure"
UOM_CREATED = "catalog.unit_of_measure.created"
UOM_UPDATED = "catalog.unit_of_measure.updated"
UOM_DEACTIVATED = "catalog.unit_of_measure.deactivated"
UOM_REACTIVATED = "catalog.unit_of_measure.reactivated"

ADD_UOM_PERMISSION = "catalog.add_unitofmeasure"
CHANGE_UOM_PERMISSION = "catalog.change_unitofmeasure"

DUPLICATE_CODE_MESSAGE = "Bu birim kodu zaten kullanılıyor."


@dataclass(frozen=True)
class UnitOfMeasureWriteResult:
    unit: UnitOfMeasure
    changed: bool


def canonical_unit_of_measure_snapshot(unit: UnitOfMeasure) -> dict[str, Any]:
    """Return the canonical UnitOfMeasure audit payload (exactly five fields)."""
    return {
        "id": str(unit.id),
        "code": unit.code,
        "name": unit.name,
        "decimal_places": unit.decimal_places,
        "active": bool(unit.active),
    }


def create_unit_of_measure(
    *,
    actor,
    code,
    name,
    using: str = "default",
) -> UnitOfMeasureWriteResult:
    _require_permission(actor, ADD_UOM_PERMISSION)
    _ensure_actor_database(actor, using)

    with transaction.atomic(using=using):
        unit = UnitOfMeasure(
            code=code,
            name=name,
            decimal_places=None,
            active=True,
        )
        unit._state.db = using
        _validate_unit(unit)
        _save_unit(unit, using=using)
        record_audit_event(
            actor=actor,
            event_type=UOM_CREATED,
            entity_type=UOM_ENTITY_TYPE,
            entity_id=unit.id,
            before_data=None,
            after_data=canonical_unit_of_measure_snapshot(unit),
            using=using,
        )
        return UnitOfMeasureWriteResult(unit=unit, changed=True)


def update_unit_of_measure(
    *,
    actor,
    unit_of_measure_id,
    code,
    name,
    using: str = "default",
) -> UnitOfMeasureWriteResult:
    _require_permission(actor, CHANGE_UOM_PERMISSION)
    _ensure_actor_database(actor, using)

    with transaction.atomic(using=using):
        unit = _locked_unit(unit_of_measure_id, using)
        before = canonical_unit_of_measure_snapshot(unit)

        unit.code = code
        unit.name = name
        _validate_unit(unit)

        after = canonical_unit_of_measure_snapshot(unit)
        if before == after:
            return UnitOfMeasureWriteResult(unit=unit, changed=False)

        _save_unit(
            unit,
            using=using,
            update_fields=["code", "name", "updated_at"],
        )
        record_audit_event(
            actor=actor,
            event_type=UOM_UPDATED,
            entity_type=UOM_ENTITY_TYPE,
            entity_id=unit.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return UnitOfMeasureWriteResult(unit=unit, changed=True)


def set_unit_of_measure_active(
    *,
    actor,
    unit_of_measure_id,
    active: bool,
    using: str = "default",
) -> UnitOfMeasureWriteResult:
    _require_permission(actor, CHANGE_UOM_PERMISSION)
    _ensure_actor_database(actor, using)

    target_active = bool(active)
    with transaction.atomic(using=using):
        unit = _locked_unit(unit_of_measure_id, using)
        if unit.active == target_active:
            return UnitOfMeasureWriteResult(unit=unit, changed=False)

        before = canonical_unit_of_measure_snapshot(unit)
        unit.active = target_active
        _validate_unit(unit)
        unit.save(using=using, update_fields=["active", "updated_at"])
        after = canonical_unit_of_measure_snapshot(unit)
        record_audit_event(
            actor=actor,
            event_type=(
                UOM_REACTIVATED if target_active else UOM_DEACTIVATED
            ),
            entity_type=UOM_ENTITY_TYPE,
            entity_id=unit.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return UnitOfMeasureWriteResult(unit=unit, changed=True)


def _require_permission(actor, permission: str) -> None:
    if actor is None or not actor.has_perm(permission):
        raise PermissionDenied


def _ensure_actor_database(actor, using: str) -> None:
    if getattr(actor, "pk", None) is None:
        raise ValidationError("Actor must be saved before mutating units of measure.")
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise ValidationError(
            "Actor must belong to the same database alias as the UnitOfMeasure mutation."
        )


def _locked_unit(unit_id, using: str) -> UnitOfMeasure:
    return UnitOfMeasure.objects.using(using).select_for_update().get(pk=unit_id)


def _validate_unit(unit: UnitOfMeasure) -> None:
    unit.full_clean()


def _is_duplicate_uom_code_error(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    if cause is None:
        return False
    diag = getattr(cause, "diag", None)
    if diag is None:
        return False
    return diag.constraint_name == "catalog_uom_code_uniq"


def _save_unit(
    unit: UnitOfMeasure,
    *,
    using: str,
    update_fields: list[str] | None = None,
) -> None:
    try:
        with transaction.atomic(using=using):
            if update_fields is not None:
                unit.save(using=using, update_fields=update_fields)
            else:
                unit.save(using=using)
    except IntegrityError as exc:
        if _is_duplicate_uom_code_error(exc):
            raise ValidationError({"code": DUPLICATE_CODE_MESSAGE}) from exc
        raise
