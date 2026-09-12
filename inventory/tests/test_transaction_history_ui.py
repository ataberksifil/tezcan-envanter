from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from accounts.models import Employee
from accounts.roles import (
    ADMIN_MANAGER,
    SAFE_CATALOG_PERMISSION_LABELS,
    STOREKEEPER,
    TECHNICIAN,
)
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    ProductionLine,
    StockBalance,
)
from inventory.services.issues import issue_quantity
from inventory.services.receipts import receive_quantity
from inventory.transaction_history import TRANSACTION_HISTORY_PAGE_SIZE
from locations.models import Location

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
LIST_URL = "/inventory/transactions/"


def _inventory_tables_exist() -> bool:
    tables = connection.introspection.table_names()
    return "inventory_inventorytransaction" in tables


@pytest.fixture(autouse=True)
def require_inventory_schema():
    if not _inventory_tables_exist():
        pytest.skip(
            "inventory migration not applied to test DB; "
            "transaction history UI tests deferred until test DB is migrated"
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


def _view_inventorytransaction_permission():
    return Permission.objects.get(
        content_type__app_label="inventory",
        codename="view_inventorytransaction",
    )


def _grant_view_inventorytransaction(user):
    user.user_permissions.add(_view_inventorytransaction_permission())
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
def history_master_data():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"TH-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"TH kategori {suffix}")
    material_a = Material.objects.create(
        material_code=f"TH-MA-{suffix}",
        name="Alfa malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material_b = Material.objects.create(
        material_code=f"TH-MB-{suffix}",
        name="Beta malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition_a = MaterialCondition.objects.create(
        code=f"TH-CA-{suffix}",
        name="Alfa kondisyon",
        sort_order=400,
    )
    condition_b = MaterialCondition.objects.create(
        code=f"TH-CB-{suffix}",
        name="Beta kondisyon",
        sort_order=401,
    )
    receipt_location = Location.objects.create(
        code=f"TH-RL-{suffix}",
        name="Giriş rafı",
        active=True,
        can_hold_stock=True,
    )
    issue_location = Location.objects.create(
        code=f"TH-IL-{suffix}",
        name="Çıkış rafı",
        active=True,
        can_hold_stock=True,
    )
    other_location = Location.objects.create(
        code=f"TH-OL-{suffix}",
        name="Diğer raf",
        active=True,
        can_hold_stock=True,
    )
    receiver = Employee.objects.create(
        employee_number=f"TH-E-{suffix}",
        first_name="Ali",
        last_name="Veli",
    )
    production_line = ProductionLine.objects.create(
        code=f"TH-PL-{suffix}",
        name="Test Hattı",
    )
    receipt_actor = _grant_permissions(
        _create_ordinary_user(f"receipt-{suffix}"),
        "inventory.receive_stock",
    )
    issue_actor = _grant_permissions(
        _create_ordinary_user(f"issue-{suffix}"),
        "inventory.issue_stock",
    )
    StockBalance.objects.create(
        material=material_a,
        location=issue_location,
        condition=condition_a,
        quantity=Decimal("100.000"),
    )
    return {
        "suffix": suffix,
        "unit": unit,
        "category": category,
        "material_a": material_a,
        "material_b": material_b,
        "condition_a": condition_a,
        "condition_b": condition_b,
        "receipt_location": receipt_location,
        "issue_location": issue_location,
        "other_location": other_location,
        "receiver": receiver,
        "production_line": production_line,
        "receipt_actor": receipt_actor,
        "issue_actor": issue_actor,
    }


def _create_receipt(
    data,
    *,
    material=None,
    location=None,
    condition=None,
    quantity="5.000",
    occurred_at=None,
):
    material = material or data["material_a"]
    location = location or data["receipt_location"]
    condition = condition or data["condition_a"]
    kwargs = {
        "actor": data["receipt_actor"],
        "operation_id": uuid.uuid4(),
        "material_id": material.pk,
        "unit_id": material.unit_id,
        "condition_id": condition.pk,
        "target_location_id": location.pk,
        "quantity": Decimal(quantity),
    }
    if occurred_at is not None:
        with patch("inventory.services.receipts.timezone.now", return_value=occurred_at):
            return receive_quantity(**kwargs).transaction
    return receive_quantity(**kwargs).transaction


def _create_issue(
    data,
    *,
    material=None,
    location=None,
    condition=None,
    quantity="2.000",
    usage_location_text="Pano 7",
    occurred_at=None,
):
    material = material or data["material_a"]
    location = location or data["issue_location"]
    condition = condition or data["condition_a"]
    kwargs = {
        "actor": data["issue_actor"],
        "operation_id": uuid.uuid4(),
        "material_id": material.pk,
        "unit_id": material.unit_id,
        "condition_id": condition.pk,
        "source_location_id": location.pk,
        "quantity": Decimal(quantity),
        "receiver_employee_id": data["receiver"].pk,
        "production_line_id": data["production_line"].pk,
        "usage_location_text": usage_location_text,
    }
    if occurred_at is not None:
        with patch("inventory.services.issues.timezone.now", return_value=occurred_at):
            return issue_quantity(**kwargs).transaction
    return issue_quantity(**kwargs).transaction


def _main_content(response) -> str:
    content = response.content.decode()
    start = content.index("<main")
    end = content.index("</main>")
    return content[start:end]


def _transaction_material_codes(transactions):
    codes = set()
    for transaction in transactions:
        codes.add(transaction.lines.all()[0].material.material_code)
    return codes


def _transaction_types(transactions):
    return {transaction.transaction_type for transaction in transactions}


def _transaction_actor_usernames(transactions):
    return {transaction.acting_user.username for transaction in transactions}


def _transaction_location_codes(transactions):
    codes = set()
    for transaction in transactions:
        line = transaction.lines.all()[0]
        if transaction.transaction_type == InventoryTransaction.TransactionType.ISSUE:
            if line.source_location is not None:
                codes.add(line.source_location.code)
        else:
            if line.target_location is not None:
                codes.add(line.target_location.code)
    return codes


@pytest.fixture
def sample_transactions(history_master_data):
    receipt = _create_receipt(history_master_data)
    issue = _create_issue(history_master_data)
    return {"receipt": receipt, "issue": issue}


def test_named_routes():
    assert reverse("inventory:transaction-history-list") == LIST_URL
    tx_id = uuid.uuid4()
    assert (
        reverse("inventory:transaction-history-detail", args=[tx_id])
        == f"/inventory/transactions/{tx_id}/"
    )


def test_no_edit_or_delete_routes():
    tx_id = uuid.uuid4()
    with pytest.raises(NoReverseMatch):
        reverse("inventory:transaction-update", args=[tx_id])
    with pytest.raises(NoReverseMatch):
        reverse("inventory:transaction-delete", args=[tx_id])


@pytest.mark.parametrize("path", (LIST_URL,))
def test_anonymous_list_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [path]


def test_anonymous_detail_redirects_to_login(app_client, sample_transactions):
    detail_url = reverse(
        "inventory:transaction-history-detail",
        args=[sample_transactions["receipt"].pk],
    )
    response = app_client.get(detail_url)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"


def test_unauthorized_user_gets_403(app_client):
    user = _create_ordinary_user("no-history")
    app_client.force_login(user)
    assert app_client.get(LIST_URL).status_code == 403


def test_group_name_alone_does_not_grant_access(app_client):
    fake_group = Group.objects.create(name=TECHNICIAN)
    user = _create_ordinary_user("technician-group-only")
    user.groups.add(fake_group)
    user = _refresh_user_permissions(user)
    app_client.force_login(user)
    assert user.has_perm("inventory.view_inventorytransaction") is False
    assert app_client.get(LIST_URL).status_code == 403


def test_explicit_permission_grants_list_and_detail(app_client, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("viewer"))
    app_client.force_login(user)
    list_response = app_client.get(LIST_URL)
    assert list_response.status_code == 200
    assert "Envanter hareketleri" in list_response.content.decode()

    detail_url = reverse(
        "inventory:transaction-history-detail",
        args=[sample_transactions["issue"].pk],
    )
    detail_response = app_client.get(detail_url)
    assert detail_response.status_code == 200
    assert "Envanter hareketi" in detail_response.content.decode()


def test_receipt_row_rendering(app_client, history_master_data, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("receipt-row"))
    app_client.force_login(user)
    content = app_client.get(LIST_URL).content.decode()
    assert "Stok girişi" in content
    assert history_master_data["material_a"].material_code in content
    assert history_master_data["receipt_location"].code in content
    assert history_master_data["receipt_actor"].username in content


def test_issue_row_rendering(app_client, history_master_data, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("issue-row"))
    app_client.force_login(user)
    content = app_client.get(LIST_URL).content.decode()
    assert "Stok çıkışı" in content
    assert history_master_data["issue_location"].code in content
    assert history_master_data["issue_actor"].username in content


def test_issue_detail_shows_snapshots(app_client, history_master_data, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("snapshots"))
    app_client.force_login(user)
    issue = sample_transactions["issue"]
    detail_url = reverse("inventory:transaction-history-detail", args=[issue.pk])
    content = app_client.get(detail_url).content.decode()
    assert history_master_data["receiver"].first_name in content
    assert history_master_data["receiver"].last_name in content
    assert history_master_data["receiver"].employee_number in content
    assert history_master_data["production_line"].code in content
    assert history_master_data["production_line"].name in content
    assert "Pano 7" in content


def test_historical_issue_snapshot_unchanged_after_master_rename(
    app_client, history_master_data, sample_transactions
):
    user = _grant_view_inventorytransaction(_create_ordinary_user("historical"))
    app_client.force_login(user)
    issue = sample_transactions["issue"]
    detail_url = reverse("inventory:transaction-history-detail", args=[issue.pk])

    Employee.objects.filter(pk=history_master_data["receiver"].pk).update(
        first_name="Yeni",
        last_name="Ad",
        employee_number="NEW-999",
    )
    ProductionLine.objects.filter(pk=history_master_data["production_line"].pk).update(
        code="NEW-CODE",
        name="Yeni Hat",
    )

    content = app_client.get(detail_url).content.decode()
    assert "Ali" in content
    assert "Veli" in content
    assert history_master_data["receiver"].employee_number in content
    assert history_master_data["production_line"].code in content
    assert history_master_data["production_line"].name in content
    assert "NEW-999" not in content
    assert "NEW-CODE" not in content
    assert "Yeni Hat" not in content


def test_material_search(app_client, history_master_data, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("material-search"))
    app_client.force_login(user)
    response = app_client.get(LIST_URL, {"q": history_master_data["material_a"].material_code})
    codes = _transaction_material_codes(response.context["transactions"])
    assert history_master_data["material_a"].material_code in codes
    assert history_master_data["material_b"].material_code not in codes


def test_type_filter(app_client, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("type-filter"))
    app_client.force_login(user)
    response = app_client.get(LIST_URL, {"transaction_type": "RECEIPT"})
    types = _transaction_types(response.context["transactions"])
    assert types == {InventoryTransaction.TransactionType.RECEIPT}


def test_condition_filter(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("condition-filter"))
    app_client.force_login(user)
    _create_receipt(history_master_data, condition=history_master_data["condition_a"])
    _create_receipt(history_master_data, condition=history_master_data["condition_b"])
    response = app_client.get(
        LIST_URL,
        {"condition": str(history_master_data["condition_b"].pk)},
    )
    condition_names = {
        transaction.lines.all()[0].condition.name
        for transaction in response.context["transactions"]
    }
    assert condition_names == {history_master_data["condition_b"].name}


def test_exact_location_filter_receipt_target(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("loc-receipt"))
    app_client.force_login(user)
    receipt = _create_receipt(
        history_master_data,
        location=history_master_data["receipt_location"],
    )
    _create_issue(history_master_data, location=history_master_data["issue_location"])
    response = app_client.get(
        LIST_URL,
        {"location": str(history_master_data["receipt_location"].pk)},
    )
    transactions = response.context["transactions"]
    assert {transaction.pk for transaction in transactions} == {receipt.pk}
    assert _transaction_location_codes(transactions) == {
        history_master_data["receipt_location"].code
    }


def test_exact_location_filter_issue_source(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("loc-issue"))
    app_client.force_login(user)
    _create_receipt(
        history_master_data,
        location=history_master_data["receipt_location"],
    )
    issue = _create_issue(
        history_master_data,
        location=history_master_data["issue_location"],
    )
    response = app_client.get(
        LIST_URL,
        {"location": str(history_master_data["issue_location"].pk)},
    )
    transactions = response.context["transactions"]
    assert {transaction.pk for transaction in transactions} == {issue.pk}
    assert _transaction_location_codes(transactions) == {
        history_master_data["issue_location"].code
    }


def test_actor_filter(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("actor-filter"))
    app_client.force_login(user)
    _create_receipt(history_master_data)
    _create_issue(history_master_data)
    response = app_client.get(
        LIST_URL,
        {"actor": str(history_master_data["receipt_actor"].pk)},
    )
    actors = _transaction_actor_usernames(response.context["transactions"])
    assert actors == {history_master_data["receipt_actor"].username}


def test_date_filters(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("date-filter"))
    app_client.force_login(user)
    receipt_day = timezone.make_aware(datetime(2026, 3, 10, 12, 0))
    issue_day = timezone.make_aware(datetime(2026, 3, 11, 12, 0))
    receipt = _create_receipt(history_master_data, occurred_at=receipt_day)
    _create_issue(history_master_data, occurred_at=issue_day)
    response = app_client.get(
        LIST_URL,
        {
            "date_from": "2026-03-10",
            "date_to": "2026-03-10",
        },
    )
    transactions = response.context["transactions"]
    assert {transaction.pk for transaction in transactions} == {receipt.pk}
    assert _transaction_types(transactions) == {InventoryTransaction.TransactionType.RECEIPT}


def test_combined_filters(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("combined"))
    app_client.force_login(user)
    _create_receipt(history_master_data, material=history_master_data["material_a"])
    _create_receipt(history_master_data, material=history_master_data["material_b"])
    response = app_client.get(
        LIST_URL,
        {
            "transaction_type": "RECEIPT",
            "material": str(history_master_data["material_a"].pk),
        },
    )
    transactions = response.context["transactions"]
    assert _transaction_types(transactions) == {InventoryTransaction.TransactionType.RECEIPT}
    assert _transaction_material_codes(transactions) == {
        history_master_data["material_a"].material_code
    }


@pytest.mark.parametrize(
    "params",
    [
        {"material": "not-a-uuid"},
        {"condition": "bad"},
        {"location": "bad"},
        {"actor": "bad"},
        {"date_from": "2026-13-40"},
        {"date_to": "invalid"},
        {"transaction_type": "RETURN"},
        {"transaction_type": "TRANSFER"},
    ],
)
def test_invalid_params_safe(app_client, sample_transactions, params):
    user = _grant_view_inventorytransaction(_create_ordinary_user("invalid"))
    app_client.force_login(user)
    response = app_client.get(LIST_URL, params)
    assert response.status_code == 200


def test_pagination_page_size(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("pagination"))
    app_client.force_login(user)
    for _ in range(TRANSACTION_HISTORY_PAGE_SIZE + 1):
        _create_receipt(history_master_data)
    response = app_client.get(LIST_URL)
    assert response.status_code == 200
    assert len(response.context["transactions"]) == TRANSACTION_HISTORY_PAGE_SIZE
    page_two = app_client.get(LIST_URL, {"page": "2"})
    assert page_two.status_code == 200
    assert len(page_two.context["transactions"]) == 1


def test_query_preservation_in_pagination(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("preserve"))
    app_client.force_login(user)
    for _ in range(TRANSACTION_HISTORY_PAGE_SIZE + 1):
        _create_receipt(history_master_data)
    response = app_client.get(
        LIST_URL,
        {"q": history_master_data["material_a"].material_code, "transaction_type": "RECEIPT"},
    )
    content = response.content.decode()
    assert "page=2" in content
    assert "q=" in content
    assert "transaction_type=RECEIPT" in content


def test_ordering_newest_first(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("ordering"))
    app_client.force_login(user)
    older = _create_receipt(
        history_master_data,
        occurred_at=timezone.make_aware(datetime(2026, 2, 1, 9, 0)),
    )
    newer = _create_issue(
        history_master_data,
        occurred_at=timezone.make_aware(datetime(2026, 2, 2, 9, 0)),
    )
    transactions = list(app_client.get(LIST_URL).context["transactions"])
    assert transactions[0].pk == newer.pk
    assert transactions[1].pk == older.pk


def test_post_not_allowed(app_client, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("post-block"))
    app_client.force_login(user)
    assert app_client.post(LIST_URL).status_code == 405
    detail_url = reverse(
        "inventory:transaction-history-detail",
        args=[sample_transactions["receipt"].pk],
    )
    assert app_client.post(detail_url).status_code == 405


def test_get_creates_no_audit_event(app_client, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("no-audit"))
    app_client.force_login(user)
    before = AuditEvent.objects.count()
    assert app_client.get(LIST_URL).status_code == 200
    detail_url = reverse(
        "inventory:transaction-history-detail",
        args=[sample_transactions["issue"].pk],
    )
    assert app_client.get(detail_url).status_code == 200
    assert AuditEvent.objects.count() == before


def test_no_edit_delete_controls(app_client, sample_transactions):
    user = _grant_view_inventorytransaction(_create_ordinary_user("readonly"))
    app_client.force_login(user)
    list_content = _main_content(app_client.get(LIST_URL))
    detail_url = reverse(
        "inventory:transaction-history-detail",
        args=[sample_transactions["receipt"].pk],
    )
    detail_content = _main_content(app_client.get(detail_url))
    for content in (list_content, detail_content):
        assert "Düzenle" not in content
        assert "Sil" not in content
        assert 'method="post"' not in content.lower()
        assert "RETURN" not in content
        assert "TRANSFER" not in content


def test_list_query_count_bounded(app_client, history_master_data):
    user = _grant_view_inventorytransaction(_create_ordinary_user("queries"))
    app_client.force_login(user)
    for _ in range(5):
        _create_receipt(history_master_data)
        _create_issue(history_master_data)
    app_client.force_login(user)
    with CaptureQueriesContext(connection) as context:
        response = app_client.get(LIST_URL)
    assert response.status_code == 200
    assert len(context) <= 12


def test_nav_visible_only_with_permission(app_client):
    authorized = _grant_view_inventorytransaction(_create_ordinary_user("nav-yes"))
    app_client.force_login(authorized)
    content = app_client.get("/").content.decode()
    assert ">Envanter hareketleri</a>" in content
    assert 'href="/inventory/transactions/"' in content

    viewer = _grant_permissions(_create_ordinary_user("nav-no"), "catalog.view_material")
    app_client.force_login(viewer)
    content = app_client.get("/").content.decode()
    assert ">Envanter hareketleri</a>" not in content


def test_material_detail_shows_recent_transactions(app_client, history_master_data):
    user = _grant_permissions(
        _create_ordinary_user("material-history"),
        "catalog.view_material",
        "inventory.view_inventorytransaction",
    )
    app_client.force_login(user)
    for _ in range(11):
        _create_receipt(history_master_data)
    material = history_master_data["material_a"]
    response = app_client.get(reverse("catalog:material-detail", args=[material.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Son envanter hareketleri" in content
    assert len(response.context["recent_inventory_transactions"]) == 10


def test_material_detail_hides_history_without_permission(app_client, history_master_data):
    user = _grant_permissions(
        _create_ordinary_user("material-only"),
        "catalog.view_material",
    )
    app_client.force_login(user)
    _create_receipt(history_master_data)
    material = history_master_data["material_a"]
    response = app_client.get(reverse("catalog:material-detail", args=[material.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Son envanter hareketleri" not in content
    assert "recent_inventory_transactions" not in response.context


def test_managed_permission_count_is_twenty_one():
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 21
    assert "inventory.view_inventorytransaction" in SAFE_CATALOG_PERMISSION_LABELS


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER))
def test_fresh_bootstrap_roles_can_access_transaction_history(
    app_client, role_name, sample_transactions
):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(f"fresh-history-{role_name.lower()}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.view_inventorytransaction") is True
    app_client.force_login(user)
    assert app_client.get(LIST_URL).status_code == 200
    detail_url = reverse(
        "inventory:transaction-history-detail",
        args=[sample_transactions["receipt"].pk],
    )
    assert app_client.get(detail_url).status_code == 200


def test_existing_customized_group_does_not_gain_view_inventorytransaction_on_rerun(
    app_client,
):
    call_command("setup_roles", verbosity=0)
    technician = Group.objects.get(name=TECHNICIAN)
    permission = Permission.objects.get(
        content_type__app_label="inventory",
        codename="view_inventorytransaction",
    )
    technician.permissions.remove(permission)
    before = set(technician.permissions.values_list("pk", flat=True))
    call_command("setup_roles", verbosity=0)
    assert set(technician.permissions.values_list("pk", flat=True)) == before
    user = _create_ordinary_user("custom-history-tech")
    user.groups.add(technician)
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.view_inventorytransaction") is False
    app_client.force_login(user)
    assert app_client.get(LIST_URL).status_code == 403


def test_fresh_bootstrap_role_sees_transaction_history_nav(app_client):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user("fresh-nav-tech")
    user.groups.add(Group.objects.get(name=TECHNICIAN))
    user = _refresh_user_permissions(user)
    app_client.force_login(user)
    content = app_client.get("/").content.decode()
    assert ">Envanter hareketleri</a>" in content
    assert 'href="/inventory/transactions/"' in content
