from __future__ import annotations

import uuid

from django import forms

from catalog.models import Category, Material, UnitOfMeasure


def category_choice_label(category: Category) -> str:
    parts = [category.name]
    if category.code:
        parts.append(f"({category.code})")
    if not category.active:
        parts.append("[Pasif]")
    parts.append(f"— {category.pk}")
    return " ".join(parts)


def descendant_ids(category: Category) -> set[uuid.UUID]:
    found: set[uuid.UUID] = set()
    queue = list(category.children.all())
    while queue:
        child = queue.pop()
        if child.pk in found:
            continue
        found.add(child.pk)
        queue.extend(child.children.all())
    return found


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["code", "name", "parent"]
        labels = {
            "code": "Kod",
            "name": "Ad",
            "parent": "Üst kategori",
        }
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        self.fields["parent"].required = False
        self.fields["parent"].empty_label = "Üst düzey (üst kategori yok)"
        self.fields["parent"].label_from_instance = category_choice_label

        all_categories = Category.objects.order_by("name", "id")
        # Keep every existing Category in the queryset so a crafted POST with
        # self/descendant still reaches model.clean() instead of failing as an
        # unknown choice. Visible options are narrowed separately.
        self.fields["parent"].queryset = all_categories

        visible = all_categories
        if self.instance.pk:
            excluded_ids = {self.instance.pk, *descendant_ids(self.instance)}
            visible = all_categories.exclude(pk__in=excluded_ids)

        self.fields["parent"].widget.choices = [
            ("", self.fields["parent"].empty_label),
            *[
                (str(category.pk), category_choice_label(category))
                for category in visible
            ],
        ]


def unit_choice_label(unit: UnitOfMeasure) -> str:
    parts = [unit.code, f"({unit.name})"]
    if not unit.active:
        parts.append("[Pasif]")
    parts.append(f"— {unit.pk}")
    return " ".join(parts)


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = [
            "material_code",
            "name",
            "category",
            "brand",
            "model",
            "unit",
            "tracking_mode",
            "minimum_stock_value",
        ]
        labels = {
            "material_code": "Malzeme kodu",
            "name": "Ad",
            "category": "Kategori",
            "brand": "Marka",
            "model": "Model",
            "unit": "Ölçü birimi",
            "tracking_mode": "Takip modu",
            "minimum_stock_value": "Minimum stok eşiği",
        }
        widgets = {
            "material_code": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "brand": forms.TextInput(attrs={"class": "form-control"}),
            "model": forms.TextInput(attrs={"class": "form-control"}),
            "unit": forms.Select(attrs={"class": "form-select"}),
            "tracking_mode": forms.Select(attrs={"class": "form-select"}),
            "minimum_stock_value": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.001"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].required = False
        self.fields["model"].required = False
        self.fields["unit"].required = False
        self.fields["minimum_stock_value"].required = False
        self.fields["unit"].empty_label = "Tanımsız"
        self.fields["category"].label_from_instance = category_choice_label
        self.fields["unit"].label_from_instance = unit_choice_label

        all_categories = Category.objects.order_by("name", "id")
        self.fields["category"].queryset = all_categories
        visible_categories = all_categories.filter(active=True)
        if self.instance.pk and self.instance.category_id:
            visible_categories = visible_categories | all_categories.filter(
                pk=self.instance.category_id
            )
        self.fields["category"].widget.choices = [
            (str(category.pk), category_choice_label(category))
            for category in visible_categories.distinct().order_by("name", "id")
        ]

        all_units = UnitOfMeasure.objects.order_by("code", "id")
        self.fields["unit"].queryset = all_units
        visible_units = all_units.filter(active=True)
        if self.instance.pk and self.instance.unit_id:
            visible_units = visible_units | all_units.filter(pk=self.instance.unit_id)
        self.fields["unit"].widget.choices = [
            ("", self.fields["unit"].empty_label),
            *[
                (str(unit.pk), unit_choice_label(unit))
                for unit in visible_units.distinct().order_by("code", "id")
            ],
        ]


class UnitOfMeasureForm(forms.ModelForm):
    class Meta:
        model = UnitOfMeasure
        fields = ["code", "name"]
        labels = {
            "code": "Kod",
            "name": "Ad",
        }
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
        }
