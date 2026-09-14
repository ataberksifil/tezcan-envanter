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

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    ProductionLine,
    StockBalance,
)
from inventory.services.issues import issue_quantity
from inventory.services.receipts import receive_quantity
from inventory.services.returns import (
    RETURN_EXCEEDS_ISSUE_QUANTITY,
    return_quantity,
)
from locations.models import Location


class QuantityReturnServiceConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL service races over separate database connections."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.unit = UnitOfMeasure.objects.create(code=f"RSC-U-{suffix}", name="Adet")
        self.category = Category.objects.create(name=f"Return service race {suffix}")
        self.material = Material.objects.create(
            material_code=f"RSC-M-{suffix}",
            name="Return service race material",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"RSC-C-{suffix}",
            name="Return service race condition",
            sort_order=975,
        )
        self.source = Location.objects.create(
            code=f"RSC-S-{suffix}",
            name="Return service source",
            can_hold_stock=True,
        )
        self.target = Location.objects.create(
            code=f"RSC-T-{suffix}",
            name="Return service target",
            can_hold_stock=True,
        )
        self.employee = Employee.objects.create(
            employee_number=f"RSC-E-{suffix}",
            first_name="Ayşe",
            last_name="Yılmaz",
        )
        self.production_line = ProductionLine.objects.create(
            code=f"RSC-PL-{suffix}",
            name="Return service line",
        )
        self.actor = get_user_model().objects.create_user(username=f"rsc-{suffix}")
        permissions = Permission.objects.filter(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename__in=("receive_stock", "issue_stock", "return_stock"),
        )
        self.actor.user_permissions.add(*permissions)
        self.actor = get_user_model().objects.get(pk=self.actor.pk)

        receive_quantity(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            target_location_id=self.source.pk,
            quantity=Decimal("10.000"),
        )
        issue = issue_quantity(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            source_location_id=self.source.pk,
            quantity=Decimal("10.000"),
            receiver_employee_id=self.employee.pk,
            production_line_id=self.production_line.pk,
            usage_location_text="Pano 8",
        )
        self.issue_line = issue.lines[0]

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

    def _return_request(self, *, operation_id=None, quantity=Decimal("2.000")):
        return {
            "actor": self.actor,
            "operation_id": operation_id or uuid.uuid4(),
            "original_issue_line_id": self.issue_line.pk,
            "target_location_id": self.target.pk,
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
                except Exception as exc:  # exact cross-thread assertion value
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=30) for future in futures]

    def test_concurrent_duplicate_operation_has_one_mutation_and_one_replay(self):
        request = self._return_request(operation_id=uuid.uuid4())

        results = self._run_concurrently(
            lambda: return_quantity(**request),
            lambda: return_quantity(**request),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].transaction.pk, results[1].transaction.pk)
        returns = InventoryTransaction.objects.filter(
            transaction_type=InventoryTransaction.TransactionType.RETURN
        )
        self.assertEqual(returns.count(), 1)
        self.assertEqual(self.issue_line.return_lines.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(location=self.target).quantity,
            Decimal("2.000"),
        )

    def test_concurrent_returns_enforce_cumulative_cap_once(self):
        return_quantity(**self._return_request(quantity=Decimal("6.000")))
        requests = (
            self._return_request(quantity=Decimal("4.000")),
            self._return_request(quantity=Decimal("4.000")),
        )

        results = self._run_concurrently(
            lambda: return_quantity(**requests[0]),
            lambda: return_quantity(**requests[1]),
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, RETURN_EXCEEDS_ISSUE_QUANTITY)
        linked = InventoryTransactionLine.objects.filter(
            original_issue_line_id=self.issue_line.pk,
            transaction__transaction_type=InventoryTransaction.TransactionType.RETURN,
        )
        self.assertEqual(linked.count(), 2)
        self.assertEqual(
            sum(linked.values_list("quantity", flat=True)),
            Decimal("10.000"),
        )
        self.assertEqual(
            StockBalance.objects.get(location=self.target).quantity,
            Decimal("10.000"),
        )

    def test_concurrent_first_return_balance_creation_has_no_lost_update(self):
        requests = (
            self._return_request(quantity=Decimal("2.000")),
            self._return_request(quantity=Decimal("3.000")),
        )

        results = self._run_concurrently(
            lambda: return_quantity(**requests[0]),
            lambda: return_quantity(**requests[1]),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        target_balances = StockBalance.objects.filter(
            material=self.material,
            location=self.target,
            condition=self.condition,
        )
        self.assertEqual(target_balances.count(), 1)
        self.assertEqual(target_balances.get().quantity, Decimal("5.000"))
        self.assertEqual(self.issue_line.return_lines.count(), 2)

    def test_return_and_receipt_into_same_missing_bucket_have_no_lost_update(self):
        return_request = self._return_request(quantity=Decimal("2.000"))
        receipt_request = {
            "actor": self.actor,
            "operation_id": uuid.uuid4(),
            "material_id": self.material.pk,
            "unit_id": self.unit.pk,
            "condition_id": self.condition.pk,
            "target_location_id": self.target.pk,
            "quantity": Decimal("3.000"),
        }

        results = self._run_concurrently(
            lambda: return_quantity(**return_request),
            lambda: receive_quantity(**receipt_request),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        target_balances = StockBalance.objects.filter(
            material=self.material,
            location=self.target,
            condition=self.condition,
        )
        self.assertEqual(target_balances.count(), 1)
        self.assertEqual(target_balances.get().quantity, Decimal("5.000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.RETURN
            ).count(),
            1,
        )
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.RECEIPT
            ).count(),
            2,
        )
