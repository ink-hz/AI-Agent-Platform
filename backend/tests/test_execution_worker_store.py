from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
from threading import Barrier, Thread
from uuid import UUID

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx

from app.execution_relay import worker_store
from app.execution_relay.metabot_client import MetaBotClient, MetaBotRuntimeMap
from app.execution_relay.models import RelayEvent, RelayJobPayload, RelayLease
from app.execution_relay.worker import (
    CallbackResult,
    SignedCloudClient,
    WorkerRuntime,
    callback_server,
)
from app.execution_relay.worker_auth import WorkerRequestSigner
from app.execution_relay.worker_store import WorkerStore, WorkerStoreError


BACKEND = Path(__file__).parents[1]
SCHEMA = BACKEND / "app/execution_relay/worker_schema.sql"
NOW = datetime(2026, 8, 21, 4, 0, tzinfo=timezone.utc)
RUN_ID = UUID("00000000-0000-4000-8000-000000000101")
JOB_ID = UUID("00000000-0000-4000-8000-000000000111")


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def worker_database():
    if not all(shutil.which(command) for command in ("initdb", "pg_ctl")):
        pytest.fail("disposable PostgreSQL requires initdb and pg_ctl")

    root = Path(tempfile.mkdtemp(prefix="worker-pg-", dir="/tmp"))
    data = root / "data"
    socket_dir = root / "socket"
    socket_dir.mkdir()
    port = _available_port()
    subprocess.run(
        [
            "initdb",
            "-D",
            str(data),
            "--auth=trust",
            "--encoding=UTF8",
            "--no-locale",
            "--username=worker_test_admin",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "pg_ctl",
            "-D",
            str(data),
            "-l",
            str(root / "postgres.log"),
            "-o",
            f"-F -h 127.0.0.1 -p {port} -k {socket_dir}",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    dsn = f"postgresql://worker_test_admin@127.0.0.1:{port}/postgres"
    try:
        with psycopg.connect(dsn) as connection:
            connection.execute(SCHEMA.read_text(encoding="utf-8"))
            connection.execute(SCHEMA.read_text(encoding="utf-8"))
        yield dsn
    finally:
        subprocess.run(
            ["pg_ctl", "-D", str(data), "stop", "-m", "immediate"],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def dsn_file(worker_database: str, tmp_path: Path) -> Path:
    secret_dir = tmp_path / "worker-secrets"
    secret_dir.mkdir(mode=0o700)
    path = secret_dir / "postgres.dsn"
    path.write_text(worker_database + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture(autouse=True)
def empty_worker_tables(worker_database: str):
    with psycopg.connect(worker_database) as connection:
        connection.execute("delete from execution_worker.event_outbox")
        connection.execute("delete from execution_worker.local_runs")


def _lease(
    *,
    job_id: UUID = JOB_ID,
    run_id: UUID = RUN_ID,
    agent_id: str = "hr-bot",
) -> RelayLease:
    return RelayLease(
        job_id=job_id,
        payload=RelayJobPayload(
            run_id=run_id,
            conversation_id=UUID("00000000-0000-4000-8000-000000000102"),
            trigger_message_id=UUID("00000000-0000-4000-8000-000000000103"),
            agent_id=agent_id,
            prompt="请根据岗位要求形成候选人画像。",
            max_turns=24,
        ),
        lease_expires_at=NOW + timedelta(seconds=45),
        cancel_requested=False,
    )


def _event(seq: int, *, payload: dict[str, object] | None = None) -> RelayEvent:
    return RelayEvent(
        run_id=RUN_ID,
        seq=seq,
        event_type="turn",
        created_at=NOW + timedelta(seconds=seq),
        payload=payload or {"text": f"event-{seq}"},
    )


def _terminal_event(status: str = "completed") -> RelayEvent:
    event_type = {
        "completed": "agent.complete",
        "failed": "agent.error",
    }[status]
    return RelayEvent(
        run_id=RUN_ID,
        seq=1,
        event_type=event_type,
        created_at=NOW + timedelta(seconds=1),
        payload={"text": f"run {status}"},
    )


def _terminal_callback_body(
    status: str = "completed",
    *,
    execution_chat_id: str = (
        "platform-00000000-0000-4000-8000-000000000102-hr-bot"
    ),
) -> bytes:
    event = _terminal_event(status)
    return json.dumps(
        {
            "runId": str(event.run_id),
            "seq": event.seq,
            "type": "complete" if status == "completed" else "error",
            "createdAt": event.created_at.isoformat(),
            "bridge": {
                "botName": "hr-bot",
                "executionChatId": execution_chat_id,
            },
            "payload": event.payload,
        }
    ).encode()


@pytest.mark.postgres
def test_schema_is_versioned_idempotent_constrained_and_grant_free(
    worker_database: str,
) -> None:
    sql = SCHEMA.read_text(encoding="utf-8").lower()
    assert "schema version 1" in sql
    assert "grant " not in sql
    assert "flywheel" not in sql
    assert "platform_control" not in sql

    with psycopg.connect(worker_database) as connection:
        connection.execute(SCHEMA.read_text(encoding="utf-8"))
        assert connection.execute(
            "select (select count(*) from execution_worker.local_runs),"
            "(select count(*) from execution_worker.event_outbox)"
        ).fetchone() == (0, 0)
        tables = connection.execute(
            "select table_name from information_schema.tables "
            "where table_schema='execution_worker' order by table_name"
        ).fetchall()
        assert tables == [
            ("event_outbox",),
            ("local_runs",),
            ("schema_migrations",),
        ]
        version = connection.execute(
            "select singleton,version from execution_worker.schema_migrations"
        ).fetchone()
        assert version == (True, 1)
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "insert into execution_worker.local_runs "
                "(run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at) "
                "values (%s,%s,'hr-bot',0,%s,'leased',now())",
                (RUN_ID, JOB_ID, b"x" * 32),
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "insert into execution_worker.local_runs "
                "(run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at) "
                "values (%s,%s,'hr-bot',9101,%s,'unknown',now())",
                (RUN_ID, JOB_ID, b"short"),
            )


@pytest.mark.postgres
def test_schema_refuses_unversioned_preexisting_target_tables(
    worker_database: str,
) -> None:
    with psycopg.connect(worker_database) as connection:
        connection.execute("drop schema execution_worker cascade")
        connection.execute("create schema execution_worker")
        connection.execute(
            "create table execution_worker.local_runs ("
            "run_id uuid primary key,job_id uuid not null unique,"
            "agent_id varchar(128) not null,metabot_port integer not null,"
            "callback_token_hash bytea not null,state varchar(32) not null,"
            "leased_at timestamptz not null,dispatched_at timestamptz,"
            "terminal_at timestamptz)"
        )
        connection.execute(
            "create table execution_worker.event_outbox ("
            "run_id uuid not null references execution_worker.local_runs(run_id),"
            "seq integer not null,event_json jsonb not null,"
            "delivered_at timestamptz,primary key(run_id,seq))"
        )
        with pytest.raises(psycopg.Error):
            connection.execute(SCHEMA.read_text(encoding="utf-8"))
        connection.rollback()


@pytest.mark.postgres
def test_schema_reapply_rejects_wrong_version_or_incompatible_layout(
    worker_database: str,
) -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "update execution_worker.schema_migrations set version=2"
        )
        with pytest.raises(psycopg.Error):
            connection.execute(sql)
        connection.rollback()

    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "alter table execution_worker.local_runs "
            "alter column metabot_port type bigint"
        )
        with pytest.raises(psycopg.Error):
            connection.execute(sql)
        connection.rollback()

    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "alter table execution_worker.local_runs "
            "drop constraint local_runs_metabot_port_check"
        )
        connection.execute(
            "alter table execution_worker.local_runs add constraint "
            "local_runs_metabot_port_check check (true)"
        )
        with pytest.raises(psycopg.Error):
            connection.execute(sql)
        connection.rollback()


@pytest.mark.postgres
@pytest.mark.parametrize(
    "statements",
    [
        (
            "alter table execution_worker.local_runs drop constraint "
            "local_runs_metabot_port_check",
            "alter table execution_worker.local_runs add constraint "
            "local_runs_metabot_port_check check (metabot_port <= 65535)",
        ),
        (
            "alter table execution_worker.event_outbox drop constraint "
            "event_outbox_seq_check",
            "alter table execution_worker.event_outbox add constraint "
            "event_outbox_seq_check check (seq < 0)",
        ),
        (
            "alter table execution_worker.event_outbox drop constraint "
            "event_outbox_event_json_check",
            "alter table execution_worker.event_outbox add constraint "
            "event_outbox_event_json_check check "
            "(jsonb_typeof(event_json) <> 'object')",
        ),
        (
            "alter table execution_worker.local_runs drop constraint "
            "local_runs_job_id_key",
            "alter table execution_worker.local_runs add constraint "
            "local_runs_job_id_key unique (agent_id)",
        ),
        (
            "alter table execution_worker.event_outbox drop constraint "
            "event_outbox_pkey",
            "alter table execution_worker.event_outbox add constraint "
            "event_outbox_pkey primary key (seq,run_id)",
        ),
        (
            "alter table execution_worker.event_outbox drop constraint "
            "event_outbox_run_id_fkey",
            "alter table execution_worker.event_outbox add constraint "
            "event_outbox_run_id_fkey foreign key (run_id) "
            "references execution_worker.local_runs(job_id)",
        ),
        (
            "alter table execution_worker.local_runs add constraint "
            "local_runs_agent_id_key unique (agent_id)",
        ),
        (
            "alter table execution_worker.local_runs drop constraint "
            "local_runs_state_check",
            "alter table execution_worker.local_runs add constraint "
            "local_runs_state_check check (state = 'leased')",
        ),
        (
            "alter table execution_worker.local_runs drop constraint "
            "local_runs_metabot_port_check",
            "alter table execution_worker.local_runs add constraint "
            "local_runs_metabot_port_check check (metabot_port in (1,65535))",
        ),
        (
            "alter table execution_worker.event_outbox drop constraint "
            "event_outbox_seq_check",
            "alter table execution_worker.event_outbox add constraint "
            "event_outbox_seq_check check (seq = 1)",
        ),
        (
            "alter table execution_worker.local_runs add constraint "
            "local_runs_agent_port_key unique (agent_id,metabot_port)",
        ),
    ],
)
def test_schema_behavioral_validation_rejects_false_positive_layouts(
    worker_database: str, statements: tuple[str, ...]
) -> None:
    with psycopg.connect(worker_database) as connection:
        for statement in statements:
            connection.execute(statement)
        with pytest.raises(psycopg.Error, match="incompatible execution worker schema"):
            connection.execute(SCHEMA.read_text(encoding="utf-8"))
        connection.rollback()


def _seed_worker_rows(connection) -> tuple[tuple[object, ...], tuple[object, ...]]:
    run_id = UUID("00000000-0000-4000-8000-000000000151")
    job_id = UUID("00000000-0000-4000-8000-000000000152")
    connection.execute(
        "insert into execution_worker.local_runs "
        "(run_id,job_id,agent_id,metabot_port,callback_token_hash,state,"
        "leased_at,dispatched_at,terminal_at) values "
        "(%s,%s,'hr-bot',9101,%s,'running',%s,%s,null)",
        (run_id, job_id, bytes(range(32)), NOW, NOW + timedelta(seconds=1)),
    )
    connection.execute(
        "insert into execution_worker.event_outbox "
        "(run_id,seq,event_json,delivered_at) values (%s,1,%s::jsonb,%s)",
        (
            run_id,
            json.dumps(
                {
                    "run_id": str(run_id),
                    "seq": 1,
                    "event_type": "turn",
                    "created_at": NOW.isoformat(),
                    "payload": {"nested": [True, 1, "seeded"]},
                }
            ),
            NOW + timedelta(seconds=2),
        ),
    )
    local_row = connection.execute(
        "select run_id,job_id,agent_id,metabot_port,callback_token_hash,state,"
        "leased_at,dispatched_at,terminal_at "
        "from execution_worker.local_runs where run_id=%s",
        (run_id,),
    ).fetchone()
    outbox_row = connection.execute(
        "select run_id,seq,event_json,delivered_at "
        "from execution_worker.event_outbox where run_id=%s and seq=1",
        (run_id,),
    ).fetchone()
    return local_row, outbox_row


@pytest.mark.postgres
def test_schema_successful_reapply_preserves_seeded_rows_field_for_field(
    worker_database: str,
) -> None:
    with psycopg.connect(worker_database) as connection:
        before = _seed_worker_rows(connection)
        connection.execute(SCHEMA.read_text(encoding="utf-8"))
        run_id = before[0][0]
        after = (
            connection.execute(
                "select run_id,job_id,agent_id,metabot_port,callback_token_hash,"
                "state,leased_at,dispatched_at,terminal_at "
                "from execution_worker.local_runs where run_id=%s",
                (run_id,),
            ).fetchone(),
            connection.execute(
                "select run_id,seq,event_json,delivered_at "
                "from execution_worker.event_outbox where run_id=%s and seq=1",
                (run_id,),
            ).fetchone(),
        )
        assert after == before


@pytest.mark.postgres
def test_schema_failed_reapply_rolls_back_and_preserves_seeded_rows(
    worker_database: str,
) -> None:
    with psycopg.connect(worker_database) as connection:
        before = _seed_worker_rows(connection)
    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "alter table execution_worker.local_runs add constraint "
            "local_runs_agent_port_key unique (agent_id,metabot_port)"
        )
        with pytest.raises(psycopg.Error, match="incompatible execution worker schema"):
            connection.execute(SCHEMA.read_text(encoding="utf-8"))
        connection.rollback()
    with psycopg.connect(worker_database) as connection:
        run_id = before[0][0]
        after = (
            connection.execute(
                "select run_id,job_id,agent_id,metabot_port,callback_token_hash,"
                "state,leased_at,dispatched_at,terminal_at "
                "from execution_worker.local_runs where run_id=%s",
                (run_id,),
            ).fetchone(),
            connection.execute(
                "select run_id,seq,event_json,delivered_at "
                "from execution_worker.event_outbox where run_id=%s and seq=1",
                (run_id,),
            ).fetchone(),
        )
        assert after == before


@pytest.mark.postgres
def test_lease_is_durable_token_hashed_and_exactly_idempotent(
    worker_database: str, dsn_file: Path
) -> None:
    token = "callback-secret"
    WorkerStore.from_dsn_file(dsn_file).record_lease(_lease(), 9101, token)
    reopened = WorkerStore.from_dsn_file(dsn_file)

    reopened.record_lease(_lease(), 9101, token)
    assert reopened.callback_token_matches(RUN_ID, token) is True
    assert reopened.callback_token_matches(RUN_ID, "wrong") is False
    with psycopg.connect(worker_database) as connection:
        row = connection.execute(
            "select state,callback_token_hash from execution_worker.local_runs "
            "where run_id=%s",
            (RUN_ID,),
        ).fetchone()
    assert row == ("leased", hashlib.sha256(token.encode()).digest())

    mismatches = (
        (_lease(job_id=UUID("00000000-0000-4000-8000-000000000112")), 9101, token),
        (_lease(agent_id="fae-bot"), 9101, token),
        (_lease(), 9102, token),
        (_lease(), 9101, "different-secret"),
    )
    for lease, port, candidate in mismatches:
        with pytest.raises(WorkerStoreError) as error:
            reopened.record_lease(lease, port, candidate)
        assert str(error.value) == "worker store conflict"
        assert candidate not in str(error.value)


@pytest.mark.postgres
def test_concurrent_exact_lease_replay_is_idempotent(worker_database: str) -> None:
    barrier = Barrier(2)

    class SynchronizedConnection:
        def __init__(self, connection):
            self._connection = connection

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def execute(self, query, params=None):
            if query.startswith("insert into execution_worker.local_runs"):
                barrier.wait(timeout=5)
            cursor = self._connection.execute(query, params)
            return cursor

    def synchronized_connect(*args, **kwargs):
        return SynchronizedConnection(psycopg.connect(*args, **kwargs))

    store = WorkerStore(worker_database, connect=synchronized_connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: store.record_lease(
                    _lease(), 9101, "callback-secret"
                ),
                range(2),
            )
        )

    assert results == [None, None]


@pytest.mark.postgres
def test_dispatch_and_event_outbox_are_atomic_contiguous_and_durable(
    worker_database: str, dsn_file: Path
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    store.mark_dispatching(RUN_ID)
    store.mark_dispatched(RUN_ID)
    assert store.append_event(_event(1)) is True
    assert store.append_event(_event(1)) is False

    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        store.append_event(_event(1, payload={"text": "conflict"}))
    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        store.append_event(_event(3))

    reopened = WorkerStore.from_dsn_file(dsn_file)
    assert reopened.append_event(_event(2)) is True
    assert reopened.contiguous_outbox(RUN_ID) == (_event(1), _event(2))
    reopened.mark_delivered(RUN_ID, 1)
    assert reopened.contiguous_outbox(RUN_ID) == (_event(2),)
    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        reopened.mark_delivered(RUN_ID, 3)
    assert reopened.contiguous_outbox(RUN_ID) == (_event(2),)

    with psycopg.connect(worker_database) as connection:
        state = connection.execute(
            "select state from execution_worker.local_runs where run_id=%s",
            (RUN_ID,),
        ).fetchone()
        count = connection.execute(
            "select count(*) from execution_worker.event_outbox where run_id=%s",
            (RUN_ID,),
        ).fetchone()
    assert state == ("running",)
    assert count == (2,)


@pytest.mark.postgres
@pytest.mark.parametrize("local_status", ["completed", "failed"])
@pytest.mark.parametrize("cloud_status", ["interrupted", "cancelled"])
def test_forced_cloud_terminal_replaces_local_terminal_and_discards_outbox(
    worker_database: str,
    dsn_file: Path,
    local_status: str,
    cloud_status: str,
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    store.mark_dispatching(RUN_ID)
    store.mark_dispatched(RUN_ID)
    assert store.append_terminal_event(
        _terminal_event(local_status), local_status
    ) is True
    assert store.contiguous_outbox(RUN_ID) == (_terminal_event(local_status),)

    store.reconcile_forced_terminal(RUN_ID, cloud_status)
    store.reconcile_forced_terminal(RUN_ID, cloud_status)

    assert store.contiguous_outbox(RUN_ID) == ()
    with psycopg.connect(worker_database) as connection:
        row = connection.execute(
            "select state,terminal_at is not null from "
            "execution_worker.local_runs where run_id=%s",
            (RUN_ID,),
        ).fetchone()
        outbox_count = connection.execute(
            "select count(*) from execution_worker.event_outbox "
            "where run_id=%s and delivered_at is null",
            (RUN_ID,),
        ).fetchone()
    assert row == (cloud_status, True)
    assert outbox_count == (0,)


@pytest.mark.postgres
def test_local_state_probe_distinguishes_missing_and_persisted_run(
    dsn_file: Path,
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    assert store.has_local_state(RUN_ID) is False
    store.record_lease(_lease(), 9101, "callback-secret")
    assert store.has_local_state(RUN_ID) is True


@pytest.mark.postgres
def test_callback_winning_dispatch_race_keeps_running_and_records_acceptance(
    worker_database: str, dsn_file: Path
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    store.mark_dispatching(RUN_ID)
    assert store.append_event(_event(1)) is True

    store.mark_dispatched(RUN_ID)

    with psycopg.connect(worker_database) as connection:
        row = connection.execute(
            "select state,dispatched_at is not null "
            "from execution_worker.local_runs where run_id=%s",
            (RUN_ID,),
        ).fetchone()
    assert row == ("running", True)


@pytest.mark.postgres
def test_recovery_rows_preserve_acceptance_and_outbox_facts(dsn_file: Path) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    store.mark_dispatching(RUN_ID)
    store.append_event(_event(1))
    store.mark_terminal(RUN_ID, "interrupted")

    rows = store.recoverable_runs()

    assert len(rows) == 1
    assert rows[0].run_id == RUN_ID
    assert rows[0].agent_id == "hr-bot"
    assert rows[0].state == "interrupted"
    assert rows[0].dispatched_at is None
    assert rows[0].has_events is True


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize("losing_status", ["cancelled", "interrupted"])
async def test_terminal_callback_race_has_one_database_and_http_fact(
    dsn_file: Path, worker_database: str, losing_status: str
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    token = "C" * 43
    store.record_lease(_lease(), 9101, token)
    store.mark_dispatching(RUN_ID)
    barrier = Barrier(2)
    atomic_calls = 0

    class BarrierStore:
        def __getattr__(self, name):
            return getattr(store, name)

        def append_terminal_event(self, event, status):
            nonlocal atomic_calls
            barrier.wait(timeout=2)
            atomic_calls += 1
            return store.append_terminal_event(event, status)

        def mark_terminal(self, run_id, status):
            barrier.wait(timeout=2)
            return store.mark_terminal(run_id, status)

        def reconcile_forced_terminal(self, run_id, status):
            barrier.wait(timeout=2)
            return store.reconcile_forced_terminal(run_id, status)

    class Cloud:
        async def heartbeat(self):
            return (RUN_ID,)

    class MetaBot:
        def cancel_run(self, _run_id, _agent_id):
            return None

    class RuntimeMap:
        def port_for(self, _agent_id):
            return 9101

    runtime = WorkerRuntime(
        worker_id="worker-a",
        cloud=Cloud(),
        store=BarrierStore(),
        runtime_map=RuntimeMap(),
        metabot=MetaBot(),
        callback_port=9120,
    )
    runtime.recover_run(RUN_ID, "hr-bot")
    callback = asyncio.create_task(
        runtime.accept_callback(
            RUN_ID, token, _terminal_callback_body()
        )
    )
    competitor = asyncio.create_task(
        runtime.heartbeat_once()
        if losing_status == "cancelled"
        else runtime.interrupt_active()
    )
    callback_result, _competitor_result = await asyncio.gather(
        callback, competitor
    )
    assert atomic_calls == 1

    with psycopg.connect(worker_database) as connection:
        state = connection.execute(
            "select state from execution_worker.local_runs where run_id=%s",
            (RUN_ID,),
        ).fetchone()[0]
        rows = connection.execute(
            "select event_json from execution_worker.event_outbox "
            "where run_id=%s",
            (RUN_ID,),
        ).fetchall()
    if state == "completed":
        assert callback_result is CallbackResult.ACCEPTED
        assert rows == [(_terminal_event().model_dump(mode="json"),)]
    else:
        assert state == losing_status
        if losing_status == "interrupted":
            assert callback_result is CallbackResult.CONFLICT
        else:
            assert callback_result in {
                CallbackResult.ACCEPTED,
                CallbackResult.CONFLICT,
            }
        assert rows == []


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_concurrent_exact_terminal_callbacks_are_both_idempotent_204(
    dsn_file: Path, worker_database: str
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    token = "D" * 43
    store.record_lease(_lease(), 9101, token)
    store.mark_dispatching(RUN_ID)
    barrier = Barrier(2)
    atomic_calls = 0

    class BarrierStore:
        def __getattr__(self, name):
            return getattr(store, name)

        def append_terminal_event(self, event, status):
            nonlocal atomic_calls
            barrier.wait(timeout=2)
            atomic_calls += 1
            return store.append_terminal_event(event, status)

    runtime = WorkerRuntime(
        worker_id="worker-a",
        cloud=object(),
        store=BarrierStore(),
        runtime_map=object(),
        metabot=object(),
        callback_port=9120,
    )
    runtime.recover_run(RUN_ID, "hr-bot")
    body = _terminal_callback_body()

    results = await asyncio.gather(
        runtime.accept_callback(RUN_ID, token, body),
        runtime.accept_callback(RUN_ID, token, body),
    )

    assert atomic_calls == 2
    assert results == [CallbackResult.ACCEPTED, CallbackResult.ACCEPTED]
    with psycopg.connect(worker_database) as connection:
        row = connection.execute(
            "select r.state,count(o.seq) from execution_worker.local_runs r "
            "left join execution_worker.event_outbox o on o.run_id=r.run_id "
            "where r.run_id=%s group by r.state",
            (RUN_ID,),
        ).fetchone()
    assert row == ("completed", 1)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_real_loopback_and_postgres_survive_races_failures_and_restarts(
    worker_database: str, dsn_file: Path, tmp_path: Path, request
) -> None:
    trace: list[tuple[object, ...]] = []
    lease = _lease()
    failure_counts = {"dispatched": 1, "events": 1}

    class CloudHandler(BaseHTTPRequestHandler):
        def _reply(self, status: int, value: dict[str, object] | None = None):
            body = b"" if value is None else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if body:
                self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            assert self.headers.get("X-Orbbec-Worker-Signature")
            trace.append(("cloud", self.path, json.loads(body)))
            if self.path.endswith("/lease"):
                self._reply(200, lease.model_dump(mode="json"))
            elif self.path.endswith("/heartbeat"):
                self._reply(200, {"cancel_requested_run_ids": []})
            elif self.path.endswith("/dispatched"):
                if failure_counts["dispatched"]:
                    failure_counts["dispatched"] -= 1
                    self._reply(503, {"detail": "offline"})
                else:
                    self._reply(200, {"status": "accepted"})
            elif self.path.endswith("/events"):
                if failure_counts["events"]:
                    failure_counts["events"] -= 1
                    self._reply(503, {"detail": "offline"})
                else:
                    count = len(json.loads(body)["events"])
                    self._reply(200, {"accepted": count, "inserted": count})
            elif self.path.endswith("/terminal"):
                self._reply(200, {"status": "accepted"})
            else:
                self._reply(404)

        def log_message(self, _format, *_args):
            return None

    class MetaBotHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            if self.path == "/api/core-chat/runs":
                trace.append(("metabot_start", request["runId"]))
                with httpx.Client(trust_env=False) as client:
                    response = client.post(
                        request["eventCallbackUrl"],
                        content=_terminal_callback_body(
                            execution_chat_id=request["executionChatId"]
                        ),
                        headers={"Content-Type": "application/json"},
                    )
                trace.append(("callback_status", response.status_code))
                value = {
                    "status": "accepted",
                    "runId": request["runId"],
                    "targetBot": request["targetBot"],
                }
                body = json.dumps(value).encode()
                self.send_response(202)
            else:
                body = json.dumps({"runId": self.path.rsplit("/", 2)[-2]}).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return None

    cloud_server = ThreadingHTTPServer(("127.0.0.1", 0), CloudHandler)
    metabot_server = ThreadingHTTPServer(("127.0.0.1", 0), MetaBotHandler)
    cloud_thread = Thread(target=cloud_server.serve_forever, daemon=True)
    metabot_thread = Thread(target=metabot_server.serve_forever, daemon=True)
    cloud_thread.start()
    metabot_thread.start()

    def stop_servers() -> None:
        cloud_server.shutdown()
        metabot_server.shutdown()
        cloud_server.server_close()
        metabot_server.server_close()
        cloud_thread.join(timeout=2)
        metabot_thread.join(timeout=2)

    request.addfinalizer(stop_servers)

    secret_dir = tmp_path / "metabot-secrets"
    secret_dir.mkdir(mode=0o700)
    bearer_file = secret_dir / "bearer"
    bearer_file.write_text("local-bearer", encoding="utf-8")
    bearer_file.chmod(0o600)
    bot_ids = (
        "hr-bot",
        "marketing-prospecting-bot",
        "marketing-inbound-bot",
        "marketing-voice-bot",
        "fae-bot",
        "marketing-gtm-bot",
        "marketing-intelligence-bot",
    )
    used = {metabot_server.server_port}
    ports: dict[str, int] = {"hr-bot": metabot_server.server_port}
    candidate = 30_000
    for agent_id in bot_ids[1:]:
        while candidate in used:
            candidate += 1
        ports[agent_id] = candidate
        used.add(candidate)
        candidate += 1
    runtime_map = MetaBotRuntimeMap(ports)
    metabot = MetaBotClient(runtime_map, bearer_file)
    private_key = Ed25519PrivateKey.generate()

    def cloud_client() -> SignedCloudClient:
        signer = WorkerRequestSigner("worker-a", "worker-v1", private_key)
        return SignedCloudClient(
            f"http://127.0.0.1:{cloud_server.server_port}", signer
        )

    store = WorkerStore.from_dsn_file(dsn_file)
    first_cloud = cloud_client()
    first = WorkerRuntime(
        worker_id="worker-a",
        cloud=first_cloud,
        store=store,
        runtime_map=runtime_map,
        metabot=metabot,
        callback_port=0,
        token_factory=lambda: "A" * 43,
    )
    callback_task = asyncio.create_task(callback_server(first))
    try:
        await asyncio.wait_for(first.callback_ready.wait(), timeout=1)
        assert await first.lease_once() is False
        with psycopg.connect(worker_database) as connection:
            row = connection.execute(
                "select state,dispatched_at is not null from "
                "execution_worker.local_runs where run_id=%s",
                (RUN_ID,),
            ).fetchone()
            outbox = connection.execute(
                "select count(*),bool_and(delivered_at is null) from "
                "execution_worker.event_outbox where run_id=%s",
                (RUN_ID,),
            ).fetchone()
        assert row == ("completed", True)
        assert outbox == (1, True)
        assert ("callback_status", 204) in trace

        assert await first.upload_once() is False
        assert store.contiguous_outbox(RUN_ID) != ()
    finally:
        first.stop()
        await asyncio.wait_for(callback_task, timeout=1)
        await first_cloud.aclose()

    second_cloud = cloud_client()
    second = WorkerRuntime(
        worker_id="worker-a",
        cloud=second_cloud,
        store=WorkerStore.from_dsn_file(dsn_file),
        runtime_map=runtime_map,
        metabot=metabot,
        callback_port=0,
    )
    await second.recover_local_state()
    assert await second.lease_once() is True
    assert await second.upload_once() is True
    await second_cloud.aclose()

    cloud_paths = [entry[1] for entry in trace if entry[0] == "cloud"]
    assert cloud_paths.count("/api/v1/execution-worker/lease") == 1
    assert [entry[0] for entry in trace].count("metabot_start") == 1
    final_paths = cloud_paths[-3:]
    assert final_paths[0].endswith("/dispatched")
    assert final_paths[1].endswith("/events")
    assert final_paths[2].endswith("/terminal")
    assert store.contiguous_outbox(RUN_ID) == ()

    run2 = UUID("00000000-0000-4000-8000-000000000201")
    job2 = UUID("00000000-0000-4000-8000-000000000211")
    store.record_lease(_lease(job_id=job2, run_id=run2), 9101, "B" * 43)
    store.mark_dispatching(run2)
    third_cloud = cloud_client()
    third = WorkerRuntime(
        worker_id="worker-a",
        cloud=third_cloud,
        store=store,
        runtime_map=runtime_map,
        metabot=metabot,
        callback_port=0,
    )
    await third.recover_local_state()
    assert await third.lease_once() is True
    assert [entry[0] for entry in trace].count("metabot_start") == 1
    assert {row.run_id: row.state for row in store.recoverable_runs()}[run2] == (
        "interrupted"
    )
    await third_cloud.aclose()



@pytest.mark.postgres
def test_event_replay_is_type_sensitive_and_conflicts_leave_state_unchanged(
    worker_database: str, dsn_file: Path
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    store.mark_dispatching(RUN_ID)
    original = _event(
        1,
        payload={
            "nested": {
                "enabled": True,
                "items": [{"accepted": False}, 1],
            }
        },
    )
    assert store.append_event(original) is True

    conflicting_replays = (
        _event(
            1,
            payload={
                "nested": {
                    "enabled": 1,
                    "items": [{"accepted": False}, 1],
                }
            },
        ),
        _event(
            1,
            payload={
                "nested": {
                    "enabled": True,
                    "items": [{"accepted": 0}, 1],
                }
            },
        ),
    )
    for replay in conflicting_replays:
        with pytest.raises(WorkerStoreError, match="worker store conflict"):
            store.append_event(replay)

    with psycopg.connect(worker_database) as connection:
        row = connection.execute(
            "select r.state,"
            "(select count(*) from execution_worker.event_outbox c "
            " where c.run_id=r.run_id),o.event_json "
            "from execution_worker.local_runs r "
            "join execution_worker.event_outbox o on o.run_id=r.run_id "
            "where r.run_id=%s",
            (RUN_ID,),
        ).fetchone()
    assert row == ("running", 1, original.model_dump(mode="json"))


@pytest.mark.postgres
def test_event_replay_uses_postgresql_jsonb_numeric_equality(
    worker_database: str, dsn_file: Path
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    store.mark_dispatching(RUN_ID)
    original = _event(
        1,
        payload={
            "numbers": {
                "exponent": 100.0,
                "large_finite": 1e300,
            }
        },
    )
    assert store.append_event(original) is True
    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "update execution_worker.event_outbox set event_json="
            "jsonb_set(jsonb_set(event_json,'{payload,numbers,exponent}',"
            "'1e2'::jsonb),'{payload,numbers,large_finite}','1e300'::jsonb) "
            "where run_id=%s and seq=1",
            (RUN_ID,),
        )

    assert store.append_event(original) is False
    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        store.append_event(
            original.model_copy(
                update={
                    "payload": {
                        "numbers": {
                            "exponent": True,
                            "large_finite": 1e300,
                        }
                    }
                }
            )
        )


@pytest.mark.postgres
def test_illegal_transitions_are_sanitized_and_never_reset_dispatching(
    worker_database: str, dsn_file: Path
) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        store.mark_dispatched(RUN_ID)
    store.mark_dispatching(RUN_ID)
    store.record_lease(_lease(), 9101, "callback-secret")
    with psycopg.connect(worker_database) as connection:
        assert connection.execute(
            "select state from execution_worker.local_runs where run_id=%s",
            (RUN_ID,),
        ).fetchone() == ("dispatching",)
    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        store.mark_terminal(RUN_ID, "completed")
    store.mark_terminal(RUN_ID, "interrupted")
    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        store.append_event(_event(1))


@pytest.mark.postgres
def test_running_run_accepts_only_supported_terminal_states(dsn_file: Path) -> None:
    store = WorkerStore.from_dsn_file(dsn_file)
    store.record_lease(_lease(), 9101, "callback-secret")
    store.mark_dispatching(RUN_ID)
    store.append_event(_event(1))
    with pytest.raises(WorkerStoreError, match="worker store conflict"):
        store.mark_terminal(RUN_ID, "leased")
    store.mark_terminal(RUN_ID, "completed")


def test_dsn_file_must_be_absolute_regular_0600_below_0700_parent(
    worker_database: str, tmp_path: Path, monkeypatch
) -> None:
    parent = tmp_path / "secrets"
    parent.mkdir(mode=0o700)
    dsn = parent / "worker.dsn"
    dsn.write_text(worker_database, encoding="utf-8")
    dsn.chmod(0o600)
    WorkerStore.from_dsn_file(dsn)

    monkeypatch.chdir(parent)
    with pytest.raises(WorkerStoreError, match="worker store configuration invalid"):
        WorkerStore.from_dsn_file(Path("worker.dsn"))
    dsn.chmod(0o640)
    with pytest.raises(WorkerStoreError, match="worker store configuration invalid"):
        WorkerStore.from_dsn_file(dsn)
    dsn.chmod(0o600)
    parent.chmod(0o750)
    with pytest.raises(WorkerStoreError, match="worker store configuration invalid"):
        WorkerStore.from_dsn_file(dsn)
    parent.chmod(0o700)
    assert stat.S_IMODE(dsn.stat().st_mode) == 0o600

    file_link = parent / "worker-link.dsn"
    file_link.symlink_to(dsn)
    with pytest.raises(WorkerStoreError, match="worker store configuration invalid"):
        WorkerStore.from_dsn_file(file_link)

    real_parent = tmp_path / "real-secrets"
    real_parent.mkdir(mode=0o700)
    real_dsn = real_parent / "worker.dsn"
    real_dsn.write_text(worker_database, encoding="utf-8")
    real_dsn.chmod(0o600)
    parent_link = tmp_path / "linked-secrets"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(WorkerStoreError, match="worker store configuration invalid"):
        WorkerStore.from_dsn_file(parent_link / "worker.dsn")

    directory_path = parent / "not-a-file"
    directory_path.mkdir(mode=0o700)
    with pytest.raises(WorkerStoreError, match="worker store configuration invalid"):
        WorkerStore.from_dsn_file(directory_path)

    oversized = parent / "oversized.dsn"
    oversized.write_text("x" * 16_385, encoding="utf-8")
    oversized.chmod(0o600)
    with pytest.raises(WorkerStoreError, match="worker store configuration invalid"):
        WorkerStore.from_dsn_file(oversized)


def test_dsn_path_swap_after_open_reads_only_opened_descriptor(
    worker_database: str, tmp_path: Path, monkeypatch
) -> None:
    parent = tmp_path / "secrets"
    parent.mkdir(mode=0o700)
    dsn = parent / "worker.dsn"
    dsn.write_text(worker_database, encoding="utf-8")
    dsn.chmod(0o600)
    original_open = worker_store.os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == dsn.name:
            dsn.unlink()
            dsn.write_text("postgresql://attacker/changed", encoding="utf-8")
            dsn.chmod(0o600)
            swapped = True
        return descriptor

    monkeypatch.setattr(worker_store.os, "open", swapping_open)
    store = WorkerStore.from_dsn_file(dsn)

    assert swapped is True
    assert store._database_url == worker_database


def test_dsn_descriptor_close_failures_are_sanitized_and_independent(
    dsn_file: Path, monkeypatch
) -> None:
    original_close = worker_store.os.close
    closed: list[int] = []

    def first_close_fails(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)
        if len(closed) == 1:
            raise OSError("raw close failure")

    monkeypatch.setattr(worker_store.os, "close", first_close_fails)
    with pytest.raises(WorkerStoreError) as error:
        WorkerStore.from_dsn_file(dsn_file)

    assert str(error.value) == "worker store configuration invalid"
    assert error.value.__cause__ is None
    assert len(closed) == 2
