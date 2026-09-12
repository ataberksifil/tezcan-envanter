from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import Employee
from accounts.roles import SAFE_CATALOG_PERMISSION_LABELS
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
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
    UNIT_MISMATCH,
    receive_quantity,
)
from inventory.services.returns import return_quantity
from inventory.services.transfers import (
    INSUFFICIENT_STOCK,
    INVALID_SOURCE,
    SAME_SOURCE_DESTINATION,
    TRANSFER_STOCK_PERMISSION,
    transfer_quantity,
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
def transfer_objects(db):
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"XFR-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"Transfer kategori {suffix}")
    material = Material.objects.create(
        material_code=f"XFR-M-{suffix}",
        name="Transfer malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"XFR-C-{suffix}",
        name="Transfer kondisyonu",
        sort_order=920,
    )
    source = Location.objects.create(
        code=f"XFR-S-{suffix}",
        name="Kaynak rafı",
        active=True,
        can_hold_stock=True,
    )
    target = Location.objects.create(
        code=f"XFR-T-{suffix}",
        name="Hedef rafı",
        active=True,
        can_hold_stock=True,
    )
    other_location = Location.objects.create(
        code=f"XFR-O-{suffix}",
        name="Dokunulmayan raf",
        active=True,
        can_hold_stock=True,
    )
    employee = Employee.objects.create(
        employee_number=f"XFR-E-{suffix}",
        first_name="Ayşe",
        last_name="Yılmaz",
    )
    production_line = ProductionLine.objects.create(
        code=f"XFR-PL-{suffix}",
        name="Transfer test hattı",
    )
    actor = _grant(
        User.objects.create_user(username=f"xfer-{suffix}"),
        "receive_stock",
        "issue_stock",
        "return_stock",
        "transfer_stock",
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
    return {
        "unit": unit,
        "category": category,
        "material": material,
        "condition": condition,
        "source": source,
        "target": target,
        "other_location": other_location,
        "employee": employee,
        "production_line": production_line,
        "actor": actor,
    }


def _request(objects, **overrides):
    values = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "material_id": objects["material"].pk,
        "unit_id": objects["unit"].pk,
        "condition_id": objects["condition"].pk,
        "source_location_id": objects["source"].pk,
        "target_location_id": objects["target"].pk,
        "quantity": Decimal("4.000"),
    }
    values.update(overrides)
    return values


def _assert_code(exc_info, code):
    assert exc_info.value.code == code


def _source_balance(objects):
    return StockBalance.objects.get(
        material=objects["material"],
        location=objects["source"],
        condition=objects["condition"],
    )


def _target_balance(objects):
    return StockBalance.objects.get(
        material=objects["material"],
        location=objects["target"],
        condition=objects["condition"],
    )


@pytest.mark.django_db
def test_simple_transfer_moves_quantity_and_writes_one_line(transfer_objects):
    operation_id = uuid.uuid4()
    audit_count = AuditEvent.objects.count()

    result = transfer_quantity(
        **_request(transfer_objects, operation_id=operation_id, quantity="4")
    )

    assert result.replayed is False
    assert result.transaction.transaction_type == InventoryTransaction.TransactionType.TRANSFER
    assert result.transaction.operation_id == operation_id
    assert result.transaction.acting_user_id == transfer_objects["actor"].pk
    assert result.issue_context is None
    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.line_number == 1
    assert line.material_id == transfer_objects["material"].pk
    assert line.unit_id == transfer_objects["unit"].pk
    assert line.condition_id == transfer_objects["condition"].pk
    assert line.source_location_id == transfer_objects["source"].pk
    assert line.target_location_id == transfer_objects["target"].pk
    assert line.quantity == Decimal("4.000")
    assert line.original_issue_line_id is None
    assert _source_balance(transfer_objects).quantity == Decimal("6.000")
    assert _target_balance(transfer_objects).quantity == Decimal("4.000")
    assert IssueContext.objects.count() == 0
    assert AuditEvent.objects.count() == audit_count
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1
    assert InventoryTransactionLine.objects.filter(
        transaction__transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1

    payload = {
        "acting_user_id": str(transfer_objects["actor"].pk),
        "condition_id": str(transfer_objects["condition"].pk),
        "material_id": str(transfer_objects["material"].pk),
        "quantity": "4.000",
        "source_location_id": str(transfer_objects["source"].pk),
        "target_location_id": str(transfer_objects["target"].pk),
        "transaction_type": "TRANSFER",
        "unit_id": str(transfer_objects["unit"].pk),
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
def test_partial_transfer_leaves_remaining_source(transfer_objects):
    result = transfer_quantity(**_request(transfer_objects, quantity=Decimal("2.500")))

    assert result.lines[0].quantity == Decimal("2.500")
    assert _source_balance(transfer_objects).quantity == Decimal("7.500")
    assert _target_balance(transfer_objects).quantity == Decimal("2.500")


@pytest.mark.django_db
def test_full_source_depletion_retains_zero_row(transfer_objects):
    result = transfer_quantity(**_request(transfer_objects, quantity=Decimal("10.000")))
    source = _source_balance(transfer_objects)

    assert result.lines[0].quantity == Decimal("10.000")
    assert source.quantity == Decimal("0.000")
    assert StockBalance.objects.filter(pk=source.pk).exists()
    assert _target_balance(transfer_objects).quantity == Decimal("10.000")


@pytest.mark.django_db
def test_existing_target_is_incremented(transfer_objects):
    receive_quantity(
        actor=transfer_objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=transfer_objects["material"].pk,
        unit_id=transfer_objects["unit"].pk,
        condition_id=transfer_objects["condition"].pk,
        target_location_id=transfer_objects["target"].pk,
        quantity=Decimal("1.250"),
    )

    transfer_quantity(**_request(transfer_objects, quantity=Decimal("3.000")))

    assert StockBalance.objects.filter(
        material=transfer_objects["material"],
        location=transfer_objects["target"],
        condition=transfer_objects["condition"],
    ).count() == 1
    assert _target_balance(transfer_objects).quantity == Decimal("4.250")
    assert _source_balance(transfer_objects).quantity == Decimal("7.000")


@pytest.mark.django_db
def test_missing_target_is_created_from_zero(transfer_objects):
    assert not StockBalance.objects.filter(location=transfer_objects["target"]).exists()

    transfer_quantity(**_request(transfer_objects, quantity=Decimal("1.000")))

    target = _target_balance(transfer_objects)
    assert target.quantity == Decimal("1.000")
    assert StockBalance.objects.filter(
        material=transfer_objects["material"],
        location=transfer_objects["target"],
        condition=transfer_objects["condition"],
    ).count() == 1


@pytest.mark.django_db
def test_missing_source_balance_is_rejected_without_creating_source(transfer_objects):
    _source_balance(transfer_objects).delete()
    transaction_count = InventoryTransaction.objects.count()

    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(**_request(transfer_objects))

    _assert_code(exc_info, INSUFFICIENT_STOCK)
    assert not StockBalance.objects.filter(
        material=transfer_objects["material"],
        location=transfer_objects["source"],
        condition=transfer_objects["condition"],
    ).exists()
    assert not StockBalance.objects.filter(location=transfer_objects["target"]).exists()
    assert InventoryTransaction.objects.count() == transaction_count


@pytest.mark.django_db
def test_zero_source_balance_is_rejected(transfer_objects):
    transfer_quantity(**_request(transfer_objects, quantity=Decimal("10.000")))
    transaction_count = InventoryTransaction.objects.count()

    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(**_request(transfer_objects, quantity=Decimal("0.001")))

    _assert_code(exc_info, INSUFFICIENT_STOCK)
    assert _source_balance(transfer_objects).quantity == Decimal("0.000")
    assert InventoryTransaction.objects.count() == transaction_count


@pytest.mark.django_db
def test_insufficient_source_is_rejected(transfer_objects):
    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(**_request(transfer_objects, quantity=Decimal("10.001")))

    _assert_code(exc_info, INSUFFICIENT_STOCK)
    assert _source_balance(transfer_objects).quantity == Decimal("10.000")
    assert not StockBalance.objects.filter(location=transfer_objects["target"]).exists()
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()


@pytest.mark.django_db
def test_same_source_and_target_is_rejected_before_mutation(transfer_objects):
    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(
            **_request(
                transfer_objects,
                target_location_id=transfer_objects["source"].pk,
            )
        )

    _assert_code(exc_info, SAME_SOURCE_DESTINATION)
    assert _source_balance(transfer_objects).quantity == Decimal("10.000")
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("inactive_material", INACTIVE_MATERIAL),
        ("serialized_material", TRACKING_MODE_MISMATCH),
        ("unit_mismatch", UNIT_MISMATCH),
        ("inactive_condition", INACTIVE_CONDITION),
        ("inactive_source", INVALID_SOURCE),
        ("inactive_target", INVALID_DESTINATION),
        ("source_cannot_hold", INVALID_SOURCE),
        ("target_cannot_hold", INVALID_DESTINATION),
    ],
)
def test_new_transfer_validates_locked_master_state(transfer_objects, mutation, code):
    request = _request(transfer_objects)
    if mutation == "inactive_material":
        Material.objects.filter(pk=transfer_objects["material"].pk).update(active=False)
    elif mutation == "serialized_material":
        serialized = Material.objects.create(
            material_code=f"SER-{uuid.uuid4().hex[:8]}",
            name="Seri malzeme",
            category=transfer_objects["category"],
            unit=None,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        request["material_id"] = serialized.pk
    elif mutation == "unit_mismatch":
        request["unit_id"] = UnitOfMeasure.objects.create(
            code=f"OTHER-{uuid.uuid4().hex[:8]}",
            name="Başka birim",
        ).pk
    elif mutation == "inactive_condition":
        MaterialCondition.objects.filter(pk=transfer_objects["condition"].pk).update(
            active=False
        )
    elif mutation == "inactive_source":
        empty_source = Location.objects.create(
            code=f"INACT-S-{uuid.uuid4().hex[:8]}",
            name="Pasif kaynak",
            active=False,
            can_hold_stock=True,
        )
        request["source_location_id"] = empty_source.pk
    elif mutation == "inactive_target":
        Location.objects.filter(pk=transfer_objects["target"].pk).update(active=False)
    elif mutation == "source_cannot_hold":
        empty_source = Location.objects.create(
            code=f"HOLD-S-{uuid.uuid4().hex[:8]}",
            name="Tutamayan kaynak",
            active=True,
            can_hold_stock=False,
        )
        request["source_location_id"] = empty_source.pk
    else:
        Location.objects.filter(pk=transfer_objects["target"].pk).update(
            can_hold_stock=False
        )

    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(**request)

    _assert_code(exc_info, code)
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()
    assert _source_balance(transfer_objects).quantity == Decimal("10.000")
    assert not StockBalance.objects.filter(
        material=transfer_objects["material"],
        location=transfer_objects["target"],
        condition=transfer_objects["condition"],
    ).exists()


@pytest.mark.django_db
def test_other_condition_and_location_are_untouched(transfer_objects):
    other_condition = MaterialCondition.objects.create(
        code=f"OTHER-{uuid.uuid4().hex[:8]}",
        name="Diğer kondisyon",
        sort_order=921,
    )
    other_condition_balance = StockBalance.objects.create(
        material=transfer_objects["material"],
        location=transfer_objects["source"],
        condition=other_condition,
        quantity=Decimal("50.000"),
    )
    other_location_balance = StockBalance.objects.create(
        material=transfer_objects["material"],
        location=transfer_objects["other_location"],
        condition=transfer_objects["condition"],
        quantity=Decimal("50.000"),
    )

    transfer_quantity(**_request(transfer_objects, quantity=Decimal("3.000")))

    other_condition_balance.refresh_from_db()
    other_location_balance.refresh_from_db()
    assert other_condition_balance.quantity == Decimal("50.000")
    assert other_location_balance.quantity == Decimal("50.000")
    assert _source_balance(transfer_objects).quantity == Decimal("7.000")
    assert _target_balance(transfer_objects).quantity == Decimal("3.000")


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
def test_invalid_quantity_is_rejected(transfer_objects, quantity):
    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(**_request(transfer_objects, quantity=quantity))
    _assert_code(exc_info, INVALID_QUANTITY)


@pytest.mark.django_db
def test_authorization_requires_active_explicit_permission(transfer_objects):
    suffix = uuid.uuid4().hex[:8]
    unauthorized = User.objects.create_user(username=f"unauth-xfr-{suffix}")
    staff = User.objects.create_user(username=f"staff-xfr-{suffix}", is_staff=True)
    named = User.objects.create_user(username=f"named-xfr-{suffix}")
    group, _ = Group.objects.get_or_create(name="STOREKEEPER")
    named.groups.add(group)
    inactive = _grant(
        User.objects.create_user(
            username=f"inactive-xfr-{suffix}",
            is_active=False,
        ),
        "transfer_stock",
    )

    for actor in (unauthorized, staff, named, inactive, User(username="unsaved-xfr")):
        with pytest.raises(PermissionDenied):
            transfer_quantity(**_request(transfer_objects, actor=actor))

    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()


@pytest.mark.django_db
def test_explicit_permission_and_superuser_follow_django_semantics(transfer_objects):
    explicit = _grant(
        User.objects.create_user(username=f"explicit-xfr-{uuid.uuid4().hex[:8]}"),
        "transfer_stock",
    )
    explicit_result = transfer_quantity(
        **_request(transfer_objects, actor=explicit, quantity=Decimal("1.000"))
    )
    superuser = User.objects.create_superuser(
        username=f"super-xfr-{uuid.uuid4().hex[:8]}",
        password="test-only",
    )
    superuser_result = transfer_quantity(
        **_request(transfer_objects, actor=superuser, quantity=Decimal("1.000"))
    )

    assert explicit_result.transaction.acting_user_id == explicit.pk
    assert superuser_result.transaction.acting_user_id == superuser.pk


@pytest.mark.django_db
def test_actor_must_belong_to_requested_database_alias(transfer_objects):
    transfer_objects["actor"]._state.db = "other"
    with pytest.raises(PermissionDenied):
        transfer_quantity(**_request(transfer_objects))


@pytest.mark.django_db
def test_replay_is_denied_after_permission_revocation(transfer_objects):
    operation_id = uuid.uuid4()
    request = _request(transfer_objects, operation_id=operation_id)
    first = transfer_quantity(**request)
    transfer_objects["actor"].user_permissions.remove(_permission("transfer_stock"))

    with pytest.raises(PermissionDenied):
        transfer_quantity(**request)

    assert first.replayed is False
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1
    assert _source_balance(transfer_objects).quantity == Decimal("6.000")
    assert _target_balance(transfer_objects).quantity == Decimal("4.000")


@pytest.mark.django_db
def test_same_semantic_operation_replays_without_second_effect(transfer_objects):
    operation_id = uuid.uuid4()
    first = transfer_quantity(
        **_request(transfer_objects, operation_id=operation_id, quantity="4")
    )
    replay = transfer_quantity(
        **_request(
            transfer_objects,
            operation_id=operation_id,
            quantity=Decimal("4.000"),
        )
    )

    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert replay.lines[0].pk == first.lines[0].pk
    assert replay.issue_context is None
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1
    assert _source_balance(transfer_objects).quantity == Decimal("6.000")
    assert _target_balance(transfer_objects).quantity == Decimal("4.000")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changed_field",
    ["quantity", "source_location_id", "target_location_id"],
)
def test_same_operation_with_changed_semantics_conflicts(
    transfer_objects, changed_field
):
    operation_id = uuid.uuid4()
    request = _request(transfer_objects, operation_id=operation_id)
    transfer_quantity(**request)
    changed = dict(request)
    if changed_field == "quantity":
        changed[changed_field] = Decimal("1.000")
    elif changed_field == "source_location_id":
        changed[changed_field] = transfer_objects["other_location"].pk
    else:
        changed[changed_field] = transfer_objects["other_location"].pk

    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(**changed)

    _assert_code(exc_info, OPERATION_CONFLICT)
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1
    assert _source_balance(transfer_objects).quantity == Decimal("6.000")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "lifecycle_change",
    ["material", "condition", "source", "target"],
)
def test_replay_skips_current_mutable_master_state(transfer_objects, lifecycle_change):
    operation_id = uuid.uuid4()
    request = _request(transfer_objects, operation_id=operation_id)
    first = transfer_quantity(**request)

    if lifecycle_change == "material":
        Material.objects.filter(pk=transfer_objects["material"].pk).update(active=False)
    elif lifecycle_change == "condition":
        MaterialCondition.objects.filter(pk=transfer_objects["condition"].pk).update(
            active=False
        )
    elif lifecycle_change == "source":
        StockBalance.objects.filter(
            material=transfer_objects["material"],
            location=transfer_objects["source"],
            condition=transfer_objects["condition"],
        ).update(quantity=Decimal("0.000"))
        Location.objects.filter(pk=transfer_objects["source"].pk).update(active=False)
    else:
        StockBalance.objects.filter(
            material=transfer_objects["material"],
            location=transfer_objects["target"],
            condition=transfer_objects["condition"],
        ).update(quantity=Decimal("0.000"))
        Location.objects.filter(pk=transfer_objects["target"].pk).update(active=False)

    source_before = _source_balance(transfer_objects).quantity
    target_before = _target_balance(transfer_objects).quantity
    replay = transfer_quantity(**request)

    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).count() == 1
    assert _source_balance(transfer_objects).quantity == source_before
    assert _target_balance(transfer_objects).quantity == target_before


@pytest.mark.django_db
def test_forced_balance_failure_rolls_back_and_same_operation_can_retry(
    transfer_objects,
):
    receive_quantity(
        actor=transfer_objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=transfer_objects["material"].pk,
        unit_id=transfer_objects["unit"].pk,
        condition_id=transfer_objects["condition"].pk,
        target_location_id=transfer_objects["target"].pk,
        quantity=Decimal("1.000"),
    )
    target_pk = _target_balance(transfer_objects).pk
    operation_id = uuid.uuid4()
    request = _request(
        transfer_objects,
        operation_id=operation_id,
        quantity=Decimal("4.000"),
    )
    original_save = StockBalance.save

    def fail_target_increment(self, *args, **kwargs):
        if self.pk == target_pk:
            raise RuntimeError("controlled")
        return original_save(self, *args, **kwargs)

    with patch.object(StockBalance, "save", fail_target_increment):
        with pytest.raises(RuntimeError, match="controlled"):
            transfer_quantity(**request)

    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()
    assert _source_balance(transfer_objects).quantity == Decimal("10.000")
    assert _target_balance(transfer_objects).quantity == Decimal("1.000")

    retry = transfer_quantity(**request)
    assert retry.replayed is False
    assert retry.lines[0].quantity == Decimal("4.000")
    assert _source_balance(transfer_objects).quantity == Decimal("6.000")
    assert _target_balance(transfer_objects).quantity == Decimal("5.000")


@pytest.mark.django_db
def test_projection_adds_target_and_subtracts_source(transfer_objects):
    transfer_quantity(**_request(transfer_objects, quantity=Decimal("4.000")))

    assert verify_quantity_projection() == ()
    assert _source_balance(transfer_objects).quantity == Decimal("6.000")
    assert _target_balance(transfer_objects).quantity == Decimal("4.000")


@pytest.mark.django_db
def test_projection_combines_receipt_issue_return_and_transfer(transfer_objects):
    issue_quantity(
        actor=transfer_objects["actor"],
        operation_id=uuid.uuid4(),
        material_id=transfer_objects["material"].pk,
        unit_id=transfer_objects["unit"].pk,
        condition_id=transfer_objects["condition"].pk,
        source_location_id=transfer_objects["source"].pk,
        quantity=Decimal("2.000"),
        receiver_employee_id=transfer_objects["employee"].pk,
        production_line_id=transfer_objects["production_line"].pk,
        usage_location_text="Pano 9",
    )
    original_issue_line = InventoryTransactionLine.objects.get(
        transaction__transaction_type=InventoryTransaction.TransactionType.ISSUE
    )
    return_quantity(
        actor=transfer_objects["actor"],
        operation_id=uuid.uuid4(),
        original_issue_line_id=original_issue_line.pk,
        target_location_id=transfer_objects["target"].pk,
        quantity=Decimal("1.000"),
    )
    transfer_quantity(**_request(transfer_objects, quantity=Decimal("3.000")))

    assert verify_quantity_projection() == ()
    assert _source_balance(transfer_objects).quantity == Decimal("5.000")
    assert _target_balance(transfer_objects).quantity == Decimal("4.000")


@pytest.mark.django_db
def test_projection_verifier_is_read_only_and_reports_corruption(transfer_objects):
    transfer_quantity(**_request(transfer_objects, quantity=Decimal("4.000")))
    target = _target_balance(transfer_objects)
    original_updated_at = target.updated_at
    StockBalance.objects.filter(pk=target.pk).update(quantity=Decimal("1.000"))
    corrupted_updated_at = StockBalance.objects.get(pk=target.pk).updated_at
    before_count = StockBalance.objects.count()

    mismatches = verify_quantity_projection()

    assert len(mismatches) == 1
    assert mismatches[0].location_id == transfer_objects["target"].pk
    assert mismatches[0].expected_quantity == Decimal("4.000")
    assert mismatches[0].actual_quantity == Decimal("1.000")
    assert StockBalance.objects.count() == before_count
    assert StockBalance.objects.get(pk=target.pk).updated_at == corrupted_updated_at
    assert corrupted_updated_at == original_updated_at


@pytest.mark.django_db
def test_transfer_stock_is_not_rolled_out_in_this_step(transfer_objects):
    assert TRANSFER_STOCK_PERMISSION not in SAFE_CATALOG_PERMISSION_LABELS
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 22
    assert "transfer_stock" in dict(InventoryTransaction._meta.permissions)


@pytest.mark.django_db
def test_malformed_operation_id_is_rejected(transfer_objects):
    with pytest.raises(ValidationError) as exc_info:
        transfer_quantity(**_request(transfer_objects, operation_id="not-a-uuid"))
    _assert_code(exc_info, "inventory.invalid_operation_id")
    assert not InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.TRANSFER
    ).exists()
