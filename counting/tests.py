import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import RestrictedError
from django.utils import timezone

from accounts.models import User
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountQuantityLine, PhysicalCountSession
from inventory.models import InventoryTransaction, SerializedAsset, StockBalance
from locations.models import Location


pytestmark = pytest.mark.django_db


@pytest.fixture
def count_objects():
    user = User.objects.create_user(
        username=f"counter-{uuid.uuid4().hex[:8]}",
        password="test-only-password",
    )
    category = Category.objects.create(
        code=f"COUNT-{uuid.uuid4().hex[:8]}",
        name="Sayım kategorisi",
    )
    unit = UnitOfMeasure.objects.create(
        code=f"COUNT-{uuid.uuid4().hex[:8]}",
        name="Sayım birimi",
    )
    quantity_material = Material.objects.create(
        material_code=f"COUNT-Q-{uuid.uuid4().hex[:8]}",
        name="Miktar malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    serialized_material = Material.objects.create(
        material_code=f"COUNT-S-{uuid.uuid4().hex[:8]}",
        name="Tekil malzeme",
        category=category,
        unit=None,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"COUNT-{uuid.uuid4().hex[:8]}",
        name="Sayım kondisyonu",
    )
    other_condition = MaterialCondition.objects.create(
        code=f"COUNT-{uuid.uuid4().hex[:8]}",
        name="Diğer sayım kondisyonu",
    )
    root = Location.objects.create(
        code=f"COUNT-ROOT-{uuid.uuid4().hex[:8]}",
        name="Sayım kökü",
    )
    location = Location.objects.create(
        code=f"COUNT-LOC-{uuid.uuid4().hex[:8]}",
        name="Sayım rafı",
        parent=root,
        can_hold_stock=True,
    )
    return {
        "user": user,
        "category": category,
        "unit": unit,
        "quantity_material": quantity_material,
        "serialized_material": serialized_material,
        "condition": condition,
        "other_condition": other_condition,
        "root": root,
        "location": location,
    }


def create_session(count_objects, **overrides):
    values = {
        "reference_number": f"CNT-{uuid.uuid4().hex[:12]}",
        "scope_location": count_objects["root"],
    }
    values.update(overrides)
    return PhysicalCountSession.objects.create(**values)


def create_line(count_objects, session=None, **overrides):
    values = {
        "session": session or create_session(count_objects),
        "material": count_objects["quantity_material"],
        "location": count_objects["location"],
        "condition": count_objects["condition"],
        "expected_quantity": Decimal("0"),
    }
    values.update(overrides)
    return PhysicalCountQuantityLine.objects.create(**values)


def start_session(session, user):
    started_at = timezone.now()
    PhysicalCountSession.objects.filter(pk=session.pk).update(
        status=PhysicalCountSession.Status.STARTED,
        started_by_user=user,
        started_at=started_at,
    )
    session.refresh_from_db()
    return started_at


def test_session_uuid_defaults_and_valid_creation(count_objects):
    session = create_session(count_objects)

    assert isinstance(session.pk, uuid.UUID)
    assert session.status == PhysicalCountSession.Status.DRAFT
    assert (
        session.reconciliation_status
        == PhysicalCountSession.ReconciliationStatus.NOT_STARTED
    )
    assert session.baseline_candidate is False
    assert session.started_by_user is None
    assert session.started_at is None
    assert session.created_at is not None


def test_started_session_requires_actor_and_system_timestamp(count_objects):
    session = PhysicalCountSession(
        reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
        scope_location=count_objects["root"],
        status=PhysicalCountSession.Status.STARTED,
    )

    with pytest.raises(ValidationError):
        session.full_clean()

    session.started_by_user = count_objects["user"]
    session.started_at = timezone.now()
    session.full_clean()


def test_quantity_bucket_is_unique_inside_one_session(count_objects):
    session = create_session(count_objects)
    create_line(count_objects, session=session)

    with pytest.raises(IntegrityError, match="counting_qline_bucket_uniq"):
        with transaction.atomic():
            create_line(count_objects, session=session)


@pytest.mark.parametrize("expected", [Decimal("0"), Decimal("7.125")])
def test_zero_and_positive_expected_quantity_are_allowed(count_objects, expected):
    line = create_line(count_objects, expected_quantity=expected)

    assert line.expected_quantity == expected


def test_negative_expected_quantity_is_rejected(count_objects):
    with pytest.raises(IntegrityError, match="counting_qline_expected_nonneg"):
        with transaction.atomic():
            create_line(count_objects, expected_quantity=Decimal("-0.001"))


def test_counted_quantity_null_means_not_counted(count_objects):
    line = create_line(count_objects)

    assert line.counted_quantity is None
    assert line.counted_by_user is None
    assert line.counted_at is None
    assert line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED


@pytest.mark.parametrize("counted", [Decimal("0"), Decimal("4.250")])
def test_explicit_zero_and_positive_counted_quantity_are_allowed(
    count_objects, counted
):
    counted_at = timezone.now()
    line = create_line(
        count_objects,
        expected_quantity=counted,
        counted_quantity=counted,
        counted_by_user=count_objects["user"],
        counted_at=counted_at,
        resolution_status=PhysicalCountQuantityLine.ResolutionStatus.NO_DISCREPANCY,
    )

    assert line.counted_quantity == counted
    assert line.counted_at == counted_at


def test_negative_counted_quantity_is_rejected(count_objects):
    with pytest.raises(IntegrityError, match="counting_qline_counted_nonneg"):
        with transaction.atomic():
            create_line(
                count_objects,
                counted_quantity=Decimal("-0.001"),
                counted_by_user=count_objects["user"],
                counted_at=timezone.now(),
                resolution_status=(
                    PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
                ),
            )


def test_missing_count_cannot_be_silently_represented_as_zero(count_objects):
    with pytest.raises(IntegrityError, match="counting_qline_count_state_match"):
        with transaction.atomic():
            create_line(
                count_objects,
                counted_quantity=Decimal("0"),
                resolution_status=(
                    PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED
                ),
            )


def test_more_than_three_decimal_places_are_not_silently_accepted(count_objects):
    line = PhysicalCountQuantityLine(
        session=create_session(count_objects),
        material=count_objects["quantity_material"],
        location=count_objects["location"],
        condition=count_objects["condition"],
        expected_quantity=Decimal("1.0001"),
    )

    with pytest.raises(ValidationError):
        line.full_clean()


def test_quantity_material_is_accepted_by_model_and_database(count_objects):
    line = PhysicalCountQuantityLine(
        session=create_session(count_objects),
        material=count_objects["quantity_material"],
        location=count_objects["location"],
        condition=count_objects["condition"],
        expected_quantity=Decimal("1"),
    )
    line.full_clean()
    line.save()

    assert line.pk is not None


def test_serialized_material_is_rejected_by_model_validation(count_objects):
    line = PhysicalCountQuantityLine(
        session=create_session(count_objects),
        material=count_objects["serialized_material"],
        location=count_objects["location"],
        condition=count_objects["condition"],
        expected_quantity=Decimal("1"),
    )

    with pytest.raises(ValidationError, match="QUANTITY"):
        line.full_clean()


def test_serialized_material_is_rejected_by_raw_database_guard(count_objects):
    session = create_session(count_objects)

    with pytest.raises(IntegrityError, match="QUANTITY material"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO counting_physicalcountquantityline (
                    id, expected_quantity, counted_quantity, counted_at,
                    resolution_status, created_at, condition_id,
                    counted_by_user_id, location_id, material_id, session_id
                ) VALUES (%s, %s, NULL, NULL, %s, %s, %s, NULL, %s, %s, %s)
                """,
                [
                    uuid.uuid4(),
                    Decimal("1"),
                    PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED,
                    timezone.now(),
                    count_objects["condition"].pk,
                    count_objects["location"].pk,
                    count_objects["serialized_material"].pk,
                    session.pk,
                ],
            )


def test_material_tracking_mode_cannot_change_after_count_history(count_objects):
    create_line(count_objects)

    with pytest.raises(IntegrityError, match="physical count history"):
        with transaction.atomic():
            Material.objects.filter(pk=count_objects["quantity_material"].pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )


def test_same_bucket_may_exist_in_two_sessions(count_objects):
    first = create_line(count_objects, session=create_session(count_objects))
    second = create_line(count_objects, session=create_session(count_objects))

    assert first.pk != second.pk


def test_different_condition_is_a_distinct_bucket(count_objects):
    session = create_session(count_objects)
    first = create_line(count_objects, session=session)
    second = create_line(
        count_objects,
        session=session,
        condition=count_objects["other_condition"],
    )

    assert first.condition_id != second.condition_id


def test_known_unexpected_stock_uses_explicit_zero_expected(count_objects):
    line = create_line(
        count_objects,
        expected_quantity=Decimal("0"),
        counted_quantity=Decimal("3.500"),
        counted_by_user=count_objects["user"],
        counted_at=timezone.now(),
        resolution_status=PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL,
    )

    assert line.expected_quantity == Decimal("0")
    assert line.counted_quantity == Decimal("3.500")


@pytest.mark.parametrize("target", ["material", "location", "condition", "user"])
def test_historical_foreign_keys_use_restrict(count_objects, target):
    line = create_line(
        count_objects,
        counted_quantity=Decimal("0"),
        counted_by_user=count_objects["user"],
        counted_at=timezone.now(),
        resolution_status=PhysicalCountQuantityLine.ResolutionStatus.NO_DISCREPANCY,
    )
    referenced = {
        "material": line.material,
        "location": line.location,
        "condition": line.condition,
        "user": line.counted_by_user,
    }[target]

    with pytest.raises(RestrictedError):
        referenced.delete()


def test_count_records_create_no_ledger_or_projection_effect(count_objects):
    before = {
        "transactions": InventoryTransaction.objects.count(),
        "balances": StockBalance.objects.count(),
        "assets": SerializedAsset.objects.count(),
    }
    session = create_session(count_objects)
    create_line(
        count_objects,
        session=session,
        counted_quantity=Decimal("2"),
        counted_by_user=count_objects["user"],
        counted_at=timezone.now(),
        resolution_status=PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL,
    )

    assert InventoryTransaction.objects.count() == before["transactions"]
    assert StockBalance.objects.count() == before["balances"]
    assert SerializedAsset.objects.count() == before["assets"]


def test_baseline_candidate_classification_has_no_inventory_effect(count_objects):
    before = (
        InventoryTransaction.objects.count(),
        StockBalance.objects.count(),
        SerializedAsset.objects.count(),
    )

    session = create_session(count_objects, baseline_candidate=True)

    assert session.baseline_candidate is True
    assert (
        InventoryTransaction.objects.count(),
        StockBalance.objects.count(),
        SerializedAsset.objects.count(),
    ) == before


def test_expected_snapshot_is_immutable_after_start_via_orm(count_objects):
    session = create_session(count_objects)
    line = create_line(
        count_objects,
        session=session,
        expected_quantity=Decimal("2"),
    )
    start_session(session, count_objects["user"])

    with pytest.raises(DatabaseError, match="expected snapshot is immutable"):
        with transaction.atomic():
            PhysicalCountQuantityLine.objects.filter(pk=line.pk).update(
                expected_quantity=Decimal("3")
            )


def test_expected_snapshot_is_immutable_after_start_via_raw_sql(count_objects):
    session = create_session(count_objects)
    line = create_line(
        count_objects,
        session=session,
        expected_quantity=Decimal("2"),
    )
    start_session(session, count_objects["user"])

    with pytest.raises(DatabaseError, match="expected snapshot is immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE counting_physicalcountquantityline
                SET expected_quantity = %s
                WHERE id = %s
                """,
                [Decimal("4"), line.pk],
            )


def test_expected_snapshot_line_cannot_be_deleted_after_start(count_objects):
    session = create_session(count_objects)
    line = create_line(count_objects, session=session)
    start_session(session, count_objects["user"])

    with pytest.raises(DatabaseError, match="expected snapshot is immutable"):
        with transaction.atomic():
            line.delete()


def test_started_session_accepts_zero_expected_unexpected_stock_row(count_objects):
    session = create_session(count_objects)
    start_session(session, count_objects["user"])

    line = create_line(
        count_objects,
        session=session,
        expected_quantity=Decimal("0"),
    )

    assert line.expected_quantity == Decimal("0")


def test_started_session_rejects_new_positive_expected_snapshot_row(count_objects):
    session = create_session(count_objects)
    start_session(session, count_objects["user"])

    with pytest.raises(DatabaseError, match="only zero-expected"):
        with transaction.atomic():
            create_line(
                count_objects,
                session=session,
                expected_quantity=Decimal("1"),
            )


def test_started_session_cannot_be_deleted_even_without_lines(count_objects):
    session = create_session(count_objects)
    start_session(session, count_objects["user"])

    with pytest.raises(DatabaseError, match="cannot be deleted"):
        with transaction.atomic():
            session.delete()


@pytest.mark.parametrize(
    "update_values",
    [
        {"baseline_candidate": True},
        {"scope_location_id": uuid.uuid4()},
    ],
)
def test_started_session_scope_and_classification_are_immutable(
    count_objects, update_values
):
    session = create_session(count_objects)
    start_session(session, count_objects["user"])

    with pytest.raises(DatabaseError, match="scope and baseline classification"):
        with transaction.atomic():
            PhysicalCountSession.objects.filter(pk=session.pk).update(**update_values)


def test_inventory_package_has_no_counting_dependency():
    inventory_root = Path(__file__).resolve().parents[1] / "inventory"
    dependency = re.compile(r"(^|\n)\s*(?:from\s+counting\b|import\s+counting\b)")

    offenders = [
        str(path.relative_to(inventory_root))
        for path in inventory_root.rglob("*.py")
        if dependency.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
