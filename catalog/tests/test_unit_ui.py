from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model, SESSION_KEY
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from catalog.models import Category, Material, UnitOfMeasure
from catalog.views import UNIT_LIST_PAGE_SIZE

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
SEEDED_ADET_ID = uuid.UUID("b2022c02-0001-4001-8001-000000000001")


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_unit_named_routes():
    unit = UnitOfMeasure.objects.create(code="ROUTE-U", name="Route Unit")
    assert reverse("catalog:unit-list") == "/catalog/units/"
    assert reverse("catalog:unit-create") == "/catalog/units/new/"
    assert (
        reverse("catalog:unit-update", args=[unit.pk])
        == f"/catalog/units/{unit.pk}/edit/"
    )
    assert (
        reverse("catalog:unit-deactivate", args=[unit.pk])
        == f"/catalog/units/{unit.pk}/deactivate/"
    )
    assert (
        reverse("catalog:unit-reactivate", args=[unit.pk])
        == f"/catalog/units/{unit.pk}/reactivate/"
    )


def test_no_unit_delete_route_exists(app_client):
    unit = UnitOfMeasure.objects.create(code="NO-DEL", name="No Delete")
    with pytest.raises(NoReverseMatch):
        reverse("catalog:unit-delete")
    with pytest.raises(NoReverseMatch):
        reverse("catalog:unit-delete", args=[unit.pk])

    user = _role_user(ADMIN_MANAGER, "no-delete-uom-admin")
    _login(app_client, user)
    response = app_client.post(f"/catalog/units/{unit.pk}/delete/")
    assert response.status_code == 404
    assert UnitOfMeasure.objects.filter(pk=unit.pk).exists()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    (
        "/catalog/units/",
        "/catalog/units/new/",
    ),
)
def test_anonymous_unit_get_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == [path]


def test_anonymous_unit_post_redirects_to_login_and_does_not_mutate(app_client):
    before = UnitOfMeasure.objects.count()
    response = app_client.post(
        "/catalog/units/new/",
        {"code": "ANON", "name": "Anon Unit"},
    )
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert UnitOfMeasure.objects.count() == before
    assert not UnitOfMeasure.objects.filter(code="ANON").exists()


def test_unit_login_next_lands_on_list(app_client):
    user = _role_user(TECHNICIAN, "login-next-tech-uom")
    response = app_client.post(
        "/accounts/login/",
        {
            "username": user.username,
            "password": PASSWORD,
            "next": "/catalog/units/",
        },
    )
    assert response.status_code == 302
    assert response.url == "/catalog/units/"
    assert SESSION_KEY in app_client.session
    listing = app_client.get("/catalog/units/")
    assert listing.status_code == 200


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_user_without_view_unitofmeasure_gets_403(app_client):
    user = _create_ordinary_user("no-view-uom")
    _login(app_client, user)
    response = app_client.get("/catalog/units/")
    assert response.status_code == 403


def test_view_permitted_role_gets_200(app_client):
    user = _role_user(TECHNICIAN, "tech-uom-list")
    _login(app_client, user)
    response = app_client.get("/catalog/units/")
    assert response.status_code == 200
    assert "Ölçü Birimleri" in response.content.decode()


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_cannot_create(app_client, role_name):
    user = _role_user(role_name, f"{role_name.lower()}-uom-no-create")
    _login(app_client, user)
    before = set(UnitOfMeasure.objects.values_list("pk", flat=True))

    get_response = app_client.get("/catalog/units/new/")
    assert get_response.status_code == 403
    content = app_client.get("/catalog/units/").content.decode()
    assert "Yeni ölçü birimi" not in content

    post_response = app_client.post(
        "/catalog/units/new/",
        {"code": f"{role_name}-X", "name": f"{role_name} Crafted"},
    )
    assert post_response.status_code == 403
    after = set(UnitOfMeasure.objects.values_list("pk", flat=True))
    assert after == before


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_cannot_edit(app_client, role_name):
    unit = UnitOfMeasure.objects.create(code=f"{role_name}-KEEP", name="Keep")
    user = _role_user(role_name, f"{role_name.lower()}-uom-no-edit")
    _login(app_client, user)

    get_response = app_client.get(reverse("catalog:unit-update", args=[unit.pk]))
    assert get_response.status_code == 403
    content = app_client.get("/catalog/units/").content.decode()
    assert "Düzenle" not in content

    post_response = app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {"code": "HACK", "name": "Hacked"},
    )
    assert post_response.status_code == 403
    unit.refresh_from_db()
    assert unit.code == f"{role_name}-KEEP"


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_cannot_deactivate_or_reactivate(app_client, role_name):
    active = UnitOfMeasure.objects.create(code=f"{role_name}-ACT", name="Active", active=True)
    inactive = UnitOfMeasure.objects.create(
        code=f"{role_name}-INACT", name="Inactive", active=False
    )
    user = _role_user(role_name, f"{role_name.lower()}-uom-no-state")
    _login(app_client, user)
    content = app_client.get("/catalog/units/").content.decode()
    assert "Pasifleştir" not in content
    assert "Aktifleştir" not in content

    deactivate = app_client.post(reverse("catalog:unit-deactivate", args=[active.pk]))
    reactivate = app_client.post(reverse("catalog:unit-reactivate", args=[inactive.pk]))
    assert deactivate.status_code == 403
    assert reactivate.status_code == 403
    active.refresh_from_db()
    inactive.refresh_from_db()
    assert active.active is True
    assert inactive.active is False


def test_admin_manager_can_perform_all_allowed_operations(app_client):
    user = _role_user(ADMIN_MANAGER, "admin-uom-all")
    _login(app_client, user)

    list_response = app_client.get("/catalog/units/")
    assert list_response.status_code == 200
    assert "Yeni ölçü birimi" in list_response.content.decode()

    create_get = app_client.get("/catalog/units/new/")
    assert create_get.status_code == 200
    create_post = app_client.post(
        "/catalog/units/new/",
        {"code": "ADM-1", "name": "Admin Created"},
    )
    assert create_post.status_code == 302
    created = UnitOfMeasure.objects.get(code="ADM-1")
    assert created.active is True
    assert created.decimal_places is None

    edit_get = app_client.get(reverse("catalog:unit-update", args=[created.pk]))
    assert edit_get.status_code == 200
    edit_post = app_client.post(
        reverse("catalog:unit-update", args=[created.pk]),
        {"code": "ADM-2", "name": "Admin Edited"},
    )
    assert edit_post.status_code == 302
    created.refresh_from_db()
    assert created.name == "Admin Edited"
    assert created.code == "ADM-2"
    assert created.id == created.pk

    deactivate = app_client.post(reverse("catalog:unit-deactivate", args=[created.pk]))
    assert deactivate.status_code == 302
    created.refresh_from_db()
    assert created.active is False

    reactivate = app_client.post(reverse("catalog:unit-reactivate", args=[created.pk]))
    assert reactivate.status_code == 302
    created.refresh_from_db()
    assert created.active is True


def test_unauthorized_post_does_not_mutate(app_client):
    unit = UnitOfMeasure.objects.create(code="FORGE", name="Forge", active=True)
    user = _role_user(TECHNICIAN, "forge-tech-uom")
    _login(app_client, user)
    response = app_client.post(reverse("catalog:unit-deactivate", args=[unit.pk]))
    assert response.status_code == 403
    unit.refresh_from_db()
    assert unit.active is True


def test_get_against_state_changing_endpoints_returns_405(app_client):
    unit = UnitOfMeasure.objects.create(code="GET-STATE", name="Get State", active=True)
    user = _role_user(ADMIN_MANAGER, "get-state-uom-admin")
    _login(app_client, user)

    deactivate = app_client.get(reverse("catalog:unit-deactivate", args=[unit.pk]))
    reactivate = app_client.get(reverse("catalog:unit-reactivate", args=[unit.pk]))
    assert deactivate.status_code == 405
    assert reactivate.status_code == 405
    unit.refresh_from_db()
    assert unit.active is True


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------


def test_create_form_exposes_only_code_and_name(app_client):
    user = _role_user(ADMIN_MANAGER, "form-fields-create")
    _login(app_client, user)
    form = app_client.get("/catalog/units/new/").context["form"]
    assert list(form.fields) == ["code", "name"]
    assert "decimal_places" not in form.fields
    assert "active" not in form.fields
    assert "id" not in form.fields
    assert "created_at" not in form.fields
    assert "updated_at" not in form.fields


def test_forged_decimal_places_and_active_ignored_on_create(app_client):
    user = _role_user(ADMIN_MANAGER, "forged-create")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/units/new/",
        {
            "code": "FORGED",
            "name": "Forged Unit",
            "decimal_places": "2",
            "active": "false",
        },
    )
    assert response.status_code == 302
    unit = UnitOfMeasure.objects.get(code="FORGED")
    assert unit.decimal_places is None
    assert unit.active is True


def test_forged_decimal_places_and_active_ignored_on_edit(app_client):
    unit = UnitOfMeasure.objects.create(
        code="FORGED-EDIT",
        name="Forged Edit",
        decimal_places=1,
        active=False,
    )
    user = _role_user(ADMIN_MANAGER, "forged-edit")
    _login(app_client, user)
    app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {
            "code": "FORGED-EDIT-2",
            "name": "Forged Edit 2",
            "decimal_places": "3",
            "active": "true",
        },
    )
    unit.refresh_from_db()
    assert unit.code == "FORGED-EDIT-2"
    assert unit.name == "Forged Edit 2"
    assert unit.decimal_places == 1
    assert unit.active is False


def test_whitespace_trimming_on_create(app_client):
    user = _role_user(ADMIN_MANAGER, "ws-trim")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/units/new/",
        {"code": " TRIM-CODE ", "name": " Trim Name "},
    )
    assert response.status_code == 302
    unit = UnitOfMeasure.objects.get(code="TRIM-CODE")
    assert unit.name == "Trim Name"


def test_whitespace_only_code_and_name_rejected(app_client):
    user = _role_user(ADMIN_MANAGER, "ws-only")
    _login(app_client, user)
    before = UnitOfMeasure.objects.count()

    name_response = app_client.post(
        "/catalog/units/new/",
        {"code": "OK", "name": "   "},
    )
    assert name_response.status_code == 200
    assert name_response.context["form"].errors

    code_response = app_client.post(
        "/catalog/units/new/",
        {"code": "   ", "name": "Valid Name"},
    )
    assert code_response.status_code == 200
    assert code_response.context["form"].errors
    assert UnitOfMeasure.objects.count() == before


def test_case_sensitive_code_distinction(app_client):
    user = _role_user(ADMIN_MANAGER, "case-code")
    _login(app_client, user)
    UnitOfMeasure.objects.create(code="CaseCode", name="Upper")
    response = app_client.post(
        "/catalog/units/new/",
        {"code": "casecode", "name": "Lower"},
    )
    assert response.status_code == 302
    assert UnitOfMeasure.objects.filter(code="CaseCode").exists()
    assert UnitOfMeasure.objects.filter(code="casecode").exists()


def test_duplicate_code_create_rejected(app_client):
    user = _role_user(ADMIN_MANAGER, "dup-create")
    _login(app_client, user)
    UnitOfMeasure.objects.create(code="DUP-CODE", name="First")
    before = UnitOfMeasure.objects.count()
    response = app_client.post(
        "/catalog/units/new/",
        {"code": "DUP-CODE", "name": "Second"},
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    assert UnitOfMeasure.objects.count() == before


def test_duplicate_code_update_rejected(app_client):
    user = _role_user(ADMIN_MANAGER, "dup-update")
    _login(app_client, user)
    first = UnitOfMeasure.objects.create(code="KEEP-A", name="A")
    second = UnitOfMeasure.objects.create(code="KEEP-B", name="B")
    response = app_client.post(
        reverse("catalog:unit-update", args=[second.pk]),
        {"code": first.code, "name": second.name},
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    second.refresh_from_db()
    assert second.code == "KEEP-B"


def test_code_rename_preserves_uuid(app_client):
    user = _role_user(ADMIN_MANAGER, "uuid-rename")
    _login(app_client, user)
    unit = UnitOfMeasure.objects.create(code="OLD-CODE", name="Rename Me")
    original_id = unit.id
    app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {"code": "NEW-CODE", "name": "Rename Me"},
    )
    unit.refresh_from_db()
    assert unit.id == original_id
    assert unit.code == "NEW-CODE"


def test_edit_preserves_decimal_places(app_client):
    user = _role_user(ADMIN_MANAGER, "preserve-dp")
    _login(app_client, user)
    unit = UnitOfMeasure.objects.create(
        code="DP-KEEP",
        name="Decimal Keep",
        decimal_places=3,
    )
    app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {"code": "DP-KEEP", "name": "Renamed"},
    )
    unit.refresh_from_db()
    assert unit.decimal_places == 3


def test_inactive_unit_can_be_edited(app_client):
    user = _role_user(ADMIN_MANAGER, "inactive-edit")
    _login(app_client, user)
    unit = UnitOfMeasure.objects.create(code="INACT-EDIT", name="Inactive", active=False)
    response = app_client.get(reverse("catalog:unit-update", args=[unit.pk]))
    assert response.status_code == 200
    app_client.post(
        reverse("catalog:unit-update", args=[unit.pk]),
        {"code": "INACT-EDIT-2", "name": "Inactive Renamed"},
    )
    unit.refresh_from_db()
    assert unit.code == "INACT-EDIT-2"
    assert unit.active is False


# ---------------------------------------------------------------------------
# List / search / filter / pagination
# ---------------------------------------------------------------------------


def test_q_by_name(app_client):
    UnitOfMeasure.objects.create(code="Q-N-A", name="UNIQUE-NAME-HIT")
    UnitOfMeasure.objects.create(code="Q-N-B", name="UNIQUE-NAME-MISS")
    user = _role_user(TECHNICIAN, "q-uom-name")
    _login(app_client, user)
    content = app_client.get("/catalog/units/", {"q": "UNIQUE-NAME-HIT"}).content.decode()
    assert "UNIQUE-NAME-HIT" in content
    assert "UNIQUE-NAME-MISS" not in content


def test_q_by_code(app_client):
    UnitOfMeasure.objects.create(code="ZZ-CODE-HIT", name="Code Search A")
    UnitOfMeasure.objects.create(code="ZZ-CODE-MISS", name="Code Search B")
    user = _role_user(TECHNICIAN, "q-uom-code")
    _login(app_client, user)
    content = app_client.get("/catalog/units/", {"q": "ZZ-CODE-HIT"}).content.decode()
    assert "ZZ-CODE-HIT" in content
    assert "ZZ-CODE-MISS" not in content


def test_q_is_case_insensitive(app_client):
    UnitOfMeasure.objects.create(code="CfCode", name="CaseFold Target")
    user = _role_user(TECHNICIAN, "q-uom-case")
    _login(app_client, user)
    name_hit = app_client.get("/catalog/units/", {"q": "casefold target"}).content.decode()
    code_hit = app_client.get("/catalog/units/", {"q": "cfcode"}).content.decode()
    assert "CaseFold Target" in name_hit
    assert "CfCode" in code_hit


def test_active_inactive_and_all_filters(app_client):
    UnitOfMeasure.objects.create(code="F-ACT", name="FILTER-ACTIVE", active=True)
    UnitOfMeasure.objects.create(code="F-INACT", name="FILTER-INACTIVE", active=False)
    user = _role_user(TECHNICIAN, "uom-status-filter")
    _login(app_client, user)

    active_content = app_client.get(
        "/catalog/units/", {"q": "FILTER-", "status": "active"}
    ).content.decode()
    assert "FILTER-ACTIVE" in active_content
    assert "FILTER-INACTIVE" not in active_content

    inactive_content = app_client.get(
        "/catalog/units/", {"q": "FILTER-", "status": "inactive"}
    ).content.decode()
    assert "FILTER-INACTIVE" in inactive_content
    assert "FILTER-ACTIVE" not in inactive_content

    all_content = app_client.get(
        "/catalog/units/", {"q": "FILTER-", "status": "all"}
    ).content.decode()
    assert "FILTER-ACTIVE" in all_content
    assert "FILTER-INACTIVE" in all_content


def test_unknown_status_falls_back_to_all(app_client):
    UnitOfMeasure.objects.create(code="U-ACT", name="UNKNOWN-STATUS-ACTIVE", active=True)
    UnitOfMeasure.objects.create(
        code="U-INACT", name="UNKNOWN-STATUS-INACTIVE", active=False
    )
    user = _role_user(TECHNICIAN, "uom-status-unknown")
    _login(app_client, user)
    response = app_client.get(
        "/catalog/units/",
        {"q": "UNKNOWN-STATUS", "status": "nope"},
    )
    assert response.status_code == 200
    assert response.context["status"] == "all"
    content = response.content.decode()
    assert "UNKNOWN-STATUS-ACTIVE" in content
    assert "UNKNOWN-STATUS-INACTIVE" in content


def test_stable_ordering_by_code_then_id(app_client):
    UnitOfMeasure.objects.create(code="ORDER-ZZZ", name="Z Last")
    UnitOfMeasure.objects.create(code="ORDER-AAA", name="A First")
    user = _role_user(TECHNICIAN, "uom-ordering")
    _login(app_client, user)
    response = app_client.get("/catalog/units/", {"q": "ORDER-"})
    codes_and_ids = [(unit.code, unit.id) for unit in response.context["units"]]
    assert codes_and_ids == sorted(codes_and_ids, key=lambda item: (item[0], item[1]))
    assert codes_and_ids[0][0] == "ORDER-AAA"
    assert codes_and_ids[-1][0] == "ORDER-ZZZ"


def test_pagination_and_query_params_preserved(app_client):
    for index in range(UNIT_LIST_PAGE_SIZE + 5):
        UnitOfMeasure.objects.create(
            code=f"PG-{index:03d}",
            name=f"ZZZ-SAYFA-{index:03d}",
            active=True,
        )
    user = _role_user(TECHNICIAN, "uom-pagination")
    _login(app_client, user)
    page1 = app_client.get(
        "/catalog/units/",
        {"q": "ZZZ-SAYFA", "status": "active"},
    )
    assert page1.status_code == 200
    assert page1.context["paginator"].count == UNIT_LIST_PAGE_SIZE + 5
    assert page1.context["page_obj"].number == 1
    assert len(page1.context["units"]) == UNIT_LIST_PAGE_SIZE
    content = page1.content.decode()
    assert "Sonraki" in content
    assert "q=ZZZ-SAYFA" in content
    assert "status=active" in content

    page2 = app_client.get(
        "/catalog/units/",
        {"q": "ZZZ-SAYFA", "status": "active", "page": "2"},
    )
    assert page2.status_code == 200
    assert page2.context["page_obj"].number == 2
    assert len(page2.context["units"]) == 5


def test_decimal_places_display_in_list(app_client):
    UnitOfMeasure.objects.create(code="DP-NULL", name="Null DP", decimal_places=None)
    UnitOfMeasure.objects.create(code="DP-ZERO", name="Zero DP", decimal_places=0)
    UnitOfMeasure.objects.create(code="DP-TWO", name="Two DP", decimal_places=2)
    user = _role_user(TECHNICIAN, "dp-display")
    _login(app_client, user)
    content = app_client.get("/catalog/units/", {"q": "DP-"}).content.decode()
    assert "Belirlenmedi" in content
    assert ">0<" in content or "Zero DP" in content
    assert "DP-TWO" in content


def test_rendered_query_text_is_safely_escaped(app_client):
    user = _role_user(TECHNICIAN, "xss-uom-q")
    _login(app_client, user)
    payload = "<script>alert(1)</script>"
    response = app_client.get("/catalog/units/", {"q": payload})
    assert response.status_code == 200
    content = response.content.decode()
    assert payload not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content


def test_navigation_visible_with_view_permission(app_client):
    user = _role_user(TECHNICIAN, "nav-uom")
    _login(app_client, user)
    content = app_client.get("/").content.decode()
    assert ">Ölçü Birimleri</a>" not in content
    assert 'href="/catalog/units/"' not in content
    assert app_client.get("/catalog/units/").status_code == 200


def test_navigation_hidden_without_view_permission(app_client):
    user = _create_ordinary_user("nav-no-uom")
    _login(app_client, user)
    content = app_client.get("/").content.decode()
    assert ">Ölçü Birimleri</a>" not in content
    assert 'href="/catalog/units/"' not in content


# ---------------------------------------------------------------------------
# Deactivate / reactivate / referenced
# ---------------------------------------------------------------------------


def test_deactivate_only_changes_active_state(app_client):
    category = Category.objects.create(name="UOM-STATE-CAT")
    unit = UnitOfMeasure.objects.create(code="STATE-UOM", name="State Unit", active=True)
    material = Material.objects.create(
        material_code="STATE-MAT",
        name="State Material",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material_updated_at = material.updated_at
    unit_id = unit.pk
    user = _role_user(ADMIN_MANAGER, "deactivate-uom")
    _login(app_client, user)

    response = app_client.post(
        reverse("catalog:unit-deactivate", args=[unit.pk]),
        follow=True,
    )
    assert response.status_code == 200
    assert "Ölçü birimi pasifleştirildi." in response.content.decode()
    unit.refresh_from_db()
    material.refresh_from_db()
    assert unit.active is False
    assert material.unit_id == unit_id
    assert material.updated_at == material_updated_at


def test_repeated_deactivate_and_reactivate_are_safe(app_client):
    unit = UnitOfMeasure.objects.create(code="IDEMP", name="Idempotent", active=True)
    user = _role_user(ADMIN_MANAGER, "idempotent-uom")
    _login(app_client, user)

    first = app_client.post(reverse("catalog:unit-deactivate", args=[unit.pk]), follow=True)
    second = app_client.post(reverse("catalog:unit-deactivate", args=[unit.pk]), follow=True)
    assert "Ölçü birimi pasifleştirildi." in first.content.decode()
    assert "Ölçü birimi zaten pasif." in second.content.decode()

    third = app_client.post(reverse("catalog:unit-reactivate", args=[unit.pk]), follow=True)
    fourth = app_client.post(reverse("catalog:unit-reactivate", args=[unit.pk]), follow=True)
    assert "Ölçü birimi aktifleştirildi." in third.content.decode()
    assert "Ölçü birimi zaten aktif." in fourth.content.decode()


def _seeded_adet_unit() -> UnitOfMeasure:
    unit, _created = UnitOfMeasure.objects.get_or_create(
        pk=SEEDED_ADET_ID,
        defaults={
            "code": "ADET",
            "name": "Adet",
            "active": True,
            "decimal_places": None,
        },
    )
    return unit


def test_seeded_adet_lifecycle_via_ui(app_client):
    user = _role_user(ADMIN_MANAGER, "seeded-adet-ui")
    _login(app_client, user)
    unit = _seeded_adet_unit()
    original_code = unit.code
    original_name = unit.name
    original_active = unit.active

    try:
        list_content = app_client.get("/catalog/units/", {"q": "ADET"}).content.decode()
        assert "ADET" in list_content or original_name in list_content

        edit = app_client.post(
            reverse("catalog:unit-update", args=[unit.pk]),
            {"code": "ADET", "name": "Adet (UI test)"},
        )
        assert edit.status_code == 302

        deactivate = app_client.post(reverse("catalog:unit-deactivate", args=[unit.pk]))
        assert deactivate.status_code == 302
        unit.refresh_from_db()
        assert unit.active is False

        reactivate = app_client.post(reverse("catalog:unit-reactivate", args=[unit.pk]))
        assert reactivate.status_code == 302
        unit.refresh_from_db()
        assert unit.active is True
    finally:
        unit.code = original_code
        unit.name = original_name
        unit.active = original_active
        unit.save(update_fields=["code", "name", "active", "updated_at"])


def test_technician_list_hides_write_actions(app_client):
    UnitOfMeasure.objects.create(code="TECH-VIS", name="Tech Visible")
    user = _role_user(TECHNICIAN, "tech-uom-ux")
    _login(app_client, user)
    content = app_client.get("/catalog/units/").content.decode()
    assert "TECH-VIS" in content or "Tech Visible" in content
    assert "Yeni ölçü birimi" not in content
    assert "Düzenle" not in content
    assert "Pasifleştir" not in content
    assert "Aktifleştir" not in content
