from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.services import record_audit_event
from catalog.models import Category

CATEGORY_ENTITY_TYPE = "catalog.category"
CATEGORY_CREATED = "catalog.category.created"
CATEGORY_UPDATED = "catalog.category.updated"
CATEGORY_DEACTIVATED = "catalog.category.deactivated"
CATEGORY_REACTIVATED = "catalog.category.reactivated"

ADD_CATEGORY_PERMISSION = "catalog.add_category"
CHANGE_CATEGORY_PERMISSION = "catalog.change_category"


@dataclass(frozen=True)
class CategoryWriteResult:
    category: Category
    changed: bool


def canonical_category_snapshot(category: Category) -> dict[str, Any]:
    """Return the canonical Category audit payload (exactly five fields)."""
    return {
        "id": str(category.id),
        "code": category.code,
        "name": category.name,
        "parent_id": str(category.parent_id) if category.parent_id is not None else None,
        "active": bool(category.active),
    }


def create_category(
    *,
    actor,
    code,
    name,
    parent_id=None,
    using: str = "default",
) -> CategoryWriteResult:
    _require_permission(actor, ADD_CATEGORY_PERMISSION)
    _ensure_actor_database(actor, using)

    with transaction.atomic(using=using):
        category = Category(
            code=code,
            name=name,
            parent_id=_normalize_parent_id(parent_id),
            active=True,
        )
        category._state.db = using
        _validate_category(category)
        category.save(using=using)
        record_audit_event(
            actor=actor,
            event_type=CATEGORY_CREATED,
            entity_type=CATEGORY_ENTITY_TYPE,
            entity_id=category.id,
            before_data=None,
            after_data=canonical_category_snapshot(category),
            using=using,
        )
        return CategoryWriteResult(category=category, changed=True)


def update_category(
    *,
    actor,
    category_id,
    code,
    name,
    parent_id=None,
    using: str = "default",
) -> CategoryWriteResult:
    _require_permission(actor, CHANGE_CATEGORY_PERMISSION)
    _ensure_actor_database(actor, using)

    with transaction.atomic(using=using):
        category = _locked_category(category_id, using)
        before = canonical_category_snapshot(category)

        category.code = code
        category.name = name
        category.parent_id = _normalize_parent_id(parent_id)
        _validate_category(category)

        after = canonical_category_snapshot(category)
        if before == after:
            return CategoryWriteResult(category=category, changed=False)

        category.save(
            using=using,
            update_fields=["code", "name", "parent", "updated_at"],
        )
        record_audit_event(
            actor=actor,
            event_type=CATEGORY_UPDATED,
            entity_type=CATEGORY_ENTITY_TYPE,
            entity_id=category.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return CategoryWriteResult(category=category, changed=True)


def set_category_active(
    *,
    actor,
    category_id,
    active: bool,
    using: str = "default",
) -> CategoryWriteResult:
    _require_permission(actor, CHANGE_CATEGORY_PERMISSION)
    _ensure_actor_database(actor, using)

    target_active = bool(active)
    with transaction.atomic(using=using):
        category = _locked_category(category_id, using)
        if category.active == target_active:
            return CategoryWriteResult(category=category, changed=False)

        before = canonical_category_snapshot(category)
        category.active = target_active
        _validate_category(category)
        category.save(using=using, update_fields=["active", "updated_at"])
        after = canonical_category_snapshot(category)
        record_audit_event(
            actor=actor,
            event_type=(
                CATEGORY_REACTIVATED if target_active else CATEGORY_DEACTIVATED
            ),
            entity_type=CATEGORY_ENTITY_TYPE,
            entity_id=category.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return CategoryWriteResult(category=category, changed=True)


def _require_permission(actor, permission: str) -> None:
    if actor is None or not actor.has_perm(permission):
        raise PermissionDenied


def _ensure_actor_database(actor, using: str) -> None:
    if getattr(actor, "pk", None) is None:
        raise ValidationError("Actor must be saved before mutating categories.")
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise ValidationError(
            "Actor must belong to the same database alias as the Category mutation."
        )


def _normalize_parent_id(parent_id):
    if parent_id in (None, ""):
        return None
    return parent_id


def _locked_category(category_id, using: str) -> Category:
    return Category.objects.using(using).select_for_update().get(pk=category_id)


def _validate_category(category: Category) -> None:
    category.full_clean()
