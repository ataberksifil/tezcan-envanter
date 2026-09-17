from __future__ import annotations

import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

import pytest
from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from accounts.models import Employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from counting.models import PhysicalCountSession
from identification.codec import (
    encode_location,
    encode_location_qr,
    encode_material,
    encode_material_qr,
    encode_serialized_asset,
    encode_serialized_asset_qr,
)
from imports.models import InventoryBaseline
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    IssueContext,
    ProductionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.services.issues import issue_serialized
from inventory.services.receipts import receive_serialized
from locations.models import Location

pytestmark = pytest.mark.django_db
User = get_user_model()
SCANNER_JS = (
    Path(settings.BASE_DIR) / "identification/static/identification/js/scanner.js"
)


def _grant(user, *labels):
    for label in labels:
        app_label, codename = label.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
        )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def qr_objects():
    suffix = uuid.uuid4().hex[:8]
    category = Category.objects.create(name=f"QR kategori {suffix}")
    unit = UnitOfMeasure.objects.create(code=f"QR-U-{suffix}", name="Adet")
    quantity_material = Material.objects.create(
        material_code=f"QR-M-{suffix}",
        name="QR <script>alert('material')</script>",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    serialized_material = Material.objects.create(
        material_code=f"QR-SM-{suffix}",
        name="QR tekil malzeme",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.SERIALIZED,
    )
    condition = MaterialCondition.objects.create(
        code=f"QR-C-{suffix}", name="Yeni", sort_order=990
    )
    location = Location.objects.create(
        code=f"QR-L-{suffix}",
        name="QR <b>lokasyon</b>",
        can_hold_stock=True,
    )
    inactive_location = Location.objects.create(
        code=f"QR-LI-{suffix}",
        name="Pasif QR lokasyonu",
        active=False,
        can_hold_stock=True,
    )
    asset = SerializedAsset.objects.create(
        material=serialized_material,
        internal_asset_code=f"QR-A-{suffix}",
        serial_number=f"SN-{suffix}",
        current_location=location,
        current_condition=condition,
        current_state=SerializedAsset.CurrentState.IN_STOCK,
    )
    StockBalance.objects.create(
        material=quantity_material,
        location=location,
        condition=condition,
        quantity=Decimal("4.000"),
    )
    return {
        "quantity_material": quantity_material,
        "serialized_material": serialized_material,
        "condition": condition,
        "location": location,
        "inactive_location": inactive_location,
        "asset": asset,
    }


@pytest.fixture
def viewer(qr_objects):
    return _grant(
        User.objects.create_user(username=f"qr-viewer-{uuid.uuid4().hex[:8]}"),
        "catalog.view_material",
        "inventory.view_stockbalance",
        "locations.view_location",
    )


def _inventory_counts():
    return {
        "transactions": InventoryTransaction.objects.count(),
        "lines": InventoryTransactionLine.objects.count(),
        "balances": StockBalance.objects.count(),
        "contexts": IssueContext.objects.count(),
        "counts": PhysicalCountSession.objects.count(),
        "baselines": InventoryBaseline.objects.count(),
    }


def _routes(data):
    return (
        reverse("identification:scanner"),
        reverse("identification:material-image", args=[data["quantity_material"].pk]),
        reverse("identification:material-code128", args=[data["quantity_material"].pk]),
        reverse("identification:material-label", args=[data["quantity_material"].pk]),
        reverse(
            "identification:material-label-compact",
            args=[data["quantity_material"].pk],
        ),
        reverse("identification:asset-image", args=[data["asset"].pk]),
        reverse("identification:asset-code128", args=[data["asset"].pk]),
        reverse("identification:asset-label", args=[data["asset"].pk]),
        reverse("identification:asset-label-compact", args=[data["asset"].pk]),
        reverse("identification:location-image", args=[data["location"].pk]),
        reverse("identification:location-code128", args=[data["location"].pk]),
        reverse("identification:location-label", args=[data["location"].pk]),
        reverse("identification:location-label-compact", args=[data["location"].pk]),
    )


def test_named_routes(qr_objects):
    asset_pk = qr_objects["asset"].pk
    material_pk = qr_objects["quantity_material"].pk
    assert reverse("identification:scanner") == "/identification/scan/"
    assert reverse("identification:resolve") == "/identification/resolve/"
    assert reverse("identification:asset-label", args=[asset_pk]) == (
        f"/identification/assets/{asset_pk}/label/"
    )
    assert reverse("identification:asset-label-compact", args=[asset_pk]) == (
        f"/identification/assets/{asset_pk}/label/compact/"
    )
    assert reverse("identification:material-code128", args=[material_pk]) == (
        f"/identification/materials/{material_pk}/code128.svg"
    )
    assert reverse("identification:material-image", args=[material_pk]) == (
        f"/identification/materials/{material_pk}/qr.png"
    )


@pytest.mark.parametrize("route_index", range(13))
def test_qr_read_routes_require_authentication(client, qr_objects, route_index):
    url = _routes(qr_objects)[route_index]
    response = client.get(url)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [url]


def test_resolver_requires_authentication(client, qr_objects):
    url = reverse("identification:resolve")
    response = client.post(
        url,
        {"payload": encode_material(qr_objects["quantity_material"].pk)},
    )
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"


def test_scanner_requires_at_least_one_supported_view_permission(client):
    user = User.objects.create_user(username=f"qr-none-{uuid.uuid4().hex[:8]}")
    client.force_login(user)
    assert client.get(reverse("identification:scanner")).status_code == 403


def test_scanner_includes_local_multiformat_library_manual_and_hid_path(
    client, viewer
):
    client.force_login(viewer)
    response = client.get(reverse("identification:scanner"))
    content = response.content.decode()
    js = SCANNER_JS.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "/static/vendor/zxing/zxing-browser.min.js" in content
    assert "/static/identification/js/scanner.js" in content
    assert "cdn." not in content.lower()
    assert "Kamerayı başlat" in content
    assert "Kamerayı durdur" in content
    assert "Tekrar dene" in content
    assert "Elle gir veya USB okuyucu kullan" in content
    assert "autofocus" in content
    assert 'action="/identification/resolve/"' in content
    assert "navigator.mediaDevices" not in content
    assert "BrowserMultiFormatReader" in js
    assert "CODE_128" in js
    assert "QR_CODE" in js
    assert "BrowserQRCodeReader" not in js
    assert "addEventListener(\"keydown\"" not in js
    assert "cdn" not in js.lower()
    assert "DataMatrix" not in js


@pytest.mark.parametrize(
    ("payload_factory", "object_key", "target_route"),
    (
        (encode_material, "quantity_material", "catalog:material-detail"),
        (encode_material_qr, "quantity_material", "catalog:material-detail"),
        (encode_serialized_asset, "asset", "inventory:serialized-asset-detail"),
        (encode_serialized_asset_qr, "asset", "inventory:serialized-asset-detail"),
        (encode_location, "location", "locations:location-detail"),
        (encode_location_qr, "location", "locations:location-detail"),
    ),
)
def test_resolve_redirects_to_canonical_detail(
    client, viewer, qr_objects, payload_factory, object_key, target_route
):
    client.force_login(viewer)
    target = qr_objects[object_key]
    response = client.post(
        reverse("identification:resolve"),
        {"payload": payload_factory(target.pk)},
    )
    assert response.status_code == 302
    assert response.url == reverse(target_route, args=[target.pk])
    assert not response.url.startswith("http")
    assert "://" not in response.url


def test_inactive_location_resolves_to_its_own_canonical_detail(
    client, viewer, qr_objects
):
    client.force_login(viewer)
    location = qr_objects["inactive_location"]
    response = client.post(
        reverse("identification:resolve"),
        {"payload": encode_location(location.pk)},
    )
    assert response.status_code == 302
    assert response.url == reverse("locations:location-detail", args=[location.pk])


@pytest.mark.parametrize(
    "payload",
    (
        "",
        "random text",
        "OTHER:1:M:00000000-0000-0000-0000-000000000000",
        "TEZCAN:1:M:00000000-0000-0000-0000-000000000000",
        "TEZCAN:2:M:00000000-0000-0000-0000-000000000000",
        "TZ1X:AAAAAAAAAAAAAAAAAAAAAA",
        "TZ1M:",
        "TZ1M:AAAAAAAAAAAAAAAAAAAAA",
        "TZ1M:AAAAAAAAAAAAAAAAAAAAAAA",
        "TZ1M:AAAAAAAAAAAAAAAAAAAAAA==",
        " TZ1M:AAAAAAAAAAAAAAAAAAAAAA",
        "TZ1M:AAAAAAAAAAAAAAAAAAAAAA ",
    ),
)
def test_invalid_manual_payload_is_friendly_400(client, viewer, payload):
    client.force_login(viewer)
    response = client.post(reverse("identification:resolve"), {"payload": payload})
    assert response.status_code == 400
    content = response.content.decode()
    assert "invalid-feedback" in content or "alert-danger" in content
    assert "://evil" not in content


def test_well_formed_nonexistent_object_is_friendly_404(client, viewer):
    client.force_login(viewer)
    response = client.post(
        reverse("identification:resolve"),
        {"payload": encode_material(uuid.uuid4())},
    )
    assert response.status_code == 404
    assert "kayıt bulunamadı" in response.content.decode()


@pytest.mark.parametrize(
    ("payload_factory", "object_key"),
    (
        (encode_serialized_asset, "quantity_material"),
        (encode_material, "location"),
    ),
)
def test_wrong_discriminator_does_not_cross_resolve(
    client, viewer, qr_objects, payload_factory, object_key
):
    client.force_login(viewer)
    response = client.post(
        reverse("identification:resolve"),
        {"payload": payload_factory(qr_objects[object_key].pk)},
    )
    assert response.status_code == 404
    assert "kayıt bulunamadı" in response.content.decode()


def test_resolver_checks_type_permission_before_object_lookup(client, qr_objects):
    user = _grant(
        User.objects.create_user(username=f"qr-material-only-{uuid.uuid4().hex[:8]}"),
        "catalog.view_material",
    )
    client.force_login(user)
    response = client.post(
        reverse("identification:resolve"),
        {"payload": encode_location(qr_objects["location"].pk)},
    )
    assert response.status_code == 403
    assert qr_objects["location"].name not in response.content.decode()


@pytest.mark.parametrize(
    ("route_name", "object_key", "permission"),
    (
        ("identification:material-image", "quantity_material", "catalog.view_material"),
        ("identification:material-code128", "quantity_material", "catalog.view_material"),
        ("identification:material-label", "quantity_material", "catalog.view_material"),
        (
            "identification:material-label-compact",
            "quantity_material",
            "catalog.view_material",
        ),
        ("identification:asset-image", "asset", "inventory.view_stockbalance"),
        ("identification:asset-code128", "asset", "inventory.view_stockbalance"),
        ("identification:asset-label", "asset", "inventory.view_stockbalance"),
        ("identification:asset-label-compact", "asset", "inventory.view_stockbalance"),
        ("identification:location-image", "location", "locations.view_location"),
        ("identification:location-code128", "location", "locations.view_location"),
        ("identification:location-label", "location", "locations.view_location"),
        (
            "identification:location-label-compact",
            "location",
            "locations.view_location",
        ),
    ),
)
def test_image_and_label_permission_enforced(
    client, qr_objects, route_name, object_key, permission
):
    user = User.objects.create_user(username=f"qr-denied-{uuid.uuid4().hex[:8]}")
    client.force_login(user)
    url = reverse(route_name, args=[qr_objects[object_key].pk])
    assert client.get(url).status_code == 403

    user = _grant(user, permission)
    client.force_login(user)
    assert client.get(url).status_code == 200


@pytest.mark.parametrize(
    ("route_name", "object_key", "payload_factory"),
    (
        ("identification:material-image", "quantity_material", encode_material),
        ("identification:asset-image", "asset", encode_serialized_asset),
        ("identification:location-image", "location", encode_location),
    ),
)
def test_qr_image_uses_correct_canonical_payload(
    client, viewer, qr_objects, route_name, object_key, payload_factory
):
    client.force_login(viewer)
    target = qr_objects[object_key]
    with patch("identification.views.generate_qr_png", return_value=b"png") as generate:
        response = client.get(reverse(route_name, args=[target.pk]))

    assert response.status_code == 200
    generate.assert_called_once_with(payload_factory(target.pk))


@pytest.mark.parametrize(
    ("route_name", "object_key", "payload_factory"),
    (
        ("identification:material-code128", "quantity_material", encode_material),
        ("identification:asset-code128", "asset", encode_serialized_asset),
        ("identification:location-code128", "location", encode_location),
    ),
)
def test_code128_image_uses_the_same_canonical_payload(
    client, viewer, qr_objects, route_name, object_key, payload_factory
):
    client.force_login(viewer)
    target = qr_objects[object_key]
    with patch(
        "identification.views.generate_code128_svg", return_value=b"<svg></svg>"
    ) as generate:
        response = client.get(reverse(route_name, args=[target.pk]))

    assert response.status_code == 200
    generate.assert_called_once_with(payload_factory(target.pk))


def test_qr_image_is_valid_png_private_and_downloadable(
    client, viewer, qr_objects
):
    client.force_login(viewer)
    url = reverse(
        "identification:material-image",
        args=[qr_objects["quantity_material"].pk],
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert "private" in response["Cache-Control"]
    assert "no-store" in response["Cache-Control"]
    assert response["X-Content-Type-Options"] == "nosniff"
    image = Image.open(BytesIO(response.content))
    assert image.format == "PNG"
    assert image.width == image.height

    download = client.get(url, {"download": "1"})
    assert download["Content-Disposition"].startswith("attachment;")


def test_code128_image_is_valid_svg_private_and_downloadable(
    client, viewer, qr_objects
):
    client.force_login(viewer)
    url = reverse(
        "identification:material-code128",
        args=[qr_objects["quantity_material"].pk],
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    assert "private" in response["Cache-Control"]
    assert "no-store" in response["Cache-Control"]
    assert response["X-Content-Type-Options"] == "nosniff"
    root = ET.fromstring(response.content)
    width = root.attrib["width"]
    assert width.endswith("mm")
    assert float(width[:-2]) == 88.0

    download = client.get(url, {"download": "1"})
    assert download["Content-Disposition"].startswith("attachment;")
    assert download["Content-Disposition"].endswith('.svg"') or ".svg" in download[
        "Content-Disposition"
    ]


@pytest.mark.parametrize(
    ("route_name", "object_key", "required_text"),
    (
        ("identification:material-label", "quantity_material", "malzeme türünü"),
        ("identification:asset-label", "asset", "SN-"),
        ("identification:location-label", "location", "QR-L-"),
    ),
)
def test_standard_labels_use_code128_and_human_readable_values(
    client, viewer, qr_objects, route_name, object_key, required_text
):
    client.force_login(viewer)
    target = qr_objects[object_key]
    response = client.get(reverse(route_name, args=[target.pk]))
    content = response.content.decode()
    assert response.status_code == 200
    assert required_text in content
    assert "window.print()" in content
    assert "Standart barkod" in content
    assert "Kompakt QR" in content
    assert "code128.svg" in content
    assert "TZ1" in content
    assert "id-label--standard" in content
    assert "id-label--compact" not in content
    assert "DataMatrix" not in content


@pytest.mark.parametrize(
    ("route_name", "object_key", "required_text"),
    (
        ("identification:material-label-compact", "quantity_material", "malzeme türünü"),
        ("identification:asset-label-compact", "asset", "SN-"),
        ("identification:location-label-compact", "location", "QR-L-"),
    ),
)
def test_compact_labels_use_qr_and_human_readable_values(
    client, viewer, qr_objects, route_name, object_key, required_text
):
    client.force_login(viewer)
    target = qr_objects[object_key]
    response = client.get(reverse(route_name, args=[target.pk]))
    content = response.content.decode()
    assert response.status_code == 200
    assert required_text in content
    assert "window.print()" in content
    assert "Kompakt QR" in content
    assert "qr.png" in content
    assert "TZ1" in content
    assert "id-label--compact" in content
    assert "id-label--standard" not in content
    assert "code128.svg" not in content.split("id-label--compact")[-1]


def test_label_escapes_user_controlled_names(client, viewer, qr_objects):
    client.force_login(viewer)
    material = client.get(
        reverse(
            "identification:material-label",
            args=[qr_objects["quantity_material"].pk],
        )
    ).content.decode()
    compact = client.get(
        reverse(
            "identification:material-label-compact",
            args=[qr_objects["quantity_material"].pk],
        )
    ).content.decode()
    location = client.get(
        reverse(
            "identification:location-label", args=[qr_objects["location"].pk]
        )
    ).content.decode()
    assert "<script>" not in material
    assert "&lt;script&gt;" in material
    assert "<script>" not in compact
    assert "&lt;script&gt;" in compact
    assert "<b>lokasyon</b>" not in location
    assert "&lt;b&gt;lokasyon&lt;/b&gt;" in location


def test_scan_render_and_print_are_read_only(client, viewer, qr_objects):
    client.force_login(viewer)
    before = _inventory_counts()

    assert client.get(reverse("identification:scanner")).status_code == 200
    assert client.post(
        reverse("identification:resolve"),
        {"payload": encode_serialized_asset(qr_objects["asset"].pk)},
    ).status_code == 302
    for url in _routes(qr_objects)[1:]:
        assert client.get(url).status_code == 200

    qr_objects["asset"].refresh_from_db()
    assert qr_objects["asset"].current_state == SerializedAsset.CurrentState.IN_STOCK
    assert qr_objects["asset"].current_location_id == qr_objects["location"].pk
    assert _inventory_counts() == before


def test_material_and_location_details_expose_labels_and_safe_navigation(
    client, viewer, qr_objects
):
    client.force_login(viewer)
    material_content = client.get(
        reverse(
            "catalog:material-detail", args=[qr_objects["quantity_material"].pk]
        )
    ).content.decode()
    location_content = client.get(
        reverse("locations:location-detail", args=[qr_objects["location"].pk])
    ).content.decode()
    assert reverse(
        "identification:material-label", args=[qr_objects["quantity_material"].pk]
    ) in material_content
    assert "tek bir fiziksel birimi temsil etmez" in material_content
    assert reverse(
        "identification:location-label", args=[qr_objects["location"].pk]
    ) in location_content
    assert f"?location={qr_objects['location'].pk}" in location_content
    assert "Bu lokasyondaki güncel stok" in location_content
    assert qr_objects["quantity_material"].material_code in location_content
    assert qr_objects["asset"].internal_asset_code in location_content


def test_location_stock_section_requires_stock_view_permission(client, qr_objects):
    user = _grant(
        User.objects.create_user(username=f"loc-only-{uuid.uuid4().hex[:8]}"),
        "locations.view_location",
    )
    client.force_login(user)
    content = client.get(
        reverse("locations:location-detail", args=[qr_objects["location"].pk])
    ).content.decode()
    assert "Bu lokasyondaki güncel stok" not in content
    assert qr_objects["asset"].internal_asset_code not in content


def test_in_stock_asset_detail_keeps_phase_57_actions_and_adds_labels(
    client, qr_objects
):
    user = _grant(
        User.objects.create_user(username=f"qr-mover-{uuid.uuid4().hex[:8]}"),
        "inventory.view_stockbalance",
        "inventory.issue_stock",
        "inventory.transfer_stock",
        "inventory.return_stock",
        "catalog.view_material",
    )
    client.force_login(user)
    response = client.get(
        reverse(
            "inventory:serialized-asset-detail", args=[qr_objects["asset"].pk]
        )
    )
    content = response.content.decode()
    asset_pk = qr_objects["asset"].pk
    issue_url = reverse("inventory:serialized-issue-create", args=[asset_pk])
    transfer_url = reverse("inventory:serialized-transfer-create", args=[asset_pk])
    assert response.status_code == 200
    assert "Çıkış yap" in content
    assert "Transfer et" in content
    assert "İade al" not in content
    assert reverse("identification:asset-label", args=[asset_pk]) in content
    assert issue_url in content
    assert transfer_url in content
    assert content.count(issue_url) == 1
    assert content.count(transfer_url) == 1
    assert f"/inventory/serialized-assets/{asset_pk}/returns/" not in content


def test_issued_asset_scan_lands_on_return_only_quick_action(client, qr_objects):
    suffix = uuid.uuid4().hex[:8]
    actor = _grant(
        User.objects.create_user(username=f"qr-issued-{suffix}"),
        "inventory.receive_stock",
        "inventory.issue_stock",
        "inventory.return_stock",
        "inventory.transfer_stock",
        "inventory.view_stockbalance",
        "catalog.view_material",
    )
    received = receive_serialized(
        actor=actor,
        operation_id=uuid.uuid4(),
        material_id=qr_objects["serialized_material"].pk,
        internal_asset_code=f"QR-ISSUED-{suffix}",
        serial_number=f"QR-ISSUED-SN-{suffix}",
        condition_id=qr_objects["condition"].pk,
        target_location_id=qr_objects["location"].pk,
    )
    employee = Employee.objects.create(
        employee_number=f"QR-E-{suffix}",
        first_name="Ayşe",
        last_name="Yılmaz",
    )
    production_line = ProductionLine.objects.create(
        code=f"QR-PL-{suffix}", name="QR hattı"
    )
    issue_serialized(
        actor=actor,
        operation_id=uuid.uuid4(),
        serialized_asset_id=received.serialized_asset.pk,
        source_location_id=qr_objects["location"].pk,
        condition_id=qr_objects["condition"].pk,
        receiver_employee_id=employee.pk,
        production_line_id=production_line.pk,
        usage_location_text="Pano 7",
    )

    client.force_login(actor)
    resolved = client.post(
        reverse("identification:resolve"),
        {"payload": encode_serialized_asset(received.serialized_asset.pk)},
    )
    assert resolved.status_code == 302
    detail = client.get(resolved.url).content.decode()
    asset_pk = received.serialized_asset.pk
    issue_url = reverse("inventory:serialized-issue-create", args=[asset_pk])
    transfer_url = reverse("inventory:serialized-transfer-create", args=[asset_pk])
    serialized_return_prefix = f"/inventory/serialized-assets/{asset_pk}/returns/"
    assert "İade al" in detail
    assert ">Çıkış yap</a>" not in detail
    assert issue_url not in detail
    assert transfer_url not in detail
    assert serialized_return_prefix in detail
    assert detail.count(serialized_return_prefix) == 1


def test_resolve_endpoint_is_post_only(client, viewer):
    client.force_login(viewer)
    assert client.get(reverse("identification:resolve")).status_code == 405
