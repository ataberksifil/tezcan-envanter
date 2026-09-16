from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import OperationalError, close_old_connections, connection, connections
from django.test import TransactionTestCase

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.services import (
    add_candidate_serialized_count,
    add_unexpected_quantity_count,
    complete_physical_count,
    create_physical_count_session,
    record_quantity_count,
    start_physical_count_session,
)
from imports.models import InventoryBaseline
from imports.services import establish_inventory_baseline, prepare_inventory_baseline
from inventory.models import InventoryTransaction, SerializedAsset, StockBalance
from inventory.services.projections import (
    verify_quantity_projection,
    verify_serialized_projection,
)
from inventory.services.receipts import receive_quantity, receive_serialized
from locations.models import Location


EXPLANATION = "Eşzamanlı kesim açıklaması yeterince uzun metindir."


class BaselineConcurrencyTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.counter = get_user_model().objects.create_user(username=f"bconc-c-{suffix}")
        self.establisher = get_user_model().objects.create_user(
            username=f"bconc-e-{suffix}"
        )
        self.counter.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                content_type__model="inventorytransaction",
                codename="receive_stock",
            )
        )
        self.establisher.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="imports",
                content_type__model="inventorybaseline",
                codename="establish_baseline",
            )
        )
        self.counter = get_user_model().objects.get(pk=self.counter.pk)
        self.establisher = get_user_model().objects.get(pk=self.establisher.pk)
        self.category = Category.objects.create(code=f"BC-CAT-{suffix}", name="Kat")
        self.unit = UnitOfMeasure.objects.create(code=f"BC-U-{suffix}", name="Adet")
        self.material = Material.objects.create(
            material_code=f"BC-M-{suffix}",
            name="Miktar",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.serialized_material = Material.objects.create(
            material_code=f"BC-S-{suffix}",
            name="Tekil",
            category=self.category,
            tracking_mode=Material.TrackingMode.SERIALIZED,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"BC-C-{suffix}", name="Kondisyon", sort_order=860
        )
        self.root = Location.objects.create(code=f"BC-R-{suffix}", name="Kök")
        self.location = Location.objects.create(
            code=f"BC-L-{suffix}",
            name="Raf",
            parent=self.root,
            can_hold_stock=True,
        )

    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    audit_auditevent,
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
        from accounts.models import Employee
        from inventory.models import ProductionLine

        Material.objects.filter(
            pk__in=[self.material.pk, self.serialized_material.pk]
        ).delete()
        Location.objects.filter(
            pk__in=[
                self.location.pk,
                self.root.pk,
                *getattr(self, "_extra_location_ids", ()),
            ]
        ).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        Employee.objects.filter(employee_number__startswith="BC-E-").delete()
        ProductionLine.objects.filter(code__startswith="BC-PL-").delete()
        get_user_model().objects.filter(
            pk__in=[self.counter.pk, self.establisher.pk]
        ).delete()

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
            return [future.result(timeout=30) for future in futures]

    def _opening_session(self):
        session = create_physical_count_session(
            actor=self.counter,
            reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
            scope_location_id=self.root.pk,
            baseline_candidate=True,
        )
        start_physical_count_session(actor=self.counter, session_id=session.pk)
        add_unexpected_quantity_count(
            actor=self.counter,
            session_id=session.pk,
            material_id=self.material.pk,
            location_id=self.location.pk,
            condition_id=self.condition.pk,
            counted_quantity=Decimal("3.000"),
        )
        complete_physical_count(actor=self.counter, session_id=session.pk)
        return session

    def _matching_session(self, quantity=Decimal("4.000")):
        receive_quantity(
            actor=self.counter,
            operation_id=uuid.uuid4(),
            material_id=self.material.pk,
            unit_id=self.unit.pk,
            condition_id=self.condition.pk,
            target_location_id=self.location.pk,
            quantity=quantity,
        )
        session = create_physical_count_session(
            actor=self.counter,
            reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
            scope_location_id=self.root.pk,
            baseline_candidate=True,
        )
        start_physical_count_session(actor=self.counter, session_id=session.pk)
        line = session.quantity_lines.get()
        record_quantity_count(
            actor=self.counter,
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=quantity,
        )
        complete_physical_count(actor=self.counter, session_id=session.pk)
        return session

    def test_baseline_versus_quantity_receive(self):
        session = self._matching_session()
        prepared = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session.pk]
        )

        def do_establish():
            return establish_inventory_baseline(
                actor=self.establisher,
                baseline_id=prepared.baseline.pk,
                operation_id=uuid.uuid4(),
                explanation=EXPLANATION,
            )

        def do_receive():
            return receive_quantity(
                actor=self.counter,
                operation_id=uuid.uuid4(),
                material_id=self.material.pk,
                unit_id=self.unit.pk,
                condition_id=self.condition.pk,
                target_location_id=self.location.pk,
                quantity=Decimal("1.000"),
            )

        results = self._run_concurrently(do_establish, do_receive)
        establish_result, receive_result = results
        self.assertFalse(isinstance(receive_result, Exception), receive_result)
        balance = StockBalance.objects.get(
            material=self.material, location=self.location, condition=self.condition
        )
        self.assertEqual(balance.quantity, Decimal("5.000"))
        baseline = InventoryBaseline.objects.get(pk=prepared.baseline.pk)
        if isinstance(establish_result, Exception):
            self.assertIsInstance(establish_result, ValidationError)
            self.assertEqual(establish_result.code, "imports.count_drift")
            self.assertEqual(baseline.status, InventoryBaseline.Status.PREPARED)
        else:
            self.assertEqual(baseline.status, InventoryBaseline.Status.ESTABLISHED)

    def test_duplicate_establishment_has_one_effect(self):
        session = self._opening_session()
        prepared = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session.pk]
        )
        operation_id = uuid.uuid4()

        def do_establish():
            return establish_inventory_baseline(
                actor=self.establisher,
                baseline_id=prepared.baseline.pk,
                operation_id=operation_id,
                explanation=EXPLANATION,
            )

        results = self._run_concurrently(do_establish, do_establish)
        successes = [item for item in results if not isinstance(item, Exception)]
        self.assertEqual(len(successes), 2)
        self.assertEqual(
            InventoryTransaction.objects.filter(transaction_type="INITIAL_BALANCE").count(),
            1,
        )
        self.assertEqual(StockBalance.objects.get().quantity, Decimal("3.000"))
        self.assertEqual(
            {result.replayed for result in successes},
            {True, False},
        )

    def test_competing_baselines_same_session(self):
        session = self._opening_session()

        def do_prepare():
            return prepare_inventory_baseline(
                actor=self.establisher,
                session_ids=[session.pk],
                reference=f"BL-{uuid.uuid4().hex[:10]}",
            )

        results = self._run_concurrently(do_prepare, do_prepare)
        successes = [item for item in results if not isinstance(item, Exception)]
        errors = [item for item in results if isinstance(item, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(InventoryBaseline.objects.count(), 1)

    def test_baseline_versus_quantity_issue(self):
        from accounts.models import Employee
        from inventory.models import ProductionLine
        from inventory.services.issues import issue_quantity

        self.counter.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                content_type__model="inventorytransaction",
                codename="issue_stock",
            )
        )
        self.counter = get_user_model().objects.get(pk=self.counter.pk)
        session = self._matching_session()
        prepared = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session.pk]
        )
        employee = Employee.objects.create(
            employee_number=f"BC-E-{uuid.uuid4().hex[:6]}",
            first_name="Ali",
            last_name="Kaya",
        )
        production_line = ProductionLine.objects.create(
            code=f"BC-PL-{uuid.uuid4().hex[:6]}", name="Hat"
        )

        def do_establish():
            return establish_inventory_baseline(
                actor=self.establisher,
                baseline_id=prepared.baseline.pk,
                operation_id=uuid.uuid4(),
                explanation=EXPLANATION,
            )

        def do_issue():
            return issue_quantity(
                actor=self.counter,
                operation_id=uuid.uuid4(),
                material_id=self.material.pk,
                unit_id=self.unit.pk,
                condition_id=self.condition.pk,
                source_location_id=self.location.pk,
                quantity=Decimal("1.000"),
                receiver_employee_id=employee.pk,
                production_line_id=production_line.pk,
                usage_location_text="Hat kenarı kullanım yeri",
            )

        results = self._run_concurrently(do_establish, do_issue)
        establish_result, issue_result = results
        self.assertFalse(isinstance(issue_result, Exception), issue_result)
        balance = StockBalance.objects.get(
            material=self.material, location=self.location, condition=self.condition
        )
        self.assertEqual(balance.quantity, Decimal("3.000"))
        baseline = InventoryBaseline.objects.get(pk=prepared.baseline.pk)
        if isinstance(establish_result, Exception):
            self.assertIsInstance(establish_result, ValidationError)
            self.assertEqual(establish_result.code, "imports.count_drift")
            self.assertEqual(baseline.status, InventoryBaseline.Status.PREPARED)
        else:
            self.assertEqual(baseline.status, InventoryBaseline.Status.ESTABLISHED)

    def test_candidate_versus_serialized_receive_same_code(self):
        session = create_physical_count_session(
            actor=self.counter,
            reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
            scope_location_id=self.root.pk,
            baseline_candidate=True,
        )
        start_physical_count_session(actor=self.counter, session_id=session.pk)
        add_candidate_serialized_count(
            actor=self.counter,
            session_id=session.pk,
            material_id=self.serialized_material.pk,
            internal_asset_code="RACE-CODE",
            serial_number=None,
            observed_location_id=self.location.pk,
            observed_condition_id=self.condition.pk,
        )
        complete_physical_count(actor=self.counter, session_id=session.pk)
        prepared = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session.pk]
        )

        def do_establish():
            return establish_inventory_baseline(
                actor=self.establisher,
                baseline_id=prepared.baseline.pk,
                operation_id=uuid.uuid4(),
                explanation=EXPLANATION,
            )

        def do_receive():
            return receive_serialized(
                actor=self.counter,
                operation_id=uuid.uuid4(),
                material_id=self.serialized_material.pk,
                internal_asset_code="RACE-CODE",
                serial_number=None,
                condition_id=self.condition.pk,
                target_location_id=self.location.pk,
            )

        results = self._run_concurrently(do_establish, do_receive)
        self.assertEqual(SerializedAsset.objects.filter(internal_asset_code="RACE-CODE").count(), 1)
        genesis = InventoryTransaction.objects.filter(
            lines__serialized_asset__internal_asset_code="RACE-CODE"
        ).distinct()
        self.assertEqual(genesis.count(), 1)
        self.assertIn(
            genesis.get().transaction_type,
            {
                InventoryTransaction.TransactionType.RECEIPT,
                InventoryTransaction.TransactionType.INITIAL_BALANCE,
            },
        )
        errors = [item for item in results if isinstance(item, Exception)]
        self.assertTrue(errors or InventoryBaseline.objects.get().status == "ESTABLISHED")

    def test_candidate_versus_serialized_receive_same_serial(self):
        session = create_physical_count_session(
            actor=self.counter,
            reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
            scope_location_id=self.root.pk,
            baseline_candidate=True,
        )
        start_physical_count_session(actor=self.counter, session_id=session.pk)
        add_candidate_serialized_count(
            actor=self.counter,
            session_id=session.pk,
            material_id=self.serialized_material.pk,
            internal_asset_code="CAND-SN",
            serial_number="SHARED-SN",
            observed_location_id=self.location.pk,
            observed_condition_id=self.condition.pk,
        )
        complete_physical_count(actor=self.counter, session_id=session.pk)
        prepared = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session.pk]
        )

        def do_establish():
            return establish_inventory_baseline(
                actor=self.establisher,
                baseline_id=prepared.baseline.pk,
                operation_id=uuid.uuid4(),
                explanation=EXPLANATION,
            )

        def do_receive():
            return receive_serialized(
                actor=self.counter,
                operation_id=uuid.uuid4(),
                material_id=self.serialized_material.pk,
                internal_asset_code="RECV-SN",
                serial_number="SHARED-SN",
                condition_id=self.condition.pk,
                target_location_id=self.location.pk,
            )

        results = self._run_concurrently(do_establish, do_receive)
        self.assertEqual(
            SerializedAsset.objects.filter(
                material=self.serialized_material, serial_number="SHARED-SN"
            ).count(),
            1,
        )
        errors = [item for item in results if isinstance(item, Exception)]
        self.assertTrue(errors or InventoryBaseline.objects.get().status == "ESTABLISHED")

    def test_concurrent_different_baselines_serialized_candidates_do_not_deadlock(self):
        suffix = uuid.uuid4().hex[:8]
        root_b = Location.objects.create(code=f"BC-R2-{suffix}", name="Kök 2")
        location_b = Location.objects.create(
            code=f"BC-L2-{suffix}",
            name="Raf 2",
            parent=root_b,
            can_hold_stock=True,
        )
        self._extra_location_ids = (root_b.pk, location_b.pk)

        def _candidate_session(scope_root, observed_location, code):
            session = create_physical_count_session(
                actor=self.counter,
                reference_number=f"CNT-{uuid.uuid4().hex[:12]}",
                scope_location_id=scope_root.pk,
                baseline_candidate=True,
            )
            start_physical_count_session(actor=self.counter, session_id=session.pk)
            add_candidate_serialized_count(
                actor=self.counter,
                session_id=session.pk,
                material_id=self.serialized_material.pk,
                internal_asset_code=code,
                serial_number=None,
                observed_location_id=observed_location.pk,
                observed_condition_id=self.condition.pk,
            )
            complete_physical_count(actor=self.counter, session_id=session.pk)
            return session

        session_a = _candidate_session(self.root, self.location, "BASE-A")
        session_b = _candidate_session(root_b, location_b, "BASE-B")
        prepared_a = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session_a.pk]
        )
        prepared_b = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session_b.pk]
        )

        def do_establish_a():
            return establish_inventory_baseline(
                actor=self.establisher,
                baseline_id=prepared_a.baseline.pk,
                operation_id=uuid.uuid4(),
                explanation=EXPLANATION,
            )

        def do_establish_b():
            return establish_inventory_baseline(
                actor=self.establisher,
                baseline_id=prepared_b.baseline.pk,
                operation_id=uuid.uuid4(),
                explanation=EXPLANATION,
            )

        results = self._run_concurrently(do_establish_a, do_establish_b)
        for result in results:
            self.assertFalse(isinstance(result, OperationalError), result)
            self.assertFalse(isinstance(result, Exception), result)
        self.assertEqual(
            InventoryBaseline.objects.filter(
                status=InventoryBaseline.Status.ESTABLISHED
            ).count(),
            2,
        )
        self.assertEqual(
            SerializedAsset.objects.filter(
                internal_asset_code__in=["BASE-A", "BASE-B"]
            ).count(),
            2,
        )
        self.assertEqual(
            InventoryTransaction.objects.filter(
                transaction_type="INITIAL_BALANCE"
            ).count(),
            2,
        )
        self.assertEqual(verify_quantity_projection(), ())
        self.assertEqual(verify_serialized_projection(), ())

