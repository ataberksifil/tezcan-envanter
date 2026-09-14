import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class PhysicalCountSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Taslak"
        STARTED = "STARTED", "Başlatıldı"
        COMPLETED = "COMPLETED", "Tamamlandı"

    class ReconciliationStatus(models.TextChoices):
        NOT_STARTED = "NOT_STARTED", "Başlatılmadı"
        PENDING = "PENDING", "Bekliyor"
        COMPLETED = "COMPLETED", "Tamamlandı"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference_number = models.CharField(max_length=64)
    scope_location = models.ForeignKey(
        "locations.Location",
        on_delete=models.RESTRICT,
        related_name="physical_count_sessions",
        db_index=False,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    reconciliation_status = models.CharField(
        max_length=16,
        choices=ReconciliationStatus.choices,
        default=ReconciliationStatus.NOT_STARTED,
    )
    started_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="started_physical_count_sessions",
        db_index=False,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="completed_physical_count_sessions",
        db_index=False,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    baseline_candidate = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"], name="counting_session_status_idx"),
            models.Index(
                fields=["reconciliation_status"],
                name="counting_session_recon_idx",
            ),
            models.Index(
                fields=["scope_location"],
                name="counting_session_scope_idx",
            ),
            models.Index(
                fields=["started_at"],
                name="counting_session_started_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["reference_number"],
                name="counting_session_reference_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(reference_number__regex=r"^\s*$"),
                name="counting_session_reference_nonblank",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["DRAFT", "STARTED", "COMPLETED"]),
                name="counting_session_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    reconciliation_status__in=[
                        "NOT_STARTED",
                        "PENDING",
                        "COMPLETED",
                    ]
                ),
                name="counting_session_recon_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="DRAFT",
                        started_by_user__isnull=True,
                        started_at__isnull=True,
                    )
                    | Q(
                        status__in=["STARTED", "COMPLETED"],
                        started_by_user__isnull=False,
                        started_at__isnull=False,
                    )
                ),
                name="counting_session_started_shape",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="COMPLETED",
                        completed_by_user__isnull=False,
                        completed_at__isnull=False,
                    )
                    | Q(
                        status__in=["DRAFT", "STARTED"],
                        completed_by_user__isnull=True,
                        completed_at__isnull=True,
                    )
                ),
                name="counting_session_completed_shape",
            ),
        ]

    def __str__(self) -> str:
        return self.reference_number

    def clean(self) -> None:
        super().clean()

        if self.reference_number is not None:
            self.reference_number = self.reference_number.strip()
            if not self.reference_number:
                raise ValidationError(
                    {"reference_number": "Sayım referansı boş olamaz."}
                )

        start_metadata_present = (
            self.started_by_user_id is not None and self.started_at is not None
        )
        if self.status == self.Status.DRAFT and (
            self.started_by_user_id is not None or self.started_at is not None
        ):
            raise ValidationError(
                "Taslak sayım oturumunda başlangıç aktörü ve zamanı bulunamaz."
            )
        if self.status in {self.Status.STARTED, self.Status.COMPLETED} and not (
            start_metadata_present
        ):
            raise ValidationError(
                "Başlatılmış sayım oturumunda başlangıç aktörü ve zamanı zorunludur."
            )

        completion_metadata_present = (
            self.completed_by_user_id is not None and self.completed_at is not None
        )
        if self.status == self.Status.COMPLETED and not completion_metadata_present:
            raise ValidationError(
                "Tamamlanmış sayım oturumunda tamamlayan aktör ve zaman zorunludur."
            )
        if self.status != self.Status.COMPLETED and (
            self.completed_by_user_id is not None or self.completed_at is not None
        ):
            raise ValidationError(
                "Tamamlanmamış sayım oturumunda tamamlama aktörü ve zamanı bulunamaz."
            )


class PhysicalCountQuantityLine(models.Model):
    class ResolutionStatus(models.TextChoices):
        NOT_COUNTED = "NOT_COUNTED", "Sayılmadı"
        NO_DISCREPANCY = "NO_DISCREPANCY", "Fark yok"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Onay bekliyor"
        APPROVED = "APPROVED", "Onaylandı"
        DISPOSITIONED = "DISPOSITIONED", "Sonuçlandırıldı"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        PhysicalCountSession,
        on_delete=models.RESTRICT,
        related_name="quantity_lines",
        db_index=False,
    )
    material = models.ForeignKey(
        "catalog.Material",
        on_delete=models.RESTRICT,
        related_name="physical_count_quantity_lines",
        db_index=False,
    )
    location = models.ForeignKey(
        "locations.Location",
        on_delete=models.RESTRICT,
        related_name="physical_count_quantity_lines",
        db_index=False,
    )
    condition = models.ForeignKey(
        "catalog.MaterialCondition",
        on_delete=models.RESTRICT,
        related_name="physical_count_quantity_lines",
        db_index=False,
    )
    expected_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
    )
    counted_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    counted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="physical_count_quantity_lines",
        db_index=False,
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    resolution_status = models.CharField(
        max_length=20,
        choices=ResolutionStatus.choices,
        default=ResolutionStatus.NOT_COUNTED,
    )
    approved_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="approved_physical_count_quantity_lines",
        db_index=False,
    )
    approval_explanation = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["material"],
                name="counting_qline_material_idx",
            ),
            models.Index(
                fields=["location"],
                name="counting_qline_location_idx",
            ),
            models.Index(
                fields=["resolution_status"],
                name="counting_qline_resolution_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "material", "location", "condition"],
                name="counting_qline_bucket_uniq",
            ),
            models.CheckConstraint(
                condition=Q(expected_quantity__gte=Decimal("0")),
                name="counting_qline_expected_nonneg",
            ),
            models.CheckConstraint(
                condition=(
                    Q(counted_quantity__isnull=True)
                    | Q(counted_quantity__gte=Decimal("0"))
                ),
                name="counting_qline_counted_nonneg",
            ),
            models.CheckConstraint(
                condition=Q(
                    resolution_status__in=[
                        "NOT_COUNTED",
                        "NO_DISCREPANCY",
                        "PENDING_APPROVAL",
                        "APPROVED",
                        "DISPOSITIONED",
                    ]
                ),
                name="counting_qline_resolution_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        counted_quantity__isnull=True,
                        counted_by_user__isnull=True,
                        counted_at__isnull=True,
                        resolution_status="NOT_COUNTED",
                    )
                    | Q(
                        counted_quantity__isnull=False,
                        counted_by_user__isnull=False,
                        counted_at__isnull=False,
                    )
                    & ~Q(resolution_status="NOT_COUNTED")
                ),
                name="counting_qline_count_state_match",
            ),
        ]

    def clean(self) -> None:
        super().clean()

        if self.material_id is not None:
            from catalog.models import Material

            if self.material.tracking_mode != Material.TrackingMode.QUANTITY:
                raise ValidationError(
                    {"material": "Miktar sayım satırı için QUANTITY malzeme zorunludur."}
                )

        count_metadata_present = (
            self.counted_quantity is not None
            and self.counted_by_user_id is not None
            and self.counted_at is not None
        )
        if self.resolution_status == self.ResolutionStatus.NOT_COUNTED:
            if (
                self.counted_quantity is not None
                or self.counted_by_user_id is not None
                or self.counted_at is not None
            ):
                raise ValidationError(
                    "Sayılmamış satırda fiziksel miktar, sayaç veya sayım zamanı bulunamaz."
                )
        elif not count_metadata_present:
            raise ValidationError(
                "Sayılmış satırda fiziksel miktar, sayaç ve sayım zamanı zorunludur."
            )
