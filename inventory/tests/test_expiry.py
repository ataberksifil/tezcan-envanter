from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    ExpiryInspection,
    ReceiptExpiry,
    SKT_APPROACHING_WINDOW_DAYS,
    StockBalance,
)
from inventory.services.expiry import record_physical_inspection
from inventory.services.receipts import (
    INVALID_EXPIRY_DATE,
    OPERATION_CONFLICT,
    receive_quantity,
    receive_serialized,
)
from locations.models import Location

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
    unit = UnitOfMeasure.objects.create(code=f"ADET-{suffix}", name="Adet")
    category = Category.objects.create(name=f"SKT kat {suffix}")
    material = Material.objects.create(
        material_code=f"SKT-M-{suffix}",
        name="Kontaktör",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    serialized = Material.objects.create(
        material_code=f"SKT-S-{suffix}",
        name="Analizör",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"SKT-C-{suffix}",
        name="Yeni",
        sort_order=510,
    )
    location = Location.objects.create(
        code=f"SKT-LOC-{suffix}",
        name="Mal kabul",
        active=True,
        can_hold_stock=True,
    )
    actor = _grant(
        User.objects.create_user(username=f"skt-{suffix}"),
        "inventory.receive_stock",
        "inventory.view_inventorytransaction",
    )
    technician = _grant(
        User.objects.create_user(username=f"skt-tech-{suffix}"),
        "inventory.view_inventorytransaction",
    )
    return {
        "suffix": suffix,
        "unit": unit,
        "material": material,
        "serialized": serialized,
        "condition": condition,
        "location": location,
        "actor": actor,
        "technician": technician,
    }


def _receive(masters, **overrides):
    values = {
        "actor": masters["actor"],
        "operation_id": uuid.uuid4(),
        "material_id": masters["material"].pk,
        "unit_id": masters["unit"].pk,
        "condition_id": masters["condition"].pk,
        "target_location_id": masters["location"].pk,
        "quantity": Decimal("3.000"),
    }
    values.update(overrides)
    return receive_quantity(**values)


def test_receipt_without_expiry_creates_no_row(masters):
    result = _receive(masters)
    assert result.receipt_expiry is None
    assert ReceiptExpiry.objects.count() == 0
    assert StockBalance.objects.get().quantity == Decimal("3.000")


def test_expiry_is_receipt_scoped_and_allows_a_past_date(masters):
    expires_on = date(2020, 1, 1)
    result = _receive(masters, expires_on=expires_on)
    expiry = result.receipt_expiry
    assert expiry.expires_on == expires_on
    assert expiry.transaction_id == result.transaction.pk
    assert not hasattr(masters["material"], "expires_on")
    assert expiry.warning_status(date(2026, 9, 24)) == "expired"


def test_approaching_window_is_thirty_days(masters):
    today = timezone.localdate()
    near = _receive(masters, expires_on=today + timedelta(days=SKT_APPROACHING_WINDOW_DAYS))
    far = _receive(
        masters,
        expires_on=today + timedelta(days=SKT_APPROACHING_WINDOW_DAYS + 1),
    )
    assert near.receipt_expiry.warning_status(today) == "approaching"
    assert far.receipt_expiry.warning_status(today) is None


def test_same_operation_replays_one_expiry_row(masters):
    operation_id = uuid.uuid4()
    expires_on = date(2026, 12, 1)
    first = _receive(masters, operation_id=operation_id, expires_on=expires_on)
    replay = _receive(masters, operation_id=operation_id, expires_on=expires_on)
    assert replay.replayed is True
    assert replay.receipt_expiry.pk == first.receipt_expiry.pk
    assert ReceiptExpiry.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("3.000")


def test_different_expiry_on_same_operation_conflicts(masters):
    operation_id = uuid.uuid4()
    _receive(masters, operation_id=operation_id, expires_on=date(2026, 12, 1))
    with pytest.raises(ValidationError) as exc:
        _receive(masters, operation_id=operation_id, expires_on=date(2027, 1, 1))
    assert exc.value.code == OPERATION_CONFLICT
    assert ReceiptExpiry.objects.count() == 1


def test_invalid_expiry_rejected_without_stock(masters):
    with pytest.raises(ValidationError) as exc:
        _receive(masters, expires_on="2026-12-01")
    assert exc.value.code == INVALID_EXPIRY_DATE
    assert StockBalance.objects.count() == 0
    assert ReceiptExpiry.objects.count() == 0


def test_serialized_receipt_can_record_expiry(masters):
    result = receive_serialized(
        actor=masters["actor"],
        operation_id=uuid.uuid4(),
        material_id=masters["serialized"].pk,
        internal_asset_code=f"AST-{masters['suffix']}",
        serial_number=None,
        condition_id=masters["condition"].pk,
        target_location_id=masters["location"].pk,
        expires_on=date(2026, 10, 1),
    )
    assert result.receipt_expiry.expires_on == date(2026, 10, 1)
    assert StockBalance.objects.count() == 0


def test_inspection_does_not_change_stock(masters):
    result = _receive(masters, expires_on=date(2020, 5, 1))
    before = StockBalance.objects.get().quantity
    inspection = record_physical_inspection(
        actor=masters["actor"],
        receipt_id=result.transaction.pk,
        outcome="KONTROL_EDILDI_UYGUN",
        note="Rafta ayrıldı, etiket okundu ve kutu kenara alındı.",
    )
    assert inspection.receipt_expiry_id == result.transaction.pk
    assert StockBalance.objects.get().quantity == before
    assert ExpiryInspection.objects.count() == 1


def test_technician_cannot_record_inspection(masters):
    result = _receive(masters, expires_on=date(2020, 5, 1))
    with pytest.raises(PermissionDenied):
        record_physical_inspection(
            actor=masters["technician"],
            receipt_id=result.transaction.pk,
            outcome="KONTROL_EDILDI_UYGUN",
            note="Teknisyen kontrol notu yeterli uzunlukta.",
        )
    assert ExpiryInspection.objects.count() == 0


def test_short_note_rejected(masters):
    result = _receive(masters, expires_on=date(2020, 5, 1))
    with pytest.raises(ValidationError):
        record_physical_inspection(
            actor=masters["actor"],
            receipt_id=result.transaction.pk,
            outcome="KONTROL_EDILDI_UYGUN",
            note="kısa",
        )


def test_inspection_without_expiry_rejected(masters):
    result = _receive(masters)
    with pytest.raises(ValidationError):
        record_physical_inspection(
            actor=masters["actor"],
            receipt_id=result.transaction.pk,
            outcome="KONTROL_EDILDI_UYGUN",
            note="SKT olmayan girişe kontrol yazılamaz.",
        )


def test_expiry_and_inspection_are_immutable(masters):
    result = _receive(masters, expires_on=date(2026, 11, 1))
    expiry = result.receipt_expiry
    with pytest.raises(ValidationError):
        expiry.expires_on = date(2027, 1, 1)
        expiry.save()
    inspection = record_physical_inspection(
        actor=masters["actor"],
        receipt_id=expiry.pk,
        outcome="URUN_BULUNAMADI",
        note="Kutu açıldı, tarih etiketle aynı okundu.",
    )
    with pytest.raises(ValidationError):
        inspection.note = "Değiştirilmiş not yeterince uzun."
        inspection.save()
    with pytest.raises(ValidationError):
        inspection.delete()
    with pytest.raises(DatabaseError, match="immutable"):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_receiptexpiry SET expires_on = %s WHERE transaction_id = %s",
                [date(2030, 1, 1), expiry.pk],
            )


def test_warning_list_and_inspection_ui(client, masters):
    today = timezone.localdate()
    expired = _receive(masters, expires_on=today - timedelta(days=1))
    _receive(masters, expires_on=today + timedelta(days=10))
    _receive(masters, expires_on=today + timedelta(days=90))
    client.force_login(masters["actor"])
    response = client.get(reverse("inventory:expiry-warning-list"))
    assert response.status_code == 200
    assert len(response.context["warnings"]) == 2
    content = response.content.decode()
    assert "Süresi geçmiş" in content
    assert "Yaklaşıyor" in content

    post = client.post(
        reverse("inventory:expiry-inspection-create", args=[expired.transaction.pk]),
        {"note": "Süresi geçmiş kutu raftan ayrılıp kontrol alanına alındı.", "outcome": "AYIRMA_GEREKIYOR"},
    )
    assert post.status_code == 302
    assert ExpiryInspection.objects.filter(receipt_expiry_id=expired.transaction.pk).count() == 1
    assert StockBalance.objects.get(
        material=masters["material"],
        location=masters["location"],
        condition=masters["condition"],
    ).quantity == Decimal("9.000")
    detail = client.get(reverse("inventory:receipt-detail", args=[expired.transaction.pk]))
    assert detail.status_code == 200
    assert "Ayırma gerekiyor" in detail.content.decode()
    assert "Süresi geçmiş" in detail.content.decode()


def test_outcomes_stay_in_history_and_do_not_clear_or_move_stock(client, masters):
    today = timezone.localdate()
    expired = _receive(masters, expires_on=today - timedelta(days=1))
    before = StockBalance.objects.get().quantity
    record_physical_inspection(
        actor=masters["actor"],
        receipt_id=expired.transaction.pk,
        outcome="KONTROL_EDILDI_UYGUN",
        note="İlk bakışta kutu yerinde ve etiket okunur durumda.",
    )
    latest = record_physical_inspection(
        actor=masters["actor"],
        receipt_id=expired.transaction.pk,
        outcome="AYIRMA_GEREKIYOR",
        note="Uygun olmayan kutular elle ayrılmalı; stok henüz taşınmadı.",
    )
    outcomes = list(
        ExpiryInspection.objects.filter(receipt_expiry_id=expired.transaction.pk)
        .order_by("recorded_at", "id")
        .values_list("outcome", flat=True)
    )
    assert outcomes == ["KONTROL_EDILDI_UYGUN", "AYIRMA_GEREKIYOR"]
    assert latest.actor_id == masters["actor"].pk
    assert StockBalance.objects.get().quantity == before
    client.force_login(masters["actor"])
    page = client.get(reverse("inventory:expiry-warning-list"))
    body = page.content.decode()
    assert "Süresi geçmiş" in body
    assert "Ayırma gerekiyor" in body
    detail = client.get(reverse("inventory:receipt-detail", args=[expired.transaction.pk]))
    history = detail.content.decode()
    assert "Kontrol edildi, uygun" in history
    assert "Ayırma gerekiyor" in history
    assert "Süresi geçmiş" in history


def test_invalid_outcome_rejected_without_a_row(masters):
    result = _receive(masters, expires_on=date(2020, 5, 1))
    with pytest.raises(ValidationError) as exc:
        record_physical_inspection(
            actor=masters["actor"],
            receipt_id=result.transaction.pk,
            outcome="IMHA",
            note="Geçersiz sonuç stok yazmamalıdır.",
        )
    assert exc.value.code == "inventory.invalid_inspection_outcome"
    assert ExpiryInspection.objects.count() == 0


def test_expiry_on_today_is_approaching(masters):
    today = timezone.localdate()
    result = _receive(masters, expires_on=today)
    assert result.receipt_expiry.warning_status(today) == "approaching"

