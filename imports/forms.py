from __future__ import annotations

import uuid

from django import forms

from counting.models import PhysicalCountSession
from imports.models import InventoryBaselineCountSessionLink


class BaselinePrepareForm(forms.Form):
    reference = forms.CharField(
        label="Kesim referansı",
        required=False,
        max_length=64,
        strip=True,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Boş bırakılırsa sistem bir referans üretir.",
    )
    sessions = forms.ModelMultipleChoiceField(
        label="Kesim adayı sayım oturumları",
        queryset=PhysicalCountSession.objects.none(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        help_text="Yalnız tamamlanmış, başka kesime bağlı olmayan kesim adayı oturumlar.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        linked_ids = InventoryBaselineCountSessionLink.objects.values_list(
            "physical_count_session_id", flat=True
        )
        self.fields["sessions"].queryset = (
            PhysicalCountSession.objects.filter(
                baseline_candidate=True,
                status=PhysicalCountSession.Status.COMPLETED,
            )
            .exclude(pk__in=linked_ids)
            .select_related("scope_location")
            .order_by("reference_number", "id")
        )
        self.fields["sessions"].label_from_instance = _session_label


class BaselineEstablishForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput())
    explanation = forms.CharField(
        label="Kesim açıklaması",
        min_length=10,
        max_length=2000,
        strip=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 5}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["operation_id"].initial = uuid.uuid4()


def _session_label(session: PhysicalCountSession) -> str:
    return f"{session.reference_number} — {session.scope_location.code}"
