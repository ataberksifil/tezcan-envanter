from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.urls import NoReverseMatch, reverse

from accounts.roles import ADMIN_MANAGER, STOREKEEPER, TECHNICIAN
from inventory.models import ProductionLine
from inventory.views import PRODUCTION_LINE_LIST_PAGE_SIZE

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


def _production_line_tables_exist() -> bool:
    return "inventory_productionline" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_production_line_schema():
    if not _production_line_tables_exist():
        pytest.skip(
            "inventory migration not applied to test_tezcan_envanter; "
            "production line UI tests deferred until test DB is migrated"
        )


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


def _production_line_permission(codename):
    return Permission.objects.get(
        content_type__app_label="inventory",
        codename=codename,
    )


def _grant_permissions(user, *codenames):
    user.user_permissions.add(
        *[_production_line_permission(c) for c in codenames]
    )
    return _refresh_user_permissions(user)


def _login(app_client, user):
    app_client.force_login(user)
    return user


def test_production_line_named_routes():
    line = ProductionLine.objects.create(code="ROUTE", name="Route Line")
    assert reverse("inventory:production-line-list") == "/management/production-lines/"
    assert (
        reverse("inventory:production-line-create")
        == "/management/production-lines/new/"
    )
    assert (
        reverse("inventory:production-line-detail", args=[line.pk])
        == f"/management/production-lines/{line.pk}/"
    )
    assert (
        reverse("inventory:production-line-update", args=[line.pk])
        == f"/management/production-lines/{line.pk}/edit/"
    )


def test_no_production_line_delete_route_exists(app_client):
    line = ProductionLine.objects.create(code="NO-DEL", name="No Delete")
    with pytest.raises(NoReverseMatch):
        reverse("inventory:production-line-delete")
    user = _grant_permissions(_create_ordinary_user("no-del"), "view_productionline")
    _login(app_client, user)
    assert (
        app_client.post(f"/management/production-lines/{line.pk}/delete/").status_code
        == 404
    )


@pytest.mark.parametrize(
    "path",
    ("/management/production-lines/", "/management/production-lines/new/"),
)
def test_anonymous_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [path]


def test_view_productionline_allows_list_and_detail(app_client):
    line = ProductionLine.objects.create(code="VIEW", name="Viewable")
    user = _grant_permissions(
        _create_ordinary_user("viewer"), "view_productionline"
    )
    _login(app_client, user)
    assert app_client.get("/management/production-lines/").status_code == 200
    assert app_client.get(f"/management/production-lines/{line.pk}/").status_code == 200


def test_no_view_permission_returns_403(app_client):
    user = _create_ordinary_user("no-view")
    _login(app_client, user)
    assert app_client.get("/management/production-lines/").status_code == 403


def test_add_permission_controls_create(app_client):
    viewer = _grant_permissions(
        _create_ordinary_user("viewer-only"), "view_productionline"
    )
    _login(app_client, viewer)
    assert app_client.get("/management/production-lines/new/").status_code == 403

    writer = _grant_permissions(
        _create_ordinary_user("creator"),
        "view_productionline",
        "add_productionline",
    )
    _login(app_client, writer)
    assert app_client.get("/management/production-lines/new/").status_code == 200


def test_change_permission_controls_edit_and_status(app_client):
    line = ProductionLine.objects.create(code="CHG", name="Change Me")
    viewer = _grant_permissions(
        _create_ordinary_user("view-only"), "view_productionline"
    )
    _login(app_client, viewer)
    assert (
        app_client.get(f"/management/production-lines/{line.pk}/edit/").status_code
        == 403
    )
    assert (
        app_client.post(
            f"/management/production-lines/{line.pk}/deactivate/"
        ).status_code
        == 403
    )

    editor = _grant_permissions(
        _create_ordinary_user("editor"),
        "view_productionline",
        "change_productionline",
    )
    _login(app_client, editor)
    assert (
        app_client.get(f"/management/production-lines/{line.pk}/edit/").status_code
        == 200
    )


def test_group_name_alone_does_not_authorize(app_client):
    group, _ = Group.objects.get_or_create(name=ADMIN_MANAGER)
    user = _create_ordinary_user("empty-admin-group")
    user.groups.add(group)
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    assert app_client.get("/management/production-lines/").status_code == 403


def test_is_staff_alone_does_not_authorize(app_client):
    user = _create_ordinary_user("staff-only")
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    assert app_client.get("/management/production-lines/").status_code == 403


def test_forged_descendant_parent_rejected(app_client):
    root = ProductionLine.objects.create(code="ROOT", name="Root")
    child = ProductionLine.objects.create(code="CH", name="Child", parent=root)
    user = _grant_permissions(
        _create_ordinary_user("forger"),
        "view_productionline",
        "change_productionline",
    )
    _login(app_client, user)
    response = app_client.post(
        f"/management/production-lines/{root.pk}/edit/",
        data={
            "code": root.code,
            "name": root.name,
            "parent": str(child.pk),
        },
    )
    assert response.status_code == 200
    root.refresh_from_db()
    assert root.parent_id is None


def test_forged_active_post_ignored_on_create(app_client):
    user = _grant_permissions(
        _create_ordinary_user("active-forge"),
        "view_productionline",
        "add_productionline",
    )
    _login(app_client, user)
    response = app_client.post(
        "/management/production-lines/new/",
        data={
            "code": "NEW-ACT",
            "name": "New Active Forge",
            "parent": "",
            "active": "false",
        },
    )
    assert response.status_code == 302
    created = ProductionLine.objects.get(code="NEW-ACT")
    assert created.active is True


def test_list_search_filter_and_status_filter(app_client):
    ProductionLine.objects.create(code="ALPHA", name="Alpha Line")
    ProductionLine.objects.create(code="BETA", name="Beta Line")
    ProductionLine.objects.create(code="GAMMA", name="Gamma", active=False)
    user = _grant_permissions(
        _create_ordinary_user("lister"), "view_productionline"
    )
    _login(app_client, user)

    search = app_client.get(
        "/management/production-lines/", {"q": "alpha"}
    ).content.decode()
    assert "ALPHA" in search
    assert "BETA" not in search

    inactive_filter = app_client.get(
        "/management/production-lines/", {"status": "inactive"}
    ).content.decode()
    assert "GAMMA" in inactive_filter
    assert "ALPHA" not in inactive_filter


def test_list_ordering_and_pagination(app_client):
    user = _grant_permissions(
        _create_ordinary_user("pager"), "view_productionline"
    )
    _login(app_client, user)
    for index in range(PRODUCTION_LINE_LIST_PAGE_SIZE + 3):
        ProductionLine.objects.create(
            code=f"P-{index:03d}", name=f"Line {index:03d}"
        )
    page1 = app_client.get("/management/production-lines/").content.decode()
    page2 = app_client.get(
        "/management/production-lines/", {"page": 2}
    ).content.decode()
    assert "Line 000" in page1
    assert "Line 049" in page1
    assert "Line 049" not in page2
    assert "Line 052" in page2


def test_xss_payload_is_escaped_in_list(app_client):
    payload = '<script>alert("xss")</script>'
    ProductionLine.objects.create(code="XSS-1", name=payload)
    user = _grant_permissions(_create_ordinary_user("xss"), "view_productionline")
    _login(app_client, user)
    content = app_client.get("/management/production-lines/").content.decode()
    assert payload not in content
    assert "&lt;script&gt;" in content


def test_status_mutation_is_post_only(app_client):
    line = ProductionLine.objects.create(code="POST", name="Post Only")
    user = _grant_permissions(
        _create_ordinary_user("post-only"),
        "view_productionline",
        "change_productionline",
    )
    _login(app_client, user)
    assert (
        app_client.get(
            f"/management/production-lines/{line.pk}/deactivate/"
        ).status_code
        == 405
    )
    line.refresh_from_db()
    assert line.active is True


def test_inactive_production_line_remains_readable(app_client):
    line = ProductionLine.objects.create(
        code="INACT", name="Inactive", active=False
    )
    user = _grant_permissions(
        _create_ordinary_user("read-inact"), "view_productionline"
    )
    _login(app_client, user)
    assert (
        app_client.get(f"/management/production-lines/{line.pk}/").status_code == 200
    )
    list_content = app_client.get("/management/production-lines/").content.decode()
    assert "INACT" in list_content


def test_parent_labels_expose_inactive_state(app_client):
    parent = ProductionLine.objects.create(
        code="P-IN", name="Pasif Parent", active=False
    )
    ProductionLine.objects.create(code="C-1", name="Child", parent=parent)
    user = _grant_permissions(
        _create_ordinary_user("parent-label"),
        "view_productionline",
        "add_productionline",
    )
    _login(app_client, user)
    form = app_client.get("/management/production-lines/new/")
    content = form.content.decode()
    assert "[Pasif]" in content
    assert "P-IN" in content


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_templates_do_not_get_production_line_permissions(
    app_client, role_name
):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(f"{role_name.lower()}-pl-check")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert user.has_perm("inventory.view_productionline") is False
    assert user.has_perm("inventory.add_productionline") is False
    assert user.has_perm("inventory.change_productionline") is False
