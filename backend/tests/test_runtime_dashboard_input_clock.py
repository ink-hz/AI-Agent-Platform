"""Input cohorts use source input times, independently of later replies/updates."""
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import json

import psycopg
import pytest

from app.cloud_sync.projection import project_voc_snapshot
from app.cloud_replica.sanitize import SanitizationPolicy, sanitize_session
from app.cloud_replica.exporter import build_session_record
from app.runtime_dashboard.models import SourceEvidence
from app.runtime_dashboard.repository import SourceData, contribution
from app.runtime_dashboard.service import DashboardService, usage_for
from test_runtime_dashboard_postgres import dashboard_db, as_role, seed_hr_data, NOW  # noqa: F401
from test_observability_migration_runner import observability_database  # noqa: F401


def voc_record(question='2026-10-29T23:59:00+08:00', answer='2026-10-30T00:01:00+08:00'):
    raw, = project_voc_snapshot([dict(id=1, internal_user_id='11111111-1111-4111-8111-111111111111',
        conversation_id='input-clock', conversation_type='single', sender_name=None, sender_department=None,
        received_at=question, question_at=question, question='PRIVATE QUESTION', answer='PRIVATE REPLY',
        answer_at=answer, job_status='done', delivery_status='sent')])
    return build_session_record(raw, sanitize_session(raw, SanitizationPolicy()), b'k'*32)


def service_for(record):
    class Reader:
        def snapshot(self, now):
            return {'voc': SourceData(SourceEvidence(source_id='voc', source_kind='voc',
                status='fresh', complete=True, observed_at=now), (contribution(record),))}
    return DashboardService(replica=Reader())


@pytest.mark.parametrize('offset', ['+08:00', '+00:00'])
def test_real_voc_projection_cross_day_and_rolling24h_use_input_cohort(offset):
    record = voc_record()
    # The real producer deliberately persists the answer as record time.
    assert record['turns'][0]['created_at'].startswith('2026-10-29T16:01:00')
    now = datetime.fromisoformat('2026-10-31T00:00:00+08:00').astimezone(
        datetime.fromisoformat('2026-10-31T00:00:00'+offset).tzinfo)
    overview = service_for(record).overview(now=now)
    trend = {row.date: row for row in overview.trend}
    assert (trend['2026-10-29'].turns.value, trend['2026-10-30'].turns.value,
        overview.last_24h.turns.value) == (1, 0, 0)
    assert trend['2026-10-29'].answered_turns.value == trend['2026-10-29'].deliveries.value == 1
    assert trend['2026-10-30'].answered_turns.value == trend['2026-10-30'].deliveries.value == 0
    assert overview.ranking[0].turns.value == 1


@pytest.mark.parametrize('days,now', [(7,'2026-11-05T00:00:00+08:00'), (30,'2026-11-28T00:00:00+08:00')])
def test_calendar_windows_exclude_input_even_when_reply_is_inside(days, now):
    result = service_for(voc_record()).overview(days, now=datetime.fromisoformat(now))
    assert result.summary.usage.turns.value == result.ranking[0].turns.value == 0
    assert result.summary.usage.answered_turns.value == result.summary.usage.deliveries.value == 0


@pytest.mark.parametrize('question,status', [('2026-10-29T15:59:00Z','exact'), ('2026-10-29T23:59:00+08:00','estimated')])
def test_valid_supplied_input_time_wins_and_reply_cannot_refresh_last_input(question, status):
    record = voc_record()
    record['turns'][0].update(question_at=question, question_time_status=status)
    now = datetime.fromisoformat('2026-10-30T00:15:00+08:00')
    item = contribution(record)
    use = usage_for([item], SourceEvidence(source_id='voc',source_kind='voc',status='fresh',complete=True),
        now-timedelta(days=1),now,delivery_supported=True)
    assert use.last_activity_at == datetime.fromisoformat('2026-10-29T15:59:00+00:00')
    assert use.last_activity_at < now-timedelta(minutes=15)  # reply was within15min, input was not
    assert item.last_active_at == datetime.fromisoformat(record['last_active_at'].replace('Z','+00:00'))
    assert '输入群组' in use.window.answered_turns.definition
    assert '回复事件' in use.window.answered_turns.definition
    assert 'record.created_at' in use.window.turns.definition


@pytest.mark.parametrize('question', ['not-a-timestamp', '2026-10-29T23:59:00', ''])
def test_invalid_supplied_question_time_is_rejected_without_proxy(question):
    record = voc_record()
    record['turns'][0]['question_at'] = question
    with pytest.raises(ValueError):
        contribution(record)


def persist_record(c, cipher, record):
    raw = json.dumps(record,sort_keys=True,separators=(',',':'))
    sealed = cipher.encrypt(raw, f"1:session:{record['key']}")
    decode = lambda value: base64.urlsafe_b64decode(value+'='*(-len(value)%4))
    c.execute('insert into platform_replica.agents(agent_id,display_payload,payload_nonce,payload_sha256,updated_at) values (%s,%s,%s,%s,%s) on conflict do nothing',
        (record['agent_id'],b'x',b'x','a'*64,NOW))
    c.execute('''insert into platform_replica.sessions(session_key,user_id,agent_id,source_kind,channel,created_at,last_active_at,expires_at,generation_sequence,display_payload,payload_nonce,payload_sha256)
        values (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s) on conflict(session_key) do update set display_payload=excluded.display_payload,payload_nonce=excluded.payload_nonce,payload_sha256=excluded.payload_sha256''',
        (record['key'],record['user_id'],record['agent_id'],record['source_kind'],record['channel'],record['created_at'],record['last_active_at'],NOW+timedelta(days=90),
         decode(sealed['ciphertext']),decode(sealed['nonce']),hashlib.sha256(raw.encode()).hexdigest()))
    c.execute("select platform_replica.set_agent_publisher(%s,%s,'cloud_direct','cloud','platform_cloud_sync','input-clock-test')",(record['agent_id'],record['source_kind']))
    c.execute('update platform_replica.agent_sources set last_source_success_at=%s,last_published_at=%s,source_sessions=1,source_turns=%s where agent_id=%s',
        (NOW,NOW,len(record['turns']),record['agent_id']))


@pytest.mark.postgres
def test_real_encrypted_producer_clock_evidence_incremental_and_http(dashboard_db):
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    from test_runtime_dashboard_http import client_for
    cipher = FieldCipher(b'c'*32)
    record = voc_record()
    daq = voc_record()
    daq.update(key='clock-daq',agent_id='ai-daq-fae-agent',source_kind='fae',channel='fae')
    daq['turns'][0].pop('question_at')
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        persist_record(c,cipher,record)
        persist_record(c,cipher,daq)
    reader = ReplicaDashboardReader(ReplicaObservabilityRepository(dashboard_db,cipher=cipher,
        connect=as_role('platform_replica_read')),ttl_seconds=0)
    data = reader.snapshot(NOW)
    assert data['voc'].evidence.input_time_basis == 'question_at'
    assert data['voc'].evidence.missing_question_time_records == 0
    assert data['ai-daq-fae-agent'].evidence.input_time_basis == 'record_created_at'
    assert data['ai-daq-fae-agent'].evidence.missing_question_time_records == 1
    assert data['ai-daq-fae-agent'].contributions[0].turns[0].at == datetime.fromisoformat('2026-10-29T16:01:00+00:00')
    initial_client,_ = client_for(service=DashboardService(replica=reader,
        clock=lambda:datetime.fromisoformat('2026-10-31T00:00:00+08:00')))
    initial = initial_client.get('/api/dashboard/overview').json()
    daily = {row['date']:row['turns']['value'] for row in initial['trend']}
    assert (daily['2026-10-29'],daily['2026-10-30'],initial['last_24h']['turns']['value']) == (1,1,1)
    # Only DAQ's declared record-time proxy crosses midnight; VOC's exact input does not.
    assert next(a for a in initial['applications'] if a['id']=='voc')['usage']['last_activity_at'] == '2026-10-29T15:59:00Z'
    reader.snapshot(NOW)
    assert reader.stats['decrypts'] == reader.stats['payload_reads'] == 2
    # Only a ciphertext update changes one source's input anchor; source metadata stays authenticated.
    record['turns'][0]['question_at'] = None
    with psycopg.connect(dashboard_db) as c:
        persist_record(c,cipher,record)
    changed = reader.snapshot(NOW)['voc']
    assert changed.evidence.complete and changed.evidence.input_time_basis == 'record_created_at'
    assert changed.evidence.missing_question_time_records == 1
    assert reader.stats['decrypts'] == reader.stats['payload_reads'] == 3
    record['turns'].append(dict(record['turns'][0],key='second-clock-turn',question_at='2026-10-29T15:59:00Z'))
    with psycopg.connect(dashboard_db) as c:
        persist_record(c,cipher,record)
    mixed = reader.snapshot(NOW)['voc']
    assert mixed.evidence.input_time_basis == 'mixed' and mixed.evidence.missing_question_time_records == 1
    client,_ = client_for(service=DashboardService(replica=reader,clock=lambda:NOW))
    response = client.get('/api/dashboard/overview')
    assert response.status_code == 200
    voc = next(a for a in response.json()['applications'] if a['id']=='voc')
    assert voc['source']['input_time_basis'] == 'mixed'
    assert voc['source']['missing_question_time_records'] == 1
    assert 'PRIVATE' not in response.text and 'PRIVATE' not in repr(reader._cached)
    # Invalid supplied time retains warm evidence; a cold failed source has no clock coverage proof.
    record['turns'][0]['question_at'] = 'invalid'
    with psycopg.connect(dashboard_db) as c:
        persist_record(c,cipher,record)
    failed = reader.snapshot(NOW)['voc']
    assert failed.evidence.status == 'stale' and failed.evidence.error == 'replica_payload_invalid'
    assert failed.evidence.input_time_basis == 'mixed' and failed.evidence.missing_question_time_records == 1
    cold = ReplicaDashboardReader(reader.repository).snapshot(NOW)['voc']
    assert cold.evidence.status == 'unavailable'
    assert cold.evidence.input_time_basis is None and cold.evidence.missing_question_time_records is None


@pytest.mark.postgres
def test_native_hr_last_input_ignores_assistant_and_work_activity(dashboard_db):
    from app.runtime_dashboard.native import HrMetadataReader
    with psycopg.connect(dashboard_db) as c:
        c.execute('grant select(created_at) on platform_hr_agent.inputs to dashboard_metadata_read')
    seed_hr_data(dashboard_db)
    # Make both inputs old while retaining the very recent work timestamp/assistant entry.
    with psycopg.connect(dashboard_db) as c:
        c.execute('update platform_hr_agent.inputs set created_at=%s',(NOW-timedelta(hours=1),))
    data = HrMetadataReader(dashboard_db,connect=as_role('dashboard_metadata_read')).snapshot(NOW)
    assert data.evidence.input_time_basis == 'inputs_created_at'
    assert data.evidence.missing_question_time_records == 0
    usage = usage_for(data.contributions,data.evidence,NOW-timedelta(days=1),NOW)
    assert usage.last_activity_at == NOW-timedelta(hours=1)
    assert usage.window.turns.value == 2 and usage.window.answered_turns.value == 1


def test_empty_session_has_no_last_input_activity():
    record = voc_record()
    record['turns'] = []
    use = usage_for([contribution(record)],SourceEvidence(source_id='voc',source_kind='voc',status='fresh',complete=True),
        NOW-timedelta(days=7),NOW)
    assert use.last_activity_at is None


def test_active_agents_15m_uses_same_input_clock_and_unknown_coverage():
    assert DashboardService().overview(now=NOW).summary.active_agents_15m.value is None
    record = voc_record(question='2026-10-30T09:44:00+08:00',answer='2026-10-30T09:59:00+08:00')
    old = service_for(record).overview(now=NOW)
    assert old.summary.active_agents_15m.value == 0
    assert old.summary.active_agents_15m.status == 'partial'
    record['turns'][0]['question_at'] = (NOW-timedelta(minutes=15)).isoformat()
    active = service_for(record).overview(now=NOW)
    assert active.summary.active_agents_15m.value == 1
    record['turns'][0]['question_at'] = NOW.isoformat()
    assert service_for(record).overview(now=NOW).summary.active_agents_15m.value == 0


def test_active_agents_complete_precision_proxy_and_channel_exclusion():
    from app.runtime_dashboard.repository import DIRECT
    base = voc_record(question=(NOW-timedelta(minutes=1)).isoformat(),answer=NOW.isoformat())
    items = {}
    for key in (*DIRECT,'hr-bot','marketing-inbound-bot','legacy-hr-feishu'):
        basis = 'inputs_created_at' if key == 'hr-bot' else 'question_at'
        family = 'hr' if key == 'hr-bot' else DIRECT.get(key,('metabot',()))[0]
        evidence = SourceEvidence(source_id=key,source_kind=family,status='fresh',complete=True,
            input_time_basis=basis,missing_question_time_records=0)
        record = dict(base,agent_id=key)
        items[key] = SourceData(evidence,(contribution(record),))
    class Reader:
        def snapshot(self, now):
            return items
    class Hr:
        def snapshot(self, now):
            return items['hr-bot']
    service = DashboardService(replica=Reader(),hr=Hr())
    exact = service.overview(now=NOW).summary.active_agents_15m
    assert (exact.value,exact.status) == (5,'observed')
    record = dict(base)
    record['turns'] = [dict(base['turns'][0],question_at=None)]
    items['voc'].contributions = (contribution(record),)
    proxy = service.overview(now=NOW).summary.active_agents_15m
    assert (proxy.value,proxy.status,proxy.reason) == (4,'partial','source_coverage_or_input_time_partial')
    # Retained numeric truth remains observed; only proxy-based windows lose precision.
    use = usage_for(items['voc'].contributions,items['voc'].evidence,NOW-timedelta(days=1),NOW)
    assert use.retained.turns.status == 'observed'
    assert use.window.turns.status == 'partial' and use.window.turns.reason == 'input_time_proxy'


def test_dashboard_fixture_and_schema_include_additive_clock_contract():
    from pathlib import Path
    from app.runtime_dashboard.models import DashboardOverview
    folder = Path(__file__).parent/'fixtures'
    model = DashboardOverview.model_validate_json((folder/'runtime_dashboard_v1.json').read_text())
    assert json.loads((folder/'runtime_dashboard_v1.schema.json').read_text()) == DashboardOverview.model_json_schema()
    assert model.schema_version == 1 and model.summary.active_agents_15m.unit == 'agent'
    daq = next(row for row in model.applications if row.id=='ai-daq-fae-agent')
    assert (daq.source.input_time_basis,daq.source.missing_question_time_records) == ('record_created_at',1)
    assert daq.usage.window.turns.status == 'partial'
