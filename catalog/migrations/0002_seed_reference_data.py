# Generated manually for Phase 2.2 — catalog reference data seeding.

import uuid

from django.db import migrations
from django.utils import timezone


# Fixed UUIDs identify rows created by this migration for safe reverse.
UNIT_SEED_ROWS = (
    {
        "id": uuid.UUID("b2022c02-0001-4001-8001-000000000001"),
        "code": "ADET",
        "name": "Adet",
    },
    {
        "id": uuid.UUID("b2022c02-0001-4001-8001-000000000002"),
        "code": "METRE",
        "name": "Metre",
    },
    {
        "id": uuid.UUID("b2022c02-0001-4001-8001-000000000003"),
        "code": "MAKARA",
        "name": "Makara",
    },
    {
        "id": uuid.UUID("b2022c02-0001-4001-8001-000000000004"),
        "code": "SET",
        "name": "Set",
    },
    {
        "id": uuid.UUID("b2022c02-0001-4001-8001-000000000005"),
        "code": "PAKET",
        "name": "Paket",
    },
)

CATEGORY_SEED_ROWS = (
    {
        "id": uuid.UUID("b2022c02-0002-4001-8001-000000000001"),
        "name": "Otomasyon",
    },
    {
        "id": uuid.UUID("b2022c02-0002-4001-8001-000000000002"),
        "name": "Elektrik / Şalt",
    },
    {
        "id": uuid.UUID("b2022c02-0002-4001-8001-000000000003"),
        "name": "Motorlar",
    },
    {
        "id": uuid.UUID("b2022c02-0002-4001-8001-000000000004"),
        "name": "Komponentler",
    },
    {
        "id": uuid.UUID("b2022c02-0002-4001-8001-000000000005"),
        "name": "X-Ray",
    },
    {
        "id": uuid.UUID("b2022c02-0002-4001-8001-000000000006"),
        "name": "ShapeMeter",
    },
    {
        "id": uuid.UUID("b2022c02-0002-4001-8001-000000000007"),
        "name": "Kablolar",
    },
)

SEEDED_UNIT_IDS = tuple(row["id"] for row in UNIT_SEED_ROWS)
SEEDED_CATEGORY_IDS = tuple(row["id"] for row in CATEGORY_SEED_ROWS)


def seed_reference_data(apps, schema_editor):
    UnitOfMeasure = apps.get_model("catalog", "UnitOfMeasure")
    Category = apps.get_model("catalog", "Category")
    now = timezone.now()

    for row in UNIT_SEED_ROWS:
        UnitOfMeasure.objects.update_or_create(
            id=row["id"],
            defaults={
                "code": row["code"],
                "name": row["name"],
                "decimal_places": None,
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )

    for row in CATEGORY_SEED_ROWS:
        Category.objects.update_or_create(
            id=row["id"],
            defaults={
                "code": None,
                "name": row["name"],
                "parent": None,
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )


def unseed_reference_data(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    UnitOfMeasure = apps.get_model("catalog", "UnitOfMeasure")

    Category.objects.filter(id__in=SEEDED_CATEGORY_IDS).delete()
    UnitOfMeasure.objects.filter(id__in=SEEDED_UNIT_IDS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_reference_data, unseed_reference_data),
    ]
