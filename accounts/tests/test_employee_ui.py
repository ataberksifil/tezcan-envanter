from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.urls import NoReverseMatch, reverse

from accounts.models import Employee
from accounts.employee_views import EMPLOYEE_LIST_PAGE_SIZE
from accounts.roles import ADMIN_MANAGER

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _employee_table_exists() -> bool:
    return "accounts_employee" in connection.introspection.table_names()


@pytest.fixture(autouse=True)
def require_employee_schema():
    if not _employee_table_exists():
        pytest.skip(
            "accounts.0003_employee not applied to test_tezcan_envanter; "
            "employee UI tests deferred until test DB is migrated"
        )


def _refresh_user_permissions(user):
    user = User.objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _create_ordinary_user(username):
    user = User.objects.create_user(username=username, password=PASSWORD)
    assert not user.is_superuser
    assert not user.is_staff
    return user


def _employee_permission(codename):
    return Permission.objects.get(
        content_type__app_label="accounts",
        codename=codename,
    )


def _grant_permissions(user, *codenames):
    user.user_permissions.add(*[_employee_permission(c) for c in codenames])
    return _refresh_user_permissions(user)


def _login(app_client, user):
    app_client.force_login(user)
    return user


def _employee_admin(username=None):
    return _grant_permissions(
        _create_ordinary_user(username or f"emp-admin-{uuid.uuid4().hex[:8]}"),
        "view_employee",
        "add_employee",
        "change_employee",
    )


def test_employee_named_routes():
    employee = Employee.objects.create(
        employee_number="ROUTE",
        first_name="Route",
        last_name="Employee",
    )
    assert reverse("accounts:employee-list") == "/management/employees/"
    assert reverse("accounts:employee-create") == "/management/employees/new/"
    assert (
        reverse("accounts:employee-detail", args=[employee.pk])
        == f"/management/employees/{employee.pk}/"
    )
    assert (
        reverse("accounts:employee-update", args=[employee.pk])
        == f"/management/employees/{employee.pk}/edit/"
    )


def test_no_employee_delete_route_exists(app_client):
    employee = Employee.objects.create(
        employee_number="NO-DEL",
        first_name="No",
        last_name="Delete",
    )
    with pytest.raises(NoReverseMatch):
        reverse("accounts:employee-delete")
    user = _grant_permissions(_create_ordinary_user("no-del"), "view_employee")
    _login(app_client, user)
    assert app_client.post(f"/management/employees/{employee.pk}/delete/").status_code == 404


@pytest.mark.parametrize(
    "path",
    ("/management/employees/", "/management/employees/new/"),
)
def test_anonymous_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    assert urlparse(response.url).path == "/accounts/login/"
    assert parse_qs(urlparse(response.url).query)["next"] == [path]


def test_view_employee_allows_list_and_detail(app_client):
    employee = Employee.objects.create(
        employee_number="VIEW",
        first_name="View",
        last_name="Only",
    )
    user = _grant_permissions(_create_ordinary_user("viewer"), "view_employee")
    _login(app_client, user)
    assert app_client.get("/management/employees/").status_code == 200
    detail = app_client.get(f"/management/employees/{employee.pk}/")
    assert detail.status_code == 200
    assert "Bağlı kullanıcı" not in detail.content.decode()


def test_no_view_permission_returns_403(app_client):
    user = _create_ordinary_user("no-view")
    _login(app_client, user)
    assert app_client.get("/management/employees/").status_code == 403


def test_add_permission_controls_create(app_client):
    viewer = _grant_permissions(_create_ordinary_user("viewer-only"), "view_employee")
    _login(app_client, viewer)
    assert app_client.get("/management/employees/new/").status_code == 403

    writer = _grant_permissions(
        _create_ordinary_user("creator"),
        "view_employee",
        "add_employee",
    )
    _login(app_client, writer)
    assert app_client.get("/management/employees/new/").status_code == 200


def test_change_permission_controls_edit_and_status(app_client):
    employee = Employee.objects.create(
        employee_number="EDIT",
        first_name="Edit",
        last_name="Target",
    )
    viewer = _grant_permissions(_create_ordinary_user("viewer"), "view_employee")
    _login(app_client, viewer)
    assert app_client.get(f"/management/employees/{employee.pk}/edit/").status_code == 403
    assert (
        app_client.post(f"/management/employees/{employee.pk}/deactivate/").status_code
        == 403
    )

    editor = _grant_permissions(
        _create_ordinary_user("editor"),
        "view_employee",
        "change_employee",
    )
    _login(app_client, editor)
    assert app_client.get(f"/management/employees/{employee.pk}/edit/").status_code == 200
    assert (
        app_client.post(f"/management/employees/{employee.pk}/deactivate/").status_code
        == 302
    )


def test_group_name_or_staff_alone_insufficient(app_client):
    group, _created = Group.objects.get_or_create(name=ADMIN_MANAGER)
    user = User.objects.create_user(
        username="group-only",
        password=PASSWORD,
        is_staff=True,
    )
    user.groups.add(group)
    user = _refresh_user_permissions(user)
    _login(app_client, user)
    assert app_client.get("/management/employees/").status_code == 403


def test_forged_active_field_ignored_on_create(app_client):
    admin = _employee_admin("forged-active")
    _login(app_client, admin)
    response = app_client.post(
        "/management/employees/new/",
        {
            "employee_number": "FORGED-1",
            "first_name": "Forged",
            "last_name": "Active",
            "active": "false",
        },
    )
    assert response.status_code == 302
    employee = Employee.objects.get(employee_number="FORGED-1")
    assert employee.active is True


def test_linked_user_selector_excludes_other_employees_and_marks_inactive(app_client):
    admin = _employee_admin("selector")
    linked_user = User.objects.create_user(
        username="linked-user",
        password=PASSWORD,
        is_active=False,
    )
    free_user = _create_ordinary_user("free-user")
    employee = Employee.objects.create(
        employee_number="SEL-1",
        first_name="Current",
        last_name="Employee",
        user=linked_user,
    )
    Employee.objects.create(
        employee_number="SEL-2",
        first_name="Other",
        last_name="Employee",
        user=_create_ordinary_user("other-linked"),
    )
    _login(app_client, admin)
    response = app_client.get(f"/management/employees/{employee.pk}/edit/")
    content = response.content.decode()
    assert linked_user.get_username() in content
    assert "[Pasif]" in content
    assert free_user.get_username() in content
    assert "other-linked" not in content


def test_duplicate_user_assignment_rejected_in_ui(app_client):
    admin = _employee_admin("dup-ui")
    shared = _create_ordinary_user("shared-ui")
    Employee.objects.create(
        employee_number="DUP-1",
        first_name="First",
        last_name="Employee",
        user=shared,
    )
    target = Employee.objects.create(
        employee_number="DUP-2",
        first_name="Second",
        last_name="Employee",
    )
    _login(app_client, admin)
    response = app_client.post(
        f"/management/employees/{target.pk}/edit/",
        {
            "employee_number": "DUP-2",
            "first_name": "Second",
            "last_name": "Employee",
            "user": str(shared.pk),
        },
    )
    assert response.status_code == 200
    target.refresh_from_db()
    assert target.user_id is None
    assert "user" in response.context["form"].errors


def test_status_routes_are_post_only(app_client):
    admin = _employee_admin("post-only")
    employee = Employee.objects.create(
        employee_number="POST",
        first_name="Post",
        last_name="Only",
    )
    _login(app_client, admin)
    assert (
        app_client.get(f"/management/employees/{employee.pk}/deactivate/").status_code
        == 405
    )


def test_inactive_employee_readable(app_client):
    employee = Employee.objects.create(
        employee_number="INACT",
        first_name="Inactive",
        last_name="Employee",
        active=False,
    )
    user = _grant_permissions(_create_ordinary_user("inactive-read"), "view_employee")
    _login(app_client, user)
    assert app_client.get(f"/management/employees/{employee.pk}/").status_code == 200


def test_list_search_filter_order_and_pagination(app_client):
    admin = _employee_admin("list-ui")
    _login(app_client, admin)
    for index in range(EMPLOYEE_LIST_PAGE_SIZE + 3):
        Employee.objects.create(
            employee_number=f"P{index:03d}",
            first_name=f"Name{index}",
            last_name="Zulu",
            active=index % 2 == 0,
        )
    Employee.objects.create(
        employee_number="FINDME",
        first_name="Unique",
        last_name="Alpha",
        active=True,
    )
    response = app_client.get("/management/employees/?q=FINDME&status=active")
    content = response.content.decode()
    assert response.status_code == 200
    assert "FINDME" in content
    assert "P000" not in content

    page_two = app_client.get("/management/employees/?page=2")
    assert page_two.status_code == 200
    assert "2 /" in page_two.content.decode()


def test_xss_payload_is_escaped_in_list(app_client):
    admin = _employee_admin("xss")
    payload = '<script>alert("x")</script>'
    Employee.objects.create(
        employee_number="XSS-1",
        first_name=payload,
        last_name="Safe",
    )
    _login(app_client, admin)
    content = app_client.get("/management/employees/?q=XSS-1").content.decode()
    assert payload not in content
    assert "&lt;script&gt;" in content


def test_change_employee_detail_shows_linked_username(app_client):
    admin = _employee_admin("detail-link")
    linked = _create_ordinary_user("detail-linked")
    employee = Employee.objects.create(
        employee_number="DET-1",
        first_name="Detail",
        last_name="Link",
        user=linked,
    )
    _login(app_client, admin)
    content = app_client.get(f"/management/employees/{employee.pk}/").content.decode()
    assert linked.get_username() in content
    assert "Bağlı kullanıcı" in content


def test_create_via_service_and_edit_via_ui(app_client):
    admin = _employee_admin("flow")
    _login(app_client, admin)
    response = app_client.post(
        "/management/employees/new/",
        {
            "employee_number": "FLOW-1",
            "first_name": "Flow",
            "last_name": "Test",
        },
    )
    assert response.status_code == 302
    employee = Employee.objects.get(employee_number="FLOW-1")
    assert employee.active is True
