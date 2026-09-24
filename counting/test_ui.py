from __future__ import annotations

import uuid
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from django.contrib.auth.models import Permission
from django.urls import NoReverseMatch, reverse

from accounts.models import User
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountSerializedLine,
    PhysicalCountSession,
)
from counting.services import (
    complete_physical_count,
    create_physical_count_session,
    mark_quantity_line_not_counted,
    record_quantity_count,
    record_serialized_asset_count,
    start_physical_count_session,
)
from identification.codec import encode_serialized_asset
from inventory.models import InventoryTransaction, SerializedAsset, StockBalance
from inventory.services.receipts import receive_quantity, receive_serialized
from locations.models import Location

pytestmark = pytest.mark.django_db

EXPECTED_QTY = Decimal("12.345")
COUNT_PERMS = (
    "counting.view_physicalcountsession",
    "counting.add_physicalcountsession",
    "counting.change_physicalcountsession",
)
VIEW_PERM = ("counting.view_physicalcountsession",)
DECIDE_PERM = ("counting.decide_discrepancy",)
RECEIVE_PERM = ("inventory.receive_stock",)
HISTORY_PERM = ("inventory.view_inventorytransaction",)


def _grant(user, *labels):
    for label in labels:
        app_label, codename = label.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def ui():
    suffix = uuid.uuid4().hex[:8]
    performer = _grant(
        User.objects.create_user(username=f"cnt-perf-{suffix}"),
        *COUNT_PERMS,
        *RECEIVE_PERM,
    )
    viewer = _grant(
        User.objects.create_user(username=f"cnt-view-{suffix}"),
        *VIEW_PERM,
    )
    reviewer = _grant(
        User.objects.create_user(username=f"cnt-rev-{suffix}"),
        *COUNT_PERMS,
        *DECIDE_PERM,
        *RECEIVE_PERM,
        *HISTORY_PERM,
    )
    outsider = User.objects.create_user(username=f"cnt-out-{suffix}")
    category = Category.objects.create(code=f"CUI-CAT-{suffix}", name="Sayım UI")
    unit = UnitOfMeasure.objects.create(code=f"CUI-U-{suffix}", name="Adet")
    material = Material.objects.create(
        material_code=f"CUI-M-{suffix}",
        name="Miktar UI",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    other_material = Material.objects.create(
        material_code=f"CUI-M2-{suffix}",
        name="İkinci miktar",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    serialized_material = Material.objects.create(
        material_code=f"CUI-S-{suffix}",
        name="Tekil UI",
        category=category,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"CUI-C-{suffix}", name="Yeni", sort_order=10
    )
    root = Location.objects.create(
        code=f"CUI-R-{suffix}", name="Kök", can_hold_stock=False
    )
    location = Location.objects.create(
        code=f"CUI-L-{suffix}",
        name="Raf",
        parent=root,
        can_hold_stock=True,
    )
    sibling_root = Location.objects.create(
        code=f"CUI-SR-{suffix}", name="Diğer kök", can_hold_stock=True
    )
    return locals()


def _inventory_snapshot():
    return (
        InventoryTransaction.objects.count(),
        StockBalance.objects.count(),
        SerializedAsset.objects.count(),
        PhysicalCountSession.objects.count(),
        PhysicalCountQuantityLine.objects.count(),
        PhysicalCountSerializedLine.objects.count(),
    )


def _receive_qty(ui, *, quantity=EXPECTED_QTY, actor=None, location=None, material=None):
    actor = actor or ui["reviewer"]
    return receive_quantity(
        actor=actor,
        operation_id=uuid.uuid4(),
        material_id=(material or ui["material"]).pk,
        unit_id=ui["unit"].pk,
        condition_id=ui["condition"].pk,
        target_location_id=(location or ui["location"]).pk,
        quantity=quantity,
    )


def _receive_asset(ui, *, code=None):
    return receive_serialized(
        actor=ui["reviewer"],
        operation_id=uuid.uuid4(),
        material_id=ui["serialized_material"].pk,
        internal_asset_code=code or f"CUI-A-{uuid.uuid4().hex[:8]}",
        serial_number=None,
        condition_id=ui["condition"].pk,
        target_location_id=ui["location"].pk,
    )


def _draft_session(ui, *, baseline=False, scope=None, actor=None):
    return create_physical_count_session(
        actor=actor or ui["performer"],
        reference_number=f"CUI-{uuid.uuid4().hex[:10]}",
        scope_location_id=(scope or ui["root"]).pk,
        baseline_candidate=baseline,
    )


def _started_session(ui, **kwargs):
    session = _draft_session(ui, **kwargs)
    return start_physical_count_session(
        actor=ui["performer"], session_id=session.pk
    ).session


def _formset_post(response, *, counted_quantity, extra=None):
    formset = response.context["formset"]
    form = formset.forms[0]
    data = {
        "qty-TOTAL_FORMS": str(formset.total_form_count()),
        "qty-INITIAL_FORMS": str(formset.initial_form_count()),
        "qty-MIN_NUM_FORMS": "0",
        "qty-MAX_NUM_FORMS": "1000",
        "qty-0-line_id": form["line_id"].value(),
        "qty-0-counted_at_token": form["counted_at_token"].value() or "",
        "qty-0-counted_quantity": counted_quantity,
    }
    if extra:
        data.update(extra)
    return data


def test_list_requires_auth_and_view_permission(client, ui):
    url = reverse("counting:session-list")
    assert client.get(url).status_code == 302
    client.force_login(ui["outsider"])
    assert client.get(url).status_code == 403
    client.force_login(ui["viewer"])
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Yeni sayım" not in content
    assert "Kesimler" not in content


def test_list_shows_status_and_hides_sensitive_controls(client, ui):
    session = _draft_session(ui)
    client.force_login(ui["performer"])
    response = client.get(reverse("counting:session-list"))
    content = response.content.decode()
    assert response.status_code == 200
    assert session.reference_number in content
    assert "Taslak" in content
    assert reverse("counting:session-create") in content
    assert "Onayla" not in content


def test_create_permission_and_valid_creation(client, ui):
    url = reverse("counting:session-create")
    client.force_login(ui["viewer"])
    assert client.get(url).status_code == 403
    client.force_login(ui["performer"])
    get_response = client.get(url)
    assert get_response.status_code == 200
    before = _inventory_snapshot()
    response = client.post(
        url,
        {
            "reference_number": "CUI-NEW-1",
            "scope_location": str(ui["root"].pk),
        },
    )
    session = PhysicalCountSession.objects.get(reference_number="CUI-NEW-1")
    assert response.status_code == 302
    assert response.url == reverse("counting:session-detail", args=[session.pk])
    assert session.status == PhysicalCountSession.Status.DRAFT
    assert _inventory_snapshot()[:3] == before[:3]


def test_create_overlap_is_domain_error_not_500(client, ui):
    _draft_session(ui)
    client.force_login(ui["performer"])
    response = client.post(
        reverse("counting:session-create"),
        {
            "reference_number": "CUI-OVERLAP",
            "scope_location": str(ui["root"].pk),
        },
    )
    assert response.status_code == 200
    assert "açık sayım oturumu" in response.content.decode()
    assert not PhysicalCountSession.objects.filter(
        reference_number="CUI-OVERLAP"
    ).exists()


def test_start_get_does_not_mutate_post_captures_snapshot_and_prg(client, ui):
    _receive_qty(ui)
    session = _draft_session(ui)
    client.force_login(ui["performer"])
    start_url = reverse("counting:session-start", args=[session.pk])
    before = _inventory_snapshot()
    get_response = client.get(start_url)
    assert get_response.status_code == 200
    assert "dondurulmaz" in get_response.content.decode()
    assert _inventory_snapshot() == before
    post_response = client.post(start_url)
    assert post_response.status_code == 302
    session.refresh_from_db()
    assert session.status == PhysicalCountSession.Status.STARTED
    assert session.quantity_lines.get().expected_quantity == EXPECTED_QTY
    assert _inventory_snapshot()[:3] == before[:3]
    repeat = client.post(start_url)
    assert repeat.status_code == 302
    assert PhysicalCountQuantityLine.objects.filter(session=session).count() == 1


def test_blind_count_hides_expected_from_performer_html(client, ui):
    _receive_qty(ui)
    session = _started_session(ui)
    line = session.quantity_lines.get()
    client.force_login(ui["performer"])
    pages = [
        client.get(reverse("counting:session-detail", args=[session.pk])),
        client.get(reverse("counting:quantity-count", args=[session.pk])),
    ]
    for response in pages:
        html = response.content.decode()
        assert response.status_code == 200
        assert "12.345" not in html
        assert "12,345" not in html
        assert "expected_quantity" not in html
        assert "Beklenen" not in html
        assert str(line.expected_quantity) not in html
    client.force_login(ui["reviewer"])
    review_html = client.get(
        reverse("counting:session-detail", args=[session.pk])
    ).content.decode()
    assert "12.345" in review_html or "12,345" in review_html
    assert "Beklenen" in review_html


def test_quantity_explicit_positive_zero_untouched_and_not_counted(client, ui):
    _receive_qty(ui, quantity=Decimal("5.000"))
    session = _started_session(ui)
    url = reverse("counting:quantity-count", args=[session.pk])
    client.force_login(ui["performer"])
    get_response = client.get(url)
    blank = _formset_post(get_response, counted_quantity="")
    client.post(url, blank)
    line = session.quantity_lines.get()
    assert line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.PENDING_COUNT
    assert line.counted_quantity is None
    get_response = client.get(url)
    client.post(url, _formset_post(get_response, counted_quantity="0.000"))
    line.refresh_from_db()
    assert line.counted_quantity == Decimal("0.000")
    assert (
        line.resolution_status
        == PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
    )
    get_response = client.get(url)
    client.post(url, _formset_post(get_response, counted_quantity="5.000"))
    line.refresh_from_db()
    assert line.counted_quantity == Decimal("5.000")
    assert (
        line.resolution_status
        == PhysicalCountQuantityLine.ResolutionStatus.NO_DISCREPANCY
    )
    client.post(
        reverse("counting:quantity-not-counted", args=[session.pk, line.pk]),
        {"counted_at_token": get_response.context["formset"].forms[0]["counted_at_token"].value() or ""},
    )
    # CAS will fail because counted_at changed; fetch current token.
    line.refresh_from_db()
    client.post(
        reverse("counting:quantity-not-counted", args=[session.pk, line.pk]),
        {"counted_at_token": line.counted_at.isoformat()},
    )
    line.refresh_from_db()
    assert line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED
    assert line.counted_quantity is None


def test_invalid_decimal_and_forged_bucket_ids(client, ui):
    _receive_qty(ui)
    session = _started_session(ui)
    url = reverse("counting:quantity-count", args=[session.pk])
    client.force_login(ui["performer"])
    get_response = client.get(url)
    invalid = _formset_post(get_response, counted_quantity="1.2345")
    response = client.post(url, invalid)
    assert response.status_code == 200
    line = session.quantity_lines.get()
    assert line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.PENDING_COUNT
    unexpected_url = reverse("counting:unexpected-quantity", args=[session.pk])
    response = client.post(
        unexpected_url,
        {
            "material": str(uuid.uuid4()),
            "location": str(uuid.uuid4()),
            "condition": str(uuid.uuid4()),
            "counted_quantity": "1.000",
        },
    )
    assert response.status_code == 200
    assert session.quantity_lines.count() == 1


def test_unexpected_quantity_valid_and_unknown_rejected(client, ui):
    session = _started_session(ui)
    url = reverse("counting:unexpected-quantity", args=[session.pk])
    client.force_login(ui["performer"])
    before = _inventory_snapshot()
    response = client.post(
        url,
        {
            "material": str(ui["material"].pk),
            "location": str(ui["location"].pk),
            "condition": str(ui["condition"].pk),
            "counted_quantity": "3.000",
        },
    )
    assert response.status_code == 302
    line = session.quantity_lines.get()
    assert line.expected_quantity == Decimal("0.000")
    assert line.counted_quantity == Decimal("3.000")
    assert _inventory_snapshot()[:3] == before[:3]
    ui["material"].active = False
    ui["material"].save()
    inactive = client.post(
        url,
        {
            "material": str(ui["material"].pk),
            "location": str(ui["location"].pk),
            "condition": str(ui["condition"].pk),
            "counted_quantity": "1.000",
        },
    )
    assert inactive.status_code == 200
    serialized = client.post(
        url,
        {
            "material": str(ui["serialized_material"].pk),
            "location": str(ui["location"].pk),
            "condition": str(ui["condition"].pk),
            "counted_quantity": "1.000",
        },
    )
    assert serialized.status_code == 200
    assert session.quantity_lines.count() == 1
    assert _inventory_snapshot()[:3] == before[:3]


def test_serialized_count_observe_missing_and_scan_convenience(client, ui):
    result = _receive_asset(ui)
    asset = result.serialized_asset
    session = _started_session(ui)
    client.force_login(ui["performer"])
    url = reverse("counting:serialized-count", args=[session.pk])
    html = client.get(url).content.decode()
    assert "quantity=1" not in html.lower()
    payload = encode_serialized_asset(asset.pk)
    lookup = client.get(url, {"identity": payload})
    assert lookup.status_code == 200
    assert asset.internal_asset_code in lookup.content.decode()
    assert InventoryTransaction.objects.filter(
        transaction_type="ISSUE"
    ).count() == 0
    form = lookup.context["observe_form"]
    response = client.post(
        url,
        {
            "serialized_asset_id": str(asset.pk),
            "counted_at_token": form["counted_at_token"].value() or "",
            "observed_location": str(ui["location"].pk),
            "observed_condition": str(ui["condition"].pk),
        },
    )
    assert response.status_code == 302
    line = session.serialized_lines.get(serialized_asset=asset)
    assert line.observed_present is True
    assert line.resolution_status == PhysicalCountSerializedLine.ResolutionStatus.NO_DISCREPANCY
    client.post(
        reverse("counting:serialized-missing", args=[session.pk, line.pk]),
        {"counted_at_token": line.counted_at.isoformat()},
    )
    line.refresh_from_db()
    assert line.observed_present is False
    stale = client.post(
        url,
        {
            "serialized_asset_id": str(uuid.uuid4()),
            "observed_location": str(ui["location"].pk),
            "observed_condition": str(ui["condition"].pk),
        },
    )
    assert stale.status_code == 200


def test_complete_routine_and_baseline_rules(client, ui):
    _receive_qty(ui, quantity=Decimal("2.000"))
    session = _started_session(ui)
    client.force_login(ui["performer"])
    complete_url = reverse("counting:session-complete", args=[session.pk])
    assert client.get(complete_url).status_code == 200
    response = client.post(complete_url)
    assert response.status_code == 200 or response.status_code == 302
    session.refresh_from_db()
    assert session.status == PhysicalCountSession.Status.STARTED
    line = session.quantity_lines.get()
    mark_quantity_line_not_counted(
        actor=ui["performer"], session_id=session.pk, line_id=line.pk
    )
    assert client.post(complete_url).status_code == 302
    session.refresh_from_db()
    assert session.status == PhysicalCountSession.Status.COMPLETED

    _receive_qty(ui, quantity=Decimal("4.000"), location=ui["sibling_root"])
    baseline = _started_session(ui, baseline=True, scope=ui["sibling_root"])
    baseline_line = baseline.quantity_lines.get()
    mark_quantity_line_not_counted(
        actor=ui["performer"], session_id=baseline.pk, line_id=baseline_line.pk
    )
    client.post(reverse("counting:session-complete", args=[baseline.pk]))
    baseline.refresh_from_db()
    assert baseline.status == PhysicalCountSession.Status.STARTED
    record_quantity_count(
        actor=ui["performer"],
        session_id=baseline.pk,
        line_id=baseline_line.pk,
        counted_quantity=Decimal("4.000"),
        expected_counted_at=PhysicalCountQuantityLine.objects.get(pk=baseline_line.pk).counted_at,
    )
    assert client.post(
        reverse("counting:session-complete", args=[baseline.pk])
    ).status_code == 302
    baseline.refresh_from_db()
    assert baseline.status == PhysicalCountSession.Status.COMPLETED


def test_discrepancy_review_approve_reject_separation_and_transaction_link(client, ui):
    _receive_qty(ui, quantity=Decimal("8.000"))
    session = _started_session(ui)
    line = session.quantity_lines.get()
    record_quantity_count(
        actor=ui["performer"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("7.000"),
    )
    complete_physical_count(actor=ui["performer"], session_id=session.pk)
    review_url = reverse("counting:discrepancy-review", args=[session.pk])
    client.force_login(ui["performer"])
    assert client.get(review_url).status_code == 403
    client.force_login(ui["reviewer"])
    page = client.get(review_url)
    html = page.content.decode()
    assert page.status_code == 200
    # Approved Turkish display format (UI Faz 7): 8.000 -> "8", 7.000 -> "7".
    assert '<span class="qty">8<span class="qty-unit">' in html
    assert '<span class="qty">7<span class="qty-unit">' in html
    assert ">Onayla<" in html or 'btn-success">Onayla' in html
    form = page.context["pending_items"][0]["approval_form"]
    approve_url = reverse("counting:quantity-approve", args=[session.pk, line.pk])
    assert client.get(approve_url).status_code == 405
    response = client.post(
        approve_url,
        {
            "operation_id": str(form["operation_id"].value()),
            "explanation": "Fiziksel sayım farkı onaylandı.",
        },
    )
    assert response.status_code == 302
    line.refresh_from_db()
    assert line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.APPROVED
    detail = client.get(reverse("counting:session-detail", args=[session.pk]))
    assert str(line.reconciliation_transaction_id) in detail.content.decode() or (
        reverse(
            "inventory:transaction-history-detail",
            args=[line.reconciliation_transaction_id],
        )
        in detail.content.decode()
    )
    history = client.get(
        reverse(
            "inventory:transaction-history-detail",
            args=[line.reconciliation_transaction_id],
        )
    )
    assert history.status_code == 200
    assert "Sayım mutabakatı" in history.content.decode()
    decided = client.post(
        approve_url,
        {
            "operation_id": str(uuid.uuid4()),
            "explanation": "İkinci onay denemesi yeterince uzun.",
        },
    )
    assert decided.status_code == 302
    line.refresh_from_db()
    assert line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.APPROVED
    assert (
        InventoryTransaction.objects.filter(
            transaction_type="COUNT_RECONCILIATION"
        ).count()
        == 1
    )


def test_reject_reopens_and_self_approval_hidden(client, ui):
    _receive_qty(ui, quantity=Decimal("6.000"))
    session = _draft_session(ui)
    start_physical_count_session(actor=ui["reviewer"], session_id=session.pk)
    session.refresh_from_db()
    line = session.quantity_lines.get()
    record_quantity_count(
        actor=ui["reviewer"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("1.000"),
    )
    complete_physical_count(actor=ui["reviewer"], session_id=session.pk)
    client.force_login(ui["reviewer"])
    html = client.get(
        reverse("counting:discrepancy-review", args=[session.pk])
    ).content.decode()
    assert 'btn-success">Onayla' not in html
    assert ">Onayla<" not in html
    assert "kendi" in html.lower()

    _receive_qty(ui, quantity=Decimal("9.000"), location=ui["sibling_root"])
    session2 = create_physical_count_session(
        actor=ui["performer"],
        reference_number=f"CUI-R-{uuid.uuid4().hex[:8]}",
        scope_location_id=ui["sibling_root"].pk,
    )
    start_physical_count_session(actor=ui["performer"], session_id=session2.pk)
    line2 = PhysicalCountSession.objects.get(pk=session2.pk).quantity_lines.get()
    record_quantity_count(
        actor=ui["performer"],
        session_id=session2.pk,
        line_id=line2.pk,
        counted_quantity=Decimal("1.000"),
    )
    complete_physical_count(actor=ui["performer"], session_id=session2.pk)
    client.force_login(ui["reviewer"])
    response = client.post(
        reverse("counting:quantity-reject", args=[session2.pk, line2.pk]),
        {"reason": "Yeniden sayılsın"},
    )
    assert response.status_code == 302
    session2.refresh_from_db()
    assert session2.status == PhysicalCountSession.Status.STARTED


def test_drift_refusal_does_not_overwrite_observation(client, ui):
    _receive_qty(ui, quantity=Decimal("10.000"))
    session = _started_session(ui)
    line = session.quantity_lines.get()
    record_quantity_count(
        actor=ui["performer"],
        session_id=session.pk,
        line_id=line.pk,
        counted_quantity=Decimal("9.000"),
    )
    complete_physical_count(actor=ui["performer"], session_id=session.pk)
    receive_quantity(
        actor=ui["reviewer"],
        operation_id=uuid.uuid4(),
        material_id=ui["material"].pk,
        unit_id=ui["unit"].pk,
        condition_id=ui["condition"].pk,
        target_location_id=ui["location"].pk,
        quantity=Decimal("1.000"),
    )
    client.force_login(ui["reviewer"])
    page = client.get(reverse("counting:discrepancy-review", args=[session.pk]))
    form = page.context["pending_items"][0]["approval_form"]
    before_counted = PhysicalCountQuantityLine.objects.get(pk=line.pk).counted_quantity
    response = client.post(
        reverse("counting:quantity-approve", args=[session.pk, line.pk]),
        {
            "operation_id": str(form["operation_id"].value()),
            "explanation": "Sapmalı onay denemesi.",
        },
    )
    assert response.status_code == 302
    line.refresh_from_db()
    assert line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
    assert line.counted_quantity == before_counted


def test_get_pages_do_not_mutate_ledger_or_projection(client, ui):
    _receive_qty(ui)
    result = _receive_asset(ui)
    session = _started_session(ui)
    client.force_login(ui["performer"])
    before = _inventory_snapshot()
    urls = [
        reverse("counting:session-list"),
        reverse("counting:session-create"),
        reverse("counting:session-detail", args=[session.pk]),
        reverse("counting:session-start", args=[session.pk]),
        reverse("counting:quantity-count", args=[session.pk]),
        reverse("counting:unexpected-quantity", args=[session.pk]),
        reverse("counting:serialized-count", args=[session.pk]),
        reverse("counting:serialized-candidate", args=[session.pk]),
        reverse("counting:session-complete", args=[session.pk]),
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code in {200, 302}
        assert _inventory_snapshot()[:3] == before[:3]
    assert result.serialized_asset.pk


def test_no_ad_hoc_initial_balance_route():
    with pytest.raises(NoReverseMatch):
        reverse("inventory:initial-balance-create")
    with pytest.raises(NoReverseMatch):
        reverse("imports:initial-balance-create")


def test_candidate_serialized_does_not_create_asset(client, ui):
    session = _started_session(ui)
    url = reverse("counting:serialized-candidate", args=[session.pk])
    client.force_login(ui["performer"])
    before = _inventory_snapshot()
    response = client.post(
        url,
        {
            "material": str(ui["serialized_material"].pk),
            "internal_asset_code": f"CUI-CAND-{uuid.uuid4().hex[:8]}",
            "serial_number": "",
            "observed_location": str(ui["location"].pk),
            "observed_condition": str(ui["condition"].pk),
        },
    )
    assert response.status_code == 302
    line = session.serialized_lines.get()
    assert line.expected_present is False
    assert line.serialized_asset_id is None
    assert _inventory_snapshot()[:3] == before[:3]


def test_count_post_requires_csrf(ui):
    from django.test import Client

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(ui["performer"])
    response = csrf_client.post(
        reverse("counting:session-create"),
        {
            "reference_number": "CUI-CSRF",
            "scope_location": str(ui["root"].pk),
        },
    )
    assert response.status_code == 403
    assert not PhysicalCountSession.objects.filter(
        reference_number="CUI-CSRF"
    ).exists()
