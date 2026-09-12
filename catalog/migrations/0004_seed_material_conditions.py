# Generated manually for Phase 4.0A — MaterialCondition reference data seeding.

import uuid

from django.db import migrations
from django.utils import timezone


CONDITION_SEED_NAMESPACE = uuid.UUID("b2024c04-0001-4001-8001-000000000001")

CONDITION_SEED_ROWS = (
    {
        "code": "NEW_GOOD",
        "name": "Yeni / Sağlam",
        "sort_order": 10,
    },
    {
        "code": "USED_REMOVED_GOOD",
        "name": "Çıkma / Sağlam",
        "sort_order": 20,
    },
    {
        "code": "DEFECTIVE",
        "name": "Bozuk",
        "sort_order": 30,
    },
    {
        "code": "USED_REMOVED_DEFECTIVE",
        "name": "Çıkma / Bozuk",
        "sort_order": 40,
    },
)


def _seed_id(code: str) -> uuid.UUID:
    return uuid.uuid5(CONDITION_SEED_NAMESPACE, code)


SEEDED_CONDITION_IDS = tuple(_seed_id(row["code"]) for row in CONDITION_SEED_ROWS)


def seed_material_conditions(apps, schema_editor):
    MaterialCondition = apps.get_model("catalog", "MaterialCondition")
    now = timezone.now()

    for row in CONDITION_SEED_ROWS:
        MaterialCondition.objects.update_or_create(
            id=_seed_id(row["code"]),
            defaults={
                "code": row["code"],
                "name": row["name"],
                "sort_order": row["sort_order"],
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )


def unseed_material_conditions(apps, schema_editor):
    MaterialCondition = apps.get_model("catalog", "MaterialCondition")
    MaterialCondition.objects.filter(id__in=SEEDED_CONDITION_IDS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0003_material_condition"),
    ]

    operations = [
        migrations.RunPython(seed_material_conditions, unseed_material_conditions),
    ]
