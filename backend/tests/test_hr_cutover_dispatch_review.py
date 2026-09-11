"""Cutover regressions: disposable PostgreSQL and authenticated HTTP, no model.

Wrong-lane residual bindings are explicit fault fixtures, never simulated
successful transitions: the real transition rejects those nonterminal records.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
import socket

import httpx
import uvicorn
from time import monotonic, sleep
from uuid import uuid4

import psycopg
import pytest

from hr_agent_support import hr_agent_database
from test_agent_brain_conversation_repository import _codec, repository
from test_execution_transport_v5 import PREFIX, post, signed_api as signed_http_app
from test_hr_direct_command_binding import bindings, prepared, transport_worker
from test_hr_direct_worker import worker_conversation, worker_turn
from test_execution_acceptance_v5 import response
from test_execution_worker_v5_receiver import event
from test_hr_candidate_database import _seed_candidate_scope
from app.agent_brain.turn_attempts import TurnAttemptRepository
from app.agent_brain.conversation_repository import ConversationRepositoryError
from app.execution_relay.contracts_v5 import parse_v5_command
from app.execution_relay.readiness_v5 import record_observation
from app.execution_relay.repository import ExecutionRelayRepository
from app.hr.candidate_repository import CandidateRepository, CandidateUnavailable
from app.hr.candidate_models import ConfirmCandidateDraft, RetryCandidateDraft
from app.hr_agent.cutover import ADVISORY_LOCK_KEY
from tests.helpers.v5_readiness import observation

_FIXTURES = (repository, signed_http_app, bindings, prepared, transport_worker,
             worker_conversation, worker_turn)
pytestmark = pytest.mark.postgres


@pytest.fixture()
def database():
    with hr_agent_database(cutover_phase="legacy") as db:
        yield db


@pytest.fixture()
def conversation_database(database):
    owner = uuid4()
    with database.admin_connection() as c:
        c.execute("insert into platform_control.internal_users(internal_user_id,display_name,status) values(%s,'Synthetic cutover owner','active')", (owner,))
    return {"admin": database.admin_dsn, "urls": {"platform_control_app": database.dsn}}, owner, uuid4()


@pytest.fixture()
def direct_database(conversation_database):
    return conversation_database


@pytest.fixture()
def attempt_repository(database):
    worker_id = "readiness-" + uuid4().hex
    with database.admin_connection() as c:
        c.execute("insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) values(%s,array['hr-bot'],'active')", (worker_id,))
    observed = observation()
    observed["version"] = "hr_v6_readiness_v1"
    observed["service"]["contractVersion"] = "core_chat_collaboration_v6"
    observed["service"]["rolePackage"] = {"teamCommit": "a" * 40, "catalogRelease": "recruiting-1", "manifestSha256": "b" * 64}
    observed["service"]["toolCapabilities"] = ["hr.read_context", "hr.submit_result", "hr.confirm_standard"]
    record_observation(ExecutionRelayRepository(database.dsn, content_codec=_codec()), worker_id, observed)
    return TurnAttemptRepository(database.dsn, _codec())


@pytest.fixture()
def signed_api(signed_http_app):
    _, signer, app = signed_http_app
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    server.install_signal_handlers = lambda: None
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started and monotonic() < deadline:
            sleep(.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", trust_env=False, timeout=10) as client:
            yield client, signer, app
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def authorize(bindings, prepared, attempt_repository, worker):
    lease, _ = prepared
    with attempt_repository.transaction() as c:
        bindings.authorize_transport(lease, worker, "http://127.0.0.1:19191", connection=c)


def set_residual_phase(database, phase):
    # Explicit fault injection: a delayed old worker observes an opposite lane.
    with database.admin_connection() as c:
        c.execute("select pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
        c.execute("update platform_control.hr_execution_cutover set phase=%s", (phase,))


@pytest.mark.parametrize("phase", ["cloud", "draining_cloud"])
def test_signed_handoff_rejects_residual_old_dispatch(database, signed_api, bindings, prepared, attempt_repository, transport_worker, phase):
    client, signer, _ = signed_api
    authorize(bindings, prepared, attempt_repository, transport_worker)
    set_residual_phase(database, phase)
    result = post(client, signer, PREFIX + "/handoff", b"{}")
    assert result.status_code == 204
    with database.admin_connection() as c:
        assert c.execute("select offered_at from platform_control.direct_command_bindings where attempt_id=%s", (prepared[0].attempt_id,)).fetchone()[0] is None


def test_same_lane_drain_handoff_acceptance_and_callback(database, signed_api, bindings, prepared, attempt_repository, transport_worker):
    client, signer, _ = signed_api
    authorize(bindings, prepared, attempt_repository, transport_worker)
    with database.admin_connection() as c:
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
    handoff = post(client, signer, PREFIX + "/handoff", b"{}")
    assert handoff.status_code == 200
    raw = handoff.json()["command"]
    accepted = post(client, signer, f"{PREFIX}/runs/{prepared[1].run_id}/acceptance", json.dumps(response(parse_v5_command(raw))).encode())
    assert accepted.status_code == 200
    callback = post(client, signer, f"{PREFIX}/runs/{prepared[1].run_id}/events", json.dumps(event(raw, 1, "cancelled")).encode())
    assert callback.status_code == 200
    assert callback.json()["acceptedThrough"] == 1


def test_callback_for_accepted_work_is_not_a_new_dispatch_gate(database, signed_api, bindings, prepared, attempt_repository, transport_worker):
    client, signer, _ = signed_api
    authorize(bindings, prepared, attempt_repository, transport_worker)
    raw = post(client, signer, PREFIX + "/handoff", b"{}").json()["command"]
    set_residual_phase(database, "cloud")
    accepted = post(client, signer, f"{PREFIX}/runs/{prepared[1].run_id}/acceptance", json.dumps(response(parse_v5_command(raw))).encode())
    assert accepted.status_code == 200
    callback = post(client, signer, f"{PREFIX}/runs/{prepared[1].run_id}/events", json.dumps(event(raw, 1, "cancelled")).encode())
    assert callback.status_code == 200
    assert callback.json()["acceptedThrough"] == 1


def test_handoff_waits_for_transition_lock_then_rechecks_phase(database, signed_api, bindings, prepared, attempt_repository, transport_worker):
    client, signer, _ = signed_api
    authorize(bindings, prepared, attempt_repository, transport_worker)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.admin_connection() as c:
            c.execute("select pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            pending = pool.submit(post, client, signer, PREFIX + "/handoff", b"{}")
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline and not pending.done():
                blocked = c.execute("select exists(select 1 from pg_locks where locktype='advisory' and not granted)").fetchone()[0]
                if blocked:
                    break
                sleep(.01)
            c.execute("update platform_control.hr_execution_cutover set phase='cloud'")
        result = pending.result(timeout=5)
    assert blocked, "handoff must serialize before any offer with the transition lock"
    assert result.status_code == 204


@pytest.mark.parametrize("operation", ["confirm", "dismiss"])
def test_ready_legacy_draft_cannot_write_after_real_cutover(database, operation):
    ids = _seed_candidate_scope({"admin": database.admin_dsn})
    repo = CandidateRepository(database.dsn)
    with database.admin_connection() as c:
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
    with pytest.raises(CandidateUnavailable):
        mutate_draft(repo, ids, operation)
    with database.admin_connection() as c:
        assert c.execute("select state,row_version from platform_hr.candidate_drafts where draft_id=%s", (ids['draft'],)).fetchone() == ('ready', 2)
        assert c.execute("select count(*) from platform_hr.candidates").fetchone()[0] == 0


def mutate_draft(repo, ids, operation):
    if operation == "dismiss":
        return repo.dismiss_draft(RetryCandidateDraft(ids['owner'], ids['draft'], uuid4(), 2))
    return repo.confirm_draft(ConfirmCandidateDraft(ids['owner'], ids['draft'], ids['confirmation'], 2, ids['candidate'], 'Synthetic Candidate', {'skills': ['Python']}, None), document_id=ids['document'], position_candidate_id=ids['relation'], context_version_id=ids['context'])


@pytest.mark.parametrize("operation", ["confirm", "dismiss"])
def test_ready_legacy_draft_can_finish_during_same_lane_drain(database, operation):
    ids = _seed_candidate_scope({"admin": database.admin_dsn})
    with database.admin_connection() as c:
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
    mutate_draft(CandidateRepository(database.dsn), ids, operation)
    with database.admin_connection() as c:
        assert c.execute("select state from platform_hr.candidate_drafts where draft_id=%s", (ids['draft'],)).fetchone()[0] == ('confirmed' if operation == 'confirm' else 'dismissed')


def test_append_reclassifies_binding_committed_before_conversation_lock(database, repository, conversation_database, monkeypatch):
    from contextlib import contextmanager
    owner = conversation_database[1]
    created = repository.start(owner, uuid4(), 'Historical synthetic conversation')
    cid = created.conversation.conversation_id
    with database.admin_connection() as c:
        # Terminal historical fixture; no execution result is asserted by this test.
        c.execute("update platform_control.conversation_turns set status='completed' where turn_id=%s", (created.turn.turn_id,))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
    before_lock, release = Event(), Event()
    original = repository._connection

    class CursorProxy:
        def __init__(self, cursor): self.cursor = cursor
        def __getattr__(self, key): return getattr(self.cursor, key)
        def execute(self, query, params=()):
            if 'select * from platform_control.conversations ' in query and 'for update' in query:
                before_lock.set()
                assert release.wait(5)
            return self.cursor.execute(query, params)

    class ConnectionProxy:
        def __init__(self, c): self.c = c
        @contextmanager
        def cursor(self):
            with self.c.cursor() as cursor:
                yield CursorProxy(cursor)

    @contextmanager
    def paused_connection():
        with original() as c:
            yield ConnectionProxy(c)

    monkeypatch.setattr(repository, '_connection', paused_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(repository.append_turn, owner, cid, uuid4(), 'Late HR input')
        try:
            assert before_lock.wait(5)
            with database.admin_connection() as c:
                position = uuid4()
                c.execute("insert into platform_hr.positions(position_id,owner_internal_user_id,client_request_id,source_kind,title) values(%s,%s,%s,'manual','Synthetic')", (position, owner, uuid4()))
                c.execute("insert into platform_hr.position_conversations(conversation_id,owner_internal_user_id,position_id,client_request_id,binding_kind) values(%s,%s,%s,%s,'historical_exact')", (cid, owner, position, uuid4()))
        finally:
            release.set()
        with pytest.raises(ConversationRepositoryError):
            pending.result(timeout=5)
    with database.admin_connection() as c:
        assert c.execute("select count(*) from platform_control.conversation_turns where conversation_id=%s", (cid,)).fetchone()[0] == 1


def test_cloud_phase_does_not_gate_other_bot_turn(database, repository, conversation_database):
    with database.admin_connection() as c:
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
        c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
    shell = repository.ensure_direct_conversation_shell(conversation_database[1], uuid4(), direct_agent_id='fae-bot', title='Other bot')
    result = repository.append_turn(conversation_database[1], shell.conversation_id, uuid4(), 'Other bot input')
    assert result.created


def test_transition_waits_for_handoff_transaction(database, signed_api, bindings, prepared, attempt_repository, transport_worker, monkeypatch):
    client, signer, _ = signed_api
    authorize(bindings, prepared, attempt_repository, transport_worker)
    authorized, release = Event(), Event()
    original = bindings._authorized

    def pause(connection, worker, attempt):
        value = original(connection, worker, attempt)
        authorized.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(bindings, '_authorized', pause)

    def drain():
        with database.admin_connection() as c:
            return c.execute("select (platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)).phase", (uuid4(),)).fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        offered = pool.submit(post, client, signer, PREFIX + '/handoff', b'{}')
        transition = None
        blocked = False
        try:
            assert authorized.wait(5)
            transition = pool.submit(drain)
            deadline = monotonic() + 5
            with database.admin_connection() as c:
                while monotonic() < deadline and not transition.done():
                    blocked = c.execute("select exists(select 1 from pg_locks where locktype='advisory' and not granted)").fetchone()[0]
                    if blocked:
                        break
                    sleep(.01)
        finally:
            release.set()
        assert offered.result(timeout=5).status_code == 200
        assert transition.result(timeout=5) == 'draining_legacy'
    assert blocked, 'transition must wait until the admitted handoff commits'


@pytest.mark.parametrize('operation', ['confirm', 'dismiss'])
def test_ready_draft_waits_for_real_cloud_transition_then_rejects(database, operation):
    ids = _seed_candidate_scope({'admin': database.admin_dsn})
    with database.admin_connection() as c:
        c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.admin_connection() as c:
            c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
            pending = pool.submit(mutate_draft, CandidateRepository(database.dsn), ids, operation)
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline and not pending.done():
                blocked = c.execute("select exists(select 1 from pg_locks where locktype='advisory' and not granted)").fetchone()[0]
                if blocked:
                    break
                sleep(.01)
        with pytest.raises(CandidateUnavailable):
            pending.result(timeout=5)
    assert blocked
