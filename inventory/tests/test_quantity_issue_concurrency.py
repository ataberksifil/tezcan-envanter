from __future__ import annotations

import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    StockBalance,
)
from inventory.services import issues
from inventory.services.issues import INSUFFICIENT_STOCK, issue_quantity
from inventory.services.receipts import receive_quantity
from locations.models import Location


class QuantityIssueConcurrencyTests(TransactionTestCase):
    """Deterministic races on independent PostgreSQL connections."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.unit = UnitOfMeasure.objects.create(code=f"IC-U-{suffix}", name="Adet")
        self.category = Category.objects.create(name=f"Issue race {suffix}")
        self.material = Material.objects.create(
            material_code=f"IC-M-{suffix}", name="Issue race material",
            category=self.category, unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"IC-C-{suffix}", name="Issue race condition", sort_order=800
        )
        self.location = Location.objects.create(
            code=f"IC-L-{suffix}", name="Issue race location",
            active=True, can_hold_stock=True,
        )
        self.employee = Employee.objects.create(
            employee_number=f"IC-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
        )
        self.production_line = ProductionLine.objects.create(
            code=f"IC-PL-{suffix}", name="Issue race line"
        )
        self.actor = get_user_model().objects.create_user(username=f"ic-{suffix}")
        self.issue_permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="issue_stock",
        )
        receipt_permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="receive_stock",
        )
        self.actor.user_permissions.add(self.issue_permission, receipt_permission)
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self.balance = StockBalance.objects.create(
            material=self.material, location=self.location,
            condition=self.condition, quantity=Decimal("1.000"),
        )

    def _fixture_teardown(self):
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
        get_user_model().objects.filter(pk=self.actor.pk).delete()
        ProductionLine.objects.filter(pk=self.production_line.pk).delete()
        Employee.objects.filter(pk=self.employee.pk).delete()
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk=self.location.pk).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()

    def _issue_request(self, *, operation_id=None, quantity=Decimal("1.000")):
        return {
            "actor": self.actor,
            "operation_id": operation_id or uuid.uuid4(),
            "material_id": self.material.pk,
            "unit_id": self.unit.pk,
            "condition_id": self.condition.pk,
            "source_location_id": self.location.pk,
            "quantity": quantity,
            "receiver_employee_id": self.employee.pk,
            "production_line_id": self.production_line.pk,
            "usage_location_text": "Pano 7",
        }

    @staticmethod
    def _run_concurrently(*calls):
        start = threading.Barrier(len(calls))

        def worker(call):
            close_old_connections()
            try:
                start.wait(timeout=10)
                try:
                    return call()
                except Exception as exc:
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_last_stock_allows_exactly_one_issue(self):
        results = self._run_concurrently(
            lambda: issue_quantity(**self._issue_request()),
            lambda: issue_quantity(**self._issue_request()),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, INSUFFICIENT_STOCK)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, Decimal("0.000"))
        self.assertEqual(InventoryTransaction.objects.filter(transaction_type="ISSUE").count(), 1)
        self.assertEqual(IssueContext.objects.count(), 1)

    def test_concurrent_duplicate_operation_has_one_mutation_and_same_result(self):
        operation_id = uuid.uuid4()
        request = self._issue_request(operation_id=operation_id)
        results = self._run_concurrently(
            lambda: issue_quantity(**request),
            lambda: issue_quantity(**request),
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].transaction.pk, results[1].transaction.pk)
        self.assertEqual(results[0].issue_context.pk, results[1].issue_context.pk)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, Decimal("0.000"))
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        self.assertEqual(IssueContext.objects.count(), 1)

    def test_receipt_and_issue_on_existing_bucket_do_not_lose_updates(self):
        self.balance.quantity = Decimal("5.000")
        self.balance.save()
        receipt_request = {
            "actor": self.actor, "operation_id": uuid.uuid4(),
            "material_id": self.material.pk, "unit_id": self.unit.pk,
            "condition_id": self.condition.pk,
            "target_location_id": self.location.pk, "quantity": Decimal("2.000"),
        }
        results = self._run_concurrently(
            lambda: receive_quantity(**receipt_request),
            lambda: issue_quantity(**self._issue_request(quantity=Decimal("3.000"))),
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, Decimal("4.000"))
        self.assertEqual(InventoryTransaction.objects.count(), 2)

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
                self.fail("Lifecycle update completed without waiting for ISSUE locks.")
        self.fail("PostgreSQL did not report the expected lifecycle lock wait.")

    def _assert_issue_locks_lifecycle_update(self, update):
        issue_ready = threading.Event()
        release_issue = threading.Event()
        updater_started = threading.Event()
        updater_done = threading.Event()
        updater_pid = queue.Queue(maxsize=1)
        original_validate = issues._validate_new_issue_masters

        def gated_validate(**kwargs):
            original_validate(**kwargs)
            issue_ready.set()
            if not release_issue.wait(timeout=10):
                raise TimeoutError("ISSUE validation gate was not released.")

        def issue_worker():
            close_old_connections()
            try:
                with patch.object(issues, "_validate_new_issue_masters", gated_validate):
                    return issue_quantity(**self._issue_request())
            finally:
                connections["default"].close()

        def update_worker():
            close_old_connections()
            try:
                if not issue_ready.wait(timeout=10):
                    raise TimeoutError("ISSUE did not acquire master locks.")
                updater_pid.put(self._backend_pid())
                updater_started.set()
                try:
                    return update()
                except DatabaseError as exc:
                    return exc
            finally:
                updater_done.set()
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            issue_future = executor.submit(issue_worker)
            update_future = executor.submit(update_worker)
            self.assertTrue(issue_ready.wait(timeout=10))
            self.assertTrue(updater_started.wait(timeout=10))
            pid = updater_pid.get(timeout=10)
            try:
                self._wait_for_lock(pid, updater_done)
            finally:
                release_issue.set()
            issue_result = issue_future.result(timeout=10)
            update_result = update_future.result(timeout=10)
        self.assertFalse(isinstance(issue_result, Exception))
        return update_result

    def test_issue_locks_material_against_tracking_mode_change(self):
        result = self._assert_issue_locks_lifecycle_update(
            lambda: Material.objects.filter(pk=self.material.pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )
        )
        self.assertIsInstance(result, DatabaseError)
        self.material.refresh_from_db()
        self.assertEqual(self.material.tracking_mode, Material.TrackingMode.QUANTITY)

    def test_issue_locks_location_against_deactivation(self):
        result = self._assert_issue_locks_lifecycle_update(
            lambda: Location.objects.filter(pk=self.location.pk).update(active=False)
        )
        self.assertEqual(result, 1)
        self.location.refresh_from_db()
        self.assertFalse(self.location.active)
        self.assertTrue(
            InventoryTransactionLine.objects.filter(
                source_location=self.location,
                transaction__transaction_type="ISSUE",
            ).exists()
        )

    def test_issue_locks_employee_against_deactivation(self):
        result = self._assert_issue_locks_lifecycle_update(
            lambda: Employee.objects.filter(pk=self.employee.pk).update(active=False)
        )
        self.assertEqual(result, 1)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.active)
        self.assertTrue(IssueContext.objects.filter(receiver_employee=self.employee).exists())

    def test_issue_locks_production_line_against_deactivation(self):
        result = self._assert_issue_locks_lifecycle_update(
            lambda: ProductionLine.objects.filter(pk=self.production_line.pk).update(active=False)
        )
        self.assertEqual(result, 1)
        self.production_line.refresh_from_db()
        self.assertFalse(self.production_line.active)
        self.assertTrue(IssueContext.objects.filter(production_line=self.production_line).exists())
