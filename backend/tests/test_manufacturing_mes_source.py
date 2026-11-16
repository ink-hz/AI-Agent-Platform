"""Real sibling workflow, host router and identity middleware; no production I/O."""
import os
from pathlib import Path
from uuid import uuid4
from pathlib import Path
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants

PREFIX='/api/v1/manufacturing/workflow/material-control/mes-source'
ROUTES={('GET',PREFIX+'/status')} | {(method,PREFIX+'/'+kind) for method in ('GET','POST') for kind in ('orders','order-materials','priority','iqc','material-iqc','order-reports','order-operations','material-demand','order-priority','warehouses','inventory')}

def test_exact_host_routes():
    from app.manufacturing_paths import WORKFLOW_AUTH_ROUTES, PRODUCTION_TOOLS_ROUTES, SAP_MATERIAL_ROUTES
    assert {r for r in WORKFLOW_AUTH_ROUTES if '/mes-source' in r[1]} == ROUTES
    assert len(PRODUCTION_TOOLS_ROUTES)==21
    assert len(SAP_MATERIAL_ROUTES)==5

class Mes:
    def __init__(self): self.calls=[]
    def read_context(self): return dict(source=dict(system='mes',environment='test',site_code='TEST'),fingerprint='isolated')
    def configuration_matches(self,fingerprint): return fingerprint=='isolated'
    def source_orders(self,prefix,after,actor_id,*,expected_fingerprint=None):
        self.calls.append(actor_id)
        return dict(items=[dict(shop_order='TEST1',description='Isolated')],next_cursor=None,total=None,page_size=5)

def test_real_host_identity_and_source_cache(tmp_path, monkeypatch):
    import app.manufacturing as host
    root=os.environ.get('TEST_MANUFACTURING_RELEASE_ROOT')
    if not root: pytest.skip('set TEST_MANUFACTURING_RELEASE_ROOT')
    import shutil
    release=tmp_path/'release'
    shutil.copytree(Path(root)/'src',release/'src',ignore=shutil.ignore_patterns('__pycache__'))
    assets=release/'webui/dist'
    assets.mkdir(parents=True)
    (assets/'manufacturing.js').write_text('// isolated host fixture')
    (assets/'manufacturing.css').write_text('/* isolated host fixture */')
    monkeypatch.setenv('PLATFORM_MANUFACTURING_ROOT',str(release))
    for key,value in dict(DATABASE_PATH=str(tmp_path/'legacy.db'),WORKFLOW_DATABASE_PATH=str(tmp_path/'domain.db'),AUTHORITY_DATABASE_PATH=str(tmp_path/'authority.db'),DATASET_ID='test',DATASET_MODE='test',DATASET_LABEL='Test').items():
        monkeypatch.setenv('PLATFORM_MANUFACTURING_'+key,value)
    mes=Mes()
    monkeypatch.setattr(host,'_native_access_components',lambda *args:(APIRouter(),{'mes':mes}))
    auth=FakeAuth()
    owner=auth.context.internal_user_id
    auth.hard_stale_audit=lambda *args:None
    app=FastAPI()
    app.include_router(host.build_manufacturing_router(resolve_workflow_subject=lambda uid:uid==owner))
    app.add_middleware(IdentitySecurityMiddleware,auth=auth,authorization=AuthorizationService(_NoObservationGrants()),routes=app.routes,public_assets=frozenset())
    with TestClient(app,base_url='https://agent.example.test') as client:
        path=PREFIX+'/orders?prefix=&after='
        assert client.get(path).status_code==401
        client.cookies.set(auth.cookie_name,'valid-cookie')
        response=client.get(path)
        assert response.status_code==200,response.text
        assert response.json()==dict(snapshot=None,refresh_error=None)
        assert response.headers['cache-control']=='private, no-store'
        assert not mes.calls
        assert client.post(path).status_code==403
        headers={'Origin':'https://agent.example.test','X-CSRF-Token':auth.csrf,'X-User-ID':'forged'}
        result=client.post(path,headers=headers)
        assert result.status_code==200,result.text
        assert result.json()['snapshot']['data']['items'][0]['shop_order']=='TEST1'
        assert mes.calls==[str(owner)]
        assert client.get('/api/v1/manufacturing/workflow/todos').json()['items']==[]
        assert client.get('/api/v1/manufacturing/workflow/projects').status_code==403
        auth.context=AuthContext(owner,Role.PLATFORM_OWNER,auth.context.session_id,True)
        assert client.get(PREFIX+'/status').json()['can_refresh'] is False
        assert client.post(path,headers=headers).status_code==503
        auth.context=AuthContext(uuid4(),Role.MEMBER,auth.context.session_id,False)
        assert client.get(PREFIX+'/status').status_code==403
        assert client.get(PREFIX+'/orders/extra').status_code==403
