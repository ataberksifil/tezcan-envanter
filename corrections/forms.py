from decimal import Decimal

from django import forms

from catalog.models import Material, MaterialCondition
from core.formatting import format_quantity
from corrections.evidence import MAX_EVIDENCE_BYTES
from corrections.models import CorrectionRequest
from inventory.forms import (
    condition_choice_label,
    disambiguate_choice_labels,
    material_choice_label,
)
from inventory.models import InventoryTransaction, InventoryTransactionLine
from locations.models import Location


def original_line_choice_label(line: InventoryTransactionLine) -> str:
    """Readable ledger line: number, material, quantity, condition and direction."""
    parts = [
        f"Satır {line.line_number}: {line.material.material_code} {line.material.name}",
        f"{format_quantity(line.quantity)} {line.unit.code}",
        line.condition.name,
    ]
    if line.source_location_id:
        parts.append(f"{line.source_location.code} kaynağından azalış")
    if line.target_location_id:
        parts.append(f"{line.target_location.code} hedefine artış")
    return ", ".join(parts)


def location_choice_label(location: Location) -> str:
    label = f"{location.code} — {location.name}"
    return label if location.active else f"{label} [Pasif]"


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

    def value_from_datadict(self, data, files, name):
        if hasattr(files, "getlist"):
            return files.getlist(name)
        value = files.get(name)
        if value in (None, ""):
            return []
        return value


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            raise forms.ValidationError(self.error_messages["required"], code="required")
        items = data if isinstance(data, (list, tuple)) else [data]
        single_file_clean = super().clean
        cleaned = [single_file_clean(item, initial) for item in items if item]
        if not cleaned:
            raise forms.ValidationError(self.error_messages["required"], code="required")
        return cleaned


class CorrectionRequestForm(forms.Form):
    original_line = forms.ModelChoiceField(
        label="Düzeltilecek satır",
        queryset=InventoryTransactionLine.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    original_location = forms.ModelChoiceField(
        label="Etkilenen özgün konum",
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    effect_type = forms.ChoiceField(
        label="Düzeltme türü",
        choices=CorrectionRequest.EffectType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity_effect = forms.DecimalField(
        label="Miktar etkisi",
        max_digits=18,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
        help_text=(
            "Miktar düzeltmesinde pozitif stok artırır, negatif azaltır. "
            "Kimlik düzeltmesinde pozitif taşınacak miktarı girin."
        ),
    )
    corrected_material = forms.ModelChoiceField(
        label="Doğru malzeme",
        queryset=Material.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    corrected_location = forms.ModelChoiceField(
        label="Doğru konum",
        queryset=Location.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    corrected_condition = forms.ModelChoiceField(
        label="Doğru kondisyon",
        queryset=MaterialCondition.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    explanation = forms.CharField(
        label="Açıklama",
        min_length=10,
        max_length=2000,
        strip=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 5}),
    )
    evidence = MultipleFileField(
        label="Kanıt fotoğrafları",
        required=True,
        widget=MultipleFileInput(
            attrs={
                "class": "form-control",
                "multiple": True,
                "accept": "image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp",
            }
        ),
        help_text=(
            "En az bir fotoğraf zorunludur. Kabul edilen biçimler: JPEG, PNG, WebP. "
            f"Dosya başına en fazla {MAX_EVIDENCE_BYTES // (1024 * 1024)} MiB. "
            "HEIC/HEIF desteklenmez; cihazınızdan JPEG, PNG veya WebP olarak "
            "dışa aktarın."
        ),
    )

    def __init__(self, *args, original_transaction, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_transaction = original_transaction
        self.fields["original_line"].queryset = (
            original_transaction.lines.filter(
                corrected_line__isnull=True,
                material__tracking_mode=Material.TrackingMode.QUANTITY,
            )
            .select_related(
                "material", "unit", "condition", "source_location", "target_location"
            )
            .order_by("line_number")
        )
        self.fields["original_line"].label_from_instance = original_line_choice_label
        self.fields["original_location"].label_from_instance = location_choice_label
        self.fields["corrected_location"].label_from_instance = location_choice_label
        self.fields["original_location"].queryset = Location.objects.order_by(
            "code", "id"
        )
        self.fields["corrected_material"].queryset = Material.objects.filter(
            active=True,
            tracking_mode=Material.TrackingMode.QUANTITY,
            unit__isnull=False,
        ).select_related("unit").order_by("material_code", "id")
        self.fields["corrected_location"].queryset = Location.objects.filter(
            active=True, can_hold_stock=True
        ).order_by("code", "id")
        self.fields["corrected_condition"].queryset = (
            MaterialCondition.objects.filter(active=True).order_by(
                "sort_order", "name", "id"
            )
        )
        self.fields["corrected_material"].label_from_instance = material_choice_label
        disambiguate_choice_labels(self.fields["corrected_material"], "material_code")
        self.fields["corrected_condition"].label_from_instance = condition_choice_label
        disambiguate_choice_labels(self.fields["corrected_condition"], "name")

    def clean(self):
        cleaned = super().clean()
        line = cleaned.get("original_line")
        location = cleaned.get("original_location")
        effect_type = cleaned.get("effect_type")
        quantity = cleaned.get("quantity_effect")
        if line is not None and line.transaction_id != self.original_transaction.pk:
            self.add_error("original_line", "Satır seçilen işleme ait değil.")
        if line is not None and location is not None and location.pk not in {
            line.source_location_id,
            line.target_location_id,
        }:
            self.add_error(
                "original_location", "Konum seçilen satırın source/target konumu değil."
            )
        corrected_fields = (
            cleaned.get("corrected_material"),
            cleaned.get("corrected_location"),
            cleaned.get("corrected_condition"),
        )
        if effect_type == CorrectionRequest.EffectType.QUANTITY:
            if quantity == Decimal("0"):
                self.add_error("quantity_effect", "Miktar etkisi sıfır olamaz.")
            if any(value is not None for value in corrected_fields):
                raise forms.ValidationError(
                    "Miktar düzeltmesinde doğru kimlik alanlarını boş bırakın."
                )
        elif effect_type == CorrectionRequest.EffectType.IDENTITY:
            if quantity is not None and quantity <= 0:
                self.add_error(
                    "quantity_effect", "Kimlik düzeltmesi miktarı pozitif olmalıdır."
                )
            if any(value is None for value in corrected_fields):
                raise forms.ValidationError(
                    "Kimlik düzeltmesinde doğru malzeme, konum ve kondisyon zorunludur."
                )
            if line is not None and location is not None and all(corrected_fields):
                corrected_identity = (
                    corrected_fields[0].pk,
                    corrected_fields[1].pk,
                    corrected_fields[2].pk,
                )
                if corrected_identity == (
                    line.material_id,
                    location.pk,
                    line.condition_id,
                ):
                    raise forms.ValidationError(
                        "Kimlik düzeltmesi malzeme, konum veya kondisyonu değiştirmelidir."
                    )
        return cleaned


class CorrectionRejectionForm(forms.Form):
    rejection_reason = forms.CharField(
        label="Ret gerekçesi (opsiyonel)",
        required=False,
        max_length=2000,
        strip=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
