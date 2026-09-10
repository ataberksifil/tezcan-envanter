from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth import get_user_model

from audit.exceptions import AuditValidationError
from audit.models import AuditEvent

UserModel = get_user_model()


def _normalize_entity_id(entity_id: Any) -> uuid.UUID:
    if isinstance(entity_id, uuid.UUID):
        return entity_id
    if isinstance(entity_id, str):
        try:
            return uuid.UUID(entity_id)
        except ValueError as exc:
            raise AuditValidationError("entity_id must be a valid UUID.") from exc
    raise AuditValidationError("entity_id must be a UUID or UUID string.")


def _validate_required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise AuditValidationError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise AuditValidationError(f"{field_name} must not be empty.")
    return normalized


def _validate_optional_object_payload(
    value: Any,
    field_name: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AuditValidationError(f"{field_name} must be a dict or None.")
    return value


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AuditValidationError("metadata must be a dict.")
    return value


def _validate_actor(actor, using: str) -> None:
    if actor is None:
        raise AuditValidationError("actor is required.")
    if not isinstance(actor, UserModel):
        raise AuditValidationError("actor must be a persisted user instance.")
    if actor.pk is None:
        raise AuditValidationError("actor must be saved before recording audit events.")
    actor_db = actor._state.db
    if actor_db is not None and actor_db != using:
        raise AuditValidationError(
            f"actor is persisted on database '{actor_db}' but audit event "
            f"targets '{using}'."
        )


def record_audit_event(
    *,
    actor,
    event_type: str,
    entity_type: str,
    entity_id,
    before_data: dict[str, Any] | None,
    after_data: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
    using: str = "default",
) -> AuditEvent:
    """Append one immutable administrative audit event.

    Callers must pass explicit actor, action, and target payloads. This
    function does not serialize request objects or domain model instances.
    """
    _validate_actor(actor, using)
    normalized_event_type = _validate_required_text(event_type, "event_type")
    normalized_entity_type = _validate_required_text(entity_type, "entity_type")
    normalized_entity_id = _normalize_entity_id(entity_id)
    normalized_before = _validate_optional_object_payload(before_data, "before_data")
    normalized_after = _validate_optional_object_payload(after_data, "after_data")
    normalized_metadata = _validate_metadata(metadata)

    return AuditEvent.objects.using(using).create(
        event_type=normalized_event_type,
        actor=actor,
        entity_type=normalized_entity_type,
        entity_id=normalized_entity_id,
        before_data=normalized_before,
        after_data=normalized_after,
        metadata=normalized_metadata,
    )
