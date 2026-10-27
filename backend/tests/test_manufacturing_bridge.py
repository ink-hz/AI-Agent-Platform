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
    (package / '__init__.py').write_text('''from fastapi import APIRouter, Request\nfrom starlette.responses import Response\nfrom pathlib import Path\ndef build_router(*, database_path, assets_dir, authorize, release):\n r=APIRouter(prefix="/api/v1/manufacturing")\n @r.get("/access")\n def access(request: Request):\n  actor=authorize(request)\n  return {"allowed":True,"available":True,"can_edit":not actor["read_only"],"release":release}\n @r.get("/catalog")\n def catalog(request: Request):\n  return authorize(request)\n @r.put("/lines/{line_id}/stages/{stage_id}")\n def write(request: Request):\n  return authorize(request,write=True)\n @r.get("/assets/{asset_name}")\n def asset(request: Request,asset_name:str):\n  authorize(request)\n  return Response((Path(assets_dir)/asset_name).read_bytes(),media_type="text/javascript")\n return r\n''')
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
    response=c.get(BASE+'/catalog',headers={'X-User-ID':'forged','X-Display-Name':'forged'})
    assert response.status_code == (200 if role in {Role.PLATFORM_ADMIN,Role.PLATFORM_OWNER} else 403)
    assert response.headers['cache-control'] == 'private, no-store'
    if response.status_code == 200:
        assert response.json()['user_id'] == str(auth.context.internal_user_id)
        assert response.json()['display_name'] == str(auth.context.internal_user_id)


def test_no_identity_mode_never_bypasses_callback(bundle):
    c=client()
    assert c.get(BASE+'/catalog',headers={'X-Role':'platform_owner'}).status_code == 401


@pytest.mark.parametrize('headers', [{}, {'Origin':'https://agent.example.test'}, {'Origin':'https://evil.test','X-CSRF-Token':'csrf-value'}, {'Origin':'https://agent.example.test','X-CSRF-Token':'invalid'}])
def test_real_middleware_origin_csrf(bundle,headers):
    auth=FakeAuth(); c=client(auth); c.cookies.set(auth.cookie_name,'valid-cookie')
    assert c.put(BASE+'/lines/cameras/stages/S1',headers=headers,json={}).status_code == 403


def test_write_and_hard_stale(bundle):
    auth=FakeAuth(); c=client(auth); c.cookies.set(auth.cookie_name,'valid-cookie')
    headers={'Origin':'https://agent.example.test','X-CSRF-Token':auth.csrf}
    assert c.put(BASE+'/lines/cameras/stages/S1',headers=headers,json={}).status_code == 200
    auth.context=AuthContext(auth.context.internal_user_id,auth.context.role,auth.context.session_id,True)
    assert c.get(BASE+'/access').json()['can_edit'] is False
    assert c.put(BASE+'/lines/cameras/stages/S1',headers=headers,json={}).status_code == 503


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
    for path in ['/health','/catalog','/lines/cameras','/lines/cameras/stages/S1','/assets/manufacturing.js']:
        result=c.get(BASE+path)
        assert result.status_code==503
        assert 'private internals' not in result.text
    assert c.get(BASE+'/unknown').status_code in {403,404}


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


def test_real_business_package(tmp_path,monkeypatch):
    release=os.environ.get('TEST_MANUFACTURING_RELEASE_ROOT')
    if not release: pytest.skip('optional versioned sibling integration')
    monkeypatch.setenv('PLATFORM_MANUFACTURING_ROOT',release)
    monkeypatch.setenv('PLATFORM_MANUFACTURING_DATABASE_PATH',str(tmp_path/'real.sqlite3'))
    auth=FakeAuth(); c=client(auth); c.cookies.set(auth.cookie_name,'valid-cookie')
    assert c.get(BASE+'/catalog').status_code==200
    response=c.put(BASE+'/lines/cameras/stages/S1',json={'expected_version':0,'practice':'integration','participants':[],'handoffs':[],'systems':[]},headers={'Origin':auth.public_base_url,'X-CSRF-Token':auth.csrf,'X-User-ID':'forged'})
    assert response.status_code==200 and response.json()['updated_by']==str(auth.context.internal_user_id)
    assert c.get(BASE+'/lines/cameras/stages/S1').json()['record']['practice']=='integration'


def test_preview_shell_csp_allows_private_workbench_assets_for_spa_entry(tmp_path,monkeypatch):
    auth=FakeAuth(prefix='/_preview/dingtalk-r1/')
    c=TestClient(_app(tmp_path,monkeypatch,auth),base_url=auth.public_base_url)
    for path in ['/manufacturing','/login']:
        csp=c.get(auth.route_prefix.rstrip('/')+path).headers['content-security-policy']
        for directive in ['script-src','style-src']:
            value=next(part.strip() for part in csp.split(';') if part.strip().startswith(directive+' '))
            assert 'https://agent.example.test/_preview/dingtalk-r1/api/v1/manufacturing/assets/' in value
            assert 'https://agent.example.test/_preview/dingtalk-r1/assets/' in value


@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('method,path',[
 ('GET','/access'),('GET','/health'),('GET','/catalog'),('GET','/assets/{asset_name}'),
 ('GET','/lines/{line_id}'),('GET','/lines/{line_id}/stages/{stage_id}'),
 ('PUT','/lines/{line_id}/stages/{stage_id}'),
 ('POST','/lines/{line_id}/stages/{stage_id}/tasks'),
 ('PATCH','/lines/{line_id}/stages/{stage_id}/tasks/{task_id}'),
])
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
