from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from catalog.models import Material, UnitOfMeasure
from procurement.services import (
    normalize_currency_code,
    normalize_description,
    normalize_request_no,
    normalize_unit_price,
)


class PurchaseRequestForm(forms.Form):
    request_no = forms.CharField(
        label="Talep no",
        max_length=64,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    request_date = forms.DateField(
        label="Talep tarihi",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
    )
    approval_date = forms.DateField(
        label="Onay tarihi",
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
    )
    note = forms.CharField(
        label="Not",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def clean_request_no(self):
        try:
            return normalize_request_no(self.cleaned_data["request_no"])
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc

    def clean(self):
        cleaned = super().clean()
        request_date = cleaned.get("request_date")
        approval_date = cleaned.get("approval_date")
        if request_date and approval_date and approval_date < request_date:
            self.add_error("approval_date", "Onay tarihi talep tarihinden önce olamaz.")
        return cleaned


class PurchaseRequestLineForm(forms.Form):
    requested_description = forms.CharField(
        label="Talep edilen kalem",
        max_length=500,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    material = forms.ModelChoiceField(
        label="Malzeme",
        required=False,
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    unit = forms.ModelChoiceField(
        label="Birim",
        queryset=UnitOfMeasure.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    requested_quantity = forms.DecimalField(
        label="Talep miktarı",
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )
    lead_time_days = forms.IntegerField(
        label="Termin (gün)",
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
    )
    expected_arrival_date = forms.DateField(
        label="Beklenen geliş",
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
    )
    supplier_name = forms.CharField(
        label="Tedarikçi / firma",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    unit_price = forms.DecimalField(
        label="Birim fiyat",
        required=False,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
    )
    currency_code = forms.CharField(
        label="Para birimi",
        required=False,
        max_length=3,
        widget=forms.TextInput(attrs={"class": "form-control", "maxlength": "3"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = (
            Material.objects.filter(active=True).select_related("unit").order_by("material_code", "name")
        )
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True).order_by("code")

    def clean_requested_description(self):
        try:
            return normalize_description(self.cleaned_data["requested_description"])
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc

    def clean(self):
        cleaned = super().clean()
        material = cleaned.get("material")
        unit = cleaned.get("unit")
        if material is not None and material.unit_id is not None:
            if unit is not None and unit.pk != material.unit_id:
                self.add_error(
                    "unit",
                    "Malzeme birimi talep birimiyle aynı olmalıdır. Birim dönüşümü yapılmaz.",
                )
            cleaned["unit"] = material.unit
        price = cleaned.get("unit_price")
        try:
            normalized_price = normalize_unit_price(price)
            cleaned["currency_code"] = normalize_currency_code(
                cleaned.get("currency_code"),
                price=normalized_price,
            )
            cleaned["unit_price"] = normalized_price
        except ValidationError as exc:
            self.add_error("unit_price", exc.messages[0] if exc.messages else str(exc))
        return cleaned


class MaterialLinkForm(forms.Form):
    material = forms.ModelChoiceField(
        label="Malzeme",
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, unit_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Material.objects.filter(active=True).select_related("unit")
        if unit_id is not None:
            queryset = queryset.filter(unit_id=unit_id)
        self.fields["material"].queryset = queryset.order_by("material_code", "name")
