import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.forms import ExpiryInspectionForm
from inventory.models import ExpiryInspection
from inventory.services.expiry import record_physical_inspection
from inventory.services.receipts import receive_quantity
from locations.models import Location

INVENTORY_0014 = "0014_receipt_expiry"
INVENTORY_0015 = "0015_expiry_inspection_outcome"
LEGACY_OUTCOME = "SONUC_KAYDEDILMEMIS"


def _applied(name):
    return MigrationRecorder.Migration.objects.filter(app="inventory", name=name).exists()


class ExpiryInspectionOutcomeMigrationTests(TransactionTestCase):
    databases = {"default"}

    def test_pre_outcome_inspection_becomes_legacy_and_cannot_be_chosen(self):
        self.assertTrue(_applied(INVENTORY_0015))
        suffix = uuid.uuid4().hex[:8]
        note = "Eski fiziksel kontrol notu olduğu gibi kalmalıdır."
        recorded_at = timezone.now()
        inspection_id = uuid.uuid4()
        try:
            call_command("migrate", "inventory", INVENTORY_0014, verbosity=0)
            self.assertFalse(_applied(INVENTORY_0015))
            actor = get_user_model().objects.create_user(username=f"skt-mig-{suffix}")
            actor.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="inventory",
                    content_type__model="inventorytransaction",
                    codename="receive_stock",
                )
            )
            actor = get_user_model().objects.get(pk=actor.pk)
            unit = UnitOfMeasure.objects.create(code=f"SKT-U-{suffix}", name="Adet")
            category = Category.objects.create(name=f"SKT kat {suffix}")
            material = Material.objects.create(
                material_code=f"SKT-MIG-{suffix}",
                name="SKT malzeme",
                category=category,
                unit=unit,
                tracking_mode=Material.TrackingMode.QUANTITY,
            )
            condition = MaterialCondition.objects.create(
                code=f"SKT-C-{suffix}",
                name="Yeni",
                sort_order=910,
            )
            location = Location.objects.create(
                code=f"SKT-L-{suffix}",
                name="Mal kabul",
                active=True,
                can_hold_stock=True,
            )
            receipt = receive_quantity(
                actor=actor,
                operation_id=uuid.uuid4(),
                material_id=material.pk,
                unit_id=unit.pk,
                condition_id=condition.pk,
                target_location_id=location.pk,
                quantity=Decimal("1.000"),
                expires_on=date(2026, 10, 1),
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO inventory_expiryinspection
                        (id, receipt_expiry_id, actor_id, note, recorded_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        inspection_id,
                        receipt.transaction.pk,
                        actor.pk,
                        note,
                        recorded_at,
                    ],
                )
            call_command("migrate", "inventory", INVENTORY_0015, verbosity=0)
            row = ExpiryInspection.objects.get(pk=inspection_id)
            self.assertEqual(row.outcome, LEGACY_OUTCOME)
            self.assertEqual(row.note, note)
            self.assertEqual(row.actor_id, actor.pk)
            self.assertEqual(row.recorded_at, recorded_at)
            self.assertEqual(
                row.get_outcome_display(),
                "Sonuç kaydedilmemiş (eski kayıt)",
            )
            form = ExpiryInspectionForm(
                data={
                    "outcome": LEGACY_OUTCOME,
                    "note": "Yeni kayıt eski sonucu seçemez.",
                }
            )
            self.assertFalse(form.is_valid())
            self.assertIn("outcome", form.errors)
            with self.assertRaises(ValidationError):
                record_physical_inspection(
                    actor=actor,
                    receipt_id=receipt.transaction.pk,
                    outcome=LEGACY_OUTCOME,
                    note="Servis eski sonucu yeni kayıt olarak kabul etmez.",
                )
        finally:
            call_command("migrate", "inventory", verbosity=0)
        self.assertTrue(_applied(INVENTORY_0015))
