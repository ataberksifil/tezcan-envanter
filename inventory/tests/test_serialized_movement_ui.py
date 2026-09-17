from __future__ import annotations

import inspect
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory import views
from inventory.forms import (
    SerializedIssueForm,
    SerializedReturnForm,
    SerializedTransferForm,
)
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.services.issues import issue_serialized
from inventory.services.receipts import receive_serialized
from inventory.services.returns import return_serialized
from inventory.services.transfers import transfer_serialized
from locations.models import Location

pytestmark = pytest.mark.django_db
User = get_user_model()


def _grant(user, *permissions):
    for label in permissions:
        app_label, codename = label.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
        )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def movement_ui():
    suffix = uuid.uuid4().hex[:8]
    category = Category.objects.create(name=f"SMUI kategori {suffix}")
    unit = UnitOfMeasure.objects.create(code=f"SMUI-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"SMUI-M-{suffix}",
        name="Tekil UI hareket malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"SMUI-C-{suffix}", name="Yeni", sort_order=910
    )
    other_condition = MaterialCondition.objects.create(
        code=f"SMUI-C2-{suffix}", name="Diğer", sort_order=911
    )
    source = Location.objects.create(
        code=f"SMUI-L1-{suffix}", name="Kaynak raf", can_hold_stock=True
    )
    target = Location.objects.create(
        code=f"SMUI-L2-{suffix}", name="Hedef raf", can_hold_stock=True
    )
    third = Location.objects.create(
        code=f"SMUI-L3-{suffix}", name="Üçüncü raf", can_hold_stock=True
    )
    inactive_target = Location.objects.create(
        code=f"SMUI-LI-{suffix}",
        name="Pasif raf",
        active=False,
        can_hold_stock=True,
    )
    non_stock_target = Location.objects.create(
        code=f"SMUI-LN-{suffix}", name="Stok tutmayan", can_hold_stock=False
    )
    employee = Employee.objects.create(
        employee_number=f"SMUI-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
    )
    inactive_employee = Employee.objects.create(
        employee_number=f"SMUI-EI-{suffix}",
        first_name="Pasif",
        last_name="Çalışan",
        active=False,
    )
    production_line = ProductionLine.objects.create(
        code=f"SMUI-PL-{suffix}", name="Montaj"
    )
    inactive_line = ProductionLine.objects.create(
        code=f"SMUI-PLI-{suffix}", name="Pasif hat", active=False
    )
    actor = _grant(
        User.objects.create_user(username=f"smui-{suffix}"),
        "inventory.receive_stock",
        "inventory.issue_stock",
        "inventory.return_stock",
        "inventory.transfer_stock",
        "inventory.view_stockbalance",
        "inventory.view_inventorytransaction",
        "catalog.view_material",
    )
    received = receive_serialized(
        actor=actor,
        operation_id=uuid.uuid4(),
        material_id=material.pk,
        internal_asset_code=f"SMUI-A-{suffix}",
        serial_number=f"SMUI-SN-{suffix}",
        condition_id=condition.pk,
        target_location_id=source.pk,
    )
    return {
        "material": material,
        "condition": condition,
        "other_condition": other_condition,
        "source": source,
        "target": target,
        "third": third,
        "inactive_target": inactive_target,
        "non_stock_target": non_stock_target,
        "employee": employee,
        "inactive_employee": inactive_employee,
        "production_line": production_line,
        "inactive_line": inactive_line,
        "actor": actor,
        "asset": received.serialized_asset,
        "receipt": received.transaction,
    }


def _issue_url(data):
    return reverse("inventory:serialized-issue-create", args=[data["asset"].pk])


def _transfer_url(data):
    return reverse("inventory:serialized-transfer-create", args=[data["asset"].pk])


def _asset_url(data):
    return reverse("inventory:serialized-asset-detail", args=[data["asset"].pk])


def _issue_data(data, **overrides):
    values = {
        "operation_id": str(uuid.uuid4()),
        "source_location": str(data["source"].pk),
        "condition": str(data["condition"].pk),
        "receiver_employee": str(data["employee"].pk),
        "production_line": str(data["production_line"].pk),
        "usage_location_text": "Pano 7",
    }
    values.update(overrides)
    return values


def _transfer_data(data, **overrides):
    values = {
        "operation_id": str(uuid.uuid4()),
        "source_location": str(data["source"].pk),
        "condition": str(data["condition"].pk),
        "target_location": str(data["target"].pk),
    }
    values.update(overrides)
    return values


def _issue_asset(data, **overrides):
    values = {
        "actor": data["actor"],
        "operation_id": uuid.uuid4(),
        "serialized_asset_id": data["asset"].pk,
        "source_location_id": data["source"].pk,
        "condition_id": data["condition"].pk,
        "receiver_employee_id": data["employee"].pk,
        "production_line_id": data["production_line"].pk,
        "usage_location_text": "Pano 7",
    }
    values.update(overrides)
    return issue_serialized(**values)


def _return_url(data, issue_line):
    return reverse(
        "inventory:serialized-return-create",
        args=[data["asset"].pk, issue_line.pk],
    )


def _return_data(data, **overrides):
    values = {
        "operation_id": str(uuid.uuid4()),
        "target_location": str(data["target"].pk),
    }
    values.update(overrides)
    return values


def test_serialized_return_requires_authentication_and_permission(client, movement_ui):
    issue = _issue_asset(movement_ui)
    url = _return_url(movement_ui, issue.lines[0])
    anonymous = client.get(url)
    assert anonymous.status_code == 302
    assert urlparse(anonymous.url).path == "/accounts/login/"

    user = _grant(
        User.objects.create_user(username=f"no-return-{uuid.uuid4().hex[:8]}"),
        "inventory.view_stockbalance",
    )
    client.force_login(user)
    assert client.get(url).status_code == 403
    assert client.post(url, _return_data(movement_ui)).status_code == 403
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).exists()


@pytest.mark.parametrize("url_helper", (_issue_url, _transfer_url, _asset_url))
def test_anonymous_serialized_routes_redirect_to_login(client, movement_ui, url_helper):
    url = url_helper(movement_ui)
    response = client.get(url)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [url]


@pytest.mark.parametrize(
    ("url_helper", "post_data"),
    ((_issue_url, _issue_data), (_transfer_url, _transfer_data)),
)
def test_missing_movement_permission_is_403(
    client, movement_ui, url_helper, post_data
):
    user = _grant(
        User.objects.create_user(username=f"no-move-{uuid.uuid4().hex[:8]}"),
        "inventory.view_stockbalance",
    )
    client.force_login(user)
    url = url_helper(movement_ui)
    assert client.get(url).status_code == 403
    assert client.post(url, post_data(movement_ui)).status_code == 403
    assert InventoryTransaction.objects.count() == 1


def test_forms_preserve_operation_id_and_never_expose_quantity_or_unit(movement_ui):
    operation_id = uuid.uuid4()
    issue = SerializedIssueForm(
        _issue_data(
            movement_ui,
            operation_id=str(operation_id),
            receiver_employee="",
        ),
        asset=movement_ui["asset"],
    )
    transfer = SerializedTransferForm(
        _transfer_data(
            movement_ui,
            operation_id=str(operation_id),
            target_location="",
        ),
        asset=movement_ui["asset"],
    )
    returned = SerializedReturnForm(
        _return_data(
            movement_ui,
            operation_id=str(operation_id),
            target_location="",
        )
    )
    for form in (issue, transfer, returned):
        assert form.is_valid() is False
        assert form["operation_id"].value() == str(operation_id)
        assert "quantity" not in form.fields
        assert "unit" not in form.fields


def test_gets_are_read_only_and_forms_are_state_aware(client, movement_ui):
    client.force_login(movement_ui["actor"])
    before = InventoryTransaction.objects.count()
    issue_response = client.get(_issue_url(movement_ui))
    transfer_response = client.get(_transfer_url(movement_ui))
    asset_response = client.get(_asset_url(movement_ui))
    assert issue_response.status_code == transfer_response.status_code == 200
    assert asset_response.status_code == 200
    assert InventoryTransaction.objects.count() == before
    assert set(issue_response.context["form"].fields) == {
        "operation_id",
        "source_location",
        "condition",
        "receiver_employee",
        "production_line",
        "usage_location_text",
    }
    target_ids = set(
        transfer_response.context["form"]
        .fields["target_location"]
        .queryset.values_list("pk", flat=True)
    )
    assert movement_ui["source"].pk not in target_ids
    assert movement_ui["target"].pk in target_ids
    assert movement_ui["inactive_target"].pk not in target_ids
    assert movement_ui["non_stock_target"].pk not in target_ids


def test_serialized_issue_success_prg_context_projection_and_no_quantity_artifacts(
    client, movement_ui
):
    client.force_login(movement_ui["actor"])
    response = client.post(_issue_url(movement_ui), _issue_data(movement_ui))
    issue = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.ISSUE
    )
    line = issue.lines.get()
    context = IssueContext.objects.get(transaction=issue)
    movement_ui["asset"].refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("inventory:issue-detail", args=[issue.pk])
    assert line.serialized_asset_id == movement_ui["asset"].pk
    assert line.quantity is None and line.unit_id is None
    assert line.source_location_id == movement_ui["source"].pk
    assert line.condition_id == movement_ui["condition"].pk
    assert context.receiver_employee_id == movement_ui["employee"].pk
    assert context.production_line_id == movement_ui["production_line"].pk
    assert context.usage_location_text == "Pano 7"
    assert movement_ui["asset"].current_state == SerializedAsset.CurrentState.ISSUED
    assert movement_ui["asset"].current_location_id is None
    assert movement_ui["asset"].current_condition_id == movement_ui["condition"].pk
    assert StockBalance.objects.count() == 0

    detail = client.get(response.url).content.decode()
    assert movement_ui["asset"].internal_asset_code in detail
    assert "Birim" not in detail
    assert "İade edilebilir miktar" not in detail


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ("source_location", "belirtilen kaynak konumda değildir"),
        ("condition", "belirtilen kondisyonda değildir"),
    ),
)
def test_serialized_issue_forged_projection_is_friendly(
    client, movement_ui, override, message
):
    client.force_login(movement_ui["actor"])
    value = (
        movement_ui["target"].pk
        if override == "source_location"
        else movement_ui["other_condition"].pk
    )
    response = client.post(
        _issue_url(movement_ui), _issue_data(movement_ui, **{override: str(value)})
    )
    assert response.status_code == 200
    assert message in response.content.decode()
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.ISSUE
    ).exists()


@pytest.mark.parametrize("field", ("receiver_employee", "production_line"))
def test_serialized_issue_inactive_context_is_rejected_without_mutation(
    client, movement_ui, field
):
    client.force_login(movement_ui["actor"])
    value = (
        movement_ui["inactive_employee"].pk
        if field == "receiver_employee"
        else movement_ui["inactive_line"].pk
    )
    response = client.post(
        _issue_url(movement_ui), _issue_data(movement_ui, **{field: str(value)})
    )
    assert response.status_code == 200
    assert response.context["form"].errors[field]
    assert InventoryTransaction.objects.count() == 1


def test_serialized_issue_replay_conflict_and_stale_state_are_clean(
    client, movement_ui
):
    client.force_login(movement_ui["actor"])
    data = _issue_data(movement_ui)
    first = client.post(_issue_url(movement_ui), data)
    replay = client.post(_issue_url(movement_ui), data)
    assert first.status_code == replay.status_code == 302
    assert first.url == replay.url
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.ISSUE
    ).count() == 1

    conflict_data = dict(data, usage_location_text="Başka pano")
    conflict = client.post(_issue_url(movement_ui), conflict_data)
    assert conflict.status_code == 200
    assert "farklı bir envanter isteği" in conflict.content.decode()


def test_serialized_transfer_success_replay_conflict_and_read_models(client, movement_ui):
    client.force_login(movement_ui["actor"])
    data = _transfer_data(movement_ui)
    first = client.post(_transfer_url(movement_ui), data)
    transfer = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    )
    replay = client.post(_transfer_url(movement_ui), data)
    assert first.status_code == replay.status_code == 302
    assert first.url == replay.url == reverse(
        "inventory:transfer-detail", args=[transfer.pk]
    )
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1

    movement_ui["asset"].refresh_from_db()
    line = transfer.lines.get()
    assert movement_ui["asset"].current_state == SerializedAsset.CurrentState.IN_STOCK
    assert movement_ui["asset"].current_location_id == movement_ui["target"].pk
    assert movement_ui["asset"].current_condition_id == movement_ui["condition"].pk
    assert line.quantity is None and line.unit_id is None
    assert StockBalance.objects.count() == 0
    detail = client.get(first.url).content.decode()
    assert movement_ui["asset"].internal_asset_code in detail
    assert "1,000" not in detail and "1.000" not in detail

    conflict = client.post(
        _transfer_url(movement_ui), dict(data, target_location=str(movement_ui["third"].pk))
    )
    assert conflict.status_code == 200
    assert "farklı bir envanter isteği" in conflict.content.decode()


@pytest.mark.parametrize("target_key", ("inactive_target", "non_stock_target"))
def test_serialized_transfer_rejects_ineligible_target(client, movement_ui, target_key):
    client.force_login(movement_ui["actor"])
    response = client.post(
        _transfer_url(movement_ui),
        _transfer_data(
            movement_ui, target_location=str(movement_ui[target_key].pk)
        ),
    )
    assert response.status_code == 200
    assert "aktif ve stok tutabilir" in response.content.decode()
    assert InventoryTransaction.objects.count() == 1


def test_serialized_transfer_stale_source_and_issued_state_do_not_500(
    client, movement_ui
):
    stale_data = _transfer_data(movement_ui)
    transfer_serialized(
        actor=movement_ui["actor"],
        operation_id=uuid.uuid4(),
        serialized_asset_id=movement_ui["asset"].pk,
        source_location_id=movement_ui["source"].pk,
        target_location_id=movement_ui["third"].pk,
        condition_id=movement_ui["condition"].pk,
    )
    client.force_login(movement_ui["actor"])
    stale = client.post(_transfer_url(movement_ui), stale_data)
    assert stale.status_code == 200
    assert "belirtilen kaynak konumda değildir" in stale.content.decode()

    movement_ui["source"] = movement_ui["third"]
    _issue_asset(movement_ui)
    assert client.get(_transfer_url(movement_ui)).status_code == 404
    issued_post = client.post(
        _transfer_url(movement_ui),
        _transfer_data(movement_ui, target_location=str(movement_ui["target"].pk)),
    )
    assert issued_post.status_code == 200
    assert "Yalnız stoktaki tekil varlık transfer edilebilir" in issued_post.content.decode()


def test_serialized_return_success_lineage_prg_replay_and_no_condition_input(
    client, movement_ui
):
    issue = _issue_asset(movement_ui)
    issue_line = issue.lines[0]
    url = _return_url(movement_ui, issue_line)
    client.force_login(movement_ui["actor"])
    page = client.get(url)
    assert page.status_code == 200
    assert set(page.context["form"].fields) == {"operation_id", "target_location"}
    assert movement_ui["employee"].employee_number in page.content.decode()

    data = _return_data(movement_ui)
    first = client.post(url, data)
    returned = InventoryTransaction.objects.get(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    )
    replay = client.post(url, data)
    assert first.status_code == replay.status_code == 302
    assert first.url == replay.url == reverse(
        "inventory:return-detail", args=[returned.pk]
    )
    line = returned.lines.get()
    movement_ui["asset"].refresh_from_db()
    assert line.original_issue_line_id == issue_line.pk
    assert line.serialized_asset_id == movement_ui["asset"].pk
    assert line.quantity is None and line.unit_id is None
    assert line.condition_id == issue_line.condition_id
    assert movement_ui["asset"].current_state == SerializedAsset.CurrentState.IN_STOCK
    assert movement_ui["asset"].current_location_id == movement_ui["target"].pk
    assert movement_ui["asset"].current_condition_id == movement_ui["condition"].pk
    assert StockBalance.objects.count() == 0

    conflict = client.post(
        url,
        dict(data, target_location=str(movement_ui["third"].pk)),
    )
    assert conflict.status_code == 200
    assert "farklı bir envanter isteği" in conflict.content.decode()

    detail = client.get(first.url).content.decode()
    assert str(issue.transaction.pk) in detail
    assert "Toplam iade" not in detail
    assert "1,000" not in detail and "1.000" not in detail


def test_serialized_return_rejects_in_stock_wrong_line_consumed_and_bad_target(
    client, movement_ui
):
    client.force_login(movement_ui["actor"])
    fake_line_id = uuid.uuid4()
    fake_url = _return_url(movement_ui, type("Line", (), {"pk": fake_line_id})())
    assert client.get(fake_url).status_code == 404

    issue = _issue_asset(movement_ui)
    line = issue.lines[0]
    url = _return_url(movement_ui, line)
    bad_target = client.post(
        url,
        _return_data(
            movement_ui, target_location=str(movement_ui["inactive_target"].pk)
        ),
    )
    assert bad_target.status_code == 200
    assert "aktif ve stok tutabilir" in bad_target.content.decode()

    return_serialized(
        actor=movement_ui["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=line.pk,
        serialized_asset_id=movement_ui["asset"].pk,
        target_location_id=movement_ui["target"].pk,
    )
    assert client.get(url).status_code == 404
    second = client.post(url, _return_data(movement_ui))
    assert second.status_code == 200
    assert second.context["form"].non_field_errors()
    assert (
        second.context["form"].errors.as_data()["__all__"][0].code
        == "inventory.serialized_issue_already_returned"
    )


def test_state_and_permission_control_asset_actions(client, movement_ui):
    view_only = _grant(
        User.objects.create_user(username=f"view-{uuid.uuid4().hex[:8]}"),
        "inventory.view_stockbalance",
    )
    client.force_login(view_only)
    view_only_html = client.get(_asset_url(movement_ui)).content.decode()
    assert "Çıkış yap" not in view_only_html
    assert "Transfer et" not in view_only_html
    assert "İade al" not in view_only_html

    client.force_login(movement_ui["actor"])
    in_stock_html = client.get(_asset_url(movement_ui)).content.decode()
    assert "Çıkış yap" in in_stock_html
    assert "Transfer et" in in_stock_html
    assert "İade al" not in in_stock_html
    issue_url = _issue_url(movement_ui)
    transfer_url = _transfer_url(movement_ui)
    assert issue_url in in_stock_html
    assert transfer_url in in_stock_html

    issue = _issue_asset(movement_ui)
    issued_html = client.get(_asset_url(movement_ui)).content.decode()
    assert "İade al" in issued_html
    assert issue_url not in issued_html
    assert transfer_url not in issued_html
    assert reverse(
        "inventory:serialized-return-create",
        args=[movement_ui["asset"].pk, issue.lines[0].pk],
    ) in issued_html


def test_material_detail_and_history_link_to_canonical_asset_surface(
    client, movement_ui
):
    client.force_login(movement_ui["actor"])
    material = client.get(
        reverse("catalog:material-detail", args=[movement_ui["material"].pk])
    )
    assert _asset_url(movement_ui) in material.content.decode()
    history = client.get(
        reverse(
            "inventory:transaction-history-detail",
            args=[movement_ui["receipt"].pk],
        )
    )
    assert _asset_url(movement_ui) in history.content.decode()


def test_csrf_and_views_keep_mutation_inside_services(movement_ui):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(movement_ui["actor"])
    assert csrf_client.post(
        _issue_url(movement_ui), _issue_data(movement_ui)
    ).status_code == 403

    for view_class in (
        views.SerializedIssueCreateView,
        views.SerializedReturnCreateView,
        views.SerializedTransferCreateView,
    ):
        source = inspect.getsource(view_class)
        assert "InventoryTransaction.objects.create" not in source
        assert "InventoryTransactionLine.objects.create" not in source
        assert ".current_state =" not in source
        assert ".current_location =" not in source
        assert "StockBalance" not in source
        assert "IssueContext.objects.create" not in source
