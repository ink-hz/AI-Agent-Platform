from pathlib import Path
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.control_plane.authorization import AuthorizationService
from app.control_plane.auth import validate_return_path
from app.control_plane.middleware import IdentitySecurityMiddleware, is_public_request
from app.control_plane.models import AuthContext, Role
from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants, _app

BASE = '/api/v1/manufacturing'


def test_old_release_still_serves_legacy_only_workflow_probe_and_assets(bundle, monkeypatch, tmp_path):
    for key, value in {
        'PLATFORM_MANUFACTURING_WORKFLOW_DATABASE_PATH': str(tmp_path / 'domain.db'),
        'PLATFORM_MANUFACTURING_AUTHORITY_DATABASE_PATH': str(tmp_path / 'authority.db'),
        'PLATFORM_MANUFACTURING_DATASET_ID': 'test',
        'PLATFORM_MANUFACTURING_DATASET_MODE': 'test',
        'PLATFORM_MANUFACTURING_DATASET_LABEL': 'Test',
    }.items():
        monkeypatch.setenv(key, value)
    auth = FakeAuth()
    c = client(auth)
    c.cookies.set(auth.cookie_name, 'valid-cookie')
    probe = c.get(BASE + '/workflow/access')
    assert probe.status_code == 200
    assert probe.json()['workflow_available'] is False
    assert probe.json()['dataset'] is None
    assert probe.json()['legacy_access'] is True
    assert c.get(BASE + '/assets/manufacturing.js').status_code == 200
    assert c.get(BASE + '/workflow/me').status_code == 503


def bridge():
    from app.manufacturing import build_manufacturing_router
    return build_manufacturing_router()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / 'release'
    package = root / 'src/manufacturing_platform'
    assets = root / 'webui/dist'
    package.mkdir(parents=True)
    assets.mkdir(parents=True)
    (assets / 'manufacturing.js').write_text('window.OrbbecManufacturing = {}')
    (assets / 'manufacturing.css').write_text('.mf-app {}')
    # Host contract double: no installed sibling package is required by this suite.
    (package / '__init__.py').write_text('''from fastapi import APIRouter, Request\nfrom starlette.responses import Response\nfrom pathlib import Path\ndef build_router(*, database_path, assets_dir, authorize, release):\n r=APIRouter(prefix="/api/v1/manufacturing")\n @r.get("/access")\n def access(request: Request):\n  actor=authorize(request)\n  return {"allowed":True,"available":True,"can_edit":not actor["read_only"],"release":release}\n @r.get("/health")\n def health(request: Request):\n  return authorize(request)\n @r.put("/lines/{line_id}/stages/{stage_id}")\n def retired_write(request: Request):\n  return authorize(request,write=True)\n @r.get("/assets/{asset_name}")\n def asset(request: Request,asset_name:str):\n  authorize(request)\n  return Response((Path(assets_dir)/asset_name).read_bytes(),media_type="text/javascript")\n return r\n''')
    monkeypatch.setenv('PLATFORM_MANUFACTURING_ROOT', str(root))
    monkeypatch.setenv('PLATFORM_MANUFACTURING_DATABASE_PATH', str(tmp_path / 'db/workbench.sqlite3'))
    monkeypatch.setenv('PLATFORM_MANUFACTURING_RELEASE', 'test-release')
    return root


def client(auth=None, prefix='/'):
    app = FastAPI()
    app.include_router(bridge(), prefix=prefix.rstrip('/'))
    if auth:
        auth.hard_stale_audit = lambda *args: None
        app.add_middleware(IdentitySecurityMiddleware, auth=auth,
            authorization=AuthorizationService(_NoObservationGrants()),
            routes=app.routes, public_assets=frozenset())
    return TestClient(app, base_url='https://agent.example.test')


@pytest.mark.parametrize('role', list(Role))
def test_identity_roles_and_trusted_actor(bundle, role):
    auth=FakeAuth(); auth.context=AuthContext(uuid4(),role,uuid4(),False)
    c=client(auth)
    assert c.get(BASE+'/access').status_code == 401
    c.cookies.set(auth.cookie_name,'valid-cookie')
    response=c.get(BASE+'/health',headers={'X-User-ID':'forged','X-Display-Name':'forged'})
    assert response.status_code == (200 if role in {Role.PLATFORM_ADMIN,Role.PLATFORM_OWNER} else 403)
    assert response.headers['cache-control'] == 'private, no-store'
    if response.status_code == 200:
        assert response.json()['user_id'] == str(auth.context.internal_user_id)
        assert response.json()['display_name'] == str(auth.context.internal_user_id)


def test_no_identity_mode_never_bypasses_callback(bundle):
    c=client()
    assert c.get(BASE+'/health',headers={'X-Role':'platform_owner'}).status_code == 401


@pytest.mark.parametrize('headers', [{}, {'Origin':'https://agent.example.test'}, {'Origin':'https://evil.test','X-CSRF-Token':'csrf-value'}, {'Origin':'https://agent.example.test','X-CSRF-Token':'invalid'}])
def test_real_middleware_origin_csrf(bundle,headers):
    auth=FakeAuth(); c=client(auth); c.cookies.set(auth.cookie_name,'valid-cookie')
    assert c.post(BASE+'/workflow/handoffs',headers=headers,json={}).status_code == 403
    valid={'Origin':'https://agent.example.test','X-CSRF-Token':auth.csrf}
    # Accepted writes reach the (unconfigured) workflow boundary, not a 403.
    assert c.post(BASE+'/workflow/handoffs',headers=valid,json={}).status_code == 503


def test_retired_legacy_write_is_denied_and_hard_stale_is_read_only(bundle):
    # Phase 1 has no legacy-prefix business writes. Even when a release still
    # exposes a retired handler, the exact host allowlist denies it with valid CSRF.
    # Legacy-prefix write security stays covered by native access POST routes.
    auth=FakeAuth(); c=client(auth); c.cookies.set(auth.cookie_name,'valid-cookie')
    headers={'Origin':'https://agent.example.test','X-CSRF-Token':auth.csrf}
    assert c.put(BASE+'/lines/cameras/stages/S1',headers=headers,json={}).status_code == 403
    assert c.get(BASE+'/access').json()['can_edit'] is True
    auth.context=AuthContext(auth.context.internal_user_id,auth.context.role,auth.context.session_id,True)
    assert c.get(BASE+'/access').json()['can_edit'] is False
    assert c.get(BASE+'/health').status_code == 200


@pytest.mark.parametrize('prefix',['/','/_preview/dingtalk-r1/'])
def test_assets_private_and_whitelist(bundle,prefix):
    auth=FakeAuth(prefix=prefix); c=client(auth,prefix); base=prefix.rstrip('/')+BASE
    assert c.get(base+'/assets/manufacturing.js').status_code == 401
    c.cookies.set(auth.cookie_name,'valid-cookie')
    result=c.get(base+'/assets/manufacturing.js')
    assert result.status_code == 200
    assert result.headers['cache-control']=='private, no-store'
    for path in ['/unknown','/lines/cameras/extra']:
        assert c.get(base+path).status_code in {403,404}
    assert not is_public_request('GET',base+'/assets/manufacturing.js',prefix,frozenset({'manufacturing.js'}))


@pytest.mark.parametrize('config',['absent','bad','malformed'])
def test_disabled_is_safe_and_known_routes_preserved(tmp_path,monkeypatch,config):
    monkeypatch.delenv('PLATFORM_MANUFACTURING_ROOT',raising=False)
    monkeypatch.delenv('PLATFORM_MANUFACTURING_DATABASE_PATH',raising=False)
    if config!='absent':
        monkeypatch.setenv('PLATFORM_MANUFACTURING_ROOT', str(tmp_path))
        monkeypatch.setenv('PLATFORM_MANUFACTURING_DATABASE_PATH',str(tmp_path/'db.sqlite'))
        if config=='malformed':
            package=tmp_path/'src/manufacturing_platform'; package.mkdir(parents=True)
            (package/'__init__.py').write_text('raise RuntimeError("private internals")')
            assets=tmp_path/'webui/dist'; assets.mkdir(parents=True)
            for name in ['manufacturing.js','manufacturing.css']: (assets/name).write_text('')
    auth=FakeAuth(); c=client(auth); c.cookies.set(auth.cookie_name,'valid-cookie')
    access=c.get(BASE+'/access')
    assert access.status_code==200 and access.json()['available'] is False
    for path in ['/health','/handbook','/assets/manufacturing.js']:
        result=c.get(BASE+path)
        assert result.status_code==503
        assert 'private internals' not in result.text
    for path in ['/unknown','/catalog','/lines/cameras','/dashboard','/projects']:
        result=c.get(BASE+path)
        assert result.status_code in {403,404}
        assert 'private internals' not in result.text


@pytest.mark.parametrize('prefix',['/','/_preview/dingtalk-r1/'])
def test_login_return_and_shell(tmp_path,monkeypatch,prefix):
    for suffix in ['/manufacturing','/manufacturing/']:
        path=prefix.rstrip('/')+suffix
        assert validate_return_path(path,route_prefix=prefix)==path
        assert is_public_request('GET',path,prefix)
    for suffix in ['/manufacturing/secret','/manufacturing?x=1','/manufacturing%2f','/manufacturing//']:
        with pytest.raises(ValueError): validate_return_path(prefix.rstrip('/')+suffix,route_prefix=prefix)
    auth=FakeAuth(prefix=prefix)
    c=TestClient(_app(tmp_path,monkeypatch,auth),base_url='https://agent.example.test')
    assert c.get(prefix.rstrip('/')+'/manufacturing').status_code==200
    assert c.get(prefix.rstrip('/')+'/manufacturing/').status_code==200


def test_real_business_package(real_release, tmp_path, monkeypatch):
    auth=FakeAuth(); c=client(auth); c.cookies.set(auth.cookie_name,'valid-cookie')
    access=c.get(BASE+'/access')
    assert access.status_code==200 and access.json()['available'] is True
    assert access.json()['release']=='project-v2-test'
    assert c.get(BASE+'/health').json()=={'ready':True,'release':'project-v2-test'}
    headers={'Origin':auth.public_base_url,'X-CSRF-Token':auth.csrf,'X-User-ID':'forged'}
    # Retired phase-0 business routes are not reachable through the host.
    assert c.get(BASE+'/catalog').status_code==403
    assert c.put(BASE+'/lines/cameras/stages/S1',json={},headers=headers).status_code==403
    assert not (tmp_path/'real.sqlite3').exists()


def test_preview_shell_csp_allows_private_workbench_assets_for_spa_entry(tmp_path,monkeypatch):
    auth=FakeAuth(prefix='/_preview/dingtalk-r1/')
    c=TestClient(_app(tmp_path,monkeypatch,auth),base_url=auth.public_base_url)
    for path in ['/manufacturing','/login']:
        csp=c.get(auth.route_prefix.rstrip('/')+path).headers['content-security-policy']
        for directive in ['script-src','style-src']:
            value=next(part.strip() for part in csp.split(';') if part.strip().startswith(directive+' '))
            assert 'https://agent.example.test/_preview/dingtalk-r1/api/v1/manufacturing/assets/' in value
            assert 'https://agent.example.test/_preview/dingtalk-r1/assets/' in value


LEGACY_ROUTES = [('GET','/access'),('GET','/health'),('GET','/assets/{asset_name}'),('GET','/handbook')]
RETIRED_LEGACY_ROUTES = [
 ('GET','/catalog'),('GET','/lines/{line_id}'),('GET','/lines/{line_id}/stages/{stage_id}'),
 ('PUT','/lines/{line_id}/stages/{stage_id}'),('POST','/lines/{line_id}/stages/{stage_id}/tasks'),
 ('GET','/dashboard'),('GET','/projects'),('POST','/projects'),('GET','/projects/{project_id}'),
 ('PATCH','/projects/{project_id}'),('POST','/projects/{project_id}/tasks'),('GET','/project-tasks'),
 ('GET','/collaboration'),('POST','/systems'),('PATCH','/systems/{system_id}'),('POST','/authorities'),
 ('GET','/projects/{project_id}/controls'),('POST','/projects/{project_id}/packages'),
 ('POST','/projects/{project_id}/handoffs'),('POST','/projects/{project_id}/readiness'),
]


def test_legacy_allowlist_is_exact():
    from app.manufacturing_paths import MANUFACTURING_ROUTES, NATIVE_ACCESS_ROUTES
    assert MANUFACTURING_ROUTES == frozenset(LEGACY_ROUTES) | NATIVE_ACCESS_ROUTES


@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('method,path',RETIRED_LEGACY_ROUTES)
def test_retired_legacy_routes_are_denied(role,method,path):
    service=AuthorizationService(_NoObservationGrants())
    context=AuthContext(uuid4(),role,uuid4(),False)
    assert not service.decide(context,method,BASE+path,()).allowed


@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('method,path',LEGACY_ROUTES)
def test_central_exact_route_contract(role,method,path):
    service=AuthorizationService(_NoObservationGrants())
    context=AuthContext(uuid4(),role,uuid4(),False)
    result=service.decide(context,method,BASE+path,())
    allowed=role in {Role.PLATFORM_ADMIN,Role.PLATFORM_OWNER}
    assert result.allowed is allowed
    assert service.decide(None,method,BASE+path,()).status_code==401
    stale=AuthContext(context.internal_user_id,role,context.session_id,True)
    result=service.decide(stale,method,BASE+path,())
    assert result.status_code==(403 if not allowed else 200 if method=='GET' else 503)
    assert not service.decide(context,'DELETE',BASE+path,()).allowed
    assert not service.decide(context,method,BASE+path+'/extra',()).allowed


def test_loader_does_not_change_import_search_path(bundle):
    import sys
    before=tuple(sys.path)
    assert bridge()
    assert tuple(sys.path)==before


def test_disabled_namespace_stays_private_without_identity(monkeypatch):
    monkeypatch.delenv('PLATFORM_MANUFACTURING_ROOT',raising=False)
    c=client()
    for method,path in [('GET',''),('GET','/unknown'),('DELETE','/catalog'),('GET','/assets/manufacturing.js')]:
        result=c.request(method,BASE+path)
        assert result.status_code==401
        assert result.headers['cache-control']=='private, no-store'


def test_actual_create_app_native_routes_with_real_authorization(tmp_path,monkeypatch,bundle):
    auth=FakeAuth()
    app=_app(tmp_path,monkeypatch,auth)
    # create_app normally creates authorization from its DB-backed grants. This
    # network-free boundary substitutes only grants; the service remains real.
    middleware=next(value for value in app.user_middleware if value.cls is IdentitySecurityMiddleware)
    middleware.kwargs['authorization']=AuthorizationService(_NoObservationGrants())
    c=TestClient(app,base_url=auth.public_base_url)
    assert c.get(BASE+'/access').status_code==401
    c.cookies.set(auth.cookie_name,'valid-cookie')
    assert c.get(BASE+'/access').json()['available'] is True
    auth.context=AuthContext(auth.context.internal_user_id,Role.MEMBER,auth.context.session_id,False)
    assert c.get(BASE+'/catalog').status_code==403


@pytest.fixture
def real_release(tmp_path, monkeypatch):
    """Snapshot real source/assets/docs; never generate metadata in the sibling."""
    import hashlib
    import json
    import shutil

    configured = os.environ.get('TEST_MANUFACTURING_RELEASE_ROOT')
    if not configured:
        pytest.skip('set TEST_MANUFACTURING_RELEASE_ROOT for real sibling integration')
    source = Path(configured)
    root = tmp_path / 'real-release'
    for relative in ['src', 'webui/dist', 'docs']:
        shutil.copytree(source / relative, root / relative)
    files = {}
    for relative in ['docs/manufacturing-system-handbook.md', 'docs/workflows/manufacturing-process-overview.md']:
        files[relative] = {'updated_at': '2026-10-27T00:00:00+00:00',
            'sha256': hashlib.sha256((root / relative).read_bytes()).hexdigest()}
    (root / 'handbook-metadata.json').write_text(json.dumps({'files': files}))
    monkeypatch.setenv('PLATFORM_MANUFACTURING_ROOT', str(root))
    monkeypatch.setenv('PLATFORM_MANUFACTURING_DATABASE_PATH', str(tmp_path / 'real.sqlite3'))
    monkeypatch.setenv('PLATFORM_MANUFACTURING_RELEASE', 'project-v2-test')
    return root


def real_app_client(tmp_path, monkeypatch, auth):
    # _app calls actual main.create_app; only external grants/auth are doubles.
    auth.hard_stale_audit = lambda *args: None
    app = _app(tmp_path, monkeypatch, auth)
    middleware = next(value for value in app.user_middleware if value.cls is IdentitySecurityMiddleware)
    middleware.kwargs['authorization'] = AuthorizationService(_NoObservationGrants())
    c = TestClient(app, base_url=auth.public_base_url)
    return app, c


@pytest.mark.parametrize('role', [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_real_project_routes_through_create_app_and_csrf(real_release, tmp_path, monkeypatch, role):
    import hashlib

    auth = FakeAuth()
    auth.context = AuthContext(uuid4(), role, uuid4(), False)
    app, c = real_app_client(tmp_path, monkeypatch, auth)
    from app.manufacturing_paths import MANUFACTURING_ROUTES, WORKFLOW_ROUTES
    registered = {(method, route.path.removeprefix(BASE))
        for route in IdentitySecurityMiddleware._expand_included_routes(app.routes)
        if getattr(route, 'path', '').startswith(BASE) and getattr(route, 'include_in_schema', False)
        for method in route.methods}
    assert registered == MANUFACTURING_ROUTES | frozenset(
        (method, '/workflow' + path) for method, path in WORKFLOW_ROUTES
    )
    assert c.get(BASE + '/handbook').status_code == 401
    c.cookies.set(auth.cookie_name, 'valid-cookie')
    headers = {'Origin': auth.public_base_url, 'X-CSRF-Token': auth.csrf, 'X-User-ID': 'forged'}
    for asset in ['manufacturing.js', 'manufacturing.css']:
        response = c.get(BASE + '/assets/' + asset, params={'release': 'project-v2-test'})
        assert response.status_code == 200
        assert response.content == (real_release / 'webui/dist' / asset).read_bytes()
        assert response.headers['cache-control'] == 'private, no-store'
        assert c.get(BASE + '/assets/' + asset + '?release=old').status_code == 409
    # Workflow writes keep the real host Origin/CSRF boundary. The workflow store
    # is not configured here, so an accepted write reaches the unavailable router.
    handoff = {'operation_id': str(uuid4()), 'material': 'M-1', 'quantity': '1', 'unit': 'PC',
               'to_user': str(uuid4()), 'from_location': 'WH', 'to_location': 'LINE'}
    for invalid in [{}, {'Origin': auth.public_base_url},
            {'Origin': 'https://evil.test', 'X-CSRF-Token': auth.csrf},
            {'Origin': auth.public_base_url, 'X-CSRF-Token': 'wrong'}]:
        assert c.post(BASE + '/workflow/handoffs', json=handoff, headers=invalid).status_code == 403
    response = c.post(BASE + '/workflow/handoffs', json=handoff, headers=headers)
    assert response.status_code == 503
    assert response.headers['cache-control'] == 'private, no-store'
    for document, relative in [('handbook', 'docs/manufacturing-system-handbook.md'), ('workflow', 'docs/workflows/manufacturing-process-overview.md')]:
        response = c.get(BASE + '/handbook', params={'document': document, 'release': 'project-v2-test'})
        assert response.status_code == 200
        data = (real_release / relative).read_bytes()
        assert response.json()['markdown'].encode() == data
        assert response.json()['sha256'] == hashlib.sha256(data).hexdigest()
        assert response.headers['cache-control'] == 'private, no-store'
    assert c.get(BASE + '/handbook?release=old').status_code == 409
    assert c.get(BASE + '/handbook?document=../../private').status_code == 422
    for path in ['/projects/missing', '/unknown', '/assets/private.json', '/dashboard', '/catalog']:
        response = c.get(BASE + path)
        assert response.status_code in {403, 404}
        assert response.headers['cache-control'] == 'private, no-store'
        assert str(real_release) not in response.text
    (real_release / 'handbook-metadata.json').write_text('private invalid metadata')
    response = c.get(BASE + '/handbook')
    assert response.status_code == 503 and 'private invalid metadata' not in response.text
    auth.context = AuthContext(auth.context.internal_user_id, role, auth.context.session_id, True)
    assert c.get(BASE + '/health').status_code == 200
    response = c.post(BASE + '/workflow/handoffs', json=handoff, headers=headers)
    assert response.status_code == 503
    assert response.headers['cache-control'] == 'private, no-store'
    assert not (tmp_path / 'real.sqlite3').exists()


def test_real_project_routes_member_denied_before_validation(real_release, tmp_path, monkeypatch):
    auth = FakeAuth()
    auth.context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    _, c = real_app_client(tmp_path, monkeypatch, auth)
    c.cookies.set(auth.cookie_name, 'valid-cookie')
    headers = {'Origin': auth.public_base_url, 'X-CSRF-Token': auth.csrf}
    from app.manufacturing_paths import MANUFACTURING_ROUTES
    for method, path in MANUFACTURING_ROUTES:
        response = c.request(method, BASE + path.replace('{project_id}', 'missing').replace('{stage_id}', 'S1')
            .replace('{task_id}', 'missing').replace('{line_id}', 'cameras').replace('{asset_name}', 'manufacturing.js'), headers=headers)
        assert response.status_code == 403
        assert response.headers['cache-control'] == 'private, no-store'
    assert not (tmp_path / 'real.sqlite3').exists()


def test_all_exact_routes_remain_private_and_unavailable_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv('PLATFORM_MANUFACTURING_ROOT', raising=False)
    auth = FakeAuth()
    c = client(auth)
    from app.manufacturing_paths import MANUFACTURING_ROUTES
    for method, path in MANUFACTURING_ROUTES:
        concrete = path.replace('{project_id}', 'missing').replace('{stage_id}', 'S1').replace('{task_id}', 'missing')
        concrete = concrete.replace('{line_id}', 'cameras').replace('{asset_name}', 'manufacturing.js')
        assert c.request(method, BASE + concrete).status_code == 401
    c.cookies.set(auth.cookie_name, 'valid-cookie')
    headers = {'Origin': auth.public_base_url, 'X-CSRF-Token': auth.csrf}
    for method, path in MANUFACTURING_ROUTES:
        concrete = path.replace('{project_id}', 'missing').replace('{stage_id}', 'S1').replace('{task_id}', 'missing')
        concrete = concrete.replace('{line_id}', 'cameras').replace('{asset_name}', 'manufacturing.js')
        response = c.request(method, BASE + concrete, headers=headers)
        assert response.status_code == (200 if path == '/access' else 503)
        assert response.headers['cache-control'] == 'private, no-store'


@pytest.mark.parametrize('role', [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_real_retired_collaboration_routes_never_reach_storage(real_release, tmp_path, monkeypatch, role):
    """Phase-0 collaboration ledger routes are retired; administrators cannot reach them."""
    auth = FakeAuth()
    auth.context = AuthContext(uuid4(), role, uuid4(), False)
    _, c = real_app_client(tmp_path, monkeypatch, auth)
    assert c.get(BASE + '/collaboration').status_code == 401
    c.cookies.set(auth.cookie_name, 'valid-cookie')
    headers = {'Origin': auth.public_base_url, 'X-CSRF-Token': auth.csrf}
    for method, path in RETIRED_LEGACY_ROUTES:
        concrete = (path.replace('{project_id}', 'p').replace('{stage_id}', 'S1').replace('{line_id}', 'cameras')
                    .replace('{system_id}', 's'))
        response = c.request(method, BASE + concrete, json={'operation_id': str(uuid4())}, headers=headers)
        assert response.status_code in {403, 404, 405}, (method, path, response.status_code)
        assert response.headers['cache-control'] == 'private, no-store'
    assert not (tmp_path / 'real.sqlite3').exists()
    c.close()


@pytest.mark.parametrize('enabled', [True, False])
def test_collaboration_method_path_matrix_cannot_reach_business_handlers(real_release, tmp_path, monkeypatch, enabled):
    if not enabled:
        monkeypatch.delenv('PLATFORM_MANUFACTURING_ROOT', raising=False)
    auth = FakeAuth()
    _, c = real_app_client(tmp_path, monkeypatch, auth)
    paths = [
        ('GET', '/systems'), ('PATCH', '/systems'), ('POST', '/systems/id/extra'),
        ('GET', '/authorities'), ('PATCH', '/authorities'),
        ('POST', '/projects/id/packages/id'), ('POST', '/projects/id/packages/id/actions/extra'),
        ('GET', '/projects/id/controls/extra'), ('PUT', '/projects/id/readiness'),
        ('POST', '/projects/id/readiness/id/issues/extra'),
        ('POST', '/projects/id/handoffs/id'), ('PATCH', '/projects/id/handoffs'),
        ('GET', '/collaboration/extra'), ('GET', '/projects//controls'),
    ]
    for method, path in paths:
        response = c.request(method, BASE + path)
        assert response.status_code == 401, (enabled, method, path, response.status_code)
        assert 'no-store' in response.headers['cache-control']
    c.cookies.set(auth.cookie_name, 'valid-cookie')
    headers = {'Origin': auth.public_base_url, 'X-CSRF-Token': auth.csrf}
    for method, path in paths:
        response = c.request(method, BASE + path, json={}, headers=headers)
        assert response.status_code in {403, 404, 405}, (enabled, method, path, response.status_code)
        assert 'no-store' in response.headers['cache-control']
    assert not (tmp_path / 'real.sqlite3').exists()
    c.close()
