from __future__ import annotations

import uuid

from django import forms

from catalog.models import Category


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
