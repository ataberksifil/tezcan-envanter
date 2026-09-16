import importlib

from django.db import migrations, models


_previous = importlib.import_module(
    "inventory.migrations.0010_count_reconciliation_kernel"
)


def _replace_once(sql, old, new):
    if sql.count(old) != 1:
        raise RuntimeError(f"expected exactly one inventory guard fragment: {old[:80]!r}")
    return sql.replace(old, new, 1)


def _function_definition(sql, function_name):
    start = sql.index(f"CREATE OR REPLACE FUNCTION public.{function_name}()")
    end = sql.index("$$;", start) + 3
    return sql[start:end] + "\n"


def _initial_balance_guard_sql():
    sql = _previous.COUNT_GUARD_SQL
    sql = _replace_once(
        sql,
        "    IF NEW.transaction_type = 'RECEIPT' AND line_count < 1 THEN\n",
        "    IF NEW.transaction_type IN ('RECEIPT', 'INITIAL_BALANCE') AND line_count < 1 THEN\n",
    )
    sql = _replace_once(
        sql,
        """        IF parent_transaction_type <> 'RECEIPT'
           OR NEW.source_location_id IS NOT NULL
           OR NEW.target_location_id IS NULL
           OR NEW.original_issue_line_id IS NOT NULL
           OR NEW.corrected_line_id IS NOT NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_line_serialized_receipt_shape',
                MESSAGE = 'serialized first slice permits only a source-null RECEIVE line';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM public.inventory_inventorytransactionline AS other_line
            WHERE other_line.transaction_id = NEW.transaction_id
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_serialized_receipt_one_line',
                MESSAGE = 'serialized RECEIVE must contain exactly one line';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM public.inventory_inventorytransactionline AS prior_line
            WHERE prior_line.serialized_asset_id = NEW.serialized_asset_id
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_serialized_asset_received_once',
                MESSAGE = 'serialized asset can be established by exactly one RECEIVE';
        END IF;
""",
        """        IF parent_transaction_type NOT IN ('RECEIPT', 'INITIAL_BALANCE')
           OR NEW.source_location_id IS NOT NULL
           OR NEW.target_location_id IS NULL
           OR NEW.original_issue_line_id IS NOT NULL
           OR NEW.corrected_line_id IS NOT NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_line_serialized_receipt_shape',
                MESSAGE = 'serialized first slice permits only a source-null RECEIVE line';
        END IF;
        IF parent_transaction_type = 'RECEIPT'
           AND EXISTS (
            SELECT 1
            FROM public.inventory_inventorytransactionline AS other_line
            WHERE other_line.transaction_id = NEW.transaction_id
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_serialized_receipt_one_line',
                MESSAGE = 'serialized RECEIVE must contain exactly one line';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM public.inventory_inventorytransactionline AS prior_line
            WHERE prior_line.serialized_asset_id = NEW.serialized_asset_id
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_serialized_asset_received_once',
                MESSAGE = 'serialized asset can be established by exactly one RECEIVE';
        END IF;
""",
    )
    sql = _replace_once(
        sql,
        """    IF parent_transaction_type = 'TRANSFER' THEN
""",
        """    IF parent_transaction_type = 'INITIAL_BALANCE' THEN
        IF NEW.source_location_id IS NOT NULL
           OR NEW.target_location_id IS NULL
           OR NEW.original_issue_line_id IS NOT NULL
           OR NEW.corrected_line_id IS NOT NULL
           OR NEW.serialized_asset_id IS NOT NULL
           OR NEW.quantity IS NULL
           OR NEW.unit_id IS NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_initial_balance_line_shape',
                MESSAGE = 'INITIAL_BALANCE quantity line must be target-only without lineage';
        END IF;

        PERFORM 1
        FROM public.locations_location AS location
        WHERE location.id = NEW.target_location_id
        FOR KEY SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = '23503',
                MESSAGE = 'INITIAL_BALANCE location does not exist';
        END IF;

        PERFORM 1
        FROM public.catalog_materialcondition AS condition
        WHERE condition.id = NEW.condition_id
        FOR KEY SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = '23503',
                MESSAGE = 'INITIAL_BALANCE condition does not exist';
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


INITIAL_BALANCE_GUARD_SQL = _initial_balance_guard_sql()

IDENTITY_PRECHECK_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.inventory_serializedasset
        WHERE internal_asset_code IS DISTINCT FROM btrim(internal_asset_code)
           OR btrim(internal_asset_code) = ''
           OR (
                serial_number IS NOT NULL
                AND (
                    serial_number IS DISTINCT FROM btrim(serial_number)
                    OR btrim(serial_number) = ''
                )
           )
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_asset_identity_not_canonical',
            MESSAGE = 'existing serialized asset identities must already be outer-trimmed';
    END IF;
END;
$$;
"""

REVERSE_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.inventory_inventorytransaction
        WHERE transaction_type = 'INITIAL_BALANCE'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_initial_balance_reverse_requires_no_data',
            MESSAGE = 'cannot reverse INITIAL_BALANCE kernel while data exists';
    END IF;
END;
$$;
""" + "\n".join(
    _function_definition(_previous.COUNT_GUARD_SQL, name)
    for name in (
        "inventory_tx_requires_line_guard",
        "inventory_issue_line_cardinality_guard",
        "inventory_line_quantity_guard",
    )
)

SUPPORTED_TYPES = [
    "RECEIPT",
    "ISSUE",
    "RETURN",
    "TRANSFER",
    "CONTROLLED_CORRECTION",
    "COUNT_RECONCILIATION",
    "INITIAL_BALANCE",
]


class Migration(migrations.Migration):
    dependencies = [("inventory", "0010_count_reconciliation_kernel")]

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
                    ("INITIAL_BALANCE", "Açılış bakiyesi"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorytransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(transaction_type__in=SUPPORTED_TYPES),
                name="inventory_tx_type_supported",
            ),
        ),
        migrations.RunSQL(sql=IDENTITY_PRECHECK_SQL, reverse_sql=migrations.RunSQL.noop),
        migrations.AddConstraint(
            model_name="serializedasset",
            constraint=models.CheckConstraint(
                condition=~models.Q(internal_asset_code__regex=r"^\s|\s$"),
                name="inventory_asset_internal_code_trimmed",
            ),
        ),
        migrations.AddConstraint(
            model_name="serializedasset",
            constraint=models.CheckConstraint(
                condition=models.Q(serial_number__isnull=True)
                | ~models.Q(serial_number__regex=r"^\s|\s$"),
                name="inventory_asset_serial_trimmed_or_null",
            ),
        ),
        migrations.RunSQL(sql=INITIAL_BALANCE_GUARD_SQL, reverse_sql=REVERSE_SQL),
    ]
