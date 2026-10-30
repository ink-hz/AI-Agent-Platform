from datetime import UTC, datetime, timedelta
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

NOW = datetime(2026, 10, 30, 2, tzinfo=UTC)


def test_inventory_and_unknowns_are_explicit():
    from app.runtime_dashboard.service import DashboardService
    data = DashboardService(clock=lambda: NOW).overview(7)
    assert data.inventory.model_dump() == dict(applications=7, agent_services=5, business_workspaces=2,
        configured_agents=10, channel_agents=5, legacy_streams=1)
    assert len(data.applications) == 7
    assert len(data.channel_agents) == 5
    assert data.legacy_streams[0].id == 'legacy-hr-feishu'
    assert all(a.usage.retained.turns.value is None for a in data.applications)
    assert all(a.runtime.state == 'unknown' for a in data.applications)
    assert data.window.start == datetime(2026, 10, 23, 16, tzinfo=UTC)
    assert data.window.end == NOW


def test_host_observation_exact_contract_and_object_time_isolation(tmp_path):
    from app.runtime_dashboard.health import HostObservationReader
    path = tmp_path / 'snapshot.json'
    payload = dict(schema_version=1, generated_at=NOW.isoformat(), observations=[
        dict(application_id='hr-bot', state='healthy', observed_at=NOW.isoformat(), error=None, version='a'*40),
        dict(application_id='ai-admin-agent', state='healthy', observed_at=(NOW-timedelta(seconds=91)).isoformat(), error=None, version=None)])
    path.write_text(json.dumps(payload))
    result = HostObservationReader(path).read(NOW)
    assert result['hr-bot'].runtime.state == 'healthy'
    assert result['hr-bot'].deployment.version == 'a'*40
    assert result['ai-admin-agent'].runtime.state == 'unknown'
    assert result['ai-admin-agent'].runtime.error == 'observation_stale'
    payload['observations'][1]['observed_at'] = (NOW+timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(payload))
    assert HostObservationReader(path).read(NOW)['ai-admin-agent'].runtime.error == 'observation_future'
    payload['observations'].append(payload['observations'][0])
    path.write_text(json.dumps(payload))
    assert all(x.runtime.state == 'unknown' for x in HostObservationReader(path).read(NOW).values())


def test_host_missing_and_invalid_never_claim_offline(tmp_path):
    from app.runtime_dashboard.health import HostObservationReader
    path = tmp_path / 'snapshot.json'
    for text in (None, '{}', 'null', '{"schema_version": 2}', 'not-json'):
        if text is not None:
            path.write_text(text)
        assert all(x.runtime.state == 'unknown' for x in HostObservationReader(path).read(NOW).values())


def test_contribution_counts_raw_inputs_and_half_open_windows_without_content():
    from app.runtime_dashboard.repository import contribution
    from app.runtime_dashboard.service import usage_for
    from app.runtime_dashboard.models import SourceEvidence
    record = {'key':'s','agent_id':'voc','source_kind':'voc','channel':'dingtalk',
        'created_at':NOW.isoformat(),'last_active_at':NOW.isoformat(), 'turns':[
        {'key':'a','created_at':(NOW-timedelta(seconds=1)).isoformat(), 'answer':{'text':'SECRET'}, 'outcome':'sent'},
        {'key':'b','created_at':NOW.isoformat(), 'answer':{'text':''}, 'outcome':'delivery_pending'}]}
    item = contribution(record)
    assert 'SECRET' not in repr(item)
    evidence = SourceEvidence(source_id='voc', source_kind='voc', channel='dingtalk', status='fresh', complete=True, checked_at=NOW)
    usage = usage_for([item], evidence, NOW-timedelta(days=7), NOW, delivery_supported=True)
    assert usage.retained.turns.value == 2
    assert usage.window.turns.value == 1
    assert usage.window.answered_turns.value == 1
    assert usage.window.deliveries.value == 1
    assert usage.outcome_counts == {'sent': 1, 'delivery_pending': 1}


@pytest.mark.parametrize('agent,family,channel,expected', [
    ('ai-fae-agent','fae','fae','ai-fae-agent'), ('ai-daq-fae-agent','fae','fae','ai-daq-fae-agent'),
    ('voc','voc','dingtalk','voc'), ('ai-admin-agent','admin',None,'ai-admin-agent'),
    ('ai-admin-agent','admin','dingtalk','ai-admin-agent'), ('voc','voc','new-channel','voc'),
    ('hr-bot','metabot','feishu','legacy-hr-feishu'), ('hr-bot','fae','fae',None),
    ('marketing-inbound-bot','metabot','feishu','marketing-inbound-bot')])
def test_production_source_channel_partition(agent,family,channel,expected):
    from app.runtime_dashboard.repository import source_key
    assert source_key(agent,family,channel) == expected


def test_http_health_independent_failures_and_singleflight():
    import asyncio
    import httpx
    from app.runtime_dashboard.health import HealthReader
    calls = []
    def handle(request):
        calls.append(request.url.host)
        return httpx.Response(200,json={'status':'ok'}) if request.url.host == 'fae.local' else httpx.Response(503)
    async def run():
        reader = HealthReader(urls={'ai-fae-agent':'http://fae.local/health','ai-daq-fae-agent':'http://daq.local/health'},transport=httpx.MockTransport(handle))
        values = await asyncio.gather(*(reader.read(NOW) for _ in range(5)))
        assert values[0]['ai-fae-agent'].runtime.state == 'healthy'
        assert values[0]['ai-daq-fae-agent'].runtime.state == 'degraded'
        assert values[0]['voc'].runtime.state == 'unknown'
    asyncio.run(run())
    assert sorted(calls) == ['daq.local','fae.local']


@pytest.mark.parametrize('now,days',[(datetime(2027,1,1,0,tzinfo=UTC),7),(datetime(2028,3,1,0,tzinfo=UTC),30)])
def test_shared_windows_trend_ranking_and_24h_boundary(now,days):
    from app.runtime_dashboard.models import SourceEvidence
    from app.runtime_dashboard.repository import SourceData,Contribution,Turn
    from app.runtime_dashboard.service import DashboardService
    turns=(Turn(now-timedelta(hours=24),1,1,'sent'),Turn(now-timedelta(seconds=1),0,0,'delivery_pending'),Turn(now,1,1,'sent'))
    data=SourceData(SourceEvidence(source_id='voc',source_kind='voc',status='fresh',complete=True,
        observed_at=now,checked_at=now), (Contribution('s','voc','voc','dingtalk',now-timedelta(days=2),now,turns),))
    class Reader:
        def snapshot(self, at):
            assert at==now
            return {'voc':data}
    overview=DashboardService(replica=Reader(),clock=lambda:now).overview(days)
    assert overview.summary.usage.turns.value==2
    assert overview.summary.usage.turns.status=='partial'
    assert overview.last_24h.turns.value==2
    assert sum(day.turns.value for day in overview.trend)==2
    assert overview.ranking[0].turns.value==2
    assert len(overview.trend)==days
    assert overview.rolling_24h.end==overview.window.end==overview.generated_at


def test_health_real_local_http_does_not_follow_redirects():
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from app.runtime_dashboard.health import HealthReader
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header('Location','/business-mutation')
            self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        reader=HealthReader(urls={'ai-fae-agent':f'http://127.0.0.1:{server.server_port}/health'})
        result=asyncio.run(reader.read(NOW))
        assert result['ai-fae-agent'].runtime.state=='unknown'
        assert seen==['/health']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cached_health_does_not_extend_host_observation_ttl(tmp_path):
    import asyncio
    from app.runtime_dashboard.health import HealthReader, HostObservationReader
    path=tmp_path/'snapshot.json'
    path.write_text(json.dumps(dict(schema_version=1,generated_at=NOW.isoformat(),observations=[
        dict(application_id='hr-bot',state='healthy',observed_at=NOW.isoformat(),error=None,version=None)])))
    async def run():
        reader=HealthReader(host=HostObservationReader(path))
        assert (await reader.read(NOW))['hr-bot'].runtime.state=='healthy'
        assert (await reader.read(NOW+timedelta(seconds=91)))['hr-bot'].runtime.state=='unknown'
    asyncio.run(run())


def test_management_metadata_uses_authorization_configured_catalog():
    from app.agent_brain.authorization import AgentUseAuthorization
    from app.agent_catalog import load_agent_catalog
    auth=object.__new__(AgentUseAuthorization)
    auth._catalog_cards=load_agent_catalog()
    assert auth.catalog_cards is auth._catalog_cards


def test_host_fifo_cannot_block_concurrent_health_readers(tmp_path):
    """Kill/reap the whole isolated reader process if a regression blocks open."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    path = tmp_path/'snapshot.json'
    os.mkfifo(path)
    child = '''import asyncio, sys
from datetime import UTC, datetime
import httpx
from app.runtime_dashboard.health import HealthReader, HostObservationReader
async def run():
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'status':'ok'})
    reader = HealthReader(host=HostObservationReader(sys.argv[1]), urls={'ai-fae-agent':'http://fae.local/health'}, transport=httpx.MockTransport(handle))
    print('ENTER_READER', flush=True)
    values = await asyncio.gather(*(reader.read(datetime.now(UTC)) for _ in range(4)))
    for value in values:
        assert value['hr-bot'].runtime.error == 'observation_invalid'
        assert value['ai-admin-agent'].runtime.error == 'observation_invalid'
        assert value['ai-fae-agent'].runtime.state == 'healthy'
    assert len(calls) == 1
    assert not reader._lock.locked()
    print('READERS_COMPLETED', flush=True)
asyncio.run(run())
'''
    try:
        result = subprocess.run([sys.executable,'-c',child,str(path)],capture_output=True,text=True,timeout=3,cwd=Path(__file__).resolve().parents[1])
    except subprocess.TimeoutExpired as error:
        pytest.fail(f'FIFO blocked health readers; killed/reaped child; stdout={error.stdout!r}')
    assert result.returncode == 0,result.stderr
    assert 'READERS_COMPLETED' in result.stdout


@pytest.mark.parametrize('kind',['directory','symlink','oversized','corrupt'])
def test_host_unsafe_files_are_unknown(tmp_path,kind):
    from app.runtime_dashboard.health import HostObservationReader
    path = tmp_path/'snapshot.json'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'symlink':
        target = tmp_path/'valid.json'
        target.write_text(json.dumps(dict(schema_version=1,generated_at=NOW.isoformat(),observations=[
            dict(application_id='hr-bot',state='healthy',observed_at=NOW.isoformat(),error=None,version=None)])))
        path.symlink_to(target)
    elif kind == 'oversized':
        path.write_bytes(b' '*16_385)
    else:
        path.write_text('not json')
    values = HostObservationReader(path).read(NOW)
    assert all(value.runtime.state == 'unknown' and value.runtime.error == 'observation_invalid' for value in values.values())


@pytest.mark.parametrize('failure',[None,'fstat','read','oversized','corrupt','directory'])
def test_host_descriptor_closed_after_success_and_every_opened_failure(tmp_path,monkeypatch,failure):
    import errno
    import os
    from app.runtime_dashboard import health
    path = tmp_path/'snapshot.json'
    if failure == 'directory':
        path.mkdir()
    else:
        path.write_text(json.dumps(dict(schema_version=1,generated_at=NOW.isoformat(),observations=[
            dict(application_id='hr-bot',state='healthy',observed_at=NOW.isoformat(),error=None,version=None)])))
        if failure == 'oversized':
            path.write_bytes(b' '*16_385)
        elif failure == 'corrupt':
            path.write_text('not json')
    opened,reads = [],[]
    real_open,real_read,real_fstat = os.open,os.read,os.fstat
    def tracked_open(target,flags):
        assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW
        descriptor = real_open(target,flags)
        opened.append(descriptor)
        return descriptor
    def checked_fstat(descriptor):
        if failure == 'fstat':
            raise OSError('injected descriptor failure')
        return real_fstat(descriptor)
    def checked_read(descriptor,size):
        reads.append(descriptor)
        assert descriptor in opened and size == 16_385
        if failure == 'read':
            raise OSError('injected read failure')
        return real_read(descriptor,size)
    monkeypatch.setattr(health.os,'open',tracked_open)
    monkeypatch.setattr(health.os,'fstat',checked_fstat)
    monkeypatch.setattr(health.os,'read',checked_read)
    result = health.HostObservationReader(path).read(NOW)
    assert len(opened) == 1
    for descriptor in opened:
        with pytest.raises(OSError) as error:
            real_fstat(descriptor)
        assert error.value.errno == errno.EBADF
    if failure in {'fstat','oversized','directory'}:
        assert not reads  # prove rejection precedes body IO on the opened object
    if failure is None:
        assert result['hr-bot'].runtime.state == 'healthy'
    else:
        assert all(value.runtime.error == 'observation_invalid' for value in result.values())
