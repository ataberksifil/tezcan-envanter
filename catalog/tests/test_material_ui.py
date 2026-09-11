from __future__ import annotations

import uuid
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model, SESSION_KEY
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, UnitOfMeasure
from catalog.views import MATERIAL_LIST_PAGE_SIZE

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
    user_model = get_user_model()
    user = user_model.objects.create_user(username=username, password=PASSWORD)
    assert not user.is_superuser
    assert not user.is_staff
    return user


def _role_user(role_name, username=None):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(username or f"{role_name.lower()}-{uuid.uuid4().hex[:8]}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert not user.is_superuser
    return user


def _login(app_client, user):
    app_client.force_login(user)
    return user


def _category(name="Test Category", *, active=True):
    return Category.objects.create(name=name, active=active)


def _unit(code=None, *, active=True):
    return UnitOfMeasure.objects.create(
        code=code or f"U-{uuid.uuid4().hex[:6]}",
        name="Test Unit",
        active=active,
    )


def _material(**overrides):
    category = overrides.pop("category", None) or _category()
    explicit_unit = "unit" in overrides
    unit = overrides.pop("unit", None)
    tracking_mode = overrides.get("tracking_mode", Material.TrackingMode.QUANTITY)
    if not explicit_unit:
        unit = _unit() if tracking_mode == Material.TrackingMode.QUANTITY else None
    defaults = {
        "material_code": f"MAT-{uuid.uuid4().hex[:6]}",
        "name": f"Material {uuid.uuid4().hex[:6]}",
        "category": category,
        "tracking_mode": tracking_mode,
        "unit": unit,
    }
    defaults.update(overrides)
    return Material.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_material_named_routes():
    material = _material()
    assert reverse("catalog:material-list") == "/catalog/materials/"
    assert (
        reverse("catalog:material-detail", args=[material.pk])
        == f"/catalog/materials/{material.pk}/"
    )


def test_no_material_mutation_routes_exist(app_client):
    material = _material()
    for name in (
        "material-create",
        "material-update",
        "material-delete",
        "material-deactivate",
        "material-reactivate",
    ):
        with pytest.raises(NoReverseMatch):
            reverse(f"catalog:{name}")
        with pytest.raises(NoReverseMatch):
            reverse(f"catalog:{name}", args=[material.pk])

    user = _role_user(ADMIN_MANAGER, "no-mut-mat-admin")
    _login(app_client, user)
    for suffix in ("new/", "edit/", "delete/", "deactivate/", "reactivate/"):
        response = app_client.post(f"/catalog/materials/{material.pk}/{suffix}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    (
        "/catalog/materials/",
    ),
)
def test_anonymous_material_list_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == [path]


def test_anonymous_material_detail_redirects_to_login(app_client):
    material = _material()
    path = f"/catalog/materials/{material.pk}/"
    response = app_client.get(path)
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == [path]


def test_material_login_next_lands_on_list(app_client):
    user = _role_user(TECHNICIAN, "login-next-tech-mat")
    response = app_client.post(
        "/accounts/login/",
        {
            "username": user.username,
            "password": PASSWORD,
            "next": "/catalog/materials/",
        },
    )
    assert response.status_code == 302
    assert response.url == "/catalog/materials/"
    assert SESSION_KEY in app_client.session
    listing = app_client.get("/catalog/materials/")
    assert listing.status_code == 200


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_user_without_view_material_gets_403(app_client):
    user = _create_ordinary_user("no-view-mat")
    _login(app_client, user)
    material = _material()
    assert app_client.get("/catalog/materials/").status_code == 403
    assert (
        app_client.get(reverse("catalog:material-detail", args=[material.pk])).status_code
        == 403
    )


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER))
def test_view_permitted_roles_get_200(app_client, role_name):
    _material(name=f"ROLE-{role_name}")
    user = _role_user(role_name, f"mat-view-{role_name.lower()}")
    _login(app_client, user)
    response = app_client.get("/catalog/materials/")
    assert response.status_code == 200
    assert "Malzemeler" in response.content.decode()


# ---------------------------------------------------------------------------
# List rendering and search
# ---------------------------------------------------------------------------


def test_list_renders_expected_fields(app_client):
    category = _category(name="LIST-CAT")
    unit = _unit(code="LIST-U", active=True)
    material = _material(
        material_code="LIST-CODE",
        name="LIST-NAME",
        category=category,
        brand="LIST-BRAND",
        model="LIST-MODEL",
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=True,
    )
    user = _role_user(TECHNICIAN, "mat-list-fields")
    _login(app_client, user)
    content = app_client.get("/catalog/materials/", {"q": "LIST-NAME"}).content.decode()
    assert "LIST-CODE" in content
    assert "LIST-NAME" in content
    assert "LIST-CAT" in content
    assert "LIST-BRAND" in content
    assert "LIST-MODEL" in content
    assert "Miktar" in content
    assert "LIST-U" in content
    assert "Aktif" in content
    assert str(material.id) in content


@pytest.mark.parametrize(
    ("field", "query", "needle"),
    (
        ("material_code", "SRCH-CODE-XYZ", "SRCH-CODE-XYZ"),
        ("name", "SRCH-NAME-UNIQUE", "SRCH-NAME-UNIQUE"),
        ("brand", "SRCH-BRAND-UNIQUE", "SRCH-BRAND-UNIQUE"),
        ("model", "SRCH-MODEL-UNIQUE", "SRCH-MODEL-UNIQUE"),
    ),
)
def test_search_by_individual_fields(app_client, field, query, needle):
    kwargs = {field: query, "name": f"Base-{uuid.uuid4().hex[:6]}"}
    if field != "material_code":
        kwargs.setdefault("material_code", f"CODE-{uuid.uuid4().hex[:6]}")
    _material(**kwargs)
    user = _role_user(TECHNICIAN, f"mat-search-{field}")
    _login(app_client, user)
    content = app_client.get("/catalog/materials/", {"q": query}).content.decode()
    assert needle in content


def test_search_or_semantics(app_client):
    _material(material_code="OR-CODE-HIT", name="Other Name")
    _material(material_code="Other Code", name="OR-NAME-HIT")
    user = _role_user(TECHNICIAN, "mat-or-search")
    _login(app_client, user)
    code_content = app_client.get("/catalog/materials/", {"q": "OR-CODE-HIT"}).content.decode()
    name_content = app_client.get("/catalog/materials/", {"q": "OR-NAME-HIT"}).content.decode()
    assert "OR-CODE-HIT" in code_content
    assert "OR-NAME-HIT" in name_content


def test_technical_specs_not_searched(app_client):
    unique_token = f"SPEC-ONLY-{uuid.uuid4().hex[:12]}"
    _material(
        material_code="NO-SPEC-SEARCH",
        name="Visible Name",
        technical_specs={"hidden": unique_token},
    )
    user = _role_user(TECHNICIAN, "mat-no-spec-search")
    _login(app_client, user)
    content = app_client.get("/catalog/materials/", {"q": unique_token}).content.decode()
    assert "NO-SPEC-SEARCH" not in content
    assert "Visible Name" not in content


def test_category_filter_exact_match(app_client):
    cat_a = _category(name="FILTER-CAT-A")
    cat_b = _category(name="FILTER-CAT-B")
    mat_a = _material(material_code="CAT-A-MAT", name="CAT-A-MAT", category=cat_a)
    _material(material_code="CAT-B-MAT", name="CAT-B-MAT", category=cat_b)
    user = _role_user(TECHNICIAN, "mat-cat-filter")
    _login(app_client, user)
    content = app_client.get(
        "/catalog/materials/",
        {"category": str(cat_a.pk), "q": "CAT-"},
    ).content.decode()
    assert "CAT-A-MAT" in content
    assert "CAT-B-MAT" not in content
    assert str(mat_a.id) in content


def test_tracking_filter(app_client):
    _material(
        material_code="TRK-QTY",
        name="TRK-QTY",
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    _material(
        material_code="TRK-SER",
        name="TRK-SER",
        tracking_mode=Material.TrackingMode.SERIALIZED,
        unit=None,
    )
    user = _role_user(TECHNICIAN, "mat-tracking-filter")
    _login(app_client, user)
    qty_content = app_client.get(
        "/catalog/materials/",
        {"tracking": "QUANTITY", "q": "TRK-"},
    ).content.decode()
    ser_content = app_client.get(
        "/catalog/materials/",
        {"tracking": "SERIALIZED", "q": "TRK-"},
    ).content.decode()
    assert "TRK-QTY" in qty_content
    assert "TRK-SER" not in qty_content
    assert "TRK-SER" in ser_content
    assert "TRK-QTY" not in ser_content


def test_status_filters(app_client):
    _material(material_code="ST-ACT", name="ST-ACT", active=True)
    _material(material_code="ST-INACT", name="ST-INACT", active=False)
    user = _role_user(TECHNICIAN, "mat-status-filter")
    _login(app_client, user)

    active_content = app_client.get(
        "/catalog/materials/",
        {"q": "ST-", "status": "active"},
    ).content.decode()
    assert "ST-ACT" in active_content
    assert "ST-INACT" not in active_content

    inactive_content = app_client.get(
        "/catalog/materials/",
        {"q": "ST-", "status": "inactive"},
    ).content.decode()
    assert "ST-INACT" in inactive_content
    assert "ST-ACT" not in inactive_content

    all_content = app_client.get(
        "/catalog/materials/",
        {"q": "ST-", "status": "all"},
    ).content.decode()
    assert "ST-ACT" in all_content
    assert "ST-INACT" in all_content


def test_combined_filters_use_and_semantics(app_client):
    cat = _category(name="AND-CAT")
    other_cat = _category(name="AND-OTHER")
    hit = _material(
        material_code="AND-HIT",
        name="AND-HIT",
        category=cat,
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=True,
    )
    _material(
        material_code="AND-MISS-CAT",
        name="AND-MISS-CAT",
        category=other_cat,
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=True,
    )
    _material(
        material_code="AND-MISS-TRK",
        name="AND-MISS-TRK",
        category=cat,
        tracking_mode=Material.TrackingMode.SERIALIZED,
        unit=None,
        active=True,
    )
    _material(
        material_code="AND-MISS-ACT",
        name="AND-MISS-ACT",
        category=cat,
        tracking_mode=Material.TrackingMode.QUANTITY,
        active=False,
    )
    user = _role_user(TECHNICIAN, "mat-and-filter")
    _login(app_client, user)
    content = app_client.get(
        "/catalog/materials/",
        {
            "q": "AND-",
            "category": str(cat.pk),
            "tracking": "QUANTITY",
            "status": "active",
        },
    ).content.decode()
    assert "AND-HIT" in content
    assert "AND-MISS-CAT" not in content
    assert "AND-MISS-TRK" not in content
    assert "AND-MISS-ACT" not in content
    assert str(hit.id) in content


def test_invalid_category_filter_is_ignored(app_client):
    _material(material_code="BAD-CAT", name="BAD-CAT")
    user = _role_user(TECHNICIAN, "mat-bad-cat")
    _login(app_client, user)
    response = app_client.get(
        "/catalog/materials/",
        {"category": "not-a-uuid", "q": "BAD-CAT"},
    )
    assert response.status_code == 200
    assert response.context["category_filter"] is None
    assert "BAD-CAT" in response.content.decode()


def test_invalid_tracking_filter_is_ignored(app_client):
    _material(material_code="BAD-TRK", name="BAD-TRK")
    user = _role_user(TECHNICIAN, "mat-bad-trk")
    _login(app_client, user)
    response = app_client.get(
        "/catalog/materials/",
        {"tracking": "INVALID", "q": "BAD-TRK"},
    )
    assert response.status_code == 200
    assert response.context["tracking"] is None
    assert "BAD-TRK" in response.content.decode()


def test_invalid_status_falls_back_to_all(app_client):
    _material(material_code="BAD-ST-A", name="BAD-ST-A", active=True)
    _material(material_code="BAD-ST-I", name="BAD-ST-I", active=False)
    user = _role_user(TECHNICIAN, "mat-bad-status")
    _login(app_client, user)
    response = app_client.get(
        "/catalog/materials/",
        {"q": "BAD-ST", "status": "nope"},
    )
    assert response.status_code == 200
    assert response.context["status"] == "all"
    content = response.content.decode()
    assert "BAD-ST-A" in content
    assert "BAD-ST-I" in content


def test_deterministic_ordering(app_client):
    cat = _category()
    unit = _unit()
    m1 = Material.objects.create(
        material_code="ORDER-B",
        name="Alpha",
        category=cat,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    m2 = Material.objects.create(
        material_code="ORDER-A",
        name="Alpha",
        category=cat,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    m3 = Material.objects.create(
        material_code="ORDER-C",
        name="Beta",
        category=cat,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    user = _role_user(TECHNICIAN, "mat-order")
    _login(app_client, user)
    response = app_client.get("/catalog/materials/", {"q": "ORDER-"})
    ids = [material.id for material in response.context["materials"]]
    assert ids == [m2.id, m1.id, m3.id]


def test_pagination_and_query_params_preserved(app_client):
    cat = _category()
    unit = _unit()
    for index in range(MATERIAL_LIST_PAGE_SIZE + 5):
        Material.objects.create(
            material_code=f"PG-{index:03d}",
            name=f"ZZZ-PAGE-{index:03d}",
            category=cat,
            unit=unit,
            tracking_mode=Material.TrackingMode.QUANTITY,
            active=True,
        )
    user = _role_user(TECHNICIAN, "mat-pagination")
    _login(app_client, user)
    page1 = app_client.get(
        "/catalog/materials/",
        {"q": "ZZZ-PAGE", "status": "active", "tracking": "QUANTITY"},
    )
    assert page1.status_code == 200
    assert page1.context["paginator"].count == MATERIAL_LIST_PAGE_SIZE + 5
    assert page1.context["page_obj"].number == 1
    assert len(page1.context["materials"]) == MATERIAL_LIST_PAGE_SIZE
    content = page1.content.decode()
    assert "Sonraki" in content
    assert "q=ZZZ-PAGE" in content
    assert "status=active" in content
    assert "tracking=QUANTITY" in content

    page2 = app_client.get(
        "/catalog/materials/",
        {
            "q": "ZZZ-PAGE",
            "status": "active",
            "tracking": "QUANTITY",
            "page": "2",
        },
    )
    assert page2.status_code == 200
    assert page2.context["page_obj"].number == 2
    assert len(page2.context["materials"]) == 5


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_duplicate_material_code_rows_both_display(app_client):
    cat = _category()
    unit = _unit()
    shared_code = "DUP-CODE-123"
    m1 = Material.objects.create(
        material_code=shared_code,
        name="Dup One",
        category=cat,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    m2 = Material.objects.create(
        material_code=shared_code,
        name="Dup Two",
        category=cat,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    user = _role_user(TECHNICIAN, "mat-dup-code")
    _login(app_client, user)
    content = app_client.get("/catalog/materials/", {"q": shared_code}).content.decode()
    assert content.count(shared_code) >= 2
    assert "Dup One" in content
    assert "Dup Two" in content
    assert str(m1.id) in content
    assert str(m2.id) in content


def test_same_code_rows_resolve_to_different_detail_pages(app_client):
    cat = _category()
    unit = _unit()
    shared_code = "DUP-DETAIL"
    m1 = Material.objects.create(
        material_code=shared_code,
        name="Detail One",
        category=cat,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    m2 = Material.objects.create(
        material_code=shared_code,
        name="Detail Two",
        category=cat,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    user = _role_user(TECHNICIAN, "mat-dup-detail")
    _login(app_client, user)
    d1 = app_client.get(reverse("catalog:material-detail", args=[m1.pk]))
    d2 = app_client.get(reverse("catalog:material-detail", args=[m2.pk]))
    assert d1.status_code == 200
    assert d2.status_code == 200
    assert "Detail One" in d1.content.decode()
    assert "Detail Two" in d2.content.decode()
    assert str(m1.id) in d1.content.decode()
    assert str(m2.id) in d2.content.decode()


def test_unknown_uuid_returns_404(app_client):
    user = _role_user(TECHNICIAN, "mat-404")
    _login(app_client, user)
    response = app_client.get(f"/catalog/materials/{uuid.uuid4()}/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_inactive_category_shows_passive_reference(app_client):
    category = _category(name="PASSIVE-CAT", active=False)
    material = _material(name="PASSIVE-CAT-MAT", category=category)
    user = _role_user(TECHNICIAN, "mat-passive-cat")
    _login(app_client, user)
    list_content = app_client.get(
        "/catalog/materials/",
        {"q": "PASSIVE-CAT-MAT"},
    ).content.decode()
    detail_content = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    assert "PASSIVE-CAT" in list_content
    assert "Pasif referans" in list_content
    assert "Pasif referans" in detail_content


def test_inactive_unit_shows_passive_reference(app_client):
    unit = _unit(code="PASS-U", active=False)
    material = _material(name="PASSIVE-UOM-MAT", unit=unit)
    user = _role_user(TECHNICIAN, "mat-passive-uom")
    _login(app_client, user)
    list_content = app_client.get(
        "/catalog/materials/",
        {"q": "PASSIVE-UOM-MAT"},
    ).content.decode()
    detail_content = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    assert "PASS-U" in list_content
    assert "Pasif referans" in list_content
    assert "Pasif referans" in detail_content


def test_null_unit_shows_tanimli_degil(app_client):
    material = _material(
        name="NULL-UNIT-MAT",
        tracking_mode=Material.TrackingMode.SERIALIZED,
        unit=None,
    )
    user = _role_user(TECHNICIAN, "mat-null-unit")
    _login(app_client, user)
    list_content = app_client.get(
        "/catalog/materials/",
        {"q": "NULL-UNIT-MAT"},
    ).content.decode()
    detail_content = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    assert "Tanımlı değil" in list_content
    assert "Tanımlı değil" in detail_content


def test_serialized_null_unit_displays_safely(app_client):
    material = _material(
        name="SER-NULL-UNIT",
        tracking_mode=Material.TrackingMode.SERIALIZED,
        unit=None,
    )
    user = _role_user(TECHNICIAN, "mat-ser-null")
    _login(app_client, user)
    response = app_client.get(reverse("catalog:material-detail", args=[material.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Tekil" in content
    assert "Tanımlı değil" in content


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def test_minimum_stock_value_displayed_as_threshold_only(app_client):
    material = _material(
        name="MIN-STOCK-MAT",
        minimum_stock_value=Decimal("12.500"),
    )
    no_min = _material(name="NO-MIN-STOCK", minimum_stock_value=None)
    user = _role_user(TECHNICIAN, "mat-min-stock")
    _login(app_client, user)
    with_min = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    without_min = app_client.get(
        reverse("catalog:material-detail", args=[no_min.pk])
    ).content.decode()
    assert "12,500" in with_min
    assert "Minimum stok eşiği" in with_min
    assert "Tanımlı değil" in without_min
    assert "Mevcut stok" not in with_min
    assert "Uyarı" not in with_min


def test_empty_technical_specs_copy(app_client):
    material = _material(name="EMPTY-SPECS", technical_specs={})
    user = _role_user(TECHNICIAN, "mat-empty-specs")
    _login(app_client, user)
    content = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    assert "Teknik özellik tanımlanmamış" in content


def test_scalar_technical_specs(app_client):
    material = _material(
        name="SCALAR-SPECS",
        technical_specs={"voltage": "230V", "count": 3},
    )
    user = _role_user(TECHNICIAN, "mat-scalar-specs")
    _login(app_client, user)
    content = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    assert "voltage" in content
    assert "230V" in content
    assert "count" in content
    assert "3" in content


def test_nested_technical_specs_safe_representation(app_client):
    material = _material(
        name="NESTED-SPECS",
        technical_specs={
            "nested": {"inner": 1},
            "items": [1, 2],
        },
    )
    user = _role_user(TECHNICIAN, "mat-nested-specs")
    _login(app_client, user)
    content = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    assert "nested" in content
    assert "items" in content
    assert "inner" in content
    assert "1" in content
    assert "[1, 2]" in content or "[1,2]" in content


def test_technical_specs_top_level_key_ordering(app_client):
    material = _material(
        name="ORDER-SPECS",
        technical_specs={"z_key": "z", "a_key": "a", "m_key": "m"},
    )
    user = _role_user(TECHNICIAN, "mat-spec-order")
    _login(app_client, user)
    response = app_client.get(reverse("catalog:material-detail", args=[material.pk]))
    items = response.context["technical_specs_items"]
    assert [key for key, _value in items] == ["a_key", "m_key", "z_key"]


def test_xss_escaped_in_list_and_detail(app_client):
    payload = "<script>alert(1)</script>"
    material = _material(
        name=payload,
        brand=payload,
        model=payload,
        technical_specs={"xss": payload, "nested": {"bad": payload}},
    )
    user = _role_user(TECHNICIAN, "mat-xss")
    _login(app_client, user)
    list_content = app_client.get("/catalog/materials/", {"q": "alert"}).content.decode()
    detail_content = app_client.get(
        reverse("catalog:material-detail", args=[material.pk])
    ).content.decode()
    for content in (list_content, detail_content):
        assert payload not in content
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------


def test_get_list_creates_no_audit_event(app_client):
    _material(name="AUDIT-LIST")
    user = _role_user(TECHNICIAN, "mat-audit-list")
    _login(app_client, user)
    before = AuditEvent.objects.count()
    response = app_client.get("/catalog/materials/")
    assert response.status_code == 200
    assert AuditEvent.objects.count() == before


def test_get_detail_creates_no_audit_event(app_client):
    material = _material(name="AUDIT-DETAIL")
    user = _role_user(TECHNICIAN, "mat-audit-detail")
    _login(app_client, user)
    before = AuditEvent.objects.count()
    response = app_client.get(reverse("catalog:material-detail", args=[material.pk]))
    assert response.status_code == 200
    assert AuditEvent.objects.count() == before


def test_post_to_list_unsupported(app_client):
    user = _role_user(TECHNICIAN, "mat-post-list")
    _login(app_client, user)
    before = Material.objects.count()
    response = app_client.post("/catalog/materials/", {"name": "Hack"})
    assert response.status_code == 405
    assert Material.objects.count() == before


def test_post_to_detail_unsupported(app_client):
    material = _material(name="POST-DETAIL")
    user = _role_user(TECHNICIAN, "mat-post-detail")
    _login(app_client, user)
    before_updated = material.updated_at
    response = app_client.post(
        reverse("catalog:material-detail", args=[material.pk]),
        {"name": "Hacked"},
    )
    assert response.status_code == 405
    material.refresh_from_db()
    assert material.updated_at == before_updated


def test_list_has_no_create_or_edit_buttons(app_client):
    _material(name="READONLY-BUTTONS")
    user = _role_user(ADMIN_MANAGER, "mat-readonly-buttons")
    _login(app_client, user)
    content = app_client.get("/catalog/materials/").content.decode()
    assert "Kaydet" not in content
    assert "Düzenle" not in content
    assert "Pasifleştir" not in content
    assert "Aktifleştir" not in content
    assert "Yeni malzeme" not in content


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def test_navigation_visible_with_view_permission(app_client):
    user = _role_user(TECHNICIAN, "nav-mat")
    _login(app_client, user)
    content = app_client.get("/").content.decode()
    assert ">Malzemeler</a>" in content
    assert 'href="/catalog/materials/"' in content


def test_navigation_hidden_without_view_permission(app_client):
    user = _create_ordinary_user("nav-no-mat")
    _login(app_client, user)
    content = app_client.get("/").content.decode()
    assert ">Malzemeler</a>" not in content
    assert 'href="/catalog/materials/"' not in content


def test_category_nav_independent_from_material_permission(app_client):
    user = _create_ordinary_user("nav-mat-only")
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    content_type = ContentType.objects.get(app_label="catalog", model="material")
    perm = Permission.objects.get(content_type=content_type, codename="view_material")
    user.user_permissions.add(perm)
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    content = app_client.get("/").content.decode()
    assert ">Malzemeler</a>" in content
    assert ">Kategoriler</a>" not in content
    assert ">Ölçü Birimleri</a>" not in content
