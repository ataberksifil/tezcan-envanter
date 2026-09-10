import pytest
from django.db import connection


def test_root_page(client):
    response = client.get('/', HTTP_HOST='localhost')
    assert response.status_code == 200

    template_names = [template.name for template in response.templates]
    assert 'core/home.html' in template_names
    assert 'base.html' in template_names

    content = response.content.decode()
    assert 'Elektrik Atölyesi Envanter Uygulaması' in content
    assert 'envanter ve malzeme takibi' in content


@pytest.mark.django_db
def test_postgresql_backend_and_test_database_name():
    assert connection.vendor == 'postgresql'

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        database_name = cursor.fetchone()[0]

    assert database_name == 'test_tezcan_envanter'
