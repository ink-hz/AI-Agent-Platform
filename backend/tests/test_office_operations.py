from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.control_plane.office_operations import OperationsQuery, OperationsInvalid
from app.control_plane.routes_office_operations import build_office_operations_router


def test_filters_launch_boundary_and_monthly_ranges():
    q = OperationsQuery(date(2026, 9, 14), date(2027, 9, 13), grain='month')
    assert q.grain == 'month'
    for kwargs in ({'date_from': date(2026,9,13)}, {'date_to': date(2026,9,1)}, {'metric':'login'}, {'scope':'owner'}, {'grain':'week'}, {'department':'x'*161}):
        args = dict(date_from=date(2026,9,14), date_to=date(2026,10,23)) | kwargs
        with pytest.raises(OperationsInvalid):
            OperationsQuery(**args)


class Reports:
    def __init__(self): self.calls=[]
    def report(self, query):
        self.calls.append(query)
        return {'summary': {'events': 7, 'users': 2}}
    def export(self, query): return 'period,department\r\n2026-09,研发\r\n'


def test_private_report_requires_both_loopback_and_bearer():
    service=Reports(); app=FastAPI()
    app.include_router(build_office_operations_router(service, bearer_secret='t'*32))
    public=TestClient(app, client=('192.0.2.10',3210))
    local=TestClient(app, client=('127.0.0.1',3210))
    path='/api/v1/internal/office/operations'
    assert public.get(path, headers={'authorization':'Bearer '+'t'*32}).status_code==404
    assert local.get(path).status_code==404
    response=local.get(path, headers={'authorization':'Bearer '+'t'*32}, params={'date_from':'2026-09-14','date_to':'2026-10-23','grain':'month'})
    assert response.status_code==200
    assert response.json()['summary']['users']==2
    assert response.headers['cache-control']=='no-store'
    assert len(service.calls)==1 and service.calls[0].grain=='month'
    assert local.get(path, headers={'authorization':'Bearer '+'t'*32}, params={'date_from':'2026-09-01'}).status_code==400

import psycopg
from uuid import uuid4
from test_control_plane_migration import control_database
from app.control_plane.models import AuthContext, Role


@pytest.mark.postgres
def test_durable_exact_counts_exclusion_filters_and_privileges(control_database):
    from app.control_plane.office_operations import OfficeOperationsRepository
    env=control_database['environments']['production']
    a,b,developer=uuid4(),uuid4(),uuid4()
    with psycopg.connect(env['admin']) as c:
        for actor in (a,b,developer):
            c.execute("insert into platform_control.internal_users(internal_user_id,display_name,status,role) values (%s,'合成测试','active','member')",(actor,))
        c.execute("insert into platform_control.office_operations_exclusions(actor_key) values(platform_control.office_actor_key_v124(%s))",(developer,))
        for day,actor,dept,page,count in [('2026-09-14',a,'研发','office.shuttle',3),('2026-09-15',a,'研发','office.shuttle',2),('2026-09-15',b,'行政','office.lodging',1),('2026-09-15',developer,'研发','office.shuttle',50),('2026-09-15',a,'研发','office.shuttle_admin',4),('2026-09-15',a,'研发','office.service.other',2)]:
            c.execute("insert into platform_control.office_operations_daily(day,actor_key,department,page_key,source,events) values(%s,platform_control.office_actor_key_v124(%s),%s,%s,'legacy',%s)",(day,actor,dept,page,count))
        c.execute("update platform_control.office_operations_state set backfilled_at=now()")
        c.execute("update platform_control.internal_users set display_name='已经改名' where internal_user_id=%s",(developer,))
    repo=OfficeOperationsRepository(env['urls']['platform_control_app'])
    q=OperationsQuery(date(2026,9,14),date(2026,9,30),grain='month')
    report=repo.report(q)
    assert report['summary']=={'events':8,'users':2,'departments':2,'modules':3}
    assert report['trend']==[{'period':'2026-09','events':8,'users':2}]
    assert report['coverage']['excluded_developer_accounts']==1
    assert report['coverage']['tracking_started_at'].startswith('2026-09-14T00:00:00')
    before=repo.report(OperationsQuery(date(2026,9,14),date(2026,9,30),module='meetings'))
    assert before['trend']==[]
    assert before['coverage']['tracking_started_at'][:10] >= '2026-10-22'
    assert any(m['historical_mixed'] for m in report['modules'])
    assert not any(str(actor) in str(report) for actor in (a,b,developer))
    selected=repo.report(OperationsQuery(date(2026,9,14),date(2026,9,30),department='研发',module='shenzhen-shunde-shuttle'))
    assert selected['summary']['events']==5 and selected['summary']['users']==1
    assert len(selected['options']['departments'])==2
    assert repo.report(OperationsQuery(date(2026,9,14),date(2026,9,30),scope='admin'))['summary']['events']==4
    export=repo.export(q)
    assert '2026-09' in export and '50' not in export
    with psycopg.connect(env['urls']['platform_control_app']) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('select * from platform_control.office_operations_daily')


@pytest.mark.postgres
def test_capture_is_atomic_deduplicated_and_uses_shanghai_day(control_database):
    from app.control_plane.access_history import AccessHistoryRepository, PageAccessDescriptor
    from app.control_plane.office_operations import OfficeOperationsRepository
    env=control_database['environments']['preview']; actor,session,event=uuid4(),uuid4(),uuid4()
    with psycopg.connect(env['admin']) as c:
        c.execute("insert into platform_control.internal_users(internal_user_id,display_name,status,role) values(%s,'合成','active','member')",(actor,))
        c.execute("insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,token_hash_key_version,csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) values(%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')",(session,actor,uuid4().bytes*2,uuid4().bytes*2))
    context=AuthContext(actor,Role.MEMBER,session,False)
    repo=AccessHistoryRepository(env['urls']['platform_control_app_preview'], department_resolver=lambda _: '研发')
    page=PageAccessDescriptor('office','office.meetings',None)
    assert repo.record_page_view(event,context,page)=='inserted'
    assert repo.record_page_view(event,context,page)=='duplicate'
    ops=OfficeOperationsRepository(env['urls']['platform_control_app_preview'],department_resolver=lambda _:'研发')
    click=uuid4()
    assert ops.record_click(click,context,'meetings')=='inserted'
    assert ops.record_click(click,context,'meetings')=='duplicate'
    with psycopg.connect(env['admin']) as c:
        rows=c.execute("select page_key,sum(events),min(day)= (clock_timestamp() at time zone 'Asia/Shanghai')::date from platform_control.office_operations_daily group by page_key order by page_key").fetchall()
        assert rows==[('entry.meetings',1,True),('office.meetings',1,True)]
        c.execute("delete from platform_control.user_access_events where internal_user_id=%s",(actor,))
        assert c.execute('select sum(events) from platform_control.office_operations_daily').fetchone()[0]==2


@pytest.mark.postgres
def test_backfill_is_idempotent_preserves_mixed_history_and_live_facts(control_database):
    from psycopg.types.json import Jsonb
    from app.control_plane.office_operations_cli import backfill
    from app.control_plane.access_history import AccessHistoryRepository,PageAccessDescriptor
    env=control_database['environments']['production']
    actor,session,event=uuid4(),uuid4(),uuid4()
    with psycopg.connect(env['admin']) as c:
        c.execute('truncate platform_control.office_operations_daily,platform_control.office_operations_receipts,platform_control.office_operations_exclusions')
        c.execute('update platform_control.office_operations_state set backfilled_at=null')
        c.execute("insert into platform_control.internal_users(internal_user_id,display_name,status,role) values(%s,'合成回填','active','member')",(actor,))
        c.execute("insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,token_hash_key_version,csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) values(%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')",(session,actor,uuid4().bytes*2,uuid4().bytes*2))
        c.execute("insert into platform_control.user_access_events(access_event_id,internal_user_id,session_id,event_kind,workspace_key,page_key,occurred_at) values(%s,%s,%s,'page_view','office','office.service.other','2026-09-13 16:30:00+00')",(event,actor,session))
    repo=AccessHistoryRepository(env['urls']['platform_control_app'],department_resolver=lambda _:'新部门')
    assert repo.record_page_view(uuid4(),AuthContext(actor,Role.MEMBER,session,False),PageAccessDescriptor('office','office.meetings',None))=='inserted'
    args=dict(codec=None,corp_id='test',excluded_actor=actor)
    assert backfill(env['urls']['platform_control_maintenance'],**args)['daily_rows_inserted']==1
    assert backfill(env['urls']['platform_control_maintenance'],**args)['daily_rows_inserted']==0
    with psycopg.connect(env['admin']) as c:
        rows=c.execute("select day,department,page_key,source,events from platform_control.office_operations_daily order by source").fetchall()
        assert len(rows)==2
        assert rows[0]==(date(2026,9,14),'未归属部门','office.service.other','legacy',1)
        assert rows[1][1:]==('新部门','office.meetings','live',1)
        assert c.execute('select count(*) from platform_control.user_access_events where access_event_id=%s',(event,)).fetchone()[0]==1
    with psycopg.connect(env['urls']['platform_control_app']) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('select platform_control.backfill_office_operations_v124(%s,%s)',(Jsonb({}),actor))


def test_department_uses_trusted_primary_then_unique_membership():
    from app.control_plane.office_operations import trusted_department
    from app.control_plane.access_history import AccessHistoryUnavailable
    context=AuthContext(uuid4(),Role.MEMBER,uuid4(),False)
    assert trusted_department(context,lambda _: {'primary_department':'主部门','departments':['A','B']})=='主部门'
    assert trusted_department(context,lambda _: {'departments':['A']})=='A'
    assert trusted_department(context,lambda _: {'departments':['A','B']})=='未归属部门'
    with pytest.raises(AccessHistoryUnavailable):
        trusted_department(context,None)


def test_click_body_never_accepts_client_identity_or_department():
    from app.control_plane.routes_access_history import build_access_history_router
    class Clicks:
        def __init__(self): self.calls=[]
        def record_click(self,event,context,service):
            self.calls.append((event,context,service));return 'inserted'
    from test_access_history_api import FakeAccessHistoryRepository
    from fastapi import Request
    context=AuthContext(uuid4(),Role.MEMBER,uuid4(),False)
    app=FastAPI();clicks=Clicks()
    @app.middleware('http')
    async def auth(request: Request, call_next):
        request.state.auth_context=context
        return await call_next(request)
    app.include_router(build_access_history_router(FakeAccessHistoryRepository(),clicks))
    client=TestClient(app);body={'access_event_id':str(uuid4()),'service_id':'meetings'}
    assert client.post('/api/v1/access-events/office-service-click',json=body|{'department':'伪造'}).status_code==400
    assert client.post('/api/v1/access-events/office-service-click',json=body).status_code==204
    assert len(clicks.calls)==1 and clicks.calls[0][1]==context
