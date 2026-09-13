from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from catalog.models import Material, MaterialCondition
from inventory.services.projections import verify_quantity_projection
from locations.models import Location


class Command(BaseCommand):
    help = (
        "Read-only quantity projection verification. "
        "Compares ledger-derived expected balances with StockBalance rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help='Database alias to use (default: "default").',
        )

    def handle(self, *args, **options):
        database = options["database"]
        mismatches = verify_quantity_projection(using=database)

        if not mismatches:
            self.stdout.write(self.style.SUCCESS("Quantity projection is consistent."))
            return

        self.stderr.write(
            self.style.ERROR(
                f"Quantity projection drift detected: {len(mismatches)} mismatch(es)."
            )
        )
        material_ids = {mismatch.material_id for mismatch in mismatches}
        location_ids = {mismatch.location_id for mismatch in mismatches}
        condition_ids = {mismatch.condition_id for mismatch in mismatches}

        materials = {
            row.pk: row
            for row in Material.objects.using(database).filter(pk__in=material_ids)
        }
        locations = {
            row.pk: row
            for row in Location.objects.using(database).filter(pk__in=location_ids)
        }
        conditions = {
            row.pk: row
            for row in MaterialCondition.objects.using(database).filter(pk__in=condition_ids)
        }

        for mismatch in mismatches:
            material = materials.get(mismatch.material_id)
            location = locations.get(mismatch.location_id)
            condition = conditions.get(mismatch.condition_id)
            material_label = (
                f"{material.material_code} — {material.name}"
                if material is not None
                else str(mismatch.material_id)
            )
            location_label = (
                f"{location.code} — {location.name}"
                if location is not None
                else str(mismatch.location_id)
            )
            condition_label = (
                condition.name if condition is not None else str(mismatch.condition_id)
            )
            self.stderr.write(
                "- "
                f"material={material_label}; "
                f"location={location_label}; "
                f"condition={condition_label}; "
                f"expected={mismatch.expected_quantity}; "
                f"actual={mismatch.actual_quantity}"
            )

        raise CommandError("Quantity projection verification failed.")
