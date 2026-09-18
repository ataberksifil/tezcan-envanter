from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from accounts.models import User
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountSession
from counting.services import (
    complete_physical_count,
    create_physical_count_session,
    record_quantity_count,
    start_physical_count_session,
)
from imports.models import InventoryBaseline
from imports.services import prepare_inventory_baseline
from inventory.models import InventoryTransaction, StockBalance
from inventory.services.receipts import receive_quantity
from locations.models import Location

pytestmark = pytest.mark.django_db

EXPLANATION = "Kesim onay açıklaması yeterli uzunlukta."


def _grant(user, *labels):
    for label in labels:
        app_label, codename = label.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def baseline_ui():
    suffix = uuid.uuid4().hex[:8]
    counter = _grant(
        User.objects.create_user(username=f"bui-c-{suffix}"),
        "counting.view_physicalcountsession",
        "counting.add_physicalcountsession",
        "counting.change_physicalcountsession",
        "inventory.receive_stock",
    )
    establisher = _grant(
        User.objects.create_user(username=f"bui-e-{suffix}"),
        "counting.view_physicalcountsession",
        "counting.add_physicalcountsession",
        "counting.change_physicalcountsession",
        "imports.establish_baseline",
        "inventory.receive_stock",
        "inventory.view_inventorytransaction",
    )
    category = Category.objects.create(code=f"BUI-CAT-{suffix}", name="Kesim UI")
    unit = UnitOfMeasure.objects.create(code=f"BUI-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"BUI-M-{suffix}",
        name="Kesim miktar",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(code=f"BUI-C-{suffix}", name="Yeni")
    empty_root = Location.objects.create(
        code=f"BUI-ER-{suffix}", name="Boş kök", can_hold_stock=True
    )
    history_root = Location.objects.create(
        code=f"BUI-HR-{suffix}", name="Geçmiş kök", can_hold_stock=True
    )
    return locals()


def _opening_session(baseline_ui, *, scope, counted=Decimal("0"), expected=None):
    if expected:
        receive_quantity(
            actor=baseline_ui["establisher"],
            operation_id=uuid.uuid4(),
            material_id=baseline_ui["material"].pk,
            unit_id=baseline_ui["unit"].pk,
            condition_id=baseline_ui["condition"].pk,
            target_location_id=scope.pk,
            quantity=expected,
        )
    session = create_physical_count_session(
        actor=baseline_ui["counter"],
        reference_number=f"BUI-{uuid.uuid4().hex[:10]}",
        scope_location_id=scope.pk,
        baseline_candidate=True,
    )
    start_physical_count_session(actor=baseline_ui["counter"], session_id=session.pk)
    session.refresh_from_db()
    for line in session.quantity_lines.all():
        record_quantity_count(
            actor=baseline_ui["counter"],
            session_id=session.pk,
            line_id=line.pk,
            counted_quantity=counted if counted is not None else line.expected_quantity,
        )
    complete_physical_count(actor=baseline_ui["counter"], session_id=session.pk)
    return PhysicalCountSession.objects.get(pk=session.pk)


def test_baseline_pages_require_establish_permission(client, baseline_ui):
    client.force_login(baseline_ui["counter"])
    assert client.get(reverse("imports:baseline-list")).status_code == 403
    assert client.get(reverse("imports:baseline-create")).status_code == 403


def test_prepare_eligible_session_and_block_invalid(client, baseline_ui):
    session = _opening_session(baseline_ui, scope=baseline_ui["empty_root"])
    client.force_login(baseline_ui["establisher"])
    get_page = client.get(reverse("imports:baseline-create"))
    assert get_page.status_code == 200
    assert session.reference_number in get_page.content.decode()
    response = client.post(
        reverse("imports:baseline-create"),
        {"sessions": [str(session.pk)], "reference": "BUI-REF-1"},
    )
    baseline = InventoryBaseline.objects.get(reference="BUI-REF-1")
    assert response.status_code == 302
    assert baseline.status == InventoryBaseline.Status.PREPARED
    assert InventoryTransaction.objects.filter(
        transaction_type="INITIAL_BALANCE"
    ).count() == 0

    routine = create_physical_count_session(
        actor=baseline_ui["counter"],
        reference_number=f"BUI-R-{uuid.uuid4().hex[:8]}",
        scope_location_id=baseline_ui["history_root"].pk,
        baseline_candidate=False,
    )
    blocked = client.post(
        reverse("imports:baseline-create"),
        {"sessions": [str(routine.pk)], "reference": "BUI-REF-BAD"},
    )
    assert blocked.status_code == 200
    assert not InventoryBaseline.objects.filter(reference="BUI-REF-BAD").exists()


def test_readiness_shows_prior_history_and_unresolved_blockers(client, baseline_ui):
    history_session = _opening_session(
        baseline_ui,
        scope=baseline_ui["history_root"],
        expected=Decimal("4.000"),
        counted=Decimal("5.000"),
    )
    prepared = prepare_inventory_baseline(
        actor=baseline_ui["establisher"],
        session_ids=[history_session.pk],
        reference="BUI-HIST",
    )
    client.force_login(baseline_ui["establisher"])
    html = client.get(
        reverse("imports:baseline-detail", args=[prepared.baseline.pk])
    ).content.decode()
    assert "ledger geçmişi" in html.lower()
    establish = client.post(
        reverse("imports:baseline-establish", args=[prepared.baseline.pk]),
        {
            "operation_id": str(uuid.uuid4()),
            "explanation": EXPLANATION,
        },
    )
    assert establish.status_code == 302
    prepared.baseline.refresh_from_db()
    assert prepared.baseline.status == InventoryBaseline.Status.PREPARED
    assert InventoryTransaction.objects.filter(
        transaction_type="INITIAL_BALANCE"
    ).count() == 0

    empty = _opening_session(baseline_ui, scope=baseline_ui["empty_root"])
    # leave a not-started extra? empty session with no lines is complete.
    client.force_login(baseline_ui["establisher"])
    create_page = client.get(
        reverse("imports:baseline-create") + f"?session={empty.pk}"
    )
    assert empty.reference_number in create_page.content.decode()


def test_establishment_creates_initial_balance_and_is_idempotent(client, baseline_ui):
    session = _opening_session(
        baseline_ui,
        scope=baseline_ui["empty_root"],
        counted=Decimal("0"),
    )
    from counting.services import add_unexpected_quantity_count
    session = create_physical_count_session(
        actor=baseline_ui["counter"],
        reference_number=f"BUI-E-{uuid.uuid4().hex[:8]}",
        scope_location_id=baseline_ui["empty_root"].pk,
        baseline_candidate=True,
    )
    start_physical_count_session(actor=baseline_ui["counter"], session_id=session.pk)
    add_unexpected_quantity_count(
        actor=baseline_ui["counter"],
        session_id=session.pk,
        material_id=baseline_ui["material"].pk,
        location_id=baseline_ui["empty_root"].pk,
        condition_id=baseline_ui["condition"].pk,
        counted_quantity=Decimal("6.000"),
    )
    complete_physical_count(actor=baseline_ui["counter"], session_id=session.pk)
    client.force_login(baseline_ui["establisher"])
    client.post(
        reverse("imports:baseline-create"),
        {"sessions": [str(session.pk)], "reference": "BUI-EST"},
    )
    baseline = InventoryBaseline.objects.get(reference="BUI-EST")
    page = client.get(reverse("imports:baseline-detail", args=[baseline.pk]))
    assert "Kesimi uygula" in page.content.decode()
    assert (
        client.get(
            reverse("imports:baseline-establish", args=[baseline.pk])
        ).status_code
        == 405
    )
    form = page.context["establish_form"]
    operation_id = form["operation_id"].value()
    before_tx = InventoryTransaction.objects.count()
    response = client.post(
        reverse("imports:baseline-establish", args=[baseline.pk]),
        {"operation_id": operation_id, "explanation": EXPLANATION},
    )
    assert response.status_code == 302
    baseline.refresh_from_db()
    assert baseline.status == InventoryBaseline.Status.ESTABLISHED
    assert InventoryTransaction.objects.filter(
        transaction_type="INITIAL_BALANCE"
    ).count() == 1
    link = baseline.transaction_links.get()
    detail = client.get(
        reverse("inventory:transaction-history-detail", args=[link.inventory_transaction_id])
    )
    assert detail.status_code == 200
    assert "Açılış bakiyesi" in detail.content.decode()
    replay = client.post(
        reverse("imports:baseline-establish", args=[baseline.pk]),
        {"operation_id": operation_id, "explanation": EXPLANATION},
    )
    assert replay.status_code == 302
    assert InventoryTransaction.objects.filter(
        transaction_type="INITIAL_BALANCE"
    ).count() == 1
    assert InventoryTransaction.objects.count() == before_tx + 1


def test_baseline_get_does_not_mutate(client, baseline_ui):
    session = _opening_session(baseline_ui, scope=baseline_ui["empty_root"])
    client.force_login(baseline_ui["establisher"])
    before = (
        InventoryTransaction.objects.count(),
        StockBalance.objects.count(),
        InventoryBaseline.objects.count(),
    )
    client.get(reverse("imports:baseline-list"))
    client.get(reverse("imports:baseline-create"))
    assert (
        InventoryTransaction.objects.count(),
        StockBalance.objects.count(),
        InventoryBaseline.objects.count(),
    ) == before
    assert session.pk
