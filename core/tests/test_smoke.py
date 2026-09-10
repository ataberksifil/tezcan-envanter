from urllib.parse import urlparse

import pytest
from django.db import connection


def test_root_page_requires_login(client):
    response = client.get('/', HTTP_HOST='localhost')
    assert response.status_code == 302
    assert urlparse(response.url).path == '/accounts/login/'


@pytest.mark.django_db
def test_postgresql_backend_and_test_database_name():
    assert connection.vendor == 'postgresql'

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        database_name = cursor.fetchone()[0]

    assert database_name == 'test_tezcan_envanter'
