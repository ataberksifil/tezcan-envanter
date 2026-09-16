import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from inventory.models import InventoryTransaction, InventoryTransactionLine
from corrections.test_services import make_request
from corrections.models import CorrectionRequest

pytestmark = pytest.mark.django_db


def header(objects, transaction_type="CONTROLLED_CORRECTION"):
    return InventoryTransaction.objects.create(
        operation_id=uuid.uuid4(),
        request_fingerprint=uuid.uuid4().hex * 2,
        transaction_type=transaction_type,
        acting_user=objects["approver"],
        occurred_at=timezone.now(),
    )


def force_constraints():
    with connection.cursor() as cursor:
        cursor.execute(
            "SET CONSTRAINTS inventory_tx_requires_line_trg, "
            "inventory_issue_line_cardinality_trg IMMEDIATE"
        )
        cursor.execute(
            "SET CONSTRAINTS inventory_tx_requires_line_trg, "
            "inventory_issue_line_cardinality_trg DEFERRED"
        )


def correction_line(objects, tx, **overrides):
    values = {
        "transaction": tx,
        "line_number": 1,
        "material": objects["material"],
        "quantity": Decimal("1.000"),
        "unit": objects["unit"],
        "condition": objects["condition"],
        "source_location": None,
        "target_location": objects["source"],
        "original_issue_line": None,
        "corrected_line": objects["line"],
    }
    values.update(overrides)
    return InventoryTransactionLine.objects.create(**values)


def test_correction_lineage_is_required(correction_objects):
    with pytest.raises(IntegrityError, match="corrected lineage"):
        with transaction.atomic():
            tx = header(correction_objects)
            correction_line(correction_objects, tx, corrected_line=None)


def test_noncorrection_cannot_misuse_correction_lineage(correction_objects):
    with pytest.raises(IntegrityError, match="non-correction"):
        with transaction.atomic():
            tx = header(correction_objects, "RECEIPT")
            correction_line(correction_objects, tx)


def test_correction_of_correction_is_rejected(correction_objects):
    with transaction.atomic():
        first_tx = header(correction_objects)
        first_line = correction_line(correction_objects, first_tx)
        force_constraints()
    with pytest.raises(IntegrityError, match="canonical non-correction"):
        with transaction.atomic():
            second_tx = header(correction_objects)
            correction_line(correction_objects, second_tx, corrected_line=first_line)


def test_arbitrary_multiline_correction_is_rejected(correction_objects):
    with pytest.raises(IntegrityError, match="one or two lines"):
        with transaction.atomic():
            tx = header(correction_objects)
            for number in (1, 2, 3):
                correction_line(
                    correction_objects,
                    tx,
                    line_number=number,
                    quantity=Decimal("1.000"),
                )
            force_constraints()


def test_two_line_shape_requires_equal_decrease_and_increase(correction_objects):
    with pytest.raises(IntegrityError, match="equal decrease/increase"):
        with transaction.atomic():
            tx = header(correction_objects)
            correction_line(
                correction_objects,
                tx,
                source_location=correction_objects["source"],
                target_location=None,
            )
            correction_line(
                correction_objects,
                tx,
                line_number=2,
                quantity=Decimal("2.000"),
                material=correction_objects["other_material"],
                unit=correction_objects["unit"],
                target_location=correction_objects["target"],
            )
            force_constraints()


def test_cumulative_negative_original_equivalent_is_db_guarded(correction_objects):
    with pytest.raises(IntegrityError, match="negative original quantity"):
        with transaction.atomic():
            tx = header(correction_objects)
            correction_line(
                correction_objects,
                tx,
                quantity=Decimal("11.000"),
                source_location=correction_objects["source"],
                target_location=None,
            )
            force_constraints()


def test_correction_ledger_is_immutable_for_raw_update_and_delete(correction_objects):
    with transaction.atomic():
        tx = header(correction_objects)
        line = correction_line(correction_objects, tx)
        force_constraints()
    line.quantity = Decimal("2.000")
    with pytest.raises(ValidationError):
        line.save()
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_inventorytransactionline SET quantity = %s WHERE id = %s",
                [Decimal("2.000"), line.pk],
            )
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM inventory_inventorytransaction WHERE id = %s", [tx.pk]
            )


def test_submitted_correction_request_cannot_be_deleted(correction_objects):
    request_record = make_request(correction_objects)
    with pytest.raises(ValidationError):
        request_record.delete()
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CorrectionRequest.objects.filter(pk=request_record.pk).delete()


def test_request_db_guard_requires_line_to_belong_to_transaction(correction_objects):
    with transaction.atomic():
        other_tx = header(correction_objects, "RECEIPT")
        InventoryTransactionLine.objects.create(
            transaction=other_tx,
            line_number=1,
            material=correction_objects["material"],
            quantity=Decimal("1.000"),
            unit=correction_objects["unit"],
            condition=correction_objects["condition"],
            target_location=correction_objects["target"],
        )
        force_constraints()
    with pytest.raises(IntegrityError, match="quantity non-correction original line"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS inventory_tx_requires_line_trg DEFERRED")
            cursor.execute(
                """
                INSERT INTO corrections_correctionrequest (
                    id, original_transaction_id, original_line_id,
                    original_location_id, requester_id, explanation,
                    effect_type, quantity_effect, status, requested_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'QUANTITY', 1,
                          'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                [
                    uuid.uuid4(),
                    other_tx.pk,
                    correction_objects["line"].pk,
                    correction_objects["source"].pk,
                    correction_objects["requester"].pk,
                    "Geçersiz transaction ve line eşlemesi",
                ],
            )
