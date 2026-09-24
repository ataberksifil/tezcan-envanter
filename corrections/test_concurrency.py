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
from audit.models import AuditEvent
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from corrections.conftest import jpeg_upload
from corrections.models import CorrectionRequest
from corrections.services import approve_correction_request, create_correction_request
from inventory.models import InventoryTransaction, ProductionLine, StockBalance
from inventory.services.corrections import apply_controlled_correction
from inventory.services.issues import issue_quantity
from inventory.services.projections import verify_quantity_projection
from inventory.services.receipts import receive_quantity
from inventory.services.transfers import transfer_quantity
from locations.models import Location


class ControlledCorrectionConcurrencyTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.unit = UnitOfMeasure.objects.create(code=f"CRC-U-{suffix}", name="Adet")
        self.category = Category.objects.create(name=f"Correction race {suffix}")
        self.material = Material.objects.create(
            material_code=f"CRC-M-{suffix}",
            name="Correction race material",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"CRC-C-{suffix}", name="Good", sort_order=980
        )
        self.source = Location.objects.create(
            code=f"CRC-S-{suffix}", name="Source", can_hold_stock=True
        )
        self.target = Location.objects.create(
            code=f"CRC-T-{suffix}", name="Target", can_hold_stock=True
        )
        self.employee = Employee.objects.create(
            employee_number=f"CRC-E-{suffix}", first_name="Ayşe", last_name="Yılmaz"
        )
        self.production_line = ProductionLine.objects.create(
            code=f"CRC-PL-{suffix}", name="Correction race line"
        )
        self.requester = get_user_model().objects.create_user(
            username=f"crc-request-{suffix}"
        )
        self.approver = get_user_model().objects.create_user(
            username=f"crc-approve-{suffix}"
        )
        for user, codenames in (
            (self.requester, ("add_correctionrequest",)),
            (
                self.approver,
                (
                    "decide_correctionrequest",
                    "receive_stock",
                    "issue_stock",
                    "transfer_stock",
                ),
            ),
        ):
            user.user_permissions.add(
                *Permission.objects.filter(codename__in=codenames)
            )
        self.requester = get_user_model().objects.get(pk=self.requester.pk)
        self.approver = get_user_model().objects.get(pk=self.approver.pk)
        receipt = receive_quantity(
            actor=self.approver,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            target_location_id=self.source.pk,
            quantity=Decimal("10.000"),
        )
        self.original_transaction = receipt.transaction
        self.original_line = receipt.lines[0]

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
                    audit_auditevent,
                    corrections_correctionevidence,
                    corrections_correctionrequest,
                    inventory_receiptmetadata,
                    inventory_issuecontext,
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance
                """
            )
        get_user_model().objects.filter(
            pk__in=[self.requester.pk, self.approver.pk]
        ).delete()
        ProductionLine.objects.filter(pk=self.production_line.pk).delete()
        Employee.objects.filter(pk=self.employee.pk).delete()
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk__in=[self.source.pk, self.target.pk]).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()

    def _request(self, quantity=Decimal("-7.000")):
        return create_correction_request(
            actor=self.requester,
            original_transaction_id=self.original_transaction.pk,
            original_line_id=self.original_line.pk,
            original_location_id=self.source.pk,
            explanation="Concurrent correction request",
            effect_type="QUANTITY",
            quantity_effect=quantity,
            evidence_files=[jpeg_upload()],
        )

    @staticmethod
    def _run(*calls):
        barrier = threading.Barrier(len(calls))

        def worker(call):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return call()
                except Exception as exc:
                    return exc
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=30) for future in futures]

    def _balance(self):
        return StockBalance.objects.get(
            material=self.material,
            location=self.source,
            condition=self.condition,
        ).quantity

    def test_two_approvals_racing_same_request_apply_once_and_replay(self):
        request = self._request(Decimal("-2.000"))
        results = self._run(
            lambda: approve_correction_request(
                actor=self.approver, correction_request_id=request.pk
            ),
            lambda: approve_correction_request(
                actor=self.approver, correction_request_id=request.pk
            ),
        )
        self.assertTrue(all(not isinstance(result, Exception) for result in results))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(self._balance(), Decimal("8.000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type="CONTROLLED_CORRECTION"
            ).count(),
            1,
        )
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_approval_and_issue_same_bucket_do_not_go_negative(self):
        request = self._request()
        results = self._run(
            lambda: approve_correction_request(
                actor=self.approver, correction_request_id=request.pk
            ),
            lambda: issue_quantity(
                actor=self.approver,
                operation_id=uuid.uuid4(),
                material_id=self.material.pk,
                unit_id=self.unit.pk,
                condition_id=self.condition.pk,
                source_location_id=self.source.pk,
                quantity=Decimal("7.000"),
                receiver_employee_id=self.employee.pk,
                production_line_id=self.production_line.pk,
                usage_location_text="Pano 7",
            ),
        )
        self._assert_one_source_decrease(results, request)

    def test_approval_and_transfer_same_bucket_do_not_go_negative(self):
        request = self._request()
        results = self._run(
            lambda: approve_correction_request(
                actor=self.approver, correction_request_id=request.pk
            ),
            lambda: transfer_quantity(
                actor=self.approver,
                operation_id=uuid.uuid4(),
                material_id=self.material.pk,
                unit_id=self.unit.pk,
                condition_id=self.condition.pk,
                source_location_id=self.source.pk,
                target_location_id=self.target.pk,
                quantity=Decimal("7.000"),
            ),
        )
        self._assert_one_source_decrease(results, request)

    def _assert_one_source_decrease(self, results, request):
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(self._balance(), Decimal("3.000"))
        self.assertGreaterEqual(self._balance(), Decimal("0.000"))
        request.refresh_from_db()
        if request.status == CorrectionRequest.Status.PENDING:
            self.assertIsNone(request.resulting_transaction_id)
        self.assertEqual(verify_quantity_projection(), ())

    def test_two_kernel_corrections_same_line_serialize_cumulative_floor(self):
        def mutation():
            return apply_controlled_correction(
                actor=self.approver,
                operation_id=uuid.uuid4(),
                original_line_id=self.original_line.pk,
                original_location_id=self.source.pk,
                effect_type="QUANTITY",
                quantity_effect=Decimal("-6.000"),
            )

        results = self._run(mutation, mutation)
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(self._balance(), Decimal("4.000"))
        self.assertEqual(verify_quantity_projection(), ())
