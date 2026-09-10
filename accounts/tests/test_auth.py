from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model, SESSION_KEY
from django.test import Client
from django.urls import NoReverseMatch, reverse

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _create_ordinary_user(username="workshop-user", *, password=PASSWORD):
    user_model = get_user_model()
    user = user_model.objects.create_user(username=username, password=password)
    assert not user.is_superuser
    assert not user.is_staff
    return user


def _csrf_token(response):
    match = re.search(
        r'name="csrfmiddlewaretoken" value="([^"]+)"',
        response.content.decode(),
    )
    assert match is not None
    return match.group(1)


def test_login_and_logout_named_urls():
    assert reverse("login") == "/accounts/login/"
    assert reverse("logout") == "/accounts/logout/"
    assert reverse("home") == "/"


def test_login_redirect_settings():
    assert settings.LOGIN_URL == "login"
    assert settings.LOGIN_REDIRECT_URL == "home"
    assert settings.LOGOUT_REDIRECT_URL == "login"


def test_anonymous_root_redirects_to_login(app_client):
    response = app_client.get("/")
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == ["/"]


def test_anonymous_login_page_is_accessible(app_client):
    response = app_client.get("/accounts/login/")
    assert response.status_code == 200
    template_names = [template.name for template in response.templates]
    assert "registration/login.html" in template_names
    content = response.content.decode()
    assert 'name="username"' in content
    assert 'name="password"' in content
    assert 'name="csrfmiddlewaretoken"' in content


def test_valid_user_can_log_in(app_client):
    user = _create_ordinary_user("login-ok-user")
    response = app_client.post(
        "/accounts/login/",
        {"username": user.username, "password": PASSWORD},
    )
    assert response.status_code == 302
    assert SESSION_KEY in app_client.session
    assert int(app_client.session[SESSION_KEY]) == user.pk


def test_invalid_credentials_stay_on_login_with_error(app_client):
    _create_ordinary_user("login-bad-user")
    response = app_client.post(
        "/accounts/login/",
        {"username": "login-bad-user", "password": "wrong-password"},
    )
    assert response.status_code == 200
    template_names = [template.name for template in response.templates]
    assert "registration/login.html" in template_names
    form = response.context["form"]
    assert form.errors
    assert SESSION_KEY not in app_client.session


def test_successful_login_redirects_to_home(app_client):
    user = _create_ordinary_user("login-home-user")
    response = app_client.post(
        "/accounts/login/",
        {"username": user.username, "password": PASSWORD},
    )
    assert response.status_code == 302
    assert response.url == "/"


def test_authenticated_home_returns_200(app_client):
    user = _create_ordinary_user("home-ok-user")
    app_client.force_login(user)
    response = app_client.get("/")
    assert response.status_code == 200
    template_names = [template.name for template in response.templates]
    assert "core/home.html" in template_names
    assert "base.html" in template_names
    content = response.content.decode()
    assert "Oturumunuz açık." in content
    assert user.username in content


def test_logout_via_post_logs_user_out(app_client):
    user = _create_ordinary_user("logout-post-user")
    app_client.force_login(user)
    response = app_client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response.url == "/accounts/login/"
    assert SESSION_KEY not in app_client.session
    home = app_client.get("/")
    assert home.status_code == 302
    assert urlparse(home.url).path == "/accounts/login/"


def test_get_logout_is_not_allowed(app_client):
    user = _create_ordinary_user("logout-get-user")
    app_client.force_login(user)
    response = app_client.get("/accounts/logout/")
    assert response.status_code == 405
    assert SESSION_KEY in app_client.session


def test_anonymous_logout_post_is_safe(app_client):
    response = app_client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response.url == "/accounts/login/"
    assert SESSION_KEY not in app_client.session


def test_anonymous_get_logout_is_safe(app_client):
    response = app_client.get("/accounts/logout/")
    assert response.status_code == 405


def test_authenticated_shell_contains_csrf_logout_form(app_client):
    user = _create_ordinary_user("logout-form-user")
    app_client.force_login(user)
    response = app_client.get("/")
    content = response.content.decode()
    assert 'class="app-logout-form' in content or "app-logout-form" in content
    assert 'method="post"' in content
    assert "/accounts/logout/" in content
    assert 'name="csrfmiddlewaretoken"' in content
    token = _csrf_token(response)
    assert token


def test_logout_post_requires_csrf_token():
    user = _create_ordinary_user("logout-csrf-user")
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.defaults["HTTP_HOST"] = "localhost"
    csrf_client.force_login(user)

    forbidden = csrf_client.post("/accounts/logout/")
    assert forbidden.status_code == 403
    assert SESSION_KEY in csrf_client.session

    home = csrf_client.get("/")
    allowed = csrf_client.post(
        "/accounts/logout/",
        {"csrfmiddlewaretoken": _csrf_token(home)},
    )
    assert allowed.status_code == 302
    assert SESSION_KEY not in csrf_client.session


@pytest.mark.parametrize(
    "url_name",
    (
        "register",
        "signup",
        "password_reset",
        "password_reset_done",
        "password_change",
    ),
)
def test_registration_and_password_reset_urls_are_not_exposed(url_name):
    with pytest.raises(NoReverseMatch):
        reverse(url_name)


def test_login_page_has_no_registration_or_password_reset_flow(app_client):
    response = app_client.get("/accounts/login/")
    content = response.content.decode().lower()
    assert "kayıt ol" not in content
    assert "üye ol" not in content
    assert "/register" not in content
    assert "/signup" not in content
    assert "password_reset" not in content
    assert "şifre sıfırla" not in content
    assert "parola sıfırla" not in content


def test_ordinary_non_superuser_can_authenticate(app_client):
    user = _create_ordinary_user("ordinary-user")
    assert not user.is_superuser
    assert not user.is_staff
    response = app_client.post(
        "/accounts/login/",
        {"username": user.username, "password": PASSWORD},
        follow=True,
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.pk == user.pk
    assert not response.wsgi_request.user.is_superuser


def test_role_group_setup_is_not_required_to_authenticate(app_client):
    user = _create_ordinary_user("no-role-user")
    assert user.groups.count() == 0
    response = app_client.post(
        "/accounts/login/",
        {"username": user.username, "password": PASSWORD},
    )
    assert response.status_code == 302
    assert response.url == "/"
    home = app_client.get("/")
    assert home.status_code == 200
    assert user.username in home.content.decode()
