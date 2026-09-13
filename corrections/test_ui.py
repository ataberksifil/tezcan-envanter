from decimal import Decimal

import pytest
from django.urls import reverse

from corrections.models import CorrectionRequest
from corrections.test_services import make_request
from corrections.services import approve_correction_request
from inventory.models import InventoryTransaction

pytestmark = pytest.mark.django_db


def test_request_entry_from_transaction_detail_and_creation(client, correction_objects):
    client.force_login(correction_objects["requester"])
    detail_url = reverse(
        "inventory:transaction-history-detail",
        args=[correction_objects["transaction"].pk],
    )
    response = client.get(detail_url)
    assert response.status_code == 200
    assert reverse(
        "corrections:request-create",
        args=[correction_objects["transaction"].pk],
    ) in response.content.decode()

    response = client.post(
        reverse(
            "corrections:request-create",
            args=[correction_objects["transaction"].pk],
        ),
        {
            "original_line": correction_objects["line"].pk,
            "original_location": correction_objects["source"].pk,
            "effect_type": "QUANTITY",
            "quantity_effect": "-2.000",
            "explanation": "Yanlış miktar iki adet fazla",
        },
    )
    request_record = CorrectionRequest.objects.get()
    assert response.status_code == 302
    assert response.url == reverse(
        "corrections:request-detail", args=[request_record.pk]
    )
    assert InventoryTransaction.objects.count() == 1


def test_approval_queue_detail_and_approve_action(client, correction_objects):
    request_record = make_request(correction_objects, quantity_effect=Decimal("2"))
    client.force_login(correction_objects["approver"])
    response = client.get(reverse("corrections:request-list"))
    assert response.status_code == 200
    assert str(request_record.pk) in response.content.decode()
    response = client.get(
        reverse("corrections:request-detail", args=[request_record.pk])
    )
    assert response.status_code == 200
    assert "Onayla" in response.content.decode()
    response = client.post(reverse("corrections:approve", args=[request_record.pk]))
    assert response.status_code == 302
    request_record.refresh_from_db()
    assert request_record.status == CorrectionRequest.Status.APPROVED


def test_requester_does_not_see_decision_actions(client, correction_objects):
    request_record = make_request(correction_objects)
    client.force_login(correction_objects["requester"])
    response = client.get(
        reverse("corrections:request-detail", args=[request_record.pk])
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "Onayla" not in content
    assert "Reddet" not in content


def test_unauthorized_user_cannot_open_correction_pages(client, correction_objects):
    user = correction_objects["approver"]
    user.user_permissions.clear()
    user = type(user).objects.get(pk=user.pk)
    client.force_login(user)
    assert client.get(reverse("corrections:request-list")).status_code == 403
    assert client.get(
        reverse(
            "corrections:request-create",
            args=[correction_objects["transaction"].pk],
        )
    ).status_code == 403


def test_transaction_history_filters_and_renders_correction_lineage(
    client, correction_objects
):
    request_record = make_request(
        correction_objects,
        effect_type=CorrectionRequest.EffectType.IDENTITY,
        quantity_effect=Decimal("3.000"),
        corrected_material_id=correction_objects["material"].pk,
        corrected_location_id=correction_objects["target"].pk,
        corrected_condition_id=correction_objects["condition"].pk,
    )
    decision = approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=request_record.pk
    )
    client.force_login(correction_objects["approver"])
    response = client.get(
        reverse("inventory:transaction-history-list"),
        {"transaction_type": "CONTROLLED_CORRECTION"},
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "Kontrollü düzeltme" in content
    assert str(decision.transaction.pk) in content
    response = client.get(
        reverse(
            "inventory:transaction-history-detail", args=[decision.transaction.pk]
        )
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "Satır 1" in content and "Satır 2" in content
    assert str(correction_objects["transaction"].pk) in content
