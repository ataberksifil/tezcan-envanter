from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import IntegrityError
from django.test import TransactionTestCase

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountQuantityLine, PhysicalCountQuantityRejection
from counting.services import (
    approve_quantity_discrepancy,
    complete_physical_count,
    create_physical_count_session,
    record_quantity_count,
    reject_quantity_discrepancy,
    start_physical_count_session,
)
from inventory.models import InventoryTransaction, StockBalance
from inventory.services.receipts import receive_quantity
from locations.models import Location


COUNTING_0003 = "0003_physicalcountquantityrejection_and_more"
COUNTING_0004 = "0004_serialized_physical_count"
IMPORTS_0001 = "0001_inventory_baseline"


def _counting_0003_applied() -> bool:
    return MigrationRecorder.Migration.objects.filter(
        app="counting", name=COUNTING_0003
    ).exists()


def _counting_0004_applied() -> bool:
    return MigrationRecorder.Migration.objects.filter(
        app="counting", name=COUNTING_0004
    ).exists()


def _imports_0001_applied() -> bool:
    return MigrationRecorder.Migration.objects.filter(
        app="imports", name=IMPORTS_0001
    ).exists()


def _restore_migration_graph() -> None:
    call_command("migrate", verbosity=0)


class CountReconciliationMigrationReverseTests(TransactionTestCase):
    """Fail-closed reverse of uncommitted counting 0003 against the real test DB."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.counter = get_user_model().objects.create_user(username=f"rv-c-{suffix}")
        self.approver = get_user_model().objects.create_user(username=f"rv-a-{suffix}")
        self.approver.user_permissions.add(
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
        self.approver = get_user_model().objects.get(pk=self.approver.pk)
        self.category = Category.objects.create(code=f"RV-CAT-{suffix}", name="Kat")
        self.unit = UnitOfMeasure.objects.create(code=f"RV-U-{suffix}", name="Adet")
        self.material = Material.objects.create(
            material_code=f"RV-M-{suffix}",
            name="Malzeme",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"RV-C-{suffix}", name="Kondisyon", sort_order=910
        )
        self.root = Location.objects.create(code=f"RV-R-{suffix}", name="Kök")
        self.location = Location.objects.create(
            code=f"RV-L-{suffix}",
            name="Raf",
            parent=self.root,
            can_hold_stock=True,
        )

    def _fixture_teardown(self):
        if not _counting_0004_applied() or not _imports_0001_applied():
            _restore_migration_graph()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    audit_auditevent,
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
                    inventory_stockbalance
                """
            )
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk__in=[self.location.pk, self.root.pk]).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(
            pk__in=[self.counter.pk, self.approver.pk]
        ).delete()

    def _clear_counting_history(self):
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
                    counting_physicalcountsession
                """
            )

    def _completed_discrepancy(self, *, expected=Decimal("5.000"), counted=Decimal("7.000")):
        receive_quantity(
            actor=self.approver,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            target_location_id=self.location.pk,
            quantity=expected,
        )
        session = create_physical_count_session(
            actor=self.counter,
            reference_number=f"RV-{uuid.uuid4().hex}",
            scope_location_id=self.root.pk,
        )
        session = start_physical_count_session(
            actor=self.counter, session_id=session.pk
        ).session
        line = record_quantity_count(
            actor=self.counter,
            session_id=session.pk,
            line_id=session.quantity_lines.get().pk,
            counted_quantity=counted,
        )
        complete_physical_count(actor=self.counter, session_id=session.pk)
        return session, line

    @staticmethod
    def _assert_reverse_guard(exc):
        cause = exc.__cause__
        diag = getattr(cause, "diag", None)
        assert "approval/rejection history exists" in str(exc)
        assert getattr(diag, "constraint_name", None) == (
            "counting_reconciliation_reverse_requires_no_data"
        )

    def test_empty_reconciliation_history_can_reverse_counting_0003(self):
        self._clear_counting_history()
        self.assertTrue(_counting_0003_applied())
        call_command("migrate", "counting", "0002", verbosity=0)
        self.assertFalse(_counting_0003_applied())
        _restore_migration_graph()
        self.assertTrue(_counting_0003_applied())
        self.assertTrue(_counting_0004_applied())
        self.assertTrue(_imports_0001_applied())

    def test_rejection_history_blocks_counting_0003_reverse(self):
        session, line = self._completed_discrepancy()
        reject_quantity_discrepancy(
            actor=self.approver,
            session_id=session.pk,
            line_id=line.pk,
            reason="Tarihsel ret kaydı korunmalıdır.",
        )
        rejection_id = PhysicalCountQuantityRejection.objects.get(line=line).pk
        with self.assertRaises(IntegrityError) as exc_info:
            call_command("migrate", "counting", "0002", verbosity=0)
        self._assert_reverse_guard(exc_info.exception)
        self.assertTrue(_counting_0003_applied())
        self.assertTrue(
            PhysicalCountQuantityRejection.objects.filter(pk=rejection_id).exists()
        )
        line.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(line.expected_quantity, Decimal("5.000"))
        self.assertEqual(line.counted_quantity, Decimal("7.000"))
        self.assertEqual(session.status, "STARTED")

    def test_approved_history_blocks_counting_0003_reverse(self):
        session, line = self._completed_discrepancy()
        result = approve_quantity_discrepancy(
            actor=self.approver,
            session_id=session.pk,
            line_id=line.pk,
            operation_id=uuid.uuid4(),
            explanation="Onay geçmişi geri alınamaz.",
        )
        with self.assertRaises(IntegrityError) as exc_info:
            call_command("migrate", "counting", "0002", verbosity=0)
        self._assert_reverse_guard(exc_info.exception)
        self.assertTrue(_counting_0003_applied())
        line.refresh_from_db()
        self.assertEqual(line.resolution_status, "APPROVED")
        self.assertEqual(line.reconciliation_transaction_id, result.transaction.pk)
        self.assertEqual(line.approval_explanation, "Onay geçmişi geri alınamaz.")
        self.assertTrue(
            InventoryTransaction.objects.filter(pk=result.transaction.pk).exists()
        )
        self.assertEqual(result.transaction.lines.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(
                material=self.material,
                location=self.location,
                condition=self.condition,
            ).quantity,
            Decimal("7.000"),
        )


class SerializedCountMigrationReverseTests(TransactionTestCase):
    """Fail-closed reverse of counting 0004 against the real test DB."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_user(username=f"srv-{suffix}")
        self.actor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                content_type__model="inventorytransaction",
                codename="receive_stock",
            )
        )
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self.category = Category.objects.create(code=f"SRV-CAT-{suffix}", name="Kat")
        self.material = Material.objects.create(
            material_code=f"SRV-M-{suffix}",
            name="Tekil malzeme",
            category=self.category,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"SRV-C-{suffix}", name="Kondisyon", sort_order=920
        )
        self.root = Location.objects.create(code=f"SRV-R-{suffix}", name="Kök")
        self.location = Location.objects.create(
            code=f"SRV-L-{suffix}",
            name="Raf",
            parent=self.root,
            can_hold_stock=True,
        )

    def _fixture_teardown(self):
        if not _counting_0004_applied() or not _imports_0001_applied():
            _restore_migration_graph()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    audit_auditevent,
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
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk__in=[self.location.pk, self.root.pk]).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk=self.actor.pk).delete()

    def test_empty_serialized_history_can_reverse_counting_0004(self):
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE counting_physicalcountserializedline")
        self.assertTrue(_counting_0004_applied())
        call_command("migrate", "counting", "0003", verbosity=0)
        self.assertFalse(_counting_0004_applied())
        _restore_migration_graph()
        self.assertTrue(_counting_0004_applied())
        self.assertTrue(_imports_0001_applied())

    def test_serialized_count_history_blocks_counting_0004_reverse(self):
        from inventory.services.receipts import receive_serialized
        from counting.models import PhysicalCountSerializedLine
        from counting.services import (
            create_physical_count_session,
            start_physical_count_session,
        )

        receive_serialized(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            internal_asset_code=f"SRV-A-{uuid.uuid4().hex[:8]}",
            serial_number=None,
            condition_id=self.condition.pk,
            target_location_id=self.location.pk,
        )
        session = create_physical_count_session(
            actor=self.actor,
            reference_number=f"SRV-{uuid.uuid4().hex}",
            scope_location_id=self.root.pk,
        )
        start_physical_count_session(actor=self.actor, session_id=session.pk)
        line_id = PhysicalCountSerializedLine.objects.get(session=session).pk
        with self.assertRaises(IntegrityError) as exc_info:
            call_command("migrate", "counting", "0003", verbosity=0)
        cause = exc_info.exception.__cause__
        diag = getattr(cause, "diag", None)
        self.assertIn("serialized count history exists", str(exc_info.exception))
        self.assertEqual(
            getattr(diag, "constraint_name", None),
            "counting_serialized_reverse_requires_no_data",
        )
        self.assertTrue(_counting_0004_applied())
        self.assertTrue(PhysicalCountSerializedLine.objects.filter(pk=line_id).exists())
