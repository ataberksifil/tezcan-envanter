import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class InventoryBaseline(models.Model):
    class Status(models.TextChoices):
        PREPARED = "PREPARED", "Hazırlandı"
        ESTABLISHED = "ESTABLISHED", "Kesildi"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PREPARED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="created_inventory_baselines",
        db_index=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    established_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="established_inventory_baselines",
        db_index=False,
    )
    established_at = models.DateTimeField(null=True, blank=True)
    establishment_operation_id = models.UUIDField(null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    establishment_explanation = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "inventory_baselines"
        permissions = [
            ("establish_baseline", "Can establish inventory baseline"),
        ]
        indexes = [
            models.Index(fields=["established_at"], name="imports_baseline_est_at_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["reference"],
                name="imports_baseline_reference_uniq",
            ),
            models.UniqueConstraint(
                fields=["establishment_operation_id"],
                condition=Q(establishment_operation_id__isnull=False),
                name="imports_baseline_operation_id_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(reference__regex=r"^\s*$"),
                name="imports_baseline_reference_nonblank",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["PREPARED", "ESTABLISHED"]),
                name="imports_baseline_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="PREPARED",
                        established_by__isnull=True,
                        established_at__isnull=True,
                        establishment_operation_id__isnull=True,
                        request_fingerprint__isnull=True,
                        establishment_explanation__isnull=True,
                    )
                    | Q(
                        status="ESTABLISHED",
                        established_by__isnull=False,
                        established_at__isnull=False,
                        establishment_operation_id__isnull=False,
                        request_fingerprint__isnull=False,
                        establishment_explanation__isnull=False,
                    )
                ),
                name="imports_baseline_established_shape",
            ),
            models.CheckConstraint(
                condition=(
                    Q(request_fingerprint__isnull=True)
                    | Q(request_fingerprint__regex=r"^[0-9a-f]{64}$")
                ),
                name="imports_baseline_fingerprint_hex",
            ),
        ]

    def __str__(self) -> str:
        return self.reference

    def clean(self) -> None:
        super().clean()
        if self.reference is not None:
            self.reference = self.reference.strip()
            if not self.reference:
                raise ValidationError({"reference": "Baseline referansı boş olamaz."})
        if self.establishment_explanation is not None:
            normalized = self.establishment_explanation.strip()
            if not normalized:
                self.establishment_explanation = None
            else:
                self.establishment_explanation = normalized

    def save(self, *args, **kwargs):
        if not self._state.adding:
            persisted = type(self).objects.using(self._state.db).get(pk=self.pk)
            if persisted.status == self.Status.ESTABLISHED:
                raise ValidationError("Kesilmiş envanter baseline'ı değiştirilemez.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.ESTABLISHED:
            raise ValidationError("Kesilmiş envanter baseline'ı silinemez.")
        return super().delete(*args, **kwargs)


class InventoryBaselineCountSessionLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inventory_baseline = models.ForeignKey(
        InventoryBaseline,
        on_delete=models.RESTRICT,
        related_name="count_session_links",
        db_index=False,
    )
    physical_count_session = models.ForeignKey(
        "counting.PhysicalCountSession",
        on_delete=models.RESTRICT,
        related_name="baseline_links",
        db_index=False,
    )
    required = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_baseline_count_session_links"
        constraints = [
            models.UniqueConstraint(
                fields=["inventory_baseline", "physical_count_session"],
                name="imports_baseline_session_uniq",
            ),
            models.UniqueConstraint(
                fields=["physical_count_session"],
                name="imports_baseline_session_global_uniq",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Baseline sayım oturumu bağlantısı değiştirilemez.")
        baseline = self.inventory_baseline
        if baseline is not None and baseline.status == InventoryBaseline.Status.ESTABLISHED:
            raise ValidationError(
                "Kesilmiş baseline'a sayım oturumu bağlantısı eklenemez."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        baseline = self.inventory_baseline
        if baseline is not None and baseline.status == InventoryBaseline.Status.ESTABLISHED:
            raise ValidationError(
                "Kesilmiş baseline sayım oturumu bağlantısı silinemez."
            )
        return super().delete(*args, **kwargs)


class InventoryBaselineTransactionLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inventory_baseline = models.ForeignKey(
        InventoryBaseline,
        on_delete=models.RESTRICT,
        related_name="transaction_links",
        db_index=False,
    )
    inventory_transaction = models.ForeignKey(
        "inventory.InventoryTransaction",
        on_delete=models.RESTRICT,
        related_name="baseline_result_links",
        db_index=False,
    )
    physical_count_session = models.ForeignKey(
        "counting.PhysicalCountSession",
        on_delete=models.RESTRICT,
        related_name="baseline_result_links",
        db_index=False,
    )
    scope_key = models.CharField(max_length=64)
    operation_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_baseline_transaction_links"
        constraints = [
            models.UniqueConstraint(
                fields=["inventory_transaction"],
                name="imports_baseline_tx_uniq",
            ),
            models.UniqueConstraint(
                fields=["operation_id"],
                name="imports_baseline_tx_operation_uniq",
            ),
            models.UniqueConstraint(
                fields=["inventory_baseline", "scope_key"],
                name="imports_baseline_scope_uniq",
            ),
            models.UniqueConstraint(
                fields=["physical_count_session"],
                name="imports_baseline_result_session_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(scope_key__regex=r"^\s*$"),
                name="imports_baseline_scope_key_nonblank",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Baseline sonuç bağlantısı değiştirilemez.")
        baseline = self.inventory_baseline
        if baseline is not None and baseline.status == InventoryBaseline.Status.ESTABLISHED:
            raise ValidationError("Kesilmiş baseline sonuç bağlantısı eklenemez.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        baseline = self.inventory_baseline
        if baseline is not None and baseline.status == InventoryBaseline.Status.ESTABLISHED:
            raise ValidationError("Kesilmiş baseline sonuç bağlantısı silinemez.")
        return super().delete(*args, **kwargs)
