from __future__ import annotations

import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import formset_factory
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from locations.models import Location


class ServiceValidatedModelChoiceField(forms.ModelChoiceField):
    """Show the usable queryset while letting the service judge stale/forged rows."""

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


class CountedAtTokenField(forms.CharField):
    widget = forms.HiddenInput()

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        parsed = parse_datetime(str(value))
        if parsed is None:
            raise ValidationError("Sayım zamanı geçersiz.", code="invalid")
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed


def counted_at_token(value):
    if value is None:
        return ""
    return value.isoformat()


class PhysicalCountSessionCreateForm(forms.Form):
    reference_number = forms.CharField(
        label="Sayım referansı",
        min_length=1,
        max_length=64,
        strip=True,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    scope_location = ServiceValidatedModelChoiceField(
        label="Kapsam kök lokasyonu",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Oturum bu lokasyonun alt ağacını kapsar. "
            "Çakışan açık sayım oturumu oluşturulamaz."
        ),
    )
    baseline_candidate = forms.BooleanField(
        label="Kesim (baseline) adayı",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text=(
            "İşaretlenirse oturum rutin mutabakat üretmez; sayılan envanter "
            "yalnız kontrollü kesime beslenir. Zorunlu satırlar fiziksel "
            "sayılmalıdır; açık ‘sayılmadı’ kabul edilmez."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        locations = Location.objects.filter(active=True).order_by("code", "name", "id")
        self.fields["scope_location"].queryset = locations
        self.fields["scope_location"].label_from_instance = _location_label


class QuantityCountLineForm(forms.Form):
    line_id = forms.UUIDField(widget=forms.HiddenInput())
    counted_at_token = CountedAtTokenField()
    counted_quantity = forms.DecimalField(
        label="Sayılan miktar",
        required=False,
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control count-input",
                "step": "0.001",
                "inputmode": "decimal",
                # Enter moves to the next line's count instead of saving part of the sheet.
                "data-enter-next": "true",
            }
        ),
    )


QuantityCountLineFormSet = formset_factory(QuantityCountLineForm, extra=0)


class UnexpectedQuantityForm(forms.Form):
    material = ServiceValidatedModelChoiceField(
        label="Malzeme",
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    location = ServiceValidatedModelChoiceField(
        label="Lokasyon",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    condition = ServiceValidatedModelChoiceField(
        label="Kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    counted_quantity = forms.DecimalField(
        label="Sayılan miktar",
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0"),
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.001", "inputmode": "decimal"}
        ),
        help_text="Beklenen sistem bakiyesi sıfırdır. Birim dönüşümü yoktur.",
    )

    def __init__(self, *args, location_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = (
            Material.objects.filter(
                active=True,
                tracking_mode=Material.TrackingMode.QUANTITY,
                unit__isnull=False,
            )
            .select_related("unit")
            .order_by("material_code", "name", "id")
        )
        self.fields["material"].label_from_instance = _material_label
        locations = location_queryset
        if locations is None:
            locations = Location.objects.filter(active=True, can_hold_stock=True)
        self.fields["location"].queryset = locations.order_by("code", "name", "id")
        self.fields["location"].label_from_instance = _location_label
        self.fields["condition"].queryset = MaterialCondition.objects.filter(
            active=True
        ).order_by("sort_order", "name", "id")
        self.fields["condition"].label_from_instance = _condition_label


class SerializedIdentityLookupForm(forms.Form):
    identity = forms.CharField(
        label="Kimlik",
        required=False,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "TZ1A:… / TZ1L:… / dahili varlık kodu",
                "autocomplete": "off",
            }
        ),
        help_text=(
            "Barkod, QR veya dahili varlık kodu yalnız kimliği çözer; "
            "sayım kaydı için ayrıca onay gerekir."
        ),
    )


class SerializedObserveForm(forms.Form):
    serialized_asset_id = forms.UUIDField(widget=forms.HiddenInput())
    counted_at_token = CountedAtTokenField()
    observed_location = ServiceValidatedModelChoiceField(
        label="Gözlenen lokasyon",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    observed_condition = ServiceValidatedModelChoiceField(
        label="Gözlenen kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, location_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        locations = location_queryset
        if locations is None:
            locations = Location.objects.filter(active=True, can_hold_stock=True)
        self.fields["observed_location"].queryset = locations.order_by(
            "code", "name", "id"
        )
        self.fields["observed_location"].label_from_instance = _location_label
        self.fields["observed_condition"].queryset = MaterialCondition.objects.filter(
            active=True
        ).order_by("sort_order", "name", "id")
        self.fields["observed_condition"].label_from_instance = _condition_label


class CandidateSerializedForm(forms.Form):
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
        required=False,
        max_length=255,
        strip=True,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Boş bırakılırsa kayıtlı seri yoktur. Yetkili varlık oluşturulmaz.",
    )
    observed_location = ServiceValidatedModelChoiceField(
        label="Gözlenen lokasyon",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    observed_condition = ServiceValidatedModelChoiceField(
        label="Gözlenen kondisyon",
        queryset=MaterialCondition.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, location_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(
            active=True,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        ).order_by("material_code", "name", "id")
        self.fields["material"].label_from_instance = _material_label
        locations = location_queryset
        if locations is None:
            locations = Location.objects.filter(active=True, can_hold_stock=True)
        self.fields["observed_location"].queryset = locations.order_by(
            "code", "name", "id"
        )
        self.fields["observed_location"].label_from_instance = _location_label
        self.fields["observed_condition"].queryset = MaterialCondition.objects.filter(
            active=True
        ).order_by("sort_order", "name", "id")
        self.fields["observed_condition"].label_from_instance = _condition_label


class QuantityDiscrepancyApprovalForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    explanation = forms.CharField(
        label="Onay açıklaması",
        min_length=10,
        max_length=2000,
        strip=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()


class QuantityDiscrepancyRejectionForm(forms.Form):
    reason = forms.CharField(
        label="Ret gerekçesi",
        required=False,
        max_length=2000,
        strip=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text="İsteğe bağlıdır. En fazla 2000 karakter.",
    )


def _location_label(location: Location) -> str:
    parts = [location.code]
    if location.name and location.name != location.code:
        parts.append(f"({location.name})")
    if not location.active:
        parts.append("[Pasif]")
    if not location.can_hold_stock:
        parts.append("[Stok tutmaz]")
    return " ".join(parts)


def _material_label(material: Material) -> str:
    parts = [material.material_code, f"({material.name})"]
    if material.unit_id is not None:
        parts.append(f"[{material.unit.code}]")
    return " ".join(parts)


def _condition_label(condition: MaterialCondition) -> str:
    parts = [condition.name]
    if not condition.active:
        parts.append("[Pasif]")
    return " ".join(parts)
