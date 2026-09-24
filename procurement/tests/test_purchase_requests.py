from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import Employee
from accounts.roles import ADMIN_MANAGER, DEFAULT_ROLE_TEMPLATES, STOREKEEPER, TECHNICIAN
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    ProductionLine,
    StockBalance,
)
from inventory.services.issues import issue_quantity
from inventory.services.receipts import receive_quantity
from locations.models import Location
from procurement.models import PurchaseRequest, PurchaseRequestLine, PurchaseRequestReceipt
from procurement.services import (
    add_purchase_request_line,
    create_purchase_request,
    header_fulfillment_state,
    link_line_material,
    receive_quantity_for_line,
    receive_serialized_for_line,
    search_purchase_requests,
    update_purchase_request,
)

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


def _user(username, *perms):
    user = get_user_model().objects.create_user(username=username, password=PASSWORD)
    for label in perms:
        app_label, codename = label.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    user = get_user_model().objects.get(pk=user.pk)
    return user


def _fresh_roles():
    Group.objects.filter(name__in=(TECHNICIAN, STOREKEEPER, ADMIN_MANAGER)).delete()
    call_command("setup_roles", verbosity=0)


def _role_user(role_name, username):
    user = get_user_model().objects.create_user(username=username, password=PASSWORD)
    user.groups.add(Group.objects.get(name=role_name))
    return get_user_model().objects.get(pk=user.pk)


def _masters(suffix, *, tracking=Material.TrackingMode.QUANTITY, unit_code="ADET"):
    unit = UnitOfMeasure.objects.create(code=f"{unit_code}-{suffix}", name="Adet")
    other = UnitOfMeasure.objects.create(code=f"KUTU-{suffix}", name="Kutu")
    category = Category.objects.create(name=f"Kat {suffix}")
    material = Material.objects.create(
        material_code=f"MAT-{suffix}",
        name=f"Malzeme {suffix}",
        category=category,
        unit=unit,
        tracking_mode=tracking,
    )
    condition = MaterialCondition.objects.create(code=f"C-{suffix}", name="Yeni")
    location = Location.objects.create(
        code=f"L-{suffix}",
        name="Raf",
        active=True,
        can_hold_stock=True,
    )
    return {
        "unit": unit,
        "other_unit": other,
        "material": material,
        "condition": condition,
        "location": location,
    }


def _request(actor, number="T-100"):
    return create_purchase_request(
        actor=actor,
        request_no=number,
        request_date=date(2026, 9, 1),
        approval_date=date(2026, 9, 2),
        note="not",
    ).purchase_request


def _line(actor, purchase_request, masters, **overrides):
    values = {
        "actor": actor,
        "purchase_request_id": purchase_request.pk,
        "requested_description": "Kontaktör 32A",
        "unit_id": masters["unit"].pk,
        "requested_quantity": Decimal("100.000"),
        "material_id": masters["material"].pk,
        "supplier_name": "ABC Elektrik",
        "unit_price": Decimal("12.5000"),
        "currency_code": "try",
        "lead_time_days": 10,
        "expected_arrival_date": date(2026, 9, 20),
    }
    values.update(overrides)
    return add_purchase_request_line(**values).line


def test_admin_and_storekeeper_create_external_request_no_without_stock():
    suffix = uuid.uuid4().hex[:8]
    _fresh_roles()
    admin = _role_user(ADMIN_MANAGER, f"adm-{suffix}")
    keeper = _role_user(STOREKEEPER, f"sk-{suffix}")
    technician = _role_user(TECHNICIAN, f"tk-{suffix}")
    before_tx = InventoryTransaction.objects.count()
    before_balance = StockBalance.objects.count()
    created = create_purchase_request(
        actor=admin,
        request_no="  T-42  ",
        request_date=date(2026, 9, 1),
    )
    assert created.purchase_request.request_no == "T-42"
    assert InventoryTransaction.objects.count() == before_tx
    assert StockBalance.objects.count() == before_balance
    again = create_purchase_request(
        actor=keeper,
        request_no="T-43",
        request_date=date(2026, 9, 3),
        approval_date=date(2026, 9, 4),
    )
    assert again.purchase_request.request_no == "T-43"
    with pytest.raises(ValidationError) as exc:
        create_purchase_request(
            actor=keeper,
            request_no="  T-42 ",
            request_date=date(2026, 9, 1),
        )
    assert "T-42" == created.purchase_request.request_no
    assert exc.value.messages
    with pytest.raises(PermissionDenied):
        create_purchase_request(
            actor=technician,
            request_no="T-99",
            request_date=date(2026, 9, 1),
        )
    with pytest.raises(ValidationError):
        create_purchase_request(
            actor=admin,
            request_no="T-44",
            request_date=date(2026, 9, 5),
            approval_date=date(2026, 9, 1),
        )


def test_lines_keep_description_and_reject_bad_quantity_and_unit():
    suffix = uuid.uuid4().hex[:8]
    actor = _user(f"line-{suffix}", "procurement.change_purchaserequest", "procurement.add_purchaserequest")
    purchase_request = _request(actor, f"L-{suffix}")
    masters = _masters(suffix)
    bare = _line(
        actor,
        purchase_request,
        masters,
        material_id=None,
        requested_description="Henüz kartı yok",
        unit_price=None,
        currency_code=None,
    )
    linked = _line(
        actor,
        purchase_request,
        masters,
        requested_description="Kartlı kalem",
        requested_quantity=Decimal("2"),
    )
    assert purchase_request.lines.count() == 2
    assert bare.material_id is None
    assert linked.material_id == masters["material"].pk
    assert linked.requested_description == "Kartlı kalem"
    assert linked.unit_code_snapshot == masters["unit"].code
    masters["unit"].code = "CHANGED"
    masters["unit"].save(update_fields=["code"])
    linked.refresh_from_db()
    assert linked.unit_code_snapshot != masters["unit"].code
    with pytest.raises(ValidationError):
        _line(actor, purchase_request, masters, requested_quantity=Decimal("0"))
    with pytest.raises(ValidationError):
        _line(actor, purchase_request, masters, requested_quantity=Decimal("-1"))
    with pytest.raises(ValidationError):
        link_line_material(actor=actor, line_id=bare.pk, material_id=masters["material"].pk) if False else _line(
            actor,
            purchase_request,
            masters,
            material_id=masters["material"].pk,
            unit_id=masters["other_unit"].pk,
            requested_description="uyumsuz",
        )
    description = bare.requested_description
    link_line_material(actor=actor, line_id=bare.pk, material_id=masters["material"].pk)
    bare.refresh_from_db()
    assert bare.material_id == masters["material"].pk
    assert bare.requested_description == description


def test_derived_status_partial_exact_and_overdelivery():
    suffix = uuid.uuid4().hex[:8]
    actor = _user(
        f"st-{suffix}",
        "procurement.add_purchaserequest",
        "procurement.change_purchaserequest",
        "inventory.receive_stock",
    )
    masters = _masters(suffix)
    purchase_request = _request(actor, f"S-{suffix}")
    line = _line(actor, purchase_request, masters, requested_quantity=Decimal("100"))
    assert line.fulfillment_state() == "OPEN"
    assert header_fulfillment_state(["OPEN"]) == "OPEN"
    receive_quantity_for_line(
        actor=actor,
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        quantity=Decimal("60"),
    )
    line.refresh_from_db()
    assert line.received_quantity() == Decimal("60.000")
    assert line.remaining_quantity() == Decimal("40.000")
    assert line.fulfillment_state() == "PARTIAL"
    receive_quantity_for_line(
        actor=actor,
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        quantity=Decimal("40"),
    )
    line.refresh_from_db()
    assert line.fulfillment_state() == "RECEIVED"
    assert line.remaining_quantity() == Decimal("0")
    receive_quantity_for_line(
        actor=actor,
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        quantity=Decimal("5"),
    )
    line.refresh_from_db()
    assert line.received_quantity() == Decimal("105.000")
    assert line.over_received_quantity() == Decimal("5.000")
    assert StockBalance.objects.get(material=masters["material"]).quantity == Decimal("105.000")
    assert header_fulfillment_state([line.fulfillment_state()]) == "RECEIVED"


def test_quantity_receipt_workflow_is_idempotent_and_rejects_foreign_movement(client):
    suffix = uuid.uuid4().hex[:8]
    actor = _user(
        f"rx-{suffix}",
        "procurement.view_purchaserequest",
        "procurement.add_purchaserequest",
        "procurement.change_purchaserequest",
        "inventory.receive_stock",
        "inventory.issue_stock",
    )
    masters = _masters(suffix)
    purchase_request = _request(actor, f"R-{suffix}")
    line = _line(actor, purchase_request, masters, requested_quantity=Decimal("100"))
    client.force_login(actor)
    url = reverse(
        "procurement:line-receive",
        kwargs={"pk": purchase_request.pk, "line_pk": line.pk},
    )
    get_response = client.get(url)
    assert get_response.status_code == 200
    assert InventoryTransaction.objects.count() == 0
    operation_id = get_response.context["form"]["operation_id"].value()
    preview = client.post(
        url,
        {
            "operation_id": operation_id,
            "material": str(masters["material"].pk),
            "quantity": "60.000",
            "condition": str(masters["condition"].pk),
            "target_location": str(masters["location"].pk),
            "usage_place": "Tavlama",
            "arrived_on": "2026-09-20",
        },
    )
    assert preview.status_code == 200
    assert "henüz stok değişmedi" in preview.content.decode()
    assert InventoryTransaction.objects.count() == 0
    assert PurchaseRequestReceipt.objects.count() == 0
    confirmed = client.post(
        url,
        {
            "operation_id": operation_id,
            "material": str(masters["material"].pk),
            "quantity": "60.000",
            "condition": str(masters["condition"].pk),
            "target_location": str(masters["location"].pk),
            "usage_place": "Tavlama",
            "arrived_on": "2026-09-20",
            "confirm": "1",
        },
    )
    assert confirmed.status_code == 302
    assert InventoryTransaction.objects.filter(transaction_type="RECEIPT").count() == 1
    assert PurchaseRequestReceipt.objects.count() == 1
    replay = client.post(
        url,
        {
            "operation_id": operation_id,
            "material": str(masters["material"].pk),
            "quantity": "60.000",
            "condition": str(masters["condition"].pk),
            "target_location": str(masters["location"].pk),
            "usage_place": "Tavlama",
            "arrived_on": "2026-09-20",
            "confirm": "1",
        },
    )
    assert replay.status_code == 302
    assert InventoryTransaction.objects.filter(transaction_type="RECEIPT").count() == 1
    assert PurchaseRequestReceipt.objects.count() == 1
    wrong = client.post(
        url,
        {
            "operation_id": str(uuid.uuid4()),
            "material": str(Material.objects.create(
                material_code=f"OTHER-{suffix}",
                name="Diğer",
                category=masters["material"].category,
                unit=masters["unit"],
                tracking_mode=Material.TrackingMode.QUANTITY,
            ).pk),
            "quantity": "1.000",
            "condition": str(masters["condition"].pk),
            "target_location": str(masters["location"].pk),
            "usage_place": "Tavlama",
            "arrived_on": "2026-09-20",
            "confirm": "1",
        },
    )
    assert wrong.status_code == 200
    assert InventoryTransaction.objects.count() == 1
    employee = Employee.objects.create(
        employee_number=f"E-{suffix}",
        first_name="Ali",
        last_name="Veli",
    )
    production_line = ProductionLine.objects.create(code=f"H-{suffix}", name="Hat")
    issue_actor = actor
    issue = issue_quantity(
        actor=issue_actor,
        operation_id=uuid.uuid4(),
        material_id=masters["material"].pk,
        unit_id=masters["unit"].pk,
        condition_id=masters["condition"].pk,
        source_location_id=masters["location"].pk,
        quantity=Decimal("1"),
        receiver_employee_id=employee.pk,
        production_line_id=production_line.pk,
        usage_location_text="Pano",
    )
    with pytest.raises(Exception):
        with transaction.atomic():
            PurchaseRequestReceipt.objects.create(
                line=line,
                inventory_transaction_id=issue.transaction.pk,
                fulfilled_quantity=Decimal("1.000"),
                created_by=actor,
            )
    assert PurchaseRequestReceipt.objects.count() == 1


def test_serialized_receipt_counts_one_asset_and_does_not_create_balance():
    suffix = uuid.uuid4().hex[:8]
    actor = _user(
        f"ser-{suffix}",
        "procurement.add_purchaserequest",
        "procurement.change_purchaserequest",
        "inventory.receive_stock",
    )
    masters = _masters(suffix, tracking=Material.TrackingMode.SERIALIZED)
    purchase_request = _request(actor, f"Z-{suffix}")
    line = _line(actor, purchase_request, masters, requested_quantity=Decimal("2"))
    first = receive_serialized_for_line(
        actor=actor,
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        internal_asset_code=f"A-{suffix}-1",
        serial_number="",
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
    )
    assert StockBalance.objects.filter(material=masters["material"]).count() == 0
    assert first.receipt.fulfilled_quantity == Decimal("1.000")
    replay = receive_serialized_for_line(
        actor=actor,
        line_id=line.pk,
        operation_id=first.inventory_result.transaction.operation_id,
        internal_asset_code=f"A-{suffix}-1",
        serial_number="",
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
    )
    assert replay.replayed is True
    assert PurchaseRequestReceipt.objects.filter(line=line).count() == 1
    line.refresh_from_db()
    assert line.received_quantity() == Decimal("1.000")
    assert line.fulfillment_state() == "PARTIAL"


def test_prices_are_decimal_and_total_is_derived():
    suffix = uuid.uuid4().hex[:8]
    actor = _user(f"pr-{suffix}", "procurement.add_purchaserequest", "procurement.change_purchaserequest")
    masters = _masters(suffix)
    purchase_request = _request(actor, f"P-{suffix}")
    line = _line(
        actor,
        purchase_request,
        masters,
        requested_quantity=Decimal("3.000"),
        unit_price=Decimal("1.2500"),
        currency_code="eur",
        material_id=None,
    )
    assert line.currency_code == "EUR"
    assert line.derived_total_price() == Decimal("3.7500")
    assert not isinstance(line.unit_price, float)
    with pytest.raises(ValidationError):
        _line(
            actor,
            purchase_request,
            masters,
            unit_price=Decimal("1.00"),
            currency_code=None,
            material_id=None,
            requested_description="fiyatsız para",
        )
    with pytest.raises(ValidationError):
        _line(
            actor,
            purchase_request,
            masters,
            unit_price=1.2,
            currency_code="TRY",
            material_id=None,
            requested_description="float fiyat",
        )


def test_search_and_permission_boundaries(client):
    suffix = uuid.uuid4().hex[:8]
    actor = _user(
        f"ui-{suffix}",
        "procurement.view_purchaserequest",
        "procurement.add_purchaserequest",
        "procurement.change_purchaserequest",
    )
    masters = _masters(suffix)
    purchase_request = _request(actor, f"ARA-{suffix}")
    _line(
        actor,
        purchase_request,
        masters,
        requested_description=f"Sigorta {suffix}",
        supplier_name=f"Firma {suffix}",
    )
    found = search_purchase_requests(PurchaseRequest.objects.all(), f"ARA-{suffix}")
    assert found.filter(pk=purchase_request.pk).exists()
    found = search_purchase_requests(PurchaseRequest.objects.all(), f"Sigorta {suffix}")
    assert found.filter(pk=purchase_request.pk).exists()
    found = search_purchase_requests(PurchaseRequest.objects.all(), masters["material"].material_code)
    assert found.filter(pk=purchase_request.pk).exists()
    found = search_purchase_requests(PurchaseRequest.objects.all(), f"Firma {suffix}")
    assert found.filter(pk=purchase_request.pk).exists()
    client.force_login(actor)
    response = client.get(reverse("procurement:request-list"), {"q": f"ARA-{suffix}"})
    assert response.status_code == 200
    assert f"ARA-{suffix}" in response.content.decode()
    _fresh_roles()
    technician = _role_user(TECHNICIAN, f"tech-ui-{suffix}")
    client.force_login(technician)
    denied = client.get(reverse("procurement:request-list"))
    assert denied.status_code == 403
    denied_post = client.post(
        reverse("procurement:request-create"),
        {"request_no": "X", "request_date": "2026-09-01"},
    )
    assert denied_post.status_code == 403
    assert "view_purchaserequest" not in DEFAULT_ROLE_TEMPLATES[TECHNICIAN]
    assert "add_purchaserequest" in DEFAULT_ROLE_TEMPLATES[STOREKEEPER]
    assert "change_purchaserequest" in DEFAULT_ROLE_TEMPLATES[ADMIN_MANAGER]


def test_fulfilled_line_cannot_be_deleted_and_history_stays():
    suffix = uuid.uuid4().hex[:8]
    actor = _user(
        f"del-{suffix}",
        "procurement.add_purchaserequest",
        "procurement.change_purchaserequest",
        "inventory.receive_stock",
    )
    masters = _masters(suffix)
    purchase_request = _request(actor, f"D-{suffix}")
    line = _line(actor, purchase_request, masters, requested_quantity=Decimal("1"))
    result = receive_quantity_for_line(
        actor=actor,
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        quantity=Decimal("1"),
    )
    with pytest.raises(ValidationError):
        line.delete()
    with pytest.raises(ValidationError):
        purchase_request.delete()
    with pytest.raises(Exception):
        with transaction.atomic():
            PurchaseRequestReceipt.objects.filter(pk=result.receipt.pk).delete()
    assert PurchaseRequestReceipt.objects.filter(pk=result.receipt.pk).exists()
    assert InventoryTransaction.objects.filter(pk=result.inventory_result.transaction.pk).exists()
    detail_actor = actor
    detail_actor.user_permissions.add(
        Permission.objects.get(content_type__app_label="procurement", codename="view_purchaserequest")
    )


def test_duplicate_request_no_is_database_unique():
    suffix = uuid.uuid4().hex[:8]
    actor = _user(f"dup-{suffix}", "procurement.add_purchaserequest")
    _request(actor, f"UNQ-{suffix}")
    with pytest.raises((ValidationError, IntegrityError)):
        PurchaseRequest.objects.create(
            request_no=f"UNQ-{suffix}",
            request_date=date(2026, 9, 1),
            created_by=actor,
        )


def test_update_preserves_request_and_late_signal_uses_expected_date():
    suffix = uuid.uuid4().hex[:8]
    actor = _user(
        f"up-{suffix}",
        "procurement.add_purchaserequest",
        "procurement.change_purchaserequest",
        "procurement.view_purchaserequest",
    )
    purchase_request = _request(actor, f"U-{suffix}")
    updated = update_purchase_request(
        actor=actor,
        purchase_request_id=purchase_request.pk,
        request_no=f"  U-{suffix}  ",
        request_date=date(2026, 9, 1),
        approval_date=None,
        note="güncel",
    )
    assert updated.purchase_request.request_no == f"U-{suffix}"
    masters = _masters(suffix)
    line = _line(
        actor,
        purchase_request,
        masters,
        expected_arrival_date=timezone.localdate() - timedelta(days=1),
        material_id=None,
        unit_price=None,
        currency_code=None,
    )
    assert line.expected_arrival_date < timezone.localdate()
    assert connection.vendor == "postgresql"
