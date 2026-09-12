from __future__ import annotations

import uuid

from django import forms

from inventory.models import ProductionLine


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
