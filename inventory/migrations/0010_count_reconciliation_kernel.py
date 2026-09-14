import importlib

from django.db import migrations, models


_previous = importlib.import_module(
    "inventory.migrations.0009_serialized_inventory_foundation"
)


def _replace_once(sql, old, new):
    if sql.count(old) != 1:
        raise RuntimeError(f"expected exactly one inventory guard fragment: {old[:60]!r}")
    return sql.replace(old, new, 1)


def _function_definition(sql, function_name):
    start = sql.index(f"CREATE OR REPLACE FUNCTION public.{function_name}()")
    end = sql.index("$$;", start) + 3
    return sql[start:end] + "\n"


def _count_guard_sql():
    sql = _previous.SERIALIZED_GUARD_SQL
    sql = _replace_once(
        sql,
        """    IF NEW.transaction_type = 'CONTROLLED_CORRECTION'
       AND line_count NOT IN (1, 2) THEN
""",
        """    IF NEW.transaction_type = 'COUNT_RECONCILIATION'
       AND line_count <> 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_count_recon_requires_one_line',
            MESSAGE = 'COUNT_RECONCILIATION transaction must contain exactly one line';
    END IF;

    IF NEW.transaction_type = 'CONTROLLED_CORRECTION'
       AND line_count NOT IN (1, 2) THEN
""",
    )
    sql = _replace_once(
        sql,
        "IF parent_transaction_type IN ('ISSUE', 'RETURN', 'TRANSFER', 'CONTROLLED_CORRECTION') THEN",
        "IF parent_transaction_type IN ('ISSUE', 'RETURN', 'TRANSFER', 'CONTROLLED_CORRECTION', 'COUNT_RECONCILIATION') THEN",
    )
    sql = _replace_once(
        sql,
        """        IF parent_transaction_type = 'CONTROLLED_CORRECTION' THEN
""",
        """        IF parent_transaction_type = 'COUNT_RECONCILIATION'
           AND line_count <> 1 THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_count_recon_requires_one_line',
                MESSAGE = 'COUNT_RECONCILIATION transaction must contain exactly one line';
        END IF;

        IF parent_transaction_type = 'CONTROLLED_CORRECTION' THEN
""",
    )
    sql = _replace_once(
        sql,
        """    IF parent_transaction_type = 'TRANSFER' THEN
""",
        """    IF parent_transaction_type = 'COUNT_RECONCILIATION' THEN
        IF (NEW.source_location_id IS NULL) = (NEW.target_location_id IS NULL)
           OR NEW.original_issue_line_id IS NOT NULL
           OR NEW.corrected_line_id IS NOT NULL
           OR NEW.serialized_asset_id IS NOT NULL
           OR NEW.quantity IS NULL
           OR NEW.unit_id IS NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_count_recon_line_shape',
                MESSAGE = 'COUNT_RECONCILIATION requires one quantity direction and no ledger lineage';
        END IF;

        PERFORM 1
        FROM public.locations_location AS location
        WHERE location.id = COALESCE(NEW.source_location_id, NEW.target_location_id)
        FOR KEY SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = '23503',
                MESSAGE = 'COUNT_RECONCILIATION location does not exist';
        END IF;

        PERFORM 1
        FROM public.catalog_materialcondition AS condition
        WHERE condition.id = NEW.condition_id
        FOR KEY SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = '23503',
                MESSAGE = 'COUNT_RECONCILIATION condition does not exist';
        END IF;
        RETURN NEW;
    END IF;

    IF parent_transaction_type = 'TRANSFER' THEN
""",
    )
    return "\n".join(
        _function_definition(sql, name)
        for name in (
            "inventory_tx_requires_line_guard",
            "inventory_issue_line_cardinality_guard",
            "inventory_line_quantity_guard",
        )
    )


COUNT_GUARD_SQL = _count_guard_sql()

REVERSE_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.inventory_inventorytransaction
        WHERE transaction_type = 'COUNT_RECONCILIATION'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_count_recon_reverse_requires_no_data',
            MESSAGE = 'cannot reverse COUNT_RECONCILIATION kernel while data exists';
    END IF;
END;
$$;
""" + "\n".join(
    _function_definition(_previous.SERIALIZED_GUARD_SQL, name)
    for name in (
        "inventory_tx_requires_line_guard",
        "inventory_issue_line_cardinality_guard",
        "inventory_line_quantity_guard",
    )
)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0009_serialized_inventory_foundation")]

    operations = [
        migrations.RemoveConstraint(
            model_name="inventorytransaction",
            name="inventory_tx_type_supported",
        ),
        migrations.AlterField(
            model_name="inventorytransaction",
            name="transaction_type",
            field=models.CharField(
                choices=[
                    ("RECEIPT", "Stok girişi"),
                    ("ISSUE", "Stok çıkışı"),
                    ("RETURN", "Stok iadesi"),
                    ("TRANSFER", "Stok transferi"),
                    ("CONTROLLED_CORRECTION", "Kontrollü düzeltme"),
                    ("COUNT_RECONCILIATION", "Sayım mutabakatı"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorytransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    transaction_type__in=[
                        "RECEIPT",
                        "ISSUE",
                        "RETURN",
                        "TRANSFER",
                        "CONTROLLED_CORRECTION",
                        "COUNT_RECONCILIATION",
                    ]
                ),
                name="inventory_tx_type_supported",
            ),
        ),
        migrations.RunSQL(sql=COUNT_GUARD_SQL, reverse_sql=REVERSE_SQL),
    ]
