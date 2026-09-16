from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountQuantityLine, PhysicalCountSession
from counting.services import (
    add_unexpected_quantity_count,
    complete_physical_count,
    create_physical_count_session,
    mark_quantity_line_not_counted,
    record_quantity_count,
    start_physical_count_session,
)
from imports.models import (
    InventoryBaseline,
    InventoryBaselineCountSessionLink,
    InventoryBaselineTransactionLink,
)
from imports.services import (
    establish_inventory_baseline,
    prepare_inventory_baseline,
)
from inventory.models import InventoryTransaction, StockBalance
from inventory.services.issues import issue_quantity
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import receive_quantity
from inventory.services.transfers import transfer_quantity
from locations.models import Location


pytestmark = pytest.mark.django_db

EXPLANATION = "Kesim onay açıklaması yeterli uzunlukta."


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
    counter = User.objects.create_user(username=f"bl-c-{suffix}")
    counter = _perm(
        counter, app="inventory", model="inventorytransaction", codename="receive_stock"
    )
    counter = _perm(
        counter, app="inventory", model="inventorytransaction", codename="issue_stock"
    )
    counter = _perm(
        counter, app="inventory", model="inventorytransaction", codename="transfer_stock"
    )
    establisher = User.objects.create_user(username=f"bl-e-{suffix}")
    establisher = _perm(
        establisher, app="imports", model="inventorybaseline", codename="establish_baseline"
    )
    establisher = _perm(
        establisher, app="inventory", model="inventorytransaction", codename="receive_stock"
    )
    category = Category.objects.create(code=f"BL-CAT-{suffix}", name="Kat")
    unit = UnitOfMeasure.objects.create(code=f"BL-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"BL-M-{suffix}",
        name="Malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    other_material = Material.objects.create(
        material_code=f"BL-M2-{suffix}",
        name="İkinci",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(code=f"BL-C-{suffix}", name="Yeni")
    root = Location.objects.create(code=f"BL-R-{suffix}", name="Kök")
    child = Location.objects.create(
        code=f"BL-L-{suffix}", name="Raf", parent=root, can_hold_stock=True
    )
    grandchild = Location.objects.create(
        code=f"BL-G-{suffix}", name="Alt", parent=child, can_hold_stock=True
    )
    sibling_root = Location.objects.create(code=f"BL-SR-{suffix}", name="Diğer kök")
    sibling = Location.objects.create(
        code=f"BL-SL-{suffix}", name="Diğer raf", parent=sibling_root, can_hold_stock=True
    )
    return locals()


def _receive(objects, *, quantity, material=None, location=None, actor=None):
    return receive_quantity(
        actor=actor or objects["counter"],
        operation_id=uuid.uuid4(),
        material_id=(material or objects["material"]).pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=(location or objects["child"]).pk,
        quantity=quantity,
    )


def _completed_session(objects, *, scope=None, counted=None, expected=None, material=None, location=None):
    if expected:
        _receive(objects, quantity=expected, material=material, location=location)
    session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=(scope or objects["root"]).pk,
        baseline_candidate=True,
    )
    start_physical_count_session(actor=objects["counter"], session_id=session.pk)
    session = PhysicalCountSession.objects.get(pk=session.pk)
    for line in session.quantity_lines.all():
        record_quantity_count(
            actor=objects["counter"],
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=counted if counted is not None else line.expected_quantity,
        )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    return PhysicalCountSession.objects.get(pk=session.pk)


def _establish(objects, session_or_ids, *, operation_id=None, explanation=EXPLANATION):
    session_ids = (
        [session_or_ids]
        if isinstance(session_or_ids, PhysicalCountSession)
        else list(session_or_ids)
    )
    if session_ids and isinstance(session_ids[0], PhysicalCountSession):
        session_ids = [session.pk for session in session_ids]
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"],
        session_ids=session_ids,
    )
    return establish_inventory_baseline(
        actor=objects["establisher"],
        baseline_id=prepared.baseline.pk,
        operation_id=operation_id or uuid.uuid4(),
        explanation=explanation,
    )


def test_prepare_rejects_unauthorized_actor(objects):
    session = _completed_session(objects, expected=Decimal("1.000"), counted=Decimal("1.000"))
    with pytest.raises(PermissionDenied):
        prepare_inventory_baseline(
            actor=objects["counter"], session_ids=[session.pk]
        )


def test_prepare_one_session_is_non_authoritative(objects):
    session = _completed_session(objects, expected=Decimal("2.000"), counted=Decimal("2.000"))
    result = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    assert result.baseline.status == InventoryBaseline.Status.PREPARED
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0
    assert StockBalance.objects.filter(quantity__gt=0).count() == 1


def test_prepare_multiple_disjoint_sessions(objects):
    first = _completed_session(objects, expected=Decimal("1.000"), counted=Decimal("1.000"))
    second = _completed_session(
        objects,
        scope=objects["sibling_root"],
        expected=Decimal("2.000"),
        counted=Decimal("2.000"),
        location=objects["sibling"],
        material=objects["other_material"],
    )
    result = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[first.pk, second.pk]
    )
    assert result.baseline.count_session_links.count() == 2


def test_prepare_rejects_overlapping_scopes(objects):
    first = _completed_session(objects, expected=Decimal("1.000"), counted=Decimal("1.000"))
    nested = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=objects["child"].pk,
        baseline_candidate=True,
    )
    start_physical_count_session(actor=objects["counter"], session_id=nested.pk)
    nested = PhysicalCountSession.objects.get(pk=nested.pk)
    for line in nested.quantity_lines.all():
        record_quantity_count(
            actor=objects["counter"],
            session_id=nested.pk,
            line_id=line.pk,
            counted_quantity=line.expected_quantity,
        )
    if not nested.quantity_lines.exists():
        complete_physical_count(actor=objects["counter"], session_id=nested.pk)
    else:
        complete_physical_count(actor=objects["counter"], session_id=nested.pk)
    with pytest.raises(ValidationError) as exc:
        prepare_inventory_baseline(
            actor=objects["establisher"], session_ids=[first.pk, nested.pk]
        )
    assert exc.value.code == "imports.overlapping_scope"


def test_prepare_rejects_non_baseline_candidate(objects):
    _receive(objects, quantity=Decimal("1.000"))
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
    with pytest.raises(ValidationError) as exc:
        prepare_inventory_baseline(
            actor=objects["establisher"], session_ids=[session.pk]
        )
    assert exc.value.code == "imports.invalid_session"


def test_prepare_rejects_pending_count_session(objects):
    session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=objects["root"].pk,
        baseline_candidate=True,
    )
    start_physical_count_session(actor=objects["counter"], session_id=session.pk)
    with pytest.raises(ValidationError) as exc:
        prepare_inventory_baseline(
            actor=objects["establisher"], session_ids=[session.pk]
        )
    assert exc.value.code == "imports.invalid_session"


def test_establish_rejects_not_counted_after_prepare(objects):
    session = _completed_session(objects, expected=Decimal("1.000"), counted=Decimal("1.000"))
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    line = session.quantity_lines.get()
    PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
        counted_quantity=None,
        resolution_status=PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED,
        counted_by_user=objects["counter"],
        counted_at=timezone.now(),
    )
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.incomplete_count"
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0


def test_matching_existing_bucket_creates_no_initial_balance(objects):
    session = _completed_session(objects, expected=Decimal("5.000"), counted=Decimal("5.000"))
    result = _establish(objects, session)
    assert result.transactions == ()
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0
    assert StockBalance.objects.get().quantity == Decimal("5.000")
    assert result.baseline.status == InventoryBaseline.Status.ESTABLISHED
    assert AuditEvent.objects.filter(event_type="InventoryBaselineEstablished").count() == 1


def test_existing_bucket_physical_discrepancy_is_rejected(objects):
    session = _completed_session(objects, expected=Decimal("5.000"), counted=Decimal("7.000"))
    with pytest.raises(ValidationError) as exc:
        _establish(objects, session)
    assert exc.value.code == "imports.prior_history"
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0
    assert InventoryTransaction.objects.filter(transaction_type="COUNT_RECONCILIATION").count() == 0
    assert InventoryBaseline.objects.get().status == InventoryBaseline.Status.PREPARED


def test_unexpected_no_history_counted_positive_creates_initial_balance(objects):
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
        counted_quantity=Decimal("4.000"),
    )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    result = _establish(objects, session)
    assert len(result.transactions) == 1
    line = result.transactions[0].lines.get()
    assert line.quantity == Decimal("4.000")
    assert line.unit_id == objects["material"].unit_id
    assert line.source_location_id is None
    assert line.target_location_id == objects["child"].pk
    assert StockBalance.objects.get().quantity == Decimal("4.000")
    assert verify_quantity_projection() == ()


def test_counted_zero_creates_no_fake_line(objects):
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
        counted_quantity=Decimal("0.000"),
    )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    result = _establish(objects, session)
    assert result.transactions == ()
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0
    assert result.baseline.count_session_links.count() == 1


def test_historical_zero_bucket_unexpected_count_rejected(objects):
    from inventory.services.issues import issue_quantity as do_issue
    from accounts.models import Employee
    from inventory.models import ProductionLine

    _receive(objects, quantity=Decimal("2.000"))
    employee = Employee.objects.create(
        employee_number=f"BL-E-{uuid.uuid4().hex[:6]}",
        first_name="Ali",
        last_name="Kaya",
    )
    line = ProductionLine.objects.create(
        code=f"BL-PL-{uuid.uuid4().hex[:6]}", name="Hat"
    )
    do_issue(
        actor=objects["counter"],
        operation_id=uuid.uuid4(),
        material_id=objects["material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        source_location_id=objects["child"].pk,
        quantity=Decimal("2.000"),
        receiver_employee_id=employee.pk,
        production_line_id=line.pk,
        usage_location_text="Hat kenarı kullanım yeri",
    )
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
        counted_quantity=Decimal("3.000"),
    )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    with pytest.raises(ValidationError) as exc:
        _establish(objects, session)
    assert exc.value.code == "imports.prior_history"


def test_orphan_positive_stockbalance_is_integrity_rejection(objects):
    StockBalance.objects.create(
        material=objects["material"],
        location=objects["child"],
        condition=objects["condition"],
        quantity=Decimal("6.000"),
    )
    session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=objects["root"].pk,
        baseline_candidate=True,
    )
    start_physical_count_session(actor=objects["counter"], session_id=session.pk)
    line = session.quantity_lines.get()
    record_quantity_count(
        actor=objects["counter"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("6.000"),
    )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    with pytest.raises(ValidationError) as exc:
        _establish(objects, session)
    assert exc.value.code == "imports.projection_integrity"


def test_one_baseline_cannot_double_apply_same_session(objects):
    session = _completed_session(objects, expected=Decimal("1.000"), counted=Decimal("1.000"))
    _establish(objects, session)
    with pytest.raises(ValidationError) as exc:
        prepare_inventory_baseline(
            actor=objects["establisher"], session_ids=[session.pk]
        )
    assert exc.value.code == "imports.invalid_session"


def test_explanation_and_self_approval_rules(objects):
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
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation="kısa",
        )
    assert exc.value.code == "imports.invalid_explanation"

    objects["counter"].user_permissions.add(
        Permission.objects.get(
            content_type__app_label="imports",
            content_type__model="inventorybaseline",
            codename="establish_baseline",
        )
    )
    counter = User.objects.get(pk=objects["counter"].pk)
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=counter,
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.self_approval"

    matching = _completed_session(
        objects,
        scope=objects["sibling_root"],
        expected=Decimal("1.000"),
        counted=Decimal("1.000"),
        location=objects["sibling"],
        material=objects["other_material"],
    )
    matching_prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[matching.pk]
    )
    result = establish_inventory_baseline(
        actor=counter,
        baseline_id=matching_prepared.baseline.pk,
        operation_id=uuid.uuid4(),
        explanation=EXPLANATION,
    )
    assert result.baseline.status == InventoryBaseline.Status.ESTABLISHED


def test_idempotent_establish_replay_and_conflict(objects):
    session = _completed_session(objects, expected=Decimal("1.000"), counted=Decimal("1.000"))
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    operation_id = uuid.uuid4()
    first = establish_inventory_baseline(
        actor=objects["establisher"],
        baseline_id=prepared.baseline.pk,
        operation_id=operation_id,
        explanation=EXPLANATION,
    )
    second = establish_inventory_baseline(
        actor=objects["establisher"],
        baseline_id=prepared.baseline.pk,
        operation_id=operation_id,
        explanation=EXPLANATION,
    )
    assert second.replayed is True
    assert second.baseline.pk == first.baseline.pk
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=operation_id,
            explanation="Farklı kesim açıklaması burada yeterince uzun.",
        )
    assert exc.value.code == "inventory.operation_conflict"


def test_quantity_receive_drift_refuses_without_mutation(objects):
    session = _completed_session(objects, expected=Decimal("2.000"), counted=Decimal("2.000"))
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    _receive(objects, quantity=Decimal("1.000"))
    before = list(
        InventoryTransaction.objects.order_by("pk").values_list("pk", "transaction_type")
    )
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.count_drift"
    after = list(
        InventoryTransaction.objects.order_by("pk").values_list("pk", "transaction_type")
    )
    assert after == before
    assert InventoryBaseline.objects.get().status == InventoryBaseline.Status.PREPARED


def test_new_positive_bucket_in_subtree_is_drift(objects):
    session = _completed_session(objects, expected=Decimal("1.000"), counted=Decimal("1.000"))
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    _receive(
        objects,
        quantity=Decimal("2.000"),
        material=objects["other_material"],
        location=objects["grandchild"],
    )
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.count_drift"


def test_issue_and_transfer_drift_refuse(objects):
    from accounts.models import Employee
    from inventory.models import ProductionLine

    session = _completed_session(objects, expected=Decimal("5.000"), counted=Decimal("5.000"))
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    employee = Employee.objects.create(
        employee_number=f"BL-E-{uuid.uuid4().hex[:6]}",
        first_name="Ali",
        last_name="Kaya",
    )
    production_line = ProductionLine.objects.create(
        code=f"BL-PL-{uuid.uuid4().hex[:6]}", name="Hat"
    )
    issue_quantity(
        actor=objects["counter"],
        operation_id=uuid.uuid4(),
        material_id=objects["material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        source_location_id=objects["child"].pk,
        quantity=Decimal("1.000"),
        receiver_employee_id=employee.pk,
        production_line_id=production_line.pk,
        usage_location_text="Hat kenarı kullanım yeri",
    )
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.count_drift"

    session2 = _completed_session(
        objects,
        scope=objects["sibling_root"],
        expected=Decimal("3.000"),
        counted=Decimal("3.000"),
        location=objects["sibling"],
        material=objects["other_material"],
    )
    prepared2 = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session2.pk]
    )
    transfer_quantity(
        actor=objects["counter"],
        operation_id=uuid.uuid4(),
        material_id=objects["other_material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        source_location_id=objects["sibling"].pk,
        target_location_id=objects["child"].pk,
        quantity=Decimal("1.000"),
    )
    with pytest.raises(ValidationError) as exc:
        establish_inventory_baseline(
            actor=objects["establisher"],
            baseline_id=prepared2.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation=EXPLANATION,
        )
    assert exc.value.code == "imports.count_drift"


def test_corrupted_projection_rolls_back_establishment(objects):
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
    prepared = prepare_inventory_baseline(
        actor=objects["establisher"], session_ids=[session.pk]
    )
    from inventory.services.projections import QuantityProjectionMismatch

    fake = QuantityProjectionMismatch(
        material_id=objects["material"].pk,
        location_id=objects["child"].pk,
        condition_id=objects["condition"].pk,
        expected_quantity=Decimal("2.000"),
        actual_quantity=Decimal("9.000"),
    )
    with patch(
        "imports.services.verify_quantity_projection",
        return_value=(fake,),
    ):
        with pytest.raises(ValidationError) as exc:
            establish_inventory_baseline(
                actor=objects["establisher"],
                baseline_id=prepared.baseline.pk,
                operation_id=uuid.uuid4(),
                explanation=EXPLANATION,
            )
    assert exc.value.code == "imports.projection_mismatch"
    assert InventoryBaseline.objects.get().status == InventoryBaseline.Status.PREPARED
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 0
    assert StockBalance.objects.filter(quantity__gt=0).count() == 0

