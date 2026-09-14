from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from catalog.models import Material, MaterialCondition
from inventory.services.projections import (
    verify_quantity_projection,
    verify_serialized_projection,
)
from locations.models import Location


class Command(BaseCommand):
    help = (
        "Read-only inventory projection verification. Compares quantity balances "
        "and serialized current state with immutable ledger-derived expectations."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help='Database alias to use (default: "default").',
        )

    def handle(self, *args, **options):
        database = options["database"]
        quantity_mismatches = verify_quantity_projection(using=database)
        serialized_mismatches = verify_serialized_projection(using=database)

        if not quantity_mismatches and not serialized_mismatches:
            self.stdout.write(self.style.SUCCESS("Inventory projection is consistent."))
            return

        if quantity_mismatches:
            self.stderr.write(
                self.style.ERROR(
                    "Quantity projection drift detected: "
                    f"{len(quantity_mismatches)} mismatch(es)."
                )
            )
        material_ids = {mismatch.material_id for mismatch in quantity_mismatches}
        location_ids = {mismatch.location_id for mismatch in quantity_mismatches}
        condition_ids = {mismatch.condition_id for mismatch in quantity_mismatches}

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

        for mismatch in quantity_mismatches:
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

        if serialized_mismatches:
            self.stderr.write(
                self.style.ERROR(
                    "Serialized projection drift detected: "
                    f"{len(serialized_mismatches)} mismatch(es)."
                )
            )
            for mismatch in serialized_mismatches:
                self.stderr.write(
                    "- "
                    f"asset={mismatch.internal_asset_code} ({mismatch.asset_id}); "
                    f"reasons={','.join(mismatch.reasons)}; "
                    f"expected_material={mismatch.expected_material_id}; "
                    f"actual_material={mismatch.actual_material_id}; "
                    f"expected_location={mismatch.expected_location_id}; "
                    f"actual_location={mismatch.actual_location_id}; "
                    f"expected_condition={mismatch.expected_condition_id}; "
                    f"actual_condition={mismatch.actual_condition_id}; "
                    f"expected_state={mismatch.expected_state}; "
                    f"actual_state={mismatch.actual_state}"
                )

        raise CommandError("Inventory projection verification failed.")
