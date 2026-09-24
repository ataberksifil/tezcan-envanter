from __future__ import annotations

import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from core.formatting import format_quantity
from inventory.services.receipts import ReceiptMetadataInput

from accounts.models import Employee
from catalog.models import Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    ExpiryInspection,
    InventoryTransaction,
    InventoryTransactionLine,
    ProductionLine,
    SerializedAsset,
    StockBalance,
)
from locations.display import location_path_label
from locations.models import Location


def production_line_choice_label(production_line: ProductionLine) -> str:
    parts = [production_line.code]
    if not production_line.active:
        parts.append("[Pasif]")
    return " ".join(parts)


def descendant_ids(production_line: ProductionLine) -> set[uuid.UUID]:
    found: set[uuid.UUID] = set()
    queue = list(production_line.children.all())
    while queue:
        child = queue.pop()
        if child.pk in found:
            continue
        found.add(child.pk)
        queue.extend(child.children.all())
    return found


def material_choice_label(material: Material) -> str:
    parts = [material.material_code, material.name]
    identity = []
    if material.brand:
        identity.append(material.brand)
    if material.model:
        identity.append(material.model)
    if identity:
        parts.append("— " + " / ".join(identity))
    if material.unit_id is not None:
        parts.append(f"[{material.unit.code}]")
    parts.append(f"({material.get_tracking_mode_display()})")
    return " ".join(parts)


def condition_choice_label(condition: MaterialCondition) -> str:
    parts = [condition.name]
    if not condition.active:
        parts.append("[Pasif]")
    return " ".join(parts)


def stock_location_choice_label(location: Location) -> str:
    parts = [location_path_label(location)]
    if not location.active:
        parts.append("[Pasif]")
    return " ".join(parts)


def disambiguate_choice_labels(field, key: str) -> None:
    """Append a short id only to options whose ``key`` repeats in the queryset.

    Location codes and employee numbers are unique, so their labels need no id.
    Legacy material codes and condition names may repeat; only those get one.
    """
    duplicated = set(
        field.queryset.order_by()
        .values(key)
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .values_list(key, flat=True)
    )
    if not duplicated:
        return
    base_label = field.label_from_instance

    def label(obj):
        text = base_label(obj)
        if getattr(obj, key) in duplicated:
            return f"{text} — #{str(obj.pk)[:8]}"
        return text

    field.label_from_instance = label


class ServiceValidatedModelChoiceField(forms.ModelChoiceField):
    """Show the usability queryset while letting the service judge stale/forged rows."""

    def to_python(self, value):
        if value in self.empty_values:
            return None
        self.validate_no_null_characters(value)
        try:
            key = self.to_field_name or "pk"
            if isinstance(value, self.queryset.model):
                value = getattr(value, key)
            return self.queryset.model._default_manager.using(self.queryset.db).get(
                **{key: value}
            )
        except (ValueError, TypeError, self.queryset.model.DoesNotExist):
            raise ValidationError(
                self.error_messages["invalid_choice"],
                code="invalid_choice",
                params={"value": value},
            )


class QuantityReceiptForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    material = forms.ModelChoiceField(
        label="Malzeme",
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.DecimalField(
        label="Miktar",
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )
    condition = forms.ModelChoiceField(
        label="Kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    target_location = forms.ModelChoiceField(
        label="Kayıt konumu (mal kabul / yerleştirme bekleyen veya raf)",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Gelen malzeme önce mal kabul / yerleştirme bekleyen stok konumuna "
            "alınabilir. Raf yerleşimi kayıt sonrası mevcut Transfer ile yapılır. "
            "Sistem tek bir sabit raf zorlamaz. Kullanıldığı yer bu konum değildir."
        ),
    )
    usage_place = forms.CharField(
        label="Kullanıldığı / uygulandığı yer",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Örn. Tavlama. Fiziksel raf/bin konumu değildir.",
    )
    arrived_on = forms.DateField(
        label="Fiziksel geliş tarihi",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
        help_text="Malzeme depoya fiilen geldiği gün. Sistem kayıt saati ayrıca saklanır.",
    )
    supplier_name = forms.CharField(
        label="Tedarikçi / firma",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    package_count = forms.IntegerField(
        label="Paket adedi",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )
    package_label = forms.CharField(
        label="Paket tanımı",
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "KUTU"}),
        help_text="İsteğe bağlı. Ölçü birimi değildir; dönüşüm yapılmaz.",
    )
    contents_per_package = forms.DecimalField(
        label="Paket içi miktar (stok biriminde)",
        required=False,
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
        help_text="Doluysa paket adedi × paket içi miktar, girilen stok miktarına eşit olmalıdır.",
    )
    expires_on = forms.DateField(
        label="SKT (son kullanma tarihi)",
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
        help_text="İsteğe bağlı. Parti/giriş kaydına aittir; malzeme kartına yazılmaz.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()
            self.fields["arrived_on"].initial = timezone.localdate()

        receipt_materials = (
            Material.objects.filter(
                active=True,
                tracking_mode=Material.TrackingMode.QUANTITY,
                unit__isnull=False,
            )
            .select_related("unit")
            .order_by("material_code", "name", "id")
        )
        self.fields["material"].queryset = receipt_materials
        self.fields["material"].label_from_instance = material_choice_label
        disambiguate_choice_labels(self.fields["material"], "material_code")
        self.fields["material"].widget.attrs.update(
            {
                "hx-get": reverse("inventory:receipt-material-summary"),
                "hx-trigger": "change",
                "hx-target": "#receipt-material-summary",
                "hx-include": "[name='material']",
            }
        )

        receipt_conditions = MaterialCondition.objects.filter(active=True).order_by(
            "sort_order", "name", "id"
        )
        self.fields["condition"].queryset = receipt_conditions
        self.fields["condition"].label_from_instance = condition_choice_label
        disambiguate_choice_labels(self.fields["condition"], "name")

        receipt_locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).select_related("parent").order_by("code", "name", "id")
        self.fields["target_location"].queryset = receipt_locations
        self.fields["target_location"].label_from_instance = stock_location_choice_label

        selected_material = self._selected_material()
        if selected_material is not None and selected_material.unit_id is not None:
            unit = selected_material.unit
            self.fields["quantity"].label = f"Miktar ({unit.code})"
            self.fields["quantity"].help_text = (
                f"Yetkili stok birimi: {unit.code} ({unit.name}). "
                "Paket adedi stoğu kendiliğinden çarpmaz."
            )

    def _selected_material(self):
        if self.is_bound:
            value = self.data.get("material")
        else:
            value = self.initial.get("material")
        if not value:
            return None
        try:
            return self.fields["material"].queryset.select_related("unit").get(pk=value)
        except (ValueError, TypeError, Material.DoesNotExist):
            return None

    def clean_usage_place(self):
        return self.cleaned_data["usage_place"].strip()

    def clean_supplier_name(self):
        value = self.cleaned_data.get("supplier_name") or ""
        return value.strip()

    def clean_package_label(self):
        value = self.cleaned_data.get("package_label") or ""
        return value.strip()

    def clean_arrived_on(self):
        value = self.cleaned_data["arrived_on"]
        if value > timezone.localdate():
            raise ValidationError("Geliş tarihi gelecek bir gün olamaz.")
        return value

    def clean(self):
        cleaned = super().clean()
        return _clean_quantity_packaging(cleaned)


RECEIPT_ERROR_FIELD_MAP = {
    "inventory.invalid_quantity": "quantity",
    "inventory.inactive_material": "material",
    "inventory.tracking_mode_mismatch": "material",
    "inventory.unit_mismatch": "material",
    "inventory.inactive_condition": "condition",
    "inventory.invalid_destination": "target_location",
    "inventory.invalid_operation_id": "operation_id",
    "inventory.invalid_usage_place": "usage_place",
    "inventory.invalid_arrival_date": "arrived_on",
    "inventory.invalid_supplier": "supplier_name",
    "inventory.invalid_packaging": "package_count",
    "inventory.packaging_quantity_mismatch": "contents_per_package",
    "inventory.invalid_expiry_date": "expires_on",
}


class SerializedReceiptForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    material = ServiceValidatedModelChoiceField(
        label="Malzeme",
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    internal_asset_code = forms.CharField(
        label="Dahili varlık kodu",
        max_length=64,
        strip=True,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    serial_number = forms.CharField(
        label="Üretici seri numarası",
        max_length=255,
        strip=True,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    condition = ServiceValidatedModelChoiceField(
        label="Kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    target_location = ServiceValidatedModelChoiceField(
        label="Hedef konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Fiziksel stok konumu. Kullanıldığı yer bu alan değildir.",
    )
    usage_place = forms.CharField(
        label="Kullanıldığı / uygulandığı yer",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Örn. Tavlama. Fiziksel raf/bin konumu değildir.",
    )
    arrived_on = forms.DateField(
        label="Fiziksel geliş tarihi",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
        help_text="Malzeme depoya fiilen geldiği gün. Sistem kayıt saati ayrıca saklanır.",
    )
    supplier_name = forms.CharField(
        label="Tedarikçi / firma",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    expires_on = forms.DateField(
        label="SKT (son kullanma tarihi)",
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
        help_text="İsteğe bağlı. Parti/giriş kaydına aittir; malzeme kartına yazılmaz.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()
            self.fields["arrived_on"].initial = timezone.localdate()

        materials = (
            Material.objects.filter(
                active=True,
                tracking_mode=Material.TrackingMode.SERIALIZED,
            )
            .select_related("unit")
            .order_by("material_code", "name", "id")
        )
        self.fields["material"].queryset = materials
        self.fields["material"].label_from_instance = material_choice_label
        disambiguate_choice_labels(self.fields["material"], "material_code")

        conditions = MaterialCondition.objects.filter(active=True).order_by(
            "sort_order", "name", "id"
        )
        self.fields["condition"].queryset = conditions
        self.fields["condition"].label_from_instance = condition_choice_label
        disambiguate_choice_labels(self.fields["condition"], "name")

        locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).order_by("code", "name", "id")
        self.fields["target_location"].queryset = locations
        self.fields["target_location"].label_from_instance = stock_location_choice_label

    def clean_serial_number(self):
        value = self.cleaned_data["serial_number"].strip()
        return value or None

    def clean_usage_place(self):
        return self.cleaned_data["usage_place"].strip()

    def clean_supplier_name(self):
        value = self.cleaned_data.get("supplier_name") or ""
        return value.strip()

    def clean_arrived_on(self):
        value = self.cleaned_data["arrived_on"]
        if value > timezone.localdate():
            raise ValidationError("Geliş tarihi gelecek bir gün olamaz.")
        return value

    def clean(self):
        cleaned = super().clean()
        internal_asset_code = cleaned.get("internal_asset_code")
        material = cleaned.get("material")
        serial_number = cleaned.get("serial_number")

        if internal_asset_code and SerializedAsset.objects.filter(
            internal_asset_code=internal_asset_code
        ).exists():
            self.add_error(
                "internal_asset_code",
                "Bu dahili varlık kodu zaten kullanılıyor.",
            )
        if material is not None and serial_number and SerializedAsset.objects.filter(
            material=material,
            serial_number=serial_number,
        ).exists():
            self.add_error(
                "serial_number",
                "Bu üretici seri numarası seçilen malzeme için zaten kullanılıyor.",
            )
        return cleaned


SERIALIZED_RECEIPT_ERROR_FIELD_MAP = {
    "inventory.invalid_internal_asset_code": "internal_asset_code",
    "inventory.internal_asset_code_conflict": "internal_asset_code",
    "inventory.invalid_serial_number": "serial_number",
    "inventory.serial_number_conflict": "serial_number",
    "inventory.inactive_material": "material",
    "inventory.tracking_mode_mismatch": "material",
    "inventory.inactive_condition": "condition",
    "inventory.invalid_destination": "target_location",
    "inventory.invalid_operation_id": "operation_id",
    "inventory.invalid_usage_place": "usage_place",
    "inventory.invalid_arrival_date": "arrived_on",
    "inventory.invalid_supplier": "supplier_name",
    "inventory.invalid_packaging": "usage_place",
    "inventory.invalid_expiry_date": "expires_on",
}


def _clean_quantity_packaging(cleaned):
    package_count = cleaned.get("package_count")
    contents = cleaned.get("contents_per_package")
    label = cleaned.get("package_label") or ""
    quantity = cleaned.get("quantity")
    has_packaging = package_count is not None or contents is not None or bool(label)
    if not has_packaging:
        cleaned["package_label"] = ""
        return cleaned
    if package_count is None or contents is None:
        raise ValidationError(
            "Paket bilgisi için hem paket adedi hem paket içi miktar girilmelidir."
        )
    if quantity is not None and (contents * package_count) != quantity:
        raise ValidationError(
            {
                "contents_per_package": (
                    "Paket adedi × paket içi miktar, kaydedilen stok miktarına "
                    "eşit olmalıdır. Stok birimi değişmez."
                )
            }
        )
    return cleaned


class ExpiryInspectionForm(forms.Form):
    outcome = forms.ChoiceField(
        label="Kontrol sonucu",
        choices=[
            (choice.value, choice.label)
            for choice in ExpiryInspection.SELECTABLE_OUTCOMES
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Ayırma gerekiyor, ürünün fiziksel olarak ayrılması gerektiğini kaydeder. "
            "Stok kendiliğinden düşmez, taşınmaz veya imha edilmez."
        ),
    )
    note = forms.CharField(
        label="Fiziksel kontrol notu",
        min_length=10,
        max_length=2000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        help_text=(
            "Gördüğünüz fiziksel kontrolü yazın. Bu kayıt stok miktarını, "
            "konumu veya defteri değiştirmez."
        ),
    )

    def clean_note(self):
        return self.cleaned_data["note"].strip()


def receipt_metadata_from_cleaned(cleaned, *, include_packaging: bool) -> ReceiptMetadataInput:
    return ReceiptMetadataInput(
        usage_place=cleaned["usage_place"],
        arrived_on=cleaned["arrived_on"],
        supplier_name=cleaned.get("supplier_name") or None,
        package_count=cleaned.get("package_count") if include_packaging else None,
        package_label=(cleaned.get("package_label") or None) if include_packaging else None,
        contents_per_package=(
            cleaned.get("contents_per_package") if include_packaging else None
        ),
    )


def attach_serialized_receipt_validation_error(
    form: SerializedReceiptForm,
    exc: ValidationError,
) -> None:
    if hasattr(exc, "error_list"):
        for error in exc.error_list:
            field = SERIALIZED_RECEIPT_ERROR_FIELD_MAP.get(
                getattr(error, "code", None)
            )
            if field is not None and field in form.fields:
                form.add_error(field, error)
            else:
                form.add_error(None, error)
        return
    _attach_errors_to_form(form, exc)


def _attach_errors_to_form(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = None if field == "__all__" else field
            for error in errors:
                if target is not None and target in form.fields:
                    form.add_error(target, error)
                else:
                    form.add_error(None, error)
    else:
        form.add_error(None, exc)


def employee_choice_label(employee: Employee) -> str:
    parts = [
        employee.employee_number,
        f"({employee.first_name} {employee.last_name})",
    ]
    if not employee.active:
        parts.append("[Pasif]")
    return " ".join(parts)


class QuantityIssueForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    material = forms.ModelChoiceField(
        label="Malzeme",
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    source_location = forms.ModelChoiceField(
        label="Kaynak konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    condition = forms.ModelChoiceField(
        label="Kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.DecimalField(
        label="Miktar",
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )
    receiver_employee = forms.ModelChoiceField(
        label="Alıcı çalışan",
        queryset=Employee.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    production_line = forms.ModelChoiceField(
        label="Üretim hattı",
        queryset=ProductionLine.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    usage_location_text = forms.CharField(
        label="Kullanım yeri",
        max_length=2000,
        strip=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()

        issue_materials = (
            Material.objects.filter(
                active=True,
                tracking_mode=Material.TrackingMode.QUANTITY,
                unit__isnull=False,
            )
            .select_related("unit")
            .order_by("material_code", "name", "id")
        )
        self.fields["material"].queryset = issue_materials
        self.fields["material"].label_from_instance = material_choice_label
        disambiguate_choice_labels(self.fields["material"], "material_code")

        issue_locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).order_by("code", "name", "id")
        self.fields["source_location"].queryset = issue_locations
        self.fields["source_location"].label_from_instance = stock_location_choice_label

        issue_conditions = MaterialCondition.objects.filter(active=True).order_by(
            "sort_order", "name", "id"
        )
        self.fields["condition"].queryset = issue_conditions
        self.fields["condition"].label_from_instance = condition_choice_label
        disambiguate_choice_labels(self.fields["condition"], "name")

        issue_employees = Employee.objects.filter(active=True).order_by(
            "employee_number", "last_name", "first_name", "id"
        )
        self.fields["receiver_employee"].queryset = issue_employees
        self.fields["receiver_employee"].label_from_instance = employee_choice_label

        issue_production_lines = ProductionLine.objects.filter(active=True).order_by(
            "name", "id"
        )
        self.fields["production_line"].queryset = issue_production_lines
        self.fields["production_line"].label_from_instance = production_line_choice_label

    def clean_usage_location_text(self):
        value = self.cleaned_data.get("usage_location_text")
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        return value.strip()


class SerializedIssueForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    source_location = ServiceValidatedModelChoiceField(
        label="Kaynak konum",
        queryset=Location.objects.none(),
        widget=forms.HiddenInput(),
    )
    condition = ServiceValidatedModelChoiceField(
        label="Kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.HiddenInput(),
    )
    receiver_employee = forms.ModelChoiceField(
        label="Alıcı çalışan",
        queryset=Employee.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    production_line = forms.ModelChoiceField(
        label="Üretim hattı",
        queryset=ProductionLine.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    usage_location_text = forms.CharField(
        label="Kullanım yeri",
        max_length=2000,
        strip=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, asset: SerializedAsset, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()
            self.initial.setdefault("source_location", asset.current_location_id)
            self.initial.setdefault("condition", asset.current_condition_id)

        self.fields["source_location"].queryset = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        )
        self.fields["condition"].queryset = MaterialCondition.objects.filter(active=True)

        employees = Employee.objects.filter(active=True).order_by(
            "employee_number", "last_name", "first_name", "id"
        )
        self.fields["receiver_employee"].queryset = employees
        self.fields["receiver_employee"].label_from_instance = employee_choice_label

        production_lines = ProductionLine.objects.filter(active=True).order_by(
            "name", "id"
        )
        self.fields["production_line"].queryset = production_lines
        self.fields["production_line"].label_from_instance = production_line_choice_label

    def clean_usage_location_text(self):
        value = self.cleaned_data.get("usage_location_text")
        return value.strip() if isinstance(value, str) else value


class SerializedReturnForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    target_location = ServiceValidatedModelChoiceField(
        label="Hedef konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()
        locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).order_by("code", "name", "id")
        self.fields["target_location"].queryset = locations
        self.fields["target_location"].label_from_instance = stock_location_choice_label


class SerializedTransferForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    source_location = ServiceValidatedModelChoiceField(
        label="Kaynak konum",
        queryset=Location.objects.none(),
        widget=forms.HiddenInput(),
    )
    condition = ServiceValidatedModelChoiceField(
        label="Kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.HiddenInput(),
    )
    target_location = ServiceValidatedModelChoiceField(
        label="Hedef konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, asset: SerializedAsset, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()
            self.initial.setdefault("source_location", asset.current_location_id)
            self.initial.setdefault("condition", asset.current_condition_id)

        locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).order_by("code", "name", "id")
        self.fields["source_location"].queryset = locations
        self.fields["condition"].queryset = MaterialCondition.objects.filter(active=True)

        source_id = (
            self.data.get("source_location")
            if self.is_bound
            else asset.current_location_id
        )
        self.fields["target_location"].queryset = locations.exclude(pk=source_id)
        self.fields["target_location"].label_from_instance = stock_location_choice_label


ISSUE_ERROR_FIELD_MAP = {
    "inventory.invalid_quantity": "quantity",
    "inventory.inactive_material": "material",
    "inventory.tracking_mode_mismatch": "material",
    "inventory.unit_mismatch": "material",
    "inventory.inactive_condition": "condition",
    "inventory.invalid_source": "source_location",
    "inventory.inactive_employee": "receiver_employee",
    "inventory.inactive_production_line": "production_line",
    "inventory.invalid_usage_location": "usage_location_text",
    "inventory.insufficient_stock": "quantity",
    "inventory.invalid_operation_id": "operation_id",
}


SERIALIZED_ISSUE_ERROR_FIELD_MAP = {
    "inventory.invalid_source": "source_location",
    "inventory.inactive_condition": "condition",
    "inventory.condition_mismatch": "condition",
    "inventory.inactive_employee": "receiver_employee",
    "inventory.inactive_production_line": "production_line",
    "inventory.invalid_usage_location": "usage_location_text",
    "inventory.invalid_operation_id": "operation_id",
}

SERIALIZED_RETURN_ERROR_FIELD_MAP = {
    "inventory.invalid_destination": "target_location",
    "inventory.invalid_operation_id": "operation_id",
}

SERIALIZED_TRANSFER_ERROR_FIELD_MAP = {
    "inventory.invalid_source": "source_location",
    "inventory.invalid_destination": "target_location",
    "inventory.same_source_destination": "target_location",
    "inventory.inactive_condition": "condition",
    "inventory.condition_mismatch": "condition",
    "inventory.invalid_operation_id": "operation_id",
}


def _attach_serialized_movement_validation_error(form, exc, field_map) -> None:
    if hasattr(exc, "error_list"):
        for error in exc.error_list:
            field = field_map.get(getattr(error, "code", None))
            if field is not None and field in form.fields:
                form.add_error(field, error)
            else:
                form.add_error(None, error)
        return
    _attach_errors_to_form(form, exc)


def attach_serialized_issue_validation_error(
    form: SerializedIssueForm, exc: ValidationError
) -> None:
    _attach_serialized_movement_validation_error(
        form, exc, SERIALIZED_ISSUE_ERROR_FIELD_MAP
    )


def attach_serialized_return_validation_error(
    form: SerializedReturnForm, exc: ValidationError
) -> None:
    _attach_serialized_movement_validation_error(
        form, exc, SERIALIZED_RETURN_ERROR_FIELD_MAP
    )


def attach_serialized_transfer_validation_error(
    form: SerializedTransferForm, exc: ValidationError
) -> None:
    _attach_serialized_movement_validation_error(
        form, exc, SERIALIZED_TRANSFER_ERROR_FIELD_MAP
    )


def return_issue_line_choice_label(line: InventoryTransactionLine) -> str:
    occurred_at = timezone.localtime(line.transaction.occurred_at).strftime(
        "%d.%m.%Y %H:%M"
    )
    returned_quantity = getattr(line, "returned_quantity", Decimal("0.000"))
    remaining_quantity = line.quantity - returned_quantity
    parts = [
        occurred_at,
        f"{line.material.material_code} ({line.material.name})",
        (
            f"Çıkış: {format_quantity(line.quantity)} {line.unit.code}, "
            f"İade: {format_quantity(returned_quantity)}, "
            f"Kalan: {format_quantity(remaining_quantity)} {line.unit.code}"
        ),
        f"Kaynak: {line.source_location.code} ({line.source_location.name})",
    ]
    issue_context = line.transaction.issue_context
    parts.append(
        "Alıcı: "
        f"{issue_context.receiver_first_name_snapshot} "
        f"{issue_context.receiver_last_name_snapshot} "
        f"({issue_context.receiver_employee_number_snapshot})"
    )
    return " — ".join(parts)


def eligible_return_issue_lines():
    quantity_field = DecimalField(max_digits=18, decimal_places=3)
    return (
        InventoryTransactionLine.objects.filter(
            transaction__transaction_type=InventoryTransaction.TransactionType.ISSUE,
            source_location__isnull=False,
            target_location__isnull=True,
            original_issue_line__isnull=True,
            material__active=True,
            material__tracking_mode=Material.TrackingMode.QUANTITY,
            material__unit__isnull=False,
            condition__active=True,
        )
        .annotate(
            returned_quantity=Coalesce(
                Sum(
                    "return_lines__quantity",
                    filter=Q(
                        return_lines__transaction__transaction_type=(
                            InventoryTransaction.TransactionType.RETURN
                        )
                    ),
                ),
                Value(Decimal("0.000")),
                output_field=quantity_field,
            )
        )
        .filter(returned_quantity__lt=F("quantity"))
        .select_related(
            "transaction",
            "transaction__issue_context",
            "material",
            "unit",
            "condition",
            "source_location",
        )
        .order_by("-transaction__occurred_at", "material__material_code", "id")
    )


class QuantityReturnForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    original_issue_line = ServiceValidatedModelChoiceField(
        label="Orijinal stok çıkışı",
        queryset=InventoryTransactionLine.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    target_location = ServiceValidatedModelChoiceField(
        label="Hedef konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.DecimalField(
        label="İade miktarı",
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()

        self.fields["original_issue_line"].queryset = eligible_return_issue_lines()
        self.fields["original_issue_line"].label_from_instance = (
            return_issue_line_choice_label
        )

        return_locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).order_by("code", "name", "id")
        self.fields["target_location"].queryset = return_locations
        self.fields["target_location"].label_from_instance = stock_location_choice_label


RETURN_ERROR_FIELD_MAP = {
    "inventory.invalid_original_issue": "original_issue_line",
    "inventory.return_exceeds_issue_quantity": "quantity",
    "inventory.inactive_material": "original_issue_line",
    "inventory.tracking_mode_mismatch": "original_issue_line",
    "inventory.inactive_condition": "original_issue_line",
    "inventory.invalid_destination": "target_location",
    "inventory.invalid_quantity": "quantity",
}


def attach_issue_validation_error(form: QuantityIssueForm, exc: ValidationError) -> None:
    if hasattr(exc, "error_list"):
        for error in exc.error_list:
            code = getattr(error, "code", None)
            field = ISSUE_ERROR_FIELD_MAP.get(code)
            if field is not None and field in form.fields:
                form.add_error(field, error)
            else:
                form.add_error(None, error)
        return

    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = None if field == "__all__" else field
            for error in errors:
                if target is not None and target in form.fields:
                    form.add_error(target, error)
                else:
                    form.add_error(None, error)


def attach_receipt_validation_error(form: QuantityReceiptForm, exc: ValidationError) -> None:
    if hasattr(exc, "error_list"):
        for error in exc.error_list:
            code = getattr(error, "code", None)
            field = RECEIPT_ERROR_FIELD_MAP.get(code)
            if field is not None and field in form.fields:
                form.add_error(field, error)
            else:
                form.add_error(None, error)
        return

    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = None if field == "__all__" else field
            for error in errors:
                if target is not None and target in form.fields:
                    form.add_error(target, error)
                else:
                    form.add_error(None, error)


class QuantityTransferForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    material = ServiceValidatedModelChoiceField(
        label="Malzeme",
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    unit = ServiceValidatedModelChoiceField(
        label="Birim",
        queryset=UnitOfMeasure.objects.none(),
        required=False,
        widget=forms.HiddenInput(),
    )
    condition = ServiceValidatedModelChoiceField(
        label="Kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    source_location = ServiceValidatedModelChoiceField(
        label="Kaynak konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Yerleştirmede kaynak, malzemenin şu an kayıtlı olduğu konumdur.",
    )
    target_location = ServiceValidatedModelChoiceField(
        label="Hedef konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Fiziksel rafa yerleştirirken lokasyon etiketini okuyup doğrulayın. "
            "Elle seçim yedektir; sunucu tarama ile yazmayı ayırt etmez."
        ),
    )
    quantity = forms.DecimalField(
        label="Miktar",
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()

        transfer_materials = (
            Material.objects.filter(
                active=True,
                tracking_mode=Material.TrackingMode.QUANTITY,
                unit__isnull=False,
            )
            .select_related("unit")
            .order_by("material_code", "name", "id")
        )
        self.fields["material"].queryset = transfer_materials
        self.fields["material"].label_from_instance = material_choice_label
        disambiguate_choice_labels(self.fields["material"], "material_code")

        self.fields["unit"].queryset = UnitOfMeasure.objects.order_by("code", "id")

        transfer_conditions = MaterialCondition.objects.filter(active=True).order_by(
            "sort_order", "name", "id"
        )
        self.fields["condition"].queryset = transfer_conditions
        self.fields["condition"].label_from_instance = condition_choice_label
        disambiguate_choice_labels(self.fields["condition"], "name")

        stock_locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).order_by("code", "name", "id")
        self.fields["source_location"].queryset = stock_locations
        self.fields["source_location"].label_from_instance = stock_location_choice_label

        target_locations = stock_locations
        selected_source_id = self.data.get("source_location") if self.is_bound else None
        if selected_source_id:
            target_locations = stock_locations.exclude(pk=selected_source_id)
        self.fields["target_location"].queryset = target_locations
        self.fields["target_location"].label_from_instance = stock_location_choice_label

        selected_material = self._posted_instance(Material, "material")
        selected_condition = self._posted_instance(MaterialCondition, "condition")
        selected_source = self._posted_instance(Location, "source_location")
        if selected_material is not None and selected_material.unit_id is not None:
            if not self.data.get("unit"):
                self.fields["unit"].initial = selected_material.unit_id
        if (
            selected_material is not None
            and selected_condition is not None
            and selected_source is not None
        ):
            balance = StockBalance.objects.filter(
                material=selected_material,
                location=selected_source,
                condition=selected_condition,
            ).first()
            if balance is not None:
                self.fields["quantity"].help_text = (
                    f"Kaynak stok (bilgi amaçlı): {balance.quantity}"
                )

    def _posted_instance(self, model, field_name):
        if not self.is_bound:
            return None
        value = self.data.get(field_name)
        if not value:
            return None
        try:
            return model._default_manager.get(pk=value)
        except (ValueError, TypeError, model.DoesNotExist):
            return None

    def clean(self):
        cleaned = super().clean()
        material = cleaned.get("material")
        unit = cleaned.get("unit")
        if material is not None and unit is None and material.unit_id is not None:
            cleaned["unit"] = material.unit
        return cleaned


TRANSFER_ERROR_FIELD_MAP = {
    "inventory.invalid_quantity": ("quantity",),
    "inventory.inactive_material": ("material",),
    "inventory.tracking_mode_mismatch": ("material",),
    "inventory.unit_mismatch": ("material", "unit"),
    "inventory.inactive_condition": ("condition",),
    "inventory.invalid_source": ("source_location",),
    "inventory.invalid_destination": ("target_location",),
    "inventory.insufficient_stock": ("quantity", "source_location"),
    "inventory.same_source_destination": ("source_location", "target_location"),
    "inventory.invalid_operation_id": ("operation_id",),
}


def attach_transfer_validation_error(
    form: QuantityTransferForm, exc: ValidationError
) -> None:
    if hasattr(exc, "error_list"):
        for error in exc.error_list:
            code = getattr(error, "code", None)
            fields = TRANSFER_ERROR_FIELD_MAP.get(code, ())
            attached = False
            for field in fields:
                if field in form.fields:
                    form.add_error(field, error.message if attached else error)
                    attached = True
            if not attached:
                form.add_error(None, error)
        return

    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = None if field == "__all__" else field
            for error in errors:
                if target is not None and target in form.fields:
                    form.add_error(target, error)
                else:
                    form.add_error(None, error)


def attach_return_validation_error(form: QuantityReturnForm, exc: ValidationError) -> None:
    if hasattr(exc, "error_list"):
        for error in exc.error_list:
            code = getattr(error, "code", None)
            field = RETURN_ERROR_FIELD_MAP.get(code)
            if field is not None and field in form.fields:
                form.add_error(field, error)
            else:
                form.add_error(None, error)
        return

    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = None if field == "__all__" else field
            for error in errors:
                if target is not None and target in form.fields:
                    form.add_error(target, error)
                else:
                    form.add_error(None, error)


class ProductionLineForm(forms.ModelForm):
    class Meta:
        model = ProductionLine
        fields = ["code", "name", "parent"]
        labels = {
            "code": "Kod",
            "name": "Ad",
            "parent": "Üst üretim hattı",
        }
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].required = False
        self.fields["parent"].empty_label = "Üst düzey (üst hat yok)"
        self.fields["parent"].label_from_instance = production_line_choice_label

        all_lines = ProductionLine.objects.order_by("name", "id")
        self.fields["parent"].queryset = all_lines

        visible = all_lines
        if self.instance.pk:
            excluded_ids = {self.instance.pk, *descendant_ids(self.instance)}
            visible = all_lines.exclude(pk__in=excluded_ids)

        self.fields["parent"].widget.choices = [
            ("", self.fields["parent"].empty_label),
            *[
                (str(line.pk), production_line_choice_label(line))
                for line in visible
            ],
        ]
