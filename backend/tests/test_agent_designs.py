import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.agent_designs.routes import build_agent_designs_router, CONTENT
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from test_agent_brain_api import FakeAuth, NoManagementGrants, _credentials


def client_for(role):
    auth = FakeAuth(AuthContext(uuid4(), role, uuid4(), False))
    auth.hard_stale_audit = lambda *_: None
    app = FastAPI()
    app.include_router(build_agent_designs_router())
    app.add_middleware(IdentitySecurityMiddleware, auth=auth, public_assets=frozenset(),
                      authorization=AuthorizationService(NoManagementGrants()), routes=tuple(app.router.routes))
    return TestClient(app), auth

@pytest.mark.parametrize('slug', ['hr', 'fae'])
@pytest.mark.parametrize('role', [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_registered_design_is_readable_only_with_management_identity(role, slug):
    client, auth = client_for(role)
    index = client.get('/api/v1/manage/agent-designs', **_credentials(auth))
    assert index.status_code == 200
    assert index.json()['documents'][0]['slug'] == 'hr'
    response = client.get('/api/v1/manage/agent-designs/' + slug, **_credentials(auth))
    assert response.status_code == 200
    assert 'private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control']
    data = response.json()
    assert data['markdown'].encode() == (CONTENT / (slug + '.md')).read_bytes()
    assert hashlib.sha256(data['markdown'].encode()).hexdigest() == data['source']['sha256']
    assert '目标架构，未全部实施' in data['markdown']
    assert client.get('/api/v1/manage/agent-designs', cookies={}).status_code == 401

@pytest.mark.parametrize('role', [Role.MEMBER, Role.MANAGEMENT_VIEWER])
def test_non_management_cannot_read_design_content(role):
    client, auth = client_for(role)
    for suffix in ['', '/hr', '/fae']:
        response = client.get('/api/v1/manage/agent-designs' + suffix, **_credentials(auth))
        assert response.status_code == 403
        assert 'Hannah' not in response.text
        assert 'no-store' in response.headers['cache-control']

def test_unknown_design_and_integrity_failure_are_closed(monkeypatch, tmp_path):
    client, auth = client_for(Role.PLATFORM_OWNER)
    assert client.get('/api/v1/manage/agent-designs/unknown', **_credentials(auth)).status_code == 404
    from app.agent_designs import routes
    (tmp_path / 'index.json').write_bytes((CONTENT / 'index.json').read_bytes())
    (tmp_path / 'hr.md').write_text('bad copy')
    monkeypatch.setattr(routes, 'CONTENT', tmp_path)
    response = client.get('/api/v1/manage/agent-designs/hr', **_credentials(auth))
    assert response.status_code == 503
    assert 'bad copy' not in response.text and str(tmp_path) not in response.text


def test_design_reader_is_a_safe_login_return_path():
    from app.control_plane.auth import validate_return_path
    assert validate_return_path('/admin/agent-designs', route_prefix='') == '/admin/agent-designs'
