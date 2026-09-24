from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


RECEIPT_METADATA_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION public.inventory_receipt_metadata_type_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_transaction_type varchar(32);
BEGIN
    SELECT tx.transaction_type
    INTO parent_transaction_type
    FROM public.inventory_inventorytransaction AS tx
    WHERE tx.id = NEW.transaction_id
    FOR KEY SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23503',
            MESSAGE = 'receipt metadata transaction does not exist';
    END IF;
    IF parent_transaction_type <> 'RECEIPT' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_receipt_metadata_transaction_type',
            MESSAGE = 'ReceiptMetadata requires a RECEIPT transaction';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER inventory_receipt_metadata_type_trg
BEFORE INSERT ON public.inventory_receiptmetadata
FOR EACH ROW
EXECUTE FUNCTION public.inventory_receipt_metadata_type_guard();

CREATE TRIGGER inventory_receipt_metadata_immutable_trg
BEFORE UPDATE OR DELETE ON public.inventory_receiptmetadata
FOR EACH ROW
EXECUTE FUNCTION public.inventory_ledger_immutability_guard();
"""


RECEIPT_METADATA_GUARD_REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS inventory_receipt_metadata_immutable_trg
    ON public.inventory_receiptmetadata;
DROP TRIGGER IF EXISTS inventory_receipt_metadata_type_trg
    ON public.inventory_receiptmetadata;
DROP FUNCTION IF EXISTS public.inventory_receipt_metadata_type_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0012_serialized_inventory_movements"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReceiptMetadata",
            fields=[
                (
                    "transaction",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.RESTRICT,
                        primary_key=True,
                        related_name="receipt_metadata",
                        serialize=False,
                        to="inventory.inventorytransaction",
                    ),
                ),
                ("usage_place", models.CharField(max_length=255)),
                ("arrived_on", models.DateField()),
                (
                    "supplier_name",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("package_count", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "package_label",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "contents_per_package",
                    models.DecimalField(
                        blank=True,
                        decimal_places=3,
                        max_digits=18,
                        null=True,
                    ),
                ),
                ("material_code_snapshot", models.CharField(max_length=64)),
                ("material_name_snapshot", models.CharField(max_length=255)),
                (
                    "material_brand_snapshot",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                (
                    "material_model_snapshot",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                (
                    "unit_code_snapshot",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "unit_name_snapshot",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["arrived_on"],
                        name="inventory_rcpt_meta_arr_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("usage_place__regex", r"^\s*$"),
                            _negated=True,
                        ),
                        name="inventory_rcpt_meta_usage_nonblank",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("material_code_snapshot__regex", r"^\s*$"),
                            _negated=True,
                        ),
                        name="inventory_rcpt_meta_code_nonblank",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("material_name_snapshot__regex", r"^\s*$"),
                            _negated=True,
                        ),
                        name="inventory_rcpt_meta_name_nonblank",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("supplier_name__isnull", True),
                            models.Q(
                                ("supplier_name__regex", r"^\s*$"),
                                _negated=True,
                            ),
                            _connector="OR",
                        ),
                        name="inventory_rcpt_meta_supplier_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("material_brand_snapshot__isnull", True),
                            models.Q(
                                ("material_brand_snapshot__regex", r"^\s*$"),
                                _negated=True,
                            ),
                            _connector="OR",
                        ),
                        name="inventory_rcpt_meta_brand_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("material_model_snapshot__isnull", True),
                            models.Q(
                                ("material_model_snapshot__regex", r"^\s*$"),
                                _negated=True,
                            ),
                            _connector="OR",
                        ),
                        name="inventory_rcpt_meta_model_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("unit_code_snapshot__isnull", True),
                            models.Q(
                                ("unit_code_snapshot__regex", r"^\s*$"),
                                _negated=True,
                            ),
                            _connector="OR",
                        ),
                        name="inventory_rcpt_meta_unit_code_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("unit_name_snapshot__isnull", True),
                            models.Q(
                                ("unit_name_snapshot__regex", r"^\s*$"),
                                _negated=True,
                            ),
                            _connector="OR",
                        ),
                        name="inventory_rcpt_meta_unit_name_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("package_label__isnull", True),
                            models.Q(
                                ("package_label__regex", r"^\s*$"),
                                _negated=True,
                            ),
                            _connector="OR",
                        ),
                        name="inventory_rcpt_meta_pkg_label_shape",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                package_count__isnull=True,
                                contents_per_package__isnull=True,
                                package_label__isnull=True,
                            )
                            | (
                                models.Q(package_count__gte=1)
                                & models.Q(contents_per_package__gt=Decimal("0"))
                            )
                        ),
                        name="inventory_rcpt_meta_packaging_shape",
                    ),
                ],
            },
        ),
        migrations.RunSQL(
            sql=RECEIPT_METADATA_GUARD_SQL,
            reverse_sql=RECEIPT_METADATA_GUARD_REVERSE_SQL,
        ),
    ]
