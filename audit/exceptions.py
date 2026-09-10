class AuditError(Exception):
    """Base error for audit foundation operations."""


class AuditValidationError(AuditError):
    """Raised when audit payload or arguments fail validation."""


class AuditImmutabilityError(AuditError):
    """Raised when an append-only audit record is modified or deleted."""
