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
                condition=Q(transaction_type__in=["RECEIPT", "ISSUE"]),
                name="inventory_tx_type_receipt_or_issue",
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
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit = models.ForeignKey(
        "catalog.UnitOfMeasure",
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transaction", "line_number"],
                name="inventory_line_tx_num_uniq",
            ),
            models.CheckConstraint(
                condition=Q(line_number__gt=0),
                name="inventory_line_num_positive",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=Decimal("0")),
                name="inventory_line_qty_positive",
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
