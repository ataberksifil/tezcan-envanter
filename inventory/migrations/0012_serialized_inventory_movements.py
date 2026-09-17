import importlib

import django.db.models.deletion
from django.db import migrations, models


_previous = importlib.import_module(
    "inventory.migrations.0011_initial_balance_kernel"
)
_serialized_foundation = importlib.import_module(
    "inventory.migrations.0009_serialized_inventory_foundation"
)


def _replace_once(sql, old, new):
    if sql.count(old) != 1:
        raise RuntimeError(
            f"expected exactly one inventory guard fragment: {old[:80]!r}"
        )
    return sql.replace(old, new, 1)


def _function_definition(sql, function_name):
    start = sql.index(f"CREATE OR REPLACE FUNCTION public.{function_name}()")
    end = sql.index("$$;", start) + 3
    return sql[start:end] + "\n"


# Next-seq is enforced inside the existing parent-aware serialized line
# guard after that function already took FOR UPDATE on the asset row.
# This does not add a second lock order; uniqueness is also constrained
# by inventory_line_asset_event_seq_uniq.
SERIALIZED_MOVEMENT_BLOCK = r"""    IF material_tracking_mode = 'SERIALIZED' THEN
        IF NEW.asset_event_seq IS NULL OR NEW.asset_event_seq < 1 THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_line_serialized_event_seq_required',
                MESSAGE = 'serialized line requires a positive asset_event_seq';
        END IF;
        IF NEW.asset_event_seq IS DISTINCT FROM (
            SELECT COALESCE(MAX(prior_line.asset_event_seq), 0) + 1
            FROM public.inventory_inventorytransactionline AS prior_line
            WHERE prior_line.serialized_asset_id = NEW.serialized_asset_id
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_line_serialized_event_seq_next',
                MESSAGE = 'serialized line asset_event_seq must be the next causal event';
        END IF;
        IF parent_transaction_type IN ('RECEIPT', 'INITIAL_BALANCE') THEN
            IF NEW.source_location_id IS NOT NULL
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
            IF NEW.target_location_id IS DISTINCT FROM asset_current_location_id
               OR NEW.condition_id IS DISTINCT FROM asset_current_condition_id
               OR asset_current_state <> 'IN_STOCK' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_receipt_projection_match',
                    MESSAGE = 'serialized RECEIVE line must match the asset projection';
            END IF;
            RETURN NEW;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM public.inventory_inventorytransactionline AS genesis_line
            JOIN public.inventory_inventorytransaction AS genesis_tx
              ON genesis_tx.id = genesis_line.transaction_id
            WHERE genesis_line.serialized_asset_id = NEW.serialized_asset_id
              AND genesis_tx.transaction_type IN ('RECEIPT', 'INITIAL_BALANCE')
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_serialized_asset_not_established',
                MESSAGE = 'serialized movement requires an established RECEIVE or INITIAL_BALANCE';
        END IF;

        IF parent_transaction_type = 'ISSUE' THEN
            IF NEW.source_location_id IS NULL
               OR NEW.target_location_id IS NOT NULL
               OR NEW.original_issue_line_id IS NOT NULL
               OR NEW.corrected_line_id IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_line_serialized_issue_shape',
                    MESSAGE = 'serialized ISSUE requires source, null target, and no lineage';
            END IF;
            IF asset_current_state <> 'IN_STOCK'
               OR NEW.source_location_id IS DISTINCT FROM asset_current_location_id
               OR NEW.condition_id IS DISTINCT FROM asset_current_condition_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_issue_projection_match',
                    MESSAGE = 'serialized ISSUE must match the IN_STOCK asset projection';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.inventory_inventorytransactionline AS issue_line
                JOIN public.inventory_inventorytransaction AS issue_tx
                  ON issue_tx.id = issue_line.transaction_id
                WHERE issue_line.serialized_asset_id = NEW.serialized_asset_id
                  AND issue_tx.transaction_type = 'ISSUE'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.inventory_inventorytransactionline AS return_line
                      JOIN public.inventory_inventorytransaction AS return_tx
                        ON return_tx.id = return_line.transaction_id
                      WHERE return_line.original_issue_line_id = issue_line.id
                        AND return_tx.transaction_type = 'RETURN'
                  )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_unreturned_issue',
                    MESSAGE = 'serialized asset already has an unreturned ISSUE';
            END IF;
            RETURN NEW;
        END IF;

        IF parent_transaction_type = 'RETURN' THEN
            IF NEW.source_location_id IS NOT NULL
               OR NEW.target_location_id IS NULL
               OR NEW.original_issue_line_id IS NULL
               OR NEW.corrected_line_id IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_line_serialized_return_shape',
                    MESSAGE = 'serialized RETURN requires target, null source, and original ISSUE lineage';
            END IF;

            SELECT original_tx.transaction_type,
                   original_line.material_id,
                   original_line.condition_id,
                   original_line.serialized_asset_id,
                   original_line.source_location_id,
                   original_line.target_location_id,
                   original_line.original_issue_line_id,
                   original_line.quantity,
                   original_line.unit_id
            INTO original_transaction_type,
                 original_material_id,
                 original_condition_id,
                 original_serialized_asset_id,
                 original_source_location_id,
                 original_target_location_id,
                 original_issue_line_id,
                 original_quantity,
                 original_unit_id
            FROM public.inventory_inventorytransactionline AS original_line
            JOIN public.inventory_inventorytransaction AS original_tx
              ON original_tx.id = original_line.transaction_id
            WHERE original_line.id = NEW.original_issue_line_id
            FOR UPDATE OF original_line;

            IF NOT FOUND
               OR original_transaction_type <> 'ISSUE'
               OR original_serialized_asset_id IS NULL
               OR original_quantity IS NOT NULL
               OR original_unit_id IS NOT NULL
               OR original_source_location_id IS NULL
               OR original_target_location_id IS NOT NULL
               OR original_issue_line_id IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_return_original_issue',
                    MESSAGE = 'serialized RETURN original line must be a serialized ISSUE';
            END IF;
            IF original_serialized_asset_id IS DISTINCT FROM NEW.serialized_asset_id
               OR original_material_id IS DISTINCT FROM NEW.material_id
               OR original_condition_id IS DISTINCT FROM NEW.condition_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_return_identity',
                    MESSAGE = 'serialized RETURN must keep the original ISSUE asset, material, and condition';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.inventory_inventorytransactionline AS return_line
                JOIN public.inventory_inventorytransaction AS return_tx
                  ON return_tx.id = return_line.transaction_id
                WHERE return_line.original_issue_line_id = NEW.original_issue_line_id
                  AND return_tx.transaction_type = 'RETURN'
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_return_lineage_unique',
                    MESSAGE = 'serialized ISSUE may be returned only once';
            END IF;
            IF asset_current_state <> 'ISSUED'
               OR asset_current_location_id IS NOT NULL
               OR NEW.condition_id IS DISTINCT FROM asset_current_condition_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_return_projection_match',
                    MESSAGE = 'serialized RETURN must match the ISSUED asset projection';
            END IF;

            PERFORM 1
            FROM public.locations_location AS location
            WHERE location.id = NEW.target_location_id
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23503',
                    MESSAGE = 'inventory line target location does not exist';
            END IF;
            RETURN NEW;
        END IF;

        IF parent_transaction_type = 'TRANSFER' THEN
            IF NEW.source_location_id IS NULL
               OR NEW.target_location_id IS NULL
               OR NEW.source_location_id = NEW.target_location_id
               OR NEW.original_issue_line_id IS NOT NULL
               OR NEW.corrected_line_id IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_line_serialized_transfer_shape',
                    MESSAGE = 'serialized TRANSFER requires distinct source and target without lineage';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.inventory_issuecontext AS issue_context
                WHERE issue_context.transaction_id = NEW.transaction_id
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_transfer_issue_context',
                    MESSAGE = 'serialized TRANSFER cannot own IssueContext';
            END IF;
            IF asset_current_state <> 'IN_STOCK'
               OR NEW.source_location_id IS DISTINCT FROM asset_current_location_id
               OR NEW.condition_id IS DISTINCT FROM asset_current_condition_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'inventory_serialized_transfer_projection_match',
                    MESSAGE = 'serialized TRANSFER must match the IN_STOCK asset projection';
            END IF;

            PERFORM 1
            FROM public.locations_location AS location
            WHERE location.id = NEW.source_location_id
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23503',
                    MESSAGE = 'inventory line source location does not exist';
            END IF;
            PERFORM 1
            FROM public.locations_location AS location
            WHERE location.id = NEW.target_location_id
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23503',
                    MESSAGE = 'inventory line target location does not exist';
            END IF;
            RETURN NEW;
        END IF;

        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_line_serialized_transaction_type',
            MESSAGE = 'serialized line transaction type is not supported';
    END IF;

"""


ASSET_INTEGRITY_SQL = r"""
CREATE OR REPLACE FUNCTION public.inventory_serialized_asset_integrity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    material_tracking_mode varchar(10);
    location_active boolean;
    location_can_hold_stock boolean;
    condition_active boolean;
BEGIN
    SELECT material.tracking_mode
    INTO material_tracking_mode
    FROM public.catalog_material AS material
    WHERE material.id = NEW.material_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'serialized asset material does not exist';
    END IF;
    IF material_tracking_mode <> 'SERIALIZED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_asset_material_serialized',
            MESSAGE = 'serialized asset requires a SERIALIZED material';
    END IF;

    IF NEW.current_state = 'IN_STOCK' THEN
        IF NEW.current_location_id IS NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_asset_state_shape',
                MESSAGE = 'IN_STOCK serialized asset requires a current location';
        END IF;
        SELECT location.active, location.can_hold_stock
        INTO location_active, location_can_hold_stock
        FROM public.locations_location AS location
        WHERE location.id = NEW.current_location_id
        FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'serialized asset location does not exist';
        END IF;
        IF NOT location_active OR NOT location_can_hold_stock THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_asset_valid_location',
                MESSAGE = 'serialized asset requires an active stock-holding location';
        END IF;
    ELSIF NEW.current_state = 'ISSUED' THEN
        IF NEW.current_location_id IS NOT NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_asset_state_shape',
                MESSAGE = 'ISSUED serialized asset current location must be null';
        END IF;
    ELSE
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_asset_state_supported',
            MESSAGE = 'serialized asset state must be IN_STOCK or ISSUED';
    END IF;

    IF NEW.current_condition_id IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_asset_state_shape',
            MESSAGE = 'serialized asset requires a current condition';
    END IF;
    SELECT condition.active
    INTO condition_active
    FROM public.catalog_materialcondition AS condition
    WHERE condition.id = NEW.current_condition_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'serialized asset condition does not exist';
    END IF;
    IF NOT condition_active THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_asset_active_condition',
            MESSAGE = 'serialized asset requires an active condition';
    END IF;

    RETURN NEW;
END;
$$;
"""


CONDITION_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION public.inventory_condition_serialized_stock_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.active AND NOT NEW.active AND EXISTS (
        SELECT 1 FROM public.inventory_serializedasset AS asset
        WHERE asset.current_condition_id = OLD.id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_condition_serialized_stock',
            MESSAGE = 'condition used by current serialized inventory cannot be deactivated';
    END IF;
    RETURN NEW;
END;
$$;
"""


def backfill_asset_event_seq(apps, schema_editor):
    """Assign genesis seq=1 to pre-5.6 serialized lines.

    Committed 0009/0011 guards permit exactly one establishing RECEIVE or
    INITIAL_BALANCE line per asset. Multiple pre-0012 serialized lines would
    mean the assumption is already violated, so the migration fails closed.

    Line UPDATE is otherwise blocked by the ledger immutability trigger, so
    that trigger is disabled only for this documented backfill.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT serialized_asset_id
            FROM public.inventory_inventorytransactionline
            WHERE serialized_asset_id IS NOT NULL
            GROUP BY serialized_asset_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
        if cursor.fetchone() is not None:
            raise RuntimeError(
                "pre-0012 serialized history must have exactly one ledger line per asset"
            )
        cursor.execute(
            "ALTER TABLE public.inventory_inventorytransactionline "
            "DISABLE TRIGGER inventory_line_immutable_trg"
        )
        try:
            cursor.execute(
                """
                UPDATE public.inventory_inventorytransactionline
                SET asset_event_seq = 1
                WHERE serialized_asset_id IS NOT NULL
                  AND asset_event_seq IS NULL
                """
            )
        finally:
            cursor.execute(
                "ALTER TABLE public.inventory_inventorytransactionline "
                "ENABLE TRIGGER inventory_line_immutable_trg"
            )


def _serialized_movement_guard_sql():
    sql = _previous.INITIAL_BALANCE_GUARD_SQL
    sql = _replace_once(
        sql,
        """                MESSAGE = 'inventory line unit must match material unit';
        END IF;
    ELSIF material_tracking_mode = 'SERIALIZED' THEN
""",
        """                MESSAGE = 'inventory line unit must match material unit';
        END IF;
        IF NEW.asset_event_seq IS NOT NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_line_quantity_event_seq_null',
                MESSAGE = 'quantity line asset_event_seq must be null';
        END IF;
    ELSIF material_tracking_mode = 'SERIALIZED' THEN
""",
    )
    sql = _replace_once(
        sql,
        """    asset_material_id uuid;
    asset_current_location_id uuid;
    asset_current_condition_id uuid;
    asset_current_state varchar(16);
BEGIN
""",
        """    asset_material_id uuid;
    asset_current_location_id uuid;
    asset_current_condition_id uuid;
    asset_current_state varchar(16);
    original_serialized_asset_id uuid;
    original_source_location_id uuid;
    original_target_location_id uuid;
    original_issue_line_id uuid;
BEGIN
""",
    )
    sql = _replace_once(
        sql,
        """    IF material_tracking_mode = 'SERIALIZED' THEN
        IF parent_transaction_type NOT IN ('RECEIPT', 'INITIAL_BALANCE')
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
        IF NEW.target_location_id IS DISTINCT FROM asset_current_location_id
           OR NEW.condition_id IS DISTINCT FROM asset_current_condition_id
           OR asset_current_state <> 'IN_STOCK' THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'inventory_serialized_receipt_projection_match',
                MESSAGE = 'serialized RECEIVE line must match the asset projection';
        END IF;
        RETURN NEW;
    END IF;
""",
        SERIALIZED_MOVEMENT_BLOCK,
    )
    return (
        _function_definition(sql, "inventory_line_quantity_guard")
        + ASSET_INTEGRITY_SQL
        + CONDITION_GUARD_SQL
    )


SERIALIZED_MOVEMENT_GUARD_SQL = _serialized_movement_guard_sql()

REVERSE_PRECHECK_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.inventory_inventorytransactionline AS line
        JOIN public.inventory_inventorytransaction AS tx
          ON tx.id = line.transaction_id
        WHERE line.serialized_asset_id IS NOT NULL
          AND tx.transaction_type IN ('ISSUE', 'RETURN', 'TRANSFER')
    ) OR EXISTS (
        SELECT 1
        FROM public.inventory_serializedasset
        WHERE current_state = 'ISSUED'
           OR current_location_id IS NULL
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_serialized_movement_reverse_requires_no_data',
            MESSAGE = 'cannot reverse serialized movements while ISSUE/RETURN/TRANSFER history or ISSUED projection exists';
    END IF;
END;
$$;
"""

REVERSE_GUARD_SQL = "\n".join(
    [
        REVERSE_PRECHECK_SQL,
        _function_definition(
            _previous.INITIAL_BALANCE_GUARD_SQL, "inventory_line_quantity_guard"
        ),
        _function_definition(
            _serialized_foundation.SERIALIZED_GUARD_SQL,
            "inventory_serialized_asset_integrity_guard",
        ),
        _function_definition(
            _serialized_foundation.SERIALIZED_GUARD_SQL,
            "inventory_condition_serialized_stock_guard",
        ),
    ]
)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0011_initial_balance_kernel")]

    operations = [
        migrations.AlterField(
            model_name="serializedasset",
            name="current_location",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="current_serialized_assets",
                to="locations.location",
            ),
        ),
        migrations.AlterField(
            model_name="serializedasset",
            name="current_state",
            field=models.CharField(
                choices=[("IN_STOCK", "Stokta"), ("ISSUED", "Çıkış yapılmış")],
                default="IN_STOCK",
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="serializedasset",
            name="inventory_asset_state_in_stock",
        ),
        migrations.AddConstraint(
            model_name="serializedasset",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("current_condition__isnull", False),
                        ("current_location__isnull", False),
                        ("current_state", "IN_STOCK"),
                    ),
                    models.Q(
                        ("current_condition__isnull", False),
                        ("current_location__isnull", True),
                        ("current_state", "ISSUED"),
                    ),
                    _connector="OR",
                ),
                name="inventory_asset_state_shape",
            ),
        ),
        migrations.AddField(
            model_name="inventorytransactionline",
            name="asset_event_seq",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_asset_event_seq, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="inventorytransactionline",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("asset_event_seq__isnull", True),
                        ("serialized_asset__isnull", True),
                    ),
                    models.Q(
                        ("asset_event_seq__gte", 1),
                        ("serialized_asset__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="inventory_line_asset_event_seq_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorytransactionline",
            constraint=models.UniqueConstraint(
                condition=models.Q(("serialized_asset__isnull", False)),
                fields=("serialized_asset", "asset_event_seq"),
                name="inventory_line_asset_event_seq_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorytransactionline",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("original_issue_line__isnull", False),
                    ("serialized_asset__isnull", False),
                ),
                fields=("original_issue_line",),
                name="inventory_serialized_return_original_uniq",
            ),
        ),
        migrations.RunSQL(
            sql=SERIALIZED_MOVEMENT_GUARD_SQL,
            reverse_sql=REVERSE_GUARD_SQL,
        ),
    ]
