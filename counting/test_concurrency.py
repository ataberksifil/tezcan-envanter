from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountQuantityLine, PhysicalCountSession
from counting.services import (
    COUNT_CONFLICT,
    DUPLICATE_BUCKET,
    INVALID_SESSION,
    OVERLAPPING_SCOPE,
    add_unexpected_quantity_count,
    approve_quantity_discrepancy,
    complete_physical_count,
    create_physical_count_session,
    mark_quantity_line_not_counted,
    record_quantity_count,
    reject_quantity_discrepancy,
    start_physical_count_session,
)
from inventory.models import InventoryTransaction, StockBalance
from inventory.services.receipts import receive_quantity
from inventory.services.transfers import transfer_quantity
from locations.models import Location


class PhysicalCountConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL races using separate connections and no correctness sleeps."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_user(username=f"cc-{suffix}")
        receipt_permission = Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="inventorytransaction",
            codename="receive_stock",
        )
        self.actor.user_permissions.add(receipt_permission)
        self.actor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                content_type__model="inventorytransaction",
                codename="transfer_stock",
            )
        )
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self.approver = get_user_model().objects.create_user(
            username=f"cc-approver-{suffix}"
        )
        self.approver.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="counting",
                content_type__model="physicalcountquantityline",
                codename="decide_discrepancy",
            ),
            receipt_permission,
        )
        self.approver = get_user_model().objects.get(pk=self.approver.pk)
        self.category = Category.objects.create(code=f"CC-CAT-{suffix}", name="Kat")
        self.unit = UnitOfMeasure.objects.create(code=f"CC-U-{suffix}", name="Adet")
        self.material = Material.objects.create(
            material_code=f"CC-M-{suffix}",
            name="Malzeme",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"CC-C-{suffix}", name="Kondisyon", sort_order=900
        )
        self.root = Location.objects.create(code=f"CC-R-{suffix}", name="Kök")
        self.location = Location.objects.create(
            code=f"CC-L-{suffix}",
            name="Raf",
            parent=self.root,
            can_hold_stock=True,
        )
        self.other_root = Location.objects.create(
            code=f"CC-OR-{suffix}", name="Diğer kök"
        )
        self.other_location = Location.objects.create(
            code=f"CC-OL-{suffix}",
            name="Diğer raf",
            parent=self.other_root,
            can_hold_stock=True,
        )

    def _fixture_teardown(self):
        # Preserve migration-owned MaterialCondition seeds in the reused test DB.
        # This mirrors the repository's existing PostgreSQL concurrency-test
        # teardown contract instead of Django TransactionTestCase.flush().
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
                    corrections_correctionrequest,
                    inventory_issuecontext,
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance
                """
            )
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(
            pk__in=[self.location.pk, self.other_location.pk]
        ).delete()
        Location.objects.filter(pk__in=[self.root.pk, self.other_root.pk]).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk__in=[self.actor.pk, self.approver.pk]).delete()

    @staticmethod
    def _run_concurrently(*calls):
        ready = threading.Barrier(len(calls))

        def worker(call):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                try:
                    return call()
                except Exception as exc:  # exact cross-thread assertions
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    def _create_and_start(self, reference, scope_id):
        session = create_physical_count_session(
            actor=self.actor,
            reference_number=reference,
            scope_location_id=scope_id,
        )
        return start_physical_count_session(
            actor=self.actor, session_id=session.pk
        ).session

    def _draft(self, scope=None):
        return PhysicalCountSession.objects.create(
            reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
            scope_location=scope or self.root,
        )

    def _started_with_line(self, quantity=Decimal("1.000")):
        StockBalance.objects.create(
            material=self.material,
            location=self.location,
            condition=self.condition,
            quantity=quantity,
        )
        session = self._draft()
        start_physical_count_session(actor=self.actor, session_id=session.pk)
        return session, session.quantity_lines.get()

    def _completed_discrepancy(self, *, expected=Decimal("5.000"), counted=Decimal("7.000")):
        receive_quantity(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            target_location_id=self.location.pk,
            quantity=expected,
        )
        session = self._draft()
        start_physical_count_session(actor=self.actor, session_id=session.pk)
        line = session.quantity_lines.get()
        record_quantity_count(
            actor=self.actor,
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=counted,
        )
        complete_physical_count(actor=self.actor, session_id=session.pk)
        return session, line

    def test_two_approvals_same_discrepancy_create_one_effect(self):
        session, line = self._completed_discrepancy()
        results = self._run_concurrently(
            lambda: approve_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                operation_id=uuid.uuid4(), explanation="Birinci eşzamanlı onay açıklaması.",
            ),
            lambda: approve_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                operation_id=uuid.uuid4(), explanation="İkinci eşzamanlı onay açıklaması.",
            ),
        )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        self.assertEqual(
            InventoryTransaction.objects.filter(transaction_type="COUNT_RECONCILIATION").count(), 1
        )
        self.assertEqual(StockBalance.objects.get().quantity, Decimal("7.000"))

    def test_same_operation_approval_race_replays_one_effect(self):
        session, line = self._completed_discrepancy()
        operation_id = uuid.uuid4()
        results = self._run_concurrently(
            *[
                lambda: approve_quantity_discrepancy(
                    actor=self.approver, session_id=session.pk, line_id=line.pk,
                    operation_id=operation_id, explanation="Aynı semantik payload tekrarı.",
                )
                for _ in range(2)
            ]
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual({result.transaction.pk for result in results}, {results[0].transaction.pk})
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(
            InventoryTransaction.objects.filter(transaction_type="COUNT_RECONCILIATION").count(), 1
        )

    def test_approval_and_rejection_race_has_one_decision(self):
        session, line = self._completed_discrepancy()
        results = self._run_concurrently(
            lambda: approve_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                operation_id=uuid.uuid4(), explanation="Onay ve ret yarışı açıklaması.",
            ),
            lambda: reject_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                reason="Yeniden sayım istendi.",
            ),
        )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        session.refresh_from_db(); line.refresh_from_db()
        if line.resolution_status == "APPROVED":
            self.assertEqual(session.status, "COMPLETED")
            self.assertEqual(StockBalance.objects.get().quantity, Decimal("7.000"))
        else:
            self.assertEqual(session.status, "STARTED")
            self.assertEqual(StockBalance.objects.get().quantity, Decimal("5.000"))

    def test_inventory_movement_racing_approval_is_serializable_by_bucket_locks(self):
        session, line = self._completed_discrepancy()
        results = self._run_concurrently(
            lambda: approve_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                operation_id=uuid.uuid4(), explanation="Hareket yarışı drift kontrolü.",
            ),
            lambda: receive_quantity(
                actor=self.actor, operation_id=uuid.uuid4(), material_id=self.material.pk,
                unit_id=self.unit.pk, condition_id=self.condition.pk,
                target_location_id=self.location.pk, quantity=Decimal("1.000"),
            ),
        )
        movement = results[1]
        self.assertFalse(isinstance(movement, Exception))
        approval = results[0]
        final_quantity = StockBalance.objects.get().quantity
        if isinstance(approval, Exception):
            self.assertIsInstance(approval, ValidationError)
            self.assertEqual(approval.code, "inventory.count_reconciliation_drift")
            self.assertEqual(final_quantity, Decimal("6.000"))
        else:
            self.assertEqual(final_quantity, Decimal("8.000"))

    def test_unexpected_zero_snapshot_racing_receipt_never_double_counts(self):
        session = self._draft()
        start_physical_count_session(actor=self.actor, session_id=session.pk)
        line = add_unexpected_quantity_count(
            actor=self.actor, session_id=session.pk, material_id=self.material.pk,
            location_id=self.location.pk, condition_id=self.condition.pk,
            counted_quantity=Decimal("4.000"),
        )
        complete_physical_count(actor=self.actor, session_id=session.pk)
        results = self._run_concurrently(
            lambda: approve_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                operation_id=uuid.uuid4(), explanation="Beklenmeyen stok yarış kontrolü.",
            ),
            lambda: receive_quantity(
                actor=self.actor, operation_id=uuid.uuid4(), material_id=self.material.pk,
                unit_id=self.unit.pk, condition_id=self.condition.pk,
                target_location_id=self.location.pk, quantity=Decimal("1.000"),
            ),
        )
        self.assertFalse(isinstance(results[1], Exception))
        final_quantity = StockBalance.objects.get().quantity
        if isinstance(results[0], Exception):
            self.assertEqual(results[0].code, "inventory.count_reconciliation_drift")
            self.assertEqual(final_quantity, Decimal("1.000"))
        else:
            self.assertEqual(final_quantity, Decimal("5.000"))

    def test_recount_racing_approval_cannot_rewrite_approved_basis(self):
        session, line = self._completed_discrepancy()
        results = self._run_concurrently(
            lambda: approve_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                operation_id=uuid.uuid4(), explanation="Onay sırasında tarihsel temel korunur.",
            ),
            lambda: record_quantity_count(
                actor=self.actor, session_id=session.pk, line_id=line.pk,
                counted_quantity=Decimal("9.000"), expected_counted_at=line.counted_at,
            ),
        )
        self.assertFalse(isinstance(results[0], Exception))
        self.assertIsInstance(results[1], ValidationError)
        self.assertEqual(results[1].code, INVALID_SESSION)
        line.refresh_from_db()
        self.assertEqual(line.counted_quantity, Decimal("7.000"))
        self.assertEqual(line.resolution_status, "APPROVED")

    def test_negative_reconciliation_racing_subtraction_never_goes_negative(self):
        session, line = self._completed_discrepancy(
            expected=Decimal("5.000"), counted=Decimal("3.000")
        )
        results = self._run_concurrently(
            lambda: approve_quantity_discrepancy(
                actor=self.approver, session_id=session.pk, line_id=line.pk,
                operation_id=uuid.uuid4(), explanation="Negatif fark yarış kontrolü.",
            ),
            lambda: transfer_quantity(
                actor=self.actor, operation_id=uuid.uuid4(), material_id=self.material.pk,
                unit_id=self.unit.pk, condition_id=self.condition.pk,
                source_location_id=self.location.pk,
                target_location_id=self.other_location.pk,
                quantity=Decimal("4.000"),
            ),
        )
        source = StockBalance.objects.get(
            material=self.material, location=self.location, condition=self.condition
        ).quantity
        self.assertGreaterEqual(source, Decimal("0.000"))
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        if isinstance(results[0], Exception):
            self.assertEqual(results[0].code, "inventory.count_reconciliation_drift")

    def test_same_operation_changed_count_payload_race_conflicts(self):
        receive_quantity(
            actor=self.actor, operation_id=uuid.uuid4(), material_id=self.material.pk,
            unit_id=self.unit.pk, condition_id=self.condition.pk,
            target_location_id=self.location.pk, quantity=Decimal("5.000"),
        )
        sessions_and_lines = []
        for counted in (Decimal("6.000"), Decimal("7.000")):
            session = self._draft()
            start_physical_count_session(actor=self.actor, session_id=session.pk)
            line = session.quantity_lines.get()
            record_quantity_count(
                actor=self.actor, session_id=session.pk, line_id=line.pk,
                counted_quantity=counted,
            )
            complete_physical_count(actor=self.actor, session_id=session.pk)
            sessions_and_lines.append((session, line))
        operation_id = uuid.uuid4()
        results = self._run_concurrently(
            *[
                lambda pair=pair: approve_quantity_discrepancy(
                    actor=self.approver, session_id=pair[0].pk, line_id=pair[1].pk,
                    operation_id=operation_id, explanation="Farklı semantik payload çakışması.",
                )
                for pair in sessions_and_lines
            ]
        )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        failures = [result for result in results if isinstance(result, ValidationError)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "inventory.operation_conflict")

    def test_two_overlapping_workflows_start_at_most_one_session(self):
        results = self._run_concurrently(
            lambda: self._create_and_start("CNT-OVERLAP-A", self.root.pk),
            lambda: self._create_and_start("CNT-OVERLAP-B", self.location.pk),
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, OVERLAPPING_SCOPE)
        self.assertEqual(
            PhysicalCountSession.objects.filter(status="STARTED").count(), 1
        )

    def test_two_nonoverlapping_sessions_may_both_start(self):
        results = self._run_concurrently(
            lambda: self._create_and_start("CNT-SEPARATE-A", self.root.pk),
            lambda: self._create_and_start("CNT-SEPARATE-B", self.other_root.pk),
        )

        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(
            PhysicalCountSession.objects.filter(status="STARTED").count(), 2
        )

    def test_committing_inventory_movement_is_captured_and_later_movement_does_not_rewrite_snapshot(self):
        session = self._draft()
        movement_written = threading.Event()
        allow_commit = threading.Event()
        start_entered = threading.Event()

        def movement_worker():
            close_old_connections()
            try:
                with transaction.atomic():
                    receive_quantity(
                        actor=self.actor,
                        operation_id=uuid.uuid4(),
                        material_id=self.material.pk,
                        unit_id=self.unit.pk,
                        condition_id=self.condition.pk,
                        target_location_id=self.location.pk,
                        quantity=Decimal("1.000"),
                    )
                    movement_written.set()
                    if not allow_commit.wait(timeout=10):
                        raise AssertionError("movement release event was not set")
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
            movement_future = executor.submit(movement_worker)
            self.assertTrue(movement_written.wait(timeout=10))
            start_future = executor.submit(start_worker)
            self.assertTrue(start_entered.wait(timeout=10))
            allow_commit.set()
            movement_future.result(timeout=20)
            start_result = start_future.result(timeout=20)

        line = start_result.quantity_lines[0]
        self.assertEqual(line.expected_quantity, Decimal("1.000"))
        receive_quantity(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            target_location_id=self.location.pk,
            quantity=Decimal("2.000"),
        )
        line.refresh_from_db()
        self.assertEqual(line.expected_quantity, Decimal("1.000"))
        self.assertEqual(StockBalance.objects.get().quantity, Decimal("3.000"))

    def test_two_counters_cannot_silently_overwrite_same_line(self):
        session, line = self._started_with_line()
        results = self._run_concurrently(
            lambda: record_quantity_count(
                actor=self.actor,
                session_id=session.pk,
                line_id=line.pk,
                counted_quantity=Decimal("1.000"),
            ),
            lambda: record_quantity_count(
                actor=self.actor,
                session_id=session.pk,
                line_id=line.pk,
                counted_quantity=Decimal("2.000"),
            ),
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, COUNT_CONFLICT)
        line.refresh_from_db()
        self.assertEqual(line.counted_quantity, successes[0].counted_quantity)

    def test_count_entry_and_explicit_not_counted_cannot_silently_overwrite(self):
        session, line = self._started_with_line()
        results = self._run_concurrently(
            lambda: record_quantity_count(
                actor=self.actor,
                session_id=session.pk,
                line_id=line.pk,
                counted_quantity=Decimal("1.000"),
            ),
            lambda: mark_quantity_line_not_counted(
                actor=self.actor,
                session_id=session.pk,
                line_id=line.pk,
            ),
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, COUNT_CONFLICT)
        line.refresh_from_db()
        self.assertEqual(line.resolution_status, successes[0].resolution_status)
        self.assertEqual(line.counted_at, successes[0].counted_at)

    def test_concurrent_unexpected_same_bucket_creates_one_canonical_line(self):
        session = self._draft()
        start_physical_count_session(actor=self.actor, session_id=session.pk)

        def add():
            return add_unexpected_quantity_count(
                actor=self.actor,
                session_id=session.pk,
                material_id=self.material.pk,
                location_id=self.location.pk,
                condition_id=self.condition.pk,
                counted_quantity=Decimal("1.000"),
            )

        results = self._run_concurrently(add, add)
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, DUPLICATE_BUCKET)
        self.assertEqual(session.quantity_lines.count(), 1)

    def test_start_snapshot_and_unexpected_create_race_never_duplicates_bucket(self):
        StockBalance.objects.create(
            material=self.material,
            location=self.location,
            condition=self.condition,
            quantity=Decimal("1.000"),
        )
        session = self._draft()
        results = self._run_concurrently(
            lambda: start_physical_count_session(
                actor=self.actor, session_id=session.pk
            ),
            lambda: add_unexpected_quantity_count(
                actor=self.actor,
                session_id=session.pk,
                material_id=self.material.pk,
                location_id=self.location.pk,
                condition_id=self.condition.pk,
                counted_quantity=Decimal("1.000"),
            ),
        )

        session.refresh_from_db()
        self.assertLessEqual(session.quantity_lines.count(), 1)
        self.assertFalse(
            any(
                not isinstance(result, Exception)
                and isinstance(result, PhysicalCountQuantityLine)
                for result in results
            )
        )
        failures = [result for result in results if isinstance(result, ValidationError)]
        self.assertTrue(failures)
        self.assertIn(failures[0].code, {INVALID_SESSION, DUPLICATE_BUCKET})

    def test_completion_and_count_entry_race_has_no_completed_uncounted_state(self):
        session, line = self._started_with_line()
        self._run_concurrently(
            lambda: complete_physical_count(actor=self.actor, session_id=session.pk),
            lambda: record_quantity_count(
                actor=self.actor,
                session_id=session.pk,
                line_id=line.pk,
                counted_quantity=Decimal("1.000"),
            ),
        )

        session.refresh_from_db()
        line.refresh_from_db()
        if session.status == PhysicalCountSession.Status.COMPLETED:
            self.assertIsNotNone(line.counted_quantity)
            self.assertNotEqual(line.resolution_status, "PENDING_COUNT")
        else:
            self.assertEqual(session.status, PhysicalCountSession.Status.STARTED)
            self.assertIsNotNone(line.counted_quantity)

    def test_start_transition_and_late_positive_insert_follow_trigger_contract(self):
        StockBalance.objects.create(
            material=self.material,
            location=self.location,
            condition=self.condition,
            quantity=Decimal("1.000"),
        )
        session = self._draft()

        def late_insert():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO counting_physicalcountquantityline (
                        id, expected_quantity, counted_quantity, counted_at,
                        resolution_status, created_at, condition_id,
                        counted_by_user_id, location_id, material_id, session_id
                    ) VALUES (%s, %s, NULL, NULL, %s, %s, %s, NULL, %s, %s, %s)
                    """,
                    [
                        uuid.uuid4(),
                        Decimal("1.000"),
                        "PENDING_COUNT",
                        timezone.now(),
                        self.condition.pk,
                        self.location.pk,
                        self.material.pk,
                        session.pk,
                    ],
                )
            return "inserted"

        results = self._run_concurrently(
            lambda: start_physical_count_session(
                actor=self.actor, session_id=session.pk
            ),
            late_insert,
        )

        session.refresh_from_db()
        self.assertLessEqual(session.quantity_lines.count(), 1)
        if session.status == PhysicalCountSession.Status.STARTED:
            self.assertEqual(session.quantity_lines.get().expected_quantity, Decimal("1.000"))
            self.assertTrue(any(isinstance(result, Exception) for result in results))
        else:
            self.assertEqual(session.status, PhysicalCountSession.Status.DRAFT)
            self.assertEqual(session.quantity_lines.count(), 1)
            start_errors = [
                result
                for result in results
                if isinstance(result, ValidationError)
                and result.code == INVALID_SESSION
            ]
            self.assertEqual(len(start_errors), 1)
