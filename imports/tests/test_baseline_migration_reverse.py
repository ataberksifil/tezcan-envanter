from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import IntegrityError
from django.test import TransactionTestCase

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.services import (
    add_unexpected_quantity_count,
    complete_physical_count,
    create_physical_count_session,
    start_physical_count_session,
)
from imports.services import establish_inventory_baseline, prepare_inventory_baseline
from inventory.services.baselines import QuantityOpening, establish_initial_balance
from locations.models import Location


INVENTORY_0011 = "0011_initial_balance_kernel"
INVENTORY_0012 = "0012_serialized_inventory_movements"
IMPORTS_0001 = "0001_inventory_baseline"


def _applied(app, name):
    return MigrationRecorder.Migration.objects.filter(app=app, name=name).exists()


class BaselineMigrationReverseTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.counter = get_user_model().objects.create_user(username=f"mbl-c-{suffix}")
        self.establisher = get_user_model().objects.create_user(
            username=f"mbl-e-{suffix}"
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
        self.category = Category.objects.create(code=f"MBL-CAT-{suffix}", name="Kat")
        self.unit = UnitOfMeasure.objects.create(code=f"MBL-U-{suffix}", name="Adet")
        self.material = Material.objects.create(
            material_code=f"MBL-M-{suffix}",
            name="Malzeme",
            category=self.category,
            unit=self.unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
        self.condition = MaterialCondition.objects.create(
            code=f"MBL-C-{suffix}", name="Kondisyon", sort_order=870
        )
        self.root = Location.objects.create(code=f"MBL-R-{suffix}", name="Kök")
        self.location = Location.objects.create(
            code=f"MBL-L-{suffix}",
            name="Raf",
            parent=self.root,
            can_hold_stock=True,
        )

    def _fixture_teardown(self):
        if not _applied("imports", IMPORTS_0001):
            call_command("migrate", "imports", verbosity=0)
        if not _applied("inventory", INVENTORY_0012):
            call_command("migrate", "inventory", verbosity=0)
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
                    inventory_expiryinspection,
                    inventory_receiptexpiry,
                    inventory_receiptmetadata,
                    inventory_issuecontext,
                    inventory_inventorytransactionline,
                    inventory_inventorytransaction,
                    inventory_stockbalance,
                    inventory_serializedasset
                """
            )
        Material.objects.filter(pk=self.material.pk).delete()
        Location.objects.filter(pk__in=[self.location.pk, self.root.pk]).delete()
        MaterialCondition.objects.filter(pk=self.condition.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(
            pk__in=[self.counter.pk, self.establisher.pk]
        ).delete()

    def test_inventory_reverse_refuses_while_initial_balance_exists(self):
        establish_initial_balance(
            actor=self.establisher,
            operation_id=uuid.uuid4(),
            quantity_openings=[
                QuantityOpening(
                    material_id=self.material.pk,
                    location_id=self.location.pk,
                    condition_id=self.condition.pk,
                    quantity=Decimal("1.000"),
                )
            ],
        )
        try:
            with self.assertRaises(IntegrityError) as exc:
                call_command("migrate", "inventory", "0010", verbosity=0)
            self.assertIn("cannot reverse INITIAL_BALANCE", str(exc.exception))
            self.assertTrue(_applied("inventory", INVENTORY_0011))
        finally:
            call_command("migrate", verbosity=0)
            self.assertTrue(_applied("inventory", INVENTORY_0012))

    def test_imports_reverse_refuses_while_established_history_exists(self):
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
            counted_quantity=Decimal("2.000"),
        )
        complete_physical_count(actor=self.counter, session_id=session.pk)
        prepared = prepare_inventory_baseline(
            actor=self.establisher, session_ids=[session.pk]
        )
        establish_inventory_baseline(
            actor=self.establisher,
            baseline_id=prepared.baseline.pk,
            operation_id=uuid.uuid4(),
            explanation="Geri alma koruması için yeterince uzun açıklama.",
        )
        with self.assertRaises(IntegrityError) as exc:
            call_command("migrate", "imports", "zero", verbosity=0)
        self.assertIn("authoritative history exists", str(exc.exception))
        self.assertTrue(_applied("imports", IMPORTS_0001))

    def test_empty_history_can_reverse_and_reapply(self):
        self.assertTrue(_applied("imports", IMPORTS_0001))
        call_command("migrate", "imports", "zero", verbosity=0)
        self.assertFalse(_applied("imports", IMPORTS_0001))
        call_command("migrate", "inventory", "0010", verbosity=0)
        self.assertFalse(_applied("inventory", INVENTORY_0011))
        call_command("migrate", verbosity=0)
        self.assertTrue(_applied("inventory", INVENTORY_0011))
        self.assertTrue(_applied("inventory", INVENTORY_0012))
        self.assertTrue(_applied("imports", IMPORTS_0001))
