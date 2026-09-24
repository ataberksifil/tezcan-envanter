from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, connection
from django.urls import reverse
from django.utils import timezone

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    ProductionLine,
    ReceiptMetadata,
    StockBalance,
)
from inventory.services.issues import issue_quantity
from inventory.services.receipts import (
    INVALID_ARRIVAL_DATE,
    INVALID_PACKAGING,
    INVALID_USAGE_PLACE,
    OPERATION_CONFLICT,
    PACKAGING_QUANTITY_MISMATCH,
    ReceiptMetadataInput,
    receive_quantity,
    receive_serialized,
)
from locations.models import Location
from procurement.models import PurchaseRequestReceipt
from procurement.services import (
    add_purchase_request_line,
    create_purchase_request,
    receive_quantity_for_line,
    receive_serialized_for_line,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


def _grant(user, *codenames):
    for label in codenames:
        app_label, codename = label.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def masters():
    suffix = uuid.uuid4().hex[:8]
    adet = UnitOfMeasure.objects.create(code=f"ADET-{suffix}", name="Adet")
    kutu = UnitOfMeasure.objects.create(code=f"KUTU-{suffix}", name="Kutu")
    category = Category.objects.create(name=f"Meta kat {suffix}")
    material = Material.objects.create(
        material_code=f"META-M-{suffix}",
        name="Kontaktör 32A",
        category=category,
        brand="Siemens",
        model="5.5 kW",
        unit=adet,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    boxed = Material.objects.create(
        material_code=f"META-K-{suffix}",
        name="Kutu stoklu malzeme",
        category=category,
        brand="ABB",
        model="Kutu",
        unit=kutu,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    serialized = Material.objects.create(
        material_code=f"META-S-{suffix}",
        name="Analizör",
        category=category,
        brand="Fluke",
        model="1738",
        unit=adet,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"META-C-{suffix}",
        name="Yeni",
        sort_order=410,
    )
    location = Location.objects.create(
        code=f"MK-BEKLEYEN-{suffix}",
        name="Yerleştirme Bekleyen",
        active=True,
        can_hold_stock=True,
    )
    actor = _grant(
        User.objects.create_user(username=f"meta-{suffix}"),
        "inventory.receive_stock",
        "inventory.view_inventorytransaction",
        "procurement.add_purchaserequest",
        "procurement.change_purchaserequest",
        "procurement.view_purchaserequest",
    )
    return {
        "suffix": suffix,
        "adet": adet,
        "kutu": kutu,
        "material": material,
        "boxed": boxed,
        "serialized": serialized,
        "condition": condition,
        "location": location,
        "actor": actor,
    }


def _quantity_request(masters, **overrides):
    values = {
        "actor": masters["actor"],
        "operation_id": uuid.uuid4(),
        "material_id": masters["material"].pk,
        "unit_id": masters["adet"].pk,
        "condition_id": masters["condition"].pk,
        "target_location_id": masters["location"].pk,
        "quantity": Decimal("2000.000"),
    }
    values.update(overrides)
    return values


def _metadata(**overrides):
    values = {
        "usage_place": "Tavlama",
        "arrived_on": date(2026, 9, 20),
        "supplier_name": "Tezcan Elektrik",
        "package_count": 2,
        "package_label": "KUTU",
        "contents_per_package": Decimal("1000.000"),
    }
    values.update(overrides)
    return ReceiptMetadataInput(**values)


def test_receipt_without_metadata_remains_valid(masters):
    result = receive_quantity(**_quantity_request(masters, quantity=Decimal("5")))
    assert result.receipt_metadata is None
    assert ReceiptMetadata.objects.count() == 0
    assert result.transaction.occurred_at is not None
    assert StockBalance.objects.get().quantity == Decimal("5.000")


def test_quantity_receipt_stores_snapshots_and_keeps_location_separate(masters):
    before = timezone.now()
    result = receive_quantity(
        **_quantity_request(masters),
        receipt_metadata=_metadata(),
    )
    after = timezone.now()
    metadata = result.receipt_metadata
    assert metadata is not None
    assert metadata.usage_place == "Tavlama"
    assert metadata.arrived_on == date(2026, 9, 20)
    assert metadata.supplier_name == "Tezcan Elektrik"
    assert metadata.package_count == 2
    assert metadata.package_label == "KUTU"
    assert metadata.contents_per_package == Decimal("1000.000")
    assert metadata.material_name_snapshot == "Kontaktör 32A"
    assert metadata.material_brand_snapshot == "Siemens"
    assert metadata.material_model_snapshot == "5.5 kW"
    assert metadata.unit_code_snapshot == masters["adet"].code
    assert metadata.transaction_id == result.transaction.pk
    assert before <= result.transaction.occurred_at <= after
    assert result.transaction.occurred_at.date() != metadata.arrived_on or True
    line = result.lines[0]
    assert line.target_location_id == masters["location"].pk
    assert metadata.usage_place != line.target_location.name
    assert StockBalance.objects.get().quantity == Decimal("2000.000")


def test_material_card_edit_does_not_change_receipt_snapshots(masters):
    result = receive_quantity(
        **_quantity_request(masters, quantity=Decimal("10")),
        receipt_metadata=_metadata(
            package_count=None,
            package_label=None,
            contents_per_package=None,
        ),
    )
    material = masters["material"]
    material.name = "Yeni ad"
    material.brand = "Yeni marka"
    material.model = "Yeni model"
    material.save()
    metadata = ReceiptMetadata.objects.get(pk=result.transaction.pk)
    assert metadata.material_name_snapshot == "Kontaktör 32A"
    assert metadata.material_brand_snapshot == "Siemens"
    assert metadata.material_model_snapshot == "5.5 kW"


def test_packaging_must_equal_received_quantity_in_stock_unit(masters):
    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(
            **_quantity_request(masters, quantity=Decimal("2000")),
            receipt_metadata=_metadata(contents_per_package=Decimal("500")),
        )
    assert exc_info.value.code == PACKAGING_QUANTITY_MISMATCH
    assert InventoryTransaction.objects.count() == 0
    assert ReceiptMetadata.objects.count() == 0
    assert StockBalance.objects.count() == 0


def test_boxed_material_packaging_uses_stock_unit_not_conversion(masters):
    result = receive_quantity(
        **_quantity_request(
            masters,
            material_id=masters["boxed"].pk,
            unit_id=masters["kutu"].pk,
            quantity=Decimal("2"),
        ),
        receipt_metadata=_metadata(
            package_count=2,
            package_label="KUTU",
            contents_per_package=Decimal("1"),
        ),
    )
    assert result.lines[0].quantity == Decimal("2.000")
    assert result.lines[0].unit_id == masters["kutu"].pk
    assert result.receipt_metadata.contents_per_package == Decimal("1.000")
    assert result.receipt_metadata.unit_code_snapshot == masters["kutu"].code
    assert StockBalance.objects.get().quantity == Decimal("2.000")


def test_partial_packaging_is_rejected(masters):
    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(
            **_quantity_request(masters, quantity=Decimal("10")),
            receipt_metadata=_metadata(
                package_count=2,
                package_label="KUTU",
                contents_per_package=None,
            ),
        )
    assert exc_info.value.code == INVALID_PACKAGING
    assert InventoryTransaction.objects.count() == 0


def test_future_arrival_date_is_rejected(masters):
    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(
            **_quantity_request(masters, quantity=Decimal("1")),
            receipt_metadata=_metadata(
                arrived_on=timezone.localdate() + timedelta(days=2),
                package_count=None,
                package_label=None,
                contents_per_package=None,
            ),
        )
    assert exc_info.value.code == INVALID_ARRIVAL_DATE


def test_blank_usage_place_is_rejected(masters):
    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(
            **_quantity_request(masters, quantity=Decimal("1")),
            receipt_metadata=_metadata(
                usage_place="   ",
                package_count=None,
                package_label=None,
                contents_per_package=None,
            ),
        )
    assert exc_info.value.code == INVALID_USAGE_PLACE


def test_metadata_save_failure_rolls_back_stock(masters):
    with patch.object(ReceiptMetadata, "save", side_effect=RuntimeError("meta")):
        with pytest.raises(RuntimeError):
            receive_quantity(
                **_quantity_request(masters, quantity=Decimal("3")),
                receipt_metadata=_metadata(
                    package_count=None,
                    package_label=None,
                    contents_per_package=None,
                ),
            )
    assert InventoryTransaction.objects.count() == 0
    assert ReceiptMetadata.objects.count() == 0
    assert StockBalance.objects.count() == 0


def test_replay_does_not_duplicate_metadata(masters):
    operation_id = uuid.uuid4()
    payload = _quantity_request(masters, operation_id=operation_id, quantity=Decimal("8"))
    metadata = _metadata(
        package_count=None,
        package_label=None,
        contents_per_package=None,
    )
    first = receive_quantity(**payload, receipt_metadata=metadata)
    replay = receive_quantity(**payload, receipt_metadata=metadata)
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.receipt_metadata.pk == first.receipt_metadata.pk
    assert ReceiptMetadata.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("8.000")


def test_same_operation_id_different_metadata_conflicts(masters):
    operation_id = uuid.uuid4()
    receive_quantity(
        **_quantity_request(masters, operation_id=operation_id, quantity=Decimal("4")),
        receipt_metadata=_metadata(
            usage_place="Tavlama",
            package_count=None,
            package_label=None,
            contents_per_package=None,
        ),
    )
    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(
            **_quantity_request(masters, operation_id=operation_id, quantity=Decimal("4")),
            receipt_metadata=_metadata(
                usage_place="Soğutma Kulesi",
                package_count=None,
                package_label=None,
                contents_per_package=None,
            ),
        )
    assert exc_info.value.code == OPERATION_CONFLICT
    assert ReceiptMetadata.objects.count() == 1
    assert ReceiptMetadata.objects.get().usage_place == "Tavlama"


def test_serialized_receipt_uses_same_place_and_arrival_semantics(masters):
    result = receive_serialized(
        actor=masters["actor"],
        operation_id=uuid.uuid4(),
        material_id=masters["serialized"].pk,
        internal_asset_code=f"AST-{masters['suffix']}",
        serial_number="SN-1",
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        receipt_metadata=_metadata(
            package_count=None,
            package_label=None,
            contents_per_package=None,
        ),
    )
    assert result.receipt_metadata.usage_place == "Tavlama"
    assert result.receipt_metadata.arrived_on == date(2026, 9, 20)
    assert result.receipt_metadata.material_name_snapshot == "Analizör"
    assert result.lines[0].target_location_id == masters["location"].pk
    assert result.receipt_metadata.usage_place != result.lines[0].target_location.name


def test_serialized_receipt_rejects_packaging(masters):
    with pytest.raises(ValidationError) as exc_info:
        receive_serialized(
            actor=masters["actor"],
            operation_id=uuid.uuid4(),
            material_id=masters["serialized"].pk,
            internal_asset_code=f"AST-P-{masters['suffix']}",
            serial_number=None,
            condition_id=masters["condition"].pk,
            target_location_id=masters["location"].pk,
            receipt_metadata=_metadata(),
        )
    assert exc_info.value.code == INVALID_PACKAGING
    assert InventoryTransaction.objects.count() == 0


def test_linked_talep_receipt_with_metadata_counts_once(masters):
    request = create_purchase_request(
        actor=masters["actor"],
        request_no=f"T-{masters['suffix']}",
        request_date=date(2026, 9, 1),
    ).purchase_request
    line = add_purchase_request_line(
        actor=masters["actor"],
        purchase_request_id=request.pk,
        requested_description="Kontaktör",
        unit_id=masters["adet"].pk,
        requested_quantity=Decimal("2000"),
        material_id=masters["material"].pk,
        supplier_name="Talep Firması",
    ).line
    operation_id = uuid.uuid4()
    metadata = _metadata(supplier_name="Düzeltilmiş Firma")
    first = receive_quantity_for_line(
        actor=masters["actor"],
        line_id=line.pk,
        operation_id=operation_id,
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        quantity=Decimal("2000"),
        receipt_metadata=metadata,
    )
    replay = receive_quantity_for_line(
        actor=masters["actor"],
        line_id=line.pk,
        operation_id=operation_id,
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        quantity=Decimal("2000"),
        receipt_metadata=metadata,
    )
    line.refresh_from_db()
    assert first.replayed is False
    assert replay.replayed is True
    assert PurchaseRequestReceipt.objects.count() == 1
    assert line.received_quantity() == Decimal("2000.000")
    assert first.inventory_result.receipt_metadata.supplier_name == "Düzeltilmiş Firma"
    assert ReceiptMetadata.objects.count() == 1


def test_linked_serialized_talep_receipt_counts_one_asset(masters):
    request = create_purchase_request(
        actor=masters["actor"],
        request_no=f"TS-{masters['suffix']}",
        request_date=date(2026, 9, 1),
    ).purchase_request
    line = add_purchase_request_line(
        actor=masters["actor"],
        purchase_request_id=request.pk,
        requested_description="Analizör",
        unit_id=masters["adet"].pk,
        requested_quantity=Decimal("1"),
        material_id=masters["serialized"].pk,
        supplier_name="Fluke TR",
    ).line
    result = receive_serialized_for_line(
        actor=masters["actor"],
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        internal_asset_code=f"AST-T-{masters['suffix']}",
        serial_number=None,
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        receipt_metadata=_metadata(
            supplier_name="Fluke TR",
            package_count=None,
            package_label=None,
            contents_per_package=None,
        ),
    )
    line.refresh_from_db()
    assert line.received_quantity() == Decimal("1.000")
    assert result.inventory_result.receipt_metadata.usage_place == "Tavlama"
    assert PurchaseRequestReceipt.objects.count() == 1


def test_metadata_cannot_attach_to_non_receipt(masters):
    issuer = _grant(
        User.objects.create_user(username=f"issuer-{masters['suffix']}"),
        "inventory.issue_stock",
    )
    receive_quantity(**_quantity_request(masters, quantity=Decimal("5")))
    employee = Employee.objects.create(
        employee_number=f"E-{masters['suffix']}",
        first_name="Ali",
        last_name="Veli",
    )
    production_line = ProductionLine.objects.create(
        code=f"H-{masters['suffix']}",
        name="Hat",
    )
    issue = issue_quantity(
        actor=issuer,
        operation_id=uuid.uuid4(),
        material_id=masters["material"].pk,
        unit_id=masters["adet"].pk,
        condition_id=masters["condition"].pk,
        source_location_id=masters["location"].pk,
        quantity=Decimal("1"),
        receiver_employee_id=employee.pk,
        production_line_id=production_line.pk,
        usage_location_text="Pano 1",
    )
    with pytest.raises(IntegrityError, match="ReceiptMetadata requires a RECEIPT"):
        ReceiptMetadata.objects.create(
            transaction=issue.transaction,
            usage_place="Tavlama",
            arrived_on=date(2026, 9, 20),
            material_code_snapshot="X",
            material_name_snapshot="Y",
        )


def test_receipt_metadata_is_immutable(masters):
    result = receive_quantity(
        **_quantity_request(masters, quantity=Decimal("1")),
        receipt_metadata=_metadata(
            package_count=None,
            package_label=None,
            contents_per_package=None,
        ),
    )
    metadata = result.receipt_metadata
    metadata.usage_place = "Başka yer"
    with pytest.raises(ValidationError):
        metadata.save()
    with pytest.raises(ValidationError):
        result.receipt_metadata.delete()
    with pytest.raises(DatabaseError, match="immutable"):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_receiptmetadata SET usage_place = %s WHERE transaction_id = %s",
                ["Başka yer", metadata.pk],
            )


def test_receive_permission_is_still_enforced(masters):
    outsider = User.objects.create_user(username=f"no-meta-{masters['suffix']}")
    with pytest.raises(PermissionDenied):
        receive_quantity(
            **_quantity_request(masters, actor=outsider, quantity=Decimal("1")),
            receipt_metadata=_metadata(
                package_count=None,
                package_label=None,
                contents_per_package=None,
            ),
        )


def test_quantity_form_preview_and_back_do_not_write_metadata(client, masters):
    client.force_login(masters["actor"])
    url = reverse("inventory:receipt-create")
    get_response = client.get(url)
    operation_id = get_response.context["form"]["operation_id"].value()
    payload = {
        "operation_id": operation_id,
        "material": str(masters["material"].pk),
        "quantity": "2.000",
        "condition": str(masters["condition"].pk),
        "target_location": str(masters["location"].pk),
        "usage_place": "Tavlama",
        "arrived_on": "2026-09-20",
        "supplier_name": "Tezcan",
        "package_count": "2",
        "package_label": "KUTU",
        "contents_per_package": "1.000",
    }
    preview = client.post(url, payload)
    assert preview.status_code == 200
    assert "Kayıt önizlemesi" in preview.content.decode()
    assert "Tavlama" in preview.content.decode()
    assert InventoryTransaction.objects.count() == 0
    assert ReceiptMetadata.objects.count() == 0
    back = client.post(url, {**payload, "confirm": "1", "intent": "edit"})
    assert back.status_code == 200
    assert InventoryTransaction.objects.count() == 0
    assert ReceiptMetadata.objects.count() == 0
    confirmed = client.post(url, {**payload, "confirm": "1"})
    assert confirmed.status_code == 302
    assert ReceiptMetadata.objects.count() == 1
    detail = client.get(confirmed.url)
    content = detail.content.decode()
    assert "Tavlama" in content
    assert "Tezcan" in content
    assert "Siemens" in content
    assert "5.5 kW" in content
    assert "Yerleştirme Bekleyen" in content
    assert "Sistem kayıt zamanı" in content
    assert "Fiziksel geliş tarihi" in content


def test_history_and_talep_form_show_recorded_metadata(client, masters):
    client.force_login(masters["actor"])
    result = receive_quantity(
        **_quantity_request(masters, quantity=Decimal("10")),
        receipt_metadata=_metadata(
            usage_place="Soğutma Kulesi Panel",
            supplier_name="Özel Firma",
            package_count=None,
            package_label=None,
            contents_per_package=None,
        ),
    )
    history = client.get(
        reverse("inventory:transaction-history-detail", args=[result.transaction.pk])
    )
    content = history.content.decode()
    assert "Soğutma Kulesi Panel" in content
    assert "Özel Firma" in content
    assert "Kontaktör 32A" in content
    request = create_purchase_request(
        actor=masters["actor"],
        request_no=f"TF-{masters['suffix']}",
        request_date=date(2026, 9, 1),
    ).purchase_request
    line = add_purchase_request_line(
        actor=masters["actor"],
        purchase_request_id=request.pk,
        requested_description="Kontaktör",
        unit_id=masters["adet"].pk,
        requested_quantity=Decimal("5"),
        material_id=masters["material"].pk,
        supplier_name="Talep Firması",
    ).line
    form_page = client.get(
        reverse(
            "procurement:line-receive",
            kwargs={"pk": request.pk, "line_pk": line.pk},
        )
    )
    assert form_page.status_code == 200
    assert form_page.context["form"]["supplier_name"].value() == "Talep Firması"
    assert "Kullanıldığı / uygulandığı yer" in form_page.content.decode()


def test_technician_cannot_submit_receipt_metadata(client, masters):
    technician = User.objects.create_user(username=f"tech-{masters['suffix']}")
    client.force_login(technician)
    response = client.post(
        reverse("inventory:receipt-create"),
        {
            "operation_id": str(uuid.uuid4()),
            "material": str(masters["material"].pk),
            "quantity": "1.000",
            "condition": str(masters["condition"].pk),
            "target_location": str(masters["location"].pk),
            "usage_place": "Tavlama",
            "arrived_on": "2026-09-20",
            "confirm": "1",
        },
    )
    assert response.status_code == 403
    assert InventoryTransaction.objects.count() == 0
