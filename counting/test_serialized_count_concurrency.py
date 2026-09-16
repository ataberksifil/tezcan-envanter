from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountSerializedLine, PhysicalCountSession
from counting.services import (
    COUNT_CONFLICT,
    DUPLICATE_SERIALIZED_IDENTITY,
    add_candidate_serialized_count,
    create_physical_count_session,
    record_serialized_asset_count,
    start_physical_count_session,
)
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.services.receipts import receive_serialized
from locations.models import Location


class SerializedPhysicalCountConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL races using barriers/events and no correctness sleeps."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_user(username=f"scc-{suffix}")
        self.actor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                content_type__model="inventorytransaction",
                codename="receive_stock",
            )
        )
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self.category = Category.objects.create(code=f"SCC-CAT-{suffix}", name="Kat")
        self.unit = UnitOfMeasure.objects.create(code=f"SCC-U-{suffix}", name="Adet")
        self.material = Material.objects.create(
            material_code=f"SCC-S-{suffix}",
            name="Tekil malzeme",
            category=self.category,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        self.quantity_material = Material.objects.create(
            material_code=f"SCC-Q-{suffix}",
            name="Miktar malzemesi",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"SCC-C-{suffix}", name="Kondisyon", sort_order=940
        )
        self.root = Location.objects.create(code=f"SCC-R-{suffix}", name="Kök")
        self.location = Location.objects.create(
            code=f"SCC-L-{suffix}",
            name="Raf",
            parent=self.root,
            can_hold_stock=True,
        )

    def _fixture_teardown(self):
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
        Material.objects.filter(
            pk__in=[self.material.pk, self.quantity_material.pk]
        ).delete()
        Location.objects.filter(pk__in=[self.location.pk, self.root.pk]).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk=self.actor.pk).delete()

    @staticmethod
    def _run_concurrently(*calls):
        ready = threading.Barrier(len(calls))

        def worker(call):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                try:
                    return call()
                except Exception as exc:
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    def _draft(self):
        return create_physical_count_session(
            actor=self.actor,
            reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
            scope_location_id=self.root.pk,
        )

    def _receive(self, **overrides):
        values = {
            "actor": self.actor,
            "operation_id": uuid.uuid4(),
            "material_id": self.material.pk,
            "internal_asset_code": f"SCC-A-{uuid.uuid4().hex[:8]}",
            "serial_number": f"SCC-S-{uuid.uuid4().hex[:8]}",
            "condition_id": self.condition.pk,
            "target_location_id": self.location.pk,
        }
        values.update(overrides)
        return receive_serialized(**values)

    def test_serialized_receive_racing_start_has_coherent_snapshot(self):
        session = self._draft()
        request = {
            "actor": self.actor,
            "operation_id": uuid.uuid4(),
            "material_id": self.material.pk,
            "internal_asset_code": "RACE-START",
            "serial_number": "RACE-SER",
            "condition_id": self.condition.pk,
            "target_location_id": self.location.pk,
        }
        results = self._run_concurrently(
            lambda: start_physical_count_session(
                actor=self.actor, session_id=session.pk
            ),
            lambda: receive_serialized(**request),
        )
        start_result, receive_result = results
        self.assertFalse(isinstance(start_result, Exception), start_result)
        self.assertFalse(isinstance(receive_result, Exception), receive_result)

        session.refresh_from_db()
        self.assertEqual(session.status, PhysicalCountSession.Status.STARTED)
        self.assertEqual(SerializedAsset.objects.count(), 1)
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        self.assertEqual(InventoryTransactionLine.objects.count(), 1)
        asset = SerializedAsset.objects.get()
        expected_ids = set(
            session.serialized_lines.filter(expected_present=True).values_list(
                "serialized_asset_id", flat=True
            )
        )
        self.assertIn(len(expected_ids), {0, 1})
        if asset.pk in expected_ids:
            line = session.serialized_lines.get(serialized_asset=asset)
            self.assertTrue(line.expected_present)
            self.assertEqual(line.expected_location_id, self.location.pk)
        else:
            self.assertEqual(expected_ids, set())
        self.assertEqual(
            session.serialized_lines.filter(expected_present=True).count(),
            len(expected_ids),
        )
        asset.refresh_from_db()
        self.assertEqual(asset.current_location_id, self.location.pk)
        self.assertEqual(asset.internal_asset_code, "RACE-START")

    def test_receive_before_snapshot_commit_is_expected_and_later_receive_is_not(self):
        session = self._draft()
        receive_written = threading.Event()
        allow_receive_commit = threading.Event()
        start_entered = threading.Event()

        def receive_worker():
            close_old_connections()
            try:
                with transaction.atomic():
                    self._receive(internal_asset_code="BEFORE-SNAP")
                    receive_written.set()
                    if not allow_receive_commit.wait(timeout=10):
                        raise AssertionError("receive release event was not set")
            finally:
                connections["default"].close()

        def start_worker():
            close_old_connections()
            try:
                start_entered.set()
                return start_physical_count_session(
                    actor=self.actor, session_id=session.pk
                )
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            receive_future = executor.submit(receive_worker)
            self.assertTrue(receive_written.wait(timeout=10))
            start_future = executor.submit(start_worker)
            self.assertTrue(start_entered.wait(timeout=10))
            allow_receive_commit.set()
            receive_future.result(timeout=20)
            start_result = start_future.result(timeout=20)

        asset = SerializedAsset.objects.get(internal_asset_code="BEFORE-SNAP")
        self.assertEqual(
            {line.serialized_asset_id for line in start_result.serialized_lines},
            {asset.pk},
        )
        later = self._receive(internal_asset_code="AFTER-SNAP")
        session.refresh_from_db()
        self.assertFalse(
            session.serialized_lines.filter(
                serialized_asset=later.serialized_asset, expected_present=True
            ).exists()
        )
        self.assertEqual(session.serialized_lines.count(), 1)

    def test_concurrent_candidate_same_code_creates_one_line(self):
        session = self._draft()
        start_physical_count_session(actor=self.actor, session_id=session.pk)

        def add():
            return add_candidate_serialized_count(
                actor=self.actor,
                session_id=session.pk,
                material_id=self.material.pk,
                internal_asset_code="DUP-CAND",
                serial_number=None,
                observed_location_id=self.location.pk,
                observed_condition_id=self.condition.pk,
            )

        results = self._run_concurrently(add, add)
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, DUPLICATE_SERIALIZED_IDENTITY)
        self.assertEqual(session.serialized_lines.count(), 1)
        self.assertEqual(SerializedAsset.objects.count(), 0)
        self.assertEqual(InventoryTransaction.objects.count(), 0)
        self.assertEqual(StockBalance.objects.count(), 0)

    def test_stale_serialized_updates_do_not_silently_overwrite(self):
        asset = self._receive().serialized_asset
        session = self._draft()
        start_physical_count_session(actor=self.actor, session_id=session.pk)

        results = self._run_concurrently(
            lambda: record_serialized_asset_count(
                actor=self.actor,
                session_id=session.pk,
                serialized_asset_id=asset.pk,
                observed_location_id=self.location.pk,
                observed_condition_id=self.condition.pk,
            ),
            lambda: record_serialized_asset_count(
                actor=self.actor,
                session_id=session.pk,
                serialized_asset_id=asset.pk,
                observed_location_id=self.location.pk,
                observed_condition_id=self.condition.pk,
            ),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, COUNT_CONFLICT)
        self.assertEqual(session.serialized_lines.count(), 1)
        line = PhysicalCountSerializedLine.objects.get(session=session)
        self.assertEqual(line.counted_at, successes[0].counted_at)
        asset.refresh_from_db()
        self.assertEqual(asset.current_location_id, self.location.pk)
        self.assertEqual(InventoryTransaction.objects.count(), 1)

    def test_concurrent_unexpected_same_asset_creates_one_line(self):
        session = self._draft()
        start_physical_count_session(actor=self.actor, session_id=session.pk)
        asset = self._receive(internal_asset_code="UNEXP-RACE").serialized_asset

        def observe():
            return record_serialized_asset_count(
                actor=self.actor,
                session_id=session.pk,
                serialized_asset_id=asset.pk,
                observed_location_id=self.location.pk,
                observed_condition_id=self.condition.pk,
            )

        results = self._run_concurrently(observe, observe)
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertIn(
            failures[0].code,
            {COUNT_CONFLICT, DUPLICATE_SERIALIZED_IDENTITY},
        )
        self.assertEqual(session.serialized_lines.count(), 1)
        line = session.serialized_lines.get()
        self.assertFalse(line.expected_present)
        self.assertEqual(line.serialized_asset_id, asset.pk)
        asset.refresh_from_db()
        self.assertEqual(asset.current_location_id, self.location.pk)
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        self.assertEqual(SerializedAsset.objects.count(), 1)
