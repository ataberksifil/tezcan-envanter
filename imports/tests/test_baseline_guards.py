from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction

from accounts.models import User
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountSession
from counting.services import (
    add_unexpected_quantity_count,
    complete_physical_count,
    create_physical_count_session,
    record_quantity_count,
    start_physical_count_session,
)
from imports.models import (
    InventoryBaseline,
    InventoryBaselineCountSessionLink,
    InventoryBaselineTransactionLink,
)
from imports.services import establish_inventory_baseline, prepare_inventory_baseline
from inventory.models import InventoryTransaction, InventoryTransactionLine
from inventory.services.receipts import receive_quantity
from locations.models import Location


pytestmark = pytest.mark.django_db

EXPLANATION = "Kesim açıklaması veritabanı koruması için yeterince uzun."


def _perm(user, *, app, model, codename):
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label=app,
            content_type__model=model,
            codename=codename,
        )
    )
    return type(user).objects.get(pk=user.pk)


@pytest.fixture
def objects():
    suffix = uuid.uuid4().hex[:8]
    counter = User.objects.create_user(username=f"gbl-c-{suffix}")
    counter = _perm(
        counter, app="inventory", model="inventorytransaction", codename="receive_stock"
    )
    establisher = User.objects.create_user(username=f"gbl-e-{suffix}")
    establisher = _perm(
        establisher, app="imports", model="inventorybaseline", codename="establish_baseline"
    )
    category = Category.objects.create(code=f"GBL-CAT-{suffix}", name="Kat")
    unit = UnitOfMeasure.objects.create(code=f"GBL-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"GBL-M-{suffix}",
        name="Malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(code=f"GBL-C-{suffix}", name="Yeni")
    root = Location.objects.create(code=f"GBL-R-{suffix}", name="Kök")
    child = Location.objects.create(
        code=f"GBL-L-{suffix}", name="Raf", parent=root, can_hold_stock=True
    )
    return locals()


def _opening_session(objects):
    session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=objects["root"].pk,
        baseline_candidate=True,
    )
    start_physical_count_session(actor=objects["counter"], session_id=session.pk)
    add_unexpected_quantity_count(
        actor=objects["counter"],
        session_id=session.pk,
        material_id=objects["material"].pk,
        location_id=objects["child"].pk,
        condition_id=objects["condition"].pk,
        counted_quantity=Decimal("2.000"),
    )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    return PhysicalCountSession.objects.get(pk=session.pk)


def test_established_baseline_and_links_cannot_be_mutated(objects):
    session = _opening_session(objects)
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    result = establish_inventory_baseline(
        actor=objects["establisher"],
        baseline_id=prepared.baseline.pk,
        operation_id=uuid.uuid4(),
        explanation=EXPLANATION,
    )
    baseline = result.baseline
    with pytest.raises(ValidationError, match="değiştirilemez"):
        baseline.reference = "CHANGED"
        baseline.save()
    with pytest.raises(ValidationError, match="silinemez"):
        baseline.delete()
    with pytest.raises(DatabaseError, match="cannot be updated"):
        with transaction.atomic():
            InventoryBaseline.objects.filter(pk=baseline.pk).update(reference="RAW")
    with connection.cursor() as cursor:
        with pytest.raises(DatabaseError, match="cannot be deleted"):
            with transaction.atomic():
                cursor.execute(
                    "DELETE FROM inventory_baselines WHERE id = %s",
                    [baseline.pk],
                )
        with pytest.raises(DatabaseError, match="cannot be updated"):
            with transaction.atomic():
                cursor.execute(
                    "UPDATE inventory_baselines SET reference = %s WHERE id = %s",
                    ["SQL-CHANGE", baseline.pk],
                )
    link = baseline.count_session_links.get()
    with pytest.raises(ValidationError, match="silinemez"):
        link.delete()
    with pytest.raises(DatabaseError, match="cannot be deleted"):
        with transaction.atomic():
            InventoryBaselineCountSessionLink.objects.filter(pk=link.pk).delete()
    tx_link = baseline.transaction_links.get()
    with pytest.raises(ValidationError, match="silinemez"):
        tx_link.delete()
    with pytest.raises(DatabaseError, match="cannot be deleted"):
        with transaction.atomic():
            InventoryBaselineTransactionLink.objects.filter(pk=tx_link.pk).delete()



def test_result_link_cannot_point_to_non_initial_balance(objects):
    session = _opening_session(objects)
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    receipt = receive_quantity(
        actor=objects["counter"],
        operation_id=uuid.uuid4(),
        material_id=objects["material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["child"].pk,
        quantity=Decimal("1.000"),
    )
    with pytest.raises(Exception, match="INITIAL_BALANCE"):
        with transaction.atomic():
            InventoryBaselineTransactionLink.objects.create(
                inventory_baseline=prepared.baseline,
                inventory_transaction=receipt.transaction,
                physical_count_session=session,
                scope_key="other",
                operation_id=uuid.uuid4(),
            )


def test_same_session_cannot_belong_to_competing_baselines(objects):
    session = _opening_session(objects)
    prepare_inventory_baseline(actor=objects["establisher"], session_ids=[session.pk])
    with pytest.raises(ValidationError, match="başka bir baseline"):
        prepare_inventory_baseline(
            actor=objects["establisher"], session_ids=[session.pk]
        )


def test_non_candidate_session_link_is_blocked(objects):
    receive_quantity(
        actor=objects["counter"],
        operation_id=uuid.uuid4(),
        material_id=objects["material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["child"].pk,
        quantity=Decimal("1.000"),
    )
    session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=objects["root"].pk,
        baseline_candidate=False,
    )
    start_physical_count_session(actor=objects["counter"], session_id=session.pk)
    line = session.quantity_lines.get()
    record_quantity_count(
        actor=objects["counter"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=line.expected_quantity,
    )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    baseline = InventoryBaseline.objects.create(
        reference=f"BL-{uuid.uuid4().hex[:8]}",
        created_by=objects["establisher"],
    )
    with pytest.raises(Exception, match="baseline-candidate"):
        with transaction.atomic():
            InventoryBaselineCountSessionLink.objects.create(
                inventory_baseline=baseline,
                physical_count_session=session,
            )


def test_quantity_serialized_mix_still_blocked_on_receipt(objects):
    from inventory.models import SerializedAsset

    serialized_material = Material.objects.create(
        material_code=f"GBL-S-{uuid.uuid4().hex[:6]}",
        name="Tekil",
        category=objects["category"],
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    header = InventoryTransaction.objects.create(
        operation_id=uuid.uuid4(),
        request_fingerprint="c" * 64,
        transaction_type=InventoryTransaction.TransactionType.RECEIPT,
        acting_user=objects["establisher"],
        occurred_at="2026-09-16T08:00:00+03:00",
    )
    asset = SerializedAsset.objects.create(
        material=serialized_material,
        internal_asset_code=f"GBL-A-{uuid.uuid4().hex[:6]}",
        current_location=objects["child"],
        current_condition=objects["condition"],
    )
    with transaction.atomic():
        InventoryTransactionLine.objects.create(
            transaction=header,
            line_number=1,
            material=serialized_material,
            serialized_asset=asset,
            quantity=None,
            unit=None,
            condition=objects["condition"],
            target_location=objects["child"],
            asset_event_seq=1,
        )
    with pytest.raises(Exception, match="cannot share a RECEIVE"):
        with transaction.atomic():
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=2,
                material=objects["material"],
                quantity=Decimal("1.000"),
                unit=objects["unit"],
                condition=objects["condition"],
                target_location=objects["child"],
            )
