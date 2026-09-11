import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.urls import NoReverseMatch, reverse

from accounts.roles import ADMIN_MANAGER, MANAGE_ACCESS_PERMISSION
from audit.admin import AuditEventAdmin
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "synthetic-test-password-only"


def permission(label):
    app_label, codename = label.split(".", 1)
    return Permission.objects.get(
        content_type__app_label=app_label, codename=codename
    )


def make_user(username, **kwargs):
    return User.objects.create_user(username=username, password=PASSWORD, **kwargs)


def make_access_manager(username="manager"):
    user = make_user(username)
    role = Group.objects.create(name=f"{username}-role")
    role.permissions.add(permission(MANAGE_ACCESS_PERMISSION))
    user.groups.add(role)
    return User.objects.get(pk=user.pk)


@pytest.mark.parametrize(
    "url_name",
    [
        "accounts:role-list",
        "accounts:role-create",
        "accounts:access-user-list",
    ],
)
def test_anonymous_is_redirected_and_ordinary_authenticated_user_gets_403(client, url_name):
    url = reverse(url_name)
    assert client.get(url).status_code == 302
    client.force_login(make_user(f"plain-{url_name.split(':')[1]}"))
    assert client.get(url).status_code == 403


@pytest.mark.parametrize("kind", ["name", "staff"])
def test_group_name_or_staff_alone_does_not_authorize_ui(client, kind):
    user = make_user(f"unauthorized-{kind}", is_staff=kind == "staff")
    if kind == "name":
        role = Group.objects.create(name=ADMIN_MANAGER)
        user.groups.add(role)
    client.force_login(user)
    assert client.get(reverse("accounts:role-list")).status_code == 403


def test_manager_without_catalog_write_sees_management_hub_and_real_cards(client):
    actor = make_access_manager()
    client.force_login(actor)
    home = client.get(reverse("home")).content.decode()
    hub = client.get(reverse("management"))
    content = hub.content.decode()
    assert ">Yönetim</a>" in home
    assert hub.status_code == 200
    assert "Roller ve Yetkiler" in content
    assert "Kullanıcılar" in content
    assert reverse("accounts:role-list") in content
    assert reverse("accounts:access-user-list") in content
    assert "Teknik Alan" not in content
    assert "Lokasyon" not in content


def test_superuser_can_use_role_and_user_routes(client):
    actor = User.objects.create_superuser("root", "", PASSWORD)
    role = Group.objects.create(name="Editable")
    target = make_user("target")
    client.force_login(actor)
    assert client.get(reverse("accounts:role-list")).status_code == 200
    assert client.get(reverse("accounts:role-create")).status_code == 200
    assert client.get(reverse("accounts:role-rename", args=[role.pk])).status_code == 200
    assert client.get(reverse("accounts:role-permissions", args=[role.pk])).status_code == 200
    assert client.get(reverse("accounts:access-user-list")).status_code == 200
    assert client.get(reverse("accounts:access-user-roles", args=[target.pk])).status_code == 200


def test_role_forms_mutate_only_on_post_and_include_csrf(client):
    actor = User.objects.create_superuser("root", "", PASSWORD)
    client.force_login(actor)
    get_response = client.get(reverse("accounts:role-create"))
    assert "csrfmiddlewaretoken" in get_response.content.decode()
    assert Group.objects.count() == 0
    response = client.post(reverse("accounts:role-create"), {"name": " UI Role "})
    assert response.status_code == 302
    assert Group.objects.get().name == "UI Role"


@pytest.mark.parametrize(
    "forged",
    ["catalog.delete_material", "auth.change_group", "audit.view_auditevent"],
)
def test_permission_form_rejects_forged_choices(client, forged):
    actor = User.objects.create_superuser("root", "", PASSWORD)
    role = Group.objects.create(name="Safe")
    client.force_login(actor)
    response = client.post(
        reverse("accounts:role-permissions", args=[role.pk]),
        {"catalog_permissions": [forged]},
    )
    assert response.status_code == 200
    assert role.permissions.count() == 0
    assert AuditEvent.objects.count() == 0


def test_non_superuser_permission_form_does_not_expose_manage_access(client):
    actor = make_access_manager()
    role = Group.objects.create(name="Lower")
    client.force_login(actor)
    content = client.get(
        reverse("accounts:role-permissions", args=[role.pk])
    ).content.decode()
    assert "name=\"manage_access\"" not in content


def test_user_roles_page_shows_direct_permissions_read_only_and_preserves_them(client):
    actor = User.objects.create_superuser("root", "", PASSWORD)
    target = make_user("target")
    target.user_permissions.add(permission("catalog.view_material"))
    role = Group.objects.create(name="Assignable")
    client.force_login(actor)
    url = reverse("accounts:access-user-roles", args=[target.pk])
    content = client.get(url).content.decode()
    assert "Doğrudan izinler (salt okunur)" in content
    assert "catalog.view_material" in content
    response = client.post(url, {"roles": [str(role.pk)]})
    assert response.status_code == 302
    assert target.user_permissions.filter(pk=permission("catalog.view_material").pk).exists()


def test_inactive_target_with_excess_assigned_permissions_is_rejected_on_role_post(
    client,
):
    actor = make_access_manager("limited-manager")
    actor_role = actor.groups.get()
    actor_role.permissions.add(permission("catalog.view_category"))
    target = make_user("inactive-http-target", is_active=False)
    excessive = Group.objects.create(name="Inactive HTTP excessive")
    excessive.permissions.add(permission("catalog.view_material"))
    target.groups.add(excessive)
    client.force_login(actor)

    response = client.post(
        reverse("accounts:access-user-roles", args=[target.pk]),
        {"roles": []},
    )

    assert response.status_code == 403
    assert set(target.groups.values_list("pk", flat=True)) == {excessive.pk}
    assert AuditEvent.objects.count() == 0


def test_out_of_scope_role_and_protected_users_render_read_only(client):
    actor = User.objects.create_superuser("root", "", PASSWORD)
    legacy = Group.objects.create(name="Legacy")
    legacy.permissions.add(permission("catalog.delete_material"))
    target = make_user("legacy-user")
    target.groups.add(legacy)
    client.force_login(actor)
    roles = client.get(reverse("accounts:role-list")).content.decode()
    users = client.get(reverse("accounts:access-user-list")).content.decode()
    assert "Kapsam dışı / salt okunur" in roles
    assert "Korunuyor / salt okunur" in users


def test_no_delete_route_or_delete_ui(client):
    actor = User.objects.create_superuser("root", "", PASSWORD)
    client.force_login(actor)
    with pytest.raises(NoReverseMatch):
        reverse("accounts:role-delete", args=[1])
    assert "Sil" not in client.get(reverse("accounts:role-list")).content.decode()


def test_group_and_user_admin_bypasses_are_closed_and_audit_stays_read_only():
    assert Group not in admin.site._registry
    assert User not in admin.site._registry
    audit_admin = admin.site._registry[AuditEvent]
    assert isinstance(audit_admin, AuditEventAdmin)
    request = type("Request", (), {})()
    assert audit_admin.has_add_permission(request) is False
    assert audit_admin.has_change_permission(request) is False
    assert audit_admin.has_delete_permission(request) is False
