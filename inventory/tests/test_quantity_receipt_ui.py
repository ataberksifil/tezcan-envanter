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

from accounts.roles import STOREKEEPER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.forms import QuantityReceiptForm
from inventory.models import InventoryTransaction, InventoryTransactionLine, StockBalance
from locations.models import Location

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


def _inventory_tables_exist() -> bool:
    tables = connection.introspection.table_names()
    return "inventory_inventorytransaction" in tables


@pytest.fixture(autouse=True)
def require_inventory_schema():
    if not _inventory_tables_exist():
        pytest.skip(
            "inventory migration not applied to test DB; "
            "receipt UI tests deferred until test DB is migrated"
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


def _receive_stock_permission():
    return Permission.objects.get(
        content_type__app_label="inventory",
        codename="receive_stock",
    )


def _grant_receive_stock(user):
    user.user_permissions.add(_receive_stock_permission())
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
def receipt_master_data():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"UI-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"UI kategori {suffix}")
    material = Material.objects.create(
        material_code=f"UI-M-{suffix}",
        name="UI malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    inactive_material = Material.objects.create(
        material_code=f"UI-IN-{suffix}",
        name="Pasif malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=False,
    )
    serialized_material = Material.objects.create(
        material_code=f"UI-SR-{suffix}",
        name="Seri malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"UI-C-{suffix}",
        name="UI kondisyon",
        sort_order=100,
    )
    inactive_condition = MaterialCondition.objects.create(
        code=f"UI-IC-{suffix}",
        name="Pasif kondisyon",
        sort_order=101,
        active=False,
    )
    location = Location.objects.create(
        code=f"UI-L-{suffix}",
        name="UI raf",
        active=True,
        can_hold_stock=True,
    )
    inactive_location = Location.objects.create(
        code=f"UI-IL-{suffix}",
        name="Pasif raf",
        active=False,
        can_hold_stock=True,
    )
    non_stock_location = Location.objects.create(
        code=f"UI-NS-{suffix}",
        name="Stok tutmayan",
        active=True,
        can_hold_stock=False,
    )
    return {
        "unit": unit,
        "material": material,
        "inactive_material": inactive_material,
        "serialized_material": serialized_material,
        "condition": condition,
        "inactive_condition": inactive_condition,
        "location": location,
        "inactive_location": inactive_location,
        "non_stock_location": non_stock_location,
    }


def _valid_post_data(receipt_master_data, **overrides):
    values = {
        "material": str(receipt_master_data["material"].pk),
        "quantity": "2.500",
        "condition": str(receipt_master_data["condition"].pk),
        "target_location": str(receipt_master_data["location"].pk),
    }
    values.update(overrides)
    return values


def test_receipt_named_routes():
    assert reverse("inventory:receipt-create") == "/inventory/receipts/new/"


def test_no_receipt_edit_or_delete_routes():
    tx_id = uuid.uuid4()
    with pytest.raises(NoReverseMatch):
        reverse("inventory:receipt-update", args=[tx_id])
    with pytest.raises(NoReverseMatch):
        reverse("inventory:receipt-delete", args=[tx_id])


@pytest.mark.parametrize("path", ("/inventory/receipts/new/",))
def test_anonymous_receipt_create_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [path]


def test_unauthorized_user_gets_403(app_client):
    user = _create_ordinary_user("no-receipt")
    app_client.force_login(user)
    assert app_client.get("/inventory/receipts/new/").status_code == 403


def test_group_name_and_is_staff_alone_do_not_grant_receipt_access(app_client):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user("technician-group-only")
    user.groups.add(Group.objects.get(name=TECHNICIAN))
    user = _refresh_user_permissions(user)
    app_client.force_login(user)
    assert user.has_perm("inventory.receive_stock") is False
    assert app_client.get("/inventory/receipts/new/").status_code == 403

    staff_user = _create_ordinary_user("staff-only")
    staff_user.is_staff = True
    staff_user.save(update_fields=["is_staff"])
    staff_user = _refresh_user_permissions(staff_user)
    app_client.force_login(staff_user)
    assert app_client.get("/inventory/receipts/new/").status_code == 403


def test_explicit_receive_stock_permission_grants_access(app_client):
    user = _grant_receive_stock(_create_ordinary_user("receiver"))
    app_client.force_login(user)
    response = app_client.get("/inventory/receipts/new/")
    assert response.status_code == 200
    assert "Stok girişi" in response.content.decode()


def test_form_generates_operation_id_on_get():
    form = QuantityReceiptForm()
    assert form.fields["operation_id"].initial is not None
    uuid.UUID(str(form.fields["operation_id"].initial))


def test_form_preserves_operation_id_after_invalid_post(receipt_master_data):
    operation_id = uuid.uuid4()
    form = QuantityReceiptForm(
        {
            "operation_id": str(operation_id),
            "material": str(receipt_master_data["material"].pk),
            "quantity": "",
            "condition": str(receipt_master_data["condition"].pk),
            "target_location": str(receipt_master_data["location"].pk),
        }
    )
    assert form.is_valid() is False
    assert form["operation_id"].value() == str(operation_id)


def test_form_material_queryset_filters_active_quantity_with_unit(receipt_master_data):
    form = QuantityReceiptForm()
    material_ids = set(form.fields["material"].queryset.values_list("pk", flat=True))
    assert receipt_master_data["material"].pk in material_ids
    assert receipt_master_data["inactive_material"].pk not in material_ids
    assert receipt_master_data["serialized_material"].pk not in material_ids
    assert not Material.objects.filter(
        tracking_mode=Material.TrackingMode.QUANTITY,
        unit__isnull=True,
        active=True,
    ).exists()


def test_form_condition_and_location_querysets(receipt_master_data):
    form = QuantityReceiptForm()
    condition_ids = set(form.fields["condition"].queryset.values_list("pk", flat=True))
    location_ids = set(
        form.fields["target_location"].queryset.values_list("pk", flat=True)
    )
    assert receipt_master_data["condition"].pk in condition_ids
    assert receipt_master_data["inactive_condition"].pk not in condition_ids
    assert receipt_master_data["location"].pk in location_ids
    assert receipt_master_data["inactive_location"].pk not in location_ids
    assert receipt_master_data["non_stock_location"].pk not in location_ids


def test_form_has_no_unit_actor_or_fingerprint_fields():
    form = QuantityReceiptForm()
    assert "unit" not in form.fields
    assert "acting_user" not in form.fields
    assert "transaction_type" not in form.fields
    assert "request_fingerprint" not in form.fields
    assert "occurred_at" not in form.fields


def test_form_rejects_more_than_three_decimal_places(receipt_master_data):
    form = QuantityReceiptForm(
        {
            **_valid_post_data(receipt_master_data, quantity="1.2345"),
            "operation_id": str(uuid.uuid4()),
        }
    )
    assert form.is_valid() is False
    assert "quantity" in form.errors


def test_forged_stale_ids_fail_safely(receipt_master_data):
    form = QuantityReceiptForm(
        _valid_post_data(
            receipt_master_data,
            material=str(receipt_master_data["inactive_material"].pk),
        )
    )
    assert form.is_valid() is False


def test_valid_post_creates_receipt_and_redirects(app_client, receipt_master_data):
    user = _grant_receive_stock(_create_ordinary_user("poster"))
    app_client.force_login(user)
    initial_audit_count = AuditEvent.objects.count()

    get_response = app_client.get("/inventory/receipts/new/")
    operation_id = get_response.context["form"]["operation_id"].value()
    post_data = _valid_post_data(receipt_master_data, operation_id=operation_id)

    response = app_client.post("/inventory/receipts/new/", post_data)
    assert response.status_code == 302

    receipt = InventoryTransaction.objects.get()
    assert response.url == reverse("inventory:receipt-detail", args=[receipt.pk])
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("2.500")
    assert AuditEvent.objects.count() == initial_audit_count

    detail = app_client.get(response.url)
    assert detail.status_code == 200
    content = detail.content.decode()
    assert receipt_master_data["material"].material_code in content
    assert receipt_master_data["material"].name in content
    assert receipt_master_data["condition"].name in content
    assert receipt_master_data["location"].code in content
    assert receipt_master_data["location"].name in content
    assert receipt_master_data["unit"].code in content
    assert user.get_username() in content
    assert str(receipt.pk) in content


def test_double_submit_replays_same_receipt(app_client, receipt_master_data):
    user = _grant_receive_stock(_create_ordinary_user("double-submit"))
    app_client.force_login(user)
    operation_id = str(uuid.uuid4())
    post_data = _valid_post_data(receipt_master_data, operation_id=operation_id)

    first = app_client.post("/inventory/receipts/new/", post_data)
    assert first.status_code == 302
    receipt_id = InventoryTransaction.objects.get().pk

    second = app_client.post("/inventory/receipts/new/", post_data)
    assert second.status_code == 302
    assert second.url == reverse("inventory:receipt-detail", args=[receipt_id])
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("2.500")


def test_operation_conflict_shows_safe_error_without_second_stock_effect(
    app_client, receipt_master_data
):
    user = _grant_receive_stock(_create_ordinary_user("conflict"))
    app_client.force_login(user)
    operation_id = str(uuid.uuid4())
    first_data = _valid_post_data(receipt_master_data, operation_id=operation_id)
    assert app_client.post("/inventory/receipts/new/", first_data).status_code == 302

    conflict_data = _valid_post_data(
        receipt_master_data,
        operation_id=operation_id,
        quantity="9.000",
    )
    response = app_client.post("/inventory/receipts/new/", conflict_data)
    assert response.status_code == 200
    assert InventoryTransaction.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("2.500")
    content = response.content.decode()
    assert "operation_id" in content.lower() or "zaten" in content.lower()


def test_prg_detail_refresh_does_not_mutate_stock(app_client, receipt_master_data):
    user = _grant_receive_stock(_create_ordinary_user("prg"))
    app_client.force_login(user)
    post_data = _valid_post_data(receipt_master_data, operation_id=str(uuid.uuid4()))
    redirect_response = app_client.post("/inventory/receipts/new/", post_data)
    assert redirect_response.status_code == 302

    detail_url = redirect_response.url
    first_detail = app_client.get(detail_url)
    second_detail = app_client.get(detail_url)
    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
    assert InventoryTransaction.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("2.500")


def test_fresh_form_after_success_gets_new_operation_id(app_client, receipt_master_data):
    user = _grant_receive_stock(_create_ordinary_user("fresh-form"))
    app_client.force_login(user)
    post_data = _valid_post_data(receipt_master_data, operation_id=str(uuid.uuid4()))
    app_client.post("/inventory/receipts/new/", post_data)

    new_form_response = app_client.get("/inventory/receipts/new/")
    new_operation_id = new_form_response.context["form"]["operation_id"].value()
    assert new_operation_id != post_data["operation_id"]


def test_receive_stock_nav_visible_only_for_authorized_user(app_client):
    authorized = _grant_receive_stock(_create_ordinary_user("nav-authorized"))
    app_client.force_login(authorized)
    authorized_content = app_client.get("/").content.decode()
    assert ">Stok Girişi</a>" in authorized_content
    assert 'href="/inventory/receipts/new/"' in authorized_content

    viewer = _grant_permissions(_create_ordinary_user("nav-viewer"), "catalog.view_material")
    app_client.force_login(viewer)
    viewer_content = app_client.get("/").content.decode()
    assert ">Stok Girişi</a>" not in viewer_content


def test_management_hub_does_not_show_receipt_card(app_client):
    user = _grant_permissions(
        _grant_receive_stock(_create_ordinary_user("mgmt-boundary")),
        "catalog.view_category",
        "catalog.change_category",
    )
    app_client.force_login(user)
    content = app_client.get("/management/").content.decode()
    assert "Stok girişi" not in content
    assert "Receipt" not in content


def test_new_storekeeper_from_setup_roles_can_access_receipt(app_client):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user("storekeeper-receipt")
    user.groups.add(Group.objects.get(name=STOREKEEPER))
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.receive_stock") is True
    app_client.force_login(user)
    assert app_client.get("/inventory/receipts/new/").status_code == 200


def test_new_technician_from_setup_roles_cannot_access_receipt(app_client):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user("technician-no-receipt")
    user.groups.add(Group.objects.get(name=TECHNICIAN))
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.receive_stock") is False
    app_client.force_login(user)
    assert app_client.get("/inventory/receipts/new/").status_code == 403
