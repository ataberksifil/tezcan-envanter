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
from django.utils import timezone

from accounts.roles import (
    DEFAULT_ROLE_TEMPLATES,
    SAFE_CATALOG_PERMISSION_LABELS,
)
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    StockBalance,
)
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import (
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    INVALID_DESTINATION,
    INVALID_QUANTITY,
    OPERATION_CONFLICT,
    RECEIVE_STOCK_PERMISSION,
    TRACKING_MODE_MISMATCH,
    UNIT_MISMATCH,
    normalize_quantity,
    receive_quantity,
)
from locations.models import Location

User = get_user_model()


def _grant_receive_stock(user):
    permission = Permission.objects.get(
        content_type__app_label="inventory",
        content_type__model="inventorytransaction",
        codename="receive_stock",
    )
    user.user_permissions.add(permission)
    return User.objects.get(pk=user.pk)


@pytest.fixture
def receipt_objects(db):
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"RCV-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"Giriş kategori {suffix}")
    material = Material.objects.create(
        material_code=f"RCV-M-{suffix}",
        name="Giriş malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"RCV-C-{suffix}",
        name="Giriş kondisyonu",
        sort_order=300,
    )
    location = Location.objects.create(
        code=f"RCV-L-{suffix}",
        name="Giriş rafı",
        active=True,
        can_hold_stock=True,
    )
    actor = _grant_receive_stock(
        User.objects.create_user(username=f"receiver-{suffix}")
    )
    return {
        "unit": unit,
        "category": category,
        "material": material,
        "condition": condition,
        "location": location,
        "actor": actor,
    }


def _request(objects, **overrides):
    values = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "material_id": objects["material"].pk,
        "unit_id": objects["unit"].pk,
        "condition_id": objects["condition"].pk,
        "target_location_id": objects["location"].pk,
        "quantity": Decimal("1.250"),
    }
    values.update(overrides)
    return values


def _assert_error_code(exc_info, code):
    assert exc_info.value.code == code


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1"), Decimal("1.000")),
        (2, Decimal("2.000")),
        ("1.0", Decimal("1.000")),
        ("1.234", Decimal("1.234")),
        (Decimal("1.2300"), Decimal("1.230")),
    ],
)
def test_quantity_normalization_accepts_exact_forms(value, expected):
    assert normalize_quantity(value) == expected
    assert normalize_quantity(value).as_tuple().exponent == -3


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        True,
        Decimal("0"),
        Decimal("-1"),
        Decimal("1.2345"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("1000000000000000"),
        "not-a-number",
    ],
)
def test_quantity_normalization_rejects_invalid_values(value):
    with pytest.raises(ValidationError) as exc_info:
        normalize_quantity(value)
    _assert_error_code(exc_info, INVALID_QUANTITY)


@pytest.mark.django_db
def test_valid_receipt_appends_immutable_ledger_and_projection(receipt_objects):
    operation_id = uuid.uuid4()
    before = timezone.now()
    audit_count = AuditEvent.objects.count()

    result = receive_quantity(
        **_request(
            receipt_objects,
            operation_id=operation_id,
            quantity="1.234",
        )
    )
    after = timezone.now()

    assert result.replayed is False
    assert result.transaction.transaction_type == "RECEIPT"
    assert result.transaction.operation_id == operation_id
    assert result.transaction.acting_user_id == receipt_objects["actor"].pk
    assert before <= result.transaction.occurred_at <= after
    assert timezone.is_aware(result.transaction.occurred_at)
    assert len(result.transaction.request_fingerprint) == 64
    assert result.transaction.request_fingerprint == result.transaction.request_fingerprint.lower()
    int(result.transaction.request_fingerprint, 16)

    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.line_number == 1
    assert line.material_id == receipt_objects["material"].pk
    assert line.quantity == Decimal("1.234")
    assert line.unit_id == receipt_objects["unit"].pk
    assert line.condition_id == receipt_objects["condition"].pk
    assert line.source_location_id is None
    assert line.target_location_id == receipt_objects["location"].pk
    balance = StockBalance.objects.get()
    assert balance.quantity == Decimal("1.234")
    assert AuditEvent.objects.count() == audit_count

    payload = {
        "acting_user_id": str(receipt_objects["actor"].pk),
        "condition_id": str(receipt_objects["condition"].pk),
        "material_id": str(receipt_objects["material"].pk),
        "quantity": "1.234",
        "target_location_id": str(receipt_objects["location"].pk),
        "transaction_type": "RECEIPT",
        "unit_id": str(receipt_objects["unit"].pk),
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
def test_different_operations_accumulate_in_one_balance(receipt_objects):
    first = receive_quantity(**_request(receipt_objects, quantity="1.125"))
    second = receive_quantity(**_request(receipt_objects, quantity=Decimal("2.250")))

    assert first.transaction.pk != second.transaction.pk
    assert InventoryTransaction.objects.count() == 2
    assert InventoryTransactionLine.objects.count() == 2
    assert StockBalance.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("3.375")


@pytest.mark.django_db
def test_semantically_equivalent_quantity_replays_original(receipt_objects):
    operation_id = uuid.uuid4()
    first = receive_quantity(
        **_request(receipt_objects, operation_id=operation_id, quantity="1.0")
    )
    original_time = first.transaction.occurred_at
    original_line_id = first.lines[0].pk

    replay = receive_quantity(
        **_request(
            receipt_objects,
            operation_id=operation_id,
            quantity=Decimal("1.000"),
        )
    )

    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert replay.transaction.occurred_at == original_time
    assert replay.lines[0].pk == original_line_id
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("1.000")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changed_field",
    ["quantity", "material_id", "unit_id", "condition_id", "target_location_id", "actor"],
)
def test_operation_id_conflict_rejects_changed_fingerprint_input(
    receipt_objects, changed_field
):
    operation_id = uuid.uuid4()
    initial = _request(receipt_objects, operation_id=operation_id)
    receive_quantity(**initial)
    changed = dict(initial)

    suffix = uuid.uuid4().hex[:8]
    if changed_field == "quantity":
        changed[changed_field] = Decimal("2.000")
    elif changed_field == "material_id":
        changed[changed_field] = Material.objects.create(
            material_code=f"ALT-M-{suffix}",
            name="Başka malzeme",
            category=receipt_objects["category"],
            unit=receipt_objects["unit"],
            tracking_mode=Material.TrackingMode.QUANTITY,
        ).pk
    elif changed_field == "unit_id":
        changed[changed_field] = UnitOfMeasure.objects.create(
            code=f"ALT-U-{suffix}", name="Başka birim"
        ).pk
    elif changed_field == "condition_id":
        changed[changed_field] = MaterialCondition.objects.create(
            code=f"ALT-C-{suffix}", name="Başka kondisyon", sort_order=400
        ).pk
    elif changed_field == "target_location_id":
        changed[changed_field] = Location.objects.create(
            code=f"ALT-L-{suffix}",
            name="Başka raf",
            active=True,
            can_hold_stock=True,
        ).pk
    else:
        changed[changed_field] = _grant_receive_stock(
            User.objects.create_user(username=f"other-{suffix}")
        )

    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(**changed)
    _assert_error_code(exc_info, OPERATION_CONFLICT)
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1
    assert StockBalance.objects.get().quantity == Decimal("1.250")


@pytest.mark.django_db
def test_replay_skips_mutable_master_revalidation(receipt_objects):
    operation_id = uuid.uuid4()
    first = receive_quantity(
        **_request(receipt_objects, operation_id=operation_id)
    )
    Material.objects.filter(pk=receipt_objects["material"].pk).update(
        name="Sonradan yeniden adlandırıldı"
    )
    Location.objects.filter(pk=receipt_objects["location"].pk).update(
        code=f"RENAMED-{uuid.uuid4().hex[:8]}"
    )

    replay = receive_quantity(
        **_request(receipt_objects, operation_id=operation_id)
    )

    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("inactive_material", INACTIVE_MATERIAL),
        ("serialized_material", TRACKING_MODE_MISMATCH),
        ("unit_mismatch", UNIT_MISMATCH),
        ("inactive_condition", INACTIVE_CONDITION),
        ("inactive_location", INVALID_DESTINATION),
        ("nonholding_location", INVALID_DESTINATION),
    ],
)
def test_new_receipt_validates_locked_master_state(receipt_objects, mutation, code):
    request = _request(receipt_objects)
    if mutation == "inactive_material":
        Material.objects.filter(pk=receipt_objects["material"].pk).update(active=False)
    elif mutation == "serialized_material":
        Material.objects.filter(pk=receipt_objects["material"].pk).update(
            tracking_mode=Material.TrackingMode.SERIALIZED
        )
    elif mutation == "unit_mismatch":
        request["unit_id"] = UnitOfMeasure.objects.create(
            code=f"OTHER-{uuid.uuid4().hex[:8]}", name="Başka birim"
        ).pk
    elif mutation == "inactive_condition":
        MaterialCondition.objects.filter(pk=receipt_objects["condition"].pk).update(
            active=False
        )
    elif mutation == "inactive_location":
        Location.objects.filter(pk=receipt_objects["location"].pk).update(active=False)
    else:
        Location.objects.filter(pk=receipt_objects["location"].pk).update(
            can_hold_stock=False
        )

    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(**request)
    _assert_error_code(exc_info, code)
    assert InventoryTransaction.objects.count() == 0
    assert InventoryTransactionLine.objects.count() == 0
    assert StockBalance.objects.count() == 0


@pytest.mark.django_db
def test_null_unit_material_is_rejected_before_inventory_effect(receipt_objects):
    material = Material.objects.create(
        material_code=f"NULL-U-{uuid.uuid4().hex[:8]}",
        name="Birimsiz tekil",
        category=receipt_objects["category"],
        unit=None,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(
            **_request(receipt_objects, material_id=material.pk)
        )
    _assert_error_code(exc_info, TRACKING_MODE_MISMATCH)
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_failure_after_header_and_line_rolls_back_everything(receipt_objects):
    with patch.object(StockBalance, "save", side_effect=RuntimeError("controlled")):
        with pytest.raises(RuntimeError, match="controlled"):
            receive_quantity(**_request(receipt_objects))

    assert InventoryTransaction.objects.count() == 0
    assert InventoryTransactionLine.objects.count() == 0
    assert StockBalance.objects.count() == 0


@pytest.mark.django_db
def test_projection_verifier_is_read_only_and_reports_deterministically(
    receipt_objects,
):
    receive_quantity(**_request(receipt_objects, quantity="2.500"))
    balance = StockBalance.objects.get()
    original_updated_at = balance.updated_at
    assert verify_quantity_projection() == ()

    StockBalance.objects.filter(pk=balance.pk).update(quantity=Decimal("1.000"))
    before_count = StockBalance.objects.count()
    before_updated_at = StockBalance.objects.get(pk=balance.pk).updated_at
    mismatches = verify_quantity_projection()

    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch.material_id == receipt_objects["material"].pk
    assert mismatch.location_id == receipt_objects["location"].pk
    assert mismatch.condition_id == receipt_objects["condition"].pk
    assert mismatch.expected_quantity == Decimal("2.500")
    assert mismatch.actual_quantity == Decimal("1.000")
    assert StockBalance.objects.count() == before_count
    assert StockBalance.objects.get(pk=balance.pk).updated_at == before_updated_at
    assert before_updated_at == original_updated_at


@pytest.mark.django_db
def test_zero_balance_without_ledger_is_not_a_projection_mismatch(receipt_objects):
    StockBalance.objects.create(
        material=receipt_objects["material"],
        location=receipt_objects["location"],
        condition=receipt_objects["condition"],
        quantity=Decimal("0.000"),
    )
    assert verify_quantity_projection() == ()


@pytest.mark.django_db
def test_projection_verifier_treats_missing_balance_as_zero(receipt_objects):
    receive_quantity(**_request(receipt_objects, quantity="4.000"))
    StockBalance.objects.all().delete()

    mismatch = verify_quantity_projection()[0]

    assert mismatch.expected_quantity == Decimal("4.000")
    assert mismatch.actual_quantity == Decimal("0.000")
    assert StockBalance.objects.count() == 0


@pytest.mark.django_db
def test_receive_stock_authorization_uses_fresh_database_state(receipt_objects):
    actor = receipt_objects["actor"]
    actor.has_perm(RECEIVE_STOCK_PERMISSION)
    actor.user_permissions.clear()

    with pytest.raises(PermissionDenied):
        receive_quantity(**_request(receipt_objects, actor=actor))
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_authorization_boundaries(receipt_objects):
    suffix = uuid.uuid4().hex[:8]
    unauthorized = User.objects.create_user(username=f"unauth-{suffix}")
    inactive = _grant_receive_stock(
        User.objects.create_user(username=f"inactive-{suffix}", is_active=False)
    )
    staff = User.objects.create_user(username=f"staff-{suffix}", is_staff=True)
    named_group, _ = Group.objects.get_or_create(name="STOREKEEPER")
    group_named = User.objects.create_user(username=f"group-{suffix}")
    group_named.groups.add(named_group)

    for actor in (unauthorized, inactive, staff, group_named):
        with pytest.raises(PermissionDenied):
            receive_quantity(**_request(receipt_objects, actor=actor))

    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_unsaved_actor_and_malformed_operation_id_are_cleanly_rejected(
    receipt_objects,
):
    unsaved = User(username=f"unsaved-{uuid.uuid4().hex[:8]}")
    with pytest.raises(PermissionDenied):
        receive_quantity(**_request(receipt_objects, actor=unsaved))

    with pytest.raises(ValidationError) as exc_info:
        receive_quantity(
            **_request(receipt_objects, operation_id="not-a-uuid")
        )
    _assert_error_code(exc_info, "inventory.invalid_operation_id")
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_superuser_follows_django_permission_semantics(receipt_objects):
    superuser = User.objects.create_superuser(
        username=f"super-{uuid.uuid4().hex[:8]}", password="test-only"
    )
    result = receive_quantity(**_request(receipt_objects, actor=superuser))
    assert result.transaction.acting_user_id == superuser.pk


@pytest.mark.django_db
def test_actor_must_belong_to_requested_database_alias(receipt_objects):
    receipt_objects["actor"]._state.db = "other"
    with pytest.raises(PermissionDenied):
        receive_quantity(**_request(receipt_objects))
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_permission_is_defined_but_not_managed_or_in_default_groups():
    permission = Permission.objects.get(
        content_type__app_label="inventory",
        content_type__model="inventorytransaction",
        codename="receive_stock",
    )
    assert permission.name == "Can receive stock"
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 18
    assert RECEIVE_STOCK_PERMISSION not in SAFE_CATALOG_PERMISSION_LABELS
    assert all(
        "receive_stock" not in permissions
        for permissions in DEFAULT_ROLE_TEMPLATES.values()
    )
    assert not Group.objects.filter(permissions=permission).exists()
