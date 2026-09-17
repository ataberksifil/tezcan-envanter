from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import InventoryTransactionLine
from inventory.services.issues import issue_serialized
from inventory.services.projections import verify_serialized_projection
from inventory.services.receipts import receive_serialized
from locations.models import Location


INVENTORY_0011 = "0011_initial_balance_kernel"
INVENTORY_0012 = "0012_serialized_inventory_movements"


def _applied(name):
    return MigrationRecorder.Migration.objects.filter(
        app="inventory", name=name
    ).exists()


class SerializedEventSeqMigrationTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.user = get_user_model().objects.create_user(username=f"seq-{suffix}")
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                content_type__model="inventorytransaction",
                codename="receive_stock",
            ),
            Permission.objects.get(
                content_type__app_label="inventory",
                content_type__model="inventorytransaction",
                codename="issue_stock",
            ),
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.category = Category.objects.create(name=f"SEQ kat {suffix}")
        self.unit = UnitOfMeasure.objects.create(code=f"SEQ-U-{suffix}", name="Adet")
        self.material = Material.objects.create(
            material_code=f"SEQ-S-{suffix}",
            name="Tekil",
            category=self.category,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"SEQ-C-{suffix}", name="Yeni", sort_order=880
        )
        self.location = Location.objects.create(
            code=f"SEQ-L-{suffix}",
            name="Raf",
            active=True,
            can_hold_stock=True,
        )

    def _truncate_inventory(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    inventory_baseline_transaction_links,
                    inventory_baseline_count_session_links,
                    inventory_baselines,
                    counting_physicalcountserializedline,
                    counting_physicalcountquantityrejection,
                    counting_physicalcountquantityline,
                    counting_physicalcountsession,
                    corrections_correctionevidence,
                    corrections_correctionrequest,
                    inventory_issuecontext,
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance,
                    inventory_serializedasset
                """
            )

    def _fixture_teardown(self):
        self._truncate_inventory()
        call_command("migrate", verbosity=0)
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk=self.location.pk).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk=self.user.pk).delete()

    def _insert_pre_0012_receipt(self):
        asset_id = uuid.uuid4()
        header_id = uuid.uuid4()
        line_id = uuid.uuid4()
        with transaction.atomic(), connection.cursor() as cursor:
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
                    asset_id,
                    self.material.pk,
                    f"PRE12-{uuid.uuid4().hex[:8]}",
                    self.location.pk,
                    self.condition.pk,
                ],
            )
            cursor.execute(
                """
                INSERT INTO inventory_inventorytransaction (
                    id, operation_id, request_fingerprint, transaction_type,
                    acting_user_id, occurred_at, created_at
                ) VALUES (
                    %s, %s, %s, 'RECEIPT', %s, NOW(), NOW()
                )
                """,
                [header_id, uuid.uuid4(), "a" * 64, self.user.pk],
            )
            cursor.execute(
                """
                INSERT INTO inventory_inventorytransactionline (
                    id, transaction_id, line_number, material_id,
                    serialized_asset_id, quantity, unit_id, condition_id,
                    source_location_id, target_location_id,
                    original_issue_line_id, corrected_line_id, created_at
                ) VALUES (
                    %s, %s, 1, %s, %s, NULL, NULL, %s, NULL, %s, NULL, NULL, NOW()
                )
                """,
                [
                    line_id,
                    header_id,
                    self.material.pk,
                    asset_id,
                    self.condition.pk,
                    self.location.pk,
                ],
            )
        return asset_id, line_id

    def test_forward_0011_to_0012_backfills_genesis_seq(self):
        self._truncate_inventory()
        call_command("migrate", "inventory", INVENTORY_0011, verbosity=0)
        self.assertTrue(_applied(INVENTORY_0011))
        self.assertFalse(_applied(INVENTORY_0012))
        asset_id, line_id = self._insert_pre_0012_receipt()
        call_command("migrate", "inventory", INVENTORY_0012, verbosity=0)
        self.assertTrue(_applied(INVENTORY_0012))
        line = InventoryTransactionLine.objects.get(pk=line_id)
        self.assertEqual(line.asset_event_seq, 1)
        self.assertEqual(line.serialized_asset_id, asset_id)
        self.assertEqual(verify_serialized_projection(), ())

    def test_empty_safe_reverse_and_reapply(self):
        self._truncate_inventory()
        call_command("migrate", "inventory", INVENTORY_0011, verbosity=0)
        call_command("migrate", "inventory", INVENTORY_0012, verbosity=0)
        call_command("migrate", "inventory", INVENTORY_0011, verbosity=0)
        self.assertFalse(_applied(INVENTORY_0012))
        call_command("migrate", "inventory", INVENTORY_0012, verbosity=0)
        self.assertTrue(_applied(INVENTORY_0012))

    def test_reverse_refuses_issue_return_and_transfer_history(self):
        from accounts.models import Employee
        from inventory.models import ProductionLine

        employee = Employee.objects.create(
            employee_number=f"SEQ-E-{uuid.uuid4().hex[:6]}",
            first_name="Ayşe",
            last_name="Yılmaz",
        )
        production_line = ProductionLine.objects.create(
            code=f"SEQ-PL-{uuid.uuid4().hex[:6]}", name="Hat"
        )
        asset = receive_serialized(
            actor=self.user,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            internal_asset_code=f"SEQ-A-{uuid.uuid4().hex[:8]}",
            serial_number=None,
            condition_id=self.condition.pk,
            target_location_id=self.location.pk,
        ).serialized_asset
        issue_serialized(
            actor=self.user,
            operation_id=uuid.uuid4(),
            serialized_asset_id=asset.pk,
            source_location_id=self.location.pk,
            condition_id=self.condition.pk,
            receiver_employee_id=employee.pk,
            production_line_id=production_line.pk,
            usage_location_text="Pano 7",
        )
        with self.assertRaises(Exception) as exc:
            call_command("migrate", "inventory", INVENTORY_0011, verbosity=0)
        self.assertIn("cannot reverse serialized movements", str(exc.exception))
        self.assertTrue(_applied(INVENTORY_0012))

    def test_backfill_fails_closed_when_pre_0012_has_multiple_lines(self):
        self._truncate_inventory()
        call_command("migrate", "inventory", INVENTORY_0011, verbosity=0)
        asset_id, _line_id = self._insert_pre_0012_receipt()
        extra_header = uuid.uuid4()
        extra_line = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE inventory_inventorytransactionline "
                "DISABLE TRIGGER inventory_line_quantity_guard_trg"
            )
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO inventory_inventorytransaction (
                    id, operation_id, request_fingerprint, transaction_type,
                    acting_user_id, occurred_at, created_at
                ) VALUES (
                    %s, %s, %s, 'RECEIPT', %s, NOW(), NOW()
                )
                """,
                [extra_header, uuid.uuid4(), "b" * 64, self.user.pk],
            )
            cursor.execute(
                """
                INSERT INTO inventory_inventorytransactionline (
                    id, transaction_id, line_number, material_id,
                    serialized_asset_id, quantity, unit_id, condition_id,
                    source_location_id, target_location_id,
                    original_issue_line_id, corrected_line_id, created_at
                ) VALUES (
                    %s, %s, 1, %s, %s, NULL, NULL, %s, NULL, %s, NULL, NULL, NOW()
                )
                """,
                [
                    extra_line,
                    extra_header,
                    self.material.pk,
                    asset_id,
                    self.condition.pk,
                    self.location.pk,
                ],
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE inventory_inventorytransactionline "
                "ENABLE TRIGGER inventory_line_quantity_guard_trg"
            )
        with self.assertRaises(Exception) as exc:
            call_command("migrate", "inventory", INVENTORY_0012, verbosity=0)
        self.assertIn("exactly one ledger line per asset", str(exc.exception))
        self.assertFalse(_applied(INVENTORY_0012))
        self._truncate_inventory()
        call_command("migrate", verbosity=0)
        self.assertTrue(_applied(INVENTORY_0012))
