from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from catalog.models import Category, Material, MaterialCondition
from inventory.models import InventoryTransaction, InventoryTransactionLine, SerializedAsset
from inventory.services.receipts import (
    INTERNAL_ASSET_CODE_CONFLICT,
    SERIAL_NUMBER_CONFLICT,
    receive_serialized,
)
from locations.models import Location


class SerializedReceiptConcurrencyTests(TransactionTestCase):
    """Deterministic PostgreSQL races using separate connections and barriers."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.category = Category.objects.create(name=f"SRC kategori {suffix}")
        self.material = Material.objects.create(
            material_code=f"SRC-M-{suffix}",
            name="Tekil yarış malzemesi",
            category=self.category,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        self.other_material = Material.objects.create(
            material_code=f"SRC-M2-{suffix}",
            name="Diğer tekil yarış malzemesi",
            category=self.category,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"SRC-C-{suffix}", name="Yeni", sort_order=830
        )
        self.location = Location.objects.create(
            code=f"SRC-L-{suffix}",
            name="Tekil yarış rafı",
            active=True,
            can_hold_stock=True,
        )
        self.actor = get_user_model().objects.create_user(username=f"src-{suffix}")
        self.actor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                codename="receive_stock",
            )
        )
        self.actor = get_user_model().objects.get(pk=self.actor.pk)

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
                    inventory_stockbalance,
                    inventory_serializedasset
                """
            )
        Material.objects.filter(pk__in=[self.material.pk, self.other_material.pk]).delete()
        Location.objects.filter(pk=self.location.pk).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk=self.actor.pk).delete()

    def _request(self, **overrides):
        values = {
            "actor": self.actor,
            "operation_id": uuid.uuid4(),
            "material_id": self.material.pk,
            "internal_asset_code": f"SRC-A-{uuid.uuid4().hex[:8]}",
            "serial_number": f"SRC-S-{uuid.uuid4().hex[:8]}",
            "condition_id": self.condition.pk,
            "target_location_id": self.location.pk,
        }
        values.update(overrides)
        return values

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

    def test_same_operation_race_has_one_asset_and_one_replay(self):
        request = self._request(operation_id=uuid.uuid4())
        results = self._run_concurrently(
            lambda: receive_serialized(**request),
            lambda: receive_serialized(**request),
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].serialized_asset.pk, results[1].serialized_asset.pk)
        self.assertEqual(SerializedAsset.objects.count(), 1)
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        self.assertEqual(InventoryTransactionLine.objects.count(), 1)

    def test_same_internal_code_race_has_one_clean_winner(self):
        requests = [
            self._request(
                material_id=self.material.pk,
                internal_asset_code="RACE-CODE",
            ),
            self._request(
                material_id=self.other_material.pk,
                internal_asset_code="RACE-CODE",
            ),
        ]
        results = self._run_concurrently(
            lambda: receive_serialized(**requests[0]),
            lambda: receive_serialized(**requests[1]),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, INTERNAL_ASSET_CODE_CONFLICT)
        self.assertEqual(SerializedAsset.objects.count(), 1)
        self.assertEqual(InventoryTransaction.objects.count(), 1)

    def test_same_material_serial_race_has_one_clean_winner(self):
        requests = [
            self._request(internal_asset_code="RACE-SERIAL-1", serial_number="MFG-RACE"),
            self._request(internal_asset_code="RACE-SERIAL-2", serial_number="MFG-RACE"),
        ]
        results = self._run_concurrently(
            lambda: receive_serialized(**requests[0]),
            lambda: receive_serialized(**requests[1]),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, SERIAL_NUMBER_CONFLICT)
        self.assertEqual(SerializedAsset.objects.count(), 1)
        self.assertEqual(InventoryTransaction.objects.count(), 1)

    def test_same_serial_different_material_and_same_target_both_succeed(self):
        requests = [
            self._request(
                material_id=self.material.pk,
                internal_asset_code="DIFF-MAT-1",
                serial_number="CROSS-MATERIAL",
            ),
            self._request(
                material_id=self.other_material.pk,
                internal_asset_code="DIFF-MAT-2",
                serial_number="CROSS-MATERIAL",
            ),
        ]
        results = self._run_concurrently(
            lambda: receive_serialized(**requests[0]),
            lambda: receive_serialized(**requests[1]),
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(SerializedAsset.objects.count(), 2)
        self.assertEqual(InventoryTransaction.objects.count(), 2)
        self.assertEqual(InventoryTransactionLine.objects.count(), 2)
