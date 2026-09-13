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
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    ProductionLine,
    StockBalance,
)
from inventory.services import transfers
from inventory.services.issues import issue_quantity
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import receive_quantity
from inventory.services.transfers import INSUFFICIENT_STOCK, transfer_quantity
from locations.models import Location


class QuantityTransferServiceConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL transfer races over separate database connections."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.unit = UnitOfMeasure.objects.create(code=f"XSC-U-{suffix}", name="Adet")
        self.category = Category.objects.create(name=f"Transfer service race {suffix}")
        self.material = Material.objects.create(
            material_code=f"XSC-M-{suffix}",
            name="Transfer service race material",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"XSC-C-{suffix}",
            name="Transfer service race condition",
            sort_order=930,
        )
        self.source = Location.objects.create(
            code=f"XSC-S-{suffix}",
            name="Transfer service source",
            can_hold_stock=True,
        )
        self.target = Location.objects.create(
            code=f"XSC-T-{suffix}",
            name="Transfer service target",
            can_hold_stock=True,
        )
        self.alt_source = Location.objects.create(
            code=f"XSC-A-{suffix}",
            name="Transfer service alt source",
            can_hold_stock=True,
        )
        self.employee = Employee.objects.create(
            employee_number=f"XSC-E-{suffix}",
            first_name="Ayşe",
            last_name="Yılmaz",
        )
        self.production_line = ProductionLine.objects.create(
            code=f"XSC-PL-{suffix}",
            name="Transfer service line",
        )
        self.actor = get_user_model().objects.create_user(username=f"xsc-{suffix}")
        permissions = Permission.objects.filter(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename__in=(
                "receive_stock",
                "issue_stock",
                "return_stock",
                "transfer_stock",
            ),
        )
        self.actor.user_permissions.add(*permissions)
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self._receive(self.source, Decimal("10.000"))

    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
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
        Location.objects.filter(
            pk__in=[self.source.pk, self.target.pk, self.alt_source.pk]
        ).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()

    def _receive(self, location, quantity):
        return receive_quantity(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            target_location_id=location.pk,
            quantity=quantity,
        )

    def _transfer_request(
        self,
        *,
        operation_id=None,
        quantity=Decimal("4.000"),
        source=None,
        target=None,
    ):
        return {
            "actor": self.actor,
            "operation_id": operation_id or uuid.uuid4(),
            "material_id": self.material.pk,
            "unit_id": self.unit.pk,
            "condition_id": self.condition.pk,
            "source_location_id": (source or self.source).pk,
            "target_location_id": (target or self.target).pk,
            "quantity": quantity,
        }

    def _issue_request(self, *, quantity=Decimal("4.000")):
        return {
            "actor": self.actor,
            "operation_id": uuid.uuid4(),
            "material_id": self.material.pk,
            "unit_id": self.unit.pk,
            "condition_id": self.condition.pk,
            "source_location_id": self.source.pk,
            "quantity": quantity,
            "receiver_employee_id": self.employee.pk,
            "production_line_id": self.production_line.pk,
            "usage_location_text": "Pano 10",
        }

    def _balance(self, location):
        return StockBalance.objects.get(
            material=self.material,
            location=location,
            condition=self.condition,
        )

    @staticmethod
    def _run_concurrently(*calls):
        start = threading.Barrier(len(calls))

        def worker(call):
            close_old_connections()
            try:
                start.wait(timeout=10)
                try:
                    return call()
                except Exception as exc:  # exact cross-thread assertion value
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=30) for future in futures]

    def test_concurrent_duplicate_operation_has_one_mutation_and_one_replay(self):
        request = self._transfer_request(operation_id=uuid.uuid4())

        results = self._run_concurrently(
            lambda: transfer_quantity(**request),
            lambda: transfer_quantity(**request),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].transaction.pk, results[1].transaction.pk)
        transfers_qs = InventoryTransaction.objects.filter(
            transaction_type=InventoryTransaction.TransactionType.TRANSFER
        )
        self.assertEqual(transfers_qs.count(), 1)
        self.assertEqual(self._balance(self.source).quantity, Decimal("6.000"))
        self.assertEqual(self._balance(self.target).quantity, Decimal("4.000"))

    def test_last_stock_transfer_race_allows_exactly_one_success(self):
        transfer_quantity(
            **self._transfer_request(
                quantity=Decimal("5.000"),
                source=self.source,
                target=self.alt_source,
            )
        )

        results = self._run_concurrently(
            lambda: transfer_quantity(
                **self._transfer_request(quantity=Decimal("4.000"))
            ),
            lambda: transfer_quantity(
                **self._transfer_request(quantity=Decimal("4.000"))
            ),
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, INSUFFICIENT_STOCK)
        self.assertEqual(self._balance(self.source).quantity, Decimal("1.000"))
        self.assertEqual(self._balance(self.target).quantity, Decimal("4.000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.TRANSFER,
                lines__target_location=self.target,
            )
            .distinct()
            .count(),
            1,
        )
        self.assertGreaterEqual(self._balance(self.source).quantity, Decimal("0.000"))

    def test_opposite_direction_transfers_both_commit_without_deadlock(self):
        self._receive(self.target, Decimal("10.000"))

        results = self._run_concurrently(
            lambda: transfer_quantity(
                **self._transfer_request(
                    quantity=Decimal("3.000"),
                    source=self.source,
                    target=self.target,
                )
            ),
            lambda: transfer_quantity(
                **self._transfer_request(
                    quantity=Decimal("4.000"),
                    source=self.target,
                    target=self.source,
                )
            ),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertTrue(all(result.replayed is False for result in results))
        self.assertEqual(self._balance(self.source).quantity, Decimal("11.000"))
        self.assertEqual(self._balance(self.target).quantity, Decimal("9.000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.TRANSFER
            ).count(),
            2,
        )
        self.assertEqual(verify_quantity_projection(), ())

    def test_concurrent_first_target_creation_has_one_row_and_no_lost_update(self):
        self._receive(self.alt_source, Decimal("10.000"))

        results = self._run_concurrently(
            lambda: transfer_quantity(
                **self._transfer_request(
                    quantity=Decimal("2.000"),
                    source=self.source,
                    target=self.target,
                )
            ),
            lambda: transfer_quantity(
                **self._transfer_request(
                    quantity=Decimal("3.000"),
                    source=self.alt_source,
                    target=self.target,
                )
            ),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        targets = StockBalance.objects.filter(
            material=self.material,
            location=self.target,
            condition=self.condition,
        )
        self.assertEqual(targets.count(), 1)
        self.assertEqual(targets.get().quantity, Decimal("5.000"))
        self.assertEqual(self._balance(self.source).quantity, Decimal("8.000"))
        self.assertEqual(self._balance(self.alt_source).quantity, Decimal("7.000"))

    def test_transfer_and_receipt_into_missing_target_have_no_lost_update(self):
        receipt_request = {
            "actor": self.actor,
            "operation_id": uuid.uuid4(),
            "material_id": self.material.pk,
            "unit_id": self.unit.pk,
            "condition_id": self.condition.pk,
            "target_location_id": self.target.pk,
            "quantity": Decimal("2.000"),
        }

        results = self._run_concurrently(
            lambda: transfer_quantity(
                **self._transfer_request(quantity=Decimal("3.000"))
            ),
            lambda: receive_quantity(**receipt_request),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        targets = StockBalance.objects.filter(
            material=self.material,
            location=self.target,
            condition=self.condition,
        )
        self.assertEqual(targets.count(), 1)
        self.assertEqual(targets.get().quantity, Decimal("5.000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.TRANSFER
            ).count(),
            1,
        )
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.RECEIPT,
                lines__target_location=self.target,
            )
            .distinct()
            .count(),
            1,
        )

    def test_transfer_and_issue_on_same_source_do_not_go_negative(self):
        transfer_quantity(
            **self._transfer_request(
                quantity=Decimal("5.000"),
                source=self.source,
                target=self.alt_source,
            )
        )

        results = self._run_concurrently(
            lambda: transfer_quantity(
                **self._transfer_request(quantity=Decimal("4.000"))
            ),
            lambda: issue_quantity(**self._issue_request(quantity=Decimal("4.000"))),
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, INSUFFICIENT_STOCK)
        source = self._balance(self.source)
        self.assertEqual(source.quantity, Decimal("1.000"))
        self.assertGreaterEqual(source.quantity, Decimal("0.000"))
        committed = Decimal("0.000")
        raced_transfer = InventoryTransaction.objects.filter(
            transaction_type=InventoryTransaction.TransactionType.TRANSFER,
            lines__target_location=self.target,
        ).exists()
        if raced_transfer:
            committed += Decimal("4.000")
            self.assertEqual(self._balance(self.target).quantity, Decimal("4.000"))
        if InventoryTransaction.objects.filter(
            transaction_type=InventoryTransaction.TransactionType.ISSUE
        ).exists():
            committed += Decimal("4.000")
        self.assertEqual(committed, Decimal("4.000"))
        self.assertEqual(verify_quantity_projection(), ())

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
                self.fail("Lifecycle update completed without waiting for TRANSFER locks.")
        self.fail("PostgreSQL did not report the expected lifecycle lock wait.")

    def _assert_transfer_locks_lifecycle_update(self, update, quantity=Decimal("4.000")):
        transfer_ready = threading.Event()
        release_transfer = threading.Event()
        updater_started = threading.Event()
        updater_done = threading.Event()
        updater_pid = queue.Queue(maxsize=1)
        original_validate = transfers._validate_new_transfer_masters

        def gated_validate(**kwargs):
            original_validate(**kwargs)
            transfer_ready.set()
            if not release_transfer.wait(timeout=10):
                raise TimeoutError("TRANSFER validation gate was not released.")

        def transfer_worker():
            close_old_connections()
            try:
                with patch.object(
                    transfers, "_validate_new_transfer_masters", gated_validate
                ):
                    return transfer_quantity(
                        **self._transfer_request(quantity=quantity)
                    )
            finally:
                connections["default"].close()

        def update_worker():
            close_old_connections()
            try:
                if not transfer_ready.wait(timeout=10):
                    raise TimeoutError("TRANSFER did not acquire master locks.")
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
            transfer_future = executor.submit(transfer_worker)
            update_future = executor.submit(update_worker)
            self.assertTrue(transfer_ready.wait(timeout=10))
            self.assertTrue(updater_started.wait(timeout=10))
            pid = updater_pid.get(timeout=10)
            try:
                self._wait_for_lock(pid, updater_done)
            finally:
                release_transfer.set()
            transfer_result = transfer_future.result(timeout=10)
            update_result = update_future.result(timeout=10)
        self.assertFalse(isinstance(transfer_result, Exception))
        return update_result

    def test_transfer_locks_material_against_tracking_mode_change(self):
        result = self._assert_transfer_locks_lifecycle_update(
            lambda: Material.objects.filter(pk=self.material.pk).update(
                tracking_mode=Material.TrackingMode.SERIALIZED
            )
        )
        self.assertIsInstance(result, DatabaseError)
        self.material.refresh_from_db()
        self.assertEqual(self.material.tracking_mode, Material.TrackingMode.QUANTITY)

    def test_transfer_locks_material_against_deactivation(self):
        result = self._assert_transfer_locks_lifecycle_update(
            lambda: Material.objects.filter(pk=self.material.pk).update(active=False)
        )
        self.assertEqual(result, 1)
        self.material.refresh_from_db()
        self.assertFalse(self.material.active)
        self.assertTrue(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.TRANSFER
            ).exists()
        )

    def test_transfer_locks_condition_against_deactivation(self):
        result = self._assert_transfer_locks_lifecycle_update(
            lambda: MaterialCondition.objects.filter(pk=self.condition.pk).update(
                active=False
            )
        )
        self.assertEqual(result, 1)
        self.condition.refresh_from_db()
        self.assertFalse(self.condition.active)
        self.assertTrue(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.TRANSFER
            ).exists()
        )

    def test_transfer_locks_source_location_against_deactivation(self):
        result = self._assert_transfer_locks_lifecycle_update(
            lambda: Location.objects.filter(pk=self.source.pk).update(active=False),
            quantity=Decimal("10.000"),
        )
        self.assertEqual(result, 1)
        self.source.refresh_from_db()
        self.assertFalse(self.source.active)
        self.assertTrue(
            InventoryTransactionLine.objects.filter(
                source_location=self.source,
                transaction__transaction_type=InventoryTransaction.TransactionType.TRANSFER,
            ).exists()
        )

    def test_transfer_locks_target_location_against_deactivation(self):
        result = self._assert_transfer_locks_lifecycle_update(
            lambda: Location.objects.filter(pk=self.target.pk).update(active=False)
        )
        self.assertIsInstance(result, DatabaseError)
        self.target.refresh_from_db()
        self.assertTrue(self.target.active)
        self.assertTrue(
            InventoryTransactionLine.objects.filter(
                target_location=self.target,
                transaction__transaction_type=InventoryTransaction.TransactionType.TRANSFER,
            ).exists()
        )
