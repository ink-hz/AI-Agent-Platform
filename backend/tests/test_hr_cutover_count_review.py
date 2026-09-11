"""Drain occupancy differs from business success; all records are disposable."""
import json
from uuid import uuid4

import pytest

from test_hr_cutover_dispatch_review import (
    database, conversation_database, direct_database, attempt_repository,
    repository, bindings, signed_http_app, signed_api, transport_worker,
    PREFIX, post, response, event,
)
from test_hr_direct_command_binding import frozen_input
from app.agent_brain.turn_result_projection import TurnResultProjector
from app.execution_relay.contracts_v5 import parse_v5_command, parse_v5_event

_FIXTURES = (database, conversation_database, direct_database, attempt_repository,
             repository, bindings, signed_http_app, signed_api, transport_worker)
pytestmark = pytest.mark.postgres


def complete_v5(database, repository, owner, attempts, bindings, signed_api, worker):
    """Real signed source -> fenced projection; no fabricated success UPDATE."""
    shell = repository.ensure_direct_conversation_shell(owner, uuid4(), direct_agent_id='hr-bot', title='Synthetic completed HR')
    with database.admin_connection() as c:
        c.execute("update platform_control.conversations set execution_owner='worker_direct',route_epoch=7 where conversation_id=%s", (shell.conversation_id,))
    turn = repository.append_turn(owner, shell.conversation_id, uuid4(), 'Synthetic HR result')
    lease = attempts.claim_due(uuid4(), 60)
    with attempts.transaction() as c:
        binding = bindings.prepare(lease, frozen_input(turn), connection=c)
        bindings.authorize_transport(lease, worker, 'http://127.0.0.1:19191', connection=c)
    client, signer, _ = signed_api
    command = post(client, signer, PREFIX + '/handoff', b'{}').json()['command']
    assert post(client, signer, f'{PREFIX}/runs/{binding.run_id}/acceptance', json.dumps(response(parse_v5_command(command))).encode()).status_code == 200
    raw = event(command, kind='result')
    raw['payload']['executionRecovery'].update(executorStopped=True, executorStopProofRef='fixture:durable-stop')
    assert post(client, signer, f'{PREFIX}/runs/{binding.run_id}/events', json.dumps(raw).encode()).status_code == 200
    TurnResultProjector(attempts, bindings).commit(lease, parse_v5_event(raw))
    return turn, lease, binding


def test_three_completed_v5_envelopes_stay_queued_but_do_not_block_real_cutover(database, repository, conversation_database, attempt_repository, bindings, signed_api, transport_worker):
    for _ in range(3):
        complete_v5(database, repository, conversation_database[1], attempt_repository, bindings, signed_api, transport_worker)
    with database.admin_connection() as c:
        assert c.execute("select count(*) from platform_control.execution_jobs where job_kind='worker_direct_v5' and status='queued'").fetchone()[0] == 3
        assert c.execute("select count(*) from platform_control.turn_attempts where status='completed'").fetchone()[0] == 3
        assert c.execute("select legacy_nonterminal from platform_control.hr_execution_cutover_counts_v102()").fetchone()[0] == 0
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
        assert c.execute("select count(*) from platform_control.execution_jobs where status='queued'").fetchone()[0] == 3


@pytest.mark.parametrize('fault', ['orphan', 'nonterminal_attempt', 'nonterminal_turn'])
def test_v5_orphan_or_nonterminal_provenance_blocks_cutover(database, repository, conversation_database, attempt_repository, bindings, signed_api, transport_worker, fault):
    turn, lease, binding = complete_v5(database, repository, conversation_database[1], attempt_repository, bindings, signed_api, transport_worker)
    with database.admin_connection() as c:
        # Explicit malformed/residual state injection, never success evidence.
        if fault == 'orphan':
            c.execute("insert into platform_control.execution_jobs(job_id,run_id,agent_id,job_kind,payload_ciphertext,encryption_key_version,status) values(%s,%s,'hr-bot','worker_direct_v5',%s,1,'queued')", (uuid4(), uuid4(), b'fixture'))
        elif fault == 'nonterminal_attempt':
            c.execute("update platform_control.turn_attempts set status='reconciling',result_message_id=null where attempt_id=%s", (lease.attempt_id,))
        else:
            c.execute("update platform_control.conversation_turns set status='running',assistant_message_id=null where turn_id=%s", (turn.turn.turn_id,))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
    with database.admin_connection() as c:
        with pytest.raises(Exception, match='drain is incomplete'):
            c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))


def interrupted_job(c, kind, *, pending_stop=False, run_id=None, terminal_recorded=True):
    job_id, worker_id = uuid4(), 'stop-fixture-' + uuid4().hex
    if pending_stop:
        c.execute("insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) values(%s,array['hr-bot'],'active')", (worker_id,))
    c.execute("insert into platform_control.execution_jobs(job_id,run_id,agent_id,job_kind,payload_ciphertext,encryption_key_version,status,terminal_at,cancel_requested,lease_worker_id,stop_requested_status) values(%s,%s,'hr-bot',%s,%s,1,'interrupted',case when %s then clock_timestamp() end,%s,%s,%s)", (job_id, run_id or uuid4(), kind, b'fixture', terminal_recorded, pending_stop, worker_id if pending_stop else None, 'interrupted' if pending_stop else None))
    return job_id


def test_historical_interrupted_split_is_drain_terminal_without_claiming_success(database, repository, conversation_database):
    with database.admin_connection() as c:
        for kind, n in [('metabot_local', 15), ('legacy_brain', 3), ('direct_agent', 10)]:
            for _ in range(n):
                run_id = None
                if kind == 'direct_agent':
                    # Ten historical direct jobs have terminal Turn references.
                    # These rows are fixture history, not an execution success claim.
                    historical = repository.start(conversation_database[1], uuid4(), 'Historical interrupted work')
                    run_id = uuid4()
                    c.execute("update platform_control.conversation_turns set status='interrupted' where turn_id=%s", (historical.turn.turn_id,))
                    c.execute("insert into platform_control.mission_runs(run_id,mission_id,phase,agent_id,status,input_ciphertext,encryption_key_version,terminal_at) values(%s,%s,'planning','agent-brain-bot','interrupted',%s,1,clock_timestamp())", (run_id, historical.mission.mission_id, b'x' * 29))
                interrupted_job(c, kind, run_id=run_id)
        assert c.execute("select legacy_nonterminal from platform_control.hr_execution_cutover_counts_v102()").fetchone()[0] == 0
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
        assert c.execute("select count(*) from platform_control.execution_jobs where status='interrupted'").fetchone()[0] == 28


@pytest.mark.parametrize('kind', ['metabot_local', 'legacy_brain', 'direct_agent'])
def test_interrupted_unacknowledged_stop_blocks_cutover(database, kind):
    with database.admin_connection() as c:
        interrupted_job(c, kind, pending_stop=True)
        assert c.execute("select legacy_nonterminal from platform_control.hr_execution_cutover_counts_v102()").fetchone()[0] == 1
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
    with database.admin_connection() as c:
        with pytest.raises(Exception, match='drain is incomplete'):
            c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))


@pytest.mark.parametrize('active', ['turn', 'attempt'])
def test_interrupted_job_with_unrelated_bot_live_turn_still_blocks_hr_job(database, repository, conversation_database, attempt_repository, active):
    created = repository.start(conversation_database[1], uuid4(), 'Synthetic related work', mode='direct_agent', direct_agent_id='fae-bot')
    if active == 'attempt':
        attempt_repository.create_queued(created.turn.turn_id, 'legacy_api_v1')
    with database.admin_connection() as c:
        if active == 'attempt':
            c.execute("update platform_control.conversation_turns set status='interrupted' where turn_id=%s", (created.turn.turn_id,))
        # Historical linkage fixture: job's agent is HR, its related user turn
        # is still active even though conversation classification is another Bot.
        run_id = uuid4()
        c.execute("insert into platform_control.mission_runs(run_id,mission_id,phase,agent_id,status,input_ciphertext,encryption_key_version,terminal_at) values(%s,%s,'planning','agent-brain-bot','interrupted',%s,1,clock_timestamp())", (run_id, created.mission.mission_id, b'x' * 29))
        interrupted_job(c, 'direct_agent', run_id=run_id)
        assert c.execute("select legacy_nonterminal from platform_control.hr_execution_cutover_counts_v102()").fetchone()[0] > 0


def test_additive_count_replacement_preserves_maintenance_only_control(database):
    import psycopg
    with psycopg.connect(database.dsn) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('select * from platform_control.hr_execution_cutover_counts_v102()')
    with psycopg.connect(database.dsn, user='platform_control_maintenance') as c:
        assert c.execute('select * from platform_control.hr_execution_cutover_counts_v102()').fetchone() == (0, 0)
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
