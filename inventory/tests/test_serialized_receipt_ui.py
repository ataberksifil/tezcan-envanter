from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import InventoryTransaction, SerializedAsset, StockBalance
from inventory.services.receipts import receive_serialized
from locations.models import Location

pytestmark = pytest.mark.django_db


def _grant(user, *codenames):
    permissions = Permission.objects.filter(codename__in=codenames)
    user.user_permissions.add(*permissions)
    return get_user_model().objects.get(pk=user.pk)


@pytest.fixture
def ui_objects(django_user_model):
    suffix = uuid.uuid4().hex[:8]
    category = Category.objects.create(name=f"SUI kategori {suffix}")
    unit = UnitOfMeasure.objects.create(code=f"SUI-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"SUI-S-{suffix}",
        name="Tekil UI malzemesi",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    quantity_material = Material.objects.create(
        material_code=f"SUI-Q-{suffix}",
        name="Miktar UI malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"SUI-C-{suffix}", name="Yeni", sort_order=840
    )
    location = Location.objects.create(
        code=f"SUI-L-{suffix}", name="UI rafı", active=True, can_hold_stock=True
    )
    user = _grant(
        django_user_model.objects.create_user(username=f"sui-{suffix}"),
        "receive_stock",
        "view_material",
        "view_stockbalance",
        "view_inventorytransaction",
    )
    return {
        "material": material,
        "quantity_material": quantity_material,
        "condition": condition,
        "location": location,
        "user": user,
    }


def _post_data(objects, **overrides):
    values = {
        "operation_id": str(uuid.uuid4()),
        "material": str(objects["material"].pk),
        "internal_asset_code": "UI-ASSET-001",
        "serial_number": "UI-SERIAL-001",
        "condition": str(objects["condition"].pk),
        "target_location": str(objects["location"].pk),
        "usage_place": "Tavlama",
        "arrived_on": "2026-09-20",
    }
    values.update(overrides)
    return values


def test_serialized_receipt_route_requires_existing_receive_permission(client, ui_objects):
    url = reverse("inventory:serialized-receipt-create")
    assert url == "/inventory/serialized-receipts/new/"
    user = get_user_model().objects.create_user(username=f"no-receive-{uuid.uuid4().hex[:8]}")
    client.force_login(user)
    assert client.get(url).status_code == 403


def test_serialized_receipt_form_lists_only_active_serialized_materials(
    client, ui_objects
):
    client.force_login(ui_objects["user"])
    response = client.get(reverse("inventory:serialized-receipt-create"))
    assert response.status_code == 200
    content = response.content.decode()
    assert ui_objects["material"].material_code in content
    assert ui_objects["quantity_material"].material_code not in content
    assert "Dahili varlık kodu" in content
    assert "Üretici seri numarası" in content


def test_serialized_receipt_post_creates_asset_and_redirects_to_detail(
    client, ui_objects
):
    client.force_login(ui_objects["user"])
    response = client.post(
        reverse("inventory:serialized-receipt-create"),
        _post_data(ui_objects, serial_number="  UI-SERIAL-001  "),
    )
    transaction_record = InventoryTransaction.objects.get()
    asset = SerializedAsset.objects.get()
    assert response.status_code == 302
    assert response.url == reverse(
        "inventory:receipt-detail", args=[transaction_record.pk]
    )
    assert asset.internal_asset_code == "UI-ASSET-001"
    assert asset.serial_number == "UI-SERIAL-001"
    assert StockBalance.objects.count() == 0

    detail = client.get(response.url)
    content = detail.content.decode()
    assert "UI-ASSET-001" in content
    assert "UI-SERIAL-001" in content
    assert "Stokta" in content


def test_forged_quantity_material_is_rejected_server_side(client, ui_objects):
    client.force_login(ui_objects["user"])
    response = client.post(
        reverse("inventory:serialized-receipt-create"),
        _post_data(ui_objects, material=str(ui_objects["quantity_material"].pk)),
    )
    assert response.status_code == 200
    assert "yalnız tekil takip edilen" in response.content.decode()
    assert SerializedAsset.objects.count() == 0
    assert InventoryTransaction.objects.count() == 0


def test_material_detail_lists_current_serialized_assets_not_quantity_balance(
    client, ui_objects
):
    result = receive_serialized(
        actor=ui_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=ui_objects["material"].pk,
        internal_asset_code="DETAIL-ASSET",
        serial_number="DETAIL-SERIAL",
        condition_id=ui_objects["condition"].pk,
        target_location_id=ui_objects["location"].pk,
    )
    client.force_login(ui_objects["user"])
    response = client.get(
        reverse("catalog:material-detail", args=[ui_objects["material"].pk])
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "Mevcut tekil varlıklar" in content
    assert "DETAIL-ASSET" in content
    assert "DETAIL-SERIAL" in content
    assert ui_objects["location"].code in content
    assert result.serialized_asset.get_current_state_display() in content
    assert "Pozitif bakiye bulunmuyor" not in content


def test_transaction_history_renders_and_searches_serialized_identifiers(
    client, ui_objects
):
    result = receive_serialized(
        actor=ui_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=ui_objects["material"].pk,
        internal_asset_code="HISTORY-ASSET",
        serial_number="HISTORY-SERIAL",
        condition_id=ui_objects["condition"].pk,
        target_location_id=ui_objects["location"].pk,
    )
    client.force_login(ui_objects["user"])
    list_response = client.get(
        reverse("inventory:transaction-history-list"),
        {"q": "HISTORY-SERIAL"},
    )
    list_content = list_response.content.decode()
    assert list_response.status_code == 200
    assert "HISTORY-ASSET" in list_content
    assert "HISTORY-SERIAL" in list_content
    assert "None" not in list_content

    detail_response = client.get(
        reverse("inventory:transaction-history-detail", args=[result.transaction.pk])
    )
    detail_content = detail_response.content.decode()
    assert "HISTORY-ASSET" in detail_content
    assert "HISTORY-SERIAL" in detail_content
    assert ui_objects["condition"].name in detail_content
    assert ui_objects["location"].code in detail_content


def test_navigation_uses_existing_receive_permission_for_both_workflows(
    client, ui_objects
):
    client.force_login(ui_objects["user"])
    content = client.get(reverse("home")).content.decode()
    assert reverse("inventory:receipt-create") in content
    assert reverse("inventory:serialized-receipt-create") in content

