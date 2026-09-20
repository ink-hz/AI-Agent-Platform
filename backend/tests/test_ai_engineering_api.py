from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.control_plane.models import AuthContext, Role
from test_dingtalk_auth_api import FakeAuth, _app

PREFIX = '/api/v1/ai-engineering'
CONTENT = ['', '/documents/overview', '/assets/panorama.svg', '/assets/panorama.png']


def client_for(tmp_path, monkeypatch, role=Role.PLATFORM_ADMIN):
    auth = FakeAuth()
    auth.context = AuthContext(uuid4(), role, uuid4(), False)
    client = TestClient(_app(tmp_path, monkeypatch, auth), base_url='https://agent.example.test')
    return client, auth


def assert_private(response):
    assert 'no-store' in response.headers['cache-control']
    assert 'private' in response.headers['cache-control']


@pytest.mark.parametrize('role', list(Role))
def test_existing_platform_management_roles(tmp_path, monkeypatch, role):
    client, auth = client_for(tmp_path, monkeypatch, role)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    permitted = role in {Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER}
    assert client.get(PREFIX + '/access').json() == {'allowed': permitted}
    for suffix in CONTENT:
        response = client.get(PREFIX + suffix, headers={'X-Role': 'platform_admin'})
        assert response.status_code == (200 if permitted else 403)
        assert_private(response)


@pytest.mark.parametrize('role', [Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER])
def test_role_revocation_with_same_session(tmp_path, monkeypatch, role):
    client, auth = client_for(tmp_path, monkeypatch, role)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    assert client.get(PREFIX).status_code == 200
    auth.context = AuthContext(auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False)
    assert client.get(PREFIX + '/access').json() == {'allowed': False}
    for suffix in CONTENT:
        assert client.get(PREFIX + suffix).status_code == 403
    # Shared identity remains available to independent /office/, /hr/ apps.
    assert client.get('/api/v1/account').status_code == 200
    assert client.get('/hr/').status_code == 200
    from app.control_plane.auth import validate_return_path
    for path in ['/office/', '/hr/', '/voc/', '/fae/']:
        assert validate_return_path(path, route_prefix='/') == path


def test_session_required_even_for_admin(tmp_path, monkeypatch):
    client, auth = client_for(tmp_path, monkeypatch)
    for suffix in ['/access', *CONTENT]:
        response = client.get(PREFIX + suffix)
        assert response.status_code == 401
        assert_private(response)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    auth.revoked = True
    assert client.get(PREFIX + '/documents/overview').status_code == 401


def test_fixed_content_paths_and_sources(tmp_path, monkeypatch):
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    manifest = client.get(PREFIX).json()
    assert len(manifest['documents']) == 6
    for doc in manifest['documents']:
        payload = client.get(PREFIX + '/documents/' + doc['slug']).json()
        assert payload['markdown']
        assert payload['slug'] == doc['slug']
    for path in ['/documents/unknown', '/assets/source-manifest.json', '/assets/%2e%2e%2fsource-manifest.json', '/documents/%2fetc%2fpasswd']:
        response = client.get(PREFIX + path)
        assert response.status_code in {403, 404}
        assert 'LOGIN SHELL' not in response.text
        assert_private(response)
    assert client.get('/ai-engineering').status_code == 200
    assert client.get('/assets/panorama.svg').status_code != 200


@pytest.mark.parametrize('prefix', ['/', '/_preview/dingtalk-r1/'])
def test_login_return_paths(prefix):
    from app.control_plane.auth import validate_return_path
    base = prefix.rstrip('/')
    for path in ['/brain', '/ai-engineering', '/ai-engineering?document=domains']:
        assert validate_return_path(base + path, route_prefix=prefix) == base + path
    for query in ['document=unknown', 'document=domains&document=reading', 'document=domains&x=1', 'document=%64omains']:
        with pytest.raises(ValueError):
            validate_return_path(base + '/ai-engineering?' + query, route_prefix=prefix)


def test_prefixed_content_uses_same_gate(tmp_path, monkeypatch):
    from app.control_plane.models import IdentityMode
    prefix = '/_preview/dingtalk-r1/'
    auth = FakeAuth(mode=IdentityMode.PREVIEW, prefix=prefix)
    client = TestClient(_app(tmp_path, monkeypatch, auth), base_url='https://agent.example.test')
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    assert client.get(prefix.rstrip('/') + PREFIX + '/documents/overview').status_code == 200
    auth.context = AuthContext(auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False)
    response = client.get(prefix.rstrip('/') + PREFIX + '/assets/panorama.svg')
    assert response.status_code == 403
    assert_private(response)


def test_content_snapshot_hashes():
    import hashlib
    from pathlib import Path
    content = Path(__file__).parents[1] / 'app/ai_engineering/content'
    manifest = json.loads((content / 'source-manifest.json').read_text())
    assert manifest['commit'] == '69a9de2168de6fa42b14d586587169487797e6f3'
    for filename, metadata in manifest['files'].items():
        assert hashlib.sha256((content / filename).read_bytes()).hexdigest() == metadata['sha256']


@pytest.mark.parametrize('prefix', ['/', '/_preview/dingtalk-r1/'])
def test_deep_link_shared_shell_reaches_login_without_private_content(tmp_path, monkeypatch, prefix):
    from app.control_plane.models import IdentityMode
    auth = FakeAuth(mode=IdentityMode.PREVIEW if prefix != '/' else IdentityMode.PRODUCTION, prefix=prefix)
    client = TestClient(_app(tmp_path, monkeypatch, auth), base_url='https://agent.example.test')
    for path in ['/brain', '/ai-engineering?document=domains']:
        response = client.get(prefix.rstrip('/') + path)
        assert response.status_code == 200
        assert 'LOGIN SHELL' in response.text
        assert 'no-store' in response.headers['cache-control']
    for suffix in CONTENT:
        assert client.get(prefix.rstrip('/') + PREFIX + suffix).status_code == 401
