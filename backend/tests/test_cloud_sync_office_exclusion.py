from datetime import UTC, datetime, timedelta
import json

import psycopg
from psycopg import sql
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
import pytest

from app.cloud_replica import cli
from app.cloud_replica.crypto import BatchVerifier
from app.cloud_replica.models import ReviewFeedbackTotalsProjection
from app.cloud_replica.protocol import BatchLimits,decode_and_verify_batch
from app.cloud_replica.source import ReplicaSource
from test_cloud_source import _Connection
from test_observability_migration_runner import observability_database

NOW = datetime(2026,10,29,9,tzinfo=UTC)
EXCLUDED = ('ai-fae-agent','ai-daq-fae-agent','ai-admin-agent','voc')
ENVIRONMENT = 'PLATFORM_REPLICA_EXCLUDED_AGENT_IDS'


def test_source_passes_exclusion_to_candidates_without_changing_cursor_or_child_queries():
    calls=[]
    source=ReplicaSource('unused',excluded_agent_ids=frozenset(EXCLUDED),
        connection_factory=lambda *args,**kwargs:_Connection({},calls))
    assert source.fetch_sessions(after=NOW-timedelta(minutes=5),after_key='previous-session',through=NOW,limit=100)==()
    candidate,params=next((query,params) for query,params in calls if 'from platform_read.sessions' in query)
    assert set(params['excluded_agent_ids'])==set(EXCLUDED)
    assert candidate.index('excluded_agent_ids') < candidate.lower().index('limit %(limit)s')
    assert params['after']==NOW-timedelta(minutes=5)
    assert params['after_key']=='previous-session'
    assert params['through']==NOW and params['limit']==100


@pytest.mark.parametrize('value',[('unknown',),('ai-fae-agent','ai-fae-agent'),'ai-fae-agent', ['ai-fae-agent'],(None,)])
def test_source_rejects_ambiguous_or_unapproved_exclusion_configuration(value):
    with pytest.raises(ValueError,match='excluded_agent_ids_invalid'):
        ReplicaSource('unused',excluded_agent_ids=value)


def test_exclusion_never_suppresses_cloud_agent_management_feedback_projection():
    calls=[]
    rows={'select agent_id,':[dict(agent_id='ai-fae-agent',feedback_rows=3,negative_rows=2,negative_turns=2,positive_rows=1)]}
    source=ReplicaSource('unused',excluded_agent_ids=EXCLUDED,
        connection_factory=lambda *args,**kwargs:_Connection(rows,calls))
    totals,=source.fetch_management_projections(through=NOW)
    assert isinstance(totals,ReviewFeedbackTotalsProjection)
    assert totals.agent_id=='ai-fae-agent' and totals.feedback_rows==3
    assert all('excluded_agent_ids' not in query and 'excluded_agent_ids' not in (params or {}) for query,params in calls)


@pytest.fixture
def office_database(observability_database):
    with psycopg.connect(observability_database,autocommit=True) as connection:
        connection.execute('create schema platform_read')
        connection.execute('''create table platform_read.sessions (
            session_key text,agent_id text,source_kind text,channel text,title text,user_identity text,
            created_at timestamptz,last_active_at timestamptz,primary_sender_name text,
            primary_sender_department text,source_synced_at timestamptz,details jsonb)''')
        connection.execute('''create table platform_read.turns (
            turn_key text,session_key text,agent_id text,turn_index integer,question text,answer text,
            created_at timestamptz,question_at timestamptz,answer_at timestamptz,
            question_time_status text,answer_time_status text,outcome text,fallback_used boolean,
            duration_ms integer,trace_key text)''')
        connection.execute('create table platform_read.feedback (agent_id text,turn_key text,sentiment text,created_at timestamptz,feedback_key text)')
        connection.execute('create table platform_read.reviews (turn_key text,status text,updated_at timestamptz,review_key text)')
        rows=[(f'fae:{index:04d}','ai-fae-agent','fae') for index in range(150)]
        rows += [('daq:native','ai-daq-fae-agent','fae'),('admin:native','ai-admin-agent','admin'),('voc:native','voc','voc'),
            ('metabot:marketing:local','marketing-prospecting-bot','metabot')]
        with connection.cursor() as cursor:
            cursor.executemany('''insert into platform_read.sessions (session_key,agent_id,source_kind,
                created_at,last_active_at,source_synced_at,details) values (%s,%s,%s,%s,%s,%s,'{}')''',
                [(key,agent,kind,NOW,NOW,NOW) for key,agent,kind in rows])
    try:
        yield observability_database
    finally:
        with psycopg.connect(observability_database,autocommit=True) as connection:
            connection.execute('drop schema platform_read cascade')


@pytest.mark.postgres
def test_all_four_cloud_agents_are_excluded_before_100_session_pagination(office_database):
    after=NOW-timedelta(minutes=1)
    default=ReplicaSource(office_database).fetch_sessions(after=after,after_key='',through=NOW,limit=100)
    assert len(default)==100 and all(raw.agent_id!='marketing-prospecting-bot' for raw in default)
    local=ReplicaSource(office_database,excluded_agent_ids=EXCLUDED).fetch_sessions(after=after,after_key='',through=NOW,limit=100)
    assert len(local)==1 and local[0].agent_id=='marketing-prospecting-bot'
    assert local[0].session_key=='metabot:marketing:local' and local[0].source_kind=='metabot'
    assert ReplicaSource(office_database,excluded_agent_ids=EXCLUDED).fetch_sessions(
        after=NOW,after_key=local[0].session_key,through=NOW+timedelta(seconds=1),limit=100)==()


@pytest.mark.postgres
@pytest.mark.parametrize('excluded,retained',[
    ('ai-fae-agent','ai-daq-fae-agent'),('ai-daq-fae-agent','ai-fae-agent'),
])
def test_exclusion_uses_agent_identity_not_shared_fae_family(office_database,excluded,retained):
    rows=ReplicaSource(office_database,excluded_agent_ids=(excluded,)).fetch_sessions(
        after=NOW-timedelta(minutes=1),after_key='',through=NOW,limit=100)
    assert any(raw.agent_id==retained and raw.source_kind=='fae' for raw in rows)
    assert all(raw.agent_id!=excluded for raw in rows)


def configure_export(tmp_path,monkeypatch):
    key=Ed25519PrivateKey.generate()
    secrets={
        'SOURCE_DATABASE_URL_FILE':b'postgresql://private-synthetic-dsn',
        'SANITIZER_DICTIONARY_FILE':b'customers: []\n',
        'IDENTITY_KEY_FILE':b'i'*32,
        'SIGNING_PRIVATE_KEY_FILE':key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()),
    }
    for name,value in secrets.items():
        path=tmp_path/name
        path.write_bytes(value);path.chmod(0o600)
        monkeypatch.setenv('PLATFORM_REPLICA_'+name,str(path))
    monkeypatch.setenv('PLATFORM_REPLICA_EXPORT_STATE_PATH',str(tmp_path/'state.json'))
    monkeypatch.setenv('PLATFORM_REPLICA_EXPORT_QUEUE_DIR',str(tmp_path/'queue'))
    monkeypatch.setenv('PLATFORM_REPLICA_SOURCE_INSTANCE_ID','office-platform')
    monkeypatch.delenv('PLATFORM_OPERATIONS_DATABASE_PATH',raising=False)
    return key


def empty_source_factory(calls,management=False):
    def factory(database_url,*,excluded_agent_ids=()):
        calls.append(tuple(excluded_agent_ids))
        class Source:
            def fetch_sessions(self,**kwargs): return ()
            def fetch_management_projections(self,*,through):
                return (ReviewFeedbackTotalsProjection('ai-fae-agent',3,2,2,1,through),) if management else ()
        return Source()
    return factory


@pytest.mark.parametrize('csv',[None,'',','.join(EXCLUDED),'ai-daq-fae-agent'])
def test_cli_export_passes_explicit_exclusions_and_default_empty(tmp_path,monkeypatch,capsys,csv):
    configure_export(tmp_path,monkeypatch)
    if csv is None: monkeypatch.delenv(ENVIRONMENT,raising=False)
    else: monkeypatch.setenv(ENVIRONMENT,csv)
    calls=[]
    monkeypatch.setattr(cli,'ReplicaSource',empty_source_factory(calls))
    assert cli.main(['export'],clock=lambda:NOW)==0
    assert calls==[tuple(csv.split(',')) if csv else ()]
    assert 'private-synthetic-dsn' not in capsys.readouterr().out


@pytest.mark.parametrize('csv',['unknown','ai-fae-agent,ai-fae-agent',',voc','voc,','voc,,ai-fae-agent',
    ' voc','voc ',' ','VOC','ai-fae-agent;voc','ai-fae-agent\nvoc','ai-fae-agent, voc'])
def test_cli_rejects_unknown_duplicate_and_malformed_csv_before_reading_secrets(tmp_path,monkeypatch,capsys,csv):
    configure_export(tmp_path,monkeypatch)
    monkeypatch.setenv(ENVIRONMENT,csv)
    touched=[]
    monkeypatch.setattr(cli,'read_secret_file',lambda *args:touched.append(True))
    assert cli.main(['export'],clock=lambda:NOW)==1
    assert touched==[]
    assert not (tmp_path/'queue').exists() and not (tmp_path/'state.json').exists()
    assert json.loads(capsys.readouterr().err)=={'error':'export_failed','status':'failed'}


def test_cutover_exclusion_keeps_signed_chain_and_cloud_management_records(tmp_path,monkeypatch,capsys):
    private=configure_export(tmp_path,monkeypatch)
    monkeypatch.delenv(ENVIRONMENT,raising=False)
    calls=[]
    monkeypatch.setattr(cli,'ReplicaSource',empty_source_factory(calls,management=True))
    assert cli.main(['export'],clock=lambda:NOW)==0
    monkeypatch.setenv(ENVIRONMENT,','.join(EXCLUDED))
    assert cli.main(['export'],clock=lambda:NOW+timedelta(minutes=1))==0
    batches=[]
    for path in sorted((tmp_path/'queue').glob('batch-*.jsonl')):
        with path.open('rb') as stream:
            batches.append(decode_and_verify_batch(stream,BatchVerifier(private.public_key()),BatchLimits()))
    first,second=batches
    assert first.header.sequence==1 and second.header.sequence==2
    assert second.header.previous_digest==first.digest
    assert second.header.lower_watermark==first.header.upper_watermark==NOW
    assert all(batch.records[0]['agent_id']=='ai-fae-agent' for batch in batches)
    state=json.loads((tmp_path/'state.json').read_text())
    assert state['next_sequence']==3 and state['previous_digest']==second.digest
    assert calls==[(),EXCLUDED]
