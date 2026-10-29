"""Reduced PostgreSQL payloads preserve the existing encoder contract."""
import json

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from test_cloud_sync_sources import SID,TID,UID,encode
from test_cloud_sync_sources_postgres import source_database
from test_observability_migration_runner import observability_database
from app.cloud_sync.models import SourceProfile
from app.cloud_sync.projection import project_rows
from app.cloud_sync.sources import DatabaseAgentSource, TURN_COLUMNS, _tables


def profile(source_database, kind='fae'):
    return SourceProfile('ai-fae-agent' if kind=='fae' else 'ai-daq-fae-agent',kind,
        str(source_database[1]),'postgres','public' if kind=='fae' else 'daq_fae')


def complete_full_rows(dsn):
    with psycopg.connect(dsn,row_factory=dict_row) as connection:
        return {table:list(connection.execute(sql.SQL('select * from public.{} order by id').format(sql.Identifier(table))).fetchall())
            for table in ('chat_sessions','chat_turns','turn_feedback','turn_reviews')}


@pytest.mark.postgres
def test_reduced_sql_is_byte_identical_at_encoder_boundary_and_keeps_complete_answer(source_database):
    dsn,_ = source_database
    answer = 'complete answer text ' * 1000
    done = dict(status='failed',engine='loop',backend='anthropic',actual_provider_model='glm-5',
        error_type='provider_unavailable',tool_results=['unneeded result ' * 20000],
        model_request={'unneeded':'x'*100000})
    with psycopg.connect(dsn,autocommit=True) as connection:
        connection.execute('update public.chat_turns set answer=%s,outcome=%s,done=%s,sources=%s,metadata=%s',(
            answer,'provider_unavailable',Jsonb(done),Jsonb([{'title':'citation','body':'unused citation ' * 20000}]),
            Jsonb({'arbitrary_trace_metadata':'x'*100000})))
    full = project_rows('fae',complete_full_rows(dsn))[0]
    reduced = DatabaseAgentSource(profile(source_database)).read_snapshot().sessions[0]
    assert full.turns[0].sources and reduced.turns[0].sources == ()
    assert reduced.turns[0].answer == answer
    assert encode(full) == encode(reduced)
    assert reduced.turns[0].trace.status == 'failed'
    assert reduced.user_identity == full.user_identity and reduced.verified_subject == full.verified_subject


@pytest.mark.postgres
def test_body_bounds_measure_reduced_business_payload_not_unused_tool_or_citation_bodies(source_database):
    with psycopg.connect(source_database[0],autocommit=True) as connection:
        connection.execute('update public.chat_turns set done=%s,sources=%s,metadata=%s',(
            Jsonb({'tool_results':['x'*200000]}),Jsonb([{'snippet':'x'*200000}]),Jsonb({'private_unexported':'x'*200000})))
    adapter = DatabaseAgentSource(profile(source_database),max_bytes=10000)
    assert adapter.read_snapshot().sessions[0].turns[0].answer == 'answer'
    with pytest.raises(ValueError,match='source_body_limit'):
        DatabaseAgentSource(profile(source_database),max_bytes=100).read_snapshot()


@pytest.mark.postgres
@pytest.mark.parametrize('column,value',[
    ('done',[]),('done',False),('done','bad'),('sources',{}),('sources',[None]),
    ('sources',[[]]),('sources',['bad']),('metadata',[]),('metadata',False),
    ('done',{'status':[]}),('done',{'engine':{}}),
])
def test_reduced_sql_preserves_malformed_json_validation(source_database,column,value):
    with psycopg.connect(source_database[0],autocommit=True) as connection:
        connection.execute(sql.SQL('update public.chat_turns set {}=%s').format(sql.Identifier(column)),(Jsonb(value),))
    with pytest.raises(ValueError,match='source_row_invalid'):
        DatabaseAgentSource(profile(source_database)).read_snapshot()


@pytest.mark.postgres
def test_turn_sql_preserves_daq_agent_evidence_and_does_not_expose_other_metadata(source_database):
    from app.cloud_sync import sources
    assert hasattr(sources,'_snapshot_query'), 'bounded SQL projection is not implemented'
    with psycopg.connect(source_database[0],row_factory=dict_row) as connection:
        connection.execute('update public.chat_turns set metadata=%s',(Jsonb({'agent_id':'ai-daq-fae-agent','private_unexported':'x'*10000}),))
        row = connection.execute(sources._snapshot_query(profile(source_database,'daq'),'public','chat_turns',TURN_COLUMNS)).fetchone()
        assert row['metadata'] == {'agent_id':'ai-daq-fae-agent'}
        row = connection.execute(sources._snapshot_query(profile(source_database),'public','chat_turns',TURN_COLUMNS)).fetchone()
        assert row['metadata'] == {}


@pytest.mark.postgres
def test_reduced_source_requires_only_explicit_allowlisted_column_grants(source_database):
    dsn,_ = source_database
    role = 'cloud_adapter_column_reader'
    with psycopg.connect(dsn,autocommit=True) as connection:
        connection.execute(sql.SQL('drop role if exists {}').format(sql.Identifier(role)))
        connection.execute(sql.SQL('create role {} nologin').format(sql.Identifier(role)))
        connection.execute('alter table public.chat_turns add column private_unexported text')
        connection.execute('create table public.unrelated_private_content (secret text)')
        for (schema,table),columns in _tables(profile(source_database)).items():
            connection.execute(sql.SQL('grant select ({}) on {}.{} to {}').format(
                sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    def least_privilege_connection(dsn,**kwargs):
        kwargs['options'] += ' -c role=' + role
        return psycopg.connect(dsn,**kwargs)
    assert DatabaseAgentSource(profile(source_database),connection_factory=least_privilege_connection).read_snapshot().sessions[0].session_key == 'fae:' + SID
    with psycopg.connect(dsn,autocommit=True) as connection:
        assert connection.execute("select has_column_privilege(%s,'public.chat_turns','private_unexported','select')",(role,)).fetchone() == (False,)
        assert connection.execute("select has_table_privilege(%s,'public.unrelated_private_content','select')",(role,)).fetchone() == (False,)
        connection.execute('drop table public.unrelated_private_content')
        connection.execute(sql.SQL('drop owned by {}').format(sql.Identifier(role)))
        connection.execute(sql.SQL('drop role {}').format(sql.Identifier(role)))
