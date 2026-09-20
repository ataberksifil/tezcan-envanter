from __future__ import annotations

import re
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, UnitOfMeasure
from catalog.services.materials import (
    GENERATED_MATERIAL_CODE_PATTERN,
    create_material,
    is_generated_material_code,
    update_material,
)
from identification.codec import encode_material
from inventory.models import InventoryTransaction, StockBalance

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _refresh_user_permissions(user):
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _create_ordinary_user(username):
    return get_user_model().objects.create_user(username=username, password=PASSWORD)


def _role_user(role_name, username=None):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(username or f"{role_name.lower()}-{uuid.uuid4().hex[:8]}")
    user.groups.add(Group.objects.get(name=role_name))
    return _refresh_user_permissions(user)


def _login(app_client, user):
    app_client.force_login(user)
    return user


def _category():
    return Category.objects.create(name=f"INB-CAT-{uuid.uuid4().hex[:6]}")


def _unit():
    return UnitOfMeasure.objects.create(
        code=f"INB-U-{uuid.uuid4().hex[:6]}",
        name="Adet",
    )


def _valid_post(category, unit, **overrides):
    data = {
        "name": "Teflon Bant",
        "category": str(category.pk),
        "brand": "DemoBrand",
        "model": "opaque-1234567890",
        "unit": str(unit.pk),
        "tracking_mode": Material.TrackingMode.QUANTITY,
        "minimum_stock_value": "",
        "search_keywords": "teflon, bant, ptfe",
    }
    data.update(overrides)
    return data


def test_create_material_generates_opaque_code_without_user_supply():
    actor = _role_user(ADMIN_MANAGER, "inb-gen-admin")
    result = create_material(
        actor=actor,
        name="Generated Material",
        category_id=_category().pk,
        unit_id=_unit().pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
        search_keywords="24V, encoder",
    )
    code = result.material.material_code
    assert is_generated_material_code(code)
    assert GENERATED_MATERIAL_CODE_PATTERN.fullmatch(code)
    assert "24V" not in code
    assert "encoder" not in code
    assert str(result.material.category_id) not in code
    event = AuditEvent.objects.get(entity_id=result.material.id, event_type="catalog.material.created")
    assert event.after_data["material_code"] == code
    assert event.after_data["search_keywords"] == "24V, encoder"


def test_generated_code_stays_stable_after_edits():
    actor = _role_user(ADMIN_MANAGER, "inb-stable-admin")
    created = create_material(
        actor=actor,
        name="Stable Code",
        category_id=_category().pk,
        unit_id=_unit().pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    original = created.material.material_code
    updated = update_material(
        actor=actor,
        material_id=created.material.pk,
        material_code=created.material.material_code,
        name="Stable Code Updated",
        category_id=created.material.category_id,
        unit_id=created.material.unit_id,
        tracking_mode=created.material.tracking_mode,
        search_keywords="ohm, 2.2k",
    )
    assert updated.material.material_code == original
    assert updated.material.search_keywords == "ohm, 2.2k"


def test_historical_material_codes_remain_intact():
    actor = _role_user(ADMIN_MANAGER, "inb-hist-admin")
    historical = Material.objects.create(
        material_code="DEMO-KLEMENS-COPY",
        name="Historical",
        category=_category(),
        unit=_unit(),
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    updated = update_material(
        actor=actor,
        material_id=historical.pk,
        material_code=historical.material_code,
        name="Historical Updated",
        category_id=historical.category_id,
        unit_id=historical.unit_id,
        tracking_mode=historical.tracking_mode,
    )
    assert updated.material.material_code == "DEMO-KLEMENS-COPY"


def test_storekeeper_and_admin_can_create_technician_cannot(app_client):
    category = _category()
    unit = _unit()
    storekeeper = _role_user(STOREKEEPER, "inb-sk-create")
    admin = _role_user(ADMIN_MANAGER, "inb-ad-create")
    technician = _role_user(TECHNICIAN, "inb-tech-create")

    _login(app_client, storekeeper)
    sk_response = app_client.post(
        reverse("catalog:material-create"),
        _valid_post(category, unit, name="Storekeeper Material"),
        follow=True,
    )
    assert sk_response.status_code == 200
    created = Material.objects.get(name="Storekeeper Material")
    assert is_generated_material_code(created.material_code)
    assert InventoryTransaction.objects.count() == 0
    assert StockBalance.objects.count() == 0

    _login(app_client, admin)
    ad_response = app_client.post(
        reverse("catalog:material-create"),
        _valid_post(category, unit, name="Admin Material"),
    )
    assert ad_response.status_code == 302

    before = Material.objects.count()
    _login(app_client, technician)
    tech_get = app_client.get(reverse("catalog:material-create"))
    tech_post = app_client.post(
        reverse("catalog:material-create"),
        _valid_post(category, unit, name="Technician Material"),
    )
    assert tech_get.status_code == 403
    assert tech_post.status_code == 403
    assert Material.objects.count() == before
    assert Material.objects.filter(name="Technician Material").count() == 0


def test_model_scan_helper_fills_editable_model_and_does_not_autosave(app_client):
    user = _role_user(STOREKEEPER, "inb-scan")
    category = _category()
    unit = _unit()
    _login(app_client, user)
    before_count = Material.objects.count()
    get_response = app_client.get(reverse("catalog:material-create"))
    content = get_response.content.decode()
    assert get_response.status_code == 200
    assert "Barkod Tara" in content
    assert "data-model-scan-target" in content
    assert "model-scan-helper" in content
    assert "Form otomatik kaydedilmez" in content or "form kaydedilmez" in content.lower()
    assert Material.objects.count() == before_count
    assert InventoryTransaction.objects.count() == 0

    posted = _valid_post(
        category,
        unit,
        name="Scanned Model Material",
        model="  8690123456789  ",
        material_code="FORGED-CODE",
    )
    response = app_client.post(reverse("catalog:material-create"), posted, follow=True)
    assert response.status_code == 200
    material = Material.objects.get(name="Scanned Model Material")
    assert material.model == "8690123456789"
    assert material.material_code != "FORGED-CODE"
    assert is_generated_material_code(material.material_code)
    assert InventoryTransaction.objects.count() == 0
    assert encode_material(material.pk).startswith("TZ1M:")


def test_http_update_preserves_code_and_keeps_tz1_payload(app_client):
    user = _role_user(ADMIN_MANAGER, "inb-update")
    category = _category()
    unit = _unit()
    _login(app_client, user)
    app_client.post(
        reverse("catalog:material-create"),
        _valid_post(category, unit, name="Keep Code"),
        follow=True,
    )
    material = Material.objects.get(name="Keep Code")
    original = material.material_code
    original_payload = encode_material(material.pk)
    app_client.post(
        reverse("catalog:material-update", args=[material.pk]),
        _valid_post(
            category,
            unit,
            name="Keep Code Edited",
            material_code="CHANGED",
            model="edited-mpn",
        ),
        follow=True,
    )
    material.refresh_from_db()
    assert material.material_code == original
    assert material.model == "edited-mpn"
    assert encode_material(material.pk) == original_payload
    assert re.fullmatch(r"TZ1M:[A-Za-z0-9_-]{22}", original_payload)


def test_search_is_case_insensitive_across_identity_fields(app_client):
    category = _category()
    unit = _unit()
    Material.objects.create(
        material_code="SRCH-63A",
        name="Siemens Encoder",
        category=category,
        unit=unit,
        brand="Siemens",
        model="1XP8034",
        tracking_mode=Material.TrackingMode.QUANTITY,
        search_keywords="63a, 24v, encoder",
    )
    user = _role_user(TECHNICIAN, "inb-search")
    _login(app_client, user)
    for query in ("srch-63a", "siemens", "1xp8034", "ENCODER", "24V"):
        content = app_client.get("/catalog/materials/", {"q": query}).content.decode()
        assert "SRCH-63A" in content
    outsider = _create_ordinary_user("inb-no-view")
    _login(app_client, outsider)
    assert app_client.get("/catalog/materials/", {"q": "siemens"}).status_code == 403


def test_storekeeper_cannot_edit_or_deactivate_existing_material_by_default(app_client):
    category = _category()
    unit = _unit()
    storekeeper = _role_user(STOREKEEPER, "inb-sk-no-change")
    admin = _role_user(ADMIN_MANAGER, "inb-ad-change")
    _login(app_client, admin)
    created = create_material(
        actor=admin,
        name="Existing Material",
        category_id=category.pk,
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material = created.material
    _login(app_client, storekeeper)
    assert storekeeper.has_perm("catalog.add_material") is True
    assert storekeeper.has_perm("catalog.change_material") is False
    update_get = app_client.get(reverse("catalog:material-update", args=[material.pk]))
    update_post = app_client.post(
        reverse("catalog:material-update", args=[material.pk]),
        _valid_post(category, unit, name="Hacked Name"),
    )
    deactivate = app_client.post(
        reverse("catalog:material-deactivate", args=[material.pk])
    )
    assert update_get.status_code == 403
    assert update_post.status_code == 403
    assert deactivate.status_code == 403
    material.refresh_from_db()
    assert material.name == "Existing Material"
    assert material.active is True

    _login(app_client, admin)
    assert admin.has_perm("catalog.change_material") is True
    assert app_client.get(
        reverse("catalog:material-update", args=[material.pk])
    ).status_code == 200


def test_turkish_keyword_search_preserves_spelling_and_finds_workshop_queries(
    app_client,
):
    category = _category()
    unit = _unit()
    from catalog.models import normalize_material_search_keywords

    stored = normalize_material_search_keywords(
        "İzolasyon, Işık, Teflon Bant, izolasyon"
    )
    assert stored == "İzolasyon, Işık, Teflon, Bant"
    assert "\u0307" not in stored
    Material.objects.create(
        material_code="TR-KW-1",
        name="Yalıtım Malzemesi",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
        search_keywords=stored,
    )
    user = _role_user(TECHNICIAN, "inb-tr-search")
    _login(app_client, user)
    for query in (
        "izolasyon",
        "İzolasyon",
        "ışık",
        "Işık",
        "bant",
        "teflon",
    ):
        content = app_client.get("/catalog/materials/", {"q": query}).content.decode()
        assert "TR-KW-1" in content, query
    ascii_hit = app_client.get("/catalog/materials/", {"q": "Yalıtım"}).content.decode()
    assert "TR-KW-1" in ascii_hit
