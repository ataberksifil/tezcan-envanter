import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


BASELINE_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION public.imports_baseline_immutability_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'ESTABLISHED' THEN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                CONSTRAINT = 'imports_baseline_established_immutable',
                MESSAGE = 'established inventory baseline cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status = 'ESTABLISHED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            CONSTRAINT = 'imports_baseline_established_immutable',
            MESSAGE = 'established inventory baseline cannot be updated';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER imports_baseline_immutability_trg
BEFORE UPDATE OR DELETE ON public.inventory_baselines
FOR EACH ROW
EXECUTE FUNCTION public.imports_baseline_immutability_guard();

CREATE OR REPLACE FUNCTION public.imports_baseline_session_link_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    baseline_status varchar(16);
    session_is_candidate boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT status INTO baseline_status
        FROM public.inventory_baselines
        WHERE id = OLD.inventory_baseline_id;
        IF baseline_status = 'ESTABLISHED' THEN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                CONSTRAINT = 'imports_baseline_session_link_immutable',
                MESSAGE = 'established baseline session membership cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;

    SELECT status INTO baseline_status
    FROM public.inventory_baselines
    WHERE id = NEW.inventory_baseline_id
    FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'baseline does not exist';
    END IF;
    IF TG_OP = 'UPDATE' OR baseline_status = 'ESTABLISHED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            CONSTRAINT = 'imports_baseline_session_link_immutable',
            MESSAGE = 'established baseline session membership cannot be rewritten';
    END IF;

    SELECT baseline_candidate INTO session_is_candidate
    FROM public.counting_physicalcountsession
    WHERE id = NEW.physical_count_session_id
    FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'count session does not exist';
    END IF;
    IF NOT session_is_candidate THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'imports_baseline_session_must_be_candidate',
            MESSAGE = 'baseline session link requires a baseline-candidate count session';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER imports_baseline_session_link_trg
BEFORE INSERT OR UPDATE OR DELETE ON public.inventory_baseline_count_session_links
FOR EACH ROW
EXECUTE FUNCTION public.imports_baseline_session_link_guard();

CREATE OR REPLACE FUNCTION public.imports_baseline_transaction_link_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    baseline_status varchar(16);
    tx_type varchar(32);
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT status INTO baseline_status
        FROM public.inventory_baselines
        WHERE id = OLD.inventory_baseline_id;
        IF baseline_status = 'ESTABLISHED' THEN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                CONSTRAINT = 'imports_baseline_transaction_link_immutable',
                MESSAGE = 'established baseline result link cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;

    SELECT status INTO baseline_status
    FROM public.inventory_baselines
    WHERE id = NEW.inventory_baseline_id
    FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'baseline does not exist';
    END IF;
    IF TG_OP = 'UPDATE' OR baseline_status = 'ESTABLISHED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            CONSTRAINT = 'imports_baseline_transaction_link_immutable',
            MESSAGE = 'established baseline result link cannot be rewritten';
    END IF;

    SELECT transaction_type INTO tx_type
    FROM public.inventory_inventorytransaction
    WHERE id = NEW.inventory_transaction_id
    FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'inventory transaction does not exist';
    END IF;
    IF tx_type <> 'INITIAL_BALANCE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'imports_baseline_tx_must_be_initial_balance',
            MESSAGE = 'baseline result link requires an INITIAL_BALANCE transaction';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER imports_baseline_transaction_link_trg
BEFORE INSERT OR UPDATE OR DELETE ON public.inventory_baseline_transaction_links
FOR EACH ROW
EXECUTE FUNCTION public.imports_baseline_transaction_link_guard();
"""

REVERSE_GUARD_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.inventory_baselines WHERE status = 'ESTABLISHED'
    ) OR EXISTS (
        SELECT 1 FROM public.inventory_baseline_transaction_links
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'imports_baseline_reverse_requires_no_history',
            MESSAGE = 'cannot reverse inventory baseline while authoritative history exists';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS imports_baseline_transaction_link_trg
ON public.inventory_baseline_transaction_links;
DROP TRIGGER IF EXISTS imports_baseline_session_link_trg
ON public.inventory_baseline_count_session_links;
DROP TRIGGER IF EXISTS imports_baseline_immutability_trg
ON public.inventory_baselines;
DROP FUNCTION IF EXISTS public.imports_baseline_transaction_link_guard();
DROP FUNCTION IF EXISTS public.imports_baseline_session_link_guard();
DROP FUNCTION IF EXISTS public.imports_baseline_immutability_guard();
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("counting", "0004_serialized_physical_count"),
        ("inventory", "0011_initial_balance_kernel"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryBaseline",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("reference", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PREPARED", "Hazırlandı"),
                            ("ESTABLISHED", "Kesildi"),
                        ],
                        default="PREPARED",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("established_at", models.DateTimeField(blank=True, null=True)),
                ("establishment_operation_id", models.UUIDField(blank=True, null=True)),
                ("request_fingerprint", models.CharField(blank=True, max_length=64, null=True)),
                ("establishment_explanation", models.TextField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="created_inventory_baselines",
                        to=settings.AUTH_USER_MODEL,
                        db_index=False,
                    ),
                ),
                (
                    "established_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="established_inventory_baselines",
                        to=settings.AUTH_USER_MODEL,
                        db_index=False,
                    ),
                ),
            ],
            options={
                "db_table": "inventory_baselines",
                "permissions": [
                    ("establish_baseline", "Can establish inventory baseline")
                ],
            },
        ),
        migrations.CreateModel(
            name="InventoryBaselineCountSessionLink",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("required", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "inventory_baseline",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="count_session_links",
                        to="imports.inventorybaseline",
                        db_index=False,
                    ),
                ),
                (
                    "physical_count_session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="baseline_links",
                        to="counting.physicalcountsession",
                        db_index=False,
                    ),
                ),
            ],
            options={"db_table": "inventory_baseline_count_session_links"},
        ),
        migrations.CreateModel(
            name="InventoryBaselineTransactionLink",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("scope_key", models.CharField(max_length=64)),
                ("operation_id", models.UUIDField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "inventory_baseline",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="transaction_links",
                        to="imports.inventorybaseline",
                        db_index=False,
                    ),
                ),
                (
                    "inventory_transaction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="baseline_result_links",
                        to="inventory.inventorytransaction",
                        db_index=False,
                    ),
                ),
                (
                    "physical_count_session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="baseline_result_links",
                        to="counting.physicalcountsession",
                        db_index=False,
                    ),
                ),
            ],
            options={"db_table": "inventory_baseline_transaction_links"},
        ),
        migrations.AddIndex(
            model_name="inventorybaseline",
            index=models.Index(
                fields=["established_at"], name="imports_baseline_est_at_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaseline",
            constraint=models.UniqueConstraint(
                fields=("reference",), name="imports_baseline_reference_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaseline",
            constraint=models.UniqueConstraint(
                condition=models.Q(("establishment_operation_id__isnull", False)),
                fields=("establishment_operation_id",),
                name="imports_baseline_operation_id_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaseline",
            constraint=models.CheckConstraint(
                condition=~models.Q(reference__regex=r"^\s*$"),
                name="imports_baseline_reference_nonblank",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaseline",
            constraint=models.CheckConstraint(
                condition=models.Q(status__in=["PREPARED", "ESTABLISHED"]),
                name="imports_baseline_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaseline",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="PREPARED",
                        established_by__isnull=True,
                        established_at__isnull=True,
                        establishment_operation_id__isnull=True,
                        request_fingerprint__isnull=True,
                        establishment_explanation__isnull=True,
                    )
                    | models.Q(
                        status="ESTABLISHED",
                        established_by__isnull=False,
                        established_at__isnull=False,
                        establishment_operation_id__isnull=False,
                        request_fingerprint__isnull=False,
                        establishment_explanation__isnull=False,
                    )
                ),
                name="imports_baseline_established_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaseline",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(request_fingerprint__isnull=True)
                    | models.Q(request_fingerprint__regex=r"^[0-9a-f]{64}$")
                ),
                name="imports_baseline_fingerprint_hex",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaselinecountsessionlink",
            constraint=models.UniqueConstraint(
                fields=("inventory_baseline", "physical_count_session"),
                name="imports_baseline_session_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaselinecountsessionlink",
            constraint=models.UniqueConstraint(
                fields=("physical_count_session",),
                name="imports_baseline_session_global_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaselinetransactionlink",
            constraint=models.UniqueConstraint(
                fields=("inventory_transaction",),
                name="imports_baseline_tx_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaselinetransactionlink",
            constraint=models.UniqueConstraint(
                fields=("operation_id",),
                name="imports_baseline_tx_operation_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaselinetransactionlink",
            constraint=models.UniqueConstraint(
                fields=("inventory_baseline", "scope_key"),
                name="imports_baseline_scope_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaselinetransactionlink",
            constraint=models.UniqueConstraint(
                fields=("physical_count_session",),
                name="imports_baseline_result_session_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorybaselinetransactionlink",
            constraint=models.CheckConstraint(
                condition=~models.Q(scope_key__regex=r"^\s*$"),
                name="imports_baseline_scope_key_nonblank",
            ),
        ),
        migrations.RunSQL(sql=BASELINE_GUARD_SQL, reverse_sql=REVERSE_GUARD_SQL),
    ]
