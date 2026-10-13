import pytest
from app.core.config import get_settings, Settings


def test_skip_disabled(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'allow_development_login', False)
    assert client.get('/api/auth/options').json() == {'development_login': False}
    assert client.post('/api/auth/development-login').status_code == 404


def test_skip_real_session_and_permissions(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'allow_development_login', True)
    response = client.post('/api/auth/development-login')
    assert response.status_code == 200
    assert response.json()['role'] == 'engineer'
    assert 'password_hash' not in response.json()
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert client.get('/api/dashboard/summary').status_code == 200
    assert client.get('/api/settings/users').status_code == 403
    token = client.cookies.get('recon_session')
    assert client.post('/api/auth/logout').status_code == 204
    assert client.get('/api/auth/me', cookies={'recon_session': token}).status_code == 401
    assert client.post('/api/auth/development-login').status_code == 200


def test_skip_rejects_remote_browser(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'allow_development_login', True)
    monkeypatch.setattr(settings, 'allowed_origins', 'https://remote.example')
    assert client.post('/api/auth/development-login', headers={'Origin': 'https://remote.example'}).status_code == 403


def test_skip_rejects_staging(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'allow_development_login', True)
    monkeypatch.setattr(get_settings(), 'app_env', 'staging')
    assert client.post('/api/auth/development-login').status_code == 404


def test_production_rejects_skip():
    values = get_settings().model_dump()
    values.update(app_env='production', cookie_secure=True, allowed_origins='https://example.com', allow_development_login=True)
    with pytest.raises(ValueError, match='Development login must be disabled'):
        Settings(_env_file=None, **values)
