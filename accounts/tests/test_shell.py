from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse

from accounts.roles import (
    ADMIN_MANAGER,
    STOREKEEPER,
    TECHNICIAN,
    user_has_catalog_view_permission,
)

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


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
    return Permission.objects.get(content_type=content_type, codename=codename)


def _grant_catalog_view_permissions(user):
    permissions = [
        _catalog_permission("view_material"),
        _catalog_permission("view_category"),
        _catalog_permission("view_unitofmeasure"),
    ]
    user.user_permissions.add(*permissions)
    return _refresh_user_permissions(user)


def _grant_permissions(user, *codenames):
    user.user_permissions.add(*[_catalog_permission(codename) for codename in codenames])
    return _refresh_user_permissions(user)


def test_helper_false_without_catalog_permissions():
    user = _create_ordinary_user("no-catalog-perm")
    assert user_has_catalog_view_permission(user) is False


def test_helper_true_with_direct_catalog_view_permission():
    user = _create_ordinary_user("direct-catalog-view")
    user.user_permissions.add(_catalog_permission("view_material"))
    user = _refresh_user_permissions(user)
    assert user_has_catalog_view_permission(user) is True


def test_helper_does_not_treat_group_name_as_authorization():
    user = _create_ordinary_user("group-name-only")
    group, _created = Group.objects.get_or_create(name=TECHNICIAN)
    user.groups.add(group)
    user = _refresh_user_permissions(user)
    assert user.groups.filter(name=TECHNICIAN).exists()
    assert user_has_catalog_view_permission(user) is False


def test_unauthenticated_user_has_no_catalog_view_permission(app_client):
    response = app_client.get("/accounts/login/")
    assert user_has_catalog_view_permission(response.wsgi_request.user) is False


def test_catalog_index_route_is_not_kept_for_compatibility():
    with pytest.raises(NoReverseMatch):
        reverse("catalog:index")
    assert reverse("catalog:category-list") == "/catalog/categories/"


def test_view_category_alone_does_not_show_primary_nav_links(app_client):
    user = _create_ordinary_user("nav-category-route")
    user.user_permissions.add(_catalog_permission("view_category"))
    user = _refresh_user_permissions(user)
    app_client.force_login(user)
    response = app_client.get("/")
    content = response.content.decode()
    assert user.has_perm("catalog.view_category") is True
    assert ">Kategoriler</a>" not in content
    assert 'href="/catalog/categories/"' not in content
    assert ">Yönetim</a>" not in content
    assert ">Katalog</a>" not in content
    assert "Ana sayfa" in content
    assert "Stok girişi" not in content
    assert "Stok çıkışı" not in content
    listing = app_client.get("/catalog/categories/")
    assert listing.status_code == 200


def test_user_without_catalog_permission_has_no_catalog_nav(app_client):
    user = _create_ordinary_user("nav-no-perm")
    app_client.force_login(user)
    response = app_client.get("/")
    content = response.content.decode()
    assert user_has_catalog_view_permission(user) is False
    assert ">Kategoriler</a>" not in content
    assert ">Yönetim</a>" not in content
    assert ">Katalog</a>" not in content
    assert 'href="/catalog/categories/"' not in content
    assert user.has_perm("catalog.view_material") is False
    assert user.has_perm("catalog.view_category") is False


def test_view_only_catalog_user_sees_materials_not_management_or_config_nav(app_client):
    user = _grant_catalog_view_permissions(_create_ordinary_user("nav-view-only"))
    app_client.force_login(user)
    content = app_client.get("/").content.decode()
    assert user_has_catalog_view_permission(user) is True
    assert ">Malzemeler</a>" in content
    assert 'href="/catalog/materials/"' in content
    assert ">Kategoriler</a>" not in content
    assert ">Ölçü Birimleri</a>" not in content
    assert ">Yönetim</a>" not in content
    assert app_client.get("/management/").status_code == 403


def test_view_material_alone_does_not_show_category_or_management_nav(app_client):
    user = _create_ordinary_user("nav-material-only")
    user.user_permissions.add(_catalog_permission("view_material"))
    user = _refresh_user_permissions(user)
    app_client.force_login(user)
    content = app_client.get("/").content.decode()
    assert user_has_catalog_view_permission(user) is True
    assert user.has_perm("catalog.view_category") is False
    assert ">Kategoriler</a>" not in content
    assert 'href="/catalog/categories/"' not in content
    assert ">Ölçü Birimleri</a>" not in content
    assert ">Yönetim</a>" not in content
    assert ">Malzemeler</a>" in content
    assert 'href="/catalog/materials/"' in content


def test_configuration_writer_sees_management_nav(app_client):
    user = _grant_permissions(
        _create_ordinary_user("nav-config-writer"),
        "view_category",
        "change_category",
    )
    app_client.force_login(user)
    content = app_client.get("/").content.decode()
    assert ">Yönetim</a>" in content
    assert 'href="/management/"' in content
    assert ">Kategoriler</a>" not in content


def test_hidden_catalog_nav_is_not_the_authorization_control(app_client):
    user = _create_ordinary_user("nav-hidden-not-authz")
    app_client.force_login(user)
    home = app_client.get("/")
    content = home.content.decode()
    assert ">Kategoriler</a>" not in content
    assert 'href="/catalog/categories/"' not in content

    denied = app_client.get("/catalog/categories/")
    assert denied.status_code == 403
    assert user_has_catalog_view_permission(user) is False
    assert user.has_perm("catalog.view_category") is False


@pytest.mark.parametrize(
    ("role_name", "expect_material_nav", "expect_management_nav"),
    (
        (TECHNICIAN, True, False),
        (STOREKEEPER, True, False),
        (ADMIN_MANAGER, True, True),
    ),
)
def test_setup_roles_shell_nav_uses_permissions_not_group_names(
    app_client, role_name, expect_material_nav, expect_management_nav
):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(f"role-nav-{role_name.lower()}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert not user.is_superuser

    app_client.force_login(user)
    response = app_client.get("/")
    content = response.content.decode()
    assert (">Malzemeler</a>" in content) is expect_material_nav
    assert (">Yönetim</a>" in content) is expect_management_nav
    assert ">Kategoriler</a>" not in content
    assert ">Ölçü Birimleri</a>" not in content
    if expect_management_nav:
        assert app_client.get("/management/").status_code == 200
