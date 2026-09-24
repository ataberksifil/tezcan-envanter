"""UI shell and shared presentation helpers (UI modernization phase 1)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.utils import timezone

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from core.templatetags.ui import qty
from inventory.forms import QuantityReceiptForm
from locations.models import Location

PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _user(username, *perms):
    user = get_user_model().objects.create_user(username=username, password=PASSWORD)
    for perm in perms:
        app_label, codename = perm.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    return get_user_model().objects.get(pk=user.pk)


def _page(app_client, user, path="/"):
    app_client.force_login(user)
    response = app_client.get(path)
    assert response.status_code == 200
    return response.content.decode()


# --- quantity presentation ------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("80.000"), "80"),
        (Decimal("2.500"), "2,5"),
        (Decimal("0.001"), "0,001"),
        (Decimal("1250.750"), "1.250,75"),
        (Decimal("1000000.000"), "1.000.000"),
        (Decimal("-15.000"), "-15"),
        (Decimal("0"), "0"),
        ("12.340", "12,34"),
        (None, "—"),
    ],
)
def test_qty_filter_uses_turkish_notation_without_padding(value, expected):
    assert qty(value) == expected


def test_qty_filter_does_not_change_the_value():
    value = Decimal("80.000")
    qty(value)
    assert value == Decimal("80.000")


# --- shell navigation -------------------------------------------------------


@pytest.mark.django_db
def test_active_nav_item_is_marked_without_edge_bar(app_client):
    user = _user(f"nav-{uuid.uuid4().hex[:6]}", "inventory.view_stockbalance")
    content = _page(app_client, user, "/inventory/stock/")
    assert 'href="/inventory/stock/" aria-current="page" data-active="true"' in content
    assert 'href="/" aria-current="page"' not in content


@pytest.mark.django_db
def test_home_has_single_primary_scan_search_field(app_client):
    user = _user(f"home-{uuid.uuid4().hex[:6]}", "catalog.view_material")
    content = _page(app_client, user)
    assert 'id="home-search"' in content
    assert "data-primary-search" in content
    assert 'id="global-search"' not in content
    assert 'data-resolve-url="/identification/resolve/"' in content
    assert 'action="/catalog/materials/"' in content


@pytest.mark.django_db
def test_other_pages_use_top_bar_scan_search(app_client):
    user = _user(f"top-{uuid.uuid4().hex[:6]}", "catalog.view_material")
    content = _page(app_client, user, "/catalog/materials/")
    assert 'id="global-search"' in content
    assert 'id="home-search"' not in content


@pytest.mark.django_db
def test_location_only_user_gets_scan_only_field(app_client):
    user = _user(f"loc-{uuid.uuid4().hex[:6]}", "locations.view_location")
    content = _page(app_client, user)
    assert "data-scan-only" in content
    assert 'action="/identification/scan/"' in content


@pytest.mark.django_db
def test_user_without_permissions_sees_explanation_not_actions(app_client):
    user = _user(f"none-{uuid.uuid4().hex[:6]}")
    content = _page(app_client, user)
    assert "Hesabınıza henüz işlem yetkisi atanmamış." in content
    assert "data-scan-search" not in content
    assert "Günlük işlemler" not in content


@pytest.mark.django_db
def test_daily_operations_follow_permissions(app_client):
    technician = _user(
        f"tech-{uuid.uuid4().hex[:6]}",
        "inventory.view_stockbalance",
        "inventory.issue_stock",
    )
    content = _page(app_client, technician)
    assert "Mevcut Stok" in content
    assert "Stok Çıkışı" in content
    assert "Mal Kabul" not in content
    assert "Yerleştir / Transfer Et" not in content

    storekeeper = _user(
        f"store-{uuid.uuid4().hex[:6]}",
        "inventory.view_stockbalance",
        "inventory.receive_stock",
        "inventory.transfer_stock",
        "inventory.issue_stock",
    )
    content = _page(app_client, storekeeper)
    assert "Mal Kabul" in content
    assert "Yerleştir / Transfer Et" in content
    assert 'class="daily-tile" href="/inventory/issues/new/"' not in content


@pytest.mark.django_db
def test_home_follow_up_rows_require_permissions(app_client):
    user = _user(f"plain-{uuid.uuid4().hex[:6]}", "inventory.view_stockbalance")
    content = _page(app_client, user)
    assert "Talep Takip" not in content
    assert "SKT uyarıları" not in content


# --- SKT counts on home ---------------------------------------------------------


def _receipt_tables_exist():
    return "inventory_receiptexpiry" in connection.introspection.table_names()


@pytest.mark.django_db
def test_home_shows_expiry_counts_read_only(app_client):
    if not _receipt_tables_exist():
        pytest.skip("inventory migrations not applied to test DB")
    from inventory.models import InventoryTransaction, ReceiptExpiry
    from inventory.services.receipts import receive_quantity

    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"SK-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"SK kategori {suffix}")
    material = Material.objects.create(
        material_code=f"SK-M-{suffix}",
        name="SKT malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(code=f"SK-C-{suffix}", name="Yeni", sort_order=200)
    location = Location.objects.create(code=f"SK-L-{suffix}", name="Raf", can_hold_stock=True)
    actor = _user(
        f"skt-{uuid.uuid4().hex[:6]}",
        "inventory.receive_stock",
        "inventory.view_inventorytransaction",
    )
    today = timezone.localdate()
    before_expired = ReceiptExpiry.objects.filter(expires_on__lt=today).count()
    for expires_on in (today - timedelta(days=1), today + timedelta(days=5)):
        receive_quantity(
            actor=actor,
            operation_id=uuid.uuid4(),
            material_id=material.pk,
            unit_id=unit.pk,
            condition_id=condition.pk,
            target_location_id=location.pk,
            quantity=Decimal("1"),
            expires_on=expires_on,
        )
    transactions_before = InventoryTransaction.objects.count()

    content = _page(app_client, actor)

    assert "SKT uyarıları" in content
    assert f"{before_expired + 1} süresi geçmiş" in content
    assert "yaklaşıyor" in content
    assert InventoryTransaction.objects.count() == transactions_before


# --- forms ------------------------------------------------------------------------


@pytest.mark.django_db
def test_date_widgets_render_iso_values_for_browser_date_inputs():
    form = QuantityReceiptForm()
    html = str(form["arrived_on"])
    assert f'value="{timezone.localdate().isoformat()}"' in html


@pytest.mark.django_db
def test_forbidden_page_explains_missing_permission(app_client):
    user = _user(f"deny-{uuid.uuid4().hex[:6]}")
    app_client.force_login(user)
    response = app_client.get("/inventory/stock/")
    assert response.status_code == 403
    assert "Bu işlem veya kayıt için yetkiniz yok." in response.content.decode()


@pytest.mark.django_db
def test_phone_top_bar_search_is_short_and_not_duplicated_on_stock_page(app_client):
    user = _user(
        f"phone-{uuid.uuid4().hex[:6]}",
        "catalog.view_material",
        "inventory.view_stockbalance",
    )
    content = _page(app_client, user, "/catalog/materials/")
    assert 'data-short-placeholder="Okut veya ara"' in content
    assert "has-page-search" not in content

    content = _page(app_client, user, "/inventory/stock/")
    assert 'class="topbar-search has-page-search"' in content
    assert 'data-short-placeholder="Okut veya stokta ara"' in content
    assert 'data-resolve-url="/identification/resolve/"' in content


@pytest.mark.django_db
def test_role_label_is_read_from_session_without_group_query(app_client):
    from django.contrib.auth.models import Group
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    group, _ = Group.objects.get_or_create(name=f"UI-ROLE-{uuid.uuid4().hex[:6]}")
    user = _user(f"role-{uuid.uuid4().hex[:6]}")
    user.groups.add(group)
    app_client.force_login(user)
    with CaptureQueriesContext(connection) as queries:
        content = app_client.get("/").content.decode()
    assert group.name in content
    assert not any('SELECT "auth_group"."name"' in q["sql"] for q in queries)
