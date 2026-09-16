import uuid
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from accounts.models import User
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountQuantityRejection,
    PhysicalCountSession,
)
from counting.services import (
    INVALID_APPROVAL_EXPLANATION,
    INVALID_LINE,
    INVALID_RECONCILIATION,
    SELF_APPROVAL,
    add_unexpected_quantity_count,
    approve_quantity_discrepancy,
    complete_physical_count,
    create_physical_count_session,
    record_quantity_count,
    mark_quantity_line_not_counted,
    reject_quantity_discrepancy,
    start_physical_count_session,
)
from inventory.models import InventoryTransaction, InventoryTransactionLine, StockBalance
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import receive_quantity
from locations.models import Location


pytestmark = pytest.mark.django_db


@pytest.fixture
def objects():
    suffix = uuid.uuid4().hex[:8]
    counter = User.objects.create_user(username=f"counter-{suffix}")
    approver = User.objects.create_user(username=f"approver-{suffix}")
    approver.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="counting",
            content_type__model="physicalcountquantityline",
            codename="decide_discrepancy",
        ),
        Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename="receive_stock",
        ),
    )
    approver = User.objects.get(pk=approver.pk)
    category = Category.objects.create(code=f"RC-CAT-{suffix}", name="Kategori")
    unit = UnitOfMeasure.objects.create(code=f"RC-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"RC-M-{suffix}",
        name="Malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(code=f"RC-C-{suffix}", name="Yeni")
    root = Location.objects.create(code=f"RC-R-{suffix}", name="Kök")
    location = Location.objects.create(
        code=f"RC-L-{suffix}", name="Raf", parent=root, can_hold_stock=True
    )
    return locals()


def _completed_discrepancy(objects, *, expected, counted, baseline=False):
    if expected:
        receive_quantity(
            actor=objects["approver"],
            operation_id=uuid.uuid4(),
            material_id=objects["material"].pk,
            unit_id=objects["unit"].pk,
            condition_id=objects["condition"].pk,
            target_location_id=objects["location"].pk,
            quantity=expected,
        )
    session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"RC-{uuid.uuid4().hex}",
        scope_location_id=objects["root"].pk,
        baseline_candidate=baseline,
    )
    session = start_physical_count_session(
        actor=objects["counter"], session_id=session.pk
    ).session
    if expected:
        line = session.quantity_lines.get()
        line = record_quantity_count(
            actor=objects["counter"],
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=counted,
        )
    else:
        line = add_unexpected_quantity_count(
            actor=objects["counter"],
            session_id=session.pk,
            material_id=objects["material"].pk,
            location_id=objects["location"].pk,
            condition_id=objects["condition"].pk,
            counted_quantity=counted,
        )
    session = complete_physical_count(actor=objects["counter"], session_id=session.pk)
    return session, line


@pytest.mark.parametrize(
    ("expected", "counted", "source", "target"),
    [
        (Decimal("5.000"), Decimal("7.000"), False, True),
        (Decimal("5.000"), Decimal("3.000"), True, False),
        (Decimal("0.000"), Decimal("4.000"), False, True),
    ],
)
def test_approval_creates_one_directional_line_and_exact_projection(
    objects, expected, counted, source, target
):
    session, count_line = _completed_discrepancy(
        objects, expected=expected, counted=counted
    )
    result = approve_quantity_discrepancy(
        actor=objects["approver"],
        session_id=session.pk,
        line_id=count_line.pk,
        operation_id=uuid.uuid4(),
        explanation="Fiziksel sayım yeniden doğrulandı.",
    )

    ledger_line = result.transaction.lines.get()
    assert result.transaction.transaction_type == "COUNT_RECONCILIATION"
    assert result.transaction.lines.count() == 1
    assert (ledger_line.source_location_id is not None) is source
    assert (ledger_line.target_location_id is not None) is target
    assert ledger_line.quantity == abs(counted - expected)
    assert StockBalance.objects.get(
        material=objects["material"],
        location=objects["location"],
        condition=objects["condition"],
    ).quantity == counted
    assert verify_quantity_projection() == ()
    result.line.refresh_from_db()
    session.refresh_from_db()
    assert result.line.resolution_status == "APPROVED"
    assert result.line.reconciliation_transaction_id == result.transaction.pk
    assert session.reconciliation_status == "COMPLETED"


@pytest.mark.parametrize("length", [9, 2001])
def test_approval_explanation_length_is_enforced(objects, length):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    with pytest.raises(ValidationError) as exc_info:
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="x" * length,
        )
    assert exc_info.value.code == INVALID_APPROVAL_EXPLANATION


@pytest.mark.parametrize("length", [10, 2000])
def test_approval_explanation_boundary_and_trim(objects, length):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    result = approve_quantity_discrepancy(
        actor=objects["approver"], session_id=session.pk, line_id=line.pk,
        operation_id=uuid.uuid4(), explanation="  " + ("x" * length) + "  ",
    )
    assert result.line.approval_explanation == "x" * length


def test_counter_cannot_self_approve(objects):
    objects["counter"].user_permissions.add(
        Permission.objects.get(
            content_type__app_label="counting", codename="decide_discrepancy"
        )
    )
    objects["counter"] = User.objects.get(pk=objects["counter"].pk)
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    with pytest.raises(ValidationError) as exc_info:
        approve_quantity_discrepancy(
            actor=objects["counter"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="Yeterli bir açıklama.",
        )
    assert exc_info.value.code == SELF_APPROVAL


def test_drift_aborts_all_effects(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("5.000"), counted=Decimal("7.000")
    )
    receive_quantity(
        actor=objects["approver"], operation_id=uuid.uuid4(),
        material_id=objects["material"].pk, unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["location"].pk, quantity=Decimal("1.000"),
    )
    before = InventoryTransaction.objects.count()
    with pytest.raises(ValidationError) as exc_info:
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="Drift kontrol açıklaması.",
        )
    assert exc_info.value.code == "inventory.count_reconciliation_drift"
    assert InventoryTransaction.objects.count() == before
    assert StockBalance.objects.get(material=objects["material"]).quantity == Decimal("6.000")


def test_current_below_snapshot_also_refuses_as_drift(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("5.000"), counted=Decimal("3.000")
    )
    StockBalance.objects.filter(material=objects["material"]).update(
        quantity=Decimal("4.000")
    )
    with pytest.raises(ValidationError) as exc_info:
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="Aşağı drift yeniden sayım gerektirir.",
        )
    assert exc_info.value.code == "inventory.count_reconciliation_drift"
    assert not InventoryTransaction.objects.filter(transaction_type="COUNT_RECONCILIATION").exists()


def test_rejection_reopens_without_inventory_effect_and_preserves_snapshot(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("5.000"), counted=Decimal("7.000")
    )
    before = (InventoryTransaction.objects.count(), StockBalance.objects.get().quantity)
    reject_quantity_discrepancy(
        actor=objects["approver"], session_id=session.pk, line_id=line.pk,
        reason="Araştırma için yeniden sayılacak.",
    )
    session.refresh_from_db(); line.refresh_from_db()
    assert session.status == "STARTED"
    assert session.reconciliation_status == "NOT_STARTED"
    assert session.completed_at is None
    assert line.expected_quantity == Decimal("5.000")
    assert line.counted_quantity == Decimal("7.000")
    assert PhysicalCountQuantityRejection.objects.filter(line=line).count() == 1
    assert (InventoryTransaction.objects.count(), StockBalance.objects.get().quantity) == before
    record_quantity_count(
        actor=objects["counter"], session_id=session.pk, line_id=line.pk,
        counted_quantity=Decimal("6.000"), expected_counted_at=line.counted_at,
    )


def test_baseline_candidate_cannot_reconcile(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000"), baseline=True
    )
    with pytest.raises(ValidationError) as exc_info:
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="Baseline mutabakatı yasaktır.",
        )
    assert exc_info.value.code == INVALID_RECONCILIATION


def test_exact_match_pending_count_and_not_counted_are_ineligible(objects):
    receive_quantity(
        actor=objects["approver"], operation_id=uuid.uuid4(),
        material_id=objects["material"].pk, unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["location"].pk, quantity=Decimal("1.000"),
    )
    session = create_physical_count_session(
        actor=objects["counter"], reference_number=f"RC-{uuid.uuid4().hex}",
        scope_location_id=objects["root"].pk,
    )
    session = start_physical_count_session(actor=objects["counter"], session_id=session.pk).session
    line = session.quantity_lines.get()
    with pytest.raises(ValidationError) as pending_error:
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="Pending satır onaylanamaz.",
        )
    assert pending_error.value.code == INVALID_RECONCILIATION

    line = record_quantity_count(
        actor=objects["counter"], session_id=session.pk, line_id=line.pk,
        counted_quantity=Decimal("1.000"),
    )
    complete_physical_count(actor=objects["counter"], session_id=session.pk)
    with pytest.raises(ValidationError) as exact_error:
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="Eşit satır ledger oluşturamaz.",
        )
    assert exact_error.value.code == INVALID_RECONCILIATION

    second = create_physical_count_session(
        actor=objects["counter"], reference_number=f"RC-{uuid.uuid4().hex}",
        scope_location_id=objects["root"].pk,
    )
    second = start_physical_count_session(actor=objects["counter"], session_id=second.pk).session
    second_line = second.quantity_lines.get()
    mark_quantity_line_not_counted(
        actor=objects["counter"], session_id=second.pk, line_id=second_line.pk
    )
    complete_physical_count(actor=objects["counter"], session_id=second.pk)
    with pytest.raises(ValidationError) as not_counted_error:
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=second.pk, line_id=second_line.pk,
            operation_id=uuid.uuid4(), explanation="Sayılmayan satır onaylanamaz.",
        )
    assert not_counted_error.value.code == INVALID_RECONCILIATION


def test_same_operation_replays_and_different_operation_cannot_double_apply(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    operation_id = uuid.uuid4()
    first = approve_quantity_discrepancy(
        actor=objects["approver"], session_id=session.pk, line_id=line.pk,
        operation_id=operation_id, explanation="Aynı payload güvenli retry.",
    )
    replay = approve_quantity_discrepancy(
        actor=objects["approver"], session_id=session.pk, line_id=line.pk,
        operation_id=operation_id, explanation="Aynı payload güvenli retry.",
    )
    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert InventoryTransaction.objects.filter(transaction_type="COUNT_RECONCILIATION").count() == 1
    with pytest.raises(ValidationError):
        approve_quantity_discrepancy(
            actor=objects["approver"], session_id=session.pk, line_id=line.pk,
            operation_id=uuid.uuid4(), explanation="Farklı operation reddedilir.",
        )


def test_same_operation_different_line_payload_conflicts(objects):
    first_session, first_line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    operation_id = uuid.uuid4()
    first = approve_quantity_discrepancy(
        actor=objects["approver"],
        session_id=first_session.pk,
        line_id=first_line.pk,
        operation_id=operation_id,
        explanation="İlk semantik payload uygulanır.",
    )
    second_session = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"RC-{uuid.uuid4().hex}",
        scope_location_id=objects["root"].pk,
    )
    second_session = start_physical_count_session(
        actor=objects["counter"], session_id=second_session.pk
    ).session
    second_line = record_quantity_count(
        actor=objects["counter"],
        session_id=second_session.pk,
        line_id=second_session.quantity_lines.get().pk,
        counted_quantity=Decimal("3.000"),
    )
    complete_physical_count(actor=objects["counter"], session_id=second_session.pk)
    with pytest.raises(ValidationError) as exc_info:
        approve_quantity_discrepancy(
            actor=objects["approver"],
            session_id=second_session.pk,
            line_id=second_line.pk,
            operation_id=operation_id,
            explanation="Farklı semantik payload çakışmalıdır.",
        )
    assert exc_info.value.code == "inventory.operation_conflict"
    assert InventoryTransaction.objects.filter(
        transaction_type="COUNT_RECONCILIATION"
    ).count() == 1
    assert first.replayed is False


def test_approved_basis_and_result_link_are_database_immutable(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    result = approve_quantity_discrepancy(
        actor=objects["approver"], session_id=session.pk, line_id=line.pk,
        operation_id=uuid.uuid4(), explanation="Tarihsel temel korunmalıdır.",
    )
    with pytest.raises(DatabaseError, match="approved count reconciliation basis is immutable"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
                counted_quantity=Decimal("3.000")
            )
    assert InventoryTransactionLine.objects.filter(transaction=result.transaction).count() == 1


def test_database_rejects_malformed_or_unowned_count_reconciliation(objects):
    with pytest.raises(IntegrityError, match="requires exactly one approved count owner"):
        with transaction.atomic():
            InventoryTransaction.objects.create(
                operation_id=uuid.uuid4(),
                request_fingerprint="a" * 64,
                transaction_type="COUNT_RECONCILIATION",
                acting_user=objects["approver"],
                occurred_at=timezone.now(),
            )
            with transaction.get_connection().cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    with pytest.raises(IntegrityError, match="counting_qline_approval_shape"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
                resolution_status="APPROVED"
            )


def test_database_rejects_count_reconciliation_with_two_directions(objects):
    with pytest.raises(IntegrityError, match="requires one quantity direction"):
        with transaction.atomic():
            header = InventoryTransaction.objects.create(
                operation_id=uuid.uuid4(),
                request_fingerprint="b" * 64,
                transaction_type="COUNT_RECONCILIATION",
                acting_user=objects["approver"],
                occurred_at=timezone.now(),
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=objects["material"],
                quantity=Decimal("1.000"),
                unit=objects["unit"],
                condition=objects["condition"],
                source_location=objects["location"],
                target_location=objects["root"],
            )


def test_started_or_draft_session_cannot_be_reconciled(objects):
    receive_quantity(
        actor=objects["approver"],
        operation_id=uuid.uuid4(),
        material_id=objects["material"].pk,
        unit_id=objects["unit"].pk,
        condition_id=objects["condition"].pk,
        target_location_id=objects["location"].pk,
        quantity=Decimal("1.000"),
    )
    draft = create_physical_count_session(
        actor=objects["counter"],
        reference_number=f"RC-{uuid.uuid4().hex}",
        scope_location_id=objects["root"].pk,
    )
    with pytest.raises(ValidationError) as draft_error:
        approve_quantity_discrepancy(
            actor=objects["approver"],
            session_id=draft.pk,
            line_id=uuid.uuid4(),
            operation_id=uuid.uuid4(),
            explanation="Taslak oturum onaylanamaz.",
        )
    assert draft_error.value.code == INVALID_LINE

    session = start_physical_count_session(
        actor=objects["counter"], session_id=draft.pk
    ).session
    line = record_quantity_count(
        actor=objects["counter"],
        session_id=session.pk,
        line_id=session.quantity_lines.get().pk,
        counted_quantity=Decimal("2.000"),
    )
    with pytest.raises(ValidationError) as started_error:
        approve_quantity_discrepancy(
            actor=objects["approver"],
            session_id=session.pk,
            line_id=line.pk,
            operation_id=uuid.uuid4(),
            explanation="Başlamış oturum henüz mutabakat değildir.",
        )
    assert started_error.value.code == INVALID_RECONCILIATION
    assert not InventoryTransaction.objects.filter(
        transaction_type="COUNT_RECONCILIATION"
    ).exists()


@pytest.mark.parametrize(
    ("status", "reconciliation_status"),
    [
        ("DRAFT", "PENDING"),
        ("DRAFT", "COMPLETED"),
        ("STARTED", "PENDING"),
        ("STARTED", "COMPLETED"),
        ("COMPLETED", "NOT_STARTED"),
    ],
)
def test_impossible_session_reconciliation_correlation_is_rejected(
    objects, status, reconciliation_status
):
    session = PhysicalCountSession.objects.create(
        reference_number=f"RC-{uuid.uuid4().hex}",
        scope_location=objects["root"],
    )
    if status != "DRAFT":
        PhysicalCountSession.objects.filter(pk=session.pk).update(
            status="STARTED",
            started_by_user=objects["counter"],
            started_at=timezone.now(),
        )
    if status == "COMPLETED":
        PhysicalCountSession.objects.filter(pk=session.pk).update(
            status="COMPLETED",
            reconciliation_status="PENDING",
            completed_by_user=objects["counter"],
            completed_at=timezone.now(),
        )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PhysicalCountSession.objects.filter(pk=session.pk).update(
                reconciliation_status=reconciliation_status
            )


def test_absent_balance_is_treated_as_zero_current_stock(objects):
    assert not StockBalance.objects.filter(material=objects["material"]).exists()
    session, line = _completed_discrepancy(
        objects, expected=Decimal("0.000"), counted=Decimal("3.000")
    )
    result = approve_quantity_discrepancy(
        actor=objects["approver"],
        session_id=session.pk,
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        explanation="Eksik bakiye sıfır kabul edilir.",
    )
    assert result.transaction.lines.get().target_location_id == objects["location"].pk
    assert result.transaction.lines.get().source_location_id is None
    assert StockBalance.objects.get(material=objects["material"]).quantity == Decimal(
        "3.000"
    )
    assert verify_quantity_projection() == ()


def test_unauthorized_actor_cannot_decide_discrepancy(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    with pytest.raises(PermissionDenied):
        approve_quantity_discrepancy(
            actor=objects["counter"],
            session_id=session.pk,
            line_id=line.pk,
            operation_id=uuid.uuid4(),
            explanation="Yetkisiz onay denemesi burada.",
        )
    with pytest.raises(PermissionDenied):
        reject_quantity_discrepancy(
            actor=objects["counter"],
            session_id=session.pk,
            line_id=line.pk,
        )
    assert not InventoryTransaction.objects.filter(
        transaction_type="COUNT_RECONCILIATION"
    ).exists()


def test_decide_permission_is_sensitive_and_admin_manager_only():
    from accounts.roles import ADMIN_MANAGER, DEFAULT_ROLE_TEMPLATES, SAFE_CATALOG_PERMISSION_LABELS, STOREKEEPER, TECHNICIAN

    assert "counting.decide_discrepancy" not in SAFE_CATALOG_PERMISSION_LABELS
    assert "decide_discrepancy" in DEFAULT_ROLE_TEMPLATES[ADMIN_MANAGER]
    assert "decide_discrepancy" not in DEFAULT_ROLE_TEMPLATES[TECHNICIAN]
    assert "decide_discrepancy" not in DEFAULT_ROLE_TEMPLATES[STOREKEEPER]


def test_approved_snapshot_actor_time_bucket_and_link_are_frozen(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    result = approve_quantity_discrepancy(
        actor=objects["approver"],
        session_id=session.pk,
        line_id=line.pk,
        operation_id=uuid.uuid4(),
        explanation="Onay sonrası tarihsel temel kilitlenir.",
    )
    forbidden_updates = (
        {"expected_quantity": Decimal("9.000")},
        {"counted_quantity": Decimal("8.000")},
        {"counted_by_user": objects["approver"]},
        {"counted_at": timezone.now()},
        {"location": objects["root"]},
    )
    for updates in forbidden_updates:
        with pytest.raises(
            DatabaseError, match="approved count reconciliation basis is immutable"
        ):
            with transaction.atomic():
                PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(**updates)
    with pytest.raises(
        DatabaseError, match="approved count reconciliation basis is immutable"
    ):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE counting_physicalcountquantityline
                    SET reconciliation_transaction_id = NULL
                    WHERE id = %s
                    """,
                    [line.pk],
                )
    line.refresh_from_db()
    assert line.expected_quantity == Decimal("1.000")
    assert line.counted_quantity == Decimal("2.000")
    assert line.reconciliation_transaction_id == result.transaction.pk


def test_rejection_history_cannot_be_rewritten(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    reject_quantity_discrepancy(
        actor=objects["approver"],
        session_id=session.pk,
        line_id=line.pk,
        reason="Yeniden sayım için kayıt korunur.",
    )
    rejection = PhysicalCountQuantityRejection.objects.get(line=line)
    with pytest.raises(DatabaseError, match="count rejection history is immutable"):
        with transaction.atomic():
            PhysicalCountQuantityRejection.objects.filter(pk=rejection.pk).update(
                reason="değiştirilemez"
            )
    with pytest.raises(DatabaseError, match="count rejection history is immutable"):
        with transaction.atomic():
            rejection.delete()
    line.refresh_from_db()
    assert line.expected_quantity == Decimal("1.000")
    assert line.counted_quantity == Decimal("2.000")


def test_raw_self_approval_row_is_rejected(objects):
    session, line = _completed_discrepancy(
        objects, expected=Decimal("1.000"), counted=Decimal("2.000")
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            header = InventoryTransaction.objects.create(
                operation_id=uuid.uuid4(),
                request_fingerprint="c" * 64,
                transaction_type="COUNT_RECONCILIATION",
                acting_user=objects["counter"],
                occurred_at=timezone.now(),
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=objects["material"],
                quantity=Decimal("1.000"),
                unit=objects["unit"],
                condition=objects["condition"],
                target_location=objects["location"],
            )
            PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
                resolution_status="APPROVED",
                approved_by_user=objects["counter"],
                approved_at=timezone.now(),
                approval_explanation="Sayaç kendi farkını onaylayamaz.",
                reconciliation_transaction=header,
            )
