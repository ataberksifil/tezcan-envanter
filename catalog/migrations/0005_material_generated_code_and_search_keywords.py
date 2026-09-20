# Generated for DEC-037 inbound Material code + search-keyword foundation.

from django.db import migrations, models


GENERATED_CODE_SEQUENCE_SQL = """
CREATE SEQUENCE IF NOT EXISTS public.catalog_material_generated_code_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    NO MAXVALUE
    CACHE 1;
SELECT setval(
    'public.catalog_material_generated_code_seq',
    COALESCE(
        (
            SELECT MAX(CAST(SUBSTRING(material_code FROM 5) AS INTEGER))
            FROM public.catalog_material
            WHERE material_code ~ '^MAT-[0-9]{8}$'
        ),
        1
    ),
    EXISTS (
        SELECT 1
        FROM public.catalog_material
        WHERE material_code ~ '^MAT-[0-9]{8}$'
    )
);
"""

DROP_GENERATED_CODE_SEQUENCE_SQL = """
DROP SEQUENCE IF EXISTS public.catalog_material_generated_code_seq;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_seed_material_conditions"),
    ]

    operations = [
        migrations.AddField(
            model_name="material",
            name="search_keywords",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddConstraint(
            model_name="material",
            constraint=models.UniqueConstraint(
                condition=models.Q(("material_code__regex", r"^MAT-[0-9]{8}$")),
                fields=("material_code",),
                name="catalog_mat_generated_code_uniq",
            ),
        ),
        migrations.RunSQL(
            sql=GENERATED_CODE_SEQUENCE_SQL,
            reverse_sql=DROP_GENERATED_CODE_SEQUENCE_SQL,
        ),
    ]
