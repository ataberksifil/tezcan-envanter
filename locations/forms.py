from __future__ import annotations

import uuid

from django import forms

from locations.models import Location


def location_choice_label(location: Location) -> str:
    parts = [location.code, f"({location.name})"]
    if not location.active:
        parts.append("[Pasif]")
    parts.append(f"— {location.pk}")
    return " ".join(parts)


def descendant_ids(location: Location) -> set[uuid.UUID]:
    found: set[uuid.UUID] = set()
    queue = list(location.children.all())
    while queue:
        child = queue.pop()
        if child.pk in found:
            continue
        found.add(child.pk)
        queue.extend(child.children.all())
    return found


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["code", "name", "parent", "can_hold_stock"]
        labels = {
            "code": "Kod",
            "name": "Ad",
            "parent": "Üst lokasyon",
            "can_hold_stock": "Stok tutabilir",
        }
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
            "can_hold_stock": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].required = False
        self.fields["parent"].empty_label = "Üst düzey (üst lokasyon yok)"
        self.fields["parent"].label_from_instance = location_choice_label

        all_locations = Location.objects.order_by("name", "id")
        self.fields["parent"].queryset = all_locations

        visible = all_locations
        if self.instance.pk:
            excluded_ids = {self.instance.pk, *descendant_ids(self.instance)}
            visible = all_locations.exclude(pk__in=excluded_ids)

        self.fields["parent"].widget.choices = [
            ("", self.fields["parent"].empty_label),
            *[
                (str(location.pk), location_choice_label(location))
                for location in visible
            ],
        ]
