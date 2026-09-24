from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

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
    IssueContext,
    ProductionLine,
    SerializedAsset,
)
from inventory.services.issues import INVALID_ASSET_STATE, INVALID_SOURCE, issue_serialized
from inventory.services.projections import verify_serialized_projection
from inventory.services.receipts import OPERATION_CONFLICT, receive_serialized
from inventory.services.returns import (
    SERIALIZED_ISSUE_ALREADY_RETURNED,
    return_serialized,
)
from inventory.services.transfers import transfer_serialized
from locations.models import Location


class SerializedMovementConcurrencyTests(TransactionTestCase):
    """Deterministic PostgreSQL races using separate connections and barriers."""

    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.category = Category.objects.create(name=f"SMC kategori {suffix}")
        self.unit = UnitOfMeasure.objects.create(code=f"SMC-U-{suffix}", name="Adet")
        self.material = Material.objects.create(
            material_code=f"SMC-M-{suffix}",
            name="Tekil yarış malzemesi",
            category=self.category,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"SMC-C-{suffix}", name="Yeni", sort_order=870
        )
        self.location = Location.objects.create(
            code=f"SMC-L-{suffix}",
            name="Kaynak",
            active=True,
            can_hold_stock=True,
        )
        self.other_location = Location.objects.create(
            code=f"SMC-L2-{suffix}",
            name="Hedef",
            active=True,
            can_hold_stock=True,
        )
        self.third_location = Location.objects.create(
            code=f"SMC-L3-{suffix}",
            name="Üçüncü",
            active=True,
            can_hold_stock=True,
        )
        self.employee = Employee.objects.create(
            employee_number=f"SMC-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
        )
        self.production_line = ProductionLine.objects.create(
            code=f"SMC-PL-{suffix}", name="Hat"
        )
        self.actor = get_user_model().objects.create_user(username=f"smc-{suffix}")
        for codename in ("receive_stock", "issue_stock", "return_stock", "transfer_stock"):
            self.actor.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="inventory",
                    content_type__model="inventorytransaction",
                    codename=codename,
                )
            )
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self.asset = receive_serialized(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            internal_asset_code=f"SMC-A-{suffix}",
            serial_number=None,
            condition_id=self.condition.pk,
            target_location_id=self.location.pk,
        ).serialized_asset

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
                    inventory_receiptmetadata,
                    inventory_issuecontext,
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance,
                    inventory_serializedasset
                """
            )
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(
            pk__in=[self.location.pk, self.other_location.pk, self.third_location.pk]
        ).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        ProductionLine.objects.filter(pk=self.production_line.pk).delete()
        Employee.objects.filter(pk=self.employee.pk).delete()
        get_user_model().objects.filter(pk=self.actor.pk).delete()

    def _issue_request(self, asset=None, **overrides):
        asset = asset or self.asset
        values = {
            "actor": self.actor,
            "operation_id": uuid.uuid4(),
            "serialized_asset_id": asset.pk,
            "source_location_id": asset.current_location_id or self.location.pk,
            "condition_id": self.condition.pk,
            "receiver_employee_id": self.employee.pk,
            "production_line_id": self.production_line.pk,
            "usage_location_text": "Pano 7",
        }
        values.update(overrides)
        return values

    def _event_history(self, asset=None):
        asset = asset or self.asset
        return list(
            InventoryTransactionLine.objects.filter(serialized_asset=asset)
            .order_by("asset_event_seq")
            .values_list("asset_event_seq", "transaction__transaction_type")
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
                except Exception as exc:
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    def test_same_asset_two_issues_have_one_winner(self):
        results = self._run_concurrently(
            lambda: issue_serialized(**self._issue_request()),
            lambda: issue_serialized(**self._issue_request()),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(failures[0].code, INVALID_ASSET_STATE)
        self.assertEqual(
            InventoryTransaction.objects.filter(transaction_type="ISSUE").count(), 1
        )
        self.assertEqual(IssueContext.objects.count(), 1)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_state, SerializedAsset.CurrentState.ISSUED)
        self.assertIsNone(self.asset.current_location_id)
        self.assertEqual(verify_serialized_projection(), ())
        self.assertEqual(
            self._event_history(),
            [(1, "RECEIPT"), (2, "ISSUE")],
        )
        seqs = list(
            InventoryTransactionLine.objects.filter(
                serialized_asset=self.asset
            ).values_list("asset_event_seq", flat=True)
        )
        self.assertEqual(sorted(seqs), [1, 2])
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_issue_versus_transfer_has_one_serializable_outcome(self):
        results = self._run_concurrently(
            lambda: issue_serialized(**self._issue_request()),
            lambda: transfer_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                serialized_asset_id=self.asset.pk,
                source_location_id=self.location.pk,
                target_location_id=self.other_location.pk,
                condition_id=self.condition.pk,
            ),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertIn(failures[0].code, {INVALID_ASSET_STATE, INVALID_SOURCE})
        self.asset.refresh_from_db()
        self.assertEqual(InventoryTransaction.objects.count(), 2)
        self.assertEqual(verify_serialized_projection(), ())
        self.assertEqual(
            [seq for seq, _type in self._event_history()],
            [1, 2],
        )
        if successes[0].transaction.transaction_type == "ISSUE":
            self.assertEqual(self.asset.current_state, SerializedAsset.CurrentState.ISSUED)
            self.assertIsNone(self.asset.current_location_id)
        else:
            self.assertEqual(self.asset.current_state, SerializedAsset.CurrentState.IN_STOCK)
            self.assertEqual(self.asset.current_location_id, self.other_location.pk)

    def test_two_transfers_from_same_source_have_one_winner(self):
        results = self._run_concurrently(
            lambda: transfer_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                serialized_asset_id=self.asset.pk,
                source_location_id=self.location.pk,
                target_location_id=self.other_location.pk,
                condition_id=self.condition.pk,
            ),
            lambda: transfer_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                serialized_asset_id=self.asset.pk,
                source_location_id=self.location.pk,
                target_location_id=self.third_location.pk,
                condition_id=self.condition.pk,
            ),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, INVALID_SOURCE)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_state, SerializedAsset.CurrentState.IN_STOCK)
        self.assertIn(
            self.asset.current_location_id,
            {self.other_location.pk, self.third_location.pk},
        )
        self.assertEqual(verify_serialized_projection(), ())
        self.assertEqual(
            self._event_history(),
            [(1, "RECEIPT"), (2, "TRANSFER")],
        )

    def test_successive_transfers_match_event_sequence_order(self):
        first = transfer_serialized(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            serialized_asset_id=self.asset.pk,
            source_location_id=self.location.pk,
            target_location_id=self.other_location.pk,
            condition_id=self.condition.pk,
        )
        second = transfer_serialized(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            serialized_asset_id=self.asset.pk,
            source_location_id=self.other_location.pk,
            target_location_id=self.third_location.pk,
            condition_id=self.condition.pk,
        )
        self.assertEqual(first.lines[0].asset_event_seq, 2)
        self.assertEqual(second.lines[0].asset_event_seq, 3)
        self.assertEqual(
            self._event_history(),
            [(1, "RECEIPT"), (2, "TRANSFER"), (3, "TRANSFER")],
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_location_id, self.third_location.pk)
        self.assertEqual(verify_serialized_projection(), ())

    def test_opposing_transfers_do_not_deadlock(self):
        second = receive_serialized(
            actor=self.actor,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            internal_asset_code=f"SMC-B-{uuid.uuid4().hex[:8]}",
            serial_number=None,
            condition_id=self.condition.pk,
            target_location_id=self.other_location.pk,
        ).serialized_asset
        results = self._run_concurrently(
            lambda: transfer_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                serialized_asset_id=self.asset.pk,
                source_location_id=self.location.pk,
                target_location_id=self.other_location.pk,
                condition_id=self.condition.pk,
            ),
            lambda: transfer_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                serialized_asset_id=second.pk,
                source_location_id=self.other_location.pk,
                target_location_id=self.location.pk,
                condition_id=self.condition.pk,
            ),
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.asset.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.asset.current_location_id, self.other_location.pk)
        self.assertEqual(second.current_location_id, self.location.pk)
        self.assertEqual(verify_serialized_projection(), ())

    def test_duplicate_return_has_one_consumer(self):
        issue = issue_serialized(**self._issue_request())
        results = self._run_concurrently(
            lambda: return_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                original_issue_line_id=issue.lines[0].pk,
                serialized_asset_id=self.asset.pk,
                target_location_id=self.other_location.pk,
            ),
            lambda: return_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                original_issue_line_id=issue.lines[0].pk,
                serialized_asset_id=self.asset.pk,
                target_location_id=self.third_location.pk,
            ),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, SERIALIZED_ISSUE_ALREADY_RETURNED)
        self.assertEqual(
            InventoryTransaction.objects.filter(transaction_type="RETURN").count(), 1
        )
        self.assertEqual(verify_serialized_projection(), ())
        self.assertEqual(
            self._event_history(),
            [(1, "RECEIPT"), (2, "ISSUE"), (3, "RETURN")],
        )

    def test_return_versus_reissue_keeps_valid_order(self):
        issue = issue_serialized(**self._issue_request())
        results = self._run_concurrently(
            lambda: return_serialized(
                actor=self.actor,
                operation_id=uuid.uuid4(),
                original_issue_line_id=issue.lines[0].pk,
                serialized_asset_id=self.asset.pk,
                target_location_id=self.location.pk,
            ),
            lambda: issue_serialized(
                **self._issue_request(source_location_id=self.location.pk)
            ),
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertGreaterEqual(len(successes), 1)
        self.asset.refresh_from_db()
        self.assertEqual(verify_serialized_projection(), ())
        issue_count = InventoryTransaction.objects.filter(transaction_type="ISSUE").count()
        return_count = InventoryTransaction.objects.filter(transaction_type="RETURN").count()
        if return_count == 1 and issue_count == 2:
            self.assertEqual(len(successes), 2)
            self.assertEqual(self.asset.current_state, SerializedAsset.CurrentState.ISSUED)
            self.assertIsNone(self.asset.current_location_id)
            self.assertEqual(
                self._event_history(),
                [(1, "RECEIPT"), (2, "ISSUE"), (3, "RETURN"), (4, "ISSUE")],
            )
        else:
            self.assertEqual(return_count, 1)
            self.assertEqual(issue_count, 1)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].code, INVALID_ASSET_STATE)
            self.assertEqual(self.asset.current_state, SerializedAsset.CurrentState.IN_STOCK)
            self.assertEqual(self.asset.current_location_id, self.location.pk)
            self.assertEqual(
                self._event_history(),
                [(1, "RECEIPT"), (2, "ISSUE"), (3, "RETURN")],
            )

    def test_duplicate_operation_has_one_mutation_and_replay(self):
        request = self._issue_request(operation_id=uuid.uuid4())
        results = self._run_concurrently(
            lambda: issue_serialized(**request),
            lambda: issue_serialized(**request),
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].transaction.pk, results[1].transaction.pk)
        self.assertEqual(
            InventoryTransaction.objects.filter(transaction_type="ISSUE").count(), 1
        )
        self.assertEqual(verify_serialized_projection(), ())
        self.assertEqual(
            self._event_history(),
            [(1, "RECEIPT"), (2, "ISSUE")],
        )
