from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import inspect
from uuid import UUID, uuid4

import psycopg
import pytest

from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec, SealedContent
from app.execution_relay.models import RelayEvent, RelayJobPayload
from app.execution_relay.repository import (
    ExecutionRelayConflict,
    ExecutionRelayError,
    ExecutionRelayNotFound,
    ExecutionRelayRepository,
    ExecutionRelayWorkerUnavailable,
)
from test_control_plane_migration import control_database


NOW = datetime(2026, 8, 21, 2, 30, tzinfo=timezone.utc)


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=3,
            purpose="platform-content-encryption",
            _keys={2: b"2" * 32, 3: b"3" * 32},
        )
    )


def _payload(agent_id: str = "hr-bot", *, run_id: UUID | None = None):
    return RelayJobPayload(
        run_id=run_id or uuid4(),
        conversation_id=uuid4(),
        trigger_message_id=uuid4(),
        agent_id=agent_id,
        prompt=f"prompt for {agent_id}",
        max_turns=8,
    )


def _event(
    run_id: UUID,
    seq: int,
    *,
    event_type: str = "state",
    created_at: datetime | None = None,
    payload: dict[str, object] | None = None,
) -> RelayEvent:
    return RelayEvent(
        run_id=run_id,
        seq=seq,
        event_type=event_type,
        created_at=created_at or NOW + timedelta(seconds=seq),
        payload=payload or {"state": f"step-{seq}"},
    )


@pytest.fixture()
def relay_database(control_database):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("delete from platform_control.execution_events")
        connection.execute("delete from platform_control.execution_jobs")
        connection.execute("delete from platform_control.execution_worker_keys")
        connection.execute("delete from platform_control.execution_workers")
        connection.execute(
            "insert into platform_control.execution_workers "
            "(worker_id,allowed_agent_ids,status) values "
            "('worker-a',array['hr-bot','fae-bot'],'active'),"
            "('worker-b',array['hr-bot'],'active'),"
            "('worker-revoked',array['hr-bot'],'revoked')"
        )
    yield environment
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("delete from platform_control.execution_events")
        connection.execute("delete from platform_control.execution_jobs")
        connection.execute("delete from platform_control.execution_worker_keys")
        connection.execute("delete from platform_control.execution_workers")


@pytest.fixture()
def repository(relay_database):
    return ExecutionRelayRepository(
        relay_database["urls"]["platform_control_app"],
        content_codec=_codec(),
    )


def _job_row(relay_database, run_id: UUID):
    with psycopg.connect(relay_database["admin"]) as connection:
        return connection.execute(
            "select * from platform_control.execution_jobs where run_id=%s",
            (run_id,),
        ).fetchone()


def test_worker_authorization_uses_the_locking_security_definer_function() -> None:
    source = inspect.getsource(ExecutionRelayRepository._active_worker).lower()

    assert "touch_execution_worker_v27" in source
    assert "update platform_control.execution_workers" not in source
    assert "for share" not in source
    assert "for update" not in source


@pytest.mark.postgres
def test_enqueue_encrypts_payload_with_job_and_run_bound_subject(
    relay_database, repository
) -> None:
    payload = _payload()

    job_id = repository.enqueue(payload)

    row = _job_row(relay_database, payload.run_id)
    assert row[0] == job_id
    assert row[2] == payload.agent_id
    assert row[4] == 3
    assert row[5] == "queued"
    assert payload.prompt.encode() not in bytes(row[3])
    sealed_payload = repository.content_codec.unseal_json(
        f"execution-job:{job_id}:{payload.run_id}",
        SealedContent(bytes(row[3]), row[4]),
    )
    assert RelayJobPayload.model_validate(sealed_payload) == payload


@pytest.mark.postgres
def test_lease_skips_locked_rows_and_intersects_both_agent_allowlists(
    relay_database, repository
) -> None:
    first = _payload("hr-bot")
    second = _payload("fae-bot")
    third = _payload("marketing-gtm-bot")
    first_job = repository.enqueue(first)
    second_job = repository.enqueue(second)
    repository.enqueue(third)

    with psycopg.connect(relay_database["admin"]) as blocker:
        blocker.execute(
            "select job_id from platform_control.execution_jobs "
            "where job_id=%s for update",
            (first_job,),
        )
        lease = repository.lease(
            "worker-a", ("hr-bot", "fae-bot", "marketing-gtm-bot"), 45
        )

    assert lease is not None
    assert lease.job_id == second_job
    assert lease.payload == second
    assert lease.cancel_requested is False
    assert lease.lease_expires_at > datetime.now(timezone.utc)
    assert repository.lease("worker-a", ("marketing-gtm-bot",), 45) is None


@pytest.mark.postgres
def test_concurrent_lease_never_returns_one_job_twice(
    relay_database, repository
) -> None:
    payload = _payload()
    job_id = repository.enqueue(payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = tuple(
            pool.map(
                lambda worker: repository.lease(worker, ("hr-bot",), 45),
                ("worker-a", "worker-b"),
            )
        )

    assert [lease.job_id for lease in leases if lease is not None] == [job_id]


@pytest.mark.postgres
def test_lease_rejects_missing_or_revoked_worker_and_never_reclaims(
    relay_database, repository
) -> None:
    payload = _payload()
    repository.enqueue(payload)
    lease = repository.lease("worker-a", ("hr-bot",), 1)
    assert lease is not None

    with psycopg.connect(relay_database["admin"]) as connection:
        connection.execute(
            "update platform_control.execution_jobs "
            "set lease_expires_at=now()-interval '1 minute' where run_id=%s",
            (payload.run_id,),
        )

    assert repository.lease("worker-b", ("hr-bot",), 45) is None
    for worker_id in ("missing", "worker-revoked"):
        with pytest.raises(
            ExecutionRelayWorkerUnavailable,
            match="^execution relay worker unavailable$",
        ):
            repository.lease(worker_id, ("hr-bot",), 45)


@pytest.mark.postgres
def test_lease_collapses_malformed_decrypted_payload_without_leaking_it(
    relay_database, repository
) -> None:
    job_id = uuid4()
    run_id = uuid4()
    sealed = repository.content_codec.seal_json(
        f"execution-job:{job_id}:{run_id}",
        {"prompt": "protected malformed prompt"},
    )
    with psycopg.connect(relay_database["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_jobs "
            "(job_id,run_id,agent_id,payload_ciphertext,"
            "encryption_key_version,status) values (%s,%s,%s,%s,%s,'queued')",
            (job_id, run_id, "hr-bot", sealed.ciphertext, sealed.key_version),
        )

    with pytest.raises(ExecutionRelayError) as raised:
        repository.lease("worker-a", ("hr-bot",), 45)

    assert str(raised.value) == "execution relay unavailable"
    assert "protected malformed prompt" not in repr(raised.value)
    assert _job_row(relay_database, run_id)[5] == "queued"


@pytest.mark.postgres
def test_mark_dispatched_is_owner_bound_and_replay_safe(
    relay_database, repository
) -> None:
    payload = _payload()
    repository.enqueue(payload)
    repository.lease("worker-a", ("hr-bot",), 45)

    repository.mark_dispatched("worker-a", payload.run_id)
    repository.mark_dispatched("worker-a", payload.run_id)
    assert _job_row(relay_database, payload.run_id)[5] == "dispatched"

    with pytest.raises(ExecutionRelayNotFound):
        repository.mark_dispatched("worker-b", payload.run_id)
    with pytest.raises(ExecutionRelayNotFound):
        repository.mark_dispatched("worker-a", uuid4())


@pytest.mark.postgres
def test_append_events_accepts_exact_duplicates_and_counts_only_new_rows(
    relay_database, repository
) -> None:
    payload = _payload()
    repository.enqueue(payload)
    repository.lease("worker-a", ("hr-bot",), 45)
    repository.mark_dispatched("worker-a", payload.run_id)
    first = _event(payload.run_id, 1)
    second = _event(payload.run_id, 2)

    assert repository.append_events("worker-a", (first,)) == 1
    assert repository.append_events("worker-a", (first, second)) == 1
    assert repository.append_events("worker-a", (first, second)) == 0
    assert _job_row(relay_database, payload.run_id)[5] == "running"

    with psycopg.connect(relay_database["admin"]) as connection:
        rows = connection.execute(
            "select seq,event_type,payload_ciphertext,encryption_key_version "
            "from platform_control.execution_events where run_id=%s order by seq",
            (payload.run_id,),
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [(1, "state"), (2, "state")]
    assert b"step-1" not in bytes(rows[0][2])
    assert repository.content_codec.unseal_json(
        f"execution-event:{payload.run_id}:1",
        SealedContent(bytes(rows[0][2]), rows[0][3]),
    ) == first.payload


@pytest.mark.postgres
@pytest.mark.parametrize("mismatch", ["type", "created_at", "payload"])
def test_append_events_rejects_any_duplicate_logical_mismatch_atomically(
    relay_database, repository, mismatch
) -> None:
    payload = _payload()
    repository.enqueue(payload)
    repository.lease("worker-a", ("hr-bot",), 45)
    first = _event(payload.run_id, 1)
    sealed = repository.content_codec.seal_json(
        f"execution-event:{payload.run_id}:1", first.payload
    )
    with psycopg.connect(relay_database["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_events "
            "(run_id,seq,event_type,payload_ciphertext,encryption_key_version,created_at) "
            "values (%s,%s,%s,%s,%s,%s)",
            (
                first.run_id,
                first.seq,
                first.event_type,
                sealed.ciphertext,
                sealed.key_version,
                first.created_at,
            ),
        )
    changed = first.model_copy(
        update={
            "type": {"event_type": "output"},
            "created_at": {"created_at": first.created_at + timedelta(seconds=1)},
            "payload": {"payload": {"state": "different"}},
        }[mismatch]
    )

    with pytest.raises(
        ExecutionRelayConflict, match="^execution relay conflict$"
    ):
        repository.append_events("worker-a", (changed,))

    assert _job_row(relay_database, payload.run_id)[5] == "leased"


@pytest.mark.postgres
def test_append_events_rejects_malformed_or_noncontiguous_batches_atomically(
    relay_database, repository
) -> None:
    first_payload = _payload()
    second_payload = _payload()
    repository.enqueue(first_payload)
    repository.enqueue(second_payload)
    repository.lease("worker-a", ("hr-bot",), 45)

    assert repository.append_events("missing", ()) == 0
    invalid_batches = (
        (_event(first_payload.run_id, 2),),
        (_event(first_payload.run_id, 1), _event(first_payload.run_id, 3)),
        (_event(first_payload.run_id, 2), _event(first_payload.run_id, 1)),
        (_event(first_payload.run_id, 1), _event(first_payload.run_id, 1)),
        (_event(first_payload.run_id, 1), _event(second_payload.run_id, 2)),
    )
    for batch in invalid_batches:
        with pytest.raises(ExecutionRelayConflict):
            repository.append_events("worker-a", batch)

    with psycopg.connect(relay_database["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.execution_events"
        ).fetchone() == (0,)
    assert _job_row(relay_database, first_payload.run_id)[5] == "leased"


@pytest.mark.postgres
def test_append_events_is_worker_bound_and_terminal_allows_duplicates_only(
    relay_database, repository
) -> None:
    payload = _payload()
    repository.enqueue(payload)
    repository.lease("worker-a", ("hr-bot",), 45)
    event = _event(payload.run_id, 1)

    with pytest.raises(ExecutionRelayNotFound):
        repository.append_events("worker-b", (event,))
    repository.append_events("worker-a", (event,))
    repository.finish("worker-a", payload.run_id, "completed")
    assert repository.append_events("worker-a", (event,)) == 0
    with pytest.raises(ExecutionRelayConflict):
        repository.append_events("worker-a", (_event(payload.run_id, 2),))
    assert _job_row(relay_database, payload.run_id)[5] == "completed"


@pytest.mark.postgres
def test_request_cancel_is_idempotent_for_nonterminal_and_false_otherwise(
    relay_database, repository
) -> None:
    payload = _payload()
    repository.enqueue(payload)

    assert repository.request_cancel(payload.run_id) is True
    assert repository.request_cancel(payload.run_id) is True
    assert _job_row(relay_database, payload.run_id)[8] is True
    assert repository.request_cancel(uuid4()) is False

    repository.lease("worker-a", ("hr-bot",), 45)
    repository.finish("worker-a", payload.run_id, "cancelled")
    assert repository.request_cancel(payload.run_id) is False


@pytest.mark.postgres
def test_finish_enforces_sources_cancel_flag_owner_and_terminal_idempotency(
    relay_database, repository
) -> None:
    payload = _payload()
    repository.enqueue(payload)
    with pytest.raises(ExecutionRelayConflict):
        repository.finish("worker-a", payload.run_id, "interrupted")

    repository.lease("worker-a", ("hr-bot",), 45)
    with pytest.raises(ExecutionRelayConflict):
        repository.finish("worker-a", payload.run_id, "cancelled")
    with pytest.raises(ExecutionRelayConflict):
        repository.finish("worker-a", payload.run_id, "completed")
    with pytest.raises(ExecutionRelayNotFound):
        repository.finish("worker-b", payload.run_id, "interrupted")

    repository.mark_dispatched("worker-a", payload.run_id)
    repository.finish("worker-a", payload.run_id, "completed")
    repository.finish("worker-a", payload.run_id, "completed")
    repository.mark_dispatched("worker-a", payload.run_id)
    with pytest.raises(ExecutionRelayConflict):
        repository.finish("worker-a", payload.run_id, "failed")
    row = _job_row(relay_database, payload.run_id)
    assert row[5] == "completed"
    assert row[11] is not None


@pytest.mark.postgres
def test_heartbeat_touches_active_worker_and_returns_sorted_cancellations(
    relay_database, repository
) -> None:
    payloads = [_payload(), _payload()]
    for payload in payloads:
        repository.enqueue(payload)
        repository.lease("worker-a", ("hr-bot",), 45)
        repository.request_cancel(payload.run_id)

    assert repository.heartbeat("worker-a") == tuple(
        sorted((payload.run_id for payload in payloads), key=str)
    )
    with psycopg.connect(relay_database["admin"]) as connection:
        assert connection.execute(
            "select last_seen_at is not null from platform_control.execution_workers "
            "where worker_id='worker-a'"
        ).fetchone() == (True,)
    for worker_id in ("missing", "worker-revoked"):
        with pytest.raises(ExecutionRelayWorkerUnavailable):
            repository.heartbeat(worker_id)
