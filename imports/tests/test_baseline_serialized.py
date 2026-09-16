from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError

from accounts.models import User
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountSerializedLine, PhysicalCountSession
from counting.services import (
    add_candidate_serialized_count,
    complete_physical_count,
    create_physical_count_session,
    mark_serialized_asset_missing,
    record_serialized_asset_count,
    start_physical_count_session,
)
from imports.models import InventoryBaseline
from imports.services import establish_inventory_baseline, prepare_inventory_baseline
from inventory.models import InventoryTransaction, SerializedAsset
from inventory.services.projections import verify_serialized_projection
from inventory.services.receipts import receive_serialized
from locations.models import Location


pytestmark = pytest.mark.django_db

EXPLANATION = "Tekil kesim onay açıklaması yeterince uzun."


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
    counter = User.objects.create_user(username=f"sbl-c-{suffix}")
    counter = _perm(
        counter, app="inventory", model="inventorytransaction", codename="receive_stock"
    )
    establisher = User.objects.create_user(username=f"sbl-e-{suffix}")
    establisher = _perm(
        establisher, app="imports", model="inventorybaseline", codename="establish_baseline"
    )
    category = Category.objects.create(code=f"SBL-CAT-{suffix}", name="Kat")
    material = Material.objects.create(
        material_code=f"SBL-S-{suffix}",
        name="Tekil",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    other_material = Material.objects.create(
        material_code=f"SBL-S2-{suffix}",
        name="İkinci tekil",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"SBL-C-{suffix}", name="Yeni", sort_order=850
    )
    other_condition = MaterialCondition.objects.create(
        code=f"SBL-C2-{suffix}", name="Kullanılmış", sort_order=851
    )
    root = Location.objects.create(code=f"SBL-R-{suffix}", name="Kök")
    child = Location.objects.create(
        code=f"SBL-L-{suffix}", name="Raf", parent=root, can_hold_stock=True
    )
    grandchild = Location.objects.create(
        code=f"SBL-G-{suffix}", name="Alt", parent=child, can_hold_stock=True
    )
    return locals()


def _receive_asset(objects, **overrides):
    values = {
        "actor": objects["counter"],
        "operation_id": uuid.uuid4(),
        "material_id": objects["material"].pk,
        "internal_asset_code": f"SBL-A-{uuid.uuid4().hex[:8]}",
        "serial_number": f"SBL-S-{uuid.uuid4().hex[:8]}",
        "condition_id": objects["condition"].pk,
        "target_location_id": objects["child"].pk,
    }
    values.update(overrides)
    return receive_serialized(**values).serialized_asset


def _started(objects):
    session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=objects["root"].pk,
        baseline_candidate=True,
    )
    return start_physical_count_session(actor=objects["counter"], session_id=session.pk)


def _complete(objects, session_id):
    complete_physical_count(actor=objects["counter"], session_id=session_id)
    return PhysicalCountSession.objects.get(pk=session_id)


def _establish(objects, session, *, operation_id=None):
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    return establish_inventory_baseline(
        actor=objects["establisher"],
        baseline_id=prepared.baseline.pk,
        operation_id=operation_id or uuid.uuid4(),
        explanation=EXPLANATION,
    )


def test_expected_matching_asset_is_accepted_without_new_identity(objects):
    asset = _receive_asset(objects)
    started = _started(objects)
    record_serialized_asset_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=asset.current_condition_id,
    )
    session = _complete(objects, started.session.pk)
    result = _establish(objects, session)
    assert result.transactions == ()
    assert SerializedAsset.objects.count() == 1
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0
    assert SerializedAsset.objects.get(pk=asset.pk).internal_asset_code == asset.internal_asset_code


def test_missing_expected_asset_is_rejected(objects):
    asset = _receive_asset(objects)
    started = _started(objects)
    line = started.session.serialized_lines.get()
    mark_serialized_asset_missing(
        actor=objects["counter"],
        session_id=started.session.pk,
        line_id=line.pk,
    )
    session = _complete(objects, started.session.pk)
    with pytest.raises(ValidationError) as exc:
        _establish(objects, session)
    assert exc.value.code == "imports.serialized_discrepancy"
    assert InventoryBaseline.objects.get().status == InventoryBaseline.Status.PREPARED
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0


def test_wrong_location_or_condition_is_rejected(objects):
    asset = _receive_asset(objects)
    started = _started(objects)
    record_serialized_asset_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=objects["grandchild"].pk,
        observed_condition_id=asset.current_condition_id,
    )
    session = _complete(objects, started.session.pk)
    with pytest.raises(ValidationError) as exc:
        _establish(objects, session)
    assert exc.value.code == "imports.serialized_discrepancy"


def test_wrong_condition_is_rejected(objects):
    asset = _receive_asset(objects)
    started = _started(objects)
    record_serialized_asset_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=objects["other_condition"].pk,
    )
    session = _complete(objects, started.session.pk)
    with pytest.raises(ValidationError) as exc:
        _establish(objects, session)
    assert exc.value.code == "imports.serialized_discrepancy"


def test_new_serialized_receive_in_subtree_is_drift(objects):
    asset = _receive_asset(objects)
    started = _started(objects)
    record_serialized_asset_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=asset.current_condition_id,
    )
    session = _complete(objects, started.session.pk)
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    _receive_asset(objects, internal_asset_code="NEW-IN-SCOPE")
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.count_drift"


def test_candidate_promotes_exactly_one_asset_without_quantity_one(objects):
    started = _started(objects)
    line = add_candidate_serialized_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        material_id=objects["material"].pk,
        internal_asset_code="  CAND-1  ",
        serial_number="  ",
        observed_location_id=objects["child"].pk,
        observed_condition_id=objects["condition"].pk,
    )
    session = _complete(objects, started.session.pk)
    result = _establish(objects, session)
    assert len(result.transactions) == 1
    tx_line = result.transactions[0].lines.get()
    asset = SerializedAsset.objects.get()
    assert asset.internal_asset_code == "CAND-1"
    assert asset.serial_number is None
    assert asset.current_location_id == objects["child"].pk
    assert asset.current_condition_id == objects["condition"].pk
    assert tx_line.serialized_asset_id == asset.pk
    assert tx_line.quantity is None
    assert tx_line.unit_id is None
    line.refresh_from_db()
    assert line.serialized_asset_id is None
    assert verify_serialized_projection() == ()


def test_candidate_preserves_case_and_distinct_codes(objects):
    _receive_asset(objects, internal_asset_code="AS-1", serial_number="SN-EXIST")
    started = _started(objects)
    existing_line = started.session.serialized_lines.get()
    record_serialized_asset_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        serialized_asset_id=existing_line.serialized_asset_id,
        observed_location_id=objects["child"].pk,
        observed_condition_id=objects["condition"].pk,
    )
    add_candidate_serialized_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        material_id=objects["material"].pk,
        internal_asset_code="as-1",
        serial_number=None,
        observed_location_id=objects["child"].pk,
        observed_condition_id=objects["condition"].pk,
    )
    session = _complete(objects, started.session.pk)
    result = _establish(objects, session)
    assert SerializedAsset.objects.filter(internal_asset_code="AS-1").count() == 1
    assert SerializedAsset.objects.filter(internal_asset_code="as-1").count() == 1
    assert len(result.transactions) == 1


def test_candidate_serial_collision_is_rejected(objects):
    started = _started(objects)
    add_candidate_serialized_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        material_id=objects["material"].pk,
        internal_asset_code="NEW-CODE",
        serial_number="DUP-SN",
        observed_location_id=objects["child"].pk,
        observed_condition_id=objects["condition"].pk,
    )
    session = _complete(objects, started.session.pk)
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    _receive_asset(objects, internal_asset_code="EXIST-CODE", serial_number="DUP-SN")
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code in {"imports.identity_conflict", "imports.count_drift"}


def test_candidate_code_becoming_authoritative_before_establish_is_conflict_or_drift(
    objects,
):
    started = _started(objects)
    add_candidate_serialized_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        material_id=objects["material"].pk,
        internal_asset_code="RACE-CODE",
        serial_number=None,
        observed_location_id=objects["child"].pk,
        observed_condition_id=objects["condition"].pk,
    )
    session = _complete(objects, started.session.pk)
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    _receive_asset(objects, internal_asset_code="RACE-CODE", serial_number=None)
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code in {"imports.count_drift", "imports.identity_conflict"}
    assert InventoryBaseline.objects.get().status == InventoryBaseline.Status.PREPARED


def test_combined_quantity_and_serialized_cutover_verifies_clean(objects):
    from counting.services import add_unexpected_quantity_count, record_quantity_count
    from catalog.models import Material
    from inventory.models import StockBalance
    from inventory.services.projections import verify_quantity_projection

    quantity_material = Material.objects.create(
        material_code=f"SBL-Q-{uuid.uuid4().hex[:6]}",
        name="Miktar",
        category=objects["category"],
        unit=UnitOfMeasure.objects.create(code=f"SBL-U-{uuid.uuid4().hex[:6]}", name="Adet"),
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    started = _started(objects)
    add_unexpected_quantity_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        material_id=quantity_material.pk,
        location_id=objects["child"].pk,
        condition_id=objects["condition"].pk,
        counted_quantity=Decimal("5.000"),
    )
    add_candidate_serialized_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        material_id=objects["material"].pk,
        internal_asset_code="COMB-ASSET",
        serial_number=None,
        observed_location_id=objects["child"].pk,
        observed_condition_id=objects["condition"].pk,
    )
    session = _complete(objects, started.session.pk)
    result = _establish(objects, session)
    assert len(result.transactions) == 1
    assert result.transactions[0].lines.count() == 2
    assert StockBalance.objects.get(material=quantity_material).quantity == Decimal("5.000")
    assert SerializedAsset.objects.get(internal_asset_code="COMB-ASSET")
    assert verify_quantity_projection() == ()
    assert verify_serialized_projection() == ()


def test_location_change_of_expected_asset_is_drift(objects):
    asset = _receive_asset(objects)
    started = _started(objects)
    record_serialized_asset_count(
        actor=objects["counter"],
        session_id=started.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=asset.current_condition_id,
    )
    session = _complete(objects, started.session.pk)
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    SerializedAsset.objects.filter(pk=asset.pk).update(
        current_location_id=objects["grandchild"].pk
    )
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.count_drift"

