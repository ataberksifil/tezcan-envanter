import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class ProductionLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="children",
        db_index=False,
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["parent"], name="inventory_pl_parent_idx"),
            models.Index(fields=["name"], name="inventory_pl_name_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                name="inventory_productionline_code_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(code__regex=r"^\s*$"),
                name="inventory_productionline_code_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(name__regex=r"^\s*$"),
                name="inventory_productionline_name_nonblank",
            ),
            models.CheckConstraint(
                condition=Q(parent__isnull=True) | ~Q(parent_id=F("id")),
                name="inventory_productionline_parent_not_self",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()

        if self.code is not None:
            self.code = self.code.strip()
            if not self.code:
                raise ValidationError({"code": "Üretim hattı kodu boş olamaz."})

        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({"name": "Üretim hattı adı boş olamaz."})

        if self.parent_id is not None and self.pk is not None and self.parent_id == self.pk:
            raise ValidationError(
                {"parent": "Üretim hattı kendi üst hattı olamaz."}
            )

        if self.parent_id is not None:
            ancestor = self.parent
            visited: set[uuid.UUID] = set()
            if self.pk is not None:
                visited.add(self.pk)
            while ancestor is not None:
                if ancestor.pk in visited:
                    raise ValidationError(
                        {"parent": "Üretim hattı hiyerarşisinde döngü oluşturulamaz."}
                    )
                visited.add(ancestor.pk)
                ancestor = ancestor.parent


class InventoryTransaction(models.Model):
    class TransactionType(models.TextChoices):
        RECEIPT = "RECEIPT", "Stok girişi"
        ISSUE = "ISSUE", "Stok çıkışı"
        RETURN = "RETURN", "Stok iadesi"
        TRANSFER = "TRANSFER", "Stok transferi"
        CONTROLLED_CORRECTION = "CONTROLLED_CORRECTION", "Kontrollü düzeltme"
        COUNT_RECONCILIATION = "COUNT_RECONCILIATION", "Sayım mutabakatı"
        INITIAL_BALANCE = "INITIAL_BALANCE", "Açılış bakiyesi"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation_id = models.UUIDField()
    request_fingerprint = models.CharField(max_length=64)
    transaction_type = models.CharField(
        max_length=32,
        choices=TransactionType.choices,
    )
    acting_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="inventory_transactions",
        db_index=False,
    )
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("receive_stock", "Can receive stock"),
            ("issue_stock", "Can issue stock"),
            ("return_stock", "Can return stock"),
            ("transfer_stock", "Can transfer stock"),
        ]
        indexes = [
            models.Index(fields=["occurred_at"], name="inventory_tx_occurred_idx"),
            models.Index(
                fields=["acting_user", "occurred_at"],
                name="inventory_tx_actor_occ_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["operation_id"],
                name="inventory_tx_operation_id_uniq",
            ),
            models.CheckConstraint(
                condition=Q(request_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="inventory_tx_fingerprint_hex",
            ),
            models.CheckConstraint(
                condition=Q(
                    transaction_type__in=[
                        "RECEIPT",
                        "ISSUE",
                        "RETURN",
                        "TRANSFER",
                        "CONTROLLED_CORRECTION",
                        "COUNT_RECONCILIATION",
                        "INITIAL_BALANCE",
                    ]
                ),
                name="inventory_tx_type_supported",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                "Tamamlanmış envanter işlemi değiştirilemez."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Tamamlanmış envanter işlemi silinemez.")


class SerializedAsset(models.Model):
    class CurrentState(models.TextChoices):
        IN_STOCK = "IN_STOCK", "Stokta"
        ISSUED = "ISSUED", "Çıkış yapılmış"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material = models.ForeignKey(
        "catalog.Material",
        on_delete=models.RESTRICT,
        related_name="serialized_assets",
        db_index=False,
    )
    internal_asset_code = models.CharField(max_length=64)
    serial_number = models.CharField(max_length=255, null=True, blank=True)
    current_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="current_serialized_assets",
        db_index=False,
    )
    current_condition = models.ForeignKey(
        "catalog.MaterialCondition",
        on_delete=models.RESTRICT,
        related_name="current_serialized_assets",
        db_index=False,
    )
    current_state = models.CharField(
        max_length=16,
        choices=CurrentState.choices,
        default=CurrentState.IN_STOCK,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["material"], name="inventory_asset_material_idx"),
            models.Index(
                fields=["current_location"],
                name="inventory_asset_location_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["internal_asset_code"],
                name="inventory_asset_internal_code_uniq",
            ),
            models.UniqueConstraint(
                fields=["material", "serial_number"],
                condition=Q(serial_number__isnull=False),
                name="inventory_asset_material_serial_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(internal_asset_code__regex=r"^\s*$"),
                name="inventory_asset_internal_code_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(internal_asset_code__regex=r"^\s|\s$"),
                name="inventory_asset_internal_code_trimmed",
            ),
            models.CheckConstraint(
                condition=Q(serial_number__isnull=True)
                | ~Q(serial_number__regex=r"^\s*$"),
                name="inventory_asset_serial_nonblank_or_null",
            ),
            models.CheckConstraint(
                condition=Q(serial_number__isnull=True)
                | ~Q(serial_number__regex=r"^\s|\s$"),
                name="inventory_asset_serial_trimmed_or_null",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        current_state="IN_STOCK",
                        current_location__isnull=False,
                        current_condition__isnull=False,
                    )
                    | Q(
                        current_state="ISSUED",
                        current_location__isnull=True,
                        current_condition__isnull=False,
                    )
                ),
                name="inventory_asset_state_shape",
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
                    {"material": "Tekil varlık için SERIALIZED malzeme zorunludur."}
                )
        if self.current_state == self.CurrentState.IN_STOCK:
            if self.current_location_id is None:
                raise ValidationError(
                    {"current_location": "Stoktaki tekil varlık konum gerektirir."}
                )
            if (
                not self.current_location.active
                or not self.current_location.can_hold_stock
            ):
                raise ValidationError(
                    {
                        "current_location": "Mevcut konum aktif ve stok tutabilir olmalıdır."
                    }
                )
        elif self.current_state == self.CurrentState.ISSUED:
            if self.current_location_id is not None:
                raise ValidationError(
                    {
                        "current_location": "Çıkışı yapılmış tekil varlığın mevcut konumu boş olmalıdır."
                    }
                )
        else:
            raise ValidationError(
                {
                    "current_state": "V1 tekil varlık durumu yalnız IN_STOCK veya ISSUED olabilir."
                }
            )
        if self.current_condition_id is None:
            raise ValidationError(
                {"current_condition": "Mevcut kondisyon zorunludur."}
            )
        if not self.current_condition.active:
            raise ValidationError(
                {"current_condition": "Mevcut kondisyon aktif olmalıdır."}
            )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            persisted = type(self).objects.using(self._state.db).get(pk=self.pk)
            identity_changed = any(
                (
                    persisted.material_id != self.material_id,
                    persisted.internal_asset_code != self.internal_asset_code,
                    persisted.serial_number != self.serial_number,
                )
            )
            if identity_changed and persisted.inventory_transaction_lines.exists():
                raise ValidationError(
                    "Envanter geçmişi olan tekil varlığın kimliği değiştirilemez."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.inventory_transaction_lines.exists():
            raise ValidationError(
                "Envanter geçmişi olan tekil varlık silinemez."
            )
        return super().delete(*args, **kwargs)


class InventoryTransactionLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey(
        InventoryTransaction,
        on_delete=models.RESTRICT,
        related_name="lines",
        db_index=False,
    )
    line_number = models.PositiveIntegerField()
    material = models.ForeignKey(
        "catalog.Material",
        on_delete=models.RESTRICT,
        related_name="inventory_transaction_lines",
        db_index=False,
    )
    serialized_asset = models.ForeignKey(
        SerializedAsset,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="inventory_transaction_lines",
        db_index=False,
    )
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
    )
    unit = models.ForeignKey(
        "catalog.UnitOfMeasure",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="inventory_transaction_lines",
        db_index=False,
    )
    condition = models.ForeignKey(
        "catalog.MaterialCondition",
        on_delete=models.RESTRICT,
        related_name="inventory_transaction_lines",
        db_index=False,
    )
    source_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="inventory_lines_as_source",
        db_index=False,
    )
    target_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="inventory_lines_as_target",
        db_index=False,
    )
    original_issue_line = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="return_lines",
    )
    corrected_line = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="correction_lines",
    )
    asset_event_seq = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transaction", "line_number"],
                name="inventory_line_tx_num_uniq",
            ),
            models.UniqueConstraint(
                fields=["transaction", "serialized_asset"],
                condition=Q(serialized_asset__isnull=False),
                name="inventory_line_tx_asset_uniq",
            ),
            models.UniqueConstraint(
                fields=["original_issue_line"],
                condition=Q(
                    original_issue_line__isnull=False,
                    serialized_asset__isnull=False,
                ),
                name="inventory_serialized_return_original_uniq",
            ),
            models.UniqueConstraint(
                fields=["serialized_asset", "asset_event_seq"],
                condition=Q(serialized_asset__isnull=False),
                name="inventory_line_asset_event_seq_uniq",
            ),
            models.CheckConstraint(
                condition=Q(line_number__gt=0),
                name="inventory_line_num_positive",
            ),
            models.CheckConstraint(
                condition=Q(quantity__isnull=True) | Q(quantity__gt=Decimal("0")),
                name="inventory_line_qty_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        serialized_asset__isnull=True,
                        quantity__isnull=False,
                        unit__isnull=False,
                    )
                    | Q(
                        serialized_asset__isnull=False,
                        quantity__isnull=True,
                        unit__isnull=True,
                    )
                ),
                name="inventory_line_tracking_shape",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        serialized_asset__isnull=True,
                        asset_event_seq__isnull=True,
                    )
                    | Q(
                        serialized_asset__isnull=False,
                        asset_event_seq__gte=1,
                    )
                ),
                name="inventory_line_asset_event_seq_shape",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source_location__isnull=False)
                    | Q(target_location__isnull=False)
                ),
                name="inventory_line_source_or_target_present",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source_location__isnull=True)
                    | Q(target_location__isnull=True)
                    | ~Q(source_location=F("target_location"))
                ),
                name="inventory_line_source_ne_target",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                "Tamamlanmış envanter işlem satırı değiştirilemez."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Tamamlanmış envanter işlem satırı silinemez.")


class IssueContext(models.Model):
    transaction = models.OneToOneField(
        InventoryTransaction,
        primary_key=True,
        on_delete=models.RESTRICT,
        related_name="issue_context",
    )
    receiver_employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.RESTRICT,
        related_name="issue_contexts",
        db_index=False,
    )
    receiver_first_name_snapshot = models.CharField(max_length=150)
    receiver_last_name_snapshot = models.CharField(max_length=150)
    receiver_employee_number_snapshot = models.CharField(max_length=64)
    production_line = models.ForeignKey(
        ProductionLine,
        on_delete=models.RESTRICT,
        related_name="issue_contexts",
        db_index=False,
    )
    production_line_code_snapshot = models.CharField(max_length=64)
    production_line_name_snapshot = models.CharField(max_length=255)
    usage_location_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["receiver_employee"],
                name="inventory_issue_receiver_idx",
            ),
            models.Index(
                fields=["production_line"],
                name="inventory_issue_prodline_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(receiver_first_name_snapshot__regex=r"^\s*$"),
                name="inventory_issue_receiver_first_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(receiver_last_name_snapshot__regex=r"^\s*$"),
                name="inventory_issue_receiver_last_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(receiver_employee_number_snapshot__regex=r"^\s*$"),
                name="inventory_issue_receiver_number_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(production_line_code_snapshot__regex=r"^\s*$"),
                name="inventory_issue_line_code_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(production_line_name_snapshot__regex=r"^\s*$"),
                name="inventory_issue_line_name_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(usage_location_text__regex=r"^\s*$"),
                name="inventory_issue_usage_nonblank",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Stok çıkış bağlamı değiştirilemez.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Stok çıkış bağlamı silinemez.")


class StockBalance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material = models.ForeignKey(
        "catalog.Material",
        on_delete=models.RESTRICT,
        related_name="stock_balances",
        db_index=False,
    )
    location = models.ForeignKey(
        "locations.Location",
        on_delete=models.RESTRICT,
        related_name="stock_balances",
        db_index=False,
    )
    condition = models.ForeignKey(
        "catalog.MaterialCondition",
        on_delete=models.RESTRICT,
        related_name="stock_balances",
        db_index=False,
    )
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=Decimal("0"),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["location"], name="inventory_bal_location_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["material", "location", "condition"],
                name="inventory_bal_identity_uniq",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=Decimal("0")),
                name="inventory_bal_qty_nonnegative",
            ),
        ]
