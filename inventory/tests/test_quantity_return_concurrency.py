from __future__ import annotations

import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
)
from locations.models import Location


class QuantityReturnConcurrencyTests(TransactionTestCase):
    """The original ISSUE line is the deterministic RETURN serialization point."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.unit = UnitOfMeasure.objects.create(code=f"RC-U-{suffix}", name="Adet")
        self.category = Category.objects.create(name=f"Return race {suffix}")
        self.material = Material.objects.create(
            material_code=f"RC-M-{suffix}",
            name="Return race material",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"RC-C-{suffix}", name="Return race condition", sort_order=950
        )
        self.source = Location.objects.create(
            code=f"RC-S-{suffix}", name="Return race source", can_hold_stock=True
        )
        self.target = Location.objects.create(
            code=f"RC-T-{suffix}", name="Return race target", can_hold_stock=True
        )
        self.employee = Employee.objects.create(
            employee_number=f"RC-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
        )
        self.production_line = ProductionLine.objects.create(
            code=f"RC-PL-{suffix}", name="Return race line"
        )
        self.actor = get_user_model().objects.create_user(username=f"rc-{suffix}")
        self.issue_line = self._create_issue(Decimal("10.000"))
        self._create_return(Decimal("6.000"))

    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    counting_physicalcountquantityrejection,
                    counting_physicalcountquantityline,
                    counting_physicalcountsession,
                    corrections_correctionrequest,
                    inventory_issuecontext,
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance
                """
            )
        get_user_model().objects.filter(pk=self.actor.pk).delete()
        ProductionLine.objects.filter(pk=self.production_line.pk).delete()
        Employee.objects.filter(pk=self.employee.pk).delete()
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk__in=[self.source.pk, self.target.pk]).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()

    def _header(self, transaction_type):
        return InventoryTransaction.objects.create(
            operation_id=uuid.uuid4(),
            request_fingerprint=uuid.uuid4().hex * 2,
            transaction_type=transaction_type,
            acting_user=self.actor,
            occurred_at=timezone.now(),
        )

    def _create_issue(self, quantity):
        with transaction.atomic():
            header = self._header(InventoryTransaction.TransactionType.ISSUE)
            line = InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=self.material,
                quantity=quantity,
                unit=self.unit,
                condition=self.condition,
                source_location=self.source,
            )
            IssueContext.objects.create(
                transaction=header,
                receiver_employee=self.employee,
                receiver_first_name_snapshot=self.employee.first_name,
                receiver_last_name_snapshot=self.employee.last_name,
                receiver_employee_number_snapshot=self.employee.employee_number,
                production_line=self.production_line,
                production_line_code_snapshot=self.production_line.code,
                production_line_name_snapshot=self.production_line.name,
                usage_location_text="Pano 7",
            )
        return line

    def _create_return(self, quantity, *, hold=None):
        with transaction.atomic():
            header = self._header(InventoryTransaction.TransactionType.RETURN)
            line = InventoryTransactionLine.objects.create(
                transaction=header,
                line_number=1,
                material=self.material,
                quantity=quantity,
                unit=self.unit,
                condition=self.condition,
                target_location=self.target,
                original_issue_line_id=self.issue_line.pk,
            )
            if hold is not None:
                ready, release = hold
                ready.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("RETURN serialization lock was not released.")
        return line

    @staticmethod
    def _backend_pid():
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_catalog.pg_backend_pid()")
            return cursor.fetchone()[0]

    def _wait_for_lock(self, pid, done):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_catalog.pg_stat_activity WHERE pid = %s",
                    [pid],
                )
                row = cursor.fetchone()
            if row is not None and row[0] == "Lock":
                return
            if done.is_set():
                self.fail("Second RETURN completed without waiting for the ISSUE-line lock.")
        self.fail("PostgreSQL did not report the expected ISSUE-line lock wait.")

    def test_concurrent_returns_serialize_on_original_issue_line(self):
        first_ready = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_done = threading.Event()
        second_pid = queue.Queue(maxsize=1)

        def first_worker():
            close_old_connections()
            try:
                return self._create_return(
                    Decimal("4.000"), hold=(first_ready, release_first)
                )
            except Exception as exc:
                return exc
            finally:
                connections["default"].close()

        def second_worker():
            close_old_connections()
            try:
                if not first_ready.wait(timeout=10):
                    raise TimeoutError("First RETURN did not acquire the ISSUE-line lock.")
                second_pid.put(self._backend_pid())
                second_started.set()
                try:
                    return self._create_return(Decimal("4.000"))
                except Exception as exc:
                    return exc
            finally:
                second_done.set()
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first_worker)
            second_future = executor.submit(second_worker)
            self.assertTrue(first_ready.wait(timeout=10))
            self.assertTrue(second_started.wait(timeout=10))
            pid = second_pid.get(timeout=10)
            try:
                self._wait_for_lock(pid, second_done)
            finally:
                release_first.set()
            first_result = first_future.result(timeout=10)
            second_result = second_future.result(timeout=10)

        successes = [
            result for result in (first_result, second_result)
            if not isinstance(result, Exception)
        ]
        failures = [
            result for result in (first_result, second_result)
            if isinstance(result, Exception)
        ]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], IntegrityError)
        self.assertIn("cumulative RETURN quantity", str(failures[0]))
        linked_returns = InventoryTransactionLine.objects.filter(
            original_issue_line_id=self.issue_line.pk,
            transaction__transaction_type=InventoryTransaction.TransactionType.RETURN,
        )
        self.assertEqual(linked_returns.count(), 2)
        self.assertEqual(
            sum(linked_returns.values_list("quantity", flat=True)),
            Decimal("10.000"),
        )
