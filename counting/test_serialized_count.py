import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.query import QuerySet
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountSerializedLine,
    PhysicalCountSession,
)
from counting.services import (
    AUTHORITATIVE_ASSET_EXISTS,
    COUNT_CONFLICT,
    DUPLICATE_SERIALIZED_IDENTITY,
    INCOMPLETE_COUNT,
    INVALID_LINE,
    INVALID_SCOPE,
    add_candidate_serialized_count,
    approve_quantity_discrepancy,
    complete_physical_count,
    create_physical_count_session,
    mark_serialized_asset_missing,
    mark_serialized_line_not_counted,
    record_quantity_count,
    record_serialized_asset_count,
    start_physical_count_session,
)
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.services.receipts import receive_quantity, receive_serialized
from locations.models import Location


pytestmark = pytest.mark.django_db


@pytest.fixture
def serialized_count_objects():
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"sc-{suffix}")
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename="receive_stock",
        )
    )
    user = User.objects.get(pk=user.pk)
    category = Category.objects.create(code=f"SC-CAT-{suffix}", name="Kategori")
    unit = UnitOfMeasure.objects.create(code=f"SC-U-{suffix}", name="Adet")
    quantity_material = Material.objects.create(
        material_code=f"SC-Q-{suffix}",
        name="Miktar malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material = Material.objects.create(
        material_code=f"SC-S-{suffix}",
        name="Tekil malzeme",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    other_material = Material.objects.create(
        material_code=f"SC-S2-{suffix}",
        name="İkinci tekil malzeme",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"SC-C-{suffix}", name="Yeni", sort_order=930
    )
    other_condition = MaterialCondition.objects.create(
        code=f"SC-C2-{suffix}", name="Kullanılmış", sort_order=931
    )
    root = Location.objects.create(code=f"SC-R-{suffix}", name="Kök")
    child = Location.objects.create(
        code=f"SC-L-{suffix}",
        name="Raf",
        parent=root,
        can_hold_stock=True,
    )
    grandchild = Location.objects.create(
        code=f"SC-G-{suffix}",
        name="Alt raf",
        parent=child,
        can_hold_stock=True,
    )
    sibling_root = Location.objects.create(code=f"SC-SR-{suffix}", name="Diğer kök")
    sibling = Location.objects.create(
        code=f"SC-SL-{suffix}",
        name="Diğer raf",
        parent=sibling_root,
        can_hold_stock=True,
    )
    return {
        "user": user,
        "category": category,
        "unit": unit,
        "quantity_material": quantity_material,
        "material": material,
        "other_material": other_material,
        "condition": condition,
        "other_condition": other_condition,
        "root": root,
        "child": child,
        "grandchild": grandchild,
        "sibling_root": sibling_root,
        "sibling": sibling,
    }


def inventory_fingerprint():
    return {
        "transactions": list(
            InventoryTransaction.objects.order_by("pk").values_list(
                "pk", "transaction_type", "operation_id"
            )
        ),
        "lines": list(
            InventoryTransactionLine.objects.order_by("pk").values_list(
                "pk", "serialized_asset_id", "quantity"
            )
        ),
        "balances": list(
            StockBalance.objects.order_by("pk").values_list("pk", "quantity")
        ),
        "assets": list(
            SerializedAsset.objects.order_by("pk").values_list(
                "pk",
                "material_id",
                "internal_asset_code",
                "serial_number",
                "current_location_id",
                "current_condition_id",
                "current_state",
            )
        ),
    }


def receive_asset(objects, *, location=None, condition=None, material=None, **overrides):
    values = {
        "actor": objects["user"],
        "operation_id": uuid.uuid4(),
        "material_id": (material or objects["material"]).pk,
        "internal_asset_code": f"SC-A-{uuid.uuid4().hex[:10]}",
        "serial_number": f"SC-S-{uuid.uuid4().hex[:10]}",
        "condition_id": (condition or objects["condition"]).pk,
        "target_location_id": (location or objects["child"]).pk,
    }
    values.update(overrides)
    return receive_serialized(**values).serialized_asset


def draft(objects, *, scope=None, baseline_candidate=False):
    return create_physical_count_session(
        actor=objects["user"],
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location_id=(scope or objects["root"]).pk,
        baseline_candidate=baseline_candidate,
    )


def start(objects, **kwargs):
    session = draft(objects, **kwargs)
    return start_physical_count_session(
        actor=objects["user"], session_id=session.pk
    )


def test_start_snapshots_in_scope_serialized_asset(serialized_count_objects):
    asset = receive_asset(serialized_count_objects)
    before = inventory_fingerprint()
    audit_before = AuditEvent.objects.count()

    result = start(serialized_count_objects)

    assert len(result.serialized_lines) == 1
    line = result.serialized_lines[0]
    assert line.serialized_asset_id == asset.pk
    assert line.material_id == asset.material_id
    assert line.internal_asset_code == asset.internal_asset_code
    assert line.serial_number == asset.serial_number
    assert line.expected_present is True
    assert line.expected_location_id == asset.current_location_id
    assert line.expected_condition_id == asset.current_condition_id
    assert line.observed_present is None
    assert line.counted_by_user_id is None
    assert line.counted_at is None
    assert line.resolution_status == "PENDING_COUNT"
    assert inventory_fingerprint() == before
    assert AuditEvent.objects.count() == audit_before
    assert SerializedAsset.objects.get(pk=asset.pk).current_location_id == asset.current_location_id


def test_start_includes_descendant_and_excludes_outside_subtree(serialized_count_objects):
    in_scope = receive_asset(
        serialized_count_objects, location=serialized_count_objects["grandchild"]
    )
    receive_asset(serialized_count_objects, location=serialized_count_objects["sibling"])

    result = start(serialized_count_objects)

    assert {line.serialized_asset_id for line in result.serialized_lines} == {in_scope.pk}


def test_start_captures_quantity_and_serialized_together(serialized_count_objects):
    receive_quantity(
        actor=serialized_count_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=serialized_count_objects["quantity_material"].pk,
        unit_id=serialized_count_objects["unit"].pk,
        condition_id=serialized_count_objects["condition"].pk,
        target_location_id=serialized_count_objects["child"].pk,
        quantity=Decimal("4.000"),
    )
    asset = receive_asset(serialized_count_objects)

    result = start(serialized_count_objects)

    assert result.session.status == PhysicalCountSession.Status.STARTED
    assert len(result.quantity_lines) == 1
    assert result.quantity_lines[0].expected_quantity == Decimal("4.000")
    assert len(result.serialized_lines) == 1
    assert result.serialized_lines[0].serialized_asset_id == asset.pk


def test_serialized_snapshot_failure_rolls_back_quantity_snapshot_too(
    serialized_count_objects,
):
    receive_quantity(
        actor=serialized_count_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=serialized_count_objects["quantity_material"].pk,
        unit_id=serialized_count_objects["unit"].pk,
        condition_id=serialized_count_objects["condition"].pk,
        target_location_id=serialized_count_objects["child"].pk,
        quantity=Decimal("1.000"),
    )
    receive_asset(serialized_count_objects)
    session = draft(serialized_count_objects)
    original = QuerySet.bulk_create
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("forced serialized snapshot failure")
        return original(self, *args, **kwargs)

    with patch("django.db.models.query.QuerySet.bulk_create", new=flaky):
        with pytest.raises(RuntimeError, match="forced serialized snapshot failure"):
            start_physical_count_session(
                actor=serialized_count_objects["user"], session_id=session.pk
            )

    session.refresh_from_db()
    assert session.status == PhysicalCountSession.Status.DRAFT
    assert session.started_at is None
    assert session.quantity_lines.count() == 0
    assert session.serialized_lines.count() == 0


def test_snapshot_fields_cannot_be_rewritten(serialized_count_objects):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    line = result.serialized_lines[0]

    with pytest.raises(DatabaseError, match="snapshot is immutable|identity must match"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.filter(pk=line.pk).update(
                internal_asset_code="REWRITTEN"
            )

    with pytest.raises(DatabaseError, match="serialized count snapshot is immutable"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.filter(pk=line.pk).update(
                expected_location_id=serialized_count_objects["grandchild"].pk
            )

    with connection.cursor() as cursor:
        with pytest.raises(DatabaseError, match="serialized count snapshot is immutable"):
            with transaction.atomic():
                cursor.execute(
                    """
                    UPDATE counting_physicalcountserializedline
                    SET expected_location_id = %s
                    WHERE id = %s
                    """,
                    [serialized_count_objects["grandchild"].pk, line.pk],
                )

    line.refresh_from_db()
    assert line.internal_asset_code == asset.internal_asset_code
    assert line.expected_location_id == asset.current_location_id


def test_expected_asset_found_matching_is_no_discrepancy(serialized_count_objects):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    before = inventory_fingerprint()

    line = record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=asset.current_condition_id,
    )

    assert line.resolution_status == "NO_DISCREPANCY"
    assert line.observed_present is True
    assert line.counted_by_user_id == serialized_count_objects["user"].pk
    assert line.counted_at is not None
    assert inventory_fingerprint() == before


def test_expected_asset_wrong_location_or_condition_is_pending(
    serialized_count_objects,
):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)

    wrong_location = record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=serialized_count_objects["grandchild"].pk,
        observed_condition_id=asset.current_condition_id,
    )
    assert wrong_location.resolution_status == "PENDING_APPROVAL"

    wrong_condition = record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=serialized_count_objects["other_condition"].pk,
        expected_counted_at=wrong_location.counted_at,
    )
    assert wrong_condition.resolution_status == "PENDING_APPROVAL"


def test_explicit_missing_is_pending_and_untouched_stays_pending_count(
    serialized_count_objects,
):
    missing = receive_asset(serialized_count_objects)
    untouched = receive_asset(
        serialized_count_objects,
        location=serialized_count_objects["grandchild"],
    )
    result = start(serialized_count_objects)
    missing_line = result.session.serialized_lines.get(serialized_asset=missing)
    untouched_line = result.session.serialized_lines.get(serialized_asset=untouched)

    marked = mark_serialized_asset_missing(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        line_id=missing_line.pk,
    )

    untouched_line.refresh_from_db()
    assert marked.observed_present is False
    assert marked.resolution_status == "PENDING_APPROVAL"
    assert marked.counted_by_user_id == serialized_count_objects["user"].pk
    assert untouched_line.resolution_status == "PENDING_COUNT"
    assert untouched_line.observed_present is None
    assert untouched_line.counted_by_user_id is None


def test_duplicate_observation_requires_cas_token(serialized_count_objects):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    first = record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=asset.current_condition_id,
    )

    with pytest.raises(ValidationError) as exc_info:
        record_serialized_asset_count(
            actor=serialized_count_objects["user"],
            session_id=result.session.pk,
            serialized_asset_id=asset.pk,
            observed_location_id=asset.current_location_id,
            observed_condition_id=asset.current_condition_id,
        )

    assert exc_info.value.code == COUNT_CONFLICT
    first.refresh_from_db()
    assert first.resolution_status == "NO_DISCREPANCY"


def test_routine_not_counted_preserves_actor_and_null_presence(
    serialized_count_objects,
):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    line = result.session.serialized_lines.get()

    marked = mark_serialized_line_not_counted(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        line_id=line.pk,
    )

    assert marked.resolution_status == "NOT_COUNTED"
    assert marked.observed_present is None
    assert marked.counted_by_user_id == serialized_count_objects["user"].pk
    assert marked.counted_at is not None
    assert marked.serialized_asset_id == asset.pk


def test_unexpected_and_candidate_cannot_be_marked_not_counted(
    serialized_count_objects,
):
    result = start(serialized_count_objects)
    outside = receive_asset(
        serialized_count_objects, location=serialized_count_objects["sibling"]
    )
    unexpected = record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=outside.pk,
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )
    candidate = add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["material"].pk,
        internal_asset_code="  CAND-1  ",
        serial_number="  ",
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )

    for line in (unexpected, candidate):
        with pytest.raises(ValidationError) as exc_info:
            mark_serialized_line_not_counted(
                actor=serialized_count_objects["user"],
                session_id=result.session.pk,
                line_id=line.pk,
            )
        assert exc_info.value.code == INVALID_LINE


def test_unexpected_existing_asset_is_pending_without_mutating_projection(
    serialized_count_objects,
):
    result = start(serialized_count_objects)
    asset = receive_asset(
        serialized_count_objects, location=serialized_count_objects["sibling"]
    )
    before = inventory_fingerprint()

    line = record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )

    asset.refresh_from_db()
    assert line.expected_present is False
    assert line.serialized_asset_id == asset.pk
    assert line.observed_present is True
    assert line.resolution_status == "PENDING_APPROVAL"
    assert asset.current_location_id == serialized_count_objects["sibling"].pk
    assert inventory_fingerprint() == before


def test_candidate_normalizes_identity_and_does_not_create_asset(
    serialized_count_objects,
):
    result = start(serialized_count_objects)
    before = inventory_fingerprint()

    line = add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["material"].pk,
        internal_asset_code="  AS-1  ",
        serial_number="   ",
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )
    other_case = add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["material"].pk,
        internal_asset_code="as-1",
        serial_number=None,
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )

    assert line.internal_asset_code == "AS-1"
    assert line.serial_number is None
    assert line.serialized_asset_id is None
    assert line.expected_present is False
    assert line.observed_present is True
    assert line.resolution_status == "PENDING_APPROVAL"
    assert other_case.internal_asset_code == "as-1"
    assert inventory_fingerprint() == before
    assert SerializedAsset.objects.filter(internal_asset_code="AS-1").exists() is False


def test_candidate_serial_uniqueness_is_per_material_and_session(
    serialized_count_objects,
):
    result = start(serialized_count_objects)
    add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["material"].pk,
        internal_asset_code="CAND-SER-1",
        serial_number="MFG-1",
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )

    with pytest.raises(ValidationError) as exc_info:
        add_candidate_serialized_count(
            actor=serialized_count_objects["user"],
            session_id=result.session.pk,
            material_id=serialized_count_objects["material"].pk,
            internal_asset_code="CAND-SER-2",
            serial_number="MFG-1",
            observed_location_id=serialized_count_objects["child"].pk,
            observed_condition_id=serialized_count_objects["condition"].pk,
        )
    assert exc_info.value.code == DUPLICATE_SERIALIZED_IDENTITY

    other_material = add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["other_material"].pk,
        internal_asset_code="CAND-SER-3",
        serial_number="MFG-1",
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )
    assert other_material.serial_number == "MFG-1"


def test_candidate_internal_code_cannot_duplicate_in_session(
    serialized_count_objects,
):
    result = start(serialized_count_objects)
    add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["material"].pk,
        internal_asset_code="DUP-CODE",
        serial_number=None,
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )

    with pytest.raises(ValidationError) as exc_info:
        add_candidate_serialized_count(
            actor=serialized_count_objects["user"],
            session_id=result.session.pk,
            material_id=serialized_count_objects["other_material"].pk,
            internal_asset_code="DUP-CODE",
            serial_number=None,
            observed_location_id=serialized_count_objects["child"].pk,
            observed_condition_id=serialized_count_objects["condition"].pk,
        )
    assert exc_info.value.code == DUPLICATE_SERIALIZED_IDENTITY


def test_candidate_collision_with_authoritative_code_is_rejected(
    serialized_count_objects,
):
    asset = receive_asset(
        serialized_count_objects,
        location=serialized_count_objects["sibling"],
        internal_asset_code="LIVE-ASSET",
    )
    result = start(serialized_count_objects)
    before = inventory_fingerprint()

    with pytest.raises(ValidationError) as exc_info:
        add_candidate_serialized_count(
            actor=serialized_count_objects["user"],
            session_id=result.session.pk,
            material_id=serialized_count_objects["material"].pk,
            internal_asset_code="LIVE-ASSET",
            serial_number=None,
            observed_location_id=serialized_count_objects["child"].pk,
            observed_condition_id=serialized_count_objects["condition"].pk,
        )

    assert exc_info.value.code == AUTHORITATIVE_ASSET_EXISTS
    assert not PhysicalCountSerializedLine.objects.filter(
        session=result.session, internal_asset_code="LIVE-ASSET", expected_present=False
    ).exists()
    assert inventory_fingerprint() == before
    assert SerializedAsset.objects.filter(pk=asset.pk).count() == 1


def test_out_of_scope_observation_is_rejected(serialized_count_objects):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)

    with pytest.raises(ValidationError) as exc_info:
        record_serialized_asset_count(
            actor=serialized_count_objects["user"],
            session_id=result.session.pk,
            serialized_asset_id=asset.pk,
            observed_location_id=serialized_count_objects["sibling"].pk,
            observed_condition_id=asset.current_condition_id,
        )
    assert exc_info.value.code == INVALID_SCOPE


def test_mixed_completion_blocks_on_either_pending_count(serialized_count_objects):
    receive_quantity(
        actor=serialized_count_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=serialized_count_objects["quantity_material"].pk,
        unit_id=serialized_count_objects["unit"].pk,
        condition_id=serialized_count_objects["condition"].pk,
        target_location_id=serialized_count_objects["child"].pk,
        quantity=Decimal("1.000"),
    )
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)

    with pytest.raises(ValidationError) as exc_info:
        complete_physical_count(
            actor=serialized_count_objects["user"], session_id=result.session.pk
        )
    assert exc_info.value.code == INCOMPLETE_COUNT

    record_quantity_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        line_id=result.quantity_lines[0].pk,
        counted_quantity=Decimal("1.000"),
    )
    with pytest.raises(ValidationError) as exc_info:
        complete_physical_count(
            actor=serialized_count_objects["user"], session_id=result.session.pk
        )
    assert exc_info.value.code == INCOMPLETE_COUNT

    record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=asset.current_condition_id,
    )
    completed = complete_physical_count(
        actor=serialized_count_objects["user"], session_id=result.session.pk
    )
    assert completed.reconciliation_status == "COMPLETED"


def test_routine_serialized_not_counted_can_complete_and_baseline_cannot(
    serialized_count_objects,
):
    receive_asset(serialized_count_objects)
    routine = start(serialized_count_objects)
    mark_serialized_line_not_counted(
        actor=serialized_count_objects["user"],
        session_id=routine.session.pk,
        line_id=routine.serialized_lines[0].pk,
    )
    completed = complete_physical_count(
        actor=serialized_count_objects["user"], session_id=routine.session.pk
    )
    assert completed.status == "COMPLETED"
    assert completed.reconciliation_status == "COMPLETED"

    receive_asset(
        serialized_count_objects,
        location=serialized_count_objects["sibling"],
        internal_asset_code=f"BL-{uuid.uuid4().hex[:8]}",
    )
    baseline = start(
        serialized_count_objects,
        scope=serialized_count_objects["sibling_root"],
        baseline_candidate=True,
    )
    mark_serialized_line_not_counted(
        actor=serialized_count_objects["user"],
        session_id=baseline.session.pk,
        line_id=baseline.serialized_lines[0].pk,
    )
    with pytest.raises(ValidationError) as exc_info:
        complete_physical_count(
            actor=serialized_count_objects["user"], session_id=baseline.session.pk
        )
    assert exc_info.value.code == INCOMPLETE_COUNT


def test_explicit_missing_completes_with_pending_reconciliation(
    serialized_count_objects,
):
    receive_quantity(
        actor=serialized_count_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=serialized_count_objects["quantity_material"].pk,
        unit_id=serialized_count_objects["unit"].pk,
        condition_id=serialized_count_objects["condition"].pk,
        target_location_id=serialized_count_objects["child"].pk,
        quantity=Decimal("2.000"),
    )
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    record_quantity_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        line_id=result.quantity_lines[0].pk,
        counted_quantity=Decimal("2.000"),
    )
    mark_serialized_asset_missing(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        line_id=result.serialized_lines[0].pk,
    )
    before = inventory_fingerprint()

    completed = complete_physical_count(
        actor=serialized_count_objects["user"], session_id=result.session.pk
    )

    assert completed.reconciliation_status == "PENDING"
    assert result.session.serialized_lines.get().resolution_status == "PENDING_APPROVAL"
    assert inventory_fingerprint() == before
    assert asset.pk == SerializedAsset.objects.get(pk=asset.pk).pk
    assert not InventoryTransaction.objects.filter(
        transaction_type="COUNT_RECONCILIATION"
    ).exists()


def test_database_rejects_invalid_serialized_shapes(serialized_count_objects):
    session = PhysicalCountSession.objects.create(
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location=serialized_count_objects["root"],
    )
    asset = receive_asset(serialized_count_objects)

    with pytest.raises(IntegrityError, match="expected_shape|state_shape"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.create(
                session=session,
                serialized_asset=None,
                material=serialized_count_objects["material"],
                internal_asset_code=f"BAD-{uuid.uuid4().hex[:8]}",
                expected_present=True,
                expected_location=serialized_count_objects["child"],
                expected_condition=serialized_count_objects["condition"],
            )

    with pytest.raises(IntegrityError, match="SERIALIZED material|identity must match"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.create(
                session=session,
                serialized_asset=asset,
                material=serialized_count_objects["other_material"],
                internal_asset_code=asset.internal_asset_code,
                serial_number=asset.serial_number,
                expected_present=True,
                expected_location=asset.current_location,
                expected_condition=asset.current_condition,
            )

    started = start_physical_count_session(
        actor=serialized_count_objects["user"], session_id=session.pk
    ).session
    with pytest.raises(IntegrityError, match="state_shape"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.create(
                session=started,
                serialized_asset=None,
                material=serialized_count_objects["material"],
                internal_asset_code=f"MISS-{uuid.uuid4().hex[:8]}",
                expected_present=False,
                observed_present=True,
                counted_by_user=serialized_count_objects["user"],
                counted_at=timezone.now(),
                resolution_status="PENDING_APPROVAL",
            )


def test_completed_serialized_history_cannot_be_deleted_or_rewritten(
    serialized_count_objects,
):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=asset.pk,
        observed_location_id=asset.current_location_id,
        observed_condition_id=asset.current_condition_id,
    )
    complete_physical_count(
        actor=serialized_count_objects["user"], session_id=result.session.pk
    )
    line = result.session.serialized_lines.get()

    with pytest.raises(DatabaseError, match="snapshot is immutable|history is immutable"):
        with transaction.atomic():
            line.delete()

    with pytest.raises(DatabaseError, match="history is immutable"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.filter(pk=line.pk).update(
                resolution_status="NOT_COUNTED",
                observed_present=None,
                observed_location=None,
                observed_condition=None,
            )


def test_duplicate_session_identity_is_rejected_by_database(serialized_count_objects):
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)

    with pytest.raises(IntegrityError, match="counting_sline_session_code_uniq"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.create(
                session=result.session,
                serialized_asset=None,
                material=serialized_count_objects["other_material"],
                internal_asset_code=asset.internal_asset_code,
                expected_present=False,
                observed_present=True,
                observed_location=serialized_count_objects["child"],
                observed_condition=serialized_count_objects["condition"],
                counted_by_user=serialized_count_objects["user"],
                counted_at=timezone.now(),
                resolution_status="PENDING_APPROVAL",
            )


def test_start_does_not_snapshot_serialized_material_without_in_stock_asset(
    serialized_count_objects,
):
    result = start(serialized_count_objects)

    assert result.serialized_lines == ()
    assert result.session.serialized_lines.count() == 0
    assert SerializedAsset.objects.count() == 0


def test_candidate_nonblank_serial_is_outer_trimmed(serialized_count_objects):
    result = start(serialized_count_objects)

    line = add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["material"].pk,
        internal_asset_code="CAND-TRIM",
        serial_number="  MFG-TRIM  ",
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )

    assert line.serial_number == "MFG-TRIM"
    assert SerializedAsset.objects.count() == 0


def test_unexpected_and_candidate_rows_do_not_block_completion(
    serialized_count_objects,
):
    expected = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=expected.pk,
        observed_location_id=expected.current_location_id,
        observed_condition_id=expected.current_condition_id,
    )
    outside = receive_asset(
        serialized_count_objects,
        location=serialized_count_objects["sibling"],
        internal_asset_code=f"OUT-{uuid.uuid4().hex[:8]}",
    )
    record_serialized_asset_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        serialized_asset_id=outside.pk,
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )
    add_candidate_serialized_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        material_id=serialized_count_objects["material"].pk,
        internal_asset_code="CAND-OPEN",
        serial_number=None,
        observed_location_id=serialized_count_objects["child"].pk,
        observed_condition_id=serialized_count_objects["condition"].pk,
    )
    before = inventory_fingerprint()

    completed = complete_physical_count(
        actor=serialized_count_objects["user"], session_id=result.session.pk
    )

    assert completed.status == "COMPLETED"
    assert completed.reconciliation_status == "PENDING"
    assert inventory_fingerprint() == before
    assert not InventoryTransaction.objects.filter(
        transaction_type="COUNT_RECONCILIATION"
    ).exists()


def test_quantity_approval_keeps_reconciliation_pending_when_serialized_discrepancy_exists(
    serialized_count_objects,
):
    suffix = uuid.uuid4().hex[:8]
    approver = User.objects.create_user(username=f"sc-appr-{suffix}")
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
    receive_quantity(
        actor=serialized_count_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=serialized_count_objects["quantity_material"].pk,
        unit_id=serialized_count_objects["unit"].pk,
        condition_id=serialized_count_objects["condition"].pk,
        target_location_id=serialized_count_objects["child"].pk,
        quantity=Decimal("3.000"),
    )
    asset = receive_asset(serialized_count_objects)
    result = start(serialized_count_objects)
    quantity_line = record_quantity_count(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        line_id=result.quantity_lines[0].pk,
        counted_quantity=Decimal("4.000"),
    )
    mark_serialized_asset_missing(
        actor=serialized_count_objects["user"],
        session_id=result.session.pk,
        line_id=result.serialized_lines[0].pk,
    )
    complete_physical_count(
        actor=serialized_count_objects["user"], session_id=result.session.pk
    )
    serialized_before = inventory_fingerprint()["assets"]

    approval = approve_quantity_discrepancy(
        actor=approver,
        session_id=result.session.pk,
        line_id=quantity_line.pk,
        operation_id=uuid.uuid4(),
        explanation="Miktar farkı fiziksel olarak doğrulandı.",
    )

    approval.line.refresh_from_db()
    result.session.refresh_from_db()
    serialized_line = result.session.serialized_lines.get(serialized_asset=asset)
    assert approval.line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.APPROVED
    assert serialized_line.resolution_status == "PENDING_APPROVAL"
    assert result.session.reconciliation_status == "PENDING"
    assert inventory_fingerprint()["assets"] == serialized_before
    assert serialized_line.serialized_asset_id == asset.pk


def test_database_rejects_mismatched_asset_material_via_queryset_update(
    serialized_count_objects,
):
    session = PhysicalCountSession.objects.create(
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location=serialized_count_objects["root"],
    )
    asset = receive_asset(serialized_count_objects)
    line = PhysicalCountSerializedLine.objects.create(
        session=session,
        serialized_asset=asset,
        material=asset.material,
        internal_asset_code=asset.internal_asset_code,
        serial_number=asset.serial_number,
        expected_present=True,
        expected_location=asset.current_location,
        expected_condition=asset.current_condition,
    )

    with pytest.raises(IntegrityError, match="identity must match|SERIALIZED material"):
        with transaction.atomic():
            PhysicalCountSerializedLine.objects.filter(pk=line.pk).update(
                material_id=serialized_count_objects["other_material"].pk
            )

    line.refresh_from_db()
    assert line.material_id == asset.material_id


def test_database_rejects_malformed_presence_and_session_rewrite_via_sql(
    serialized_count_objects,
):
    asset = receive_asset(serialized_count_objects)
    started = start(serialized_count_objects).session
    started_line = started.serialized_lines.get()

    with pytest.raises(IntegrityError, match="state_shape"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE counting_physicalcountserializedline
                SET observed_present = TRUE
                WHERE id = %s
                """,
                [started_line.pk],
            )

    other_session = PhysicalCountSession.objects.create(
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location=serialized_count_objects["root"],
    )
    with pytest.raises(DatabaseError, match="snapshot is immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE counting_physicalcountserializedline
                SET session_id = %s
                WHERE id = %s
                """,
                [other_session.pk, started_line.pk],
            )

    started_line.refresh_from_db()
    assert started_line.session_id == started.pk
    assert started_line.observed_present is None
    assert started_line.serialized_asset_id == asset.pk
