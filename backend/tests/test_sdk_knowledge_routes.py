from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.sdk_knowledge.routes import build_sdk_knowledge_router
from app.sdk_knowledge.tools import SdkKnowledgeTools
from app.sdk_workbench.plan_service import PlanService


class Corpus:
    def search(self, **query):
        return {'baseline_id': 'fixture', 'items': [{'page_id': 'KB-ONE'}], 'query': query}

    def page(self, page_id):
        return {'page_id': page_id} if page_id == 'KB-ONE' else None


class Service:
    corpus = Corpus()

    def inspect_change(self, **kwargs):
        return {'observation': kwargs, 'affected_pages': [], 'runtime_validation': 'not_run'}


def application(*, allowed=True, anonymous=False, owner=False):
    app = FastAPI()
    members = SimpleNamespace(role='viewer' if allowed else None)
    members.role_for = lambda actor: members.role
    app.state.sdk_plan_service = PlanService(members, None)
    app.state.sdk_knowledge_service = Service()
    app.state.sdk_knowledge_source_authorizer = lambda actor, repository: members.role is not None

    @app.middleware('http')
    async def identity(request, call_next):
        if not anonymous:
            request.state.auth_context = AuthContext(uuid4(), Role.PLATFORM_OWNER if owner else Role.MEMBER, uuid4(), False)
        return await call_next(request)

    app.include_router(build_sdk_knowledge_router())
    return app, members


@pytest.mark.asyncio
async def test_all_reads_enforce_current_sdk_permission_and_private_responses():
    paths = ['/search?q=版本', '/pages/KB-ONE', '/changes/inspect']
    change = {'repository': 'SDK', 'base_commit': 'a' * 40, 'target_commit': 'b' * 40}
    for anonymous, allowed, expected in [(True, True, 401), (False, False, 403), (False, True, 200)]:
        app, _ = application(anonymous=anonymous, allowed=allowed)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            for path in paths:
                res = await client.post('/api/v1/sdk-knowledge' + path, json=change) if path.endswith('inspect') else await client.get('/api/v1/sdk-knowledge' + path)
                assert res.status_code == expected
                assert res.headers['cache-control'] == 'private, no-store'
    app, members = application()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/v1/sdk-knowledge/search')).status_code == 200
        members.role = None
        assert (await client.get('/api/v1/sdk-knowledge/search')).status_code == 403


@pytest.mark.asyncio
async def test_input_limits_missing_pages_and_missing_model_are_explicit(monkeypatch):
    app, _ = application(owner=True)
    for key in ('SDK_KNOWLEDGE_MODEL_ID', 'SDK_KNOWLEDGE_MODEL_BASE_URL', 'SDK_KNOWLEDGE_MODEL_KEY_FILE'):
        monkeypatch.delenv(key, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/v1/sdk-knowledge/pages/KB-MISSING')).status_code == 404
        for query in ('?limit=101', '?q=' + 'x' * 201):
            assert (await client.get('/api/v1/sdk-knowledge/search' + query)).status_code == 422
        body = {'repository': 'SDK', 'base_commit': 'HEAD', 'target_commit': 'b' * 40}
        assert (await client.post('/api/v1/sdk-knowledge/changes/inspect', json=body)).status_code == 422
        body['base_commit'] = 'a' * 40
        body['objective'] = '分析'
        res = await client.post('/api/v1/sdk-knowledge/changes/analyze', json=body)
        assert res.status_code == 503 and 'not_configured' in res.text
        assert '/Users/' not in res.text


def test_tools_recheck_authorization_each_call_and_publish_bounded_schemas():
    allowed = [True]
    tools = SdkKnowledgeTools(Service(), authorize=lambda: allowed[0])
    assert {row['name'] for row in tools.definitions} == {
        'sdk_knowledge_search', 'sdk_knowledge_page', 'sdk_knowledge_inspect_change'}
    assert tools.call('sdk_knowledge_page', {'page_id': 'KB-ONE'}) == {'page_id': 'KB-ONE'}
    allowed[0] = False
    with pytest.raises(PermissionError):
        tools.call('sdk_knowledge_page', {'page_id': 'KB-ONE'})


@pytest.mark.asyncio
async def test_knowledge_read_grant_does_not_implicitly_grant_raw_repository_diffs():
    app, _ = application()
    del app.state.sdk_knowledge_source_authorizer
    change = {'repository': 'SDK', 'base_commit': 'a' * 40, 'target_commit': 'b' * 40}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/v1/sdk-knowledge/search')).status_code == 200
        assert (await client.post('/api/v1/sdk-knowledge/changes/inspect', json=change)).status_code == 403
    app, _ = application(owner=True)
    del app.state.sdk_knowledge_source_authorizer
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/v1/sdk-knowledge/changes/inspect', json=change)).status_code == 200
    tools = SdkKnowledgeTools(Service(), authorize=lambda: True)
    with pytest.raises(PermissionError):
        tools.call('sdk_knowledge_inspect_change', change)


def test_application_route_allowlist_keeps_fine_sdk_permissions_in_the_router():
    from app.control_plane.authorization import _SDK_WORKBENCH_ROUTES
    assert {('GET', '/api/v1/sdk-knowledge/search'), ('GET', '/api/v1/sdk-knowledge/pages/{page_id}'),
            ('POST', '/api/v1/sdk-knowledge/changes/inspect'),
            ('POST', '/api/v1/sdk-knowledge/changes/analyze')}.issubset(_SDK_WORKBENCH_ROUTES)


def test_tool_authorization_failures_do_not_leak_policy_errors():
    def unavailable():
        raise RuntimeError('private-policy-connection-string')

    tools = SdkKnowledgeTools(Service(), authorize=unavailable)
    with pytest.raises(PermissionError, match='sdk_authorization_unavailable') as error:
        tools.call('sdk_knowledge_page', {'page_id': 'KB-ONE'})
    assert 'private-policy' not in str(error.value)


def test_real_application_middleware_requires_session_origin_and_csrf(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_dingtalk_auth_api import FakeAuth, _app

    auth = FakeAuth()
    auth.context = AuthContext(auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False)
    app = _app(tmp_path, monkeypatch, auth)
    members = SimpleNamespace(role='viewer')
    members.role_for = lambda actor: members.role
    app.state.sdk_plan_service = PlanService(members, None)
    app.state.sdk_knowledge_service = Service()
    app.state.sdk_knowledge_source_authorizer = lambda actor, repository: members.role is not None
    client = TestClient(app)
    change = {'repository': 'SDK', 'base_commit': 'a' * 40, 'target_commit': 'b' * 40}
    assert client.get('/api/v1/sdk-knowledge/search').status_code == 401
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    client.cookies.set(auth.csrf_cookie_name, auth.csrf)
    assert client.get('/api/v1/sdk-knowledge/search').status_code == 200
    assert client.post('/api/v1/sdk-knowledge/changes/inspect', json=change).status_code == 403
    headers = {'Origin': auth.public_base_url, 'X-CSRF-Token': auth.csrf}
    assert client.post('/api/v1/sdk-knowledge/changes/inspect', json=change, headers=headers).status_code == 200
    members.role = None
    assert client.post('/api/v1/sdk-knowledge/changes/inspect', json=change, headers=headers).status_code == 403
    auth.revoked = True
    assert client.get('/api/v1/sdk-knowledge/search').status_code == 401
