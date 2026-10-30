from datetime import UTC, datetime
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.control_plane.models import AuthContext, Role
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware


class Grants:
    def permits(self,*args):
        return True


class CatalogPermission:
    def __init__(self):
        self.allowed = {'hr-bot'}
    def permitted_catalog_for_user_id(self, actor):
        from app.agent_catalog import load_agent_catalog
        return tuple(c for c in load_agent_catalog() if c.agent_id in self.allowed)


def client_for(role=Role.PLATFORM_OWNER, *, service=None, workspace_summaries=True):
    from app.runtime_dashboard.routes import build_dashboard_router
    from app.runtime_dashboard.service import DashboardService
    actor = AuthContext(uuid4(),role,uuid4(),False)
    class Auth:
        route_prefix = '/'
        cookie_name = 'session'
        csrf_cookie_name = 'csrf'
        public_base_url = 'https://agent.example.test'
        trusted_proxy_networks = ()
        rate_limiter = None
        def authenticate(self,token):
            return (actor,b'csrf') if token == 'valid' else None
    app = FastAPI()
    permissions = CatalogPermission()
    app.include_router(build_dashboard_router(service or DashboardService(clock=lambda:datetime(2026,10,30,2,tzinfo=UTC)),permissions,
        workspace_summaries=workspace_summaries))
    app.add_middleware(IdentitySecurityMiddleware,auth=Auth(),public_assets=frozenset(),
        authorization=AuthorizationService(Grants()),routes=tuple(app.router.routes))
    client = TestClient(app,base_url='https://agent.example.test')
    client.cookies.set('session','valid')
    return client,permissions


@pytest.mark.parametrize('role,status',[(Role.PLATFORM_OWNER,200),(Role.PLATFORM_ADMIN,200),(Role.MEMBER,403),(Role.MANAGEMENT_VIEWER,403)])
@pytest.mark.parametrize('path',['/api/dashboard/catalog','/api/dashboard/overview'])
def test_real_identity_middleware_roles_and_no_store(role,status,path):
    client,_ = client_for(role)
    response = client.get(path)
    assert response.status_code == status,response.text
    assert 'no-store' in response.headers['cache-control']


def test_catalog_complete_but_use_permissions_rechecked_and_anonymous_denied():
    client, permissions = client_for()
    first = client.get('/api/dashboard/catalog').json()['agents']
    assert len(first) == 10 and sum(card['can_use'] for card in first) == 1
    hr = next(c for c in first if c['agent_id']=='hr-bot')
    assert hr['web_entry']['path'] == '/hr/'
    permissions.allowed.clear()
    assert not any(c['can_use'] for c in client.get('/api/dashboard/catalog').json()['agents'])
    overview = client.get('/api/dashboard/overview').json()
    assert not any(a['can_use'] for a in overview['applications'])
    assert all(a['entry_path'] is None for a in overview['applications'])
    client.cookies.clear()
    assert client.get('/api/dashboard/overview').status_code == 401


def test_invalid_window_no_store():
    client,_=client_for()
    response=client.get('/api/dashboard/overview?days=8')
    assert response.status_code == 422
    assert 'no-store' in response.headers['cache-control']


def test_workspace_adapters_preserve_real_identity_and_authorization(tmp_path):
    from app.runtime_dashboard.routes import build_dashboard_router
    from app.runtime_dashboard.service import DashboardService
    from app.sdk_admin.repository import SdkAdminRepository
    from test_sdk_admin_routes import package
    from fastapi import Request, HTTPException
    root,_ = package(tmp_path)
    app=FastAPI()
    repository=SdkAdminRepository(root)
    app.state.sdk_admin_repository=repository
    owner=AuthContext(uuid4(),Role.PLATFORM_OWNER,uuid4(),False)
    admin=AuthContext(uuid4(),Role.PLATFORM_ADMIN,uuid4(),False)
    permitted_projects={owner.internal_user_id:2,admin.internal_user_id:0}
    calls=[]
    from app.voc_extension.routes import build_voc_extension_router
    from app.voc_extension.client import VocUpstreamResponse
    from test_voc_extension_routes import Upstream, Directory, admin_summary
    import json
    upstream=Upstream()
    upstream.response=VocUpstreamResponse(200,json.dumps({'items':[admin_summary(latest_content='SECRET')],'next_cursor':None}).encode())
    app.state.voc_extension_client=upstream
    app.state.voc_submitter_directory=Directory()
    class VocGrant:
        granted=True
        def allows(self,actor):
            return self.granted
    app.state.voc_access=VocGrant()
    app.include_router(build_voc_extension_router())
    class Auth:
        route_prefix='/'
        cookie_name='session'
        csrf_cookie_name='csrf'
        public_base_url='https://agent.example.test'
        trusted_proxy_networks=()
        rate_limiter=None
        def authenticate(self,token):
            calls.append(('auth',token))
            return ({'owner':owner,'admin':admin}[token],b'csrf') if token in {'owner','admin'} else None
    @app.get('/api/v1/manufacturing/workflow/access')
    def access(request:Request):
        actor=request.state.auth_context
        return {'workflow_available':True,'workflow_access':bool(permitted_projects[actor.internal_user_id]),
            'dataset':{'mode':'test','label':'Synthetic'},'project_ids':['PRIVATE-PROJECT'], 'release':'configured-release'}
    @app.get('/api/v1/manufacturing/workflow/dashboard')
    def dashboard(request:Request):
        actor=request.state.auth_context
        if not permitted_projects[actor.internal_user_id]:
            raise HTTPException(403)
        calls.append(('domain',str(actor.internal_user_id)))
        return {'project_count':permitted_projects[actor.internal_user_id],'cards':[{'secret':'SECRET'}],
            'project_ids':['PRIVATE-PROJECT'], 'counts':{'unknown-business-unit':999}}
    app.include_router(build_dashboard_router(DashboardService(),CatalogPermission()))
    app.add_middleware(IdentitySecurityMiddleware,auth=Auth(),public_assets=frozenset(),authorization=AuthorizationService(Grants()),routes=tuple(app.router.routes))
    client=TestClient(app,base_url='https://agent.example.test')
    client.cookies.set('session','owner')
    data=client.get('/api/dashboard/overview').json()
    byid={a['id']:a for a in data['applications']}
    assert byid['sdk']['business_metrics']['documents']['value']==3
    assert byid['voc']['business_metrics']['registered_vocs']['value']==1
    assert upstream.calls[0]['actor_id']==owner.internal_user_id
    assert upstream.calls[0]['query']=={'limit':100}
    assert byid['manufacturing']['business_metrics']['visible_projects']['value']==2
    assert byid['manufacturing']['dataset']=={'mode':'test','label':'Synthetic'}
    assert byid['manufacturing']['runtime']['coverage']=='not_applicable'
    assert data['summary']['expected_runtime']==5
    serialized=str(data)
    assert 'SECRET' not in serialized and 'PRIVATE-PROJECT' not in serialized and str(owner.internal_user_id) not in serialized
    assert calls.count(('auth','owner'))==4  # outer, workflow access/dashboard, VOC list
    cached=repository._snapshot
    client.get('/api/dashboard/overview')
    assert repository._snapshot is cached
    permitted_projects[owner.internal_user_id]=0
    app.state.voc_access.granted=False
    data=client.get('/api/dashboard/overview').json()
    assert next(a for a in data['applications'] if a['id']=='manufacturing')['source']['error']=='business_permission_denied'
    voc=next(a for a in data['applications'] if a['id']=='voc')
    assert voc['business_metrics']['registered_vocs']['value'] is None
    assert voc['business_sources']['registered_vocs']['error']=='business_permission_denied'
    client.cookies.set('session','admin')
    data=client.get('/api/dashboard/overview').json()
    assert all(a['source']['error']=='business_permission_denied' for a in data['applications'] if a['kind']=='workspace')


@pytest.mark.parametrize('status',[302,401,403,500])
def test_manufacturing_failure_does_not_clear_other_application_data(status):
    import asyncio
    from fastapi import Request
    from fastapi.responses import Response
    from app.runtime_dashboard.workspaces import manufacturing_summary
    from app.runtime_dashboard.service import DashboardService
    service=DashboardService()
    result=service.overview()
    workspace=next(a for a in result.applications if a.id=='manufacturing')
    app=FastAPI()
    @app.get('/api/v1/manufacturing/workflow/access')
    def access():
        return Response('non-JSON',status_code=status)
    request=Request({'type':'http','scheme':'https','method':'GET','path':'/api/dashboard/overview',
        'root_path':'','server':('agent.example.test',443),'headers':[(b'host',b'agent.example.test'),(b'cookie',b'session=real')], 'app':app})
    asyncio.run(manufacturing_summary(request,workspace,result.generated_at))
    assert workspace.business_metrics['visible_projects'].value is None
    assert workspace.can_use is False
    assert len(result.applications)==7


@pytest.mark.parametrize('days',[7,30])
def test_explicit_integer_query_windows(days):
    client,_=client_for()
    response=client.get(f'/api/dashboard/overview?days={days}')
    assert response.status_code==200
    assert response.json()['window']['days']==days
