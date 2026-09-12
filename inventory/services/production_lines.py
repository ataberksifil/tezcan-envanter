from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connections, transaction

from audit.services import record_audit_event
from inventory.models import ProductionLine

PRODUCTION_LINE_ENTITY_TYPE = "inventory.production_line"
PRODUCTION_LINE_CREATED = "inventory.production_line.created"
PRODUCTION_LINE_UPDATED = "inventory.production_line.updated"
PRODUCTION_LINE_DEACTIVATED = "inventory.production_line.deactivated"
PRODUCTION_LINE_REACTIVATED = "inventory.production_line.reactivated"

ADD_PRODUCTION_LINE_PERMISSION = "inventory.add_productionline"
CHANGE_PRODUCTION_LINE_PERMISSION = "inventory.change_productionline"

DUPLICATE_CODE_MESSAGE = "Bu üretim hattı kodu zaten kullanılıyor."
PRODUCTION_LINE_CODE_UNIQUE_CONSTRAINT = "inventory_productionline_code_uniq"

# Fixed PostgreSQL advisory lock key scoped exclusively to ProductionLine hierarchy
# mutations. Must differ from Location hierarchy lock (DEC-025 Phase 3.3).
PRODUCTION_LINE_HIERARCHY_ADVISORY_LOCK_KEY = 831_002


@dataclass(frozen=True)
class ProductionLineWriteResult:
    production_line: ProductionLine
    changed: bool


def canonical_production_line_snapshot(production_line: ProductionLine) -> dict[str, Any]:
    """Return the canonical ProductionLine audit payload."""
    return {
        "id": str(production_line.id),
        "code": production_line.code,
        "name": production_line.name,
        "parent_id": (
            str(production_line.parent_id)
            if production_line.parent_id is not None
            else None
        ),
        "active": bool(production_line.active),
    }


def create_production_line(
    *,
    actor,
    code,
    name,
    parent_id=None,
    using: str = "default",
) -> ProductionLineWriteResult:
    _require_permission(actor, ADD_PRODUCTION_LINE_PERMISSION)
    _ensure_actor_database(actor, using)

    normalized_parent_id = _normalize_parent_id(parent_id)
    with transaction.atomic(using=using):
        if normalized_parent_id is not None:
            _acquire_hierarchy_lock(using)
            _validate_parent_exists(normalized_parent_id, using)

        production_line = ProductionLine(
            code=_normalize_text(code, "code", "Üretim hattı kodu boş olamaz."),
            name=_normalize_text(name, "name", "Üretim hattı adı boş olamaz."),
            parent_id=normalized_parent_id,
            active=True,
        )
        production_line._state.db = using

        if normalized_parent_id is not None:
            _revalidate_hierarchy_after_lock(
                production_line, normalized_parent_id, using
            )

        _validate_production_line(production_line)
        _save_production_line(production_line, using=using)
        record_audit_event(
            actor=actor,
            event_type=PRODUCTION_LINE_CREATED,
            entity_type=PRODUCTION_LINE_ENTITY_TYPE,
            entity_id=production_line.id,
            before_data=None,
            after_data=canonical_production_line_snapshot(production_line),
            using=using,
        )
        return ProductionLineWriteResult(
            production_line=production_line, changed=True
        )


def update_production_line(
    *,
    actor,
    production_line_id,
    code,
    name,
    parent_id=None,
    using: str = "default",
) -> ProductionLineWriteResult:
    _require_permission(actor, CHANGE_PRODUCTION_LINE_PERMISSION)
    _ensure_actor_database(actor, using)

    normalized_parent_id = _normalize_parent_id(parent_id)
    with transaction.atomic(using=using):
        production_line = _locked_production_line(production_line_id, using)
        before = canonical_production_line_snapshot(production_line)

        parent_changed = production_line.parent_id != normalized_parent_id
        if parent_changed:
            _acquire_hierarchy_lock(using)
            if normalized_parent_id is not None:
                _validate_parent_exists(normalized_parent_id, using)

        production_line.code = code
        production_line.name = name
        production_line.parent_id = normalized_parent_id

        if parent_changed:
            _revalidate_hierarchy_after_lock(
                production_line, normalized_parent_id, using
            )

        _validate_production_line(production_line)

        after = canonical_production_line_snapshot(production_line)
        if before == after:
            return ProductionLineWriteResult(
                production_line=production_line, changed=False
            )

        _save_production_line(
            production_line,
            using=using,
            update_fields=["code", "name", "parent", "updated_at"],
        )
        record_audit_event(
            actor=actor,
            event_type=PRODUCTION_LINE_UPDATED,
            entity_type=PRODUCTION_LINE_ENTITY_TYPE,
            entity_id=production_line.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return ProductionLineWriteResult(
            production_line=production_line, changed=True
        )


def set_production_line_active(
    *,
    actor,
    production_line_id,
    active: bool,
    using: str = "default",
) -> ProductionLineWriteResult:
    _require_permission(actor, CHANGE_PRODUCTION_LINE_PERMISSION)
    _ensure_actor_database(actor, using)

    target_active = bool(active)
    with transaction.atomic(using=using):
        production_line = _locked_production_line(production_line_id, using)
        if production_line.active == target_active:
            return ProductionLineWriteResult(
                production_line=production_line, changed=False
            )

        before = canonical_production_line_snapshot(production_line)
        production_line.active = target_active
        _validate_production_line(production_line)
        production_line.save(using=using, update_fields=["active", "updated_at"])
        after = canonical_production_line_snapshot(production_line)
        record_audit_event(
            actor=actor,
            event_type=(
                PRODUCTION_LINE_REACTIVATED
                if target_active
                else PRODUCTION_LINE_DEACTIVATED
            ),
            entity_type=PRODUCTION_LINE_ENTITY_TYPE,
            entity_id=production_line.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return ProductionLineWriteResult(
            production_line=production_line, changed=True
        )


def _require_permission(actor, permission: str) -> None:
    if actor is None or not actor.has_perm(permission):
        raise PermissionDenied


def _ensure_actor_database(actor, using: str) -> None:
    if getattr(actor, "pk", None) is None:
        raise ValidationError(
            "Actor must be saved before mutating production lines."
        )
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise ValidationError(
            "Actor must belong to the same database alias as the ProductionLine mutation."
        )


def _normalize_parent_id(parent_id):
    if parent_id in (None, ""):
        return None
    return parent_id


def _normalize_text(value, field_name: str, blank_message: str) -> str:
    if not isinstance(value, str):
        raise ValidationError({field_name: blank_message})
    normalized = value.strip()
    if not normalized:
        raise ValidationError({field_name: blank_message})
    return normalized


def _locked_production_line(production_line_id, using: str) -> ProductionLine:
    return (
        ProductionLine.objects.using(using)
        .select_for_update()
        .get(pk=production_line_id)
    )


def _validate_production_line(production_line: ProductionLine) -> None:
    production_line.full_clean()


def _validate_parent_exists(parent_id, using: str) -> None:
    if not ProductionLine.objects.using(using).filter(pk=parent_id).exists():
        raise ValidationError({"parent": "Seçilen üst üretim hattı bulunamadı."})


def _acquire_hierarchy_lock(using: str) -> None:
    with connections[using].cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [PRODUCTION_LINE_HIERARCHY_ADVISORY_LOCK_KEY],
        )


def _revalidate_hierarchy_after_lock(
    production_line: ProductionLine,
    parent_id,
    using: str,
) -> None:
    if production_line.pk is not None and parent_id == production_line.pk:
        raise ValidationError(
            {"parent": "Üretim hattı kendi üst hattı olamaz."}
        )

    if production_line.pk is not None:
        descendant_ids = _collect_descendant_ids(production_line.pk, using)
        if parent_id in descendant_ids:
            raise ValidationError(
                {"parent": "Üretim hattı hiyerarşisinde döngü oluşturulamaz."}
            )

    ancestor_id = parent_id
    visited: set = set()
    if production_line.pk is not None:
        visited.add(production_line.pk)
    while ancestor_id is not None:
        if ancestor_id in visited:
            raise ValidationError(
                {"parent": "Üretim hattı hiyerarşisinde döngü oluşturulamaz."}
            )
        visited.add(ancestor_id)
        ancestor_id = (
            ProductionLine.objects.using(using)
            .filter(pk=ancestor_id)
            .values_list("parent_id", flat=True)
            .first()
        )


def _collect_descendant_ids(production_line_id, using: str) -> set:
    found: set = set()
    queue = list(
        ProductionLine.objects.using(using)
        .filter(parent_id=production_line_id)
        .values_list("pk", flat=True)
    )
    while queue:
        child_id = queue.pop()
        if child_id in found:
            continue
        found.add(child_id)
        queue.extend(
            ProductionLine.objects.using(using)
            .filter(parent_id=child_id)
            .values_list("pk", flat=True)
        )
    return found


def _is_duplicate_production_line_code_error(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    if cause is None:
        return False
    diag = getattr(cause, "diag", None)
    if diag is None:
        return False
    return diag.constraint_name == PRODUCTION_LINE_CODE_UNIQUE_CONSTRAINT


def _save_production_line(
    production_line: ProductionLine,
    *,
    using: str,
    update_fields: list[str] | None = None,
) -> None:
    try:
        with transaction.atomic(using=using):
            if update_fields is not None:
                production_line.save(using=using, update_fields=update_fields)
            else:
                production_line.save(using=using)
    except IntegrityError as exc:
        if _is_duplicate_production_line_code_error(exc):
            raise ValidationError({"code": DUPLICATE_CODE_MESSAGE}) from exc
        raise
