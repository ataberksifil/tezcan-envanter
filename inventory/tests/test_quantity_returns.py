from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import Employee
from accounts.roles import SAFE_CATALOG_PERMISSION_LABELS
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    ProductionLine,
    StockBalance,
)
from inventory.services.issues import issue_quantity
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import (
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    INVALID_DESTINATION,
    INVALID_QUANTITY,
    OPERATION_CONFLICT,
    TRACKING_MODE_MISMATCH,
    receive_quantity,
)
from inventory.services.returns import (
    INVALID_ORIGINAL_ISSUE,
    RETURN_EXCEEDS_ISSUE_QUANTITY,
    RETURN_STOCK_PERMISSION,
    return_quantity,
)
from locations.models import Location

User = get_user_model()


def _permission(codename):
    return Permission.objects.get(
        content_type__app_label="inventory",
        content_type__model="inventorytransaction",
        codename=codename,
    )


def _grant(user, *codenames):
    user.user_permissions.add(*(_permission(codename) for codename in codenames))
    return User.objects.get(pk=user.pk)


@pytest.fixture
def return_objects(db):
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"RET-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"İade kategori {suffix}")
    material = Material.objects.create(
        material_code=f"RET-M-{suffix}",
        name="İade malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"RET-C-{suffix}",
        name="İade kondisyonu",
        sort_order=700,
    )
    source = Location.objects.create(
        code=f"RET-S-{suffix}",
        name="Çıkış rafı",
        active=True,
        can_hold_stock=True,
    )
    target = Location.objects.create(
        code=f"RET-T-{suffix}",
        name="İade rafı",
        active=True,
        can_hold_stock=True,
    )
    employee = Employee.objects.create(
        employee_number=f"RET-E-{suffix}",
        first_name="Ayşe",
        last_name="Yılmaz",
    )
    production_line = ProductionLine.objects.create(
        code=f"RET-PL-{suffix}",
        name="İade test hattı",
    )
    actor = _grant(
        User.objects.create_user(username=f"returner-{suffix}"),
        "receive_stock",
        "issue_stock",
        "return_stock",
    )
    receive_quantity(
        actor=actor,
        operation_id=uuid.uuid4(),
        material_id=material.pk,
        unit_id=unit.pk,
        condition_id=condition.pk,
        target_location_id=source.pk,
        quantity=Decimal("10.000"),
    )
    issue = issue_quantity(
        actor=actor,
        operation_id=uuid.uuid4(),
        material_id=material.pk,
        unit_id=unit.pk,
        condition_id=condition.pk,
        source_location_id=source.pk,
        quantity=Decimal("10.000"),
        receiver_employee_id=employee.pk,
        production_line_id=production_line.pk,
        usage_location_text="Pano 4",
    )
    return {
        "unit": unit,
        "category": category,
        "material": material,
        "condition": condition,
        "source": source,
        "target": target,
        "employee": employee,
        "production_line": production_line,
        "actor": actor,
        "issue": issue,
        "issue_line": issue.lines[0],
    }


def _request(objects, **overrides):
    values = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "original_issue_line_id": objects["issue_line"].pk,
        "target_location_id": objects["target"].pk,
        "quantity": Decimal("2.000"),
    }
    values.update(overrides)
    return values


def _assert_error(exc_info, code):
    assert exc_info.value.code == code


@pytest.mark.django_db
def test_full_return_copies_lineage_fields_and_updates_target(return_objects):
    operation_id = uuid.uuid4()
    audit_count = AuditEvent.objects.count()

    result = return_quantity(
        **_request(
            return_objects,
            operation_id=operation_id,
            quantity="10",
        )
    )

    assert result.replayed is False
    assert result.transaction.transaction_type == InventoryTransaction.TransactionType.RETURN
    assert result.transaction.operation_id == operation_id
    assert result.transaction.acting_user_id == return_objects["actor"].pk
    assert len(result.lines) == 1
    assert result.issue_context is None
    line = result.lines[0]
    assert line.line_number == 1
    assert line.material_id == return_objects["issue_line"].material_id
    assert line.unit_id == return_objects["issue_line"].unit_id
    assert line.condition_id == return_objects["issue_line"].condition_id
    assert line.source_location_id is None
    assert line.target_location_id == return_objects["target"].pk
    assert line.original_issue_line_id == return_objects["issue_line"].pk
    assert line.quantity == Decimal("10.000")
    assert StockBalance.objects.get(location=return_objects["target"]).quantity == Decimal(
        "10.000"
    )
    assert AuditEvent.objects.count() == audit_count

    payload = {
        "acting_user_id": str(return_objects["actor"].pk),
        "original_issue_line_id": str(return_objects["issue_line"].pk),
        "quantity": "10.000",
        "target_location_id": str(return_objects["target"].pk),
        "transaction_type": "RETURN",
    }
    expected = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert result.transaction.request_fingerprint == expected


@pytest.mark.django_db
def test_partial_and_multiple_returns_reach_exact_cumulative_cap(return_objects):
    quantities = (Decimal("2.500"), Decimal("3.500"), Decimal("4.000"))

    results = [
        return_quantity(**_request(return_objects, quantity=quantity))
        for quantity in quantities
    ]

    assert [result.lines[0].quantity for result in results] == list(quantities)
    assert return_objects["issue_line"].return_lines.count() == 3
    assert sum(
        return_objects["issue_line"].return_lines.values_list("quantity", flat=True)
    ) == Decimal("10.000")
    assert StockBalance.objects.get(location=return_objects["target"]).quantity == Decimal(
        "10.000"
    )


@pytest.mark.django_db
def test_sequential_over_return_rolls_back_without_projection_effect(return_objects):
    return_quantity(**_request(return_objects, quantity=Decimal("6.000")))
    transaction_count = InventoryTransaction.objects.count()
    line_count = InventoryTransactionLine.objects.count()

    with pytest.raises(ValidationError) as exc_info:
        return_quantity(**_request(return_objects, quantity=Decimal("5.000")))

    _assert_error(exc_info, RETURN_EXCEEDS_ISSUE_QUANTITY)
    assert InventoryTransaction.objects.count() == transaction_count
    assert InventoryTransactionLine.objects.count() == line_count
    assert StockBalance.objects.get(location=return_objects["target"]).quantity == Decimal(
        "6.000"
    )


@pytest.mark.django_db
def test_target_may_differ_from_original_issue_source(return_objects):
    result = return_quantity(**_request(return_objects, quantity=Decimal("1.000")))

    assert result.lines[0].target_location_id == return_objects["target"].pk
    assert result.lines[0].target_location_id != return_objects["source"].pk
    assert StockBalance.objects.get(location=return_objects["source"]).quantity == Decimal(
        "0.000"
    )


@pytest.mark.django_db
def test_new_return_does_not_revalidate_original_receiver(return_objects):
    Employee.objects.filter(pk=return_objects["employee"].pk).update(active=False)

    result = return_quantity(**_request(return_objects, quantity=Decimal("1.000")))

    assert result.replayed is False
    assert result.lines[0].original_issue_line_id == return_objects["issue_line"].pk


@pytest.mark.django_db
def test_non_issue_and_missing_original_lines_are_rejected(return_objects):
    receipt = receive_quantity(
        actor=return_objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=return_objects["material"].pk,
        unit_id=return_objects["unit"].pk,
        condition_id=return_objects["condition"].pk,
        target_location_id=return_objects["source"].pk,
        quantity=Decimal("1.000"),
    )
    for original_id in (receipt.lines[0].pk, uuid.uuid4(), "not-a-uuid"):
        with pytest.raises(ValidationError) as exc_info:
            return_quantity(
                **_request(return_objects, original_issue_line_id=original_id)
            )
        _assert_error(exc_info, INVALID_ORIGINAL_ISSUE)

    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("inactive_material", INACTIVE_MATERIAL),
        ("serialized_material", TRACKING_MODE_MISMATCH),
        ("inactive_condition", INACTIVE_CONDITION),
        ("inactive_target", INVALID_DESTINATION),
        ("nonholding_target", INVALID_DESTINATION),
    ],
)
def test_new_return_validates_derived_masters_and_target(
    return_objects, mutation, code
):
    locked_material_patch = None
    if mutation == "inactive_material":
        Material.objects.filter(pk=return_objects["material"].pk).update(active=False)
    elif mutation == "serialized_material":
        material = Material.objects.get(pk=return_objects["material"].pk)
        material.tracking_mode = Material.TrackingMode.SERIALIZED
        locked_material_patch = patch(
            "inventory.services.returns._locked_material",
            return_value=material,
        )
    elif mutation == "inactive_condition":
        MaterialCondition.objects.filter(pk=return_objects["condition"].pk).update(
            active=False
        )
    elif mutation == "inactive_target":
        Location.objects.filter(pk=return_objects["target"].pk).update(active=False)
    else:
        Location.objects.filter(pk=return_objects["target"].pk).update(
            can_hold_stock=False
        )

    context = locked_material_patch or nullcontext()
    with context:
        with pytest.raises(ValidationError) as exc_info:
            return_quantity(**_request(return_objects))

    _assert_error(exc_info, code)
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).exists()
    assert not StockBalance.objects.filter(location=return_objects["target"]).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "quantity",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("1.2345"),
        Decimal("NaN"),
        Decimal("Infinity"),
        1.0,
    ],
)
def test_invalid_quantity_is_rejected(return_objects, quantity):
    with pytest.raises(ValidationError) as exc_info:
        return_quantity(**_request(return_objects, quantity=quantity))
    _assert_error(exc_info, INVALID_QUANTITY)


@pytest.mark.django_db
def test_authorization_requires_active_explicit_permission(return_objects):
    suffix = uuid.uuid4().hex[:8]
    unauthorized = User.objects.create_user(username=f"unauth-ret-{suffix}")
    staff = User.objects.create_user(username=f"staff-ret-{suffix}", is_staff=True)
    inactive = _grant(
        User.objects.create_user(
            username=f"inactive-ret-{suffix}",
            is_active=False,
        ),
        "return_stock",
    )

    for actor in (unauthorized, staff, inactive, User(username="unsaved-return")):
        with pytest.raises(PermissionDenied):
            return_quantity(**_request(return_objects, actor=actor))

    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).exists()


@pytest.mark.django_db
def test_explicit_permission_and_superuser_follow_django_semantics(return_objects):
    explicit = _grant(
        User.objects.create_user(username=f"explicit-{uuid.uuid4().hex[:8]}"),
        "return_stock",
    )
    explicit_result = return_quantity(
        **_request(return_objects, actor=explicit, quantity=Decimal("1.000"))
    )
    superuser = User.objects.create_superuser(
        username=f"super-ret-{uuid.uuid4().hex[:8]}",
        password="test-only",
    )
    superuser_result = return_quantity(
        **_request(return_objects, actor=superuser, quantity=Decimal("1.000"))
    )

    assert explicit_result.transaction.acting_user_id == explicit.pk
    assert superuser_result.transaction.acting_user_id == superuser.pk


@pytest.mark.django_db
def test_actor_must_belong_to_requested_database_alias(return_objects):
    return_objects["actor"]._state.db = "other"
    with pytest.raises(PermissionDenied):
        return_quantity(**_request(return_objects))


@pytest.mark.django_db
def test_replay_is_denied_after_permission_revocation(return_objects):
    operation_id = uuid.uuid4()
    request = _request(return_objects, operation_id=operation_id)
    return_quantity(**request)
    return_objects["actor"].user_permissions.remove(_permission("return_stock"))

    with pytest.raises(PermissionDenied):
        return_quantity(**request)

    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).count() == 1
    assert StockBalance.objects.get(location=return_objects["target"]).quantity == Decimal(
        "2.000"
    )


@pytest.mark.django_db
def test_same_semantic_operation_replays_without_second_effect(return_objects):
    operation_id = uuid.uuid4()
    first = return_quantity(
        **_request(return_objects, operation_id=operation_id, quantity="2")
    )
    replay = return_quantity(
        **_request(
            return_objects,
            operation_id=operation_id,
            quantity=Decimal("2.000"),
        )
    )

    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert replay.lines[0].pk == first.lines[0].pk
    assert replay.issue_context is None
    assert return_objects["issue_line"].return_lines.count() == 1
    assert StockBalance.objects.get(location=return_objects["target"]).quantity == Decimal(
        "2.000"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("changed_field", ["quantity", "target_location_id", "original_issue_line_id"])
def test_same_operation_with_changed_semantics_conflicts(
    return_objects, changed_field
):
    operation_id = uuid.uuid4()
    request = _request(return_objects, operation_id=operation_id)
    return_quantity(**request)
    changed = dict(request)
    if changed_field == "quantity":
        changed[changed_field] = Decimal("1.000")
    elif changed_field == "target_location_id":
        changed[changed_field] = return_objects["source"].pk
    else:
        other_issue = issue_quantity(
            actor=return_objects["actor"],
            operation_id=uuid.uuid4(),
            material_id=return_objects["material"].pk,
            unit_id=return_objects["unit"].pk,
            condition_id=return_objects["condition"].pk,
            source_location_id=return_objects["target"].pk,
            quantity=Decimal("1.000"),
            receiver_employee_id=return_objects["employee"].pk,
            production_line_id=return_objects["production_line"].pk,
            usage_location_text="Pano 5",
        )
        changed[changed_field] = other_issue.lines[0].pk

    with pytest.raises(ValidationError) as exc_info:
        return_quantity(**changed)

    _assert_error(exc_info, OPERATION_CONFLICT)
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "lifecycle_change",
    ["material", "condition", "target", "receiver"],
)
def test_replay_skips_current_mutable_master_state(return_objects, lifecycle_change):
    operation_id = uuid.uuid4()
    request = _request(return_objects, operation_id=operation_id)
    first = return_quantity(**request)

    if lifecycle_change == "material":
        Material.objects.filter(pk=return_objects["material"].pk).update(active=False)
    elif lifecycle_change == "condition":
        MaterialCondition.objects.filter(pk=return_objects["condition"].pk).update(
            active=False
        )
    elif lifecycle_change == "target":
        StockBalance.objects.filter(location=return_objects["target"]).update(
            quantity=Decimal("0.000")
        )
        Location.objects.filter(pk=return_objects["target"].pk).update(active=False)
    else:
        Employee.objects.filter(pk=return_objects["employee"].pk).update(active=False)

    replay = return_quantity(**request)

    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).count() == 1
    assert return_objects["issue_line"].return_lines.count() == 1


@pytest.mark.django_db
def test_forced_balance_failure_rolls_back_and_does_not_consume_cap(return_objects):
    operation_id = uuid.uuid4()
    request = _request(
        return_objects,
        operation_id=operation_id,
        quantity=Decimal("10.000"),
    )
    with patch.object(StockBalance, "save", side_effect=RuntimeError("controlled")):
        with pytest.raises(RuntimeError, match="controlled"):
            return_quantity(**request)

    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RETURN
    ).exists()
    assert not return_objects["issue_line"].return_lines.exists()
    assert not StockBalance.objects.filter(location=return_objects["target"]).exists()

    retry = return_quantity(**request)
    assert retry.replayed is False
    assert retry.lines[0].quantity == Decimal("10.000")


@pytest.mark.django_db
def test_projection_formula_is_cross_location_and_verifier_is_read_only(
    return_objects,
):
    return_quantity(**_request(return_objects, quantity=Decimal("4.000")))

    assert verify_quantity_projection() == ()
    source = StockBalance.objects.get(location=return_objects["source"])
    target = StockBalance.objects.get(location=return_objects["target"])
    assert source.quantity == Decimal("0.000")
    assert target.quantity == Decimal("4.000")

    original_updated_at = target.updated_at
    StockBalance.objects.filter(pk=target.pk).update(quantity=Decimal("1.000"))
    corrupted_updated_at = StockBalance.objects.get(pk=target.pk).updated_at
    before_count = StockBalance.objects.count()

    mismatches = verify_quantity_projection()

    assert len(mismatches) == 1
    assert mismatches[0].location_id == return_objects["target"].pk
    assert mismatches[0].expected_quantity == Decimal("4.000")
    assert mismatches[0].actual_quantity == Decimal("1.000")
    assert StockBalance.objects.count() == before_count
    assert StockBalance.objects.get(pk=target.pk).updated_at == corrupted_updated_at
    assert corrupted_updated_at == original_updated_at


@pytest.mark.django_db
def test_return_permission_is_added_to_managed_role_rollout():
    assert RETURN_STOCK_PERMISSION in SAFE_CATALOG_PERMISSION_LABELS
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 23
