"""Actual admission authority; no model invocation or test-only eligibility bypass."""
# ruff: noqa: PLC0414

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import psycopg
import pytest
from test_execution_transport_v5 import post
from test_execution_transport_v5 import signed_api as signed_api
from test_execution_worker_v5_receiver import receiver as receiver
from test_execution_worker_v5_receiver import worker_database as worker_database
from test_hr_direct_command_binding import bindings as bindings
from test_hr_direct_command_binding import transport_worker as transport_worker
from test_hr_direct_worker import (
    attempt_repository as attempt_repository,
)
from test_hr_direct_worker import (
    control_database as control_database,
)
from test_hr_direct_worker import (
    conversation_database as conversation_database,
)
from test_hr_direct_worker import (
    direct_database as direct_database,
)
from test_hr_direct_worker import (
    repository as repository,
)
from test_hr_direct_worker import (
    worker_conversation as worker_conversation,
)
from test_hr_direct_worker import (
    worker_turn as worker_turn,
)

from tests.helpers.v5_readiness import observation

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def without_fixture_observation(attempt_repository):
    with attempt_repository.transaction() as connection:
        connection.execute(
            "update platform_control.execution_workers set v5_observation=null"
        )


def test_initial_claim_without_authenticated_v5_readiness_stays_queued(
    worker_turn, attempt_repository
):
    assert attempt_repository.claim_due(uuid4(), 60) is None
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select status,executor_id,transport_run_id,reason_code from "
            "platform_control.turn_attempts where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone()
    assert row == {
        "status": "queued",
        "executor_id": None,
        "transport_run_id": None,
        "reason_code": "executor_capability_missing",
    }


def test_signed_observation_admits_original_attempt(
    signed_api, worker_turn, attempt_repository, transport_worker
):
    client, signer, _ = signed_api
    path = "/api/v1/execution-worker/v5/readiness"
    value = observation()
    assert post(client, signer, path, json.dumps(value).encode()).status_code == 200
    lease = attempt_repository.claim_due(uuid4(), 60)
    assert lease is not None
    assert lease.status == "running" and lease.lease_epoch == 1
    assert lease.admission["workerId"] == transport_worker
    assert lease.admission["service"]["config"]["toolPolicy"] == "default"
    assert attempt_repository.claim_due(uuid4(), 60) is None


def test_loopback_readiness_challenge_never_uses_callback_auth(receiver, tmp_path):
    from app.execution_relay.metabot_client import (
        _APPROVED_AGENT_IDS,
        MetaBotClient,
        MetaBotRuntimeMap,
    )
    from app.execution_relay.worker import callback_server

    _, _, runtime, _, _ = receiver
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700)
    secret = directory / "machine"
    secret.write_text("owned-readiness-secret")
    secret.chmod(0o600)
    runtime.metabot = MetaBotClient(
        MetaBotRuntimeMap(
            {name: 20000 + i for i, name in enumerate(sorted(_APPROVED_AGENT_IDS))}
        ),
        secret,
    )

    async def scenario():
        callback = asyncio.create_task(callback_server(runtime))
        await runtime.callback_ready.wait()
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(
                    f"http://127.0.0.1:{runtime.callback_port}/v5/readiness",
                    json={},
                    headers={
                        "authorization": "Bearer wrong",
                        "x-v5-challenge": "a" * 32,
                    },
                )
                assert response.status_code == 401
                response = await client.post(
                    f"http://127.0.0.1:{runtime.callback_port}/v5/readiness",
                    json={},
                    headers={
                        "authorization": "Bearer owned-readiness-secret",
                        "x-v5-challenge": "b" * 32,
                    },
                )
                assert response.status_code == 200 and response.json() == {
                    "challenge": "b" * 32
                }
        finally:
            runtime.stop()
            await callback

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"sampledAt": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()},
        {"sampledAt": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()},
        {"reason": {}},
        {"receiverReady": 1},
        {"extra": True},
    ],
)
def test_invalid_signed_observation_cannot_enable_admission(
    signed_api, worker_turn, attempt_repository, change
):
    client, signer, _ = signed_api
    value = observation() | change
    assert (
        post(
            client,
            signer,
            "/api/v1/execution-worker/v5/readiness",
            json.dumps(value).encode(),
        ).status_code
        == 400
    )
    assert attempt_repository.claim_due(uuid4(), 60) is None


def test_newer_negative_cannot_be_overwritten_by_old_positive(
    signed_api, worker_turn, attempt_repository
):
    client, signer, _ = signed_api
    path = "/api/v1/execution-worker/v5/readiness"
    positive = observation()
    positive["sampledAt"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    negative = observation()
    negative.update(service=None, reason="unavailable")
    assert post(client, signer, path, json.dumps(negative).encode()).json() == {
        "recorded": True
    }
    assert post(client, signer, path, json.dumps(positive).encode()).json() == {
        "recorded": False
    }
    assert attempt_repository.claim_due(uuid4(), 60) is None


@pytest.mark.parametrize(
    "field,value",
    [("healthy", False), ("durableTerminal", False), ("capacity", "busy")],
)
def test_live_negative_service_never_creates_a_lease(
    signed_api, worker_turn, attempt_repository, field, value
):
    client, signer, _ = signed_api
    sample = observation()
    sample["service"][field] = value
    assert (
        post(
            client,
            signer,
            "/api/v1/execution-worker/v5/readiness",
            json.dumps(sample).encode(),
        ).status_code
        == 200
    )
    assert attempt_repository.claim_due(uuid4(), 60) is None


def test_readiness_path_requires_exact_signed_identity_and_body(signed_api):
    from app.control_plane.middleware import is_execution_worker_request

    client, signer, _ = signed_api
    path = "/api/v1/execution-worker/v5/readiness"
    body = json.dumps(observation()).encode()
    assert client.post(path, content=body).status_code == 401
    assert (
        client.post(
            path, content=body + b" ", headers=signer.sign("POST", path, body)
        ).status_code
        == 401
    )
    assert is_execution_worker_request("POST", path)
    assert not is_execution_worker_request("GET", path)
    assert not is_execution_worker_request("POST", path + "/extra")


def test_missing_capability_rechecks_and_alerts_without_claim_or_terminal_cancel(
    worker_turn, attempt_repository, caplog
):
    assert attempt_repository.claim_due(uuid4(), 60) is None
    with attempt_repository.transaction() as connection:
        initial = connection.execute(
            "select * from platform_control.turn_attempts where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone()
        connection.execute(
            "update platform_control.turn_attempts set capability_missing_since=clock_timestamp()-interval '6 minutes',readiness_check_after=clock_timestamp()-interval '1 second' where attempt_id=%s",
            (initial["attempt_id"],),
        )
    assert attempt_repository.claim_due(uuid4(), 60) is None
    assert (
        len([r for r in caplog.records if r.message == "executor_capability_missing"])
        == 1
    )
    assert attempt_repository.claim_due(uuid4(), 60) is None
    with attempt_repository.transaction() as connection:
        connection.execute(
            "update platform_control.turn_attempts set readiness_check_after=clock_timestamp()-interval '1 second' where attempt_id=%s",
            (initial["attempt_id"],),
        )
    assert attempt_repository.claim_due(uuid4(), 60) is None
    assert (
        len([r for r in caplog.records if r.message == "executor_capability_missing"])
        == 1
    )
    assert (
        attempt_repository.request_cancel(
            worker_turn.conversation.owner_internal_user_id, worker_turn.turn.turn_id
        )
        == "accepted"
    )
    lease = attempt_repository.claim_due(uuid4(), 60)
    assert (
        lease is not None
        and lease.status == "reconciling"
        and lease.attempt_id == initial["attempt_id"]
    )
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select t.status,a.transport_run_id from platform_control.conversation_turns t join platform_control.turn_attempts a using(turn_id) where a.attempt_id=%s",
            (lease.attempt_id,),
        ).fetchone()
    assert row == {"status": "accepted", "transport_run_id": None}


def test_fresh_observation_after_delay_claims_same_attempt_once(
    signed_api, worker_turn, attempt_repository
):
    assert attempt_repository.claim_due(uuid4(), 60) is None
    client, signer, _ = signed_api
    assert (
        post(
            client,
            signer,
            "/api/v1/execution-worker/v5/readiness",
            json.dumps(observation()).encode(),
        ).status_code
        == 200
    )
    assert (
        attempt_repository.claim_due(uuid4(), 60) is None
    )  # actual 30-second admission schedule
    with attempt_repository.transaction() as connection:
        connection.execute(
            "update platform_control.turn_attempts set readiness_check_after=clock_timestamp()-interval '1 second'"
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(
            pool.map(lambda _: attempt_repository.claim_due(uuid4(), 60), range(2))
        )
    assert len([lease for lease in leases if lease]) == 1
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select status,reason_code,capability_missing_since,capability_alerted_at from platform_control.turn_attempts where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone()
    assert row == {
        "status": "running",
        "reason_code": None,
        "capability_missing_since": None,
        "capability_alerted_at": None,
    }


def test_stale_route_prefix_does_not_starve_ready_conversation(
    signed_api, direct_database, repository, attempt_repository, worker_turn
):
    environment, owner_id, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set route_epoch=route_epoch+1 where conversation_id=%s",
            (worker_turn.conversation.conversation_id,),
        )
    for _ in range(16):
        shell = repository.ensure_direct_conversation_shell(
            owner_id, uuid4(), direct_agent_id="hr-bot", title="admission fixture"
        )
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "update platform_control.conversations set execution_owner='worker_direct' where conversation_id=%s",
                (shell.conversation_id,),
            )
        turn = repository.append_turn(
            owner_id, shell.conversation_id, uuid4(), "admission fixture"
        )
        if _ < 15:
            with psycopg.connect(environment["admin"]) as connection:
                connection.execute(
                    "update platform_control.conversations set route_epoch=route_epoch+1 where conversation_id=%s",
                    (shell.conversation_id,),
                )
    client, signer, _ = signed_api
    post(
        client,
        signer,
        "/api/v1/execution-worker/v5/readiness",
        json.dumps(observation()).encode(),
    )
    lease = attempt_repository.claim_due(uuid4(), 60)
    assert lease is not None
    with attempt_repository.transaction() as connection:
        assert (
            connection.execute(
                "select turn_id from platform_control.turn_attempts where attempt_id=%s",
                (lease.attempt_id,),
            ).fetchone()["turn_id"]
            == turn.turn.turn_id
        )


def test_completed_turn_with_held_attempt_blocks_admission_and_binding(
    signed_api, worker_turn, attempt_repository, repository, direct_database, bindings
):
    from test_hr_direct_command_binding import frozen_input

    from app.agent_brain.direct_command_binding import BindingRejected
    from app.agent_brain.turn_attempts import Lease

    client, signer, _ = signed_api
    post(
        client,
        signer,
        "/api/v1/execution-worker/v5/readiness",
        json.dumps(observation()).encode(),
    )
    old = attempt_repository.claim_due(uuid4(), 60)
    environment, owner_id, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversation_turns set status='completed' where turn_id=%s",
            (worker_turn.turn.turn_id,),
        )
    later = repository.append_turn(
        owner_id,
        worker_turn.conversation.conversation_id,
        uuid4(),
        "later admission fixture",
    )
    assert attempt_repository.claim_due(uuid4(), 60) is None
    assert attempt_repository.renew(old, 60).attempt_id == old.attempt_id
    # Fault injection represents an already-claimed alternate caller. Binding must
    # independently enforce the hold, not rely exclusively on admission.
    executor = uuid4()
    with psycopg.connect(
        environment["admin"], row_factory=psycopg.rows.dict_row
    ) as connection:
        row = connection.execute(
            "update platform_control.turn_attempts set status='running',executor_id=%s,lease_epoch=1,lease_expires_at=clock_timestamp()+interval '60 seconds' where turn_id=%s returning *",
            (str(executor), later.turn.turn_id),
        ).fetchone()
    lease = Lease(
        row["attempt_id"],
        "worker_direct",
        executor,
        1,
        row["lease_expires_at"],
        "running",
    )
    with pytest.raises(BindingRejected), attempt_repository.transaction() as connection:
        bindings.prepare(lease, frozen_input(later), connection=connection)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set status='failed' where attempt_id=%s",
            (old.attempt_id,),
        )
    with attempt_repository.transaction() as connection:
        bindings.prepare(lease, frozen_input(later), connection=connection)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set status='running' where attempt_id=%s",
            (old.attempt_id,),
        )
    with attempt_repository.transaction() as connection:
        assert bindings.mark_offered(lease, connection=connection) is False


def test_admission_rechecks_sample_expiry_at_final_write(
    signed_api, worker_turn, attempt_repository, transport_worker, monkeypatch
):
    client, signer, _ = signed_api
    post(
        client,
        signer,
        "/api/v1/execution-worker/v5/readiness",
        json.dumps(observation()).encode(),
    )
    with attempt_repository.transaction() as connection:
        connection.execute(
            "update platform_control.execution_workers set v5_observation=jsonb_set(v5_observation,'{expiresAt}',to_jsonb(clock_timestamp()+interval '200 milliseconds')) where worker_id=%s",
            (transport_worker,),
        )
    original = attempt_repository.transaction

    class Cursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            result = super().execute(query, params, **kwargs)
            if "select worker_id,v5_observation" in str(query):
                with self.connection.cursor() as pause:
                    pause.execute("select pg_sleep(0.25)")
            return result

    @contextmanager
    def delayed():
        with original() as connection:
            connection.cursor_factory = Cursor
            yield connection

    monkeypatch.setattr(attempt_repository, "transaction", delayed)
    assert attempt_repository.claim_due(uuid4(), 60) is None


def test_locked_conversation_does_not_block_unrelated_claim(
    signed_api, worker_turn, direct_database, repository, attempt_repository
):
    environment, owner_id, _ = direct_database
    shell = repository.ensure_direct_conversation_shell(
        owner_id, uuid4(), direct_agent_id="hr-bot", title="lock admission fixture"
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct' where conversation_id=%s",
            (shell.conversation_id,),
        )
    later = repository.append_turn(
        owner_id, shell.conversation_id, uuid4(), "lock admission fixture"
    )
    client, signer, _ = signed_api
    post(
        client,
        signer,
        "/api/v1/execution-worker/v5/readiness",
        json.dumps(observation()).encode(),
    )
    with psycopg.connect(environment["admin"]) as held:
        held.execute(
            "select 1 from platform_control.conversations where conversation_id=%s for update",
            (worker_turn.conversation.conversation_id,),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            lease = pool.submit(attempt_repository.claim_due, uuid4(), 60).result(
                timeout=2
            )
        assert lease is not None
        with attempt_repository.transaction() as connection:
            assert (
                connection.execute(
                    "select turn_id from platform_control.turn_attempts where attempt_id=%s",
                    (lease.attempt_id,),
                ).fetchone()["turn_id"]
                == later.turn.turn_id
            )


def test_unreachable_slow_probe_does_not_block_actual_renewal_or_cancellation(
    receiver, tmp_path, signed_api, worker_turn, attempt_repository
):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event, Thread
    from time import monotonic

    from app.execution_relay.metabot_client import (
        _APPROVED_AGENT_IDS,
        MetaBotClient,
        MetaBotRuntimeMap,
    )
    from app.execution_relay.worker import callback_server
    from app.execution_relay.worker_readiness_v5 import V5WorkerService

    client, signer, _ = signed_api
    post(
        client,
        signer,
        "/api/v1/execution-worker/v5/readiness",
        json.dumps(observation()).encode(),
    )
    lease = attempt_repository.claim_due(uuid4(), 60)
    entered, release = Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            entered.set()
            release.wait(timeout=5)
            self.send_response(503)
            self.end_headers()

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    _, _, runtime, _, _ = receiver
    directory = tmp_path / "probe-private"
    directory.mkdir(mode=0o700)
    secret = directory / "machine"
    secret.write_text("owned-probe-secret")
    secret.chmod(0o600)
    ports = {name: 20000 + i for i, name in enumerate(sorted(_APPROVED_AGENT_IDS))}
    ports["hr-bot"] = http.server_port
    runtime.metabot = MetaBotClient(MetaBotRuntimeMap(ports), secret)

    async def scenario():
        callback = asyncio.create_task(callback_server(runtime))
        await runtime.callback_ready.wait()
        service = V5WorkerService(runtime)
        before = monotonic()
        sampling = asyncio.create_task(service.sample())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert (
                await asyncio.to_thread(attempt_repository.renew, lease, 60)
            ).attempt_id == lease.attempt_id
            assert (
                await asyncio.to_thread(
                    attempt_repository.request_cancel,
                    worker_turn.conversation.owner_internal_user_id,
                    worker_turn.turn.turn_id,
                )
                == "accepted"
            )
            assert not sampling.done()
            sample = await sampling
            assert sample["service"] is None and sample["receiverReady"] is False
            assert monotonic() - before < 4
        finally:
            release.set()
            runtime.stop()
            await callback

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)
