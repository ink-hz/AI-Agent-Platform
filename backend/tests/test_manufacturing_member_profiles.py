"""Narrow trusted project-member names; no general directory endpoint."""
from uuid import uuid4
from types import SimpleNamespace
import pytest
from app.control_plane.auth import WebSessionRepository
from app.manufacturing import build_manufacturing_router


def test_host_optional_profile_callback():
    build_manufacturing_router(resolve_workflow_subject=lambda uid: True, resolve_workflow_profiles=lambda ids: [])


def test_repository_profiles_only_selected_active_subjects():
    user=uuid4()
    calls=[]
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def execute(self,sql,args):
            calls.append((sql,args))
            return SimpleNamespace(fetchall=lambda:[{"internal_user_id":user,"display_name":"Quality Engineer"}])
    repo=WebSessionRepository.__new__(WebSessionRepository)
    repo._connection=lambda:Connection()
    assert repo.active_internal_user_profiles([user]) == [{"user_id":str(user),"display_name":"Quality Engineer"}]
    sql,args=calls[0]
    assert "any(%s)" in sql and "status='active'" in sql and "locally_invalidated_at is null" in sql
    assert args==([user],)
    with pytest.raises(ValueError):repo.active_internal_user_profiles([uuid4() for _ in range(51)])
    assert len(calls)==1


@pytest.mark.parametrize("signature,supported",[("",False),(", **kwargs",False),(", resolve_subject_profiles=None",True)])
def test_old_and_explicit_optional_workflow_factory_compatibility(tmp_path,monkeypatch,signature,supported):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    package=tmp_path/"src/manufacturing_platform"
    package.mkdir(parents=True)
    assets=tmp_path/"webui/dist";assets.mkdir(parents=True)
    for name in ("manufacturing.js","manufacturing.css"):(assets/name).write_text("fixture")
    body="""from fastapi import APIRouter
WORKFLOW_API_VERSION=1
def build_router(*,database_path,assets_dir,authorize,release):
 r=APIRouter(prefix='/api/v1/manufacturing')
 @r.get('/access')
 def access():return {'available':True}
 return r
"""
    body+=f"def build_workflow_router(*,database_path,authority_database_path,assets_dir,authenticate,resolve_subject,release,dataset{signature}):\n"
    if "**kwargs" in signature:body+=" assert 'resolve_subject_profiles' not in kwargs\n"
    body+=" r=APIRouter(prefix='/api/v1/manufacturing/workflow')\n @r.get('/access')\n def access():return {'available':True,'profile_callback':"+("resolve_subject_profiles is not None" if supported else "False")+"}\n return r\n"
    (package/"__init__.py").write_text(body)
    settings={"ROOT":str(tmp_path),"DATABASE_PATH":str(tmp_path/"legacy.db"),"WORKFLOW_DATABASE_PATH":str(tmp_path/"domain.db"),"AUTHORITY_DATABASE_PATH":str(tmp_path/"authority.db"),"DATASET_ID":"profiles-test","DATASET_MODE":"test","DATASET_LABEL":"Profiles test"}
    for key,value in settings.items():monkeypatch.setenv("PLATFORM_MANUFACTURING_"+key,value)
    app=FastAPI();app.include_router(build_manufacturing_router(resolve_workflow_subject=lambda uid:True,resolve_workflow_profiles=lambda ids:[]))
    response=TestClient(app).get('/api/v1/manufacturing/workflow/access')
    assert response.status_code==200,response.text
    assert response.json()['profile_callback'] is supported
