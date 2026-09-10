import uuid

from django.conf import settings
from django.db import models, router
from django.db.models import CharField, F, Func, Q, Value
from django.db.models.lookups import Exact

from audit.exceptions import AuditImmutabilityError


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=128)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="audit_events",
    )
    occurred_at = models.DateTimeField(auto_now_add=True)
    entity_type = models.CharField(max_length=128)
    entity_id = models.UUIDField()
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["occurred_at"], name="audit_evt_occurred_at_idx"),
            models.Index(
                fields=["actor", "occurred_at"],
                name="audit_evt_actor_occ_idx",
            ),
            models.Index(
                fields=["entity_type", "entity_id"],
                name="audit_evt_entity_idx",
            ),
            models.Index(
                fields=["event_type", "occurred_at"],
                name="audit_evt_type_occ_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(event_type__regex=r"^\s*$"),
                name="audit_evt_event_type_ne",
            ),
            models.CheckConstraint(
                condition=~Q(entity_type__regex=r"^\s*$"),
                name="audit_evt_entity_type_ne",
            ),
            models.CheckConstraint(
                condition=Q(before_data__isnull=True)
                | Exact(
                    Func(
                        F("before_data"),
                        function="jsonb_typeof",
                        output_field=CharField(),
                    ),
                    Value("object"),
                ),
                name="audit_evt_before_data_obj",
            ),
            models.CheckConstraint(
                condition=Q(after_data__isnull=True)
                | Exact(
                    Func(
                        F("after_data"),
                        function="jsonb_typeof",
                        output_field=CharField(),
                    ),
                    Value("object"),
                ),
                name="audit_evt_after_data_obj",
            ),
            models.CheckConstraint(
                condition=Exact(
                    Func(
                        F("metadata"),
                        function="jsonb_typeof",
                        output_field=CharField(),
                    ),
                    Value("object"),
                ),
                name="audit_evt_metadata_obj",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} ({self.entity_type}:{self.entity_id})"

    def save(self, *args, **kwargs):
        if self.pk:
            using = kwargs.get("using")
            if using is None:
                using = self._state.db or router.db_for_write(
                    self.__class__,
                    instance=self,
                )
            if AuditEvent.objects.using(using).filter(pk=self.pk).exists():
                raise AuditImmutabilityError("AuditEvent records cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutabilityError("AuditEvent records cannot be deleted.")
