import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


RECEIPT_EXPIRY_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION public.inventory_receipt_expiry_type_guard()
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
            MESSAGE = 'receipt expiry transaction does not exist';
    END IF;
    IF parent_transaction_type <> 'RECEIPT' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'inventory_receipt_expiry_transaction_type',
            MESSAGE = 'ReceiptExpiry requires a RECEIPT transaction';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER inventory_receipt_expiry_type_trg
BEFORE INSERT ON public.inventory_receiptexpiry
FOR EACH ROW
EXECUTE FUNCTION public.inventory_receipt_expiry_type_guard();

CREATE TRIGGER inventory_receipt_expiry_immutable_trg
BEFORE UPDATE OR DELETE ON public.inventory_receiptexpiry
FOR EACH ROW
EXECUTE FUNCTION public.inventory_ledger_immutability_guard();

CREATE TRIGGER inventory_expiry_inspection_immutable_trg
BEFORE UPDATE OR DELETE ON public.inventory_expiryinspection
FOR EACH ROW
EXECUTE FUNCTION public.inventory_ledger_immutability_guard();

ALTER TABLE public.inventory_expiryinspection
    ADD CONSTRAINT inventory_exp_insp_note_len
    CHECK (char_length(note) >= 10 AND char_length(note) <= 2000);
"""


RECEIPT_EXPIRY_GUARD_REVERSE_SQL = r"""
ALTER TABLE public.inventory_expiryinspection
    DROP CONSTRAINT IF EXISTS inventory_exp_insp_note_len;
DROP TRIGGER IF EXISTS inventory_expiry_inspection_immutable_trg
    ON public.inventory_expiryinspection;
DROP TRIGGER IF EXISTS inventory_receipt_expiry_immutable_trg
    ON public.inventory_receiptexpiry;
DROP TRIGGER IF EXISTS inventory_receipt_expiry_type_trg
    ON public.inventory_receiptexpiry;
DROP FUNCTION IF EXISTS public.inventory_receipt_expiry_type_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0013_receipt_metadata"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReceiptExpiry",
            fields=[
                (
                    "transaction",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.RESTRICT,
                        primary_key=True,
                        related_name="receipt_expiry",
                        serialize=False,
                        to="inventory.inventorytransaction",
                    ),
                ),
                ("expires_on", models.DateField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["expires_on"],
                        name="inventory_rcpt_exp_date_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ExpiryInspection",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "receipt_expiry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="inspections",
                        to="inventory.receiptexpiry",
                    ),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="expiry_inspections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("note", models.CharField(max_length=2000)),
                ("recorded_at", models.DateTimeField()),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["receipt_expiry", "recorded_at"],
                        name="inventory_exp_insp_when_idx",
                    ),
                ],
            },
        ),
        migrations.RunSQL(
            sql=RECEIPT_EXPIRY_GUARD_SQL,
            reverse_sql=RECEIPT_EXPIRY_GUARD_REVERSE_SQL,
        ),
    ]
