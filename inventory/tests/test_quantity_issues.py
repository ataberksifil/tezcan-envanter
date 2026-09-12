from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
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
from inventory.services.issues import (
    INACTIVE_EMPLOYEE,
    INACTIVE_PRODUCTION_LINE,
    INSUFFICIENT_STOCK,
    INVALID_SOURCE,
    INVALID_USAGE_LOCATION,
    ISSUE_STOCK_PERMISSION,
    issue_quantity,
)
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import (
    INACTIVE_CONDITION,
    INACTIVE_MATERIAL,
    INVALID_QUANTITY,
    OPERATION_CONFLICT,
    TRACKING_MODE_MISMATCH,
    UNIT_MISMATCH,
)
from locations.models import Location

User = get_user_model()


def _issue_permission():
    return Permission.objects.get(
        content_type__app_label="inventory",
        codename="issue_stock",
    )


def _grant_issue_stock(user):
    user.user_permissions.add(_issue_permission())
    return User.objects.get(pk=user.pk)


@pytest.fixture
def issue_objects(db):
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"ISS-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"Çıkış kategori {suffix}")
    material = Material.objects.create(
        material_code=f"ISS-M-{suffix}",
        name="Çıkış malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"ISS-C-{suffix}", name="Çıkış kondisyonu", sort_order=700
    )
    location = Location.objects.create(
        code=f"ISS-L-{suffix}",
        name="Çıkış rafı",
        active=True,
        can_hold_stock=True,
    )
    employee = Employee.objects.create(
        employee_number=f"ISS-E-{suffix}",
        first_name="Ayşe",
        last_name="Yılmaz",
    )
    production_line = ProductionLine.objects.create(
        code=f"ISS-PL-{suffix}", name="Montaj Hattı"
    )
    actor = _grant_issue_stock(User.objects.create_user(username=f"issuer-{suffix}"))
    balance = StockBalance.objects.create(
        material=material,
        location=location,
        condition=condition,
        quantity=Decimal("5.000"),
    )
    return {
        "unit": unit,
        "category": category,
        "material": material,
        "condition": condition,
        "location": location,
        "employee": employee,
        "production_line": production_line,
        "actor": actor,
        "balance": balance,
    }


def _request(objects, **overrides):
    values = {
        "actor": objects["actor"],
        "operation_id": uuid.uuid4(),
        "material_id": objects["material"].pk,
        "unit_id": objects["unit"].pk,
        "condition_id": objects["condition"].pk,
        "source_location_id": objects["location"].pk,
        "quantity": Decimal("1.250"),
        "receiver_employee_id": objects["employee"].pk,
        "production_line_id": objects["production_line"].pk,
        "usage_location_text": "  Pano  7 / Sol  ",
    }
    values.update(overrides)
    return values


def _assert_code(exc_info, code):
    assert exc_info.value.code == code


@pytest.mark.django_db
def test_issue_decrements_exact_bucket_and_creates_snapshot(issue_objects):
    audit_count = AuditEvent.objects.count()
    result = issue_quantity(**_request(issue_objects))

    assert result.replayed is False
    assert result.transaction.transaction_type == "ISSUE"
    assert result.transaction.acting_user_id == issue_objects["actor"].pk
    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.line_number == 1
    assert line.quantity == Decimal("1.250")
    assert line.source_location_id == issue_objects["location"].pk
    assert line.target_location_id is None
    context = result.issue_context
    assert context is not None
    assert context.transaction_id == result.transaction.pk
    assert context.receiver_employee_id == issue_objects["employee"].pk
    assert context.receiver_first_name_snapshot == "Ayşe"
    assert context.receiver_last_name_snapshot == "Yılmaz"
    assert context.receiver_employee_number_snapshot == issue_objects["employee"].employee_number
    assert context.production_line_id == issue_objects["production_line"].pk
    assert context.production_line_code_snapshot == issue_objects["production_line"].code
    assert context.production_line_name_snapshot == "Montaj Hattı"
    assert context.usage_location_text == "Pano  7 / Sol"
    issue_objects["balance"].refresh_from_db()
    assert issue_objects["balance"].quantity == Decimal("3.750")
    assert InventoryTransaction.objects.count() == 1
    assert InventoryTransactionLine.objects.count() == 1
    assert IssueContext.objects.count() == 1
    assert AuditEvent.objects.count() == audit_count

    payload = {
        "acting_user_id": str(issue_objects["actor"].pk),
        "condition_id": str(issue_objects["condition"].pk),
        "material_id": str(issue_objects["material"].pk),
        "production_line_id": str(issue_objects["production_line"].pk),
        "quantity": "1.250",
        "receiver_employee_id": str(issue_objects["employee"].pk),
        "source_location_id": str(issue_objects["location"].pk),
        "transaction_type": "ISSUE",
        "unit_id": str(issue_objects["unit"].pk),
        "usage_location_text": "Pano  7 / Sol",
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert result.transaction.request_fingerprint == expected


@pytest.mark.django_db
def test_full_issue_retains_zero_balance_row(issue_objects):
    result = issue_quantity(**_request(issue_objects, quantity="5"))
    issue_objects["balance"].refresh_from_db()
    assert result.lines[0].quantity == Decimal("5.000")
    assert issue_objects["balance"].quantity == Decimal("0.000")
    assert StockBalance.objects.filter(pk=issue_objects["balance"].pk).exists()


@pytest.mark.django_db
def test_issue_uses_only_exact_condition_and_location(issue_objects):
    other_condition = MaterialCondition.objects.create(
        code=f"OTHER-{uuid.uuid4().hex[:8]}", name="Diğer", sort_order=701
    )
    other_location = Location.objects.create(
        code=f"OTHER-{uuid.uuid4().hex[:8]}", name="Diğer", can_hold_stock=True
    )
    other_condition_balance = StockBalance.objects.create(
        material=issue_objects["material"], location=issue_objects["location"],
        condition=other_condition, quantity=Decimal("50")
    )
    other_location_balance = StockBalance.objects.create(
        material=issue_objects["material"], location=other_location,
        condition=issue_objects["condition"], quantity=Decimal("50")
    )
    issue_objects["balance"].quantity = Decimal("1")
    issue_objects["balance"].save()

    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, quantity="2"))
    _assert_code(exc_info, INSUFFICIENT_STOCK)
    other_condition_balance.refresh_from_db()
    other_location_balance.refresh_from_db()
    assert other_condition_balance.quantity == Decimal("50")
    assert other_location_balance.quantity == Decimal("50")
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("quantity", [1.0, True, "1.2345", "0", "-1"])
def test_issue_rejects_invalid_quantity(issue_objects, quantity):
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, quantity=quantity))
    _assert_code(exc_info, INVALID_QUANTITY)


@pytest.mark.django_db
@pytest.mark.parametrize("usage", ["   ", "", None, 123])
def test_issue_rejects_invalid_usage_location(issue_objects, usage):
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, usage_location_text=usage))
    _assert_code(exc_info, INVALID_USAGE_LOCATION)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("object_key", "attribute", "value", "expected_code"),
    [
        ("material", "active", False, INACTIVE_MATERIAL),
        ("material", "tracking_mode", "SERIALIZED", TRACKING_MODE_MISMATCH),
        ("condition", "active", False, INACTIVE_CONDITION),
        ("location", "active", False, INVALID_SOURCE),
        ("location", "can_hold_stock", False, INVALID_SOURCE),
        ("employee", "active", False, INACTIVE_EMPLOYEE),
        ("production_line", "active", False, INACTIVE_PRODUCTION_LINE),
    ],
)
def test_issue_rejects_inactive_or_invalid_master(
    issue_objects, object_key, attribute, value, expected_code
):
    if object_key in {"material", "location"}:
        issue_objects["balance"].delete()
    model = issue_objects[object_key]
    setattr(model, attribute, value)
    model.save()
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects))
    _assert_code(exc_info, expected_code)
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_issue_rejects_wrong_unit_missing_and_insufficient_balance(issue_objects):
    other_unit = UnitOfMeasure.objects.create(
        code=f"WRONG-{uuid.uuid4().hex[:8]}", name="Yanlış"
    )
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, unit_id=other_unit.pk))
    _assert_code(exc_info, UNIT_MISMATCH)

    issue_objects["balance"].delete()
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects))
    _assert_code(exc_info, INSUFFICIENT_STOCK)
    assert StockBalance.objects.count() == 0

    issue_objects["balance"] = StockBalance.objects.create(
        material=issue_objects["material"], location=issue_objects["location"],
        condition=issue_objects["condition"], quantity=Decimal("0")
    )
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects))
    _assert_code(exc_info, INSUFFICIENT_STOCK)
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_issue_rejects_malformed_operation_id(issue_objects):
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, operation_id="bad"))
    _assert_code(exc_info, "inventory.invalid_operation_id")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("material_id", INACTIVE_MATERIAL),
        ("condition_id", INACTIVE_CONDITION),
        ("source_location_id", INVALID_SOURCE),
        ("receiver_employee_id", INACTIVE_EMPLOYEE),
        ("production_line_id", INACTIVE_PRODUCTION_LINE),
    ],
)
def test_issue_rejects_missing_master(issue_objects, field, expected_code):
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, **{field: uuid.uuid4()}))
    _assert_code(exc_info, expected_code)
    assert InventoryTransaction.objects.count() == 0


@pytest.mark.django_db
def test_option_b_authorization_and_replay_revocation(issue_objects):
    ordinary = User.objects.create_user(username=f"ordinary-{uuid.uuid4().hex[:8]}")
    staff = User.objects.create_user(username=f"staff-{uuid.uuid4().hex[:8]}", is_staff=True)
    named = User.objects.create_user(username=f"named-{uuid.uuid4().hex[:8]}")
    group, _ = Group.objects.get_or_create(name="TECHNICIAN")
    named.groups.add(group)
    inactive = _grant_issue_stock(
        User.objects.create_user(
            username=f"inactive-{uuid.uuid4().hex[:8]}", is_active=False
        )
    )
    for actor in (ordinary, staff, named, inactive):
        with pytest.raises(PermissionDenied):
            issue_quantity(**_request(issue_objects, actor=actor))

    operation_id = uuid.uuid4()
    first = issue_quantity(**_request(issue_objects, operation_id=operation_id))
    issue_objects["actor"].user_permissions.clear()
    with pytest.raises(PermissionDenied):
        issue_quantity(**_request(issue_objects, operation_id=operation_id))
    assert InventoryTransaction.objects.count() == 1
    assert first.replayed is False


@pytest.mark.django_db
def test_superuser_uses_django_permission_semantics(issue_objects):
    superuser = User.objects.create_superuser(
        username=f"super-{uuid.uuid4().hex[:8]}", password="test-only"
    )
    result = issue_quantity(**_request(issue_objects, actor=superuser))
    assert result.transaction.acting_user_id == superuser.pk


@pytest.mark.django_db
def test_issue_replay_is_stable_after_master_changes(issue_objects):
    operation_id = uuid.uuid4()
    first = issue_quantity(**_request(issue_objects, operation_id=operation_id))
    original_context_id = first.issue_context.pk

    Employee.objects.filter(pk=issue_objects["employee"].pk).update(
        first_name="Değişti", active=False
    )
    ProductionLine.objects.filter(pk=issue_objects["production_line"].pk).update(
        name="Değişti", active=False
    )
    Material.objects.filter(pk=issue_objects["material"].pk).update(active=False)
    MaterialCondition.objects.filter(pk=issue_objects["condition"].pk).update(active=False)
    StockBalance.objects.filter(pk=issue_objects["balance"].pk).update(quantity=Decimal("0"))

    replay = issue_quantity(**_request(issue_objects, operation_id=operation_id))
    assert replay.replayed is True
    assert replay.transaction.pk == first.transaction.pk
    assert replay.issue_context.pk == original_context_id
    assert replay.issue_context.receiver_first_name_snapshot == "Ayşe"
    assert replay.issue_context.production_line_name_snapshot == "Montaj Hattı"
    assert InventoryTransaction.objects.count() == 1
    assert IssueContext.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("quantity", Decimal("2")),
        ("material_id", uuid.uuid4()),
        ("unit_id", uuid.uuid4()),
        ("condition_id", uuid.uuid4()),
        ("source_location_id", uuid.uuid4()),
        ("receiver_employee_id", uuid.uuid4()),
        ("production_line_id", uuid.uuid4()),
        ("usage_location_text", "Başka yer"),
    ],
)
def test_issue_operation_conflicts_on_semantic_payload_change(issue_objects, field, new_value):
    operation_id = uuid.uuid4()
    issue_quantity(**_request(issue_objects, operation_id=operation_id))
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, operation_id=operation_id, **{field: new_value}))
    _assert_code(exc_info, OPERATION_CONFLICT)


@pytest.mark.django_db
def test_issue_actor_change_conflicts(issue_objects):
    operation_id = uuid.uuid4()
    issue_quantity(**_request(issue_objects, operation_id=operation_id))
    other = _grant_issue_stock(User.objects.create_user(username=f"other-{uuid.uuid4().hex[:8]}"))
    with pytest.raises(ValidationError) as exc_info:
        issue_quantity(**_request(issue_objects, operation_id=operation_id, actor=other))
    _assert_code(exc_info, OPERATION_CONFLICT)


@pytest.mark.django_db
def test_issue_atomicity_on_context_and_balance_failure(issue_objects):
    with patch.object(IssueContext, "save", side_effect=RuntimeError("context")):
        with pytest.raises(RuntimeError, match="context"):
            issue_quantity(**_request(issue_objects))
    assert InventoryTransaction.objects.count() == 0
    assert InventoryTransactionLine.objects.count() == 0
    assert IssueContext.objects.count() == 0
    issue_objects["balance"].refresh_from_db()
    assert issue_objects["balance"].quantity == Decimal("5")

    with patch.object(StockBalance, "save", side_effect=RuntimeError("balance")):
        with pytest.raises(RuntimeError, match="balance"):
            issue_quantity(**_request(issue_objects))
    assert InventoryTransaction.objects.count() == 0
    assert InventoryTransactionLine.objects.count() == 0
    assert IssueContext.objects.count() == 0
    issue_objects["balance"].refresh_from_db()
    assert issue_objects["balance"].quantity == Decimal("5")


@pytest.mark.django_db
def test_projection_verifier_subtracts_issue_and_keeps_negative_corruption_visible(issue_objects):
    # Seed the authoritative receipt ledger matching the pre-existing projection.
    receipt = InventoryTransaction.objects.create(
        operation_id=uuid.uuid4(), request_fingerprint="a" * 64,
        transaction_type="RECEIPT", acting_user=issue_objects["actor"],
        occurred_at=issue_objects["balance"].updated_at,
    )
    InventoryTransactionLine.objects.create(
        transaction=receipt, line_number=1, material=issue_objects["material"],
        quantity=Decimal("5"), unit=issue_objects["unit"], condition=issue_objects["condition"],
        source_location=None, target_location=issue_objects["location"],
    )
    issue_quantity(**_request(issue_objects, quantity="5"))
    assert verify_quantity_projection() == ()

    StockBalance.objects.filter(pk=issue_objects["balance"].pk).update(quantity=Decimal("1"))
    mismatch = verify_quantity_projection()[0]
    assert mismatch.expected_quantity == Decimal("0")
    assert mismatch.actual_quantity == Decimal("1")

    extra_issue = InventoryTransaction.objects.create(
        operation_id=uuid.uuid4(), request_fingerprint="b" * 64,
        transaction_type="ISSUE", acting_user=issue_objects["actor"],
        occurred_at=issue_objects["balance"].updated_at,
    )
    InventoryTransactionLine.objects.create(
        transaction=extra_issue, line_number=1, material=issue_objects["material"],
        quantity=Decimal("2"), unit=issue_objects["unit"], condition=issue_objects["condition"],
        source_location=issue_objects["location"], target_location=None,
    )
    IssueContext.objects.create(
        transaction=extra_issue, receiver_employee=issue_objects["employee"],
        receiver_first_name_snapshot="Ayşe", receiver_last_name_snapshot="Yılmaz",
        receiver_employee_number_snapshot=issue_objects["employee"].employee_number,
        production_line=issue_objects["production_line"],
        production_line_code_snapshot=issue_objects["production_line"].code,
        production_line_name_snapshot=issue_objects["production_line"].name,
        usage_location_text="Pano",
    )
    mismatch = verify_quantity_projection()[0]
    assert mismatch.expected_quantity == Decimal("-2")


@pytest.mark.django_db
def test_issue_stock_permission_is_canonical_after_migration():
    assert ISSUE_STOCK_PERMISSION in SAFE_CATALOG_PERMISSION_LABELS
    assert "issue_stock" in dict(InventoryTransaction._meta.permissions)
