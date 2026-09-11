from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from locations.models import Location
from locations.views import LOCATION_LIST_PAGE_SIZE

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


def _location_permission(codename):
    return Permission.objects.get(
        content_type__app_label="locations",
        codename=codename,
    )


def _grant_permissions(user, *codenames):
    user.user_permissions.add(*[_location_permission(c) for c in codenames])
    return _refresh_user_permissions(user)


def _login(app_client, user):
    app_client.force_login(user)
    return user


def test_location_named_routes():
    location = Location.objects.create(code="ROUTE", name="Route Loc")
    assert reverse("locations:location-list") == "/locations/"
    assert reverse("locations:location-create") == "/locations/new/"
    assert (
        reverse("locations:location-detail", args=[location.pk])
        == f"/locations/{location.pk}/"
    )
    assert (
        reverse("locations:location-update", args=[location.pk])
        == f"/locations/{location.pk}/edit/"
    )


def test_no_location_delete_route_exists(app_client):
    location = Location.objects.create(code="NO-DEL", name="No Delete")
    with pytest.raises(NoReverseMatch):
        reverse("locations:location-delete")
    user = _grant_permissions(_create_ordinary_user("no-del"), "view_location")
    _login(app_client, user)
    assert app_client.post(f"/locations/{location.pk}/delete/").status_code == 404


@pytest.mark.parametrize(
    "path",
    ("/locations/", "/locations/new/"),
)
def test_anonymous_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [path]


def test_view_location_allows_list_and_detail(app_client):
    location = Location.objects.create(code="VIEW", name="Viewable")
    user = _grant_permissions(_create_ordinary_user("viewer"), "view_location")
    _login(app_client, user)
    assert app_client.get("/locations/").status_code == 200
    assert app_client.get(f"/locations/{location.pk}/").status_code == 200


def test_no_view_permission_returns_403(app_client):
    user = _create_ordinary_user("no-view")
    _login(app_client, user)
    assert app_client.get("/locations/").status_code == 403


def test_add_permission_controls_create(app_client):
    viewer = _grant_permissions(_create_ordinary_user("viewer-only"), "view_location")
    _login(app_client, viewer)
    assert app_client.get("/locations/new/").status_code == 403

    writer = _grant_permissions(
        _create_ordinary_user("creator"),
        "view_location",
        "add_location",
    )
    _login(app_client, writer)
    assert app_client.get("/locations/new/").status_code == 200


def test_change_permission_controls_edit_and_status(app_client):
    location = Location.objects.create(code="CHG", name="Change Me")
    viewer = _grant_permissions(_create_ordinary_user("view-only"), "view_location")
    _login(app_client, viewer)
    assert app_client.get(f"/locations/{location.pk}/edit/").status_code == 403
    assert app_client.post(f"/locations/{location.pk}/deactivate/").status_code == 403

    editor = _grant_permissions(
        _create_ordinary_user("editor"),
        "view_location",
        "change_location",
    )
    _login(app_client, editor)
    assert app_client.get(f"/locations/{location.pk}/edit/").status_code == 200


def test_group_name_alone_does_not_authorize(app_client):
    group, _ = Group.objects.get_or_create(name=ADMIN_MANAGER)
    user = _create_ordinary_user("empty-admin-group")
    user.groups.add(group)
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    assert app_client.get("/locations/").status_code == 403


def test_forged_descendant_parent_rejected(app_client):
    root = Location.objects.create(code="ROOT", name="Root")
    child = Location.objects.create(code="CH", name="Child", parent=root)
    user = _grant_permissions(
        _create_ordinary_user("forger"),
        "view_location",
        "change_location",
    )
    _login(app_client, user)
    response = app_client.post(
        f"/locations/{root.pk}/edit/",
        data={
            "code": root.code,
            "name": root.name,
            "parent": str(child.pk),
            "can_hold_stock": "",
        },
    )
    assert response.status_code == 200
    root.refresh_from_db()
    assert root.parent_id is None


def test_forged_active_post_ignored_on_create(app_client):
    user = _grant_permissions(
        _create_ordinary_user("active-forge"),
        "view_location",
        "add_location",
    )
    _login(app_client, user)
    response = app_client.post(
        "/locations/new/",
        data={
            "code": "NEW-ACT",
            "name": "New Active Forge",
            "parent": "",
            "can_hold_stock": "",
            "active": "false",
        },
    )
    assert response.status_code == 302
    created = Location.objects.get(code="NEW-ACT")
    assert created.active is True


def test_list_search_filter_and_stock_filter(app_client):
    Location.objects.create(code="ALPHA", name="Alpha Shelf")
    Location.objects.create(code="BETA", name="Beta Bin", can_hold_stock=True)
    inactive = Location.objects.create(code="GAMMA", name="Gamma", active=False)
    user = _grant_permissions(_create_ordinary_user("lister"), "view_location")
    _login(app_client, user)

    search = app_client.get("/locations/", {"q": "alpha"}).content.decode()
    assert "ALPHA" in search
    assert "BETA" not in search

    inactive_filter = app_client.get("/locations/", {"status": "inactive"}).content.decode()
    assert "GAMMA" in inactive_filter
    assert "ALPHA" not in inactive_filter

    stock_yes = app_client.get("/locations/", {"stock": "yes"}).content.decode()
    assert "BETA" in stock_yes
    assert "ALPHA" not in stock_yes

    assert inactive.active is False


def test_list_ordering_and_pagination(app_client):
    user = _grant_permissions(_create_ordinary_user("pager"), "view_location")
    _login(app_client, user)
    for index in range(LOCATION_LIST_PAGE_SIZE + 3):
        Location.objects.create(code=f"P-{index:03d}", name=f"Loc {index:03d}")
    page1 = app_client.get("/locations/").content.decode()
    page2 = app_client.get("/locations/", {"page": 2}).content.decode()
    assert "Loc 000" in page1
    assert "Loc 049" in page1
    assert "Loc 049" not in page2
    assert "Loc 052" in page2


def test_xss_payload_is_escaped_in_list(app_client):
    payload = '<script>alert("xss")</script>'
    Location.objects.create(code="XSS-1", name=payload)
    user = _grant_permissions(_create_ordinary_user("xss"), "view_location")
    _login(app_client, user)
    content = app_client.get("/locations/").content.decode()
    assert payload not in content
    assert "&lt;script&gt;" in content


def test_status_mutation_is_post_only(app_client):
    location = Location.objects.create(code="POST", name="Post Only")
    user = _grant_permissions(
        _create_ordinary_user("post-only"),
        "view_location",
        "change_location",
    )
    _login(app_client, user)
    assert app_client.get(f"/locations/{location.pk}/deactivate/").status_code == 405
    location.refresh_from_db()
    assert location.active is True


def test_inactive_location_remains_readable(app_client):
    location = Location.objects.create(code="INACT", name="Inactive", active=False)
    user = _grant_permissions(_create_ordinary_user("read-inact"), "view_location")
    _login(app_client, user)
    assert app_client.get(f"/locations/{location.pk}/").status_code == 200
    list_content = app_client.get("/locations/").content.decode()
    assert "INACT" in list_content


def test_parent_labels_expose_inactive_state(app_client):
    parent = Location.objects.create(code="P-IN", name="Pasif Parent", active=False)
    Location.objects.create(code="C-1", name="Child", parent=parent)
    user = _grant_permissions(
        _create_ordinary_user("parent-label"),
        "view_location",
        "add_location",
    )
    _login(app_client, user)
    form = app_client.get("/locations/new/")
    content = form.content.decode()
    assert "[Pasif]" in content
    assert "P-IN" in content


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_templates_can_list_if_given_location_view(app_client, role_name):
    call_command("setup_roles", verbosity=0)
    group = Group.objects.get(name=role_name)
    group.permissions.add(_location_permission("view_location"))
    user = _create_ordinary_user(f"{role_name.lower()}-loc-view")
    user.groups.add(group)
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    assert app_client.get("/locations/").status_code == 200
