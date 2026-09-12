from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.roles import (
    MANAGE_ACCESS_PERMISSION,
    RESERVED_ROLE_NAMES,
    SAFE_CATALOG_PERMISSION_LABELS,
    SAFE_CATALOG_PERMISSION_SET,
    SUPPORTED_ACCESS_PERMISSION_SET,
)
from audit.services import record_audit_event

User = get_user_model()

ROLE_AUDIT_NAMESPACE = uuid.UUID("532d9aad-30c7-4c25-8b3f-8ff756f7d883")
USER_AUDIT_NAMESPACE = uuid.UUID("af27c646-52ab-4f22-b1b3-d7f274a885c0")

ROLE_ENTITY_TYPE = "accounts.role"
USER_ENTITY_TYPE = "accounts.user"
ROLE_CREATED = "accounts.role.created"
ROLE_UPDATED = "accounts.role.updated"
ROLE_PERMISSIONS_CHANGED = "accounts.role.permissions_changed"
USER_ROLES_CHANGED = "accounts.user.roles_changed"

GROUP_NAME_UNIQUE_CONSTRAINT = "auth_group_name_key"

WRITE_VIEW_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    *(
        (f"catalog.{action}_{model}", f"catalog.view_{model}")
        for model in ("category", "unitofmeasure", "material")
        for action in ("add", "change")
    ),
    ("locations.add_location", "locations.view_location"),
    ("locations.change_location", "locations.view_location"),
    ("accounts.add_employee", "accounts.view_employee"),
    ("accounts.change_employee", "accounts.view_employee"),
    ("inventory.add_productionline", "inventory.view_productionline"),
    ("inventory.change_productionline", "inventory.view_productionline"),
)


@dataclass(frozen=True)
class RoleWriteResult:
    role: Group
    changed: bool


@dataclass(frozen=True)
class UserRoleWriteResult:
    user: User
    changed: bool


def role_audit_id(role_or_pk) -> uuid.UUID:
    pk = role_or_pk.pk if hasattr(role_or_pk, "pk") else role_or_pk
    return uuid.uuid5(ROLE_AUDIT_NAMESPACE, str(pk))


def user_audit_id(user_or_pk) -> uuid.UUID:
    pk = user_or_pk.pk if hasattr(user_or_pk, "pk") else user_or_pk
    return uuid.uuid5(USER_AUDIT_NAMESPACE, str(pk))


def permission_label(permission: Permission) -> str:
    return f"{permission.content_type.app_label}.{permission.codename}"


def role_permission_labels(role: Group, *, using: str | None = None) -> set[str]:
    database = using or role._state.db or "default"
    permissions = role.permissions.using(database).select_related("content_type")
    return {permission_label(permission) for permission in permissions}


def is_supported_role(role: Group, *, using: str | None = None) -> bool:
    return role_permission_labels(role, using=using) <= SUPPORTED_ACCESS_PERMISSION_SET


def canonical_role_snapshot(role: Group, *, using: str | None = None) -> dict[str, Any]:
    labels = sorted(role_permission_labels(role, using=using))
    return {
        "id": role.pk,
        "audit_id": str(role_audit_id(role)),
        "name": role.name,
        "permissions": labels,
    }


def _direct_permission_labels(user, using: str) -> set[str]:
    permissions = user.user_permissions.using(using).select_related("content_type")
    return {permission_label(permission) for permission in permissions}


def _group_permission_labels(user, using: str) -> set[str]:
    permissions = Permission.objects.using(using).filter(group__user=user).select_related(
        "content_type"
    )
    return {permission_label(permission) for permission in permissions}


def assigned_permission_labels(user, *, using: str | None = None) -> set[str]:
    database = using or user._state.db or "default"
    return _direct_permission_labels(user, database) | _group_permission_labels(
        user, database
    )


def effective_permission_labels(user, *, using: str | None = None) -> set[str]:
    database = using or user._state.db or "default"
    if not user.is_active:
        return set()
    if user.is_superuser:
        permissions = Permission.objects.using(database).select_related("content_type")
        return {permission_label(permission) for permission in permissions}
    return assigned_permission_labels(user, using=database)


def canonical_user_snapshot(user, *, using: str | None = None) -> dict[str, Any]:
    database = using or user._state.db or "default"
    roles = list(user.groups.using(database).order_by("pk").values("id", "name"))
    return {
        "id": user.pk,
        "audit_id": str(user_audit_id(user)),
        "username": user.get_username(),
        "is_active": bool(user.is_active),
        "is_staff": bool(user.is_staff),
        "is_superuser": bool(user.is_superuser),
        "roles": roles,
        "direct_permissions": sorted(_direct_permission_labels(user, database)),
        "effective_permissions": sorted(effective_permission_labels(user, using=database)),
    }


def create_role(*, actor, name, using: str = "default") -> RoleWriteResult:
    actor = _require_actor(actor, using)
    normalized_name = _normalize_role_name(name)
    _reject_reserved_name(normalized_name)

    with transaction.atomic(using=using):
        role = Group(name=normalized_name)
        role._state.db = using
        role.full_clean()
        try:
            with transaction.atomic(using=using):
                role.save(using=using)
        except IntegrityError as exc:
            _raise_role_name_conflict_or_reraise(exc)
        record_audit_event(
            actor=actor,
            event_type=ROLE_CREATED,
            entity_type=ROLE_ENTITY_TYPE,
            entity_id=role_audit_id(role),
            before_data=None,
            after_data=canonical_role_snapshot(role, using=using),
            using=using,
        )
        return RoleWriteResult(role=role, changed=True)


def rename_role(
    *, actor, role_id, name, using: str = "default"
) -> RoleWriteResult:
    actor = _require_actor(actor, using)
    normalized_name = _normalize_role_name(name)

    with transaction.atomic(using=using):
        role = _locked_role(role_id, using)
        _require_supported_role(role, using)
        if role.name in RESERVED_ROLE_NAMES:
            raise ValidationError("Varsayılan roller yeniden adlandırılamaz.")
        _reject_reserved_name(normalized_name)
        _require_role_mutation_allowed(actor, role, using)

        before = canonical_role_snapshot(role, using=using)
        if role.name == normalized_name:
            return RoleWriteResult(role=role, changed=False)

        role.name = normalized_name
        role.full_clean()
        try:
            with transaction.atomic(using=using):
                role.save(using=using, update_fields=["name"])
        except IntegrityError as exc:
            _raise_role_name_conflict_or_reraise(exc)
        after = canonical_role_snapshot(role, using=using)
        record_audit_event(
            actor=actor,
            event_type=ROLE_UPDATED,
            entity_type=ROLE_ENTITY_TYPE,
            entity_id=role_audit_id(role),
            before_data=before,
            after_data=after,
            using=using,
        )
        return RoleWriteResult(role=role, changed=True)


def set_role_permissions(
    *,
    actor,
    role_id,
    catalog_permissions: Iterable[str],
    manage_access: bool | None = None,
    using: str = "default",
) -> RoleWriteResult:
    actor = _require_actor(actor, using)
    requested = _normalize_catalog_permission_labels(catalog_permissions)
    _validate_write_view_dependencies(requested)

    with transaction.atomic(using=using):
        role = _locked_role(role_id, using)
        current = role_permission_labels(role, using=using)
        _require_supported_role(role, using)
        _require_role_mutation_allowed(actor, role, using)

        currently_manages_access = MANAGE_ACCESS_PERMISSION in current
        if actor.is_superuser:
            desired_manage_access = (
                currently_manages_access
                if manage_access is None
                else bool(manage_access)
            )
        else:
            if manage_access not in (None, False):
                raise PermissionDenied
            desired_manage_access = False
            if requested - _actor_safe_permissions(actor, using):
                raise PermissionDenied

        desired_labels = set(requested)
        if desired_manage_access:
            desired_labels.add(MANAGE_ACCESS_PERMISSION)
        if current == desired_labels:
            return RoleWriteResult(role=role, changed=False)

        permission_map = _load_supported_permissions(using)
        desired_permissions = [permission_map[label] for label in sorted(desired_labels)]
        before = canonical_role_snapshot(role, using=using)
        role.permissions.set(desired_permissions)
        after = canonical_role_snapshot(role, using=using)
        record_audit_event(
            actor=actor,
            event_type=ROLE_PERMISSIONS_CHANGED,
            entity_type=ROLE_ENTITY_TYPE,
            entity_id=role_audit_id(role),
            before_data=before,
            after_data=after,
            using=using,
        )
        return RoleWriteResult(role=role, changed=True)


def set_user_roles(
    *, actor, user_id, role_ids: Iterable[int], using: str = "default"
) -> UserRoleWriteResult:
    actor = _require_actor(actor, using)
    desired_ids = _normalize_role_ids(role_ids)

    with transaction.atomic(using=using):
        target = User.objects.using(using).select_for_update().get(pk=user_id)
        current_ids = set(
            target.groups.using(using).values_list("pk", flat=True)
        )
        lock_ids = sorted(current_ids | desired_ids)
        locked_roles = list(
            Group.objects.using(using)
            .select_for_update()
            .filter(pk__in=lock_ids)
            .order_by("pk")
        )
        role_map = {role.pk: role for role in locked_roles}
        if set(role_map) != set(lock_ids):
            raise ValidationError("Seçilen rollerden biri mevcut değil.")
        for role in locked_roles:
            _require_supported_role(role, using)

        _require_user_mutation_allowed(
            actor=actor,
            target=target,
            current_ids=current_ids,
            desired_ids=desired_ids,
            role_map=role_map,
            using=using,
        )
        if current_ids == desired_ids:
            return UserRoleWriteResult(user=target, changed=False)

        before = canonical_user_snapshot(target, using=using)
        target.groups.set([role_map[pk] for pk in sorted(desired_ids)])
        after = canonical_user_snapshot(target, using=using)
        record_audit_event(
            actor=actor,
            event_type=USER_ROLES_CHANGED,
            entity_type=USER_ENTITY_TYPE,
            entity_id=user_audit_id(target),
            before_data=before,
            after_data=after,
            using=using,
        )
        return UserRoleWriteResult(user=target, changed=True)


def role_permissions_protection_reason(
    actor, role: Group, *, using="default"
) -> str | None:
    if not is_supported_role(role, using=using):
        return "Kapsam dışı izinler içerdiği için salt okunur."
    try:
        _require_role_mutation_allowed(actor, role, using)
    except PermissionDenied:
        return "Güvenlik kuralları nedeniyle bu rol korunuyor."
    return None


def role_rename_protection_reason(actor, role: Group, *, using="default") -> str | None:
    if role.name in RESERVED_ROLE_NAMES:
        return "Varsayılan roller yeniden adlandırılamaz."
    return role_permissions_protection_reason(actor, role, using=using)


def user_mutation_protection_reason(actor, target, *, using="default") -> str | None:
    current_roles = list(target.groups.using(using).order_by("pk"))
    if any(not is_supported_role(role, using=using) for role in current_roles):
        return "Kapsam dışı bir role sahip olduğu için salt okunur."
    role_map = {role.pk: role for role in current_roles}
    current_ids = set(role_map)
    try:
        _require_user_mutation_allowed(
            actor=actor,
            target=target,
            current_ids=current_ids,
            desired_ids=current_ids,
            role_map=role_map,
            using=using,
        )
    except PermissionDenied:
        return "Güvenlik kuralları nedeniyle bu kullanıcı korunuyor."
    return None


def assignable_roles_for(actor, target, *, using="default") -> list[Group]:
    if user_mutation_protection_reason(actor, target, using=using):
        return []
    roles = list(Group.objects.using(using).order_by("name", "pk"))
    supported = [role for role in roles if is_supported_role(role, using=using)]
    if actor.is_superuser:
        return supported
    actor_safe = _actor_safe_permissions(actor, using)
    return [
        role
        for role in supported
        if MANAGE_ACCESS_PERMISSION not in role_permission_labels(role, using=using)
        and (role_permission_labels(role, using=using) & SAFE_CATALOG_PERMISSION_SET)
        <= actor_safe
    ]


def _require_actor(actor, using: str):
    if actor is None or not isinstance(actor, User) or getattr(actor, "pk", None) is None:
        raise ValidationError("Actor must be persisted before managing access.")
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise ValidationError(
            "Actor must belong to the same database alias as the access mutation."
        )
    try:
        current_actor = User.objects.using(using).get(pk=actor.pk)
    except User.DoesNotExist as exc:
        raise ValidationError("Actor must exist on the access mutation database.") from exc
    if MANAGE_ACCESS_PERMISSION not in effective_permission_labels(
        current_actor, using=using
    ):
        raise PermissionDenied
    return current_actor


def _normalize_role_name(name) -> str:
    if not isinstance(name, str):
        raise ValidationError({"name": "Rol adı metin olmalıdır."})
    normalized = name.strip()
    if not normalized:
        raise ValidationError({"name": "Rol adı boş bırakılamaz."})
    return normalized


def _reject_reserved_name(name: str) -> None:
    if name in RESERVED_ROLE_NAMES:
        raise ValidationError({"name": "Bu rol adı varsayılan roller için ayrılmıştır."})


def _normalize_catalog_permission_labels(labels: Iterable[str]) -> set[str]:
    if isinstance(labels, str):
        labels = [labels]
    try:
        normalized = set(labels)
    except TypeError as exc:
        raise ValidationError("İzin seçimi geçersiz.") from exc
    if not all(isinstance(label, str) for label in normalized):
        raise ValidationError("İzin seçimi geçersiz.")
    unknown = normalized - SAFE_CATALOG_PERMISSION_SET
    if unknown:
        raise ValidationError(
            "Yalnız onaylı katalog ve lokasyon izinleri yönetilebilir."
        )
    return normalized


def _normalize_role_ids(role_ids: Iterable[int]) -> set[int]:
    if isinstance(role_ids, (str, bytes)):
        role_ids = [role_ids]
    normalized: set[int] = set()
    try:
        for value in role_ids:
            if isinstance(value, bool):
                raise ValueError
            normalized.add(int(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Rol seçimi geçersiz.") from exc
    return normalized


def _validate_write_view_dependencies(labels: set[str]) -> None:
    for write_label, view_label in WRITE_VIEW_DEPENDENCIES:
        if write_label in labels and view_label not in labels:
            raise ValidationError(
                f"{write_label} izni {view_label} izni olmadan verilemez."
            )


def _load_supported_permissions(using: str) -> dict[str, Permission]:
    permissions = Permission.objects.using(using).filter(
        content_type__app_label__in=("accounts", "catalog", "locations", "inventory")
    ).select_related("content_type")
    permission_map = {
        permission_label(permission): permission
        for permission in permissions
        if permission_label(permission) in SUPPORTED_ACCESS_PERMISSION_SET
    }
    missing = SUPPORTED_ACCESS_PERMISSION_SET - permission_map.keys()
    if missing:
        raise ValidationError(
            "Gerekli erişim izinleri veritabanında eksik: " + ", ".join(sorted(missing))
        )
    return permission_map


def _locked_role(role_id, using: str) -> Group:
    return Group.objects.using(using).select_for_update().get(pk=role_id)


def _require_supported_role(role: Group, using: str) -> None:
    if not is_supported_role(role, using=using):
        raise PermissionDenied


def _actor_safe_permissions(actor, using: str) -> set[str]:
    return effective_permission_labels(actor, using=using) & SAFE_CATALOG_PERMISSION_SET


def _require_role_mutation_allowed(actor, role: Group, using: str) -> None:
    if actor.is_superuser:
        return
    labels = role_permission_labels(role, using=using)
    actor_safe = _actor_safe_permissions(actor, using)
    if role.user_set.using(using).filter(pk=actor.pk).exists():
        raise PermissionDenied
    if MANAGE_ACCESS_PERMISSION in labels:
        raise PermissionDenied
    if (labels & SAFE_CATALOG_PERMISSION_SET) - actor_safe:
        raise PermissionDenied

    members = User.objects.using(using).filter(groups=role).distinct().order_by("pk")
    for member in members:
        if member.pk == actor.pk or member.is_superuser or member.is_staff:
            raise PermissionDenied
        if member.user_permissions.using(using).exists():
            raise PermissionDenied
        assigned = assigned_permission_labels(member, using=using)
        if MANAGE_ACCESS_PERMISSION in assigned:
            raise PermissionDenied
        if (assigned & SAFE_CATALOG_PERMISSION_SET) - actor_safe:
            raise PermissionDenied


def _require_user_mutation_allowed(
    *, actor, target, current_ids, desired_ids, role_map, using: str
) -> None:
    if target.is_superuser:
        raise PermissionDenied
    if actor.is_superuser:
        return
    if target.pk == actor.pk or target.is_staff:
        raise PermissionDenied
    if target.user_permissions.using(using).exists():
        raise PermissionDenied

    actor_safe = _actor_safe_permissions(actor, using)
    target_assigned = assigned_permission_labels(target, using=using)
    if MANAGE_ACCESS_PERMISSION in target_assigned:
        raise PermissionDenied
    if (target_assigned & SAFE_CATALOG_PERMISSION_SET) - actor_safe:
        raise PermissionDenied

    changed_ids = current_ids.symmetric_difference(desired_ids)
    for role_id in changed_ids:
        labels = role_permission_labels(role_map[role_id], using=using)
        if MANAGE_ACCESS_PERMISSION in labels:
            raise PermissionDenied

    desired_safe: set[str] = set()
    for role_id in desired_ids:
        desired_safe |= (
            role_permission_labels(role_map[role_id], using=using)
            & SAFE_CATALOG_PERMISSION_SET
        )
    if desired_safe - actor_safe:
        raise PermissionDenied


def _raise_role_name_conflict_or_reraise(exc: IntegrityError) -> None:
    cause = exc.__cause__
    constraint_name = getattr(getattr(cause, "diag", None), "constraint_name", None)
    if constraint_name == GROUP_NAME_UNIQUE_CONSTRAINT:
        raise ValidationError({"name": "Bu rol adı zaten kullanılıyor."}) from exc
    raise exc
