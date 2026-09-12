from __future__ import annotations

import inspect
import uuid
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.forms import QuantityTransferForm
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    StockBalance,
)
from inventory import views
from inventory.services.receipts import receive_quantity
from inventory.services.transfers import transfer_quantity
from locations.models import Location

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
CREATE_URL = "/inventory/transfers/new/"


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


def _create_user(username):
    return get_user_model().objects.create_user(username=username, password=PASSWORD)


def _grant(user, *permission_labels):
    permissions = []
    for label in permission_labels:
        app_label, codename = label.split(".", 1)
        permissions.append(
            Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
        )
    user.user_permissions.add(*permissions)
    return _refresh_user_permissions(user)


@pytest.fixture
def transfer_data():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"TUI-U-{suffix}", name="Adet")
    other_unit = UnitOfMeasure.objects.create(code=f"TUI-U2-{suffix}", name="Metre")
    category = Category.objects.create(name=f"TUI kategori {suffix}")
    material = Material.objects.create(
        material_code=f"TUI-M-{suffix}",
        name="Transfer malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    inactive_material = Material.objects.create(
        material_code=f"TUI-MI-{suffix}",
        name="Pasif malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=False,
    )
    serialized_material = Material.objects.create(
        material_code=f"TUI-MS-{suffix}",
        name="Seri malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"TUI-C-{suffix}", name="Transfer kondisyonu", sort_order=800
    )
    inactive_condition = MaterialCondition.objects.create(
        code=f"TUI-CI-{suffix}",
        name="Pasif kondisyon",
        sort_order=801,
        active=False,
    )
    source = Location.objects.create(
        code=f"TUI-S-{suffix}", name="Kaynak rafı", can_hold_stock=True
    )
    target = Location.objects.create(
        code=f"TUI-T-{suffix}", name="Hedef rafı", can_hold_stock=True
    )
    other_target = Location.objects.create(
        code=f"TUI-T2-{suffix}", name="Diğer hedef", can_hold_stock=True
    )
    unrelated = Location.objects.create(
        code=f"TUI-U-{suffix}", name="İlgisiz raf", can_hold_stock=True
    )
    inactive_source = Location.objects.create(
        code=f"TUI-SI-{suffix}",
        name="Pasif kaynak",
        active=False,
        can_hold_stock=True,
    )
    non_stock_source = Location.objects.create(
        code=f"TUI-SN-{suffix}", name="Stok tutmayan kaynak", can_hold_stock=False
    )
    inactive_target = Location.objects.create(
        code=f"TUI-TI-{suffix}",
        name="Pasif hedef",
        active=False,
        can_hold_stock=True,
    )
    non_stock_target = Location.objects.create(
        code=f"TUI-TN-{suffix}", name="Stok tutmayan hedef", can_hold_stock=False
    )
    kernel_actor = _grant(
        _create_user(f"kernel-{suffix}"),
        "inventory.receive_stock",
        "inventory.transfer_stock",
    )
    receive_quantity(
        actor=kernel_actor,
        operation_id=uuid.uuid4(),
        material_id=material.pk,
        unit_id=unit.pk,
        condition_id=condition.pk,
        target_location_id=source.pk,
        quantity=Decimal("10.000"),
    )
    return {
        "unit": unit,
        "other_unit": other_unit,
        "material": material,
        "inactive_material": inactive_material,
        "serialized_material": serialized_material,
        "condition": condition,
        "inactive_condition": inactive_condition,
        "source": source,
        "target": target,
        "other_target": other_target,
        "unrelated": unrelated,
        "inactive_source": inactive_source,
        "non_stock_source": non_stock_source,
        "inactive_target": inactive_target,
        "non_stock_target": non_stock_target,
        "kernel_actor": kernel_actor,
    }


def _post_data(transfer_data, **overrides):
    data = {
        "operation_id": str(uuid.uuid4()),
        "material": str(transfer_data["material"].pk),
        "unit": str(transfer_data["unit"].pk),
        "condition": str(transfer_data["condition"].pk),
        "source_location": str(transfer_data["source"].pk),
        "target_location": str(transfer_data["target"].pk),
        "quantity": "2.000",
    }
    data.update(overrides)
    return data


def _transfer_user(username="transfer-user"):
    return _grant(_create_user(username), "inventory.transfer_stock")


def _post_transfer(app_client, transfer_data, *, user=None, **overrides):
    user = user or _transfer_user(f"poster-{uuid.uuid4().hex[:8]}")
    app_client.force_login(user)
    response = app_client.post(CREATE_URL, _post_data(transfer_data, **overrides))
    return response, user


def test_transfer_named_routes_and_no_mutation_routes():
    tx_id = uuid.uuid4()
    assert reverse("inventory:transfer-create") == CREATE_URL
    assert reverse("inventory:transfer-detail", args=[tx_id]) == (
        f"/inventory/transfers/{tx_id}/"
    )
    with pytest.raises(NoReverseMatch):
        reverse("inventory:transfer-update", args=[tx_id])
    with pytest.raises(NoReverseMatch):
        reverse("inventory:transfer-delete", args=[tx_id])


def test_unauthorized_create_get_and_post_are_denied(app_client, transfer_data):
    app_client.force_login(_create_user("no-transfer"))
    assert app_client.get(CREATE_URL).status_code == 403
    assert app_client.post(CREATE_URL, _post_data(transfer_data)).status_code == 403
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 0


def test_explicit_transfer_permission_allows_create_page(app_client):
    app_client.force_login(_transfer_user("explicit-transfer"))
    response = app_client.get(CREATE_URL)
    assert response.status_code == 200
    assert "Stok transferi" in response.content.decode()
    assert 'name="operation_id"' in response.content.decode()
    assert 'type="hidden"' in response.content.decode()


@pytest.mark.parametrize(
    ("role_name", "expected"),
    ((TECHNICIAN, False), (STOREKEEPER, True), (ADMIN_MANAGER, True)),
)
def test_fresh_default_role_transfer_policy(app_client, role_name, expected):
    call_command("setup_roles", verbosity=0)
    user = _create_user(f"fresh-{role_name.lower()}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.transfer_stock") is expected
    app_client.force_login(user)
    assert app_client.get(CREATE_URL).status_code == (200 if expected else 403)


def test_existing_customized_group_is_non_destructive_on_setup_rerun():
    call_command("setup_roles", verbosity=0)
    role = Group.objects.get(name=STOREKEEPER)
    permission = Permission.objects.get(
        content_type__app_label="inventory", codename="transfer_stock"
    )
    role.permissions.remove(permission)
    before = set(role.permissions.values_list("pk", flat=True))
    call_command("setup_roles", verbosity=0)
    assert set(role.permissions.values_list("pk", flat=True)) == before
    assert not role.permissions.filter(pk=permission.pk).exists()


def test_transfer_navigation_uses_runtime_permission(app_client):
    app_client.force_login(_transfer_user("nav-transfer"))
    content = app_client.get("/").content.decode()
    assert ">Stok Transferi</a>" in content
    assert f'href="{CREATE_URL}"' in content

    app_client.force_login(_create_user("nav-no-transfer"))
    assert ">Stok Transferi</a>" not in app_client.get("/").content.decode()


def test_form_contract_expected_fields_and_operation_id(transfer_data):
    form = QuantityTransferForm()
    assert set(form.fields) == {
        "operation_id",
        "material",
        "unit",
        "condition",
        "source_location",
        "target_location",
        "quantity",
    }
    uuid.UUID(str(form.fields["operation_id"].initial))
    assert isinstance(form.fields["unit"].widget, type(form.fields["operation_id"].widget))

    operation_id = uuid.uuid4()
    invalid = QuantityTransferForm(
        _post_data(transfer_data, operation_id=str(operation_id), quantity="1.2345")
    )
    assert invalid.is_valid() is False
    assert invalid["operation_id"].value() == str(operation_id)
    assert "quantity" in invalid.errors


def test_form_querysets_exclude_ineligible_masters(app_client, transfer_data):
    app_client.force_login(_transfer_user("eligible-options"))
    response = app_client.get(CREATE_URL)
    assert response.status_code == 200
    form = response.context["form"]
    material_ids = set(form.fields["material"].queryset.values_list("pk", flat=True))
    condition_ids = set(form.fields["condition"].queryset.values_list("pk", flat=True))
    source_ids = set(form.fields["source_location"].queryset.values_list("pk", flat=True))
    target_ids = set(form.fields["target_location"].queryset.values_list("pk", flat=True))
    assert transfer_data["material"].pk in material_ids
    assert transfer_data["inactive_material"].pk not in material_ids
    assert transfer_data["serialized_material"].pk not in material_ids
    assert transfer_data["condition"].pk in condition_ids
    assert transfer_data["inactive_condition"].pk not in condition_ids
    assert transfer_data["source"].pk in source_ids
    assert transfer_data["target"].pk in source_ids
    assert transfer_data["inactive_source"].pk not in source_ids
    assert transfer_data["non_stock_source"].pk not in source_ids
    assert transfer_data["target"].pk in target_ids
    assert transfer_data["inactive_target"].pk not in target_ids
    assert transfer_data["non_stock_target"].pk not in target_ids


def test_valid_post_uses_service_and_redirects_to_detail(app_client, transfer_data):
    initial_audit = AuditEvent.objects.count()
    initial_context = IssueContext.objects.count()
    with patch("inventory.views.transfer_quantity", wraps=transfer_quantity) as mocked:
        response, user = _post_transfer(app_client, transfer_data, quantity="2.500")
    assert response.status_code == 302
    result = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    )
    assert response.url == reverse("inventory:transfer-detail", args=[result.pk])
    assert result.lines.count() == 1
    line = result.lines.get()
    assert line.source_location_id == transfer_data["source"].pk
    assert line.target_location_id == transfer_data["target"].pk
    assert line.quantity == Decimal("2.500")
    assert result.acting_user_id == user.pk
    assert StockBalance.objects.get(
        material=transfer_data["material"],
        location=transfer_data["source"],
        condition=transfer_data["condition"],
    ).quantity == Decimal("7.500")
    assert StockBalance.objects.get(
        material=transfer_data["material"],
        location=transfer_data["target"],
        condition=transfer_data["condition"],
    ).quantity == Decimal("2.500")
    assert AuditEvent.objects.count() == initial_audit
    assert IssueContext.objects.count() == initial_context
    mocked.assert_called_once()
    source = inspect.getsource(views.TransferCreateView)
    assert "StockBalance" not in source
    assert "InventoryTransaction.objects.create" not in source
    assert "InventoryTransactionLine.objects.create" not in source


def test_partial_and_full_depletion_and_distinct_target(app_client, transfer_data):
    first, _ = _post_transfer(app_client, transfer_data, quantity="3.000")
    assert first.status_code == 302
    assert StockBalance.objects.get(
        material=transfer_data["material"],
        location=transfer_data["source"],
        condition=transfer_data["condition"],
    ).quantity == Decimal("7.000")

    second, _ = _post_transfer(
        app_client,
        transfer_data,
        target_location=str(transfer_data["other_target"].pk),
        quantity="7.000",
    )
    assert second.status_code == 302
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 2
    source = StockBalance.objects.get(
        material=transfer_data["material"],
        location=transfer_data["source"],
        condition=transfer_data["condition"],
    )
    assert source.quantity == Decimal("0.000")
    assert StockBalance.objects.get(
        material=transfer_data["material"],
        location=transfer_data["other_target"],
        condition=transfer_data["condition"],
    ).quantity == Decimal("7.000")


def test_same_source_and_target_is_friendly_service_error(app_client, transfer_data):
    response, _ = _post_transfer(
        app_client,
        transfer_data,
        target_location=str(transfer_data["source"].pk),
    )
    assert response.status_code == 200
    assert "aynı olamaz" in response.content.decode()
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"material_key": "inactive_material"}, "Pasif malzeme"),
        ({"material_key": "serialized_material"}, "miktar bazlı"),
        ({"unit_key": "other_unit"}, "birimiyle eşleşmiyor"),
        ({"condition_key": "inactive_condition"}, "Pasif kondisyon"),
        ({"source_key": "inactive_source"}, "aktif ve stok tutabilir"),
        ({"source_key": "non_stock_source"}, "aktif ve stok tutabilir"),
        ({"target_key": "inactive_target"}, "aktif ve stok tutabilir"),
        ({"target_key": "non_stock_target"}, "aktif ve stok tutabilir"),
    ),
)
def test_forged_master_data_is_safe_service_validation(
    app_client, transfer_data, overrides, message
):
    post_overrides = {}
    if "material_key" in overrides:
        post_overrides["material"] = str(transfer_data[overrides["material_key"]].pk)
    if "unit_key" in overrides:
        post_overrides["unit"] = str(transfer_data[overrides["unit_key"]].pk)
    if "condition_key" in overrides:
        post_overrides["condition"] = str(transfer_data[overrides["condition_key"]].pk)
    if "source_key" in overrides:
        post_overrides["source_location"] = str(transfer_data[overrides["source_key"]].pk)
    if "target_key" in overrides:
        post_overrides["target_location"] = str(transfer_data[overrides["target_key"]].pk)
    response, _ = _post_transfer(app_client, transfer_data, **post_overrides)
    assert response.status_code == 200
    assert message in response.content.decode()
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()


def test_insufficient_and_stale_source_are_friendly_and_do_not_500(
    app_client, transfer_data
):
    response, _ = _post_transfer(app_client, transfer_data, quantity="10.001")
    assert response.status_code == 200
    assert "yeterli stok yok" in response.content.decode()

    operation_id = str(uuid.uuid4())
    stale_post = _post_data(transfer_data, operation_id=operation_id, quantity="1.000")
    transfer_quantity(
        actor=transfer_data["kernel_actor"],
        operation_id=uuid.uuid4(),
        material_id=transfer_data["material"].pk,
        unit_id=transfer_data["unit"].pk,
        condition_id=transfer_data["condition"].pk,
        source_location_id=transfer_data["source"].pk,
        target_location_id=transfer_data["target"].pk,
        quantity=Decimal("10.000"),
    )
    user = _transfer_user("stale-transfer")
    app_client.force_login(user)
    stale = app_client.post(CREATE_URL, stale_post)
    assert stale.status_code == 200
    assert "yeterli stok yok" in stale.content.decode()
    assert stale.context["form"]["operation_id"].value() == operation_id


def test_replay_and_operation_conflict_and_preserved_operation_id(
    app_client, transfer_data
):
    user = _transfer_user("transfer-replay")
    app_client.force_login(user)
    data = _post_data(transfer_data, quantity="2.000")
    first = app_client.post(CREATE_URL, data)
    second = app_client.post(CREATE_URL, data)
    assert first.status_code == second.status_code == 302
    assert first.url == second.url
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1

    operation_id = str(uuid.uuid4())
    assert app_client.post(
        CREATE_URL, _post_data(transfer_data, operation_id=operation_id, quantity="1.000")
    ).status_code == 302
    conflict = app_client.post(
        CREATE_URL, _post_data(transfer_data, operation_id=operation_id, quantity="3.000")
    )
    assert conflict.status_code == 200
    assert "farklı bir envanter isteği" in conflict.content.decode()
    assert conflict.context["form"]["operation_id"].value() == operation_id

    preserved_id = str(uuid.uuid4())
    invalid = app_client.post(
        CREATE_URL, _post_data(transfer_data, operation_id=preserved_id, quantity="")
    )
    assert invalid.status_code == 200
    assert invalid.context["form"]["operation_id"].value() == preserved_id


def test_transfer_detail_is_read_only_and_shows_required_fields(
    app_client, transfer_data
):
    response, user = _post_transfer(app_client, transfer_data, quantity="4.000")
    detail = app_client.get(response.url)
    assert detail.status_code == 200
    content = detail.content.decode()
    transfer = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    )
    for expected in (
        "Stok transferi",
        transfer_data["material"].material_code,
        transfer_data["material"].name,
        transfer_data["unit"].code,
        transfer_data["condition"].name,
        transfer_data["source"].code,
        transfer_data["target"].code,
        user.username,
        str(transfer.pk),
    ):
        assert expected in content
    assert "4,000" in content or "4.000" in content
    main = content.split("<main", 1)[-1]
    assert 'method="post"' not in main.lower()
    assert "Düzenle" not in main
    assert "Sil" not in main
    assert app_client.post(response.url).status_code == 405


def test_transfer_history_and_material_detail_integration(app_client, transfer_data):
    create_response, user = _post_transfer(app_client, transfer_data, quantity="2.000")
    transfer = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    )
    user = _grant(
        user,
        "inventory.view_inventorytransaction",
        "catalog.view_material",
    )
    app_client.force_login(user)

    history = app_client.get(
        reverse("inventory:transaction-history-list"),
        {"transaction_type": InventoryTransaction.TransactionType.TRANSFER},
    )
    assert history.status_code == 200
    content = history.content.decode()
    assert str(transfer.pk) in content or transfer_data["material"].material_code in content
    assert "Stok transferi" in content
    assert "→" in content
    assert transfer_data["source"].code in content
    assert transfer_data["target"].code in content
    assert len(history.context["transactions"]) == 1

    source_filter = app_client.get(
        reverse("inventory:transaction-history-list"),
        {
            "transaction_type": InventoryTransaction.TransactionType.TRANSFER,
            "location": str(transfer_data["source"].pk),
        },
    )
    assert [row.pk for row in source_filter.context["transactions"]] == [transfer.pk]

    target_filter = app_client.get(
        reverse("inventory:transaction-history-list"),
        {
            "transaction_type": InventoryTransaction.TransactionType.TRANSFER,
            "location": str(transfer_data["target"].pk),
        },
    )
    assert [row.pk for row in target_filter.context["transactions"]] == [transfer.pk]

    unrelated = app_client.get(
        reverse("inventory:transaction-history-list"),
        {
            "transaction_type": InventoryTransaction.TransactionType.TRANSFER,
            "location": str(transfer_data["unrelated"].pk),
        },
    )
    assert list(unrelated.context["transactions"]) == []

    generic_detail = app_client.get(
        reverse("inventory:transaction-history-detail", args=[transfer.pk])
    )
    assert generic_detail.status_code == 200
    detail_content = generic_detail.content.decode()
    assert transfer_data["source"].code in detail_content
    assert transfer_data["target"].code in detail_content
    assert "Alıcı ad" not in detail_content
    assert "Orijinal stok çıkışı" not in detail_content

    material_detail = app_client.get(
        reverse("catalog:material-detail", args=[transfer_data["material"].pk])
    )
    assert material_detail.status_code == 200
    material_content = material_detail.content.decode()
    assert "Stok transferi" in material_content
    assert "→" in material_content
    assert transfer_data["source"].code in material_content
    assert transfer_data["target"].code in material_content
    assert material_content.count("Stok transferi") == 1
    assert create_response.url == reverse(
        "inventory:transfer-detail", args=[transfer.pk]
    )


def test_anonymous_create_redirects_to_login(app_client):
    response = app_client.get(CREATE_URL)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [CREATE_URL]
