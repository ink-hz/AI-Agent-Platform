"""Disposable real PG: metadata-only HR role and encrypted replica incrementality."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import base64
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.rows import dict_row
import pytest
from test_observability_migration_runner import observability_database  # noqa: F401

NOW = datetime(2026,10,30,2,tzinfo=UTC)
ROOT = Path(__file__).parents[2]


@pytest.fixture(scope='module')
def dashboard_db(observability_database):
    dsn = observability_database
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute('create role platform_control_owner nologin')
        c.execute('create role platform_control_app nologin')
        c.execute('create role dashboard_metadata_read nologin')
        c.execute('grant create on database postgres to platform_control_owner')
        c.execute('set role platform_control_owner')
        c.execute((ROOT/'backend/control_migrations/hr_agent/096_hr_agent_runtime.sql').read_text())
        c.execute('reset role')
        c.execute('grant usage on schema platform_hr_agent to dashboard_metadata_read')
        for table, cols in [('threads','thread_id,created_at'), ('works','work_id,thread_id,created_at,last_active_at,state,answer_state'),
                            ('inputs','work_id,revision,created_at'), ('entries','work_id,input_revision,created_at,kind'),
                            ('results','result_id,created_at')]:
            c.execute(f'grant select({cols}) on platform_hr_agent.{table} to dashboard_metadata_read')
        c.execute((ROOT/'backend/migrations/008_cloud_replica.sql').read_text())
        c.execute((ROOT/'backend/migrations/cloud_agent_sync/001_agent_sources.sql').read_text())
    return dsn


def as_role(role):
    def connect(*args, **kwargs):
        c = psycopg.connect(*args, **kwargs)
        c.execute(f'set session authorization {role}')
        c.commit()
        return c
    return connect


def seed_hr_data(dashboard_db):
    owner, thread, work = uuid4(), uuid4(), uuid4()
    with psycopg.connect(dashboard_db) as c:
        c.execute("truncate platform_hr_agent.threads cascade")
        c.execute('insert into platform_hr_agent.threads(owner_id,thread_id,sealed_title,sealed_title_key_version,created_at) values (%s,%s,%s,1,%s)',(owner,thread,b'SECRET',NOW-timedelta(days=2)))
        c.execute('''insert into platform_hr_agent.works(owner_id,work_id,thread_id,state,answer_state,sealed_budget,sealed_budget_key_version,sealed_checkpoint,sealed_checkpoint_key_version,created_at,last_active_at)
            values (%s,%s,%s,'failed','partial',%s,1,%s,1,%s,%s)''',(owner,work,thread,b'SECRET',b'SECRET',NOW-timedelta(days=2),NOW-timedelta(seconds=1)))
        for revision, at in [(1,NOW-timedelta(hours=1)), (2,NOW)]:
            c.execute('''insert into platform_hr_agent.inputs(owner_id,work_id,revision,input_id,sealed_input,sealed_input_key_version,objects,role_release,role_manifest_sha,knowledge_release,configuration_revision,created_at)
                values (%s,%s,%s,%s,%s,1,'[]','r',%s,'k','c',%s)''',(owner,work,revision,uuid4(),b'SECRET','a'*64,at))
        for seq, kind in [(1,'assistant'),(2,'assistant'),(3,'question')]:
            c.execute('''insert into platform_hr_agent.entries(owner_id,entry_id,work_id,input_revision,seq,kind,objects,sealed_body,sealed_body_key_version,source_refs,created_at)
                values (%s,%s,%s,1,%s,%s,'[]',%s,1,'[]',%s)''',(owner,uuid4(),work,seq,kind,b'SECRET',NOW-timedelta(minutes=30)))
    return owner


@pytest.mark.postgres
def test_hr_real_metadata_reader_no_body_grants_and_multi_reply_dedup(dashboard_db):
    from app.runtime_dashboard.native import HrMetadataReader
    from app.runtime_dashboard.service import usage_for
    with psycopg.connect(dashboard_db) as c:
        c.execute('truncate platform_hr_agent.threads cascade')
        c.execute('grant select(created_at) on platform_hr_agent.inputs to dashboard_metadata_read')
    reader = HrMetadataReader(dashboard_db, connect=as_role('dashboard_metadata_read'), ttl_seconds=0)
    empty = reader.snapshot(NOW)
    assert empty.evidence.complete and empty.evidence.reader_role == 'dashboard_metadata_read'
    assert usage_for(empty.contributions, empty.evidence, NOW-timedelta(days=7), NOW).retained.turns.value == 0
    owner = seed_hr_data(dashboard_db)
    data = reader.snapshot(NOW)
    usage = usage_for(data.contributions,data.evidence,NOW-timedelta(days=7),NOW)
    assert usage.retained.sessions.value == 1
    assert usage.retained.turns.value == 2
    assert usage.window.turns.value == 1
    assert usage.window.answered_turns.value == 1
    assert usage.window.deliveries.value is None
    assert data.business['work_state_failed'].value == 1
    assert data.business['answer_state_partial'].value == 1
    assert data.business['question_records'].value == 1
    assert data.business['complete_answers'].value is None
    assert 'SECRET' not in repr(data) and str(owner) not in repr(data)
    with psycopg.connect(dashboard_db) as c:
        c.execute('revoke select(created_at) on platform_hr_agent.inputs from dashboard_metadata_read')
    failed = reader.snapshot(NOW+timedelta(seconds=1))
    assert failed.evidence.status == 'stale' and failed.evidence.observed_at == NOW
    assert len(failed.contributions) == 1


def put_record(c, cipher, key, agent='voc', outcome='sent'):
    family,channel = ('voc','dingtalk') if agent == 'voc' else ('fae','fae')
    record = dict(kind='session', key=key, user_id='private', title={'text':'PRIVATE'}, primary_sender_name=None, primary_sender_department=None, sanitizer_policy_version='v1', agent_id=agent, source_kind=family, channel=channel,
        created_at=(NOW-timedelta(days=1)).isoformat(), last_active_at=(NOW-timedelta(seconds=1)).isoformat(),
        turns=[dict(key=key+'-turn',created_at=(NOW-timedelta(seconds=1)).isoformat(),question={'text':'PRIVATE'},answer={'text':'ANSWER'},outcome=outcome)])
    raw = json.dumps(record,sort_keys=True,separators=(',',':'))
    sealed = cipher.encrypt(raw, f'1:session:{key}')
    decode = lambda x: base64.urlsafe_b64decode(x+'='*(-len(x)%4))
    c.execute('''insert into platform_replica.agents(agent_id,display_payload,payload_nonce,payload_sha256,updated_at) values (%s,%s,%s,%s,%s) on conflict do nothing''',(agent,b'x',b'x','a'*64,NOW))
    c.execute('''insert into platform_replica.sessions(session_key,user_id,agent_id,source_kind,channel,created_at,last_active_at,expires_at,generation_sequence,display_payload,payload_nonce,payload_sha256)
        values (%s,'private',%s,%s,%s,%s,%s,%s,1,%s,%s,%s) on conflict(session_key) do update set display_payload=excluded.display_payload,payload_nonce=excluded.payload_nonce,payload_sha256=excluded.payload_sha256''',
        (key,agent,family,channel,NOW-timedelta(days=1),NOW-timedelta(seconds=1),NOW+timedelta(days=90),decode(sealed['ciphertext']),decode(sealed['nonce']),hashlib.sha256(raw.encode()).hexdigest()))
    c.execute("select platform_replica.set_agent_publisher(%s,%s,'cloud_direct','cloud','platform_cloud_sync','test')",(agent,family))
    c.execute("update platform_replica.agent_sources set last_source_success_at=%s,last_published_at=%s,source_sessions=1,source_turns=1 where agent_id=%s",(NOW,NOW,agent))


@pytest.mark.postgres
def test_replica_real_encryption_incremental_delete_limit_failure_singleflight(dashboard_db):
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    cipher = FieldCipher(b'a'*32)
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        put_record(c,cipher,'voc-1')
        put_record(c,cipher,'fae-1','ai-fae-agent')
    clock = [0]
    replica = ReplicaObservabilityRepository(dashboard_db,cipher=cipher,connect=as_role('platform_replica_read'))
    reader = ReplicaDashboardReader(replica,timer=lambda:clock[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _:reader.snapshot(NOW), range(8)))
    assert results[0]['voc'].evidence.complete
    assert len(results[0]['voc'].contributions) == 1
    assert reader.stats == dict(metadata_reads=1,payload_reads=2,decrypts=2,refreshes=1)
    assert 'PRIVATE' not in repr(reader._cached) and 'ANSWER' not in repr(reader._cached)
    clock[0] = 31
    reader.snapshot(NOW)
    assert reader.stats['metadata_reads'] == 2 and reader.stats['decrypts'] == 2
    with psycopg.connect(dashboard_db) as c:
        put_record(c,cipher,'voc-1',outcome='delivery_failed')
    clock[0] = 62
    assert reader.snapshot(NOW)['voc'].contributions[0].turns[0].delivered == 0
    assert reader.stats['decrypts'] == 3
    with psycopg.connect(dashboard_db) as c:
        c.execute("update platform_replica.sessions set display_payload='broken', payload_sha256=%s where agent_id='voc'",('b'*64,))
    clock[0] = 93
    failed = reader.snapshot(NOW)
    assert failed['voc'].evidence.error == 'replica_payload_invalid'
    assert failed['ai-fae-agent'].evidence.status == 'fresh'
    with psycopg.connect(dashboard_db) as c:
        c.execute("delete from platform_replica.sessions where agent_id='voc'")
    clock[0] = 124
    incomplete = reader.snapshot(NOW)['voc']
    assert incomplete.evidence.complete is False
    assert len(incomplete.contributions) == 1  # unproven deletion cannot erase the last complete snapshot
    with psycopg.connect(dashboard_db) as c:
        c.execute("update platform_replica.agent_sources set source_sessions=0,source_turns=0 where agent_id='voc'")
    clock[0] = 155
    assert reader.snapshot(NOW)['voc'].contributions == ()
    assert 'voc-1' not in reader._cached
    limited = ReplicaDashboardReader(replica,max_sessions=0).snapshot(NOW)
    assert limited['voc'].evidence.error == 'replica_limit_exceeded'


@pytest.mark.postgres
def test_replica_thousand_session_cost_and_no_repeat_decryption(dashboard_db):
    from time import perf_counter
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    cipher=FieldCipher(b'z'*32)
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        for i in range(1000):
            put_record(c,cipher,f'cost-{i}')
        c.execute("update platform_replica.agent_sources set source_sessions=1000,source_turns=1000 where agent_id='voc'")
    timer=[0]
    reader=ReplicaDashboardReader(ReplicaObservabilityRepository(dashboard_db,cipher=cipher,connect=as_role('platform_replica_read')),timer=lambda:timer[0])
    start=perf_counter()
    first=reader.snapshot(NOW)
    cold_ms=(perf_counter()-start)*1000
    assert len(first['voc'].contributions)==1000 and first['voc'].evidence.complete
    first_stats=dict(reader.stats)
    start=perf_counter()
    reader.snapshot(NOW)
    hit_ms=(perf_counter()-start)*1000
    assert reader.stats==first_stats
    timer[0]=31
    start=perf_counter()
    reader.snapshot(NOW)
    unchanged_ms=(perf_counter()-start)*1000
    assert reader.stats['decrypts']==1000 and reader.stats['metadata_reads']==2
    print('DASHBOARD_COST '+json.dumps(dict(sessions=1000,cold_ms=round(cold_ms,2),cache_hit_ms=round(hit_ms,3),unchanged_refresh_ms=round(unchanged_ms,2),first=first_stats,second=reader.stats)))


@pytest.mark.postgres
def test_real_persisted_sources_to_http_contract(dashboard_db):
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    from app.runtime_dashboard.native import HrMetadataReader
    from app.runtime_dashboard.service import DashboardService
    from test_runtime_dashboard_http import client_for
    seed_hr_data(dashboard_db)
    with psycopg.connect(dashboard_db) as c:
        c.execute('grant select(created_at) on platform_hr_agent.inputs to dashboard_metadata_read')
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        put_record(c,FieldCipher(b'z'*32),'http-voc')
    replica=ReplicaDashboardReader(ReplicaObservabilityRepository(dashboard_db,cipher=FieldCipher(b'z'*32),connect=as_role('platform_replica_read')))
    service=DashboardService(replica=replica,hr=HrMetadataReader(dashboard_db,connect=as_role('dashboard_metadata_read')),clock=lambda:NOW)
    client,_=client_for(service=service)
    first=client.get('/api/dashboard/overview?days=30')
    assert first.status_code==200,first.text
    data=first.json()
    assert data['summary']['usage']['turns']['value']==2
    assert data['last_24h']['turns']['value']==2
    assert sum(item['turns']['value'] for item in data['trend'])==2
    assert data['inventory']['configured_agents']==10
    assert next(a for a in data['applications'] if a['id']=='hr-bot')['usage']['retained']['turns']['value']==2
    assert 'SECRET' not in first.text and 'PRIVATE' not in first.text and 'sealed_' not in first.text
    stats=dict(replica.stats)
    assert client.get('/api/dashboard/overview').status_code==200
    assert replica.stats==stats


@pytest.mark.postgres
def test_complete_empty_configured_channel_is_zero_not_missing(dashboard_db):
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    from app.runtime_dashboard.service import usage_for
    with psycopg.connect(dashboard_db) as c:
        c.execute("select platform_replica.set_agent_publisher('marketing-inbound-bot','metabot','office','office',NULL,'office-test')")
        c.execute("update platform_replica.agent_sources set source_sessions=0,source_turns=0,last_source_success_at=%s,last_published_at=%s where agent_id='marketing-inbound-bot'",(NOW,NOW))
    reader=ReplicaDashboardReader(ReplicaObservabilityRepository(dashboard_db,cipher=FieldCipher(b'z'*32),connect=as_role('platform_replica_read')))
    data=reader.snapshot(NOW)['marketing-inbound-bot']
    assert data.evidence.complete
    assert usage_for(data.contributions,data.evidence,NOW-timedelta(days=7),NOW).retained.turns.value==0


@pytest.mark.postgres
def test_incomplete_old_source_retention_cannot_grow_cache_past_bound(dashboard_db):
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    cipher=FieldCipher(b'a'*32)
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        put_record(c,cipher,'bound-old','ai-fae-agent')
    clock=[0]
    reader=ReplicaDashboardReader(ReplicaObservabilityRepository(dashboard_db,cipher=cipher,connect=as_role('platform_replica_read')),max_sessions=1,timer=lambda:clock[0])
    reader.snapshot(NOW)
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        put_record(c,cipher,'bound-new')
    clock[0]=31
    result=reader.snapshot(NOW)
    assert len(reader._cached)<=1
    assert result['voc'].evidence.error=='replica_limit_exceeded'


@pytest.mark.postgres
@pytest.mark.parametrize('field,value', [
    ('channel', 'web'), ('agent_id', 'ai-daq-fae-agent'), ('source_kind', 'voc'),
    ('created_at', NOW-timedelta(days=2)), ('last_active_at', NOW-timedelta(seconds=2)),
])
def test_replica_metadata_drift_revalidates_only_changed_row_and_rejects_cold_and_warm(dashboard_db, field, value):
    from psycopg import sql
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    cipher = FieldCipher(b'a'*32)
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        put_record(c,cipher,'drift-row','ai-fae-agent')
        put_record(c,cipher,'untouched-row')
        c.execute("insert into platform_replica.agents(agent_id,display_payload,payload_nonce,payload_sha256,updated_at) values ('ai-daq-fae-agent','x','x',%s,%s) on conflict do nothing", ('a'*64,NOW))
    def repository():
        return ReplicaObservabilityRepository(dashboard_db,cipher=cipher,connect=as_role('platform_replica_read'))
    warm = ReplicaDashboardReader(repository(),ttl_seconds=0)
    initial = warm.snapshot(NOW)
    assert initial['ai-fae-agent'].evidence.complete
    warm.snapshot(NOW)
    assert warm.stats['decrypts'] == warm.stats['payload_reads'] == 2
    with psycopg.connect(dashboard_db) as c:
        if field == 'agent_id':
            # The DB forbids UPDATE of agent ownership. A source replacement can
            # reuse its key: delete/reinsert atomically without disabling guards.
            row = c.cursor(row_factory=dict_row).execute('delete from platform_replica.sessions where session_key=%s returning *',('drift-row',)).fetchone()
            row[field] = value
            c.execute(sql.SQL('insert into platform_replica.sessions ({}) values ({})').format(
                sql.SQL(',').join(map(sql.Identifier,row)),sql.SQL(',').join(sql.Placeholder() for _ in row)),tuple(row.values()))
        else:
            c.execute(sql.SQL('update platform_replica.sessions set {}=%s where session_key=%s').format(sql.Identifier(field)),(value,'drift-row'))
    result = warm.snapshot(NOW+timedelta(seconds=1))
    cold = ReplicaDashboardReader(repository()).snapshot(NOW+timedelta(seconds=1))
    print('METADATA_DRIFT',field,dict(warm=result['ai-fae-agent'].evidence.model_dump(mode='json'),cold=cold['ai-fae-agent'].evidence.model_dump(mode='json'),stats=warm.stats))
    assert not result['ai-fae-agent'].evidence.complete
    assert result['ai-fae-agent'].evidence.status == 'stale'
    assert result['ai-fae-agent'].evidence.error == 'replica_payload_invalid'
    assert result['ai-fae-agent'].evidence.observed_at == initial['ai-fae-agent'].evidence.observed_at
    assert result['ai-fae-agent'].contributions == initial['ai-fae-agent'].contributions
    assert not cold['ai-fae-agent'].evidence.complete
    assert not cold['ai-fae-agent'].contributions
    assert result['voc'].evidence.complete and result['voc'].evidence.status == 'fresh'
    assert warm.stats['decrypts'] == warm.stats['payload_reads'] == 3
    assert len(warm._cached) == 2
    assert all(item.agent_id == key for key, data in result.items() for item in data.contributions)


@pytest.mark.postgres
def test_replica_matching_payload_and_metadata_change_is_incrementally_accepted(dashboard_db):
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    cipher = FieldCipher(b'a'*32)
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        put_record(c,cipher,'valid-change')
    reader = ReplicaDashboardReader(ReplicaObservabilityRepository(dashboard_db,cipher=cipher,connect=as_role('platform_replica_read')),ttl_seconds=0)
    assert reader.snapshot(NOW)['voc'].evidence.complete
    with psycopg.connect(dashboard_db) as c:
        # Change the same encrypted source contract on both sides, retaining its key.
        record = reader.repository._decrypt(c.cursor(row_factory=dict_row).execute('select * from platform_replica.sessions where session_key=%s',('valid-change',)).fetchone())
        record['channel'] = 'web'
        record['last_active_at'] = NOW.isoformat()
        raw = json.dumps(record,sort_keys=True,separators=(',',':'))
        sealed = cipher.encrypt(raw,'1:session:valid-change')
        decode = lambda value: base64.urlsafe_b64decode(value+'='*(-len(value)%4))
        c.execute('update platform_replica.sessions set channel=%s,last_active_at=%s,display_payload=%s,payload_nonce=%s,payload_sha256=%s where session_key=%s',
            ('web',NOW,decode(sealed['ciphertext']),decode(sealed['nonce']),hashlib.sha256(raw.encode()).hexdigest(),'valid-change'))
    result = reader.snapshot(NOW)['voc']
    assert result.evidence.complete and result.evidence.status == 'fresh'
    assert result.evidence.channels == ['web']
    assert result.contributions[0].last_active_at == NOW
    assert reader.stats['decrypts'] == 2
    reader.snapshot(NOW)
    assert reader.stats['decrypts'] == 2


@pytest.mark.postgres
def test_replica_original_voc_channel_fault_cold_and_warm(dashboard_db):
    from app.cloud_replica.crypto import FieldCipher
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.runtime_dashboard.repository import ReplicaDashboardReader
    cipher = FieldCipher(b'a'*32)
    with psycopg.connect(dashboard_db) as c:
        c.execute('delete from platform_replica.sessions')
        c.execute('update platform_replica.agent_sources set source_sessions=0,source_turns=0')
        put_record(c,cipher,'original-voc-drift')
    def repository():
        return ReplicaObservabilityRepository(dashboard_db,cipher=cipher,connect=as_role('platform_replica_read'))
    warm = ReplicaDashboardReader(repository(),ttl_seconds=0)
    initial = warm.snapshot(NOW)['voc']
    assert initial.evidence.complete
    with psycopg.connect(dashboard_db) as c:
        c.execute("update platform_replica.sessions set channel='web' where session_key='original-voc-drift'")
    result = warm.snapshot(NOW)['voc']
    cold = ReplicaDashboardReader(repository()).snapshot(NOW)['voc']
    assert result.evidence.error == cold.evidence.error == 'replica_payload_invalid'
    assert not result.evidence.complete and not cold.evidence.complete
    assert result.evidence.status == 'stale' and cold.evidence.status == 'unavailable'
    assert result.contributions == initial.contributions and not cold.contributions
    assert warm.stats['payload_reads'] == warm.stats['decrypts'] == 2
