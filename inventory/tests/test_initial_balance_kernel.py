from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from accounts.models import Employee
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.services.baselines import (
    ESTABLISH_BASELINE_PERMISSION,
    QuantityOpening,
    SerializedOpening,
    establish_initial_balance,
)
from inventory.services.issues import issue_serialized
from inventory.services.projections import (
    verify_quantity_projection,
    verify_serialized_projection,
)
from inventory.services.receipts import receive_quantity, receive_serialized
from locations.models import Location


pytestmark = pytest.mark.django_db


def _grant_establish(user):
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="imports",
            content_type__model="inventorybaseline",
            codename="establish_baseline",
        )
    )
    return type(user).objects.get(pk=user.pk)


@pytest.fixture
def kernel_objects():
    suffix = uuid.uuid4().hex[:8]
    user = get_user_model().objects.create_user(username=f"ib-{suffix}")
    user = _grant_establish(user)
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename="receive_stock",
        )
    )
    user = get_user_model().objects.get(pk=user.pk)
    category = Category.objects.create(name=f"IB kat {suffix}")
    unit = UnitOfMeasure.objects.create(code=f"IB-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"IB-Q-{suffix}",
        name="Miktar",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    serialized_material = Material.objects.create(
        material_code=f"IB-S-{suffix}",
        name="Tekil",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    other_serialized = Material.objects.create(
        material_code=f"IB-S2-{suffix}",
        name="Diğer tekil",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"IB-C-{suffix}", name="Yeni", sort_order=840
    )
    location = Location.objects.create(
        code=f"IB-L-{suffix}", name="Raf", active=True, can_hold_stock=True
    )
    return {
        "user": user,
        "category": category,
        "unit": unit,
        "material": material,
        "serialized_material": serialized_material,
        "other_serialized": other_serialized,
        "condition": condition,
        "location": location,
    }


def test_permission_is_required(kernel_objects):
    actor = get_user_model().objects.create_user(username=f"ib-noperm-{uuid.uuid4().hex[:6]}")
    with pytest.raises(PermissionDenied):
        establish_initial_balance(
            actor=actor,
            operation_id=uuid.uuid4(),
            quantity_openings=[
                QuantityOpening(
                    material_id=kernel_objects["material"].pk,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                    quantity=Decimal("2.000"),
                )
            ],
        )


def test_quantity_opening_writes_target_only_line_and_projection(kernel_objects):
    result = establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        quantity_openings=[
            QuantityOpening(
                material_id=kernel_objects["material"].pk,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
                quantity=Decimal("3.500"),
            )
        ],
    )
    line = result.lines[0]
    assert result.transaction.transaction_type == "INITIAL_BALANCE"
    assert line.source_location_id is None
    assert line.target_location_id == kernel_objects["location"].pk
    assert line.original_issue_line_id is None
    assert line.corrected_line_id is None
    assert line.serialized_asset_id is None
    assert line.quantity == Decimal("3.500")
    assert line.unit_id == kernel_objects["material"].unit_id
    balance = StockBalance.objects.get(
        material=kernel_objects["material"],
        location=kernel_objects["location"],
        condition=kernel_objects["condition"],
    )
    assert balance.quantity == Decimal("3.500")
    assert verify_quantity_projection() == ()


def test_quantity_opening_rejects_prior_history(kernel_objects):
    receive_quantity(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=kernel_objects["material"].pk,
        unit_id=kernel_objects["unit"].pk,
        condition_id=kernel_objects["condition"].pk,
        target_location_id=kernel_objects["location"].pk,
        quantity=Decimal("1.000"),
    )
    with pytest.raises(ValidationError, match="Ledger geçmişi"):
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
            quantity_openings=[
                QuantityOpening(
                    material_id=kernel_objects["material"].pk,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                    quantity=Decimal("2.000"),
                )
            ],
        )


def test_quantity_opening_rejects_orphan_positive_balance(kernel_objects):
    StockBalance.objects.create(
        material=kernel_objects["material"],
        location=kernel_objects["location"],
        condition=kernel_objects["condition"],
        quantity=Decimal("4.000"),
    )
    with pytest.raises(ValidationError) as exc:
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
            quantity_openings=[
                QuantityOpening(
                    material_id=kernel_objects["material"].pk,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                    quantity=Decimal("4.000"),
                )
            ],
        )
    assert exc.value.code == "inventory.projection_integrity"


def test_quantity_opening_rejects_non_positive_quantity(kernel_objects):
    with pytest.raises(ValidationError, match="sıfırdan büyük"):
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
            quantity_openings=[
                QuantityOpening(
                    material_id=kernel_objects["material"].pk,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                    quantity=Decimal("0"),
                )
            ],
        )


def test_empty_openings_rejected(kernel_objects):
    with pytest.raises(ValidationError, match="en az bir açılış"):
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
        )


def test_serialized_opening_promotes_canonical_identity(kernel_objects):
    result = establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        serialized_openings=[
            SerializedOpening(
                material_id=kernel_objects["serialized_material"].pk,
                internal_asset_code="  AS-1  ",
                serial_number="  ",
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
            )
        ],
    )
    line = result.lines[0]
    asset = SerializedAsset.objects.get(internal_asset_code="AS-1")
    assert asset.serial_number is None
    assert asset.current_location_id == kernel_objects["location"].pk
    assert asset.current_condition_id == kernel_objects["condition"].pk
    assert asset.current_state == SerializedAsset.CurrentState.IN_STOCK
    assert line.serialized_asset_id == asset.pk
    assert line.quantity is None
    assert line.unit_id is None
    assert line.source_location_id is None
    assert line.target_location_id == kernel_objects["location"].pk
    assert line.asset_event_seq == 1
    assert verify_serialized_projection() == ()


def test_internal_codes_are_case_sensitive(kernel_objects):
    establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        serialized_openings=[
            SerializedOpening(
                material_id=kernel_objects["serialized_material"].pk,
                internal_asset_code="AS-1",
                serial_number=None,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
            )
        ],
    )
    establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        serialized_openings=[
            SerializedOpening(
                material_id=kernel_objects["serialized_material"].pk,
                internal_asset_code="as-1",
                serial_number=None,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
            )
        ],
    )
    assert SerializedAsset.objects.filter(internal_asset_code="AS-1").count() == 1
    assert SerializedAsset.objects.filter(internal_asset_code="as-1").count() == 1


def test_non_null_serial_is_unique_per_material(kernel_objects):
    establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        serialized_openings=[
            SerializedOpening(
                material_id=kernel_objects["serialized_material"].pk,
                internal_asset_code="CODE-A",
                serial_number="SN-1",
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
            )
        ],
    )
    with pytest.raises(ValidationError, match="seri numarası"):
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
            serialized_openings=[
                SerializedOpening(
                    material_id=kernel_objects["serialized_material"].pk,
                    internal_asset_code="CODE-B",
                    serial_number="SN-1",
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                )
            ],
        )
    establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        serialized_openings=[
            SerializedOpening(
                material_id=kernel_objects["other_serialized"].pk,
                internal_asset_code="CODE-C",
                serial_number="SN-1",
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
            )
        ],
    )


def test_internal_code_collision_with_receive_is_rejected(kernel_objects):
    receive_serialized(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        material_id=kernel_objects["serialized_material"].pk,
        internal_asset_code="SHARED-CODE",
        serial_number=None,
        condition_id=kernel_objects["condition"].pk,
        target_location_id=kernel_objects["location"].pk,
    )
    with pytest.raises(ValidationError, match="Dahili varlık kodu"):
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
            serialized_openings=[
                SerializedOpening(
                    material_id=kernel_objects["serialized_material"].pk,
                    internal_asset_code="SHARED-CODE",
                    serial_number=None,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                )
            ],
        )


def test_combined_quantity_and_serialized_lines_are_ordered(kernel_objects):
    result = establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        quantity_openings=[
            QuantityOpening(
                material_id=kernel_objects["material"].pk,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
                quantity=Decimal("1.000"),
            )
        ],
        serialized_openings=[
            SerializedOpening(
                material_id=kernel_objects["serialized_material"].pk,
                internal_asset_code="COMB-1",
                serial_number=None,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
            )
        ],
    )
    assert result.lines[0].serialized_asset_id is None
    assert result.lines[1].serialized_asset_id is not None
    assert result.lines[0].line_number == 1
    assert result.lines[1].line_number == 2
    assert verify_quantity_projection() == ()
    assert verify_serialized_projection() == ()


def test_idempotent_replay_same_payload(kernel_objects):
    operation_id = uuid.uuid4()
    opening = QuantityOpening(
        material_id=kernel_objects["material"].pk,
        location_id=kernel_objects["location"].pk,
        condition_id=kernel_objects["condition"].pk,
        quantity=Decimal("2.000"),
    )
    first = establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=operation_id,
        quantity_openings=[opening],
    )
    second = establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=operation_id,
        quantity_openings=[opening],
    )
    assert second.replayed is True
    assert second.transaction.pk == first.transaction.pk
    assert InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count() == 1
    assert StockBalance.objects.get().quantity == Decimal("2.000")


def test_same_operation_different_payload_conflicts(kernel_objects):
    operation_id = uuid.uuid4()
    establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=operation_id,
        quantity_openings=[
            QuantityOpening(
                material_id=kernel_objects["material"].pk,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
                quantity=Decimal("2.000"),
            )
        ],
    )
    with pytest.raises(ValidationError) as exc:
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=operation_id,
            quantity_openings=[
                QuantityOpening(
                    material_id=kernel_objects["material"].pk,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                    quantity=Decimal("3.000"),
                )
            ],
        )
    assert exc.value.code == "inventory.operation_conflict"


def test_issue_context_cannot_attach_to_initial_balance(kernel_objects):
    from accounts.models import Employee
    from inventory.models import ProductionLine

    result = establish_initial_balance(
        actor=kernel_objects["user"],
        operation_id=uuid.uuid4(),
        quantity_openings=[
            QuantityOpening(
                material_id=kernel_objects["material"].pk,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
                quantity=Decimal("1.000"),
            )
        ],
    )
    employee = Employee.objects.create(
        employee_number=f"IB-E-{uuid.uuid4().hex[:6]}",
        first_name="Ali",
        last_name="Kaya",
    )
    production_line = ProductionLine.objects.create(
        code=f"IB-PL-{uuid.uuid4().hex[:6]}", name="Hat"
    )
    with pytest.raises(IntegrityError, match="IssueContext requires an ISSUE"):
        with transaction.atomic():
            IssueContext.objects.create(
                transaction=result.transaction,
                receiver_employee=employee,
                receiver_first_name_snapshot="Ali",
                receiver_last_name_snapshot="Kaya",
                receiver_employee_number_snapshot=employee.employee_number,
                production_line=production_line,
                production_line_code_snapshot=production_line.code,
                production_line_name_snapshot=production_line.name,
                usage_location_text="Hat kenarı",
            )


def test_raw_sql_rejects_source_on_initial_balance(kernel_objects):
    with connection.cursor() as cursor:
        with pytest.raises(Exception, match="target-only|source"):
            with transaction.atomic():
                header = InventoryTransaction.objects.create(
                    operation_id=uuid.uuid4(),
                    request_fingerprint="b" * 64,
                    transaction_type=InventoryTransaction.TransactionType.INITIAL_BALANCE,
                    acting_user=kernel_objects["user"],
                    occurred_at="2026-09-16T08:00:00+03:00",
                )
                cursor.execute(
                    """
                    INSERT INTO inventory_inventorytransactionline (
                        id, transaction_id, line_number, material_id, quantity, unit_id,
                        condition_id, source_location_id, target_location_id, created_at
                    ) VALUES (
                        %s, %s, 1, %s, 1.000, %s, %s, %s, %s, NOW()
                    )
                    """,
                    [
                        uuid.uuid4(),
                        header.pk,
                        kernel_objects["material"].pk,
                        kernel_objects["unit"].pk,
                        kernel_objects["condition"].pk,
                        kernel_objects["location"].pk,
                        kernel_objects["location"].pk,
                    ],
                )


def test_raw_sql_rejects_noncanonical_asset_whitespace(kernel_objects):
    with connection.cursor() as cursor:
        with pytest.raises(Exception):
            with transaction.atomic():
                cursor.execute(
                    """
                    INSERT INTO inventory_serializedasset (
                        id, material_id, internal_asset_code, serial_number,
                        current_location_id, current_condition_id, current_state,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, NULL, %s, %s, 'IN_STOCK', NOW(), NOW()
                    )
                    """,
                    [
                        uuid.uuid4(),
                        kernel_objects["serialized_material"].pk,
                        "  AS-WS  ",
                        kernel_objects["location"].pk,
                        kernel_objects["condition"].pk,
                    ],
                )
        with pytest.raises(Exception):
            with transaction.atomic():
                cursor.execute(
                    """
                    INSERT INTO inventory_serializedasset (
                        id, material_id, internal_asset_code, serial_number,
                        current_location_id, current_condition_id, current_state,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'IN_STOCK', NOW(), NOW()
                    )
                    """,
                    [
                        uuid.uuid4(),
                        kernel_objects["serialized_material"].pk,
                        "AS-SN",
                        "  ",
                        kernel_objects["location"].pk,
                        kernel_objects["condition"].pk,
                    ],
                )
        with pytest.raises(Exception):
            with transaction.atomic():
                cursor.execute(
                    """
                    INSERT INTO inventory_serializedasset (
                        id, material_id, internal_asset_code, serial_number,
                        current_location_id, current_condition_id, current_state,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'IN_STOCK', NOW(), NOW()
                    )
                    """,
                    [
                        uuid.uuid4(),
                        kernel_objects["serialized_material"].pk,
                        "AS-SN2",
                        " SN-1 ",
                        kernel_objects["location"].pk,
                        kernel_objects["condition"].pk,
                    ],
                )
        for blank_code in ("", "   "):
            with pytest.raises(Exception):
                with transaction.atomic():
                    cursor.execute(
                        """
                        INSERT INTO inventory_serializedasset (
                            id, material_id, internal_asset_code, serial_number,
                            current_location_id, current_condition_id, current_state,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, NULL, %s, %s, 'IN_STOCK', NOW(), NOW()
                        )
                        """,
                        [
                            uuid.uuid4(),
                            kernel_objects["serialized_material"].pk,
                            blank_code,
                            kernel_objects["location"].pk,
                            kernel_objects["condition"].pk,
                        ],
                    )


def test_initial_balance_rejects_asset_material_mismatch(kernel_objects):
    asset = SerializedAsset.objects.create(
        material=kernel_objects["serialized_material"],
        internal_asset_code=f"MM-{uuid.uuid4().hex[:6]}",
        current_location=kernel_objects["location"],
        current_condition=kernel_objects["condition"],
    )
    with pytest.raises(IntegrityError, match="must match asset material"):
        with transaction.atomic():
            header = InventoryTransaction.objects.create(
                operation_id=uuid.uuid4(),
                request_fingerprint="d" * 64,
                transaction_type=InventoryTransaction.TransactionType.INITIAL_BALANCE,
                acting_user=kernel_objects["user"],
                occurred_at="2026-09-16T08:00:00+03:00",
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["other_serialized"],
                serialized_asset=asset,
                quantity=None,
                unit=None,
                condition=kernel_objects["condition"],
                target_location=kernel_objects["location"],
                asset_event_seq=1,
            )


def test_wrong_tracking_mode_openings_are_rejected(kernel_objects):
    with pytest.raises(ValidationError):
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
            quantity_openings=[
                QuantityOpening(
                    material_id=kernel_objects["serialized_material"].pk,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                    quantity=Decimal("1.000"),
                )
            ],
        )
    with pytest.raises(ValidationError):
        establish_initial_balance(
            actor=kernel_objects["user"],
            operation_id=uuid.uuid4(),
            serialized_openings=[
                SerializedOpening(
                    material_id=kernel_objects["material"].pk,
                    internal_asset_code="WRONG-MODE",
                    serial_number=None,
                    location_id=kernel_objects["location"].pk,
                    condition_id=kernel_objects["condition"].pk,
                )
            ],
        )


def test_raw_sql_rejects_quantity_serialized_mix_on_initial_balance(kernel_objects):
    asset = SerializedAsset.objects.create(
        material=kernel_objects["serialized_material"],
        internal_asset_code=f"MIX-{uuid.uuid4().hex[:6]}",
        current_location=kernel_objects["location"],
        current_condition=kernel_objects["condition"],
    )
    with pytest.raises(Exception):
        with transaction.atomic():
            header = InventoryTransaction.objects.create(
                operation_id=uuid.uuid4(),
                request_fingerprint="e" * 64,
                transaction_type=InventoryTransaction.TransactionType.INITIAL_BALANCE,
                acting_user=kernel_objects["user"],
                occurred_at="2026-09-16T08:00:00+03:00",
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=kernel_objects["serialized_material"],
                serialized_asset=asset,
                quantity=Decimal("1.000"),
                unit=kernel_objects["unit"],
                condition=kernel_objects["condition"],
                target_location=kernel_objects["location"],
            )


def test_serialized_initial_balance_then_movement_uses_next_event_seq(kernel_objects):
    user = kernel_objects["user"]
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename="issue_stock",
        )
    )
    user = type(user).objects.get(pk=user.pk)
    employee = Employee.objects.create(
        employee_number=f"IB-E-{uuid.uuid4().hex[:6]}",
        first_name="Ayşe",
        last_name="Yılmaz",
    )
    production_line = ProductionLine.objects.create(
        code=f"IB-PL-{uuid.uuid4().hex[:6]}",
        name="Hat",
    )
    result = establish_initial_balance(
        actor=user,
        operation_id=uuid.uuid4(),
        serialized_openings=[
            SerializedOpening(
                material_id=kernel_objects["serialized_material"].pk,
                internal_asset_code=f"IB-SEQ-{uuid.uuid4().hex[:6]}",
                serial_number=None,
                location_id=kernel_objects["location"].pk,
                condition_id=kernel_objects["condition"].pk,
            )
        ],
    )
    asset = result.serialized_asset
    assert result.lines[0].asset_event_seq == 1
    issue = issue_serialized(
        actor=user,
        operation_id=uuid.uuid4(),
        serialized_asset_id=asset.pk,
        source_location_id=kernel_objects["location"].pk,
        condition_id=kernel_objects["condition"].pk,
        receiver_employee_id=employee.pk,
        production_line_id=production_line.pk,
        usage_location_text="Pano 7",
    )
    assert issue.lines[0].asset_event_seq == 2
    assert verify_serialized_projection() == ()


def test_establish_baseline_permission_is_sensitive_and_admin_manager_only():
    from accounts.roles import (
        ADMIN_MANAGER,
        DEFAULT_ROLE_TEMPLATES,
        SAFE_CATALOG_PERMISSION_LABELS,
        STOREKEEPER,
        TECHNICIAN,
    )

    assert ESTABLISH_BASELINE_PERMISSION not in SAFE_CATALOG_PERMISSION_LABELS
    assert "establish_baseline" in DEFAULT_ROLE_TEMPLATES[ADMIN_MANAGER]
    assert "establish_baseline" not in DEFAULT_ROLE_TEMPLATES[TECHNICIAN]
    assert "establish_baseline" not in DEFAULT_ROLE_TEMPLATES[STOREKEEPER]
    assert len(SAFE_CATALOG_PERMISSION_LABELS) == 31
