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
from catalog.views import CATEGORY_LIST_PAGE_SIZE

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


def _parent_choice_ids(form):
    return {
        str(value)
        for value, _label in form.fields["parent"].widget.choices
        if value not in ("", None)
    }


def _parent_choice_labels(form):
    return [
        str(label)
        for value, label in form.fields["parent"].widget.choices
        if value not in ("", None)
    ]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_category_named_routes():
    category = Category.objects.create(name="ROUTE-CAT")
    assert reverse("catalog:category-list") == "/catalog/categories/"
    assert reverse("catalog:category-create") == "/catalog/categories/new/"
    assert (
        reverse("catalog:category-update", args=[category.pk])
        == f"/catalog/categories/{category.pk}/edit/"
    )
    assert (
        reverse("catalog:category-deactivate", args=[category.pk])
        == f"/catalog/categories/{category.pk}/deactivate/"
    )
    assert (
        reverse("catalog:category-reactivate", args=[category.pk])
        == f"/catalog/categories/{category.pk}/reactivate/"
    )


def test_no_category_delete_route_exists(app_client):
    category = Category.objects.create(name="NO-DELETE")
    with pytest.raises(NoReverseMatch):
        reverse("catalog:category-delete")
    with pytest.raises(NoReverseMatch):
        reverse("catalog:category-delete", args=[category.pk])

    user = _role_user(ADMIN_MANAGER, "no-delete-admin")
    _login(app_client, user)
    response = app_client.post(f"/catalog/categories/{category.pk}/delete/")
    assert response.status_code == 404
    assert Category.objects.filter(pk=category.pk).exists()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    (
        "/catalog/categories/",
        "/catalog/categories/new/",
    ),
)
def test_anonymous_category_get_redirects_to_login(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == [path]


def test_anonymous_category_post_redirects_to_login_and_does_not_mutate(app_client):
    before = Category.objects.count()
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "ANON-CREATE", "code": "", "parent": ""},
    )
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert Category.objects.count() == before
    assert not Category.objects.filter(name="ANON-CREATE").exists()


def test_category_login_next_lands_on_list(app_client):
    user = _role_user(TECHNICIAN, "login-next-tech")
    response = app_client.post(
        "/accounts/login/",
        {
            "username": user.username,
            "password": PASSWORD,
            "next": "/catalog/categories/",
        },
    )
    assert response.status_code == 302
    assert response.url == "/catalog/categories/"
    assert SESSION_KEY in app_client.session
    listing = app_client.get("/catalog/categories/")
    assert listing.status_code == 200


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_user_without_view_category_gets_403(app_client):
    user = _create_ordinary_user("no-view-cat")
    _login(app_client, user)
    response = app_client.get("/catalog/categories/")
    assert response.status_code == 403


def test_view_permitted_role_gets_200(app_client):
    user = _role_user(TECHNICIAN, "tech-list-ok")
    _login(app_client, user)
    response = app_client.get("/catalog/categories/")
    assert response.status_code == 200
    assert "Kategoriler" in response.content.decode()


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_cannot_create(app_client, role_name):
    user = _role_user(role_name, f"{role_name.lower()}-no-create")
    _login(app_client, user)
    before = set(Category.objects.values_list("pk", flat=True))

    get_response = app_client.get("/catalog/categories/new/")
    assert get_response.status_code == 403
    content = app_client.get("/catalog/categories/").content.decode()
    assert "Yeni kategori" not in content

    post_response = app_client.post(
        "/catalog/categories/new/",
        {"name": f"{role_name}-CRAFTED", "code": "", "parent": ""},
    )
    assert post_response.status_code == 403
    after = set(Category.objects.values_list("pk", flat=True))
    assert after == before
    assert not Category.objects.filter(name=f"{role_name}-CRAFTED").exists()


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_cannot_edit(app_client, role_name):
    category = Category.objects.create(name=f"{role_name}-EDIT-TARGET", code="KEEP")
    user = _role_user(role_name, f"{role_name.lower()}-no-edit")
    _login(app_client, user)

    get_response = app_client.get(
        reverse("catalog:category-update", args=[category.pk])
    )
    assert get_response.status_code == 403
    content = app_client.get("/catalog/categories/").content.decode()
    assert "Düzenle" not in content

    post_response = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "HACKED-NAME", "code": "HACK", "parent": ""},
    )
    assert post_response.status_code == 403
    category.refresh_from_db()
    assert category.name == f"{role_name}-EDIT-TARGET"
    assert category.code == "KEEP"


@pytest.mark.parametrize("role_name", (TECHNICIAN, STOREKEEPER))
def test_view_only_role_cannot_deactivate_or_reactivate(app_client, role_name):
    active = Category.objects.create(name=f"{role_name}-ACTIVE-STATE", active=True)
    inactive = Category.objects.create(name=f"{role_name}-INACTIVE-STATE", active=False)
    user = _role_user(role_name, f"{role_name.lower()}-no-state")
    _login(app_client, user)
    content = app_client.get("/catalog/categories/").content.decode()
    assert "Pasifleştir" not in content
    assert "Aktifleştir" not in content

    deactivate = app_client.post(
        reverse("catalog:category-deactivate", args=[active.pk])
    )
    reactivate = app_client.post(
        reverse("catalog:category-reactivate", args=[inactive.pk])
    )
    assert deactivate.status_code == 403
    assert reactivate.status_code == 403
    active.refresh_from_db()
    inactive.refresh_from_db()
    assert active.active is True
    assert inactive.active is False


def test_admin_manager_can_perform_all_allowed_operations(app_client):
    user = _role_user(ADMIN_MANAGER, "admin-all-ops")
    _login(app_client, user)

    list_response = app_client.get("/catalog/categories/")
    assert list_response.status_code == 200
    assert "Yeni kategori" in list_response.content.decode()

    create_get = app_client.get("/catalog/categories/new/")
    assert create_get.status_code == 200
    create_post = app_client.post(
        "/catalog/categories/new/",
        {"name": "ADMIN-CREATED", "code": "ADM-1", "parent": ""},
    )
    assert create_post.status_code == 302
    created = Category.objects.get(name="ADMIN-CREATED")
    assert created.active is True

    edit_get = app_client.get(reverse("catalog:category-update", args=[created.pk]))
    assert edit_get.status_code == 200
    edit_post = app_client.post(
        reverse("catalog:category-update", args=[created.pk]),
        {"name": "ADMIN-EDITED", "code": "ADM-1", "parent": ""},
    )
    assert edit_post.status_code == 302
    created.refresh_from_db()
    assert created.name == "ADMIN-EDITED"

    deactivate = app_client.post(
        reverse("catalog:category-deactivate", args=[created.pk])
    )
    assert deactivate.status_code == 302
    created.refresh_from_db()
    assert created.active is False

    reactivate = app_client.post(
        reverse("catalog:category-reactivate", args=[created.pk])
    )
    assert reactivate.status_code == 302
    created.refresh_from_db()
    assert created.active is True


def test_unauthorized_post_does_not_mutate_even_if_button_could_be_forged(app_client):
    category = Category.objects.create(name="FORGE-TARGET", active=True)
    user = _role_user(TECHNICIAN, "forge-tech")
    _login(app_client, user)
    before_count = Category.objects.count()
    response = app_client.post(
        reverse("catalog:category-deactivate", args=[category.pk])
    )
    assert response.status_code == 403
    category.refresh_from_db()
    assert category.active is True
    assert Category.objects.count() == before_count


def test_get_against_state_changing_endpoints_returns_405(app_client):
    category = Category.objects.create(name="GET-STATE", active=True)
    user = _role_user(ADMIN_MANAGER, "get-state-admin")
    _login(app_client, user)

    deactivate = app_client.get(
        reverse("catalog:category-deactivate", args=[category.pk])
    )
    reactivate = app_client.get(
        reverse("catalog:category-reactivate", args=[category.pk])
    )
    assert deactivate.status_code == 405
    assert reactivate.status_code == 405
    category.refresh_from_db()
    assert category.active is True


# ---------------------------------------------------------------------------
# Create / edit validation
# ---------------------------------------------------------------------------


def test_create_top_level_category(app_client):
    user = _role_user(ADMIN_MANAGER, "create-top")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "TOP-LEVEL-NEW", "code": "TOP-1", "parent": ""},
        follow=True,
    )
    assert response.status_code == 200
    category = Category.objects.get(name="TOP-LEVEL-NEW")
    assert category.parent_id is None
    assert category.code == "TOP-1"
    assert category.active is True
    assert "Kategori oluşturuldu." in response.content.decode()


def test_create_child_category(app_client):
    parent = Category.objects.create(name="PARENT-FOR-CHILD")
    user = _role_user(ADMIN_MANAGER, "create-child")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "CHILD-NEW", "code": "", "parent": str(parent.pk)},
    )
    assert response.status_code == 302
    child = Category.objects.get(name="CHILD-NEW")
    assert child.parent_id == parent.pk
    assert child.active is True


def test_whitespace_only_name_rejected(app_client):
    user = _role_user(ADMIN_MANAGER, "ws-name")
    _login(app_client, user)
    before = Category.objects.count()
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "   ", "code": "WS", "parent": ""},
    )
    assert response.status_code == 200
    form = response.context["form"]
    assert form.errors
    assert "name" in form.errors
    assert Category.objects.count() == before
    assert response.status_code != 500


def test_whitespace_only_code_stored_as_null(app_client):
    user = _role_user(ADMIN_MANAGER, "ws-code")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "NULL-CODE-CAT", "code": "   ", "parent": ""},
    )
    assert response.status_code == 302
    category = Category.objects.get(name="NULL-CODE-CAT")
    assert category.code is None


def test_duplicate_name_accepted(app_client):
    Category.objects.create(name="DUP-NAME")
    user = _role_user(ADMIN_MANAGER, "dup-name")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "DUP-NAME", "code": "OTHER", "parent": ""},
    )
    assert response.status_code == 302
    assert Category.objects.filter(name="DUP-NAME").count() == 2


def test_duplicate_non_null_code_accepted(app_client):
    Category.objects.create(name="CODE-A", code="DUP-CODE")
    user = _role_user(ADMIN_MANAGER, "dup-code")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "CODE-B", "code": "DUP-CODE", "parent": ""},
    )
    assert response.status_code == 302
    assert Category.objects.filter(code="DUP-CODE").count() == 2


def test_multiple_null_codes_accepted(app_client):
    Category.objects.create(name="NULL-CODE-ONE", code=None)
    user = _role_user(ADMIN_MANAGER, "multi-null-code")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "NULL-CODE-TWO", "code": "", "parent": ""},
    )
    assert response.status_code == 302
    assert Category.objects.filter(name="NULL-CODE-TWO", code__isnull=True).exists()
    assert Category.objects.filter(code__isnull=True).count() >= 2


def test_create_uses_model_default_active_true_even_if_active_posted(app_client):
    user = _role_user(ADMIN_MANAGER, "active-not-exposed")
    _login(app_client, user)
    form = app_client.get("/catalog/categories/new/").context["form"]
    assert list(form.fields) == ["code", "name", "parent"]
    assert "active" not in form.fields
    assert "id" not in form.fields
    assert "created_at" not in form.fields
    assert "updated_at" not in form.fields

    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "ACTIVE-DEFAULT", "code": "", "parent": "", "active": "false"},
    )
    assert response.status_code == 302
    category = Category.objects.get(name="ACTIVE-DEFAULT")
    assert category.active is True


def test_edit_does_not_change_active_status(app_client):
    category = Category.objects.create(name="EDIT-KEEP-ACTIVE", active=True)
    inactive = Category.objects.create(name="EDIT-KEEP-INACTIVE", active=False)
    user = _role_user(ADMIN_MANAGER, "edit-no-active")
    _login(app_client, user)

    app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "EDIT-KEEP-ACTIVE-2", "code": "", "parent": "", "active": "false"},
    )
    category.refresh_from_db()
    assert category.name == "EDIT-KEEP-ACTIVE-2"
    assert category.active is True

    app_client.post(
        reverse("catalog:category-update", args=[inactive.pk]),
        {"name": "EDIT-KEEP-INACTIVE-2", "code": "", "parent": "", "active": "true"},
    )
    inactive.refresh_from_db()
    assert inactive.name == "EDIT-KEEP-INACTIVE-2"
    assert inactive.active is False


def test_successful_edit_redirects_with_turkish_message(app_client):
    category = Category.objects.create(name="BEFORE-EDIT")
    user = _role_user(ADMIN_MANAGER, "edit-ok")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "AFTER-EDIT", "code": "ED", "parent": ""},
        follow=True,
    )
    assert response.status_code == 200
    assert response.redirect_chain[0][0] == "/catalog/categories/"
    assert "Kategori güncellendi." in response.content.decode()
    category.refresh_from_db()
    assert category.name == "AFTER-EDIT"
    assert category.code == "ED"


def test_validation_failure_does_not_partially_mutate(app_client):
    category = Category.objects.create(name="VALID-KEEP", code="KEEP-CODE")
    user = _role_user(ADMIN_MANAGER, "no-partial")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "   ", "code": "CHANGED", "parent": ""},
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    category.refresh_from_db()
    assert category.name == "VALID-KEEP"
    assert category.code == "KEEP-CODE"


# ---------------------------------------------------------------------------
# Parent selector / cycles
# ---------------------------------------------------------------------------


def test_self_parent_crafted_post_rejected_by_model_validation(app_client):
    category = Category.objects.create(name="SELF-PARENT")
    user = _role_user(ADMIN_MANAGER, "self-parent")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:category-update", args=[category.pk]),
        {"name": "SELF-PARENT", "code": "", "parent": str(category.pk)},
    )
    assert response.status_code == 200
    form = response.context["form"]
    assert form.errors
    assert "parent" in form.errors
    assert "Kategori kendi üst kategorisi olamaz." in form.errors["parent"]
    category.refresh_from_db()
    assert category.parent_id is None


def test_descendant_cycle_crafted_post_rejected_by_model_validation(app_client):
    root = Category.objects.create(name="CYCLE-ROOT")
    child = Category.objects.create(name="CYCLE-CHILD", parent=root)
    grandchild = Category.objects.create(name="CYCLE-GRAND", parent=child)
    user = _role_user(ADMIN_MANAGER, "cycle-parent")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:category-update", args=[root.pk]),
        {"name": "CYCLE-ROOT", "code": "", "parent": str(grandchild.pk)},
    )
    assert response.status_code == 200
    form = response.context["form"]
    assert form.errors
    assert "parent" in form.errors
    assert "Kategori hiyerarşisinde döngü oluşturulamaz." in form.errors["parent"]
    root.refresh_from_db()
    assert root.parent_id is None


def test_inactive_parent_currently_accepted(app_client):
    inactive_parent = Category.objects.create(name="INACTIVE-PARENT", active=False)
    user = _role_user(ADMIN_MANAGER, "inactive-parent-ok")
    _login(app_client, user)
    response = app_client.post(
        "/catalog/categories/new/",
        {"name": "CHILD-OF-INACTIVE", "code": "", "parent": str(inactive_parent.pk)},
    )
    assert response.status_code == 302
    child = Category.objects.get(name="CHILD-OF-INACTIVE")
    assert child.parent_id == inactive_parent.pk
    assert child.active is True


def test_inactive_parent_visibly_identified_in_selector(app_client):
    inactive_parent = Category.objects.create(
        name="VISIBLE-INACTIVE",
        code="VIN",
        active=False,
    )
    user = _role_user(ADMIN_MANAGER, "inactive-label")
    _login(app_client, user)
    form = app_client.get("/catalog/categories/new/").context["form"]
    labels = _parent_choice_labels(form)
    matching = [label for label in labels if str(inactive_parent.pk) in label]
    assert matching
    assert any("[Pasif]" in label for label in matching)
    assert any("VISIBLE-INACTIVE" in label for label in matching)
    assert any("(VIN)" in label for label in matching)


def test_edit_parent_selector_excludes_self_and_all_descendants(app_client):
    root = Category.objects.create(name="SEL-ROOT")
    child = Category.objects.create(name="SEL-CHILD", parent=root)
    grandchild = Category.objects.create(name="SEL-GRAND", parent=child)
    other = Category.objects.create(name="SEL-OTHER")
    user = _role_user(ADMIN_MANAGER, "selector-exclude")
    _login(app_client, user)

    form = app_client.get(
        reverse("catalog:category-update", args=[root.pk])
    ).context["form"]
    choice_ids = _parent_choice_ids(form)
    assert str(root.pk) not in choice_ids
    assert str(child.pk) not in choice_ids
    assert str(grandchild.pk) not in choice_ids
    assert str(other.pk) in choice_ids

    child_form = app_client.get(
        reverse("catalog:category-update", args=[child.pk])
    ).context["form"]
    child_ids = _parent_choice_ids(child_form)
    assert str(child.pk) not in child_ids
    assert str(grandchild.pk) not in child_ids
    assert str(root.pk) in child_ids
    assert str(other.pk) in child_ids


def test_create_parent_selector_includes_existing_categories(app_client):
    existing = Category.objects.create(name="EXISTING-PARENT-CHOICE")
    user = _role_user(ADMIN_MANAGER, "create-selector")
    _login(app_client, user)
    form = app_client.get("/catalog/categories/new/").context["form"]
    assert str(existing.pk) in _parent_choice_ids(form)
    empty_labels = [
        label
        for value, label in form.fields["parent"].widget.choices
        if value in ("", None)
    ]
    assert empty_labels
    assert "Üst düzey" in empty_labels[0]


# ---------------------------------------------------------------------------
# List / search / filter / pagination
# ---------------------------------------------------------------------------


def test_q_by_name(app_client):
    Category.objects.create(name="UNIQUE-NAME-HIT")
    Category.objects.create(name="UNIQUE-NAME-MISS")
    user = _role_user(TECHNICIAN, "q-name")
    _login(app_client, user)
    content = app_client.get(
        "/catalog/categories/", {"q": "UNIQUE-NAME-HIT"}
    ).content.decode()
    assert "UNIQUE-NAME-HIT" in content
    assert "UNIQUE-NAME-MISS" not in content


def test_q_by_code(app_client):
    Category.objects.create(name="CODE-SEARCH-A", code="ZZ-CODE-HIT")
    Category.objects.create(name="CODE-SEARCH-B", code="ZZ-CODE-MISS")
    user = _role_user(TECHNICIAN, "q-code")
    _login(app_client, user)
    content = app_client.get(
        "/catalog/categories/", {"q": "ZZ-CODE-HIT"}
    ).content.decode()
    assert "CODE-SEARCH-A" in content
    assert "CODE-SEARCH-B" not in content


def test_q_is_case_insensitive(app_client):
    Category.objects.create(name="CaseFold Target", code="CfCode")
    user = _role_user(TECHNICIAN, "q-case")
    _login(app_client, user)
    name_hit = app_client.get(
        "/catalog/categories/", {"q": "casefold target"}
    ).content.decode()
    code_hit = app_client.get(
        "/catalog/categories/", {"q": "cfcode"}
    ).content.decode()
    assert "CaseFold Target" in name_hit
    assert "CaseFold Target" in code_hit


def test_active_inactive_and_all_filters(app_client):
    Category.objects.create(name="FILTER-ACTIVE", active=True)
    Category.objects.create(name="FILTER-INACTIVE", active=False)
    user = _role_user(TECHNICIAN, "status-filter")
    _login(app_client, user)

    active_content = app_client.get(
        "/catalog/categories/", {"q": "FILTER-", "status": "active"}
    ).content.decode()
    assert "FILTER-ACTIVE" in active_content
    assert "FILTER-INACTIVE" not in active_content

    inactive_content = app_client.get(
        "/catalog/categories/", {"q": "FILTER-", "status": "inactive"}
    ).content.decode()
    assert "FILTER-INACTIVE" in inactive_content
    assert "FILTER-ACTIVE" not in inactive_content

    all_content = app_client.get(
        "/catalog/categories/", {"q": "FILTER-", "status": "all"}
    ).content.decode()
    assert "FILTER-ACTIVE" in all_content
    assert "FILTER-INACTIVE" in all_content


def test_unknown_status_falls_back_to_all(app_client):
    Category.objects.create(name="UNKNOWN-STATUS-ACTIVE", active=True)
    Category.objects.create(name="UNKNOWN-STATUS-INACTIVE", active=False)
    user = _role_user(TECHNICIAN, "status-unknown")
    _login(app_client, user)
    response = app_client.get(
        "/catalog/categories/",
        {"q": "UNKNOWN-STATUS", "status": "nope"},
    )
    assert response.status_code == 200
    assert response.context["status"] == "all"
    content = response.content.decode()
    assert "UNKNOWN-STATUS-ACTIVE" in content
    assert "UNKNOWN-STATUS-INACTIVE" in content
    assert 'value="all" selected' in content or "value=\"all\" selected" in content


def test_q_and_status_combined(app_client):
    Category.objects.create(name="COMBO-ALPHA", code="COMBO-1", active=True)
    Category.objects.create(name="COMBO-ALPHA-OFF", code="COMBO-2", active=False)
    Category.objects.create(name="OTHER-ACTIVE", code="COMBO-3", active=True)
    user = _role_user(TECHNICIAN, "combo-filter")
    _login(app_client, user)
    content = app_client.get(
        "/catalog/categories/",
        {"q": "COMBO-ALPHA", "status": "inactive"},
    ).content.decode()
    assert "COMBO-ALPHA-OFF" in content
    assert "OTHER-ACTIVE" not in content
    assert "COMBO-1" not in content


def test_stable_ordering_by_name_then_id(app_client):
    later = Category.objects.create(name="ORDER-SAME")
    earlier = Category.objects.create(name="ORDER-SAME")
    other = Category.objects.create(name="ORDER-AAA")
    user = _role_user(TECHNICIAN, "ordering")
    _login(app_client, user)
    response = app_client.get("/catalog/categories/", {"q": "ORDER-"})
    names_and_ids = [
        (category.name, category.id) for category in response.context["categories"]
    ]
    assert names_and_ids == sorted(names_and_ids, key=lambda item: (item[0], item[1]))
    assert ("ORDER-AAA", other.id) in names_and_ids
    same_name_ids = [pk for name, pk in names_and_ids if name == "ORDER-SAME"]
    assert same_name_ids == sorted([earlier.id, later.id])


def test_pagination_and_query_params_preserved(app_client):
    for index in range(CATEGORY_LIST_PAGE_SIZE + 5):
        Category.objects.create(
            name=f"ZZZ-SAYFA-{index:03d}",
            code=f"PG-{index:03d}",
            active=True,
        )
    user = _role_user(TECHNICIAN, "pagination")
    _login(app_client, user)
    page1 = app_client.get(
        "/catalog/categories/",
        {"q": "ZZZ-SAYFA", "status": "active"},
    )
    assert page1.status_code == 200
    assert page1.context["paginator"].count == CATEGORY_LIST_PAGE_SIZE + 5
    assert page1.context["page_obj"].number == 1
    assert len(page1.context["categories"]) == CATEGORY_LIST_PAGE_SIZE
    content = page1.content.decode()
    assert "Sonraki" in content
    assert "q=ZZZ-SAYFA" in content
    assert "status=active" in content

    page2 = app_client.get(
        "/catalog/categories/",
        {"q": "ZZZ-SAYFA", "status": "active", "page": "2"},
    )
    assert page2.status_code == 200
    assert page2.context["page_obj"].number == 2
    assert len(page2.context["categories"]) == 5
    page2_content = page2.content.decode()
    assert "q=ZZZ-SAYFA" in page2_content
    assert "status=active" in page2_content
    assert page2.context["q"] == "ZZZ-SAYFA"
    assert page2.context["status"] == "active"


def test_rendered_query_text_is_safely_escaped(app_client):
    user = _role_user(TECHNICIAN, "xss-q")
    _login(app_client, user)
    payload = "<script>alert(1)</script>"
    response = app_client.get("/catalog/categories/", {"q": payload})
    assert response.status_code == 200
    content = response.content.decode()
    assert payload not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content


# ---------------------------------------------------------------------------
# Deactivate / reactivate
# ---------------------------------------------------------------------------


def test_deactivate_only_changes_expected_state(app_client):
    parent = Category.objects.create(name="STATE-PARENT")
    category = Category.objects.create(
        name="STATE-TARGET",
        code="ST-1",
        parent=parent,
        active=True,
    )
    child = Category.objects.create(name="STATE-CHILD", parent=category, active=True)
    unit = UnitOfMeasure.objects.create(code="ST-UNIT", name="State Unit")
    material = Material.objects.create(
        material_code="ST-MAT",
        name="State Material",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    snapshot = {
        "name": category.name,
        "code": category.code,
        "parent_id": category.parent_id,
        "created_at": category.created_at,
    }
    user = _role_user(ADMIN_MANAGER, "deactivate-ok")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:category-deactivate", args=[category.pk]),
        follow=True,
    )
    assert response.status_code == 200
    assert "Kategori pasifleştirildi." in response.content.decode()
    category.refresh_from_db()
    child.refresh_from_db()
    parent.refresh_from_db()
    material.refresh_from_db()
    assert category.active is False
    assert category.name == snapshot["name"]
    assert category.code == snapshot["code"]
    assert category.parent_id == snapshot["parent_id"]
    assert category.created_at == snapshot["created_at"]
    assert child.active is True
    assert child.parent_id == category.pk
    assert parent.active is True
    assert material.category_id == category.pk


def test_reactivate_only_changes_expected_state(app_client):
    category = Category.objects.create(name="REACTIVATE-TARGET", active=False)
    child = Category.objects.create(
        name="REACTIVATE-CHILD",
        parent=category,
        active=False,
    )
    user = _role_user(ADMIN_MANAGER, "reactivate-ok")
    _login(app_client, user)
    response = app_client.post(
        reverse("catalog:category-reactivate", args=[category.pk]),
        follow=True,
    )
    assert response.status_code == 200
    assert "Kategori aktifleştirildi." in response.content.decode()
    category.refresh_from_db()
    child.refresh_from_db()
    assert category.active is True
    assert child.active is False
    assert child.parent_id == category.pk


def test_repeated_deactivate_and_reactivate_are_safe(app_client):
    category = Category.objects.create(name="IDEMPOTENT-STATE", active=True)
    user = _role_user(ADMIN_MANAGER, "idempotent-state")
    _login(app_client, user)
    first = app_client.post(reverse("catalog:category-deactivate", args=[category.pk]))
    second = app_client.post(reverse("catalog:category-deactivate", args=[category.pk]))
    assert first.status_code == 302
    assert second.status_code == 302
    category.refresh_from_db()
    assert category.active is False
    assert Category.objects.filter(pk=category.pk).count() == 1

    third = app_client.post(reverse("catalog:category-reactivate", args=[category.pk]))
    fourth = app_client.post(reverse("catalog:category-reactivate", args=[category.pk]))
    assert third.status_code == 302
    assert fourth.status_code == 302
    category.refresh_from_db()
    assert category.active is True


def test_technician_list_hides_write_actions_but_server_is_authoritative(app_client):
    Category.objects.create(name="TECH-VISIBLE")
    user = _role_user(TECHNICIAN, "tech-ux")
    _login(app_client, user)
    content = app_client.get("/catalog/categories/").content.decode()
    assert "TECH-VISIBLE" in content
    assert "Yeni kategori" not in content
    assert "Düzenle" not in content
    assert "Pasifleştir" not in content
    assert "Aktifleştir" not in content
