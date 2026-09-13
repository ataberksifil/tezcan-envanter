import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError

from audit.models import AuditEvent
from accounts.roles import DEFAULT_ROLE_TEMPLATES, SAFE_CATALOG_PERMISSION_LABELS
from corrections.models import CorrectionRequest
from corrections.services import (
    approve_correction_request,
    create_correction_request,
    reject_correction_request,
)
from inventory.models import InventoryTransaction, StockBalance
from inventory.services.projections import verify_quantity_projection

pytestmark = pytest.mark.django_db


def make_request(objects, **overrides):
    values = {
        "actor": objects["requester"],
        "original_transaction_id": objects["transaction"].pk,
        "original_line_id": objects["line"].pk,
        "original_location_id": objects["source"].pk,
        "explanation": "Yanlış kayıt düzeltme açıklaması",
        "effect_type": CorrectionRequest.EffectType.QUANTITY,
        "quantity_effect": Decimal("1.000"),
    }
    values.update(overrides)
    return create_correction_request(**values)


def balance(objects, *, material=None, location=None, condition=None):
    return StockBalance.objects.get(
        material=material or objects["material"],
        location=location or objects["source"],
        condition=condition or objects["condition"],
    ).quantity


def test_authorized_request_is_trimmed_and_has_no_inventory_effect(correction_objects):
    before_transactions = InventoryTransaction.objects.count()
    before_balance = balance(correction_objects)
    request = make_request(
        correction_objects, explanation="   On karakterden uzun açıklama   "
    )
    assert request.explanation == "On karakterden uzun açıklama"
    assert request.status == CorrectionRequest.Status.PENDING
    assert InventoryTransaction.objects.count() == before_transactions
    assert balance(correction_objects) == before_balance
    assert AuditEvent.objects.count() == 0


def test_unauthorized_requester_is_rejected(correction_objects):
    actor = get_user_model().objects.create_user(username="unauthorized-correction")
    with pytest.raises(PermissionDenied):
        make_request(correction_objects, actor=actor)


@pytest.mark.parametrize("explanation", ["short", "x" * 2001])
def test_explanation_length_is_enforced(correction_objects, explanation):
    with pytest.raises(ValidationError):
        make_request(correction_objects, explanation=explanation)


def test_only_one_pending_request_per_original_transaction(correction_objects):
    make_request(correction_objects)
    with pytest.raises(ValidationError) as exc_info:
        make_request(correction_objects, quantity_effect=Decimal("2.000"))
    assert exc_info.value.code == "corrections.pending_conflict"


def test_successful_quantity_increase_is_idempotent(correction_objects):
    request = make_request(correction_objects, quantity_effect=Decimal("2.000"))
    result = approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=request.pk
    )
    replay = approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=request.pk
    )
    request.refresh_from_db()
    assert request.status == CorrectionRequest.Status.APPROVED
    assert result.transaction.pk == replay.transaction.pk
    assert replay.replayed is True
    assert balance(correction_objects) == Decimal("12.000")
    assert result.transaction.transaction_type == "CONTROLLED_CORRECTION"
    assert result.transaction.lines.count() == 1
    line = result.transaction.lines.get()
    assert line.target_location == correction_objects["source"]
    assert line.corrected_line == correction_objects["line"]
    assert AuditEvent.objects.filter(event_type="corrections.request.approved").count() == 1
    assert verify_quantity_projection() == ()


def test_successful_quantity_decrease(correction_objects):
    request = make_request(correction_objects, quantity_effect=Decimal("-3.000"))
    result = approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=request.pk
    )
    assert balance(correction_objects) == Decimal("7.000")
    assert result.transaction.lines.get().source_location == correction_objects["source"]
    assert verify_quantity_projection() == ()


def test_projection_verifier_detects_deliberate_drift_after_correction(
    correction_objects,
):
    request = make_request(correction_objects, quantity_effect=Decimal("2.000"))
    approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=request.pk
    )
    StockBalance.objects.filter(
        material=correction_objects["material"],
        location=correction_objects["source"],
        condition=correction_objects["condition"],
    ).update(quantity=Decimal("99.000"))
    mismatches = verify_quantity_projection()
    assert len(mismatches) == 1
    assert mismatches[0].expected_quantity == Decimal("12.000")
    assert mismatches[0].actual_quantity == Decimal("99.000")


@pytest.mark.parametrize("changed", ["location", "condition", "material"])
def test_identity_restatement_supports_each_identity_change(
    correction_objects, changed
):
    corrected_material = (
        correction_objects["other_material"]
        if changed == "material"
        else correction_objects["material"]
    )
    corrected_location = (
        correction_objects["target"]
        if changed == "location"
        else correction_objects["source"]
    )
    corrected_condition = (
        correction_objects["other_condition"]
        if changed == "condition"
        else correction_objects["condition"]
    )
    request = make_request(
        correction_objects,
        effect_type=CorrectionRequest.EffectType.IDENTITY,
        quantity_effect=Decimal("4.000"),
        corrected_material_id=corrected_material.pk,
        corrected_location_id=corrected_location.pk,
        corrected_condition_id=corrected_condition.pk,
    )
    result = approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=request.pk
    )
    lines = list(result.transaction.lines.order_by("line_number"))
    assert len(lines) == 2
    assert lines[0].source_location == correction_objects["source"]
    assert lines[1].target_location == corrected_location
    assert lines[0].quantity == lines[1].quantity == Decimal("4.000")
    assert lines[0].corrected_line_id == lines[1].corrected_line_id
    assert balance(correction_objects) == Decimal("6.000")
    assert balance(
        correction_objects,
        material=corrected_material,
        location=corrected_location,
        condition=corrected_condition,
    ) == Decimal("4.000")
    assert verify_quantity_projection() == ()


def test_insufficient_stock_rolls_back_and_request_stays_pending(correction_objects):
    request = make_request(correction_objects, quantity_effect=Decimal("-11.000"))
    with pytest.raises(ValidationError):
        approve_correction_request(
            actor=correction_objects["approver"], correction_request_id=request.pk
        )
    request.refresh_from_db()
    assert request.status == CorrectionRequest.Status.PENDING
    assert request.resulting_transaction_id is None
    assert balance(correction_objects) == Decimal("10.000")
    assert not InventoryTransaction.objects.filter(
        transaction_type="CONTROLLED_CORRECTION"
    ).exists()
    assert AuditEvent.objects.count() == 0


def test_repeated_corrections_and_cumulative_floor(correction_objects):
    first = make_request(correction_objects, quantity_effect=Decimal("5.000"))
    approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=first.pk
    )
    second = make_request(correction_objects, quantity_effect=Decimal("-12.000"))
    approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=second.pk
    )
    assert balance(correction_objects) == Decimal("3.000")
    third = make_request(correction_objects, quantity_effect=Decimal("-4.000"))
    with pytest.raises(ValidationError) as exc_info:
        approve_correction_request(
            actor=correction_objects["approver"], correction_request_id=third.pk
        )
    assert exc_info.value.code == "inventory.correction_cumulative_floor"
    third.refresh_from_db()
    assert third.status == CorrectionRequest.Status.PENDING
    assert balance(correction_objects) == Decimal("3.000")


def test_self_approval_and_unauthorized_approval_are_blocked(correction_objects):
    correction_objects["requester"].user_permissions.add(
        Permission.objects.get(
            content_type__app_label="corrections",
            codename="decide_correctionrequest",
        )
    )
    correction_objects["requester"] = get_user_model().objects.get(
        pk=correction_objects["requester"].pk
    )
    request = make_request(correction_objects)
    with pytest.raises(ValidationError) as exc_info:
        approve_correction_request(
            actor=correction_objects["requester"], correction_request_id=request.pk
        )
    assert exc_info.value.code == "corrections.self_decision"
    actor = get_user_model().objects.create_user(username="not-a-decider")
    with pytest.raises(PermissionDenied):
        approve_correction_request(actor=actor, correction_request_id=request.pk)


def test_rejection_is_terminal_audited_and_has_no_inventory_effect(correction_objects):
    request = make_request(correction_objects)
    result = reject_correction_request(
        actor=correction_objects["approver"],
        correction_request_id=request.pk,
        rejection_reason="  Uygun değil  ",
    )
    request.refresh_from_db()
    assert result.transaction is None
    assert request.status == CorrectionRequest.Status.REJECTED
    assert request.rejection_reason == "Uygun değil"
    assert balance(correction_objects) == Decimal("10.000")
    assert InventoryTransaction.objects.count() == 1
    event = AuditEvent.objects.get(event_type="corrections.request.rejected")
    assert event.metadata["original_transaction_id"] == str(
        correction_objects["transaction"].pk
    )
    with pytest.raises(ValidationError):
        approve_correction_request(
            actor=correction_objects["approver"], correction_request_id=request.pk
        )


def test_correction_transaction_cannot_be_a_new_root(correction_objects):
    first = make_request(correction_objects)
    result = approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=first.pk
    )
    with pytest.raises(ValidationError):
        create_correction_request(
            actor=correction_objects["requester"],
            original_transaction_id=result.transaction.pk,
            original_line_id=result.transaction.lines.get().pk,
            original_location_id=correction_objects["source"].pk,
            explanation="Correction transaction root olamaz",
            effect_type="QUANTITY",
            quantity_effect=Decimal("1.000"),
        )


def test_correction_permissions_are_minimal_and_decision_is_not_safe_allowlisted():
    assert CorrectionRequest._meta.default_permissions == ("add", "view")
    assert dict(CorrectionRequest._meta.permissions) == {
        "decide_correctionrequest": "Can approve or reject correction request"
    }
    assert "corrections.view_correctionrequest" in SAFE_CATALOG_PERMISSION_LABELS
    assert "corrections.add_correctionrequest" in SAFE_CATALOG_PERMISSION_LABELS
    assert "corrections.decide_correctionrequest" not in SAFE_CATALOG_PERMISSION_LABELS
    assert "decide_correctionrequest" not in DEFAULT_ROLE_TEMPLATES["TECHNICIAN"]
    assert "decide_correctionrequest" not in DEFAULT_ROLE_TEMPLATES["STOREKEEPER"]
    assert "decide_correctionrequest" in DEFAULT_ROLE_TEMPLATES["ADMIN_MANAGER"]
