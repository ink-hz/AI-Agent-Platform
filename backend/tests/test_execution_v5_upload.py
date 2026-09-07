import asyncio
import json
from uuid import UUID

import httpx
import psycopg
import pytest
from test_execution_worker_v5_receiver import event, send
from test_execution_worker_v5_receiver import (
    receiver as receiver,  # noqa: PLC0414 - pytest fixture re-export
)

from app.execution_relay.upload_v5 import acknowledge, claim, drain_once
from app.execution_relay.worker_store import WorkerStore
from tests.test_execution_worker_store import worker_database  # noqa: F401

pytestmark = pytest.mark.postgres


class Cloud:
    def __init__(self, run_id, status="accepted", cursor=1):
        self.run_id, self.status, self.cursor = run_id, status, cursor
        self.bodies = []

    async def post_v5_bytes(self, path, body):
        self.bodies.append(body)
        return httpx.Response(
            409 if self.status in {"gap", "conflict"} else 200,
            json={
                "status": self.status,
                "runId": self.run_id,
                "acceptedThrough": self.cursor,
                "expectedSeq": self.cursor + 1,
            },
        )


def test_upload_uses_durable_original_bytes_and_only_acknowledges_sent_seq(receiver):
    dsn, store, runtime, command, _ = receiver
    raw = json.dumps(event(command), indent=2).encode()
    assert (
        asyncio.run(
            runtime.accept_callback(UUID(command["runId"]), "A" * 43, raw)
        ).status
        == "accepted"
    )
    cloud = Cloud(command["runId"], status="duplicate", cursor=5)
    assert asyncio.run(drain_once(store, cloud, "synthetic-worker")) is True
    assert cloud.bodies == [raw]
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "select uploaded_through,upload_next_seq from execution_worker.v5_callback_runs"
        ).fetchone() == (1, 2)
    assert asyncio.run(drain_once(store, cloud, "synthetic-worker")) is False


def test_gap_resends_durable_expected_sequence_without_regressing_ack(receiver):
    dsn, store, runtime, command, _ = receiver
    for seq in (1, 2, 3):
        assert send(runtime, command, event(command, seq)).status == "accepted"
    asyncio.run(drain_once(store, Cloud(command["runId"]), "synthetic-worker"))
    assert asyncio.run(
        drain_once(store, Cloud(command["runId"], "gap", 0), "synthetic-worker")
    )
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "select uploaded_through,upload_next_seq from execution_worker.v5_callback_runs"
        ).fetchone() == (1, 1)


@pytest.mark.parametrize("failure", ["malformed", "conflict", "missing"])
def test_protocol_and_missing_source_isolate_only_this_run_durably(receiver, failure):
    dsn, store, runtime, command, _ = receiver
    send(runtime, command, event(command))
    cloud = Cloud(command["runId"], "conflict")
    if failure == "malformed":

        async def malformed(*args):
            return httpx.Response(200, content=b"not-json")

        cloud.post_v5_bytes = malformed
    if failure == "missing":
        with psycopg.connect(dsn) as connection:
            connection.execute("delete from execution_worker.v5_callback_events")
    asyncio.run(drain_once(store, cloud, "synthetic-worker"))
    with psycopg.connect(dsn) as connection:
        assert (
            connection.execute(
                "select upload_isolation_code from execution_worker.v5_callback_runs"
            ).fetchone()[0]
            is not None
        )
    assert asyncio.run(drain_once(WorkerStore(dsn), cloud, "synthetic-worker")) is False


def test_transport_failure_backoff_survives_restart(receiver):
    dsn, store, runtime, command, _ = receiver
    send(runtime, command, event(command))

    class Offline:
        async def post_v5_bytes(self, *args):
            raise OSError("synthetic disconnected socket")

    assert asyncio.run(drain_once(store, Offline(), "synthetic-worker"))
    assert (
        asyncio.run(drain_once(WorkerStore(dsn), Offline(), "synthetic-worker"))
        is False
    )
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "select upload_failures,upload_next_at>clock_timestamp() from execution_worker.v5_callback_runs"
        ).fetchone() == (1, True)


def test_stale_parallel_ack_cannot_regress_a_newer_drain(receiver):
    dsn, store, runtime, command, _ = receiver
    send(runtime, command, event(command))
    old = claim(store, "synthetic-worker")
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "update execution_worker.v5_callback_runs set upload_next_at=now()-interval '1 second'"
        )
    current = claim(store, "synthetic-worker")
    acknowledge(
        store, current, asyncio.run(Cloud(command["runId"]).post_v5_bytes("", b""))
    )
    acknowledge(
        store,
        old,
        asyncio.run(Cloud(command["runId"], "gap", 0).post_v5_bytes("", b"")),
    )
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "select uploaded_through,upload_next_seq from execution_worker.v5_callback_runs"
        ).fetchone() == (1, 2)


@pytest.mark.parametrize("status,cursor", [("gap", 0), ("accepted", 5)])
def test_inconsistent_ack_cannot_create_a_resend_loop_or_false_receipt(
    receiver, status, cursor
):
    dsn, store, runtime, command, _ = receiver
    send(runtime, command, event(command))
    asyncio.run(
        drain_once(store, Cloud(command["runId"], status, cursor), "synthetic-worker")
    )
    with psycopg.connect(dsn) as connection:
        assert (
            connection.execute(
                "select upload_isolation_code from execution_worker.v5_callback_runs"
            ).fetchone()[0]
            == "ack_invalid"
        )


def test_isolation_of_one_run_does_not_starve_independent_run(receiver):
    from dataclasses import replace
    from uuid import uuid4

    from app.execution_relay.contracts_v5 import (
        core_chat_command_hash,
        parse_v5_command,
    )

    _, store, runtime, command, binding = receiver
    send(runtime, command, event(command))
    asyncio.run(
        drain_once(store, Cloud(command["runId"], "conflict"), "synthetic-worker")
    )
    new_run, new_command, new_attempt = uuid4(), uuid4(), uuid4()
    other = parse_v5_command(command).model_copy(
        update={
            "run_id": new_run,
            "command_id": new_command,
            "attempt_id": new_attempt,
            "input_attachment_grants": (),
            "output_write_grant": None,
            "event_callback_url": f"http://127.0.0.1:9120/callbacks/{new_run}/{'A' * 43}",
        }
    )
    other = other.model_copy(
        update={"command_hash": core_chat_command_hash(other)}
    ).model_dump(mode="json", by_alias=True)
    store.register_v5_callback(
        other,
        replace(
            binding,
            run_id=new_run,
            command_id=new_command,
            attempt_id=new_attempt,
            command_hash=other["commandHash"],
        ),
    )
    send(runtime, other, event(other))
    cloud = Cloud(str(new_run))
    assert asyncio.run(drain_once(store, cloud, "synthetic-worker"))
    assert len(cloud.bodies) == 1


def test_gap_to_deleted_local_evidence_preserves_ack_and_isolates(receiver):
    dsn, store, runtime, command, _ = receiver
    send(runtime, command, event(command))
    send(runtime, command, event(command, 2))
    asyncio.run(drain_once(store, Cloud(command["runId"]), "synthetic-worker"))
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "delete from execution_worker.v5_callback_events where seq=1"
        )
    asyncio.run(
        drain_once(store, Cloud(command["runId"], "gap", 0), "synthetic-worker")
    )
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "select uploaded_through,upload_isolation_code from execution_worker.v5_callback_runs"
        ).fetchone() == (1, "source_missing")
