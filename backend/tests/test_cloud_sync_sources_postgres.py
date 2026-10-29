from datetime import timedelta

import psycopg
import pytest
from psycopg import sql
from test_cloud_sync_sources import NOW, SID, TID, UID, module
from test_observability_migration_runner import (
    observability_database,  # noqa: F401 -- pytest fixture
)


@pytest.fixture
def source_database(observability_database, tmp_path):  # noqa: F811 -- pytest fixture
    with psycopg.connect(observability_database, autocommit=True) as conn:
        conn.execute('drop schema if exists cloud_source_test cascade')
        conn.execute('create schema cloud_source_test')
        # Profiles intentionally only accept actual source schemas, so isolate by table names.
        for table in ('chat_sessions', 'chat_turns', 'turn_feedback', 'turn_reviews'):
            conn.execute(sql.SQL('drop table if exists public.{} cascade').format(sql.Identifier(table)))
        conn.execute('create table public.chat_sessions (id uuid primary key, external_session_id text, channel text, user_id text, external_user_id text, conversation_title text, created_at timestamptz, last_active_at timestamptz, owner_subject_id uuid, owner_subject_type text, metadata jsonb)')
        conn.execute('create table public.chat_turns (id uuid primary key, session_id uuid, turn_index integer, question text, answer text, created_at timestamptz, question_at timestamptz, answer_at timestamptz, outcome text, fallback_used boolean, duration_ms integer, sources jsonb, done jsonb, metadata jsonb)')
        conn.execute('create table public.turn_feedback (id uuid primary key, turn_id uuid, rating text, created_at timestamptz)')
        conn.execute('create table public.turn_reviews (id uuid primary key, turn_id uuid, review_status text, created_at timestamptz, updated_at timestamptz)')
        conn.execute("insert into public.chat_sessions values (%s,'fae:native','web',%s,null,null,%s,%s,%s,'enterprise_member','{\"authentication_mode\":\"platform_enterprise\"}')", (SID, UID, NOW, NOW, UID))
        conn.execute("insert into public.chat_turns values (%s,%s,0,'original question','answer',%s,%s,%s,'answered',false,20,'[]','{}','{}')", (TID,SID,NOW,NOW,NOW))
    path = tmp_path / 'source-dsn'
    path.write_text(observability_database)
    path.chmod(0o600)
    return observability_database, path


def source(source_database, **kwargs):
    _dsn, path = source_database
    profile = module('models').SourceProfile('ai-fae-agent', 'fae', str(path), 'postgres')
    return module('sources').DatabaseAgentSource(profile, **kwargs)


@pytest.mark.postgres
def test_complete_readonly_snapshot_canonical_identity_and_child_updates(source_database):
    adapter = source(source_database)
    first = adapter.read_snapshot()
    assert first.complete is True
    assert first.sessions[0].session_key == 'fae:' + SID
    assert first.sessions[0].turns[0].answer == 'answer'
    assert first.source_observed_at.tzinfo is not None and first.elapsed_ms >= 0
    with psycopg.connect(source_database[0], autocommit=True) as conn:
        conn.execute('insert into public.turn_feedback values (%s,%s,\'bad\',%s)', (SID,TID,NOW))
        conn.execute('insert into public.turn_reviews values (%s,%s,\'fixed\',%s,%s)', (TID,TID,NOW,NOW))
        conn.execute('update public.chat_turns set answer=\'complete updated answer\' where id=%s', (TID,))
    second = adapter.read_snapshot()
    assert second.sessions[0].last_active_at == first.sessions[0].last_active_at
    assert second.sessions[0].turns[0].answer == 'complete updated answer'
    assert second.sessions[0].turns[0].feedback_sentiments == ('negative',)
    assert second.sessions[0].turns[0].review_statuses == ('fixed',)


@pytest.mark.postgres
def test_repeatable_read_does_not_mix_late_commit_with_session_header(source_database):
    dsn = source_database[0]
    committed = False
    def factory(*args, **kwargs):
        real = psycopg.connect(*args, **kwargs)
        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def execute(self, query, params=None):
                nonlocal committed
                self.cursor.execute(query, params)
                rendered = query.as_string(real) if hasattr(query, 'as_string') else str(query)
                if 'current_database()' in rendered and not committed:
                    with psycopg.connect(dsn, autocommit=True) as writer:
                        writer.execute("update public.chat_turns set answer='late committed answer' where id=%s", (TID,))
                    committed = True
                return self
            def __getattr__(self, name): return getattr(self.cursor, name)
        class Connection:
            def __enter__(self): real.__enter__(); return self
            def __exit__(self, *args): return real.__exit__(*args)
            def transaction(self): return real.transaction()
            def cursor(self): return Cursor(real.cursor())
        return Connection()
    first = source(source_database, connection_factory=factory).read_snapshot()
    assert first.sessions[0].turns[0].answer == 'answer'
    assert source(source_database).read_snapshot().sessions[0].turns[0].answer == 'late committed answer'


@pytest.mark.postgres
def test_source_observation_time_precedes_snapshot_queries(source_database):
    current = [NOW]

    def factory(*args, **kwargs):
        real = psycopg.connect(*args, **kwargs)

        class Cursor:
            def __init__(self):
                self.cursor = real.cursor()

            def execute(self, query, params=None):
                self.cursor.execute(query, params)
                if isinstance(query, str) and query.startswith('select '):
                    current[0] = NOW + timedelta(seconds=5)
                return self

            def __getattr__(self, name):
                return getattr(self.cursor, name)

        class Connection:
            def __enter__(self):
                real.__enter__()
                return self

            def __exit__(self, *args):
                return real.__exit__(*args)

            def transaction(self):
                return real.transaction()

            def cursor(self):
                return Cursor()

        return Connection()

    snapshot = source(source_database, connection_factory=factory,
                      clock=lambda: current[0]).read_snapshot()
    assert snapshot.source_observed_at == NOW


@pytest.mark.postgres
@pytest.mark.parametrize('kwargs,code', [({'max_rows': 1}, 'source_row_limit'), ({'max_bytes': 10}, 'source_body_limit')])
def test_bounds_fail_without_returning_partial_snapshot(source_database, kwargs, code):
    with pytest.raises(ValueError, match=code):
        source(source_database, **kwargs).read_snapshot()


@pytest.mark.postgres
def test_snapshot_budget_exhaustion_is_visible(source_database):
    ticks = iter([0.0, 0.0, 11.0])
    with pytest.raises(ValueError, match='source_time_limit'):
        source(source_database, monotonic=lambda: next(ticks, 11.0)).read_snapshot()


@pytest.mark.postgres
def test_wrong_database_and_missing_table_fail_safely(source_database):
    profile = module('models').SourceProfile('ai-fae-agent','fae',str(source_database[1]), 'wrong_database')
    with pytest.raises(ValueError, match='source_identity_unverified'):
        module('sources').DatabaseAgentSource(profile).read_snapshot()
    with psycopg.connect(source_database[0], autocommit=True) as conn:
        conn.execute('drop table public.turn_reviews')
    with pytest.raises(ValueError, match='source_schema_unverified'):
        source(source_database).read_snapshot()


@pytest.mark.postgres
def test_incomplete_table_shape_fails_before_projection(source_database):
    with psycopg.connect(source_database[0], autocommit=True) as conn:
        conn.execute('alter table public.chat_turns drop column answer')
    with pytest.raises(ValueError, match='source_schema_unverified'):
        source(source_database).read_snapshot()


def test_unsafe_secret_file_is_rejected_without_leaking_credentials(tmp_path):
    path = tmp_path / 'dsn'
    path.write_text('postgresql://private-user:secret-password@private-host/database')
    path.chmod(0o644)
    profile = module('models').SourceProfile('ai-fae-agent','fae',str(path),'database')
    with pytest.raises(ValueError, match='source_credentials_invalid') as error:
        module('sources').DatabaseAgentSource(profile).read_snapshot()
    assert 'private' not in str(error.value) and 'password' not in str(error.value)


def test_source_connection_failure_is_payload_free(tmp_path):
    path = tmp_path / 'dsn'
    path.write_text('postgresql://private-user:secret-password@private-host/database')
    path.chmod(0o600)
    profile = module('models').SourceProfile('ai-fae-agent','fae',str(path),'database')
    def fail(*args, **kwargs): raise psycopg.OperationalError('secret-password raw DSN')
    with pytest.raises(ValueError, match='source_unavailable') as error:
        module('sources').DatabaseAgentSource(profile, connection_factory=fail).read_snapshot()
    assert 'secret' not in str(error.value)


@pytest.mark.postgres
def test_all_remaining_families_use_real_postgres_and_canonical_namespaces(source_database):
    dsn, path = source_database
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute('drop schema if exists daq_fae cascade')
        conn.execute('create schema daq_fae')
        for name in ('chat_sessions', 'chat_turns', 'turn_feedback', 'turn_reviews'):
            conn.execute(sql.SQL('create table daq_fae.{} (like public.{} including all)').format(sql.Identifier(name),sql.Identifier(name)))
            conn.execute(sql.SQL('insert into daq_fae.{} select * from public.{}').format(sql.Identifier(name),sql.Identifier(name)))
        conn.execute("update daq_fae.chat_sessions set external_session_id='daq:native'")
        conn.execute("update daq_fae.chat_turns set metadata='{\"agent_id\":\"ai-daq-fae-agent\"}'")
        conn.execute('create table daq_fae.daq_installation_identity (singleton boolean primary key, agent_id text, database_name text)')
        conn.execute("insert into daq_fae.daq_installation_identity values (true,'ai-daq-fae-agent','postgres')")
        for name in ('chat_sessions', 'chat_turns', 'turn_feedback', 'turn_reviews'):
            conn.execute(sql.SQL('drop table if exists public.{} cascade').format(sql.Identifier('admin_' + name)))
            conn.execute(sql.SQL('create table public.{} (like public.{} including all)').format(sql.Identifier('admin_' + name),sql.Identifier(name)))
            conn.execute(sql.SQL('insert into public.{} select * from public.{}').format(sql.Identifier('admin_' + name),sql.Identifier(name)))
        conn.execute('alter table public.admin_chat_sessions add column internal_user_id uuid, add column verification_method text, add column verified_at timestamptz')
        conn.execute("update public.admin_chat_sessions set internal_user_id=%s, verification_method='platform_session', verified_at=%s, external_user_id=%s, metadata='{\"display_name\":\"Forged\"}'", (UID,NOW,'platform:'+UID))
        conn.execute('alter table public.admin_turn_reviews rename column review_status to verdict')
        for name in ('voc_raw','voc_core','voc_meta'):
            conn.execute(sql.SQL('drop schema if exists {} cascade').format(sql.Identifier(name)))
            conn.execute(sql.SQL('create schema {}').format(sql.Identifier(name)))
        conn.execute('create table voc_raw.inbound_messages (id bigint, sender_internal_user_id uuid, conversation_id text, conversation_type text, sender_name text, sender_dept text, received_at timestamptz, dingtalk_created_at timestamptz, text_content text)')
        conn.execute('create table voc_raw.interaction_jobs (inbound_message_id bigint, status text)')
        conn.execute('create table voc_core.outbox_messages (source_inbound_message_id bigint, status text, content text, sent_at timestamptz)')
        conn.execute('create table voc_meta.schema_migrations (name text)')
        conn.execute("insert into voc_meta.schema_migrations values ('020_employee_chatbot_experience.sql')")
        conn.execute("insert into voc_raw.inbound_messages values (1,%s,'thread','group','Employee','Department',%s,%s,'VOC question')",(UID,NOW,NOW))
        conn.execute("insert into voc_raw.interaction_jobs values (1,'completed')")
        conn.execute("insert into voc_core.outbox_messages values (1,'sent','Confirmed answer',%s)",(NOW,))
    SourceProfile = module('models').SourceProfile
    Adapter = module('sources').DatabaseAgentSource
    for kind, agent, schema in [('daq','ai-daq-fae-agent','daq_fae'),('admin','ai-admin-agent','public'),('voc','voc','voc_raw/voc_core')]:
        raw, = Adapter(SourceProfile(agent,kind,str(path),'postgres',schema)).read_snapshot().sessions
        assert raw.session_key.startswith(kind + ':')
        assert raw.turns[0].turn_key.startswith(kind + ':')
        if kind == 'daq':
            assert raw.source_kind == 'fae' and raw.user_identity.startswith('daq:')
        if kind == 'admin':
            assert raw.details['verified_internal_user_id'] == UID
            assert raw.primary_sender_name == 'Forged'
        if kind == 'voc':
            assert raw.turns[0].answer == 'Confirmed answer' and raw.turns[0].outcome == 'sent'
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("update daq_fae.daq_installation_identity set database_name='wrong'")
    with pytest.raises(ValueError,match='source_identity_unverified'):
        Adapter(SourceProfile('ai-daq-fae-agent','daq',str(path),'postgres','daq_fae')).read_snapshot()


@pytest.mark.postgres
def test_source_transaction_is_repeatable_read_and_read_only(source_database):
    observed = []
    def factory(*args, **kwargs):
        real = psycopg.connect(*args, **kwargs)
        class Cursor:
            def __init__(self): self.cursor = real.cursor()
            def execute(self, query, params=None):
                self.cursor.execute(query, params)
                if isinstance(query,str) and 'current_database()' in query:
                    row = real.execute("select current_setting('transaction_read_only') as readonly, current_setting('transaction_isolation') as isolation").fetchone()
                    observed.append(row)
                return self
            def __getattr__(self,name): return getattr(self.cursor,name)
        class Connection:
            def __enter__(self): real.__enter__(); return self
            def __exit__(self,*args): return real.__exit__(*args)
            def transaction(self): return real.transaction()
            def cursor(self): return Cursor()
        return Connection()
    source(source_database,connection_factory=factory).read_snapshot()
    assert observed == [{'readonly':'on','isolation':'repeatable read'}]
