from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connections, transaction

from audit.services import record_audit_event
from locations.models import Location

LOCATION_ENTITY_TYPE = "locations.location"
LOCATION_CREATED = "locations.location.created"
LOCATION_UPDATED = "locations.location.updated"
LOCATION_DEACTIVATED = "locations.location.deactivated"
LOCATION_REACTIVATED = "locations.location.reactivated"

ADD_LOCATION_PERMISSION = "locations.add_location"
CHANGE_LOCATION_PERMISSION = "locations.change_location"

DUPLICATE_CODE_MESSAGE = "Bu lokasyon kodu zaten kullanılıyor."
LOCATION_CODE_UNIQUE_CONSTRAINT = "locations_location_code_uniq"

# Fixed PostgreSQL advisory lock key scoped exclusively to Location hierarchy
# mutations (create/update parent assignment). See DEC-023 Phase 3.1.
LOCATION_HIERARCHY_ADVISORY_LOCK_KEY = 831_001


@dataclass(frozen=True)
class LocationWriteResult:
    location: Location
    changed: bool


def canonical_location_snapshot(location: Location) -> dict[str, Any]:
    """Return the canonical Location audit payload."""
    return {
        "id": str(location.id),
        "code": location.code,
        "name": location.name,
        "parent_id": str(location.parent_id) if location.parent_id is not None else None,
        "active": bool(location.active),
        "can_hold_stock": bool(location.can_hold_stock),
    }


def create_location(
    *,
    actor,
    code,
    name,
    parent_id=None,
    can_hold_stock=False,
    using: str = "default",
) -> LocationWriteResult:
    _require_permission(actor, ADD_LOCATION_PERMISSION)
    _ensure_actor_database(actor, using)

    normalized_parent_id = _normalize_parent_id(parent_id)
    with transaction.atomic(using=using):
        if normalized_parent_id is not None:
            _acquire_hierarchy_lock(using)
            _validate_parent_exists(normalized_parent_id, using)

        location = Location(
            code=_normalize_text(code, "code", "Lokasyon kodu boş olamaz."),
            name=_normalize_text(name, "name", "Lokasyon adı boş olamaz."),
            parent_id=normalized_parent_id,
            can_hold_stock=bool(can_hold_stock),
            active=True,
        )
        location._state.db = using

        if normalized_parent_id is not None:
            _revalidate_hierarchy_after_lock(location, normalized_parent_id, using)

        _validate_location(location)
        _save_location(location, using=using)
        record_audit_event(
            actor=actor,
            event_type=LOCATION_CREATED,
            entity_type=LOCATION_ENTITY_TYPE,
            entity_id=location.id,
            before_data=None,
            after_data=canonical_location_snapshot(location),
            using=using,
        )
        return LocationWriteResult(location=location, changed=True)


def update_location(
    *,
    actor,
    location_id,
    code,
    name,
    parent_id=None,
    can_hold_stock,
    using: str = "default",
) -> LocationWriteResult:
    _require_permission(actor, CHANGE_LOCATION_PERMISSION)
    _ensure_actor_database(actor, using)

    normalized_parent_id = _normalize_parent_id(parent_id)
    with transaction.atomic(using=using):
        location = _locked_location(location_id, using)
        before = canonical_location_snapshot(location)

        parent_changed = location.parent_id != normalized_parent_id
        if parent_changed:
            _acquire_hierarchy_lock(using)
            if normalized_parent_id is not None:
                _validate_parent_exists(normalized_parent_id, using)

        location.code = code
        location.name = name
        location.parent_id = normalized_parent_id
        location.can_hold_stock = bool(can_hold_stock)

        if parent_changed:
            _revalidate_hierarchy_after_lock(location, normalized_parent_id, using)

        _validate_location(location)

        after = canonical_location_snapshot(location)
        if before == after:
            return LocationWriteResult(location=location, changed=False)

        _save_location(
            location,
            using=using,
            update_fields=["code", "name", "parent", "can_hold_stock", "updated_at"],
        )
        record_audit_event(
            actor=actor,
            event_type=LOCATION_UPDATED,
            entity_type=LOCATION_ENTITY_TYPE,
            entity_id=location.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return LocationWriteResult(location=location, changed=True)


def set_location_active(
    *,
    actor,
    location_id,
    active: bool,
    using: str = "default",
) -> LocationWriteResult:
    _require_permission(actor, CHANGE_LOCATION_PERMISSION)
    _ensure_actor_database(actor, using)

    target_active = bool(active)
    with transaction.atomic(using=using):
        location = _locked_location(location_id, using)
        if location.active == target_active:
            return LocationWriteResult(location=location, changed=False)

        before = canonical_location_snapshot(location)
        location.active = target_active
        _validate_location(location)
        location.save(using=using, update_fields=["active", "updated_at"])
        after = canonical_location_snapshot(location)
        record_audit_event(
            actor=actor,
            event_type=(
                LOCATION_REACTIVATED if target_active else LOCATION_DEACTIVATED
            ),
            entity_type=LOCATION_ENTITY_TYPE,
            entity_id=location.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return LocationWriteResult(location=location, changed=True)


def _require_permission(actor, permission: str) -> None:
    if actor is None or not actor.has_perm(permission):
        raise PermissionDenied


def _ensure_actor_database(actor, using: str) -> None:
    if getattr(actor, "pk", None) is None:
        raise ValidationError("Actor must be saved before mutating locations.")
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise ValidationError(
            "Actor must belong to the same database alias as the Location mutation."
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


def _locked_location(location_id, using: str) -> Location:
    return Location.objects.using(using).select_for_update().get(pk=location_id)


def _validate_location(location: Location) -> None:
    location.full_clean()


def _validate_parent_exists(parent_id, using: str) -> None:
    if not Location.objects.using(using).filter(pk=parent_id).exists():
        raise ValidationError({"parent": "Seçilen üst lokasyon bulunamadı."})


def _acquire_hierarchy_lock(using: str) -> None:
    with connections[using].cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [LOCATION_HIERARCHY_ADVISORY_LOCK_KEY],
        )


def _revalidate_hierarchy_after_lock(
    location: Location,
    parent_id,
    using: str,
) -> None:
    if location.pk is not None and parent_id == location.pk:
        raise ValidationError({"parent": "Lokasyon kendi üst lokasyonu olamaz."})

    if location.pk is not None:
        descendant_ids = _collect_descendant_ids(location.pk, using)
        if parent_id in descendant_ids:
            raise ValidationError(
                {"parent": "Lokasyon hiyerarşisinde döngü oluşturulamaz."}
            )

    ancestor_id = parent_id
    visited: set = set()
    if location.pk is not None:
        visited.add(location.pk)
    while ancestor_id is not None:
        if ancestor_id in visited:
            raise ValidationError(
                {"parent": "Lokasyon hiyerarşisinde döngü oluşturulamaz."}
            )
        visited.add(ancestor_id)
        ancestor_id = (
            Location.objects.using(using)
            .filter(pk=ancestor_id)
            .values_list("parent_id", flat=True)
            .first()
        )


def _collect_descendant_ids(location_id, using: str) -> set:
    found: set = set()
    queue = list(
        Location.objects.using(using)
        .filter(parent_id=location_id)
        .values_list("pk", flat=True)
    )
    while queue:
        child_id = queue.pop()
        if child_id in found:
            continue
        found.add(child_id)
        queue.extend(
            Location.objects.using(using)
            .filter(parent_id=child_id)
            .values_list("pk", flat=True)
        )
    return found


def _is_duplicate_location_code_error(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    if cause is None:
        return False
    diag = getattr(cause, "diag", None)
    if diag is None:
        return False
    return diag.constraint_name == LOCATION_CODE_UNIQUE_CONSTRAINT


def _save_location(
    location: Location,
    *,
    using: str,
    update_fields: list[str] | None = None,
) -> None:
    try:
        with transaction.atomic(using=using):
            if update_fields is not None:
                location.save(using=using, update_fields=update_fields)
            else:
                location.save(using=using)
    except IntegrityError as exc:
        if _is_duplicate_location_code_error(exc):
            raise ValidationError({"code": DUPLICATE_CODE_MESSAGE}) from exc
        raise
