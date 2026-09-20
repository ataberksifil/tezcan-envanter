from __future__ import annotations

import pytest

from locations.display import location_path_label
from locations.models import Location

pytestmark = pytest.mark.django_db


def test_location_path_label_joins_ancestors():
    warehouse = Location.objects.create(
        code="EA",
        name="Elektrik Ambarı",
        can_hold_stock=False,
    )
    rack = Location.objects.create(
        code="R03",
        name="R03",
        parent=warehouse,
        can_hold_stock=False,
    )
    bin_location = Location.objects.create(
        code="G07",
        name="G07",
        parent=rack,
        can_hold_stock=True,
    )
    assert location_path_label(bin_location) == "Elektrik Ambarı (EA) → R03 → G07"
    assert bin_location.path_label() == "Elektrik Ambarı (EA) → R03 → G07"
