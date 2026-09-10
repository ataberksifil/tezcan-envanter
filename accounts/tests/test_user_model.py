import pytest
from django.conf import settings
from django.contrib.auth import get_user_model


def test_auth_user_model_setting():
    assert settings.AUTH_USER_MODEL == 'accounts.User'


def test_get_user_model_resolves_to_accounts_user():
    user_model = get_user_model()
    assert user_model._meta.label == 'accounts.User'


@pytest.mark.django_db
def test_user_create_and_read_back():
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username='pytest-smoke-user',
        password='synthetic-test-password-only',
    )
    loaded = user_model.objects.get(pk=user.pk)
    assert loaded.username == 'pytest-smoke-user'
