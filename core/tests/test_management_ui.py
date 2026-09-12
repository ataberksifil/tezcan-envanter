from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.urls import reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, UnitOfMeasure

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
FUTURE_CARD_LABELS = ("Teknik Alan Tanımları",)


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _create_ordinary_user(username):
    user_model = get_user_model()
    user = user_model.objects.create_user(username=username, password=PASSWORD)
    assert not user.is_superuser
    assert not user.is_staff
    return user


def _refresh_user_permissions(user):
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _catalog_permission(codename):
    content_type = ContentType.objects.get(app_label="catalog", model="material")
    if codename.endswith("category"):
        content_type = ContentType.objects.get(app_label="catalog", model="category")
    elif codename.endswith("unitofmeasure"):
        content_type = ContentType.objects.get(
            app_label="catalog", model="unitofmeasure"
        )
    elif codename.endswith("location"):
        content_type = ContentType.objects.get(app_label="locations", model="location")
    elif codename.endswith("employee"):
        content_type = ContentType.objects.get(app_label="accounts", model="employee")
    elif codename.endswith("productionline"):
        content_type = ContentType.objects.get(
            app_label="inventory", model="productionline"
        )
    return Permission.objects.get(content_type=content_type, codename=codename)


def _grant_permissions(user, *codenames):
    user.user_permissions.add(*[_catalog_permission(codename) for codename in codenames])
    return _refresh_user_permissions(user)


def _login(app_client, user):
    app_client.force_login(user)
    return user


def _management_url():
    return reverse("management")


# ---------------------------------------------------------------------------
# Routing / methods
# ---------------------------------------------------------------------------


def test_management_named_route():
    assert _management_url() == "/management/"


def test_management_get_works_when_authorized(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-get"),
        "view_category",
        "change_category",
    )
    _login(app_client, user)
    response = app_client.get(_management_url())
    assert response.status_code == 200
    content = response.content.decode()
    assert "Yönetim" in content
    assert "Kategoriler" in content


def test_management_post_returns_405(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-post"),
        "view_category",
        "change_category",
    )
    _login(app_client, user)
    before_categories = Category.objects.count()
    before_units = UnitOfMeasure.objects.count()
    before_materials = Material.objects.count()
    before_events = AuditEvent.objects.count()

    response = app_client.post(_management_url())
    assert response.status_code == 405
    assert Category.objects.count() == before_categories
    assert UnitOfMeasure.objects.count() == before_units
    assert Material.objects.count() == before_materials
    assert AuditEvent.objects.count() == before_events


def test_management_put_returns_405_without_mutation(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-put"),
        "view_material",
        "change_material",
    )
    _login(app_client, user)
    before_events = AuditEvent.objects.count()
    response = app_client.put(_management_url())
    assert response.status_code == 405
    assert AuditEvent.objects.count() == before_events


# ---------------------------------------------------------------------------
# Anonymous
# ---------------------------------------------------------------------------


def test_management_anonymous_redirects_to_login_with_next(app_client):
    response = app_client.get(_management_url())
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == ["/management/"]


# ---------------------------------------------------------------------------
# View-only users
# ---------------------------------------------------------------------------


def test_view_only_user_has_no_management_nav_or_access(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-view-only"),
        "view_category",
        "view_unitofmeasure",
        "view_material",
    )
    _login(app_client, user)
    home = app_client.get("/")
    content = home.content.decode()
    assert ">Yönetim</a>" not in content
    assert 'href="/management/"' not in content
    assert ">Malzemeler</a>" in content
    assert 'href="/catalog/materials/"' in content
    assert ">Kategoriler</a>" not in content
    assert ">Ölçü Birimleri</a>" not in content

    denied = app_client.get(_management_url())
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# Actionable direct permission (no group)
# ---------------------------------------------------------------------------


def test_category_writer_sees_management_and_category_card_only(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-category-writer"),
        "view_category",
        "change_category",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" in home
    assert 'href="/management/"' in home

    page = app_client.get(_management_url())
    assert page.status_code == 200
    content = page.content.decode()
    assert reverse("catalog:category-list") in content
    assert reverse("catalog:unit-list") not in content
    assert reverse("catalog:material-list") not in content


def test_add_category_without_view_does_not_grant_management(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-add-only"),
        "add_category",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" not in home
    assert app_client.get(_management_url()).status_code == 403


# ---------------------------------------------------------------------------
# Mixed customized permissions
# ---------------------------------------------------------------------------


def test_mixed_surface_permissions_show_matching_cards_only(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-mixed"),
        "view_category",
        "add_category",
        "view_material",
        "change_material",
    )
    _login(app_client, user)
    content = app_client.get(_management_url()).content.decode()
    assert reverse("catalog:category-list") in content
    assert reverse("catalog:material-list") in content
    assert reverse("catalog:unit-list") not in content


# ---------------------------------------------------------------------------
# Default ADMIN_MANAGER bootstrap
# ---------------------------------------------------------------------------


def test_admin_manager_after_setup_roles_has_full_management_hub(app_client):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(f"mgmt-admin-{uuid.uuid4().hex[:8]}")
    user.groups.add(Group.objects.get(name=ADMIN_MANAGER))
    user = _refresh_user_permissions(user)
    _login(app_client, user)

    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" in home
    assert ">Kategoriler</a>" not in home
    assert ">Ölçü Birimleri</a>" not in home

    page = app_client.get(_management_url())
    assert page.status_code == 200
    content = page.content.decode()
    assert reverse("catalog:category-list") in content
    assert reverse("catalog:unit-list") in content
    assert reverse("catalog:material-list") in content
    assert reverse("inventory:production-line-list") in content


def test_admin_manager_group_name_without_permissions_grants_nothing(app_client):
    group, _created = Group.objects.get_or_create(name=ADMIN_MANAGER)
    user = _create_ordinary_user("mgmt-empty-admin-group")
    user.groups.add(group)
    user = _refresh_user_permissions(user)
    _login(app_client, user)

    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" not in home
    assert app_client.get(_management_url()).status_code == 403


# ---------------------------------------------------------------------------
# Links / future features / read-only audit
# ---------------------------------------------------------------------------


def test_visible_cards_link_to_existing_list_routes(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-links"),
        "view_category",
        "change_category",
        "view_unitofmeasure",
        "change_unitofmeasure",
        "view_material",
        "change_material",
    )
    _login(app_client, user)
    content = app_client.get(_management_url()).content.decode()
    assert f'href="{reverse("catalog:category-list")}"' in content
    assert f'href="{reverse("catalog:unit-list")}"' in content
    assert f'href="{reverse("catalog:material-list")}"' in content


@pytest.mark.parametrize("label", FUTURE_CARD_LABELS)
def test_management_page_has_no_future_feature_cards(app_client, label):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(f"mgmt-future-{uuid.uuid4().hex[:8]}")
    user.groups.add(Group.objects.get(name=ADMIN_MANAGER))
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    content = app_client.get(_management_url()).content.decode()
    assert label not in content


def test_management_get_creates_no_audit_event_and_no_catalog_mutation(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-readonly"),
        "view_category",
        "change_category",
    )
    _login(app_client, user)
    before_categories = Category.objects.count()
    before_units = UnitOfMeasure.objects.count()
    before_materials = Material.objects.count()
    before_events = AuditEvent.objects.count()

    response = app_client.get(_management_url())
    assert response.status_code == 200
    assert Category.objects.count() == before_categories
    assert UnitOfMeasure.objects.count() == before_units
    assert Material.objects.count() == before_materials
    assert AuditEvent.objects.count() == before_events


# ---------------------------------------------------------------------------
# Navigation regression helpers
# ---------------------------------------------------------------------------


def test_view_location_only_user_has_no_management_hub_or_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-loc-view-only"),
        "view_location",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" not in home
    assert app_client.get(_management_url()).status_code == 403
    assert app_client.get("/locations/").status_code == 200


def test_location_writer_sees_management_and_location_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-loc-writer"),
        "view_location",
        "add_location",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" in home
    page = app_client.get(_management_url())
    assert page.status_code == 200
    content = page.content.decode()
    assert reverse("locations:location-list") in content
    assert reverse("catalog:category-list") not in content


def test_location_change_without_add_shows_management_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-loc-change"),
        "view_location",
        "change_location",
    )
    _login(app_client, user)
    content = app_client.get(_management_url()).content.decode()
    assert reverse("locations:location-list") in content


def test_add_location_without_view_does_not_grant_management(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-loc-add-only"),
        "add_location",
    )
    _login(app_client, user)
    assert ">Yönetim</a>" not in app_client.get("/").content.decode()
    assert app_client.get(_management_url()).status_code == 403


def test_view_employee_only_user_has_no_management_hub_or_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-emp-view-only"),
        "view_employee",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" not in home
    assert app_client.get(_management_url()).status_code == 403
    assert app_client.get("/management/employees/").status_code == 200


def test_employee_writer_sees_management_and_employee_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-emp-writer"),
        "view_employee",
        "add_employee",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" in home
    page = app_client.get(_management_url())
    assert page.status_code == 200
    content = page.content.decode()
    assert reverse("accounts:employee-list") in content
    assert reverse("catalog:category-list") not in content


def test_employee_change_without_add_shows_management_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-emp-change"),
        "view_employee",
        "change_employee",
    )
    _login(app_client, user)
    content = app_client.get(_management_url()).content.decode()
    assert reverse("accounts:employee-list") in content


def test_add_employee_without_view_does_not_grant_management(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-emp-add-only"),
        "add_employee",
    )
    _login(app_client, user)
    assert ">Yönetim</a>" not in app_client.get("/").content.decode()
    assert app_client.get(_management_url()).status_code == 403


def test_view_productionline_only_user_has_no_management_hub_or_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-pl-view-only"),
        "view_productionline",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" not in home
    assert app_client.get(_management_url()).status_code == 403
    assert app_client.get("/management/production-lines/").status_code == 200


def test_production_line_writer_sees_management_and_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-pl-writer"),
        "view_productionline",
        "add_productionline",
    )
    _login(app_client, user)
    home = app_client.get("/").content.decode()
    assert ">Yönetim</a>" in home
    page = app_client.get(_management_url())
    assert page.status_code == 200
    content = page.content.decode()
    assert reverse("inventory:production-line-list") in content
    assert reverse("catalog:category-list") not in content


def test_production_line_change_without_add_shows_management_card(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-pl-change"),
        "view_productionline",
        "change_productionline",
    )
    _login(app_client, user)
    content = app_client.get(_management_url()).content.decode()
    assert reverse("inventory:production-line-list") in content


def test_add_productionline_without_view_does_not_grant_management(app_client):
    user = _grant_permissions(
        _create_ordinary_user("mgmt-pl-add-only"),
        "add_productionline",
    )
    _login(app_client, user)
    assert ">Yönetim</a>" not in app_client.get("/").content.decode()
    assert app_client.get(_management_url()).status_code == 403


def test_manage_access_only_user_still_sees_management_hub(app_client):
    user = _create_ordinary_user("mgmt-access-only")
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="accounts",
            codename="manage_access",
        )
    )
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    page = app_client.get(_management_url())
    assert page.status_code == 200
    content = page.content.decode()
    assert reverse("accounts:role-list") in content
    assert reverse("locations:location-list") not in content


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_templates_do_not_show_management_nav(app_client, role_name):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(f"mgmt-nav-{role_name.lower()}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    _login(app_client, user)

    content = app_client.get("/").content.decode()
    assert ">Malzemeler</a>" in content
    assert ">Kategoriler</a>" not in content
    assert ">Ölçü Birimleri</a>" not in content
    assert ">Yönetim</a>" not in content
