from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
from threading import Barrier
from uuid import UUID

import psycopg
import pytest

from app.execution_relay import worker_store
from app.execution_relay.models import RelayEvent, RelayJobPayload, RelayLease
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
