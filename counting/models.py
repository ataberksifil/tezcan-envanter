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
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=["DRAFT", "STARTED"],
                        reconciliation_status="NOT_STARTED",
                    )
                    | Q(
                        status="COMPLETED",
                        reconciliation_status__in=["PENDING", "COMPLETED"],
                    )
                ),
                name="counting_session_recon_status_match",
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
        if self.status in {self.Status.DRAFT, self.Status.STARTED}:
            if self.reconciliation_status != self.ReconciliationStatus.NOT_STARTED:
                raise ValidationError(
                    "Tamamlanmamış sayım oturumunda mutabakat başlatılamaz."
                )
        elif self.reconciliation_status not in {
            self.ReconciliationStatus.PENDING,
            self.ReconciliationStatus.COMPLETED,
        }:
            raise ValidationError(
                "Tamamlanmış sayım oturumunun mutabakat durumu geçersiz."
            )


class PhysicalCountQuantityLine(models.Model):
    class ResolutionStatus(models.TextChoices):
        PENDING_COUNT = "PENDING_COUNT", "Sayım bekliyor"
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
        default=ResolutionStatus.PENDING_COUNT,
    )
    approved_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="approved_physical_count_quantity_lines",
        db_index=False,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_explanation = models.TextField(null=True, blank=True)
    reconciliation_transaction = models.OneToOneField(
        "inventory.InventoryTransaction",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="physical_count_quantity_result",
    )
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
                        "PENDING_COUNT",
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
                        resolution_status="PENDING_COUNT",
                    )
                    | Q(
                        counted_quantity__isnull=True,
                        counted_by_user__isnull=False,
                        counted_at__isnull=False,
                        resolution_status="NOT_COUNTED",
                    )
                    | Q(
                        counted_quantity__isnull=False,
                        counted_by_user__isnull=False,
                        counted_at__isnull=False,
                    )
                    & ~Q(
                        resolution_status__in=["PENDING_COUNT", "NOT_COUNTED"]
                    )
                ),
                name="counting_qline_count_state_match",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        resolution_status="APPROVED",
                        approved_by_user__isnull=False,
                        approved_at__isnull=False,
                        approval_explanation__isnull=False,
                        reconciliation_transaction__isnull=False,
                    )
                    | Q(
                        ~Q(resolution_status="APPROVED"),
                        approved_by_user__isnull=True,
                        approved_at__isnull=True,
                        approval_explanation__isnull=True,
                        reconciliation_transaction__isnull=True,
                    )
                ),
                name="counting_qline_approval_shape",
            ),
            models.CheckConstraint(
                condition=~Q(
                    resolution_status="APPROVED",
                    approved_by_user=models.F("counted_by_user"),
                ),
                name="counting_qline_no_self_approval",
            ),
        ]
        permissions = [
            ("decide_discrepancy", "Can approve or reject count discrepancy"),
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
        if self.resolution_status == self.ResolutionStatus.PENDING_COUNT:
            if (
                self.counted_quantity is not None
                or self.counted_by_user_id is not None
                or self.counted_at is not None
            ):
                raise ValidationError(
                    "İşlenmemiş satırda fiziksel miktar, sayaç veya sayım zamanı bulunamaz."
                )
        elif self.resolution_status == self.ResolutionStatus.NOT_COUNTED:
            if self.counted_quantity is not None:
                raise ValidationError(
                    "Açıkça sayılmadı işaretlenen satırda fiziksel miktar bulunamaz."
                )
            if self.counted_by_user_id is None or self.counted_at is None:
                raise ValidationError(
                    "Açıkça sayılmadı işaretlenen satırda aktör ve zaman zorunludur."
                )
        elif not count_metadata_present:
            raise ValidationError(
                "Sayılmış satırda fiziksel miktar, sayaç ve sayım zamanı zorunludur."
            )

        if self.resolution_status == self.ResolutionStatus.APPROVED:
            explanation = (self.approval_explanation or "").strip()
            if not 10 <= len(explanation) <= 2000:
                raise ValidationError(
                    {"approval_explanation": "Onay açıklaması 10 ile 2000 karakter arasında olmalıdır."}
                )
            self.approval_explanation = explanation
            if (
                self.approved_by_user_id is None
                or self.approved_at is None
                or self.reconciliation_transaction_id is None
            ):
                raise ValidationError("Onay aktörü, zamanı ve mutabakat işlemi zorunludur.")
        elif any(
            value is not None
            for value in (
                self.approved_by_user_id,
                self.approved_at,
                self.approval_explanation,
                self.reconciliation_transaction_id,
            )
        ):
            raise ValidationError("Onay metadata'sı yalnız APPROVED satırda bulunabilir.")


class PhysicalCountSerializedLine(models.Model):
    class ResolutionStatus(models.TextChoices):
        PENDING_COUNT = "PENDING_COUNT", "Sayım bekliyor"
        NOT_COUNTED = "NOT_COUNTED", "Sayılmadı"
        NO_DISCREPANCY = "NO_DISCREPANCY", "Fark yok"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Onay bekliyor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        PhysicalCountSession,
        on_delete=models.RESTRICT,
        related_name="serialized_lines",
        db_index=False,
    )
    serialized_asset = models.ForeignKey(
        "inventory.SerializedAsset",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="physical_count_serialized_lines",
        db_index=False,
    )
    material = models.ForeignKey(
        "catalog.Material",
        on_delete=models.RESTRICT,
        related_name="physical_count_serialized_lines",
        db_index=False,
    )
    internal_asset_code = models.CharField(max_length=64)
    serial_number = models.CharField(max_length=255, null=True, blank=True)
    expected_present = models.BooleanField()
    expected_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="expected_physical_count_serialized_lines",
        db_index=False,
    )
    expected_condition = models.ForeignKey(
        "catalog.MaterialCondition",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="expected_physical_count_serialized_lines",
        db_index=False,
    )
    observed_present = models.BooleanField(null=True, blank=True)
    observed_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="observed_physical_count_serialized_lines",
        db_index=False,
    )
    observed_condition = models.ForeignKey(
        "catalog.MaterialCondition",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="observed_physical_count_serialized_lines",
        db_index=False,
    )
    counted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="physical_count_serialized_lines",
        db_index=False,
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    resolution_status = models.CharField(
        max_length=20,
        choices=ResolutionStatus.choices,
        default=ResolutionStatus.PENDING_COUNT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["material"],
                name="counting_sline_material_idx",
            ),
            models.Index(
                fields=["serialized_asset"],
                name="counting_sline_asset_idx",
            ),
            models.Index(
                fields=["resolution_status"],
                name="counting_sline_resolution_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "internal_asset_code"],
                name="counting_sline_session_code_uniq",
            ),
            models.UniqueConstraint(
                fields=["session", "serialized_asset"],
                condition=Q(serialized_asset__isnull=False),
                name="counting_sline_session_asset_uniq",
            ),
            models.UniqueConstraint(
                fields=["session", "material", "serial_number"],
                condition=Q(serial_number__isnull=False),
                name="counting_sline_session_serial_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(internal_asset_code__regex=r"^\s*$"),
                name="counting_sline_code_nonblank",
            ),
            models.CheckConstraint(
                condition=Q(serial_number__isnull=True)
                | ~Q(serial_number__regex=r"^\s*$"),
                name="counting_sline_serial_null_or_nonblank",
            ),
            models.CheckConstraint(
                condition=Q(
                    resolution_status__in=[
                        "PENDING_COUNT",
                        "NOT_COUNTED",
                        "NO_DISCREPANCY",
                        "PENDING_APPROVAL",
                    ]
                ),
                name="counting_sline_resolution_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        expected_present=True,
                        serialized_asset__isnull=False,
                        expected_location__isnull=False,
                        expected_condition__isnull=False,
                    )
                    | Q(
                        expected_present=False,
                        expected_location__isnull=True,
                        expected_condition__isnull=True,
                    )
                ),
                name="counting_sline_expected_shape",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        resolution_status="PENDING_COUNT",
                        expected_present=True,
                        serialized_asset__isnull=False,
                        expected_location__isnull=False,
                        expected_condition__isnull=False,
                        observed_present__isnull=True,
                        observed_location__isnull=True,
                        observed_condition__isnull=True,
                        counted_by_user__isnull=True,
                        counted_at__isnull=True,
                    )
                    | Q(
                        resolution_status="NOT_COUNTED",
                        expected_present=True,
                        serialized_asset__isnull=False,
                        expected_location__isnull=False,
                        expected_condition__isnull=False,
                        observed_present__isnull=True,
                        observed_location__isnull=True,
                        observed_condition__isnull=True,
                        counted_by_user__isnull=False,
                        counted_at__isnull=False,
                    )
                    | Q(
                        resolution_status="PENDING_APPROVAL",
                        expected_present=True,
                        serialized_asset__isnull=False,
                        expected_location__isnull=False,
                        expected_condition__isnull=False,
                        observed_present=False,
                        observed_location__isnull=True,
                        observed_condition__isnull=True,
                        counted_by_user__isnull=False,
                        counted_at__isnull=False,
                    )
                    | Q(
                        resolution_status="NO_DISCREPANCY",
                        expected_present=True,
                        serialized_asset__isnull=False,
                        expected_location__isnull=False,
                        expected_condition__isnull=False,
                        observed_present=True,
                        observed_location=models.F("expected_location"),
                        observed_condition=models.F("expected_condition"),
                        counted_by_user__isnull=False,
                        counted_at__isnull=False,
                    )
                    | (
                        Q(
                            resolution_status="PENDING_APPROVAL",
                            expected_present=True,
                            serialized_asset__isnull=False,
                            expected_location__isnull=False,
                            expected_condition__isnull=False,
                            observed_present=True,
                            observed_location__isnull=False,
                            observed_condition__isnull=False,
                            counted_by_user__isnull=False,
                            counted_at__isnull=False,
                        )
                        & ~Q(
                            observed_location=models.F("expected_location"),
                            observed_condition=models.F("expected_condition"),
                        )
                    )
                    | Q(
                        resolution_status="PENDING_APPROVAL",
                        expected_present=False,
                        expected_location__isnull=True,
                        expected_condition__isnull=True,
                        observed_present=True,
                        observed_location__isnull=False,
                        observed_condition__isnull=False,
                        counted_by_user__isnull=False,
                        counted_at__isnull=False,
                    )
                ),
                name="counting_sline_state_shape",
            ),
        ]

    def __str__(self) -> str:
        return self.internal_asset_code

    def clean(self) -> None:
        super().clean()

        if self.internal_asset_code is not None:
            self.internal_asset_code = self.internal_asset_code.strip()
            if not self.internal_asset_code:
                raise ValidationError(
                    {"internal_asset_code": "Dahili varlık kodu boş olamaz."}
                )
        if self.serial_number is not None:
            normalized_serial = self.serial_number.strip()
            self.serial_number = normalized_serial or None

        if self.material_id is not None:
            from catalog.models import Material

            if self.material.tracking_mode != Material.TrackingMode.SERIALIZED:
                raise ValidationError(
                    {"material": "Tekil sayım satırı için SERIALIZED malzeme zorunludur."}
                )

        if self.serialized_asset_id is not None:
            if self.serialized_asset.material_id != self.material_id:
                raise ValidationError(
                    "Sayım satırı malzeme kimliği bağlı tekil varlıkla aynı olmalıdır."
                )
            if self.serialized_asset.internal_asset_code != self.internal_asset_code:
                raise ValidationError(
                    "Sayım satırı dahili varlık kodu bağlı tekil varlıkla aynı olmalıdır."
                )
            if self.serialized_asset.serial_number != self.serial_number:
                raise ValidationError(
                    "Sayım satırı üretici seri numarası bağlı tekil varlıkla aynı olmalıdır."
                )

        if self.expected_present:
            if (
                self.serialized_asset_id is None
                or self.expected_location_id is None
                or self.expected_condition_id is None
            ):
                raise ValidationError(
                    "Beklenen tekil sayım satırında varlık, lokasyon ve kondisyon zorunludur."
                )
        elif self.expected_location_id is not None or self.expected_condition_id is not None:
            raise ValidationError(
                "Beklenmeyen tekil sayım satırında expected lokasyon veya kondisyon bulunamaz."
            )

        actor_and_time = (
            self.counted_by_user_id is not None and self.counted_at is not None
        )
        if self.resolution_status == self.ResolutionStatus.PENDING_COUNT:
            if (
                self.observed_present is not None
                or self.observed_location_id is not None
                or self.observed_condition_id is not None
                or self.counted_by_user_id is not None
                or self.counted_at is not None
            ):
                raise ValidationError(
                    "İşlenmemiş tekil satırda gözlem, aktör veya zaman bulunamaz."
                )
            if not self.expected_present:
                raise ValidationError(
                    "Beklenmeyen tekil satır PENDING_COUNT olamaz."
                )
        elif self.resolution_status == self.ResolutionStatus.NOT_COUNTED:
            if self.observed_present is not None or not actor_and_time:
                raise ValidationError(
                    "Açıkça sayılmadı işaretlenen tekil satırda aktör ve zaman zorunludur."
                )
            if not self.expected_present:
                raise ValidationError(
                    "Beklenmeyen tekil satır sayılmadı olarak işaretlenemez."
                )
        elif self.observed_present is True:
            if (
                self.observed_location_id is None
                or self.observed_condition_id is None
                or not actor_and_time
            ):
                raise ValidationError(
                    "Fiziksel bulunan tekil satırda gözlenen lokasyon, kondisyon, aktör ve zaman zorunludur."
                )
            matched = (
                self.expected_present
                and self.observed_location_id == self.expected_location_id
                and self.observed_condition_id == self.expected_condition_id
            )
            if self.resolution_status == self.ResolutionStatus.NO_DISCREPANCY:
                if not matched:
                    raise ValidationError(
                        "Fark yok durumu yalnız beklenen lokasyon ve kondisyonda geçerlidir."
                    )
            elif self.resolution_status != self.ResolutionStatus.PENDING_APPROVAL:
                raise ValidationError("Tekil sayım çözüm durumu geçersiz.")
            elif matched:
                raise ValidationError(
                    "Beklenen lokasyon ve kondisyondaki gözlem PENDING_APPROVAL olamaz."
                )
        elif self.observed_present is False:
            if (
                not self.expected_present
                or self.observed_location_id is not None
                or self.observed_condition_id is not None
                or not actor_and_time
                or self.resolution_status != self.ResolutionStatus.PENDING_APPROVAL
            ):
                raise ValidationError(
                    "Eksik tekil varlık yalnız beklenen satırda ve onay bekleyen fark olarak kaydedilir."
                )
        else:
            raise ValidationError("Tekil sayım gözlem durumu geçersiz.")


class PhysicalCountQuantityRejection(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(
        PhysicalCountQuantityLine,
        on_delete=models.RESTRICT,
        related_name="rejections",
    )
    rejected_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="rejected_physical_count_quantity_lines",
    )
    rejected_at = models.DateTimeField()
    reason = models.TextField(null=True, blank=True)
    counted_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    counted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="rejected_count_quantity_snapshots",
    )
    counted_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(counted_quantity__gte=Decimal("0")),
                name="counting_qreject_counted_nonneg",
            ),
            models.CheckConstraint(
                condition=Q(reason__isnull=True) | ~Q(reason__regex=r"^\s*$"),
                name="counting_qreject_reason_nonblank",
            ),
        ]
