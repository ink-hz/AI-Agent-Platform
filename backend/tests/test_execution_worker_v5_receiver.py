from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from app.execution_relay.worker import CallbackResult, WorkerRuntime, callback_server
from app.execution_relay.worker_store import WorkerStore, WorkerStoreError
from app.execution_relay.worker_v5_receiver import TrustedV5CallbackBinding
from tests.test_execution_worker_store import _lease, worker_database  # noqa: F401

CASES = json.loads(
    (Path(__file__).parents[2] / "contracts/hr-execution/v5/cases.json").read_text()
)
MIGRATION = (
    Path(__file__).parents[1] / "app/execution_relay/pending/worker_v5_receiver.sql"
)
TOKEN = "A" * 43


@pytest.fixture()
def receiver(worker_database):  # noqa: F811 -- Imported fixture is registered with pytest.
    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "DELETE FROM execution_worker.event_outbox; DELETE FROM execution_worker.local_runs"
        )
        connection.execute(
            "DROP TABLE IF EXISTS execution_worker.v5_callback_events, execution_worker.v5_callback_runs, execution_worker.v5_callback_metadata"
        )
        connection.execute(MIGRATION.read_text())
    store = WorkerStore(worker_database)
    c = deepcopy(CASES["command"]["valid"])
    c["eventCallbackUrl"] = f"http://127.0.0.1:9120/callbacks/{c['runId']}/{TOKEN}"
    binding = TrustedV5CallbackBinding(
        "synthetic-worker",
        UUID(c["commandId"]),
        UUID(c["runId"]),
        UUID(c["attemptId"]),
        c["commandHash"],
        c["leaseEpoch"],
        c["leaseEpoch"],
        "http://127.0.0.1:9120",
    )
    store.register_v5_callback(c, binding)
    runtime = WorkerRuntime(
        worker_id="synthetic-worker",
        cloud=None,
        store=store,
        runtime_map=None,
        metabot=None,
        callback_port=0,
        enable_v5_callbacks=True,
    )
    return worker_database, store, runtime, c, binding


def event(c, seq=1, kind="raw_progress"):
    e = deepcopy(next(e for e in CASES["callback"]["validEvents"] if e["type"] == kind))
    e.update(
        runId=c["runId"],
        commandId=c["commandId"],
        attemptId=c["attemptId"],
        leaseEpoch=c["leaseEpoch"],
        seq=seq,
    )
    return e


def send(runtime, c, e, token=TOKEN):
    return asyncio.run(
        runtime.accept_callback(UUID(c["runId"]), token, json.dumps(e).encode())
    )


def test_contiguous_duplicate_conflict_gap_and_terminal(receiver):
    dsn, _, runtime, c, _ = receiver
    first = event(c)
    assert send(runtime, c, first).status == "accepted"
    assert send(runtime, c, first).status == "duplicate"
    changed = deepcopy(first)
    changed["payload"]["text"] = "synthetic changed"
    conflict = send(runtime, c, changed)
    assert (conflict.status, conflict.accepted_through) == ("conflict", 1)
    gap = send(runtime, c, event(c, 5))
    assert (gap.status, gap.accepted_through, gap.expected_seq) == ("gap", 1, 2)
    assert send(runtime, c, event(c, 2, "cancelled")).status == "accepted"
    assert send(runtime, c, event(c, 3, "result")).status == "conflict"
    reopened = WorkerStore(dsn)
    runtime.store = reopened
    assert send(runtime, c, event(c, 2, "cancelled")).status == "duplicate"
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT event_type FROM execution_worker.v5_callback_events ORDER BY seq"
        ).fetchall() == [("raw_progress",), ("cancelled",)]


def test_concurrent_duplicate_and_competing_terminal(receiver):
    _, _, runtime, c, _ = receiver
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: send(runtime, c, event(c)), range(4)))
    assert sorted(r.status for r in results) == [
        "accepted",
        "duplicate",
        "duplicate",
        "duplicate",
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda k: send(runtime, c, event(c, 2, k)), ["cancelled", "result"]
            )
        )
    assert sorted(r.status for r in results) == ["accepted", "conflict"]


def test_rotation_retains_original_launch_and_cursor(receiver):
    _, store, runtime, c, binding = receiver
    original = event(c, 1, "cancelled")
    assert send(runtime, c, original).status == "accepted"
    rotated = deepcopy(c)
    rotated["leaseEpoch"] += 1
    rotated["eventCallbackUrl"] = rotated["eventCallbackUrl"].replace(TOKEN, "B" * 43)
    store.register_v5_callback(
        rotated, replace(binding, transport_lease_epoch=rotated["leaseEpoch"])
    )
    assert send(runtime, c, original) is CallbackResult.UNAUTHORIZED
    assert send(runtime, c, original, "B" * 43).status == "duplicate"
    with pytest.raises(ValueError, match="v5 registration"):
        store.register_v5_callback(c, binding)
    with pytest.raises(ValueError, match="v5 registration"):
        store.register_v5_callback(
            rotated,
            replace(
                binding,
                transport_lease_epoch=rotated["leaseEpoch"],
                worker_id="unrelated",
            ),
        )


@pytest.mark.parametrize(
    "field",
    ["commandId", "runId", "attemptId", "leaseEpoch", "type", "contractVersion"],
)
def test_invalid_identity_or_vocabulary_leaves_no_evidence(receiver, field):
    dsn, _, runtime, c, _ = receiver
    invalid = event(c)
    invalid[field] = 99 if field == "leaseEpoch" else "not-valid"
    assert send(runtime, c, invalid) is CallbackResult.INVALID
    assert (
        asyncio.run(runtime.accept_callback(UUID(c["runId"]), "Z" * 43, b"not json"))
        is CallbackResult.UNAUTHORIZED
    )
    with psycopg.connect(dsn) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM execution_worker.v5_callback_events"
            ).fetchone()[0]
            == 0
        )


def test_receiver_rollback_reopen_and_raw_evidence(receiver):
    dsn, _, runtime, c, _ = receiver
    raw = event(c)
    raw["createdAt"] = "2026-09-07T15:00:00.123456+08:00"
    raw["runId"] = raw["runId"].upper()
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "ALTER TABLE execution_worker.v5_callback_runs ADD CONSTRAINT synthetic_reject CHECK(accepted_through=0)"
        )
    assert send(runtime, c, raw) is CallbackResult.CONFLICT
    with psycopg.connect(dsn) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM execution_worker.v5_callback_events"
            ).fetchone()[0]
            == 0
        )
        connection.execute(
            "ALTER TABLE execution_worker.v5_callback_runs DROP CONSTRAINT synthetic_reject"
        )
    runtime.store = WorkerStore(dsn)
    assert send(runtime, c, raw).status == "accepted"
    with psycopg.connect(dsn) as connection:
        assert (
            json.loads(
                connection.execute(
                    "SELECT event_json FROM execution_worker.v5_callback_events"
                ).fetchone()[0]
            )
            == raw
        )


def test_mixed_legacy_and_v5_run_registration_rejects_both_directions(receiver):
    dsn, store, _, c, binding = receiver
    lease = _lease(run_id=UUID(c["runId"]))
    with pytest.raises(WorkerStoreError):
        store.record_lease(lease, 9120, TOKEN)
    with psycopg.connect(dsn) as connection:
        connection.execute("DELETE FROM execution_worker.v5_callback_runs")
    store.record_lease(lease, 9120, TOKEN)
    try:
        with pytest.raises(ValueError, match="v5 registration"):
            store.register_v5_callback(c, binding)
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute("DELETE FROM execution_worker.local_runs")


def test_disabled_missing_extension_and_wrong_machine_fail_closed(receiver):
    dsn, _, runtime, c, _ = receiver
    runtime.enable_v5_callbacks = False
    assert send(runtime, c, event(c)) is CallbackResult.UNAUTHORIZED
    runtime.enable_v5_callbacks = True
    runtime.worker_id = "unrelated"
    assert send(runtime, c, event(c)) is CallbackResult.UNAUTHORIZED
    runtime.worker_id = "synthetic-worker"
    with psycopg.connect(dsn) as connection:
        connection.execute("DELETE FROM execution_worker.v5_callback_metadata")
    assert send(runtime, c, event(c)) is CallbackResult.CONFLICT


def test_registration_missing_launch_or_wrong_origin_does_not_rotate(receiver):
    _, store, runtime, c, binding = receiver
    for changed in (
        replace(binding, launch_lease_epoch=None),
        replace(binding, callback_origin="http://127.0.0.1:1"),
        replace(binding, command_hash="0" * 64),
    ):
        with pytest.raises(ValueError):
            store.register_v5_callback(c, changed)
    assert send(runtime, c, event(c)).status == "accepted"


def test_pending_extension_refuses_base_layout_drift(receiver):
    dsn, _, _, _, _ = receiver
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "DROP TABLE execution_worker.v5_callback_events, execution_worker.v5_callback_runs, execution_worker.v5_callback_metadata"
        )
        connection.execute(
            "ALTER TABLE execution_worker.local_runs ADD COLUMN synthetic_drift text"
        )
    try:
        with (
            pytest.raises(psycopg.Error, match="layout"),
            psycopg.connect(dsn) as connection,
        ):
            connection.execute(MIGRATION.read_text())
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "ALTER TABLE execution_worker.local_runs DROP COLUMN synthetic_drift"
            )


def test_real_socket_auth_json_size_and_v5_ack_routing(receiver):
    dsn, _, runtime, c, _ = receiver

    async def exercise():
        server = asyncio.create_task(callback_server(runtime))
        await runtime.callback_ready.wait()

        async def request(body, token=TOKEN, length=None):
            reader, writer = await asyncio.open_connection('127.0.0.1', runtime.callback_port)
            writer.write((f"POST /callbacks/{c['runId']}/{token} HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: {len(body) if length is None else length}\r\n\r\n").encode() + body)
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            header, content = response.split(b'\r\n\r\n', 1)
            return int(header.split(b' ')[1]), content

        try:
            assert (await request(b'not-json', 'Z' * 43))[0] == 401
            assert (await request(b'not-json', ''))[0] == 401
            assert (await request(b'not-json'))[0] == 400
            assert (await request(b'', length=1048577))[0] == 413
            legacy = {'runId': c['runId'], 'seq': 1, 'type': 'complete', 'payload': {}}
            assert (await request(json.dumps(legacy).encode()))[0] == 400
            body = json.dumps(event(c)).encode()
            code, content = await request(body)
            assert code == 200 and json.loads(content)['status'] == 'accepted'
            code, content = await request(body)
            assert code == 200 and json.loads(content)['status'] == 'duplicate'
            code, content = await request(json.dumps(event(c, 5)).encode())
            assert code == 409 and json.loads(content)['expectedSeq'] == 2
        finally:
            runtime.stop_event.set()
            await server

    asyncio.run(exercise())
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT count(*) FROM execution_worker.v5_callback_events').fetchone()[0] == 1


def test_legacy_forced_terminal_cannot_remove_v5_cancelled_evidence(receiver):
    dsn, store, runtime, c, _ = receiver
    assert send(runtime, c, event(c, 1, 'cancelled')).status == 'accepted'
    with pytest.raises(WorkerStoreError):
        store.reconcile_forced_terminal(UUID(c['runId']), 'interrupted')
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT event_type FROM execution_worker.v5_callback_events').fetchall() == [('cancelled',)]
