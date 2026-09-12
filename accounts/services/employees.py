from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.models import Employee
from audit.services import record_audit_event

User = get_user_model()

EMPLOYEE_ENTITY_TYPE = "accounts.employee"
EMPLOYEE_CREATED = "accounts.employee.created"
EMPLOYEE_UPDATED = "accounts.employee.updated"
EMPLOYEE_DEACTIVATED = "accounts.employee.deactivated"
EMPLOYEE_REACTIVATED = "accounts.employee.reactivated"

VIEW_EMPLOYEE_PERMISSION = "accounts.view_employee"
ADD_EMPLOYEE_PERMISSION = "accounts.add_employee"
CHANGE_EMPLOYEE_PERMISSION = "accounts.change_employee"

DUPLICATE_EMPLOYEE_NUMBER_MESSAGE = "Bu sicil numarası zaten kullanılıyor."
DUPLICATE_USER_LINK_MESSAGE = "Bu kullanıcı başka bir çalışana bağlı."
EMPLOYEE_NUMBER_UNIQUE_CONSTRAINT = "accounts_employee_number_uniq"
EMPLOYEE_USER_UNIQUE_CONSTRAINT = "accounts_employee_user_id_key"


@dataclass(frozen=True)
class EmployeeWriteResult:
    employee: Employee
    changed: bool


def canonical_employee_snapshot(employee: Employee) -> dict[str, Any]:
    return {
        "id": str(employee.id),
        "employee_number": employee.employee_number,
        "first_name": employee.first_name,
        "last_name": employee.last_name,
        "user_id": employee.user_id,
        "active": bool(employee.active),
    }


def create_employee(
    *,
    actor,
    employee_number,
    first_name,
    last_name,
    user_id=None,
    using: str = "default",
) -> EmployeeWriteResult:
    _require_permission(actor, ADD_EMPLOYEE_PERMISSION, using)
    _ensure_actor_database(actor, using)

    normalized_user_id = _normalize_user_id(user_id)
    with transaction.atomic(using=using):
        linked_user = _load_and_validate_user_link(
            user_id=normalized_user_id,
            employee_id=None,
            using=using,
        )
        employee = Employee(
            employee_number=_normalize_employee_number(employee_number),
            first_name=_normalize_person_name(first_name, "first_name", "Ad boş olamaz."),
            last_name=_normalize_person_name(last_name, "last_name", "Soyad boş olamaz."),
            user=linked_user,
            active=True,
        )
        employee._state.db = using
        _validate_employee(employee)
        _save_employee(employee, using=using)
        record_audit_event(
            actor=actor,
            event_type=EMPLOYEE_CREATED,
            entity_type=EMPLOYEE_ENTITY_TYPE,
            entity_id=employee.id,
            before_data=None,
            after_data=canonical_employee_snapshot(employee),
            using=using,
        )
        return EmployeeWriteResult(employee=employee, changed=True)


def update_employee(
    *,
    actor,
    employee_id,
    employee_number,
    first_name,
    last_name,
    user_id=None,
    using: str = "default",
) -> EmployeeWriteResult:
    _require_permission(actor, CHANGE_EMPLOYEE_PERMISSION, using)
    _ensure_actor_database(actor, using)

    normalized_user_id = _normalize_user_id(user_id)
    with transaction.atomic(using=using):
        employee = _locked_employee(employee_id, using)
        before = canonical_employee_snapshot(employee)

        linked_user = _load_and_validate_user_link(
            user_id=normalized_user_id,
            employee_id=employee.pk,
            using=using,
        )
        employee.employee_number = _normalize_employee_number(employee_number)
        employee.first_name = _normalize_person_name(
            first_name, "first_name", "Ad boş olamaz."
        )
        employee.last_name = _normalize_person_name(
            last_name, "last_name", "Soyad boş olamaz."
        )
        employee.user = linked_user

        _validate_employee(employee)
        after = canonical_employee_snapshot(employee)
        if before == after:
            return EmployeeWriteResult(employee=employee, changed=False)

        _save_employee(
            employee,
            using=using,
            update_fields=[
                "employee_number",
                "first_name",
                "last_name",
                "user",
                "updated_at",
            ],
        )
        record_audit_event(
            actor=actor,
            event_type=EMPLOYEE_UPDATED,
            entity_type=EMPLOYEE_ENTITY_TYPE,
            entity_id=employee.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return EmployeeWriteResult(employee=employee, changed=True)


def set_employee_active(
    *,
    actor,
    employee_id,
    active: bool,
    using: str = "default",
) -> EmployeeWriteResult:
    _require_permission(actor, CHANGE_EMPLOYEE_PERMISSION, using)
    _ensure_actor_database(actor, using)

    target_active = bool(active)
    with transaction.atomic(using=using):
        employee = _locked_employee(employee_id, using)
        if employee.active == target_active:
            return EmployeeWriteResult(employee=employee, changed=False)

        before = canonical_employee_snapshot(employee)
        employee.active = target_active
        _validate_employee(employee)
        employee.save(using=using, update_fields=["active", "updated_at"])
        after = canonical_employee_snapshot(employee)
        record_audit_event(
            actor=actor,
            event_type=(
                EMPLOYEE_REACTIVATED if target_active else EMPLOYEE_DEACTIVATED
            ),
            entity_type=EMPLOYEE_ENTITY_TYPE,
            entity_id=employee.id,
            before_data=before,
            after_data=after,
            using=using,
        )
        return EmployeeWriteResult(employee=employee, changed=True)


def _require_permission(actor, permission: str, using: str) -> None:
    if actor is None or getattr(actor, "pk", None) is None:
        raise PermissionDenied
    if not getattr(actor, "is_active", False):
        raise PermissionDenied
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise PermissionDenied
    if not actor.has_perm(permission):
        raise PermissionDenied


def _ensure_actor_database(actor, using: str) -> None:
    if getattr(actor, "pk", None) is None:
        raise ValidationError("Actor must be saved before mutating employees.")
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise ValidationError(
            "Actor must belong to the same database alias as the Employee mutation."
        )


def _normalize_user_id(user_id):
    if user_id in (None, ""):
        return None
    return user_id


def _normalize_employee_number(value) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            {"employee_number": "Sicil numarası boş olamaz."}
        )
    normalized = value.strip()
    if not normalized:
        raise ValidationError(
            {"employee_number": "Sicil numarası boş olamaz."}
        )
    return normalized


def _normalize_person_name(value, field_name: str, blank_message: str) -> str:
    if not isinstance(value, str):
        raise ValidationError({field_name: blank_message})
    normalized = value.strip()
    if not normalized:
        raise ValidationError({field_name: blank_message})
    return normalized


def _locked_employee(employee_id, using: str) -> Employee:
    return Employee.objects.using(using).select_for_update().get(pk=employee_id)


def _validate_employee(employee: Employee) -> None:
    employee.full_clean()


def _load_and_validate_user_link(*, user_id, employee_id, using: str):
    if user_id is None:
        return None
    try:
        user = User.objects.using(using).get(pk=user_id)
    except User.DoesNotExist as exc:
        raise ValidationError({"user": "Seçilen kullanıcı bulunamadı."}) from exc

    linked_elsewhere = (
        Employee.objects.using(using)
        .filter(user_id=user_id)
        .exclude(pk=employee_id)
        .exists()
    )
    if linked_elsewhere:
        raise ValidationError({"user": DUPLICATE_USER_LINK_MESSAGE})
    return user


def _is_duplicate_employee_number_error(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    if cause is None:
        return False
    diag = getattr(cause, "diag", None)
    if diag is None:
        return False
    return diag.constraint_name == EMPLOYEE_NUMBER_UNIQUE_CONSTRAINT


def _is_duplicate_user_link_error(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    if cause is None:
        return False
    diag = getattr(cause, "diag", None)
    if diag is None:
        return False
    return diag.constraint_name == EMPLOYEE_USER_UNIQUE_CONSTRAINT


def _save_employee(
    employee: Employee,
    *,
    using: str,
    update_fields: list[str] | None = None,
) -> None:
    try:
        with transaction.atomic(using=using):
            if update_fields is not None:
                employee.save(using=using, update_fields=update_fields)
            else:
                employee.save(using=using)
    except IntegrityError as exc:
        if _is_duplicate_employee_number_error(exc):
            raise ValidationError(
                {"employee_number": DUPLICATE_EMPLOYEE_NUMBER_MESSAGE}
            ) from exc
        if _is_duplicate_user_link_error(exc):
            raise ValidationError({"user": DUPLICATE_USER_LINK_MESSAGE}) from exc
        raise
