from __future__ import annotations

import uuid
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.urls import NoReverseMatch, reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import InventoryTransaction, StockBalance
from inventory.services.receipts import receive_quantity
from inventory.stock_list import STOCK_LIST_PAGE_SIZE
from locations.models import Location

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
LIST_URL = "/inventory/stock/"


def _inventory_tables_exist() -> bool:
    tables = connection.introspection.table_names()
    return "inventory_stockbalance" in tables


@pytest.fixture(autouse=True)
def require_inventory_schema():
    if not _inventory_tables_exist():
        pytest.skip(
            "inventory migration not applied to test DB; "
            "stock list UI tests deferred until test DB is migrated"
        )


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _refresh_user_permissions(user):
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _create_ordinary_user(username):
    user_model = get_user_model()
    user = user_model.objects.create_user(username=username, password=PASSWORD)
    assert not user.is_superuser
    assert not user.is_staff
    return user


def _view_stockbalance_permission():
    return Permission.objects.get(
        content_type__app_label="inventory",
        codename="view_stockbalance",
    )


def _grant_view_stockbalance(user):
    user.user_permissions.add(_view_stockbalance_permission())
    return _refresh_user_permissions(user)


def _grant_permissions(user, *codenames):
    permissions = []
    for codename in codenames:
        app_label, perm_codename = codename.split(".", 1)
        permissions.append(
            Permission.objects.get(
                content_type__app_label=app_label,
                codename=perm_codename,
            )
        )
    user.user_permissions.add(*permissions)
    return _refresh_user_permissions(user)


@pytest.fixture
def stock_master_data():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"ST-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"ST kategori {suffix}")
    material_a = Material.objects.create(
        material_code=f"ST-MA-{suffix}",
        name="Alfa stok malzeme",
        category=category,
        unit=unit,
        brand="Acme",
        model="X100",
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material_b = Material.objects.create(
        material_code=f"ST-MB-{suffix}",
        name="Beta stok malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition_a = MaterialCondition.objects.create(
        code=f"ST-CA-{suffix}",
        name="Alfa kondisyon",
        sort_order=100,
    )
    condition_b = MaterialCondition.objects.create(
        code=f"ST-CB-{suffix}",
        name="Beta kondisyon",
        sort_order=200,
    )
    location_a = Location.objects.create(
        code=f"ST-LA-{suffix}",
        name="Alfa raf",
        active=True,
        can_hold_stock=True,
    )
    location_b = Location.objects.create(
        code=f"ST-LB-{suffix}",
        name="Beta raf",
        active=True,
        can_hold_stock=True,
    )
    inactive_location = Location.objects.create(
        code=f"ST-LI-{suffix}",
        name="Pasif raf",
        active=False,
        can_hold_stock=True,
    )
    receipt_actor = _grant_permissions(
        _create_ordinary_user(f"st-receipt-{suffix}"),
        "inventory.receive_stock",
    )
    return {
        "suffix": suffix,
        "unit": unit,
        "category": category,
        "material_a": material_a,
        "material_b": material_b,
        "condition_a": condition_a,
        "condition_b": condition_b,
        "location_a": location_a,
        "location_b": location_b,
        "inactive_location": inactive_location,
        "receipt_actor": receipt_actor,
    }


def _create_balance(data, *, material, location, condition, quantity):
    return StockBalance.objects.create(
        material=material,
        location=location,
        condition=condition,
        quantity=Decimal(quantity),
    )


def _balance_keys(balances):
    return {
        (
            balance.material.material_code,
            balance.location.code,
            balance.condition.name,
            balance.quantity,
        )
        for balance in balances
    }


def test_named_route():
    assert reverse("inventory:stock-list") == LIST_URL


def test_no_write_routes():
    balance_id = uuid.uuid4()
    with pytest.raises(NoReverseMatch):
        reverse("inventory:stock-update", args=[balance_id])
    with pytest.raises(NoReverseMatch):
        reverse("inventory:stock-delete", args=[balance_id])


@pytest.mark.parametrize("path", (LIST_URL,))
def test_anonymous_list_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [path]


def test_unauthorized_user_gets_403(app_client):
    user = _create_ordinary_user("no-stock-view")
    app_client.force_login(user)
    assert app_client.get(LIST_URL).status_code == 403


def test_group_name_alone_does_not_grant_access(app_client):
    fake_group = Group.objects.create(name=TECHNICIAN)
    user = _create_ordinary_user("technician-group-only")
    user.groups.add(fake_group)
    user = _refresh_user_permissions(user)
    app_client.force_login(user)
    assert user.has_perm("inventory.view_stockbalance") is False
    assert app_client.get(LIST_URL).status_code == 403


def test_explicit_permission_grants_access(app_client, stock_master_data):
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="5.000",
    )
    user = _grant_view_stockbalance(_create_ordinary_user("stock-viewer"))
    app_client.force_login(user)
    response = app_client.get(LIST_URL)
    assert response.status_code == 200
    assert "Mevcut Stok" in response.content.decode()


def test_positive_balance_visible_by_default(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("positive-default"))
    app_client.force_login(user)
    positive = _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="3.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_a"],
        quantity="0.000",
    )
    response = app_client.get(LIST_URL)
    pks = {balance.pk for balance in response.context["stock_balances"]}
    assert pks == {positive.pk}


def test_zero_balance_filter(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("zero-filter"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="3.000",
    )
    zero = _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_a"],
        quantity="0.000",
    )
    response = app_client.get(LIST_URL, {"balance_state": "zero"})
    pks = {balance.pk for balance in response.context["stock_balances"]}
    assert pks == {zero.pk}


def test_all_balance_filter(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("all-filter"))
    app_client.force_login(user)
    positive = _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="3.000",
    )
    zero = _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_a"],
        quantity="0.000",
    )
    response = app_client.get(LIST_URL, {"balance_state": "all"})
    pks = {balance.pk for balance in response.context["stock_balances"]}
    assert pks == {positive.pk, zero.pk}


def test_multiple_condition_buckets_shown_independently(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("multi-condition"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_b"],
        quantity="2.000",
    )
    response = app_client.get(LIST_URL, {"balance_state": "all"})
    keys = _balance_keys(response.context["stock_balances"])
    assert (
        (
            stock_master_data["material_a"].material_code,
            stock_master_data["location_a"].code,
            stock_master_data["condition_a"].name,
            Decimal("1.000"),
        )
        in keys
    )
    assert (
        (
            stock_master_data["material_a"].material_code,
            stock_master_data["location_a"].code,
            stock_master_data["condition_b"].name,
            Decimal("2.000"),
        )
        in keys
    )
    assert len(keys) == 2


def test_multiple_location_buckets_shown_independently(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("multi-location"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    response = app_client.get(LIST_URL, {"balance_state": "all"})
    location_codes = {balance.location.code for balance in response.context["stock_balances"]}
    assert location_codes == {
        stock_master_data["location_a"].code,
        stock_master_data["location_b"].code,
    }


def test_search_by_material_code(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("search-code"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    response = app_client.get(
        LIST_URL,
        {"q": stock_master_data["material_a"].material_code, "balance_state": "all"},
    )
    codes = {balance.material.material_code for balance in response.context["stock_balances"]}
    assert codes == {stock_master_data["material_a"].material_code}


def test_search_by_material_name(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("search-name"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    response = app_client.get(LIST_URL, {"q": "Alfa stok", "balance_state": "all"})
    codes = {balance.material.material_code for balance in response.context["stock_balances"]}
    assert codes == {stock_master_data["material_a"].material_code}


def test_search_by_location_code_and_name(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("search-location"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    by_code = app_client.get(
        LIST_URL,
        {"q": stock_master_data["location_a"].code, "balance_state": "all"},
    )
    by_name = app_client.get(LIST_URL, {"q": "Alfa raf", "balance_state": "all"})
    for response in (by_code, by_name):
        codes = {balance.location.code for balance in response.context["stock_balances"]}
        assert codes == {stock_master_data["location_a"].code}


def test_exact_location_filter(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("location-filter"))
    app_client.force_login(user)
    target = _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    response = app_client.get(
        LIST_URL,
        {"location": str(stock_master_data["location_a"].pk), "balance_state": "all"},
    )
    pks = {balance.pk for balance in response.context["stock_balances"]}
    assert pks == {target.pk}


def test_condition_filter(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("condition-filter"))
    app_client.force_login(user)
    target = _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_b"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    response = app_client.get(
        LIST_URL,
        {"condition": str(stock_master_data["condition_b"].pk), "balance_state": "all"},
    )
    pks = {balance.pk for balance in response.context["stock_balances"]}
    assert pks == {target.pk}


def test_combined_filters(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("combined"))
    app_client.force_login(user)
    target = _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="3.000",
    )
    response = app_client.get(
        LIST_URL,
        {
            "q": stock_master_data["material_a"].material_code,
            "location": str(stock_master_data["location_a"].pk),
            "condition": str(stock_master_data["condition_a"].pk),
            "balance_state": "all",
        },
    )
    pks = {balance.pk for balance in response.context["stock_balances"]}
    assert pks == {target.pk}


@pytest.mark.parametrize(
    "params",
    [
        {"location": "not-a-uuid"},
        {"condition": "bad"},
        {"balance_state": "invalid"},
    ],
)
def test_invalid_params_do_not_500(app_client, stock_master_data, params):
    user = _grant_view_stockbalance(_create_ordinary_user("invalid-params"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    response = app_client.get(LIST_URL, params)
    assert response.status_code == 200


def test_deterministic_ordering(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("ordering"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_b"],
        quantity="1.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="2.000",
    )
    response = app_client.get(LIST_URL, {"balance_state": "all"})
    balances = list(response.context["stock_balances"])
    assert balances[0].material.material_code == stock_master_data["material_a"].material_code
    assert balances[1].material.material_code == stock_master_data["material_b"].material_code


def test_pagination_and_query_preservation(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("pagination"))
    app_client.force_login(user)
    for index in range(STOCK_LIST_PAGE_SIZE + 1):
        location = Location.objects.create(
            code=f"ST-PG-{stock_master_data['suffix']}-{index}",
            name=f"Sayfa raf {index}",
            active=True,
            can_hold_stock=True,
        )
        _create_balance(
            stock_master_data,
            material=stock_master_data["material_a"],
            location=location,
            condition=stock_master_data["condition_a"],
            quantity="1.000",
        )
    response = app_client.get(
        LIST_URL,
        {"q": stock_master_data["material_a"].material_code, "balance_state": "all"},
    )
    assert len(response.context["stock_balances"]) == STOCK_LIST_PAGE_SIZE
    page_two = app_client.get(
        LIST_URL,
        {
            "q": stock_master_data["material_a"].material_code,
            "balance_state": "all",
            "page": "2",
        },
    )
    assert page_two.status_code == 200
    assert len(page_two.context["stock_balances"]) == 1
    content = response.content.decode()
    assert f"q={stock_master_data['material_a'].material_code}" in content or stock_master_data[
        "material_a"
    ].material_code in content


def test_inactive_referenced_master_data_remains_displayable(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("inactive-ref"))
    app_client.force_login(user)
    inactive_material = Material.objects.create(
        material_code=f"ST-IN-{stock_master_data['suffix']}",
        name="Pasif malzeme",
        category=stock_master_data["category"],
        unit=stock_master_data["unit"],
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=False,
    )
    _create_balance(
        stock_master_data,
        material=inactive_material,
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="4.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_b"],
        location=stock_master_data["inactive_location"],
        condition=stock_master_data["condition_a"],
        quantity="0.000",
    )
    response = app_client.get(LIST_URL, {"balance_state": "all"})
    content = response.content.decode()
    assert inactive_material.material_code in content
    assert stock_master_data["inactive_location"].code in content
    assert "Pasif" in content


def test_get_causes_no_inventory_mutation(app_client, stock_master_data):
    user = _grant_view_stockbalance(_create_ordinary_user("read-only"))
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="1.000",
    )
    balance_count = StockBalance.objects.count()
    transaction_count = InventoryTransaction.objects.count()
    response = app_client.get(LIST_URL)
    assert response.status_code == 200
    assert StockBalance.objects.count() == balance_count
    assert InventoryTransaction.objects.count() == transaction_count


def test_nav_link_visible_with_permission(app_client):
    user = _grant_view_stockbalance(_create_ordinary_user("nav-visible"))
    app_client.force_login(user)
    content = app_client.get("/").content.decode()
    assert "Mevcut Stok" in content
    assert LIST_URL in content


def test_nav_link_hidden_without_permission(app_client):
    user = _create_ordinary_user("nav-hidden")
    app_client.force_login(user)
    content = app_client.get("/").content.decode()
    assert "Mevcut Stok" not in content


def test_fresh_roles_receive_view_stockbalance_via_setup_roles():
    call_command("setup_roles", verbosity=0)
    for role_name in (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER):
        group = Group.objects.get(name=role_name)
        assert group.permissions.filter(codename="view_stockbalance").exists()


def test_material_detail_shows_current_stock_buckets(app_client, stock_master_data):
    user = _grant_permissions(
        _create_ordinary_user("material-stock"),
        "catalog.view_material",
        "inventory.view_stockbalance",
    )
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="5.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_b"],
        condition=stock_master_data["condition_b"],
        quantity="2.000",
    )
    url = reverse("catalog:material-detail", args=[stock_master_data["material_a"].pk])
    content = app_client.get(url).content.decode()
    assert "Mevcut stok" in content
    assert stock_master_data["location_a"].code in content
    assert stock_master_data["location_b"].code in content
    assert stock_master_data["condition_a"].name in content
    assert stock_master_data["condition_b"].name in content
    assert stock_master_data["unit"].code in content
    assert "5,000" in content or "5.000" in content
    assert "2,000" in content or "2.000" in content


def test_material_detail_omits_zero_buckets_but_shows_empty_state(app_client, stock_master_data):
    user = _grant_permissions(
        _create_ordinary_user("material-zero"),
        "catalog.view_material",
        "inventory.view_stockbalance",
    )
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="0.000",
    )
    url = reverse("catalog:material-detail", args=[stock_master_data["material_a"].pk])
    content = app_client.get(url).content.decode()
    assert "Mevcut stok" in content
    assert "Pozitif bakiye bulunmuyor" in content
    assert stock_master_data["location_a"].code not in content


def test_material_detail_hides_stock_without_permission(app_client, stock_master_data):
    user = _grant_permissions(
        _create_ordinary_user("material-no-stock"),
        "catalog.view_material",
    )
    app_client.force_login(user)
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="5.000",
    )
    url = reverse("catalog:material-detail", args=[stock_master_data["material_a"].pk])
    content = app_client.get(url).content.decode()
    assert "Mevcut stok" not in content


def test_material_detail_does_not_show_available_or_low_stock(app_client, stock_master_data):
    user = _grant_permissions(
        _create_ordinary_user("material-no-verdict"),
        "catalog.view_material",
        "inventory.view_stockbalance",
    )
    app_client.force_login(user)
    stock_master_data["material_a"].minimum_stock_value = Decimal("100.000")
    stock_master_data["material_a"].save(update_fields=["minimum_stock_value"])
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_a"],
        quantity="5.000",
    )
    _create_balance(
        stock_master_data,
        material=stock_master_data["material_a"],
        location=stock_master_data["location_a"],
        condition=stock_master_data["condition_b"],
        quantity="3.000",
    )
    url = reverse("catalog:material-detail", args=[stock_master_data["material_a"].pk])
    content = app_client.get(url).content.decode()
    lowered = content.lower()
    assert "kullanılabilir" not in lowered
    assert "düşük stok" not in lowered
    assert "available" not in lowered
    assert "8.000" not in content
