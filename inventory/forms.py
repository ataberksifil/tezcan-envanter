from __future__ import annotations

import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from accounts.models import Employee
from catalog.models import Material, MaterialCondition
from inventory.models import ProductionLine
from locations.models import Location


def production_line_choice_label(production_line: ProductionLine) -> str:
    parts = [production_line.code, f"({production_line.name})"]
    if not production_line.active:
        parts.append("[Pasif]")
    parts.append(f"— {production_line.pk}")
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
    parts = [material.material_code, f"({material.name})"]
    if material.unit_id is not None:
        parts.append(f"[{material.unit.code}]")
    parts.append(f"— {material.pk}")
    return " ".join(parts)


def condition_choice_label(condition: MaterialCondition) -> str:
    parts = [condition.name]
    if not condition.active:
        parts.append("[Pasif]")
    parts.append(f"— {condition.pk}")
    return " ".join(parts)


def stock_location_choice_label(location: Location) -> str:
    parts = [location.code, f"({location.name})"]
    if not location.active:
        parts.append("[Pasif]")
    parts.append(f"— {location.pk}")
    return " ".join(parts)


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
        label="Hedef konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()

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

        receipt_conditions = MaterialCondition.objects.filter(active=True).order_by(
            "sort_order", "name", "id"
        )
        self.fields["condition"].queryset = receipt_conditions
        self.fields["condition"].label_from_instance = condition_choice_label

        receipt_locations = Location.objects.filter(
            active=True,
            can_hold_stock=True,
        ).order_by("code", "name", "id")
        self.fields["target_location"].queryset = receipt_locations
        self.fields["target_location"].label_from_instance = stock_location_choice_label


RECEIPT_ERROR_FIELD_MAP = {
    "inventory.invalid_quantity": "quantity",
    "inventory.inactive_material": "material",
    "inventory.tracking_mode_mismatch": "material",
    "inventory.unit_mismatch": "material",
    "inventory.inactive_condition": "condition",
    "inventory.invalid_destination": "target_location",
    "inventory.invalid_operation_id": "operation_id",
}


def employee_choice_label(employee: Employee) -> str:
    parts = [
        employee.employee_number,
        f"({employee.first_name} {employee.last_name})",
    ]
    if not employee.active:
        parts.append("[Pasif]")
    parts.append(f"— {employee.pk}")
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
