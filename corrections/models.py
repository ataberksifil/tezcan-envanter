import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import models
from django.db.models import Q


class CorrectionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Bekliyor"
        APPROVED = "APPROVED", "Onaylandı"
        REJECTED = "REJECTED", "Reddedildi"

    class EffectType(models.TextChoices):
        QUANTITY = "QUANTITY", "Miktar düzeltmesi"
        IDENTITY = "IDENTITY", "Kimlik düzeltmesi"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_transaction = models.ForeignKey(
        "inventory.InventoryTransaction",
        on_delete=models.RESTRICT,
        related_name="correction_requests",
    )
    original_line = models.ForeignKey(
        "inventory.InventoryTransactionLine",
        on_delete=models.RESTRICT,
        related_name="correction_requests",
    )
    original_location = models.ForeignKey(
        "locations.Location",
        on_delete=models.RESTRICT,
        related_name="correction_requests_as_original_bucket",
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="requested_corrections",
    )
    explanation = models.TextField(
        validators=[MinLengthValidator(10), MaxLengthValidator(2000)]
    )
    effect_type = models.CharField(max_length=16, choices=EffectType.choices)
    quantity_effect = models.DecimalField(max_digits=18, decimal_places=3)
    corrected_material = models.ForeignKey(
        "catalog.Material",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="correction_requests_as_corrected_material",
    )
    corrected_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="correction_requests_as_corrected_location",
    )
    corrected_condition = models.ForeignKey(
        "catalog.MaterialCondition",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="correction_requests_as_corrected_condition",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="decided_corrections",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    resulting_transaction = models.OneToOneField(
        "inventory.InventoryTransaction",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="resulting_correction_request",
    )
    rejection_reason = models.TextField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ("add", "view")
        permissions = [
            ("decide_correctionrequest", "Can approve or reject correction request"),
        ]
        indexes = [
            models.Index(
                fields=["status", "requested_at"],
                name="correction_status_req_idx",
            ),
            models.Index(
                fields=["requester", "requested_at"],
                name="correction_requester_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["original_transaction"],
                condition=Q(status="PENDING"),
                name="correction_one_pending_per_tx",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["PENDING", "APPROVED", "REJECTED"]),
                name="correction_status_supported",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="PENDING",
                        decided_by__isnull=True,
                        decided_at__isnull=True,
                        resulting_transaction__isnull=True,
                    )
                    | Q(
                        status="APPROVED",
                        decided_by__isnull=False,
                        decided_at__isnull=False,
                        resulting_transaction__isnull=False,
                    )
                    | Q(
                        status="REJECTED",
                        decided_by__isnull=False,
                        decided_at__isnull=False,
                        resulting_transaction__isnull=True,
                    )
                ),
                name="correction_decision_state_complete",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        effect_type="QUANTITY",
                        corrected_material__isnull=True,
                        corrected_location__isnull=True,
                        corrected_condition__isnull=True,
                    )
                    & ~Q(quantity_effect=Decimal("0"))
                    | Q(
                        effect_type="IDENTITY",
                        quantity_effect__gt=Decimal("0"),
                        corrected_material__isnull=False,
                        corrected_location__isnull=False,
                        corrected_condition__isnull=False,
                    )
                ),
                name="correction_effect_shape",
            ),
        ]

    def clean(self):
        super().clean()
        if isinstance(self.explanation, str):
            self.explanation = self.explanation.strip()
        if self.requester_id is not None and self.requester_id == self.decided_by_id:
            raise ValidationError("Talep eden kullanıcı kendi talebinde karar veremez.")

    def delete(self, *args, **kwargs):
        raise ValidationError("Düzeltme talebi silinemez.")
