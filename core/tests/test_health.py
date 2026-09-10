from unittest.mock import MagicMock, patch

import pytest
from django.db import OperationalError
from django.urls import reverse


@pytest.mark.django_db
def test_health_get_success(client):
    response = client.get('/health/', HTTP_HOST='localhost')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'database': 'ok'}


@pytest.mark.django_db
def test_health_db_failure(client):
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = OperationalError(
        'connection refused to host 127.0.0.1'
    )
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)

    mock_connection = MagicMock()
    mock_connection.cursor.return_value = mock_cursor

    with patch('core.views.connection', mock_connection):
        response = client.get('/health/', HTTP_HOST='localhost')

    assert response.status_code == 503
    assert response.json() == {
        'status': 'unavailable',
        'database': 'unavailable',
    }
    content = response.content.decode()
    assert 'connection refused' not in content
    assert '127.0.0.1' not in content
    assert 'SELECT' not in content


def test_health_post_method_not_allowed(client):
    response = client.post('/health/', HTTP_HOST='localhost')
    assert response.status_code == 405


def test_health_url_reverse():
    assert reverse('health') == '/health/'


@pytest.mark.django_db
def test_health_executes_select_one_without_exposing_details(client):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)

    mock_connection = MagicMock()
    mock_connection.cursor.return_value = mock_cursor

    with patch('core.views.connection', mock_connection):
        response = client.get('/health/', HTTP_HOST='localhost')

    mock_cursor.execute.assert_called_once_with('SELECT 1')
    assert response.status_code == 200
    content = response.content.decode()
    assert 'SELECT' not in content
