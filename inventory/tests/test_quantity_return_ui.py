from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse

from accounts.models import Employee
from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.forms import QuantityReturnForm
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    ProductionLine,
    StockBalance,
)
from inventory.services.issues import issue_quantity
from inventory.services.receipts import receive_quantity
from inventory.services.returns import return_quantity
from locations.models import Location

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
CREATE_URL = "/inventory/returns/new/"


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
def return_data():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"RUI-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"RUI kategori {suffix}")
    material = Material.objects.create(
        material_code=f"RUI-M-{suffix}",
        name="İade malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"RUI-C-{suffix}", name="İade kondisyonu", sort_order=700
    )
    source = Location.objects.create(
        code=f"RUI-S-{suffix}", name="Çıkış rafı", can_hold_stock=True
    )
    target = Location.objects.create(
        code=f"RUI-T-{suffix}", name="İade rafı", can_hold_stock=True
    )
    inactive_target = Location.objects.create(
        code=f"RUI-I-{suffix}",
        name="Pasif raf",
        active=False,
        can_hold_stock=True,
    )
    non_stock_target = Location.objects.create(
        code=f"RUI-N-{suffix}", name="Stok tutmayan", can_hold_stock=False
    )
    employee = Employee.objects.create(
        employee_number=f"RUI-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
    )
    production_line = ProductionLine.objects.create(
        code=f"RUI-P-{suffix}", name="İade Test Hattı"
    )
    kernel_actor = _grant(
        _create_user(f"kernel-{suffix}"),
        "inventory.receive_stock",
        "inventory.issue_stock",
        "inventory.return_stock",
    )
    receipt = receive_quantity(
        actor=kernel_actor,
        operation_id=uuid.uuid4(),
        material_id=material.pk,
        unit_id=unit.pk,
        condition_id=condition.pk,
        target_location_id=source.pk,
        quantity=Decimal("10.000"),
    )
    issue = issue_quantity(
        actor=kernel_actor,
        operation_id=uuid.uuid4(),
        material_id=material.pk,
        unit_id=unit.pk,
        condition_id=condition.pk,
        source_location_id=source.pk,
        quantity=Decimal("10.000"),
        receiver_employee_id=employee.pk,
        production_line_id=production_line.pk,
        usage_location_text="Pano A-12",
    )
    return {
        "unit": unit,
        "material": material,
        "condition": condition,
        "source": source,
        "target": target,
        "inactive_target": inactive_target,
        "non_stock_target": non_stock_target,
        "employee": employee,
        "production_line": production_line,
        "kernel_actor": kernel_actor,
        "receipt_line": receipt.lines[0],
        "issue": issue.transaction,
        "issue_line": issue.lines[0],
    }


def _post_data(return_data, **overrides):
    data = {
        "operation_id": str(uuid.uuid4()),
        "original_issue_line": str(return_data["issue_line"].pk),
        "target_location": str(return_data["target"].pk),
        "quantity": "2.000",
    }
    data.update(overrides)
    return data


def _return_user(username="return-user"):
    return _grant(_create_user(username), "inventory.return_stock")


def _post_return(app_client, return_data, *, user=None, **overrides):
    user = user or _return_user(f"poster-{uuid.uuid4().hex[:8]}")
    app_client.force_login(user)
    response = app_client.post(CREATE_URL, _post_data(return_data, **overrides))
    return response, user


def test_return_named_routes_and_no_mutation_routes():
    tx_id = uuid.uuid4()
    assert reverse("inventory:return-create") == CREATE_URL
    assert reverse("inventory:return-detail", args=[tx_id]) == f"/inventory/returns/{tx_id}/"
    with pytest.raises(NoReverseMatch):
        reverse("inventory:return-update", args=[tx_id])
    with pytest.raises(NoReverseMatch):
        reverse("inventory:return-delete", args=[tx_id])


def test_anonymous_create_redirects_to_login(app_client):
    response = app_client.get(CREATE_URL)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [CREATE_URL]


def test_unauthorized_create_get_and_post_are_denied(app_client, return_data):
    app_client.force_login(_create_user("no-return"))
    assert app_client.get(CREATE_URL).status_code == 403
    assert app_client.post(CREATE_URL, _post_data(return_data)).status_code == 403
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).count() == 0


def test_explicit_return_permission_allows_create_page(app_client):
    app_client.force_login(_return_user("explicit-return"))
    response = app_client.get(CREATE_URL)
    assert response.status_code == 200
    assert "Stok iadesi" in response.content.decode()
    assert 'name="operation_id"' in response.content.decode()
    assert 'type="hidden"' in response.content.decode()


@pytest.mark.parametrize(
    ("role_name", "expected"),
    ((TECHNICIAN, False), (STOREKEEPER, True), (ADMIN_MANAGER, True)),
)
def test_fresh_default_role_return_policy(app_client, role_name, expected):
    call_command("setup_roles", verbosity=0)
    user = _create_user(f"fresh-{role_name.lower()}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.return_stock") is expected
    app_client.force_login(user)
    assert app_client.get(CREATE_URL).status_code == (200 if expected else 403)


def test_existing_customized_group_is_non_destructive_on_setup_rerun():
    call_command("setup_roles", verbosity=0)
    role = Group.objects.get(name=STOREKEEPER)
    permission = Permission.objects.get(
        content_type__app_label="inventory", codename="return_stock"
    )
    role.permissions.remove(permission)
    before = set(role.permissions.values_list("pk", flat=True))
    call_command("setup_roles", verbosity=0)
    assert set(role.permissions.values_list("pk", flat=True)) == before
    assert not role.permissions.filter(pk=permission.pk).exists()


def test_return_navigation_uses_runtime_permission(app_client):
    app_client.force_login(_return_user("nav-return"))
    content = app_client.get("/").content.decode()
    assert ">Stok İadesi</a>" in content
    assert f'href="{CREATE_URL}"' in content

    app_client.force_login(_create_user("nav-no-return"))
    assert ">Stok İadesi</a>" not in app_client.get("/").content.decode()


def test_form_contract_operation_id_and_decimal_validation(return_data):
    form = QuantityReturnForm()
    assert set(form.fields) == {
        "operation_id",
        "original_issue_line",
        "target_location",
        "quantity",
    }
    uuid.UUID(str(form.fields["operation_id"].initial))

    operation_id = uuid.uuid4()
    invalid = QuantityReturnForm(
        _post_data(
            return_data,
            operation_id=str(operation_id),
            quantity="1.2345",
        )
    )
    assert invalid.is_valid() is False
    assert invalid["operation_id"].value() == str(operation_id)
    assert "quantity" in invalid.errors


def test_create_page_lists_only_eligible_issue_lines_and_targets(
    app_client, return_data
):
    app_client.force_login(_return_user("eligible-options"))
    response = app_client.get(CREATE_URL)
    form = response.context["form"]
    issue_ids = set(
        form.fields["original_issue_line"].queryset.values_list("pk", flat=True)
    )
    target_ids = set(
        form.fields["target_location"].queryset.values_list("pk", flat=True)
    )
    assert return_data["issue_line"].pk in issue_ids
    assert return_data["receipt_line"].pk not in issue_ids
    assert return_data["source"].pk in target_ids
    assert return_data["target"].pk in target_ids
    assert return_data["inactive_target"].pk not in target_ids
    assert return_data["non_stock_target"].pk not in target_ids
    content = response.content.decode()
    assert return_data["material"].material_code in content
    assert "Kalan: 10.000" in content
    assert return_data["employee"].employee_number in content


def test_fully_returned_issue_line_is_not_offered(return_data):
    return_quantity(
        actor=return_data["kernel_actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=return_data["issue_line"].pk,
        target_location_id=return_data["target"].pk,
        quantity=Decimal("10.000"),
    )
    assert not QuantityReturnForm().fields["original_issue_line"].queryset.filter(
        pk=return_data["issue_line"].pk
    ).exists()


@pytest.mark.parametrize("master", ("material", "condition"))
def test_inactive_master_issue_line_is_not_offered(return_data, master):
    model = type(return_data[master])
    model.objects.filter(pk=return_data[master].pk).update(active=False)
    assert not QuantityReturnForm().fields["original_issue_line"].queryset.filter(
        pk=return_data["issue_line"].pk
    ).exists()


@pytest.mark.parametrize(
    ("master", "message"),
    (("material", "Pasif malzeme iade edilemez"), ("condition", "Pasif kondisyon")),
)
def test_forged_inactive_original_master_is_safe_service_validation(
    app_client, return_data, master, message
):
    model = type(return_data[master])
    model.objects.filter(pk=return_data[master].pk).update(active=False)
    response, _ = _post_return(app_client, return_data)
    assert response.status_code == 200
    assert message in response.content.decode()


def test_valid_partial_return_uses_service_and_redirects_to_detail(
    app_client, return_data
):
    initial_audit_count = AuditEvent.objects.count()
    with patch("inventory.views.return_quantity", wraps=return_quantity) as mocked:
        response, user = _post_return(app_client, return_data, quantity="2.500")
    assert response.status_code == 302
    result = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    )
    assert response.url == reverse("inventory:return-detail", args=[result.pk])
    assert result.lines.count() == 1
    line = result.lines.get()
    assert line.original_issue_line_id == return_data["issue_line"].pk
    assert line.source_location_id is None
    assert line.target_location_id == return_data["target"].pk
    assert line.quantity == Decimal("2.500")
    assert result.acting_user_id == user.pk
    assert StockBalance.objects.get(
        material=return_data["material"],
        location=return_data["target"],
        condition=return_data["condition"],
    ).quantity == Decimal("2.500")
    assert AuditEvent.objects.count() == initial_audit_count
    mocked.assert_called_once()


def test_target_may_differ_from_or_equal_original_issue_source(app_client, return_data):
    first, _ = _post_return(app_client, return_data, quantity="2.000")
    assert first.status_code == 302
    second, _ = _post_return(
        app_client,
        return_data,
        target_location=str(return_data["source"].pk),
        quantity="3.000",
    )
    assert second.status_code == 302
    targets = set(
        InventoryTransactionLine.objects.filter(
            transaction__transaction_type=InventoryTransaction.TransactionType.RETURN
        ).values_list("target_location_id", flat=True)
    )
    assert targets == {return_data["source"].pk, return_data["target"].pk}


def test_full_remaining_return_succeeds_and_line_disappears(app_client, return_data):
    _post_return(app_client, return_data, quantity="2.000")
    response, _ = _post_return(app_client, return_data, quantity="8.000")
    assert response.status_code == 302
    assert not QuantityReturnForm().fields["original_issue_line"].queryset.filter(
        pk=return_data["issue_line"].pk
    ).exists()


@pytest.mark.parametrize("forged_kind", ("receipt", "return"))
def test_forged_non_issue_line_is_safe_service_validation(
    app_client, return_data, forged_kind
):
    forged_line = return_data["receipt_line"]
    if forged_kind == "return":
        created = return_quantity(
            actor=return_data["kernel_actor"],
            operation_id=uuid.uuid4(),
            original_issue_line_id=return_data["issue_line"].pk,
            target_location_id=return_data["target"].pk,
            quantity=Decimal("1.000"),
        )
        forged_line = created.lines[0]
    response, _ = _post_return(
        app_client,
        return_data,
        original_issue_line=str(forged_line.pk),
        quantity="1.000",
    )
    assert response.status_code == 200
    assert "Orijinal stok çıkış satırı geçersiz" in response.content.decode()


def test_missing_original_line_is_safe_form_validation(app_client, return_data):
    response, _ = _post_return(
        app_client, return_data, original_issue_line=str(uuid.uuid4())
    )
    assert response.status_code == 200
    assert response.context["form"].errors.as_data()["original_issue_line"][0].code == (
        "invalid_choice"
    )


@pytest.mark.parametrize("target_key", ("inactive_target", "non_stock_target"))
def test_forged_invalid_destination_is_safe_service_validation(
    app_client, return_data, target_key
):
    response, _ = _post_return(
        app_client,
        return_data,
        target_location=str(return_data[target_key].pk),
    )
    assert response.status_code == 200
    assert "aktif ve stok tutabilir" in response.content.decode()


def test_quantity_over_remaining_has_friendly_cap_error(app_client, return_data):
    response, _ = _post_return(app_client, return_data, quantity="10.001")
    assert response.status_code == 200
    assert "kalan iade edilebilir miktarı aşamaz" in response.content.decode()
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).exists()


def test_stale_fully_returned_form_uses_service_cap_and_does_not_500(
    app_client, return_data
):
    operation_id = str(uuid.uuid4())
    stale_post = _post_data(return_data, operation_id=operation_id, quantity="1.000")
    return_quantity(
        actor=return_data["kernel_actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=return_data["issue_line"].pk,
        target_location_id=return_data["target"].pk,
        quantity=Decimal("10.000"),
    )
    user = _return_user("stale-return")
    app_client.force_login(user)
    response = app_client.post(CREATE_URL, stale_post)
    assert response.status_code == 200
    assert "kalan iade edilebilir miktarı aşamaz" in response.content.decode()
    assert response.context["form"]["operation_id"].value() == operation_id


def test_replay_redirects_to_same_detail_without_duplicate_effect(app_client, return_data):
    user = _return_user("return-replay")
    app_client.force_login(user)
    data = _post_data(return_data, quantity="2.000")
    first = app_client.post(CREATE_URL, data)
    second = app_client.post(CREATE_URL, data)
    assert first.status_code == second.status_code == 302
    assert first.url == second.url
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).count() == 1
    assert StockBalance.objects.get(
        material=return_data["material"], location=return_data["target"]
    ).quantity == Decimal("2.000")


def test_operation_conflict_is_safe_and_preserves_operation_id(app_client, return_data):
    user = _return_user("return-conflict")
    app_client.force_login(user)
    operation_id = str(uuid.uuid4())
    assert app_client.post(
        CREATE_URL, _post_data(return_data, operation_id=operation_id, quantity="2.000")
    ).status_code == 302
    response = app_client.post(
        CREATE_URL, _post_data(return_data, operation_id=operation_id, quantity="3.000")
    )
    assert response.status_code == 200
    assert "farklı bir envanter isteği" in response.content.decode()
    assert response.context["form"]["operation_id"].value() == operation_id
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).count() == 1


def test_return_detail_is_read_only_and_uses_issue_snapshots(app_client, return_data):
    response, user = _post_return(app_client, return_data, quantity="4.000")
    detail_url = response.url
    Employee.objects.filter(pk=return_data["employee"].pk).update(
        first_name="Değişen", last_name="Kişi", employee_number="NEW-EMP"
    )
    ProductionLine.objects.filter(pk=return_data["production_line"].pk).update(
        code="NEW-LINE", name="Değişen Hat"
    )
    detail = app_client.get(detail_url)
    assert detail.status_code == 200
    content = detail.content.decode()
    for expected in (
        "Stok iadesi",
        return_data["material"].material_code,
        return_data["condition"].name,
        return_data["target"].code,
        return_data["source"].code,
        str(return_data["issue"].pk),
        "10,000",
        "4,000",
        "6,000",
        "Ayşe",
        "Yılmaz",
        return_data["employee"].employee_number,
        return_data["production_line"].code,
        return_data["production_line"].name,
        "Pano A-12",
        user.username,
    ):
        assert expected in content
    assert "NEW-EMP" not in content
    assert "NEW-LINE" not in content


def test_return_detail_denies_user_without_return_permission(app_client, return_data):
    response, _ = _post_return(app_client, return_data)
    app_client.force_login(_create_user("detail-denied"))
    assert app_client.get(response.url).status_code == 403


def test_issue_detail_return_action_is_permission_and_remaining_aware(
    app_client, return_data
):
    issue_url = reverse("inventory:issue-detail", args=[return_data["issue"].pk])
    permitted = _grant(
        _create_user("issue-return-action"),
        "inventory.issue_stock",
        "inventory.return_stock",
    )
    app_client.force_login(permitted)
    content = app_client.get(issue_url).content.decode()
    assert "İade başlat" in content
    assert f"issue_line={return_data['issue_line'].pk}" in content

    issue_only = _grant(_create_user("issue-only"), "inventory.issue_stock")
    app_client.force_login(issue_only)
    assert "İade başlat" not in app_client.get(issue_url).content.decode()

    return_quantity(
        actor=return_data["kernel_actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=return_data["issue_line"].pk,
        target_location_id=return_data["target"].pk,
        quantity=Decimal("10.000"),
    )
    app_client.force_login(permitted)
    assert "İade başlat" not in app_client.get(issue_url).content.decode()


def test_issue_line_query_parameter_preselects_original_line(app_client, return_data):
    app_client.force_login(_return_user("preselect-return"))
    response = app_client.get(
        CREATE_URL, {"issue_line": str(return_data["issue_line"].pk)}
    )
    assert response.status_code == 200
    assert response.context["form"]["original_issue_line"].value() == str(
        return_data["issue_line"].pk
    )


def test_return_history_filter_location_detail_and_material_integration(
    app_client, return_data
):
    create_response, user = _post_return(app_client, return_data, quantity="2.000")
    return_tx = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    )
    user = _grant(
        user,
        "inventory.view_inventorytransaction",
        "catalog.view_material",
    )
    app_client.force_login(user)

    history = app_client.get(
        reverse("inventory:transaction-history-list"),
        {
            "transaction_type": InventoryTransaction.TransactionType.RETURN,
            "location": str(return_data["target"].pk),
        },
    )
    assert history.status_code == 200
    content = history.content.decode()
    assert str(return_tx.pk) in content
    assert "Stok iadesi" in content
    assert return_data["target"].code in content
    assert len(history.context["transactions"]) == 1

    wrong_location = app_client.get(
        reverse("inventory:transaction-history-list"),
        {
            "transaction_type": InventoryTransaction.TransactionType.RETURN,
            "location": str(return_data["source"].pk),
        },
    )
    assert len(wrong_location.context["transactions"]) == 0

    generic_detail = app_client.get(
        reverse("inventory:transaction-history-detail", args=[return_tx.pk])
    )
    assert generic_detail.status_code == 200
    assert str(return_data["issue"].pk) in generic_detail.content.decode()

    material_detail = app_client.get(
        reverse("catalog:material-detail", args=[return_data["material"].pk])
    )
    assert material_detail.status_code == 200
    material_content = material_detail.content.decode()
    assert "Stok iadesi" in material_content
    assert return_data["target"].code in material_content
    assert create_response.url == reverse("inventory:return-detail", args=[return_tx.pk])
