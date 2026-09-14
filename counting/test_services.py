import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from accounts.models import User
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountQuantityLine, PhysicalCountSession
from counting.queries import resolve_location_subtree_ids
from counting.services import (
    COUNT_CONFLICT,
    DUPLICATE_BUCKET,
    INCOMPLETE_COUNT,
    INVALID_LINE,
    INVALID_QUANTITY,
    INVALID_SCOPE,
    INVALID_SESSION,
    OVERLAPPING_SCOPE,
    add_unexpected_quantity_count,
    complete_physical_count,
    create_physical_count_session,
    mark_quantity_line_not_counted,
    record_quantity_count,
    start_physical_count_session,
)
from inventory.models import InventoryTransaction, SerializedAsset, StockBalance
from inventory.services.receipts import receive_quantity
from locations.models import Location


pytestmark = pytest.mark.django_db


@pytest.fixture
def service_objects():
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"count-service-{suffix}")
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename="receive_stock",
        )
    )
    user = User.objects.get(pk=user.pk)
    inactive_user = User.objects.create_user(
        username=f"count-inactive-{suffix}", is_active=False
    )
    category = Category.objects.create(code=f"CS-CAT-{suffix}", name="Kategori")
    unit = UnitOfMeasure.objects.create(code=f"CS-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"CS-M-{suffix}",
        name="Miktar malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    other_material = Material.objects.create(
        material_code=f"CS-M2-{suffix}",
        name="İkinci miktar malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    serialized_material = Material.objects.create(
        material_code=f"CS-S-{suffix}",
        name="Tekil malzeme",
        category=category,
        unit=None,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"CS-C-{suffix}", name="Yeni"
    )
    other_condition = MaterialCondition.objects.create(
        code=f"CS-C2-{suffix}", name="Kullanılmış"
    )
    root = Location.objects.create(code=f"CS-R-{suffix}", name="Kök")
    child = Location.objects.create(
        code=f"CS-L-{suffix}",
        name="Raf",
        parent=root,
        can_hold_stock=True,
    )
    grandchild = Location.objects.create(
        code=f"CS-G-{suffix}",
        name="Alt raf",
        parent=child,
        can_hold_stock=True,
    )
    sibling_root = Location.objects.create(code=f"CS-SR-{suffix}", name="Diğer kök")
    sibling = Location.objects.create(
        code=f"CS-SL-{suffix}",
        name="Diğer raf",
        parent=sibling_root,
        can_hold_stock=True,
    )
    return {
        "user": user,
        "inactive_user": inactive_user,
        "category": category,
        "unit": unit,
        "material": material,
        "other_material": other_material,
        "serialized_material": serialized_material,
        "condition": condition,
        "other_condition": other_condition,
        "root": root,
        "child": child,
        "grandchild": grandchild,
        "sibling_root": sibling_root,
        "sibling": sibling,
    }


def draft(objects, *, scope=None, baseline_candidate=False):
    return PhysicalCountSession.objects.create(
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location=scope or objects["root"],
        baseline_candidate=baseline_candidate,
    )


def start(objects, *, scope=None, baseline_candidate=False):
    session = draft(objects, scope=scope, baseline_candidate=baseline_candidate)
    return start_physical_count_session(
        actor=objects["user"], session_id=session.pk
    ).session


def balance(objects, *, quantity, material=None, location=None, condition=None):
    return StockBalance.objects.create(
        material=material or objects["material"],
        location=location or objects["child"],
        condition=condition or objects["condition"],
        quantity=quantity,
    )


def test_subtree_query_includes_root_and_all_descendants_in_stable_order(
    service_objects,
):
    result = resolve_location_subtree_ids(service_objects["root"].pk)

    assert set(result) == {
        service_objects["root"].pk,
        service_objects["child"].pk,
        service_objects["grandchild"].pk,
    }
    assert result == tuple(sorted(result, key=str))


def test_create_draft_normalizes_reference_and_validates_actor_and_scope(
    service_objects,
):
    session = create_physical_count_session(
        actor=service_objects["user"],
        reference_number="  CNT-SERVICE  ",
        scope_location_id=service_objects["root"].pk,
        baseline_candidate=True,
    )

    assert session.reference_number == "CNT-SERVICE"
    assert session.status == PhysicalCountSession.Status.DRAFT
    assert session.baseline_candidate is True
    with pytest.raises(PermissionDenied):
        create_physical_count_session(
            actor=service_objects["inactive_user"],
            reference_number="CNT-INACTIVE",
            scope_location_id=service_objects["sibling_root"].pk,
        )


def test_create_draft_rejects_inactive_root(service_objects):
    Location.objects.filter(pk=service_objects["root"].pk).update(active=False)

    with pytest.raises(ValidationError) as exc_info:
        create_physical_count_session(
            actor=service_objects["user"],
            reference_number="CNT-INACTIVE-ROOT",
            scope_location_id=service_objects["root"].pk,
        )

    assert exc_info.value.code == INVALID_SCOPE


def test_create_draft_enforces_open_overlap_but_allows_sibling_scope(service_objects):
    create_physical_count_session(
        actor=service_objects["user"],
        reference_number="CNT-OPEN-ROOT",
        scope_location_id=service_objects["root"].pk,
    )

    with pytest.raises(ValidationError) as exc_info:
        create_physical_count_session(
            actor=service_objects["user"],
            reference_number="CNT-OPEN-CHILD",
            scope_location_id=service_objects["child"].pk,
        )
    assert exc_info.value.code == OVERLAPPING_SCOPE

    sibling = create_physical_count_session(
        actor=service_objects["user"],
        reference_number="CNT-OPEN-SIBLING",
        scope_location_id=service_objects["sibling_root"].pk,
    )
    assert sibling.status == PhysicalCountSession.Status.DRAFT


def test_start_captures_only_positive_quantity_balances_in_scope(service_objects):
    positive = balance(service_objects, quantity=Decimal("5.125"))
    second_condition = balance(
        service_objects,
        quantity=Decimal("2.000"),
        condition=service_objects["other_condition"],
    )
    second_location = balance(
        service_objects,
        quantity=Decimal("3.000"),
        location=service_objects["grandchild"],
    )
    balance(
        service_objects,
        quantity=Decimal("0.000"),
        material=service_objects["other_material"],
        location=service_objects["child"],
    )
    balance(
        service_objects,
        quantity=Decimal("9.000"),
        location=service_objects["sibling"],
    )
    SerializedAsset.objects.create(
        material=service_objects["serialized_material"],
        internal_asset_code=f"ASSET-{uuid.uuid4().hex[:8]}",
        current_location=service_objects["child"],
        current_condition=service_objects["condition"],
    )
    session = draft(service_objects)
    before = {
        "transactions": InventoryTransaction.objects.count(),
        "balances": StockBalance.objects.count(),
        "assets": SerializedAsset.objects.count(),
    }

    result = start_physical_count_session(
        actor=service_objects["user"], session_id=session.pk
    )
    rows = {
        (line.material_id, line.location_id, line.condition_id): line.expected_quantity
        for line in result.quantity_lines
    }

    assert result.session.status == PhysicalCountSession.Status.STARTED
    assert result.session.started_by_user_id == service_objects["user"].pk
    assert result.session.started_at is not None
    assert rows == {
        (positive.material_id, positive.location_id, positive.condition_id): Decimal(
            "5.125"
        ),
        (
            second_condition.material_id,
            second_condition.location_id,
            second_condition.condition_id,
        ): Decimal("2.000"),
        (
            second_location.material_id,
            second_location.location_id,
            second_location.condition_id,
        ): Decimal("3.000"),
    }
    assert InventoryTransaction.objects.count() == before["transactions"]
    assert StockBalance.objects.count() == before["balances"]
    assert SerializedAsset.objects.count() == before["assets"]


def test_second_start_is_cleanly_rejected(service_objects):
    session = start(service_objects)

    with pytest.raises(ValidationError) as exc_info:
        start_physical_count_session(
            actor=service_objects["user"], session_id=session.pk
        )

    assert exc_info.value.code == INVALID_SESSION


def test_start_rolls_back_snapshot_and_state_when_insert_fails(service_objects):
    balance(service_objects, quantity=Decimal("1.000"))
    session = draft(service_objects)

    with patch(
        "django.db.models.query.QuerySet.bulk_create",
        side_effect=RuntimeError("forced snapshot failure"),
    ):
        with pytest.raises(RuntimeError, match="forced snapshot failure"):
            start_physical_count_session(
                actor=service_objects["user"], session_id=session.pk
            )

    session.refresh_from_db()
    assert session.status == PhysicalCountSession.Status.DRAFT
    assert session.started_at is None
    assert session.quantity_lines.count() == 0


@pytest.mark.parametrize("other_scope_key", ["root", "child"])
def test_start_rejects_same_or_child_open_scope(service_objects, other_scope_key):
    first = draft(service_objects)
    draft(service_objects, scope=service_objects[other_scope_key])

    with pytest.raises(ValidationError) as exc_info:
        start_physical_count_session(actor=service_objects["user"], session_id=first.pk)

    assert exc_info.value.code == OVERLAPPING_SCOPE


def test_start_rejects_parent_scope_when_current_is_child(service_objects):
    child_session = draft(service_objects, scope=service_objects["child"])
    draft(service_objects, scope=service_objects["root"])

    with pytest.raises(ValidationError) as exc_info:
        start_physical_count_session(
            actor=service_objects["user"], session_id=child_session.pk
        )

    assert exc_info.value.code == OVERLAPPING_SCOPE


def test_nonoverlapping_sibling_sessions_start_and_completed_no_longer_blocks(
    service_objects,
):
    first = start(service_objects)
    second = start(service_objects, scope=service_objects["sibling_root"])
    complete_physical_count(actor=service_objects["user"], session_id=first.pk)

    replacement = draft(service_objects)
    started_replacement = start_physical_count_session(
        actor=service_objects["user"], session_id=replacement.pk
    ).session

    assert second.status == PhysicalCountSession.Status.STARTED
    assert started_replacement.status == PhysicalCountSession.Status.STARTED


@pytest.mark.parametrize(
    ("counted", "expected", "status"),
    [
        (Decimal("4.000"), Decimal("4.000"), "NO_DISCREPANCY"),
        (Decimal("6.000"), Decimal("4.000"), "PENDING_APPROVAL"),
        (Decimal("2.000"), Decimal("4.000"), "PENDING_APPROVAL"),
        (Decimal("0.000"), Decimal("4.000"), "PENDING_APPROVAL"),
    ],
)
def test_record_quantity_count_sets_metadata_and_resolution(
    service_objects, counted, expected, status
):
    balance(service_objects, quantity=expected)
    session = start(service_objects)
    line = session.quantity_lines.get()

    result = record_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=counted,
    )

    assert result.counted_quantity == counted
    assert result.counted_by_user_id == service_objects["user"].pk
    assert result.counted_at is not None
    assert result.resolution_status == status


def test_recount_requires_last_seen_counted_at(service_objects):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects)
    line = session.quantity_lines.get()
    first = record_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("1.000"),
    )

    with pytest.raises(ValidationError) as exc_info:
        record_quantity_count(
            actor=service_objects["user"],
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=Decimal("2.000"),
        )
    assert exc_info.value.code == COUNT_CONFLICT

    second = record_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("2.000"),
        expected_counted_at=first.counted_at,
    )
    assert second.counted_quantity == Decimal("2.000")
    assert second.counted_at > first.counted_at


def test_explicit_not_counted_is_persisted_and_distinct_from_untouched(
    service_objects,
):
    balance(service_objects, quantity=Decimal("1.000"))
    balance(
        service_objects,
        quantity=Decimal("2.000"),
        condition=service_objects["other_condition"],
    )
    session = start(service_objects)
    lines = list(session.quantity_lines.order_by("condition_id"))
    untouched, selected = lines
    before = (
        InventoryTransaction.objects.count(),
        StockBalance.objects.count(),
        SerializedAsset.objects.count(),
    )

    skipped = mark_quantity_line_not_counted(
        actor=service_objects["user"],
        session_id=session.pk,
        line_id=selected.pk,
    )
    untouched.refresh_from_db()

    assert untouched.resolution_status == "PENDING_COUNT"
    assert untouched.counted_quantity is None
    assert untouched.counted_by_user_id is None
    assert untouched.counted_at is None
    assert skipped.resolution_status == "NOT_COUNTED"
    assert skipped.counted_quantity is None
    assert skipped.counted_by_user_id == service_objects["user"].pk
    assert skipped.counted_at is not None
    assert (
        InventoryTransaction.objects.count(),
        StockBalance.objects.count(),
        SerializedAsset.objects.count(),
    ) == before


def test_count_entry_after_explicit_not_counted_requires_and_accepts_cas_token(
    service_objects,
):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects)
    line = session.quantity_lines.get()
    skipped = mark_quantity_line_not_counted(
        actor=service_objects["user"], session_id=session.pk, line_id=line.pk
    )

    with pytest.raises(ValidationError) as exc_info:
        record_quantity_count(
            actor=service_objects["user"],
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=Decimal("0.000"),
        )
    assert exc_info.value.code == COUNT_CONFLICT

    counted = record_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("0.000"),
        expected_counted_at=skipped.counted_at,
    )
    assert counted.counted_quantity == Decimal("0.000")
    assert counted.resolution_status == "PENDING_APPROVAL"
    assert counted.counted_at > skipped.counted_at


def test_explicit_not_counted_rejects_unexpected_zero_expected_line(service_objects):
    session = start(service_objects)
    unexpected = add_unexpected_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        material_id=service_objects["material"].pk,
        location_id=service_objects["child"].pk,
        condition_id=service_objects["condition"].pk,
        counted_quantity=Decimal("1.000"),
    )

    with pytest.raises(ValidationError) as exc_info:
        mark_quantity_line_not_counted(
            actor=service_objects["user"],
            session_id=session.pk,
            line_id=unexpected.pk,
            expected_counted_at=unexpected.counted_at,
        )

    assert exc_info.value.code == INVALID_LINE


@pytest.mark.parametrize("bad_quantity", [Decimal("-0.001"), Decimal("1.0001"), 1.0])
def test_record_quantity_rejects_invalid_or_float_values(
    service_objects, bad_quantity
):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects)
    line = session.quantity_lines.get()

    with pytest.raises(ValidationError) as exc_info:
        record_quantity_count(
            actor=service_objects["user"],
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=bad_quantity,
        )

    assert exc_info.value.code == INVALID_QUANTITY


def test_record_quantity_rejects_wrong_session_and_nonstarted_state(service_objects):
    balance(service_objects, quantity=Decimal("1.000"))
    started = start(service_objects)
    line = started.quantity_lines.get()
    unrelated = draft(service_objects, scope=service_objects["sibling_root"])

    with pytest.raises(ValidationError) as exc_info:
        record_quantity_count(
            actor=service_objects["user"],
            session_id=unrelated.pk,
            line_id=line.pk,
            counted_quantity=Decimal("1.000"),
        )
    assert exc_info.value.code == INVALID_SESSION


def test_add_unexpected_known_quantity_stock(service_objects):
    session = start(service_objects)

    line = add_unexpected_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        material_id=service_objects["material"].pk,
        location_id=service_objects["child"].pk,
        condition_id=service_objects["condition"].pk,
        counted_quantity=Decimal("3.500"),
    )

    assert line.expected_quantity == Decimal("0.000")
    assert line.counted_quantity == Decimal("3.500")
    assert line.resolution_status == "PENDING_APPROVAL"
    assert InventoryTransaction.objects.count() == 0
    assert StockBalance.objects.count() == 0


def test_add_unexpected_rejects_duplicate_expected_bucket(service_objects):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects)

    with pytest.raises(ValidationError) as exc_info:
        add_unexpected_quantity_count(
            actor=service_objects["user"],
            session_id=session.pk,
            material_id=service_objects["material"].pk,
            location_id=service_objects["child"].pk,
            condition_id=service_objects["condition"].pk,
            counted_quantity=Decimal("1.000"),
        )

    assert exc_info.value.code == DUPLICATE_BUCKET


def test_add_unexpected_rejects_serialized_and_out_of_scope(service_objects):
    session = start(service_objects)
    base = {
        "actor": service_objects["user"],
        "session_id": session.pk,
        "material_id": service_objects["material"].pk,
        "location_id": service_objects["child"].pk,
        "condition_id": service_objects["condition"].pk,
        "counted_quantity": Decimal("1.000"),
    }

    with pytest.raises(ValidationError) as serialized_error:
        add_unexpected_quantity_count(
            **{**base, "material_id": service_objects["serialized_material"].pk}
        )
    assert serialized_error.value.code == INVALID_LINE

    with pytest.raises(ValidationError) as scope_error:
        add_unexpected_quantity_count(
            **{**base, "location_id": service_objects["sibling"].pk}
        )
    assert scope_error.value.code == INVALID_SCOPE



def test_add_unexpected_accepts_explicit_zero_without_automatic_discrepancy(
    service_objects,
):
    session = start(service_objects)

    line = add_unexpected_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        material_id=service_objects["material"].pk,
        location_id=service_objects["child"].pk,
        condition_id=service_objects["condition"].pk,
        counted_quantity=Decimal("0.000"),
    )

    assert line.expected_quantity == Decimal("0.000")
    assert line.counted_quantity == Decimal("0.000")
    assert line.resolution_status == "NO_DISCREPANCY"


def test_later_inventory_movement_does_not_rewrite_unexpected_expected_zero(
    service_objects,
):
    session = start(service_objects)
    line = add_unexpected_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        material_id=service_objects["material"].pk,
        location_id=service_objects["child"].pk,
        condition_id=service_objects["condition"].pk,
        counted_quantity=Decimal("2.000"),
    )

    receive_quantity(
        actor=service_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=service_objects["material"].pk,
        unit_id=service_objects["unit"].pk,
        condition_id=service_objects["condition"].pk,
        target_location_id=service_objects["child"].pk,
        quantity=Decimal("1.000"),
    )
    line.refresh_from_db()

    assert line.expected_quantity == Decimal("0.000")
    assert line.counted_quantity == Decimal("2.000")


@pytest.mark.parametrize("baseline_candidate", [False, True])
def test_completion_rejects_missing_counts_for_routine_and_baseline(
    service_objects, baseline_candidate
):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects, baseline_candidate=baseline_candidate)

    with pytest.raises(ValidationError) as exc_info:
        complete_physical_count(actor=service_objects["user"], session_id=session.pk)

    assert exc_info.value.code == INCOMPLETE_COUNT
    session.refresh_from_db()
    assert session.status == PhysicalCountSession.Status.STARTED


def test_routine_completion_accepts_explicit_not_counted_expected_row(
    service_objects,
):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects)
    line = session.quantity_lines.get()
    mark_quantity_line_not_counted(
        actor=service_objects["user"], session_id=session.pk, line_id=line.pk
    )

    completed = complete_physical_count(
        actor=service_objects["user"], session_id=session.pk
    )

    assert completed.status == PhysicalCountSession.Status.COMPLETED
    line.refresh_from_db()
    assert line.resolution_status == "NOT_COUNTED"
    assert line.counted_quantity is None


def test_baseline_completion_rejects_explicit_not_counted_expected_row(
    service_objects,
):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects, baseline_candidate=True)
    line = session.quantity_lines.get()
    mark_quantity_line_not_counted(
        actor=service_objects["user"], session_id=session.pk, line_id=line.pk
    )

    with pytest.raises(ValidationError) as exc_info:
        complete_physical_count(actor=service_objects["user"], session_id=session.pk)

    assert exc_info.value.code == INCOMPLETE_COUNT
    session.refresh_from_db()
    assert session.status == PhysicalCountSession.Status.STARTED


def test_baseline_completion_accepts_all_required_rows_physically_counted(
    service_objects,
):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects, baseline_candidate=True)
    line = session.quantity_lines.get()
    record_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("0.000"),
    )

    completed = complete_physical_count(
        actor=service_objects["user"], session_id=session.pk
    )

    assert completed.status == PhysicalCountSession.Status.COMPLETED


def test_database_rejects_not_counted_without_explicit_actor_and_time(
    service_objects,
):
    session = draft(service_objects)

    with pytest.raises(IntegrityError, match="counting_qline_count_state_match"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.create(
                session=session,
                material=service_objects["material"],
                location=service_objects["child"],
                condition=service_objects["condition"],
                expected_quantity=Decimal("1.000"),
                resolution_status="NOT_COUNTED",
            )


def test_database_rejects_pending_count_with_action_metadata(service_objects):
    session = draft(service_objects)

    with pytest.raises(IntegrityError, match="counting_qline_count_state_match"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.create(
                session=session,
                material=service_objects["material"],
                location=service_objects["child"],
                condition=service_objects["condition"],
                expected_quantity=Decimal("1.000"),
                counted_by_user=service_objects["user"],
                counted_at=timezone.now(),
                resolution_status="PENDING_COUNT",
            )


def test_completion_sets_only_completion_metadata_and_state(service_objects):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects)
    line = session.quantity_lines.get()
    record_quantity_count(
        actor=service_objects["user"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("2.000"),
    )
    before = (InventoryTransaction.objects.count(), StockBalance.objects.get().quantity)

    completed = complete_physical_count(
        actor=service_objects["user"], session_id=session.pk
    )

    assert completed.status == PhysicalCountSession.Status.COMPLETED
    assert completed.completed_by_user_id == service_objects["user"].pk
    assert completed.completed_at is not None
    assert completed.reconciliation_status == "PENDING"
    assert (InventoryTransaction.objects.count(), StockBalance.objects.get().quantity) == before


def test_completion_of_empty_quantity_scope_is_valid(service_objects):
    session = start(service_objects)

    completed = complete_physical_count(
        actor=service_objects["user"], session_id=session.pk
    )

    assert completed.status == PhysicalCountSession.Status.COMPLETED


@pytest.mark.parametrize(
    ("old_status", "new_status"),
    [
        ("DRAFT", "COMPLETED"),
        ("STARTED", "DRAFT"),
        ("COMPLETED", "STARTED"),
    ],
)
def test_illegal_session_transitions_are_rejected_by_database(
    service_objects, old_status, new_status
):
    session = draft(service_objects)
    if old_status in {"STARTED", "COMPLETED"}:
        started_at = timezone.now()
        PhysicalCountSession.objects.filter(pk=session.pk).update(
            status="STARTED",
            started_by_user=service_objects["user"],
            started_at=started_at,
        )
    if old_status == "COMPLETED":
        PhysicalCountSession.objects.filter(pk=session.pk).update(
            status="COMPLETED",
            reconciliation_status="COMPLETED",
            completed_by_user=service_objects["user"],
            completed_at=timezone.now(),
        )

    update = {"status": new_status}
    if new_status == "COMPLETED":
        update.update(
            completed_by_user=service_objects["user"], completed_at=timezone.now()
        )
    with pytest.raises(DatabaseError, match="must be started|cannot return|cannot be reopened|reopen only"):
        with transaction.atomic():
            PhysicalCountSession.objects.filter(pk=session.pk).update(**update)


def test_start_and_completion_metadata_are_immutable(service_objects):
    session = start(service_objects)
    with pytest.raises(DatabaseError, match="start metadata is immutable"):
        with transaction.atomic():
            PhysicalCountSession.objects.filter(pk=session.pk).update(
                started_at=session.started_at + timedelta(seconds=1)
            )

    session = complete_physical_count(
        actor=service_objects["user"], session_id=session.pk
    )
    with pytest.raises(DatabaseError, match="completion metadata is immutable"):
        with transaction.atomic():
            PhysicalCountSession.objects.filter(pk=session.pk).update(
                completed_at=session.completed_at + timedelta(seconds=1)
            )


@pytest.mark.parametrize("field", ["material_id", "location_id", "condition_id"])
def test_post_start_bucket_identity_rewrite_is_rejected(service_objects, field):
    balance(service_objects, quantity=Decimal("1.000"))
    session = start(service_objects)
    line = session.quantity_lines.get()
    replacement = {
        "material_id": service_objects["other_material"].pk,
        "location_id": service_objects["grandchild"].pk,
        "condition_id": service_objects["other_condition"].pk,
    }[field]

    with pytest.raises(DatabaseError, match="expected snapshot is immutable"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
                **{field: replacement}
            )


def test_line_cannot_be_moved_into_started_session(service_objects):
    started = start(service_objects)
    other = draft(service_objects, scope=service_objects["sibling_root"])
    line = PhysicalCountQuantityLine.objects.create(
        session=other,
        material=service_objects["material"],
        location=service_objects["sibling"],
        condition=service_objects["condition"],
        expected_quantity=Decimal("0"),
    )

    with pytest.raises(DatabaseError, match="expected snapshot is immutable"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
                session_id=started.pk
            )


def test_insert_into_completed_session_is_rejected(service_objects):
    session = start(service_objects)
    complete_physical_count(actor=service_objects["user"], session_id=session.pk)

    with pytest.raises(DatabaseError, match="only zero-expected"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.create(
                session=session,
                material=service_objects["material"],
                location=service_objects["child"],
                condition=service_objects["condition"],
                expected_quantity=Decimal("0"),
            )


def test_queryset_update_to_serialized_material_is_rejected(service_objects):
    session = draft(service_objects)
    line = PhysicalCountQuantityLine.objects.create(
        session=session,
        material=service_objects["material"],
        location=service_objects["child"],
        condition=service_objects["condition"],
        expected_quantity=Decimal("0"),
    )

    with pytest.raises(IntegrityError, match="QUANTITY material"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
                material_id=service_objects["serialized_material"].pk
            )


def test_draft_line_can_be_deleted_but_started_line_is_retained(service_objects):
    session = draft(service_objects)
    line = PhysicalCountQuantityLine.objects.create(
        session=session,
        material=service_objects["material"],
        location=service_objects["child"],
        condition=service_objects["condition"],
        expected_quantity=Decimal("0"),
    )
    line.delete()
    assert not PhysicalCountQuantityLine.objects.filter(pk=line.pk).exists()

    started = start(service_objects, scope=service_objects["sibling_root"])
    retained = add_unexpected_quantity_count(
        actor=service_objects["user"],
        session_id=started.pk,
        material_id=service_objects["material"].pk,
        location_id=service_objects["sibling"].pk,
        condition_id=service_objects["condition"].pk,
        counted_quantity=Decimal("1.000"),
    )
    with pytest.raises(DatabaseError, match="expected snapshot is immutable"):
        with transaction.atomic():
            retained.delete()
