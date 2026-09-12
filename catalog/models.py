import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import CharField, F, Func, Q, Value
from django.db.models.lookups import Exact


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, null=True, blank=True)
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
            models.Index(fields=["parent"], name="catalog_cat_parent_idx"),
            models.Index(fields=["name"], name="catalog_cat_name_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(name__regex=r"^\s*$"),
                name="catalog_cat_name_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(code__isnull=True) | ~Q(code__regex=r"^\s*$"),
                name="catalog_cat_code_null_or_ne",
            ),
            models.CheckConstraint(
                condition=Q(parent__isnull=True) | ~Q(parent_id=F("id")),
                name="catalog_cat_parent_not_self",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()

        if self.code is not None:
            trimmed_code = self.code.strip()
            self.code = trimmed_code if trimmed_code else None

        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({"name": "Kategori adı boş olamaz."})

        if self.parent_id is not None and self.pk is not None and self.parent_id == self.pk:
            raise ValidationError({"parent": "Kategori kendi üst kategorisi olamaz."})

        if self.parent_id is not None:
            ancestor = self.parent
            visited: set[uuid.UUID] = set()
            if self.pk is not None:
                visited.add(self.pk)
            while ancestor is not None:
                if ancestor.pk in visited:
                    raise ValidationError(
                        {"parent": "Kategori hiyerarşisinde döngü oluşturulamaz."}
                    )
                visited.add(ancestor.pk)
                ancestor = ancestor.parent


class MaterialCondition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    sort_order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                name="catalog_condition_code_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(code__regex=r"^\s*$"),
                name="catalog_condition_code_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(name__regex=r"^\s*$"),
                name="catalog_condition_name_nonblank",
            ),
            models.CheckConstraint(
                condition=Q(sort_order__gte=0),
                name="catalog_condition_sort_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return self.code


class UnitOfMeasure(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    decimal_places = models.SmallIntegerField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(3),
        ],
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                name="catalog_uom_code_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(code__regex=r"^\s*$"),
                name="catalog_uom_code_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(name__regex=r"^\s*$"),
                name="catalog_uom_name_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(decimal_places__isnull=True)
                | Q(decimal_places__gte=0, decimal_places__lte=3),
                name="catalog_uom_dec_places_rng",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    def clean(self) -> None:
        super().clean()

        if self.code is not None:
            self.code = self.code.strip()
            if not self.code:
                raise ValidationError({"code": "Birim kodu boş olamaz."})

        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({"name": "Birim adı boş olamaz."})


class Material(models.Model):
    class TrackingMode(models.TextChoices):
        QUANTITY = "QUANTITY", "Miktar"
        SERIALIZED = "SERIALIZED", "Tekil"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material_code = models.CharField(max_length=64, db_index=False)
    name = models.CharField(max_length=255, db_index=False)
    category = models.ForeignKey(
        Category,
        on_delete=models.RESTRICT,
        related_name="materials",
        db_index=False,
    )
    brand = models.CharField(max_length=255, null=True, blank=True)
    model = models.CharField(max_length=255, null=True, blank=True)
    unit = models.ForeignKey(
        UnitOfMeasure,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="materials",
        db_index=False,
    )
    tracking_mode = models.CharField(
        max_length=10,
        choices=TrackingMode.choices,
    )
    minimum_stock_value = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    technical_specs = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["material_code"], name="catalog_mat_code_idx"),
            models.Index(fields=["name"], name="catalog_mat_name_idx"),
            models.Index(fields=["category"], name="catalog_mat_category_idx"),
            models.Index(fields=["unit"], name="catalog_mat_unit_idx"),
            models.Index(fields=["active"], name="catalog_mat_active_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(material_code__regex=r"^\s*$"),
                name="catalog_mat_code_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(name__regex=r"^\s*$"),
                name="catalog_mat_name_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(tracking_mode__in=["QUANTITY", "SERIALIZED"]),
                name="catalog_mat_track_mode_ok",
            ),
            models.CheckConstraint(
                condition=~Q(tracking_mode="QUANTITY") | Q(unit__isnull=False),
                name="catalog_mat_qty_needs_unit",
            ),
            models.CheckConstraint(
                condition=Q(minimum_stock_value__isnull=True)
                | Q(minimum_stock_value__gte=Decimal("0")),
                name="catalog_mat_min_stock_nn",
            ),
            models.CheckConstraint(
                condition=Exact(
                    Func(
                        F("technical_specs"),
                        function="jsonb_typeof",
                        output_field=CharField(),
                    ),
                    Value("object"),
                ),
                name="catalog_mat_tech_specs_obj",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()

        if self.material_code is not None:
            self.material_code = self.material_code.strip()
            if not self.material_code:
                raise ValidationError({"material_code": "Malzeme kodu boş olamaz."})

        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({"name": "Malzeme adı boş olamaz."})

        if self.brand is not None:
            trimmed_brand = self.brand.strip()
            self.brand = trimmed_brand if trimmed_brand else None

        if self.model is not None:
            trimmed_model = self.model.strip()
            self.model = trimmed_model if trimmed_model else None

        if not isinstance(self.technical_specs, dict):
            raise ValidationError(
                {"technical_specs": "Teknik özellikler bir JSON nesnesi olmalıdır."}
            )

        if self.tracking_mode == self.TrackingMode.QUANTITY and self.unit_id is None:
            raise ValidationError(
                {"unit": "Miktar takibi için ölçü birimi zorunludur."}
            )
