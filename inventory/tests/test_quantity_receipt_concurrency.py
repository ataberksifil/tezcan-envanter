from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    StockBalance,
)
from inventory.services.receipts import OPERATION_CONFLICT, receive_quantity
from locations.models import Location


class QuantityReceiptConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL races with separate worker connections and no sleeps."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.unit = UnitOfMeasure.objects.create(code=f"CR-U-{suffix}", name="Adet")
        self.category = Category.objects.create(name=f"Concurrency {suffix}")
        self.material = Material.objects.create(
            material_code=f"CR-M-{suffix}",
            name="Concurrency material",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"CR-C-{suffix}", name="Concurrency condition", sort_order=500
        )
        self.location = Location.objects.create(
            code=f"CR-L-{suffix}",
            name="Concurrency location",
            active=True,
            can_hold_stock=True,
        )
        self.actor = get_user_model().objects.create_user(username=f"cr-{suffix}")
        permission = Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename="receive_stock",
        )
        self.actor.user_permissions.add(permission)
        self.actor = get_user_model().objects.get(pk=self.actor.pk)

    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance
                """
            )
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk=self.location.pk).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk=self.actor.pk).delete()

    def _request(self, *, operation_id, quantity):
        return {
            "actor": self.actor,
            "operation_id": operation_id,
            "material_id": self.material.pk,
            "unit_id": self.unit.pk,
            "condition_id": self.condition.pk,
            "target_location_id": self.location.pk,
            "quantity": quantity,
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
                except Exception as exc:  # returned for exact cross-thread assertions
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    def test_same_operation_concurrently_has_one_effect_and_one_replay(self):
        operation_id = uuid.uuid4()
        request = self._request(operation_id=operation_id, quantity=Decimal("2.000"))

        results = self._run_concurrently(
            lambda: receive_quantity(**request),
            lambda: receive_quantity(**request),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].transaction.pk, results[1].transaction.pk)
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        self.assertEqual(InventoryTransactionLine.objects.count(), 1)
        self.assertEqual(StockBalance.objects.count(), 1)
        self.assertEqual(StockBalance.objects.get().quantity, Decimal("2.000"))

    def test_same_operation_different_request_has_one_winner_and_one_conflict(self):
        operation_id = uuid.uuid4()
        requests = [
            self._request(operation_id=operation_id, quantity=Decimal("1.000")),
            self._request(operation_id=operation_id, quantity=Decimal("3.000")),
        ]

        results = self._run_concurrently(
            lambda: receive_quantity(**requests[0]),
            lambda: receive_quantity(**requests[1]),
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, OPERATION_CONFLICT)
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        self.assertEqual(InventoryTransactionLine.objects.count(), 1)
        self.assertEqual(StockBalance.objects.count(), 1)
        self.assertEqual(StockBalance.objects.get().quantity, successes[0].lines[0].quantity)

    def test_concurrent_first_balance_creation_accumulates_without_lost_update(self):
        requests = [
            self._request(operation_id=uuid.uuid4(), quantity=Decimal("1.125")),
            self._request(operation_id=uuid.uuid4(), quantity=Decimal("2.250")),
        ]

        results = self._run_concurrently(
            lambda: receive_quantity(**requests[0]),
            lambda: receive_quantity(**requests[1]),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertTrue(all(result.replayed is False for result in results))
        self.assertEqual(InventoryTransaction.objects.count(), 2)
        self.assertEqual(InventoryTransactionLine.objects.count(), 2)
        self.assertEqual(StockBalance.objects.count(), 1)
        self.assertEqual(StockBalance.objects.get().quantity, Decimal("3.375"))
