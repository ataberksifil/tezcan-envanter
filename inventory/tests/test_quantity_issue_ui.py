from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.urls import NoReverseMatch, reverse

from accounts.models import Employee
from accounts.roles import (
    ADMIN_MANAGER,
    SAFE_CATALOG_PERMISSION_LABELS,
    STOREKEEPER,
    TECHNICIAN,
)
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.forms import QuantityIssueForm
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    StockBalance,
)
from inventory.services.issues import issue_quantity
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
            "issue UI tests deferred until test DB is migrated"
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


def _issue_stock_permission():
    return Permission.objects.get(
        content_type__app_label="inventory",
        codename="issue_stock",
    )


def _grant_issue_stock(user):
    user.user_permissions.add(_issue_stock_permission())
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
def issue_master_data():
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"IUI-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"IUI kategori {suffix}")
    material = Material.objects.create(
        material_code=f"IUI-M-{suffix}",
        name="UI çıkış malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    inactive_material = Material.objects.create(
        material_code=f"IUI-IN-{suffix}",
        name="Pasif malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=False,
    )
    serialized_material = Material.objects.create(
        material_code=f"IUI-SR-{suffix}",
        name="Seri malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"IUI-C-{suffix}",
        name="UI kondisyon",
        sort_order=200,
    )
    inactive_condition = MaterialCondition.objects.create(
        code=f"IUI-IC-{suffix}",
        name="Pasif kondisyon",
        sort_order=201,
        active=False,
    )
    location = Location.objects.create(
        code=f"IUI-L-{suffix}",
        name="UI raf",
        active=True,
        can_hold_stock=True,
    )
    inactive_location = Location.objects.create(
        code=f"IUI-IL-{suffix}",
        name="Pasif raf",
        active=False,
        can_hold_stock=True,
    )
    non_stock_location = Location.objects.create(
        code=f"IUI-NS-{suffix}",
        name="Stok tutmayan",
        active=True,
        can_hold_stock=False,
    )
    employee = Employee.objects.create(
        employee_number=f"IUI-E-{suffix}",
        first_name="Mehmet",
        last_name="Demir",
    )
    inactive_employee = Employee.objects.create(
        employee_number=f"IUI-IE-{suffix}",
        first_name="Pasif",
        last_name="Çalışan",
        active=False,
    )
    production_line = ProductionLine.objects.create(
        code=f"IUI-PL-{suffix}",
        name="Montaj Hattı",
    )
    parent_line = ProductionLine.objects.create(
        code=f"IUI-PPL-{suffix}",
        name="Ana Hat",
    )
    child_line = ProductionLine.objects.create(
        code=f"IUI-CPL-{suffix}",
        name="Alt Hat",
        parent=parent_line,
    )
    inactive_line = ProductionLine.objects.create(
        code=f"IUI-IPL-{suffix}",
        name="Pasif Hat",
        active=False,
    )
    StockBalance.objects.create(
        material=material,
        location=location,
        condition=condition,
        quantity=Decimal("10.000"),
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
        "employee": employee,
        "inactive_employee": inactive_employee,
        "production_line": production_line,
        "parent_line": parent_line,
        "child_line": child_line,
        "inactive_line": inactive_line,
    }


def _valid_post_data(issue_master_data, **overrides):
    values = {
        "material": str(issue_master_data["material"].pk),
        "source_location": str(issue_master_data["location"].pk),
        "condition": str(issue_master_data["condition"].pk),
        "quantity": "2.500",
        "receiver_employee": str(issue_master_data["employee"].pk),
        "production_line": str(issue_master_data["production_line"].pk),
        "usage_location_text": "Pano 12",
    }
    values.update(overrides)
    return values


def test_issue_named_routes():
    assert reverse("inventory:issue-create") == "/inventory/issues/new/"


def test_no_issue_edit_or_delete_routes():
    tx_id = uuid.uuid4()
    with pytest.raises(NoReverseMatch):
        reverse("inventory:issue-update", args=[tx_id])
    with pytest.raises(NoReverseMatch):
        reverse("inventory:issue-delete", args=[tx_id])


@pytest.mark.parametrize("path", ("/inventory/issues/new/",))
def test_anonymous_issue_create_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [path]


def test_unauthorized_user_gets_403(app_client):
    user = _create_ordinary_user("no-issue")
    app_client.force_login(user)
    assert app_client.get("/inventory/issues/new/").status_code == 403


def test_group_name_and_is_staff_alone_do_not_grant_issue_access(app_client):
    fake_group = Group.objects.create(name="TECHNICIAN")
    user = _create_ordinary_user("technician-group-only")
    user.groups.add(fake_group)
    user = _refresh_user_permissions(user)
    app_client.force_login(user)
    assert user.has_perm("inventory.issue_stock") is False
    assert app_client.get("/inventory/issues/new/").status_code == 403

    staff_user = _create_ordinary_user("staff-only")
    staff_user.is_staff = True
    staff_user.save(update_fields=["is_staff"])
    staff_user = _refresh_user_permissions(staff_user)
    app_client.force_login(staff_user)
    assert app_client.get("/inventory/issues/new/").status_code == 403


def test_explicit_issue_stock_permission_grants_access(app_client):
    user = _grant_issue_stock(_create_ordinary_user("issuer"))
    app_client.force_login(user)
    response = app_client.get("/inventory/issues/new/")
    assert response.status_code == 200
    assert "Stok çıkışı" in response.content.decode()


def test_managed_permission_count_is_twenty_one():
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 21
    assert "inventory.issue_stock" in SAFE_CATALOG_PERMISSION_LABELS


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER))
def test_fresh_bootstrap_roles_can_access_issue_form(app_client, role_name):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(f"fresh-{role_name.lower()}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.issue_stock") is True
    app_client.force_login(user)
    assert app_client.get("/inventory/issues/new/").status_code == 200


def test_existing_customized_group_does_not_gain_issue_stock_on_rerun(app_client):
    call_command("setup_roles", verbosity=0)
    technician = Group.objects.get(name=TECHNICIAN)
    technician.permissions.remove(_issue_stock_permission())
    before = set(technician.permissions.values_list("pk", flat=True))
    call_command("setup_roles", verbosity=0)
    assert set(technician.permissions.values_list("pk", flat=True)) == before
    user = _create_ordinary_user("custom-tech")
    user.groups.add(technician)
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.issue_stock") is False
    app_client.force_login(user)
    assert app_client.get("/inventory/issues/new/").status_code == 403


def test_issue_form_usable_without_view_productionline_permission(app_client, issue_master_data):
    user = _grant_issue_stock(_create_ordinary_user("no-pl-view"))
    app_client.force_login(user)
    response = app_client.get("/inventory/issues/new/")
    assert response.status_code == 200
    form = response.context["form"]
    line_ids = set(form.fields["production_line"].queryset.values_list("pk", flat=True))
    assert issue_master_data["production_line"].pk in line_ids
    assert issue_master_data["parent_line"].pk in line_ids
    assert issue_master_data["child_line"].pk in line_ids
    assert user.has_perm("inventory.view_productionline") is False


def test_form_generates_operation_id_on_get():
    form = QuantityIssueForm()
    assert form.fields["operation_id"].initial is not None
    uuid.UUID(str(form.fields["operation_id"].initial))


def test_form_preserves_operation_id_after_invalid_post(issue_master_data):
    operation_id = uuid.uuid4()
    form = QuantityIssueForm(
        {
            "operation_id": str(operation_id),
            **_valid_post_data(issue_master_data, quantity=""),
        }
    )
    assert form.is_valid() is False
    assert form["operation_id"].value() == str(operation_id)


def test_form_material_queryset_filters_active_quantity_with_unit(issue_master_data):
    form = QuantityIssueForm()
    material_ids = set(form.fields["material"].queryset.values_list("pk", flat=True))
    assert issue_master_data["material"].pk in material_ids
    assert issue_master_data["inactive_material"].pk not in material_ids
    assert issue_master_data["serialized_material"].pk not in material_ids


def test_form_location_condition_employee_and_production_line_querysets(issue_master_data):
    form = QuantityIssueForm()
    location_ids = set(
        form.fields["source_location"].queryset.values_list("pk", flat=True)
    )
    condition_ids = set(form.fields["condition"].queryset.values_list("pk", flat=True))
    employee_ids = set(
        form.fields["receiver_employee"].queryset.values_list("pk", flat=True)
    )
    line_ids = set(form.fields["production_line"].queryset.values_list("pk", flat=True))
    assert issue_master_data["location"].pk in location_ids
    assert issue_master_data["inactive_location"].pk not in location_ids
    assert issue_master_data["non_stock_location"].pk not in location_ids
    assert issue_master_data["condition"].pk in condition_ids
    assert issue_master_data["inactive_condition"].pk not in condition_ids
    assert issue_master_data["employee"].pk in employee_ids
    assert issue_master_data["inactive_employee"].pk not in employee_ids
    assert issue_master_data["production_line"].pk in line_ids
    assert issue_master_data["parent_line"].pk in line_ids
    assert issue_master_data["child_line"].pk in line_ids
    assert issue_master_data["inactive_line"].pk not in line_ids


def test_form_has_no_unit_actor_or_fingerprint_fields():
    form = QuantityIssueForm()
    assert "unit" not in form.fields
    assert "acting_user" not in form.fields
    assert "transaction_type" not in form.fields
    assert "request_fingerprint" not in form.fields
    assert "occurred_at" not in form.fields


def test_forged_stale_ids_fail_safely(issue_master_data):
    form = QuantityIssueForm(
        _valid_post_data(
            issue_master_data,
            material=str(issue_master_data["inactive_material"].pk),
        )
    )
    assert form.is_valid() is False


def test_valid_post_creates_issue_and_redirects(app_client, issue_master_data):
    user = _grant_issue_stock(_create_ordinary_user("poster"))
    app_client.force_login(user)
    initial_audit_count = AuditEvent.objects.count()
    initial_balance = StockBalance.objects.get().quantity

    get_response = app_client.get("/inventory/issues/new/")
    operation_id = get_response.context["form"]["operation_id"].value()
    post_data = _valid_post_data(issue_master_data, operation_id=operation_id)

    response = app_client.post("/inventory/issues/new/", post_data)
    assert response.status_code == 302

    issue = InventoryTransaction.objects.get()
    assert response.url == reverse("inventory:issue-detail", args=[issue.pk])
    assert issue.transaction_type == InventoryTransaction.TransactionType.ISSUE
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1
    assert IssueContext.objects.count() == 1
    assert StockBalance.objects.get().quantity == initial_balance - Decimal("2.500")
    assert AuditEvent.objects.count() == initial_audit_count

    detail = app_client.get(response.url)
    assert detail.status_code == 200
    content = detail.content.decode()
    assert issue_master_data["material"].material_code in content
    assert issue_master_data["employee"].first_name in content
    assert issue_master_data["production_line"].code in content
    assert "Pano 12" in content
    assert user.get_username() in content
    assert str(issue.pk) in content


def test_invalid_post_preserves_operation_id_without_ledger_mutation(
    app_client, issue_master_data
):
    user = _grant_issue_stock(_create_ordinary_user("invalid-post"))
    app_client.force_login(user)
    operation_id = str(uuid.uuid4())
    post_data = _valid_post_data(issue_master_data, operation_id=operation_id, quantity="")

    response = app_client.post("/inventory/issues/new/", post_data)
    assert response.status_code == 200
    assert response.context["form"]["operation_id"].value() == operation_id
    assert InventoryTransaction.objects.count() == 0
    assert IssueContext.objects.count() == 0


def test_double_submit_replays_same_issue(app_client, issue_master_data):
    user = _grant_issue_stock(_create_ordinary_user("double-submit"))
    app_client.force_login(user)
    operation_id = str(uuid.uuid4())
    post_data = _valid_post_data(issue_master_data, operation_id=operation_id)

    first = app_client.post("/inventory/issues/new/", post_data)
    assert first.status_code == 302
    issue_id = InventoryTransaction.objects.get().pk
    balance_after_first = StockBalance.objects.get().quantity

    second = app_client.post("/inventory/issues/new/", post_data)
    assert second.status_code == 302
    assert second.url == reverse("inventory:issue-detail", args=[issue_id])
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1
    assert StockBalance.objects.get().quantity == balance_after_first


def test_operation_conflict_shows_safe_error_without_second_stock_effect(
    app_client, issue_master_data
):
    user = _grant_issue_stock(_create_ordinary_user("conflict"))
    app_client.force_login(user)
    operation_id = str(uuid.uuid4())
    first_data = _valid_post_data(issue_master_data, operation_id=operation_id)
    assert app_client.post("/inventory/issues/new/", first_data).status_code == 302

    conflict_data = _valid_post_data(
        issue_master_data,
        operation_id=operation_id,
        quantity="9.000",
    )
    response = app_client.post("/inventory/issues/new/", conflict_data)
    assert response.status_code == 200
    assert InventoryTransaction.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("7.500")
    content = response.content.decode()
    assert "operation_id" in content.lower() or "zaten" in content.lower()


def test_insufficient_stock_shows_form_error_without_ledger(app_client, issue_master_data):
    user = _grant_issue_stock(_create_ordinary_user("no-stock"))
    app_client.force_login(user)
    post_data = _valid_post_data(
        issue_master_data,
        operation_id=str(uuid.uuid4()),
        quantity="99.000",
    )
    response = app_client.post("/inventory/issues/new/", post_data)
    assert response.status_code == 200
    assert InventoryTransaction.objects.count() == 0
    assert IssueContext.objects.count() == 0
    assert StockBalance.objects.get().quantity == Decimal("10.000")
    content = response.content.decode()
    assert "stok" in content.lower()


def test_direct_post_bypass_without_permission_rejected(app_client, issue_master_data):
    user = _create_ordinary_user("post-bypass")
    app_client.force_login(user)
    post_data = _valid_post_data(issue_master_data, operation_id=str(uuid.uuid4()))
    assert app_client.post("/inventory/issues/new/", post_data).status_code == 403
    assert InventoryTransaction.objects.count() == 0


def test_detail_shows_snapshots_after_master_rename_and_deactivate(
    app_client, issue_master_data
):
    user = _grant_issue_stock(_create_ordinary_user("snapshot"))
    app_client.force_login(user)
    post_data = _valid_post_data(issue_master_data, operation_id=str(uuid.uuid4()))
    redirect_response = app_client.post("/inventory/issues/new/", post_data)
    detail_url = redirect_response.url

    Employee.objects.filter(pk=issue_master_data["employee"].pk).update(
        first_name="Yeni",
        last_name="Ad",
        employee_number="NEW-999",
        active=False,
    )
    ProductionLine.objects.filter(pk=issue_master_data["production_line"].pk).update(
        code="NEW-CODE",
        name="Yeni Hat",
        active=False,
    )

    detail = app_client.get(detail_url)
    content = detail.content.decode()
    assert "Mehmet" in content
    assert "Demir" in content
    assert issue_master_data["employee"].employee_number in content
    assert issue_master_data["production_line"].code in content
    assert issue_master_data["production_line"].name in content
    assert "NEW-999" not in content
    assert "NEW-CODE" not in content
    assert "Yeni Hat" not in content


def test_issue_stock_nav_visible_only_for_authorized_user(app_client):
    authorized = _grant_issue_stock(_create_ordinary_user("nav-authorized"))
    app_client.force_login(authorized)
    authorized_content = app_client.get("/").content.decode()
    assert ">Stok Çıkışı</a>" in authorized_content
    assert 'href="/inventory/issues/new/"' in authorized_content

    viewer = _grant_permissions(_create_ordinary_user("nav-viewer"), "catalog.view_material")
    app_client.force_login(viewer)
    viewer_content = app_client.get("/").content.decode()
    assert ">Stok Çıkışı</a>" not in viewer_content


def test_receipt_nav_unaffected_by_issue_permission(app_client):
    receiver = _grant_permissions(
        _create_ordinary_user("receipt-only"),
        "inventory.receive_stock",
    )
    app_client.force_login(receiver)
    content = app_client.get("/").content.decode()
    assert ">Stok Girişi</a>" in content
    assert ">Stok Çıkışı</a>" not in content


def test_view_calls_service_not_direct_model_writes(app_client, issue_master_data):
    user = _grant_issue_stock(_create_ordinary_user("service-path"))
    app_client.force_login(user)
    post_data = _valid_post_data(issue_master_data, operation_id=str(uuid.uuid4()))
    with patch("inventory.views.issue_quantity", wraps=issue_quantity) as mocked:
        response = app_client.post("/inventory/issues/new/", post_data)
    assert response.status_code == 302
    mocked.assert_called_once()
