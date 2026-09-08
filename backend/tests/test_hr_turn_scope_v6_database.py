"""Disposable PostgreSQL checks for scoped intake; no provider execution."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_hr_candidate_database import _seed_candidate_scope
from test_control_plane_migration import control_database
from test_agent_brain_conversation_repository import _codec
from app.agent_brain.conversation_repository import ConversationRepository, ConversationRepositoryConflict
from app.agent_brain.conversation_models import ConversationTurnSubmission
from app.agent_brain.repository import MissionRepository
from app.execution_relay.contracts_v6 import HrTurnScope


@pytest.fixture(scope='module')
def scoped_database(control_database):
    env = control_database['environments']['production']
    sql = (Path(__file__).parents[1] / 'control_migrations/pending/hr_role_tools_v6.sql').read_text()
    with psycopg.connect(env['admin']) as connection:
        connection.execute('set role platform_control_owner')
        for migration in sorted((Path(__file__).parents[1] / 'control_migrations/hr_web').glob('*.sql')):
            connection.execute(migration.read_text())
        connection.execute(sql)
    return env


def repository(env):
    url = env['urls']['platform_control_app']
    return ConversationRepository(url, content_codec=_codec())


def submission(position_id):
    return ConversationTurnSubmission('按当前岗位分析', hr_scope=HrTurnScope(
        positionId=position_id, positionCandidateIds=(), attachmentIds=()))


def test_atomic_intake_scope_and_replay_reject_different_position(scoped_database):
    env = scoped_database
    ids = _seed_candidate_scope(env)
    repo = repository(env)
    request_id = uuid4()
    result = repo.start(ids['owner'], request_id, submission(ids['position']), mode='direct_agent', direct_agent_id='hr-bot')
    replay = repo.start(ids['owner'], request_id, submission(ids['position']), mode='direct_agent', direct_agent_id='hr-bot')
    assert replay.turn.turn_id == result.turn.turn_id
    with pytest.raises(ConversationRepositoryConflict):
        repo.start(ids['owner'], request_id, submission(None), mode='direct_agent', direct_agent_id='hr-bot')
    with psycopg.connect(env['urls']['platform_control_app']) as connection:
        value = connection.execute('select platform_hr.read_turn_scope_v6(%s,%s,%s)',
            (ids['owner'], result.conversation.conversation_id, result.turn.turn_id)).fetchone()[0]
        assert value['scope']['positionId'] == str(ids['position'])
        assert connection.execute('select count(*) from platform_hr.position_conversations where conversation_id=%s',
            (result.conversation.conversation_id,)).fetchone()[0] == 0
        assert connection.execute('select count(*) from platform_hr.position_task_records where turn_id=%s',
            (result.turn.turn_id,)).fetchone()[0] == 1


def test_wrong_owner_rolls_back_intake(scoped_database):
    env = scoped_database
    ids = _seed_candidate_scope(env)
    other = _seed_candidate_scope(env)
    request_id = uuid4()
    with pytest.raises(ConversationRepositoryConflict):
        repository(env).start(ids['owner'], request_id, submission(other['position']), mode='direct_agent', direct_agent_id='hr-bot')
    with psycopg.connect(env['urls']['platform_control_app']) as connection:
        assert connection.execute('select count(*) from platform_control.conversations where started_by_client_request_id=%s',
            (request_id,)).fetchone()[0] == 0


def test_switching_position_preserves_prior_scope_and_filters_model_history(scoped_database):
    # Cancellation is a fixture boundary, not simulated model success/recovery evidence.
    from app.agent_brain.conversation_context import ConversationContextBuilder
    env = scoped_database
    ids = _seed_candidate_scope(env)
    second_position = uuid4()
    with psycopg.connect(env['admin']) as connection:
        connection.execute("insert into platform_hr.positions(position_id,owner_internal_user_id,client_request_id,source_kind,title) values (%s,%s,%s,'manual','Second position')",
            (second_position, ids['owner'], uuid4()))
    repo = repository(env)
    first = repo.start(ids['owner'], uuid4(), ConversationTurnSubmission('岗位 A 的私有讨论', hr_scope=submission(ids['position']).hr_scope),
        mode='direct_agent', direct_agent_id='hr-bot')
    with psycopg.connect(env['urls']['platform_control_app']) as connection:
        connection.execute("update platform_control.conversation_turns set status='cancelled' where turn_id=%s", (first.turn.turn_id,))
    second = repo.append_turn(ids['owner'], first.conversation.conversation_id, uuid4(),
        ConversationTurnSubmission('岗位 B 的本次要求', hr_scope=submission(second_position).hr_scope))
    context = ConversationContextBuilder(repo).build(first.conversation.conversation_id, second.turn.turn_id)
    assert [message.content for message in context.messages] == ['岗位 B 的本次要求']
    assert context.hr_workflow_contract is None
    with psycopg.connect(env['urls']['platform_control_app']) as connection:
        original = connection.execute('select platform_hr.read_turn_scope_v6(%s,%s,%s)',
            (ids['owner'], first.conversation.conversation_id, first.turn.turn_id)).fetchone()[0]
        assert original['scope']['positionId'] == str(ids['position'])
