from __future__ import annotations

import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, connection, connections
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    StockBalance,
)
from locations.models import Location


class QuantityInventoryConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL races using independently committed worker connections."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.unit = UnitOfMeasure.objects.create(
            code=f"RACE-U-{suffix}",
            name="Race birimi",
        )
        self.category = Category.objects.create(name=f"Race kategori {suffix}")
        self.material = Material.objects.create(
            material_code=f"RACE-M-{suffix}",
            name="Race malzemesi",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"RACE-C-{suffix}",
            name="Race kondisyonu",
            sort_order=200,
        )
        self.location = Location.objects.create(
            code=f"RACE-L-{suffix}",
            name="Race lokasyonu",
            active=True,
            can_hold_stock=True,
        )
        self.user = get_user_model().objects.create_user(
            username=f"race-{suffix}"
        )
        self.employee = Employee.objects.create(
            employee_number=f"RACE-E-{suffix}",
            first_name="Ayşe",
            last_name="Yılmaz",
        )
        self.production_line = ProductionLine.objects.create(
            code=f"RACE-PL-{suffix}",
            name="Race üretim hattı",
        )

    def _fixture_teardown(self):
        # Ledger DELETE is intentionally forbidden. These test-only tables are
        # truncated together so TransactionTestCase does not flush canonical
        # data-migration rows from the reused PostgreSQL test database.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    inventory_issuecontext,
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance
                """
            )

        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk=self.location.pk).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        ProductionLine.objects.filter(pk=self.production_line.pk).delete()
        Employee.objects.filter(pk=self.employee.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk=self.user.pk).delete()

    def _wait_until_postgresql_reports_lock_wait(
        self,
        *,
        backend_pid: int,
        worker_done: threading.Event,
    ) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT wait_event_type
                    FROM pg_catalog.pg_stat_activity
                    WHERE pid = %s
                    """,
                    [backend_pid],
                )
                row = cursor.fetchone()
            if row is not None and row[0] == "Lock":
                return
            if worker_done.is_set():
                self.fail("Concurrent UPDATE completed instead of waiting on row lock.")
        self.fail("PostgreSQL did not report the expected deterministic lock wait.")

    @staticmethod
    def _backend_pid() -> int:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_catalog.pg_backend_pid()")
            return cursor.fetchone()[0]

    def _run_writer_first_race(self, *, writer, updater):
        writer_ready = threading.Event()
        release_writer = threading.Event()
        updater_started = threading.Event()
        updater_done = threading.Event()
        updater_pid: queue.Queue[int] = queue.Queue(maxsize=1)

        def writer_worker():
            close_old_connections()
            try:
                return writer(writer_ready, release_writer)
            finally:
                connections["default"].close()

        def updater_worker():
            close_old_connections()
            try:
                if not writer_ready.wait(timeout=10):
                    raise TimeoutError("Writer did not reach its locked phase.")
                updater_pid.put(self._backend_pid())
                updater_started.set()
                return updater()
            finally:
                updater_done.set()
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            writer_future = executor.submit(writer_worker)
            updater_future = executor.submit(updater_worker)
            self.assertTrue(writer_ready.wait(timeout=10))
            self.assertTrue(updater_started.wait(timeout=10))
            pid = updater_pid.get(timeout=10)
            try:
                self._wait_until_postgresql_reports_lock_wait(
                    backend_pid=pid,
                    worker_done=updater_done,
                )
            finally:
                release_writer.set()

            writer_result = writer_future.result(timeout=10)
            updater_result = updater_future.result(timeout=10)

        return writer_result, updater_result

    def _positive_balance_writer(self, writer_ready, release_writer):
        with transaction.atomic():
            StockBalance.objects.create(
                material_id=self.material.pk,
                location_id=self.location.pk,
                condition_id=self.condition.pk,
                quantity=Decimal("1"),
            )
            writer_ready.set()
            if not release_writer.wait(timeout=10):
                raise TimeoutError("Balance writer was not released.")
        return "committed"

    def _location_updater(self, update_values):
        try:
            with transaction.atomic():
                Location.objects.filter(pk=self.location.pk).update(**update_values)
        except DatabaseError as exc:
            return f"rejected: {exc}"
        return "committed"

    def test_positive_balance_serializes_against_location_deactivation(self):
        writer_result, updater_result = self._run_writer_first_race(
            writer=self._positive_balance_writer,
            updater=lambda: self._location_updater({"active": False}),
        )

        self.assertEqual(writer_result, "committed")
        self.assertIn("positive stock", updater_result)
        self.location.refresh_from_db()
        balance = StockBalance.objects.get(
            material=self.material,
            location=self.location,
            condition=self.condition,
        )
        self.assertGreater(balance.quantity, 0)
        self.assertTrue(self.location.active)

    def test_positive_balance_serializes_against_stock_capability_removal(self):
        writer_result, updater_result = self._run_writer_first_race(
            writer=self._positive_balance_writer,
            updater=lambda: self._location_updater({"can_hold_stock": False}),
        )

        self.assertEqual(writer_result, "committed")
        self.assertIn("positive stock", updater_result)
        self.location.refresh_from_db()
        balance = StockBalance.objects.get(
            material=self.material,
            location=self.location,
            condition=self.condition,
        )
        self.assertGreater(balance.quantity, 0)
        self.assertTrue(self.location.can_hold_stock)

    def _line_writer(
        self,
        writer_ready,
        release_writer,
        *,
        transaction_type=InventoryTransaction.TransactionType.RECEIPT,
    ):
        with transaction.atomic():
            header = InventoryTransaction.objects.create(
                operation_id=uuid.uuid4(),
                request_fingerprint="f" * 64,
                transaction_type=transaction_type,
                acting_user_id=self.user.pk,
                occurred_at=timezone.now(),
            )
            InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material_id=self.material.pk,
                quantity=Decimal("1"),
                unit_id=self.unit.pk,
                condition_id=self.condition.pk,
                source_location_id=(
                    self.location.pk
                    if transaction_type == InventoryTransaction.TransactionType.ISSUE
                    else None
                ),
                target_location_id=(
                    self.location.pk
                    if transaction_type == InventoryTransaction.TransactionType.RECEIPT
                    else None
                ),
            )
            if transaction_type == InventoryTransaction.TransactionType.ISSUE:
                IssueContext.objects.create(
                    transaction=header,
                    receiver_employee_id=self.employee.pk,
                    receiver_first_name_snapshot=self.employee.first_name,
                    receiver_last_name_snapshot=self.employee.last_name,
                    receiver_employee_number_snapshot=self.employee.employee_number,
                    production_line_id=self.production_line.pk,
                    production_line_code_snapshot=self.production_line.code,
                    production_line_name_snapshot=self.production_line.name,
                    usage_location_text="Pano 7",
                )
            writer_ready.set()
            if not release_writer.wait(timeout=10):
                raise TimeoutError("Ledger writer was not released.")
        return "committed"

    def _tracking_mode_updater(self):
        try:
            with transaction.atomic():
                Material.objects.filter(pk=self.material.pk).update(
                    tracking_mode=Material.TrackingMode.SERIALIZED
                )
        except DatabaseError as exc:
            return f"rejected: {exc}"
        return "committed"

    def test_first_quantity_line_serializes_against_tracking_mode_change(self):
        writer_result, updater_result = self._run_writer_first_race(
            writer=self._line_writer,
            updater=self._tracking_mode_updater,
        )

        self.assertEqual(writer_result, "committed")
        self.assertIn("cannot change after inventory history", updater_result)
        self.material.refresh_from_db()
        self.assertEqual(self.material.tracking_mode, Material.TrackingMode.QUANTITY)
        self.assertTrue(
            InventoryTransactionLine.objects.filter(material=self.material).exists()
        )

    def test_first_issue_line_serializes_against_tracking_mode_change(self):
        writer_result, updater_result = self._run_writer_first_race(
            writer=lambda writer_ready, release_writer: self._line_writer(
                writer_ready,
                release_writer,
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            ),
            updater=self._tracking_mode_updater,
        )

        self.assertEqual(writer_result, "committed")
        self.assertIn("cannot change after inventory history", updater_result)
        self.material.refresh_from_db()
        self.assertEqual(self.material.tracking_mode, Material.TrackingMode.QUANTITY)
        issue_line = InventoryTransactionLine.objects.get(material=self.material)
        self.assertEqual(
            issue_line.transaction.transaction_type,
            InventoryTransaction.TransactionType.ISSUE,
        )

    def _zero_balance_writer(self, writer_ready, release_writer):
        with transaction.atomic():
            StockBalance.objects.create(
                material_id=self.material.pk,
                location_id=self.location.pk,
                condition_id=self.condition.pk,
                quantity=Decimal("0"),
            )
            writer_ready.set()
            if not release_writer.wait(timeout=10):
                raise TimeoutError("Zero-balance writer was not released.")
        return "committed"

    def test_first_balance_serializes_against_tracking_mode_change(self):
        writer_result, updater_result = self._run_writer_first_race(
            writer=self._zero_balance_writer,
            updater=self._tracking_mode_updater,
        )

        self.assertEqual(writer_result, "committed")
        self.assertIn("cannot change after inventory history", updater_result)
        self.material.refresh_from_db()
        self.assertEqual(self.material.tracking_mode, Material.TrackingMode.QUANTITY)
        self.assertTrue(StockBalance.objects.filter(material=self.material).exists())
