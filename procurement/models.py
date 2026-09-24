import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q, Sum


class FulfillmentState:
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    RECEIVED = "RECEIVED"

    LABELS = {
        OPEN: "Bekliyor",
        PARTIAL: "Kısmen geldi",
        RECEIVED: "Tamamlandı",
    }


class PurchaseRequest(models.Model):
    """Company procurement request. Creating a row does not create stock."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_no = models.CharField(max_length=64)
    request_date = models.DateField()
    approval_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="purchase_requests_created",
        db_index=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["request_date"], name="proc_req_date_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["request_no"],
                name="proc_req_no_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(request_no__regex=r"^\s*$"),
                name="proc_req_no_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(approval_date__isnull=True)
                | Q(approval_date__gte=F("request_date")),
                name="proc_req_approval_ok",
            ),
        ]

    def __str__(self) -> str:
        return self.request_no

    def delete(self, *args, **kwargs):
        if PurchaseRequestReceipt.objects.filter(line__purchase_request=self).exists():
            raise ValidationError(
                "Karşılanmış talep silinemez. Bağlı stok girişleri tarihçe olarak kalır."
            )
        return super().delete(*args, **kwargs)


class PurchaseRequestLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_request = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.RESTRICT,
        related_name="lines",
        db_index=False,
    )
    requested_description = models.TextField()
    material = models.ForeignKey(
        "catalog.Material",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="purchase_request_lines",
        db_index=False,
    )
    unit = models.ForeignKey(
        "catalog.UnitOfMeasure",
        on_delete=models.RESTRICT,
        related_name="purchase_request_lines",
        db_index=False,
    )
    unit_code_snapshot = models.CharField(max_length=64)
    unit_name_snapshot = models.CharField(max_length=255)
    requested_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    expected_arrival_date = models.DateField(null=True, blank=True)
    supplier_name = models.CharField(max_length=255, null=True, blank=True)
    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    currency_code = models.CharField(max_length=3, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["purchase_request"], name="proc_line_req_idx"),
            models.Index(fields=["material"], name="proc_line_mat_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(requested_description__regex=r"^\s*$"),
                name="proc_line_desc_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(requested_quantity__gt=Decimal("0")),
                name="proc_line_qty_pos",
            ),
            models.CheckConstraint(
                condition=~Q(unit_code_snapshot__regex=r"^\s*$"),
                name="proc_line_unit_code_ne",
            ),
            models.CheckConstraint(
                condition=Q(lead_time_days__isnull=True) | Q(lead_time_days__gte=0),
                name="proc_line_lead_nn",
            ),
            models.CheckConstraint(
                condition=(
                    Q(unit_price__isnull=True, currency_code__isnull=True)
                    | (
                        Q(unit_price__isnull=False, unit_price__gte=Decimal("0"))
                        & Q(currency_code__regex=r"^[A-Z]{3}$")
                    )
                ),
                name="proc_line_price_ccy",
            ),
        ]

    def __str__(self) -> str:
        return self.requested_description

    def delete(self, *args, **kwargs):
        if self.receipts.exists():
            raise ValidationError(
                "Karşılanmış talep kalemi silinemez. Bağlı stok girişleri tarihçe olarak kalır."
            )
        return super().delete(*args, **kwargs)

    def received_quantity(self) -> Decimal:
        total = self.receipts.aggregate(total=Sum("fulfilled_quantity"))["total"]
        return total if total is not None else Decimal("0")

    def remaining_quantity(self) -> Decimal:
        remaining = self.requested_quantity - self.received_quantity()
        if remaining < 0:
            return Decimal("0")
        return remaining

    def over_received_quantity(self) -> Decimal:
        extra = self.received_quantity() - self.requested_quantity
        if extra < 0:
            return Decimal("0")
        return extra

    def fulfillment_state(self) -> str:
        received = self.received_quantity()
        if received <= 0:
            return FulfillmentState.OPEN
        if received < self.requested_quantity:
            return FulfillmentState.PARTIAL
        return FulfillmentState.RECEIVED

    def fulfillment_label(self) -> str:
        return FulfillmentState.LABELS[self.fulfillment_state()]

    def derived_total_price(self) -> Decimal | None:
        if self.unit_price is None:
            return None
        return (self.requested_quantity * self.unit_price).quantize(Decimal("0.0001"))


class PurchaseRequestReceipt(models.Model):
    """Links one authoritative RECEIPT to one request line. Not a stock counter."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(
        PurchaseRequestLine,
        on_delete=models.RESTRICT,
        related_name="receipts",
        db_index=False,
    )
    inventory_transaction_id = models.UUIDField()
    fulfilled_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="purchase_request_receipts_created",
        db_index=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["line"], name="proc_rcpt_line_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["inventory_transaction_id"],
                name="proc_rcpt_tx_uniq",
            ),
            models.CheckConstraint(
                condition=Q(fulfilled_quantity__gt=Decimal("0")),
                name="proc_rcpt_qty_pos",
            ),
        ]

    @property
    def inventory_transaction(self):
        from inventory.models import InventoryTransaction

        cached = getattr(self, "_inventory_transaction", None)
        if cached is None:
            cached = InventoryTransaction.objects.get(pk=self.inventory_transaction_id)
            self._inventory_transaction = cached
        return cached

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Talep karşılama bağlantısı silinemez. Stok girişi tarihçesi korunur."
        )
