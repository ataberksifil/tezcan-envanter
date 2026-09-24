"""Finding a material: stock search, catalog stock summary, detail quick actions (UI Faz 2)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from catalog.forms import MaterialForm
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import StockBalance
from locations.models import Location

pytestmark = pytest.mark.django_db


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _user(*perms):
    user = get_user_model().objects.create_user(
        username=f"find-{uuid.uuid4().hex[:8]}", password="synthetic-test-password-only"
    )
    for perm in perms:
        app_label, codename = perm.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    return get_user_model().objects.get(pk=user.pk)


@pytest.fixture
def workshop():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"FD-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"FD kategori {suffix}")
    tape = Material.objects.create(
        material_code=f"FD-TAPE-{suffix}",
        name=f"İzole bant {suffix}",
        category=category,
        unit=unit,
        brand="Acme",
        model="PTFE-12",
        search_keywords=f"teflon{suffix} sızdırmazlık",
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    breaker = Material.objects.create(
        material_code=f"FD-MCB-{suffix}",
        name=f"Sigorta 63A {suffix}",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(code=f"FD-C-{suffix}", name="Yeni", sort_order=500)
    shelf_a = Location.objects.create(code=f"FD-RAF-A-{suffix}", name="Raf A", can_hold_stock=True)
    shelf_b = Location.objects.create(code=f"FD-RAF-B-{suffix}", name="Raf B", can_hold_stock=True)
    for location, quantity in ((shelf_a, "1250.500"), (shelf_b, "4.000")):
        StockBalance.objects.create(
            material=tape, location=location, condition=condition, quantity=Decimal(quantity)
        )
    return {
        "suffix": suffix,
        "tape": tape,
        "breaker": breaker,
        "condition": condition,
        "shelf_a": shelf_a,
        "unit": unit,
    }


def test_stock_search_matches_keywords_and_every_word_in_any_order(app_client, workshop):
    app_client.force_login(_user("inventory.view_stockbalance"))
    url = reverse("inventory:stock-list")

    by_keyword = app_client.get(url, {"q": f"TEFLON{workshop['suffix']}"}).content.decode()
    assert workshop["tape"].material_code in by_keyword

    reordered = app_client.get(url, {"q": f"bant izole {workshop['suffix']}"}).content.decode()
    assert workshop["tape"].material_code in reordered

    by_model = app_client.get(url, {"q": "ptfe-12"}).content.decode()
    assert workshop["tape"].material_code in by_model

    miss = app_client.get(url, {"q": f"bant 63A {workshop['suffix']}"}).content.decode()
    assert workshop["tape"].material_code not in miss


def test_material_list_shows_stock_summary_only_with_stock_permission(app_client, workshop):
    url = reverse("catalog:material-list")
    query = {"q": workshop["suffix"]}

    app_client.force_login(_user("catalog.view_material"))
    content = app_client.get(url, query).content.decode()
    assert workshop["tape"].material_code in content
    assert "1.254,5" not in content
    assert "konumda" not in content

    app_client.force_login(_user("catalog.view_material", "inventory.view_stockbalance"))
    content = app_client.get(url, query).content.decode()
    assert '<span class="qty">1.254,5<span class="qty-unit">' in content
    assert "2 konumda" in content
    assert "Stokta yok" in content  # the breaker has no stock


def test_material_detail_offers_per_location_transfer_only_with_permission(app_client, workshop):
    url = reverse("catalog:material-detail", args=[workshop["tape"].pk])
    transfer_link = (
        f"?material={workshop['tape'].pk}&amp;source_location={workshop['shelf_a'].pk}"
        f"&amp;condition={workshop['condition'].pk}"
    )

    app_client.force_login(_user("catalog.view_material", "inventory.view_stockbalance"))
    content = app_client.get(url).content.decode()
    assert '<span class="qty">1.250,5<span class="qty-unit">' in content
    assert transfer_link not in content

    app_client.force_login(
        _user("catalog.view_material", "inventory.view_stockbalance", "inventory.transfer_stock")
    )
    content = app_client.get(url).content.decode()
    assert transfer_link in content


def test_model_field_enter_does_not_submit_material_form():
    html = str(MaterialForm()["model"])
    assert 'data-scan-field="true"' in html
    assert 'data-model-scan-target="true"' in html
