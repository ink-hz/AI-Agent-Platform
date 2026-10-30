"""Dashboard-only budgets. Delayed boundaries are local doubles, never providers."""
import asyncio
from datetime import UTC, datetime, timedelta
import threading
import time
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.runtime_dashboard.health import HealthReader, HostObservationReader
from app.runtime_dashboard.native import HrMetadataReader
from app.runtime_dashboard.routes import build_dashboard_router
from app.runtime_dashboard.service import DashboardService
from test_runtime_dashboard_http import CatalogPermission, Grants

NOW = datetime(2026, 10, 30, 2, tzinfo=UTC)


def application(*, service=None, health=None, permission=None, role=Role.PLATFORM_OWNER):
    actor = AuthContext(uuid4(), role, uuid4(), False)
    class Auth:
        route_prefix = '/'
        cookie_name = 'session'
        csrf_cookie_name = 'csrf'
        public_base_url = 'https://agent.example.test'
        trusted_proxy_networks = ()
        rate_limiter = None
        def authenticate(self, token):
            return (actor, b'csrf') if token == 'valid' else None
    app = FastAPI()
    app.include_router(build_dashboard_router(service or DashboardService(clock=lambda: NOW),
        permission or CatalogPermission(), health=health))
    app.add_middleware(IdentitySecurityMiddleware, auth=Auth(), public_assets=frozenset(),
        authorization=AuthorizationService(Grants()), routes=tuple(app.router.routes))
    return app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
        base_url='https://agent.example.test', cookies={'session': 'valid'})


def test_actual_response_finishes_before_ui_deadline_with_independent_failures(tmp_path):
    calls = []
    class SlowVocHealth:
        async def health(self):
            calls.append('health')
            await asyncio.sleep(30)
    app = application(health=HealthReader(voc_client=SlowVocHealth(),
        host=HostObservationReader(tmp_path/'absent.json')))
    @app.get('/api/v1/extensions/voc/admin/vocs')
    async def slow_list():
        calls.append('business')
        await asyncio.sleep(30)
    app.user_middleware[0].kwargs['routes'] = tuple(app.router.routes)
    async def scenario():
        async with client(app) as http:
            start = time.monotonic()
            response = await http.get('/api/dashboard/overview')
            elapsed = time.monotonic()-start
            assert response.status_code == 200
            data = response.json()
            voc = next(a for a in data['applications'] if a['id'] == 'voc')
            assert len(data['applications']) == 7
            assert voc['runtime']['error'] == 'health_unavailable'
            assert voc['business_sources']['registered_vocs']['error'] == 'voc_business_unavailable'
            assert data['generated_at'] == data['window']['end'] == data['rolling_24h']['end']
            print('COMPOSED_SECONDS', elapsed)
            assert elapsed < 4.3
    asyncio.run(scenario())
    assert sorted(calls) == ['business', 'health']


class DelayedHrConnection:
    def __init__(self, delay=0):
        self.delay = delay
        self.queries = []
        self.sql = ''
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, sql, params=None):
        self.sql = sql
        if sql.lower().startswith('select'):
            self.queries.append(sql)
            time.sleep(self.delay)
        return self
    def fetchone(self):
        return {'role': 'metadata_reader'} if 'current_user' in self.sql else {'results': 2, 'questions': 3}
    def fetchall(self):
        return []


def test_whole_hr_refresh_deadline_stops_later_queries_and_keeps_watermark():
    connection = DelayedHrConnection()
    reader = HrMetadataReader('unused', connect=lambda *a, **k: connection, ttl_seconds=0)
    good = reader.snapshot(NOW)
    assert good.evidence.complete
    connection.queries.clear()
    connection.delay = .07
    start = time.monotonic()
    failed = reader.snapshot(NOW+timedelta(seconds=1), deadline=start+.12)
    assert time.monotonic()-start < .25
    assert len(connection.queries) == 2  # stop before inputs/states/totals
    assert failed.evidence.error == 'hr_metadata_unavailable'
    assert failed.evidence.observed_at == good.evidence.observed_at == NOW
    assert failed.business['results'].value == 2
    assert failed.business['results'].status == 'partial'


def test_hr_lock_wait_uses_remaining_budget():
    reader = HrMetadataReader('unused', connect=lambda *a, **k: pytest.fail('must not connect'))
    reader._lock.acquire()
    try:
        start = time.monotonic()
        data = reader.snapshot(NOW, deadline=start+.04)
        assert time.monotonic()-start < .15
        assert data.evidence.error == 'hr_metadata_unavailable'
    finally:
        reader._lock.release()


def test_concurrent_canceled_callers_share_one_blocking_source_and_sdk_job():
    """A timed-out first request must not free either worker for a duplicate."""
    from app.runtime_dashboard import routes
    from app.runtime_dashboard.workspaces import SdkSummaryReader
    from unittest.mock import patch
    entered, release = threading.Event(), threading.Event()
    counts = {'connect': 0, 'catalog': 0}
    connection = DelayedHrConnection()
    def connect(*a, **k):
        counts['connect'] += 1
        entered.set()
        release.wait(2)
        return connection
    class Catalog:
        def catalog(self):
            counts['catalog'] += 1
            release.wait(2)
            return {'generated_at': NOW.isoformat(), 'release_id': 'release',
                'counts': {'documents': 5, 'knowledge_pages': 2, 'knowledge_navigation': 1}}
    reader = HrMetadataReader('unused', connect=connect)
    async def scenario():
        async with client(app) as http:
            first = asyncio.create_task(http.get('/api/dashboard/overview'))
            while not entered.is_set():
                await asyncio.sleep(.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            start = time.monotonic()
            responses = await asyncio.gather(*(http.get('/api/dashboard/overview') for _ in range(4)))
            assert time.monotonic()-start < .4
            for response in responses:
                assert response.status_code == 200
                apps = {a['id']: a for a in response.json()['applications']}
                assert len(apps) == 7
                assert apps['hr-bot']['source']['error'] == 'hr_metadata_unavailable'
                assert apps['sdk']['source']['error'] == 'sdk_catalog_unavailable'
            assert counts == {'connect': 1, 'catalog': 1}
            assert connection.queries == []
            release.set()
            # Wait for the owned source worker; do not race cleanup/test loop exit.
            future = app_service._hr_work._future
            await asyncio.wrap_future(future)
            assert not reader._cached.evidence.complete
            assert connection.queries == []  # no SQL after abandoned connect exceeded whole budget
            with pytest.raises(TimeoutError):
                await asyncio.wrap_future(app_sdk.catalog_work._future)
            assert app_sdk._catalog is None  # expired catalog cannot publish a late success
    service = DashboardService(hr=reader, clock=lambda: NOW)
    app = application(service=service)
    app.state.sdk_admin_repository = Catalog()
    app_service = service
    # The shared catalog worker is independently covered below; no late response mutation.
    app_sdk = SdkSummaryReader()
    original = routes.sdk_summary
    async def sdk(request, application, now, **kwargs):
        return await original(request, application, now, reader=app_sdk, deadline=kwargs['deadline'])
    try:
        with patch.object(routes, 'SOURCE_SECONDS', .12), patch.object(routes, 'sdk_summary', sdk):
            asyncio.run(scenario())
    finally:
        release.set()
    print('CANCEL_CONCURRENCY_WORK', counts, 'queries', len(connection.queries))


def test_sdk_stale_numeric_catalog_requires_fresh_actor_permission():
    from app.runtime_dashboard.workspaces import SdkSummaryReader, sdk_summary
    from fastapi import Request
    actor = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), False)
    calls = {'catalog': 0, 'me': 0}
    class Catalog:
        fail = False
        def catalog(self):
            calls['catalog'] += 1
            if self.fail:
                raise OSError('local catalog failure')
            return {'generated_at': NOW.isoformat(), 'release_id': 'immutable',
                'counts': {'documents': 5, 'knowledge_pages': 2, 'knowledge_navigation': 1}, 'private': 'SECRET'}
    class Access:
        role = 'viewer'
        def me(self, current):
            assert current == actor
            calls['me'] += 1
            return {'role': self.role}
    app = FastAPI()
    app.state.sdk_admin_repository, app.state.sdk_service = Catalog(), Access()
    request = Request({'type': 'http', 'app': app, 'state': {'auth_context': actor}})
    reader = SdkSummaryReader()
    def workspace():
        return next(a for a in DashboardService().overview().applications if a.id == 'sdk')
    async def scenario():
        first = workspace()
        await sdk_summary(request, first, NOW, reader=reader)
        assert first.business_metrics['documents'].value == 5
        assert 'SECRET' not in repr(reader._catalog)
        reader._expires = 0
        app.state.sdk_admin_repository.fail = True
        stale = workspace()
        await sdk_summary(request, stale, NOW+timedelta(seconds=1), reader=reader)
        assert stale.business_metrics['documents'].value == 5
        assert stale.business_metrics['documents'].status == 'partial'
        assert stale.source.observed_at == stale.source.published_at == NOW
        assert stale.source.error == 'sdk_catalog_unavailable'
        app.state.sdk_service.role = 'revoked'
        denied = workspace()
        await sdk_summary(request, denied, NOW, reader=reader)
        assert denied.business_metrics['documents'].value is None
        assert denied.source.error == 'business_permission_denied'
    asyncio.run(scenario())
    assert calls['me'] == 3


def test_abandoned_required_authorization_stays_bounded_and_fails_closed():
    from app.runtime_dashboard import routes
    from unittest.mock import patch
    entered, release = threading.Event(), threading.Event()
    class SlowPermission(CatalogPermission):
        calls = 0
        def permitted_catalog_for_user_id(self, actor):
            self.calls += 1
            entered.set()
            release.wait(2)
            return super().permitted_catalog_for_user_id(actor)
    permission = SlowPermission()
    app = application(permission=permission)
    async def scenario():
        async with client(app) as http:
            first = asyncio.create_task(http.get('/api/dashboard/overview'))
            while not entered.is_set():
                await asyncio.sleep(.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            for _ in range(3):
                response = await http.get('/api/dashboard/overview')
                assert response.status_code == 503
                assert response.json() == {'detail': 'agent_authorization_unavailable'}
            assert permission.calls == 1
    try:
        with patch.object(routes, 'SOURCE_SECONDS', .1):
            asyncio.run(scenario())
    finally:
        release.set()


def test_health_host_timeout_preserves_independent_http_evidence_and_watermarks():
    from app.runtime_dashboard.health import HealthReader
    release = threading.Event()
    class Host:
        def read(self, now):
            release.wait(1)
            return {}
    def handle(request):
        return httpx.Response(200, json={'status': 'ok'})
    reader = HealthReader(host=Host(), urls={'ai-fae-agent': 'http://fae.local/health'},
        transport=httpx.MockTransport(handle))
    async def scenario():
        result = await reader.read(NOW, deadline=time.monotonic()+.05)
        assert result['ai-fae-agent'].runtime.state == 'healthy'
        assert result['ai-fae-agent'].runtime.observed_at == NOW
        assert result['hr-bot'].runtime.error == 'health_unavailable'
    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_replica_decrypt_deadline_stops_next_payload_and_discards_late_cache():
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    from app.cloud_replica.repository import _SESSION_COLUMNS  # preload unrelated module setup outside timed decrypt
    row = {'session_key': 'row', 'payload_sha256': 'hash', 'agent_id': 'voc',
        'source_kind': 'voc', 'channel': 'dingtalk', 'created_at': NOW, 'last_active_at': NOW}
    class Connection(DelayedHrConnection):
        def fetchall(self):
            if 'agent_sources' in self.sql:
                return [{'agent_id': 'voc', 'source_kind': 'voc', 'mode': 'cloud_direct', 'publisher': 'cloud',
                    'last_source_success_at': NOW, 'last_published_at': NOW, 'last_failure_at': None,
                    'source_sessions': 2, 'source_turns': 0}]
            return [row, {**row, 'session_key': 'second'}]
    class Repository:
        _stale_after = timedelta(seconds=60)
        def _connection(self):
            return Connection()
        def _decrypt(self, row):
            time.sleep(.08)
            return {'key': row['session_key'], 'agent_id': 'voc', 'source_kind': 'voc', 'channel': 'dingtalk',
                'created_at': NOW, 'last_active_at': NOW, 'turns': []}
    reader = ReplicaDashboardReader(Repository())
    result = reader.snapshot(NOW, deadline=time.monotonic()+.05)
    assert result['voc'].evidence.error == 'replica_unavailable'
    assert reader.stats['decrypts'] == reader.stats['payload_reads'] == 1
    assert reader.stats['refreshes'] == 0 and reader._cached == {}


def test_actor_admission_capacity_is_bounded_without_cross_actor_results():
    from app.runtime_dashboard.budget import ActorWork
    release = threading.Event()
    calls = []
    work = ActorWork(limit=1)
    def permission():
        calls.append('first')
        release.wait(1)
        return 'first-grants'
    async def scenario():
        first = asyncio.create_task(work.read('first', permission, time.monotonic()+.08))
        while not calls:
            await asyncio.sleep(.001)
        with pytest.raises(TimeoutError):
            await work.read('second', lambda: pytest.fail('no second worker'), time.monotonic()+.1)
        with pytest.raises(TimeoutError):
            await first
        assert len(work._active) == 1
        release.set()
        while work._active:
            await asyncio.sleep(.001)
        assert await work.read('second', lambda: 'second-grants', time.monotonic()+.1) == 'second-grants'
    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert calls == ['first']


@pytest.mark.postgres
def test_real_postgres_whole_refresh_budget_cancels_second_statement(dashboard_db):
    from test_runtime_dashboard_postgres import as_role
    attempted = []
    delay = [0]
    class Connection:
        def __init__(self, *args, **kwargs):
            self.raw = as_role('dashboard_metadata_read')(*args, **kwargs)
        def __enter__(self):
            self.raw.__enter__()
            return self
        def __exit__(self, *args):
            return self.raw.__exit__(*args)
        def execute(self, sql, params=None):
            if sql.lower().startswith('select'):
                attempted.append(sql)
                if delay[0]:
                    self.raw.execute('select pg_sleep(%s)', (delay[0],))
            return self.raw.execute(sql, params) if params is not None else self.raw.execute(sql)
    # Only this reader's granted metadata schema, never a live DB or cloud source.
    reader = HrMetadataReader(dashboard_db, connect=Connection, ttl_seconds=0)
    good = reader.snapshot(NOW)
    assert good.evidence.complete
    attempted.clear()
    delay[0] = .07
    start = time.monotonic()
    failed = reader.snapshot(NOW+timedelta(seconds=1), deadline=start+.12)
    elapsed = time.monotonic()-start
    assert failed.evidence.error == 'hr_metadata_unavailable'
    assert failed.evidence.observed_at == NOW
    assert len(attempted) == 2 and elapsed < .3
    print('REAL_PG_BUDGET', {'seconds': elapsed, 'attempted_selects': len(attempted), 'decrypts': reader.stats['decrypts']})


from test_runtime_dashboard_postgres import dashboard_db  # noqa: E402,F401
from test_observability_migration_runner import observability_database  # noqa: E402,F401


def test_thirty_day_projection_indexes_each_source_once():
    from app.runtime_dashboard.repository import SourceData, Contribution, Turn
    from app.runtime_dashboard.models import SourceEvidence
    class Turns(tuple):
        reads = 0
        def __iter__(self):
            self.reads += 1
            return super().__iter__()
    turns = Turns((Turn(NOW-timedelta(seconds=1), 1, 0, None, 'inputs_created_at'),)*1000)
    data = SourceData(SourceEvidence(source_id='native-hr', source_kind='hr', status='fresh',
        complete=True, observed_at=NOW, checked_at=NOW),
        (Contribution('thread', 'hr-bot', 'hr', None, NOW, NOW, turns),))
    result = DashboardService().overview(30, now=NOW, sources={'hr-bot': data})
    assert result.summary.usage.turns.value == result.last_24h.turns.value == 1000
    assert sum(day.turns.value or 0 for day in result.trend) == 1000
    assert turns.reads == 1


def test_maximum_hr_projection_cost_and_half_open_day_membership():
    from app.runtime_dashboard.repository import SourceData, Contribution, Turn
    from app.runtime_dashboard.models import SourceEvidence
    turn = Turn(NOW-timedelta(seconds=1), 1, 0, None, 'inputs_created_at')
    data = SourceData(SourceEvidence(source_id='native-hr', source_kind='hr', status='fresh',
        complete=True, observed_at=NOW, checked_at=NOW),
        tuple(Contribution(str(i), 'hr-bot', 'hr', None, NOW, NOW, (turn,)*10) for i in range(10000)))
    start = time.monotonic()
    result = DashboardService().overview(30, now=NOW, sources={'hr-bot': data})
    elapsed = time.monotonic()-start
    assert result.summary.usage.sessions.value == 10000
    assert result.summary.usage.turns.value == result.last_24h.turns.value == 100000
    assert sum(day.turns.value or 0 for day in result.trend) == 100000
    assert result.summary.active_agents_15m.value == 1
    print('MAX_HR_PROJECTION', {'seconds': elapsed, 'threads': 10000, 'inputs': 100000, 'days': 30})


def test_sdk_abandoned_actor_check_keeps_its_slot_and_never_reuses_grants():
    from fastapi import Request
    from app.runtime_dashboard.workspaces import SdkSummaryReader, sdk_summary
    release, entered = threading.Event(), threading.Event()
    actor = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), False)
    calls = []
    class Access:
        def me(self, current):
            calls.append(current.internal_user_id)
            entered.set()
            release.wait(1)
            return {'role': 'viewer'}
    class Catalog:
        def catalog(self):
            return {'generated_at': NOW.isoformat(), 'release_id': 'r',
                'counts': {'documents': 5, 'knowledge_pages': 2, 'knowledge_navigation': 1}}
    app = FastAPI()
    app.state.sdk_service, app.state.sdk_admin_repository = Access(), Catalog()
    request = Request({'type': 'http', 'app': app, 'state': {'auth_context': actor}})
    reader = SdkSummaryReader(actor_limit=1)
    def workspace():
        return next(a for a in DashboardService().overview().applications if a.id == 'sdk')
    async def scenario():
        first = asyncio.create_task(sdk_summary(request, workspace(), NOW, reader=reader,
            deadline=time.monotonic()+.08))
        while not entered.is_set():
            await asyncio.sleep(.001)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = workspace()
        start = time.monotonic()
        await sdk_summary(request, second, NOW, reader=reader, deadline=start+.08)
        assert time.monotonic()-start < .2
        assert second.business_metrics['documents'].value is None
        assert second.source.error == 'sdk_catalog_unavailable'
        assert calls == [actor.internal_user_id]
        release.set()
        while reader.actors._active:
            await asyncio.sleep(.001)
        third = workspace()
        await sdk_summary(request, third, NOW, reader=reader, deadline=time.monotonic()+.1)
        assert third.business_metrics['documents'].value == 5
        assert calls == [actor.internal_user_id, actor.internal_user_id]
    try:
        asyncio.run(scenario())
    finally:
        release.set()
