from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import signal
import socket
import threading
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest

from app.execution_relay import worker as worker_module
from app.execution_relay.models import RelayEvent, RelayJobPayload, RelayLease
from app.execution_relay.repository import RelayStopRequest
from app.execution_relay.worker_store import WorkerRunRecovery
from app.execution_relay.worker import (
    CallbackResult,
    CloudRelayError,
    ExponentialBackoff,
    SignedCloudClient,
    WorkerRuntime,
    WorkerRuntimeError,
    _owner_private_key,
    callback_server,
    heartbeat_loop,
    lease_loop,
    run_worker,
    upload_loop,
)


NOW = datetime(2026, 8, 21, 4, 0, tzinfo=timezone.utc)
RUN_ID = UUID("00000000-0000-4000-8000-000000000101")
JOB_ID = UUID("00000000-0000-4000-8000-000000000111")


def _lease(*, cancelled: bool = False) -> RelayLease:
    return RelayLease(
        job_id=JOB_ID,
        payload=RelayJobPayload(
            run_id=RUN_ID,
            conversation_id=UUID("00000000-0000-4000-8000-000000000102"),
            trigger_message_id=UUID("00000000-0000-4000-8000-000000000103"),
            agent_id="hr-bot",
            prompt="protected prompt",
            max_turns=24,
        ),
        lease_expires_at=NOW + timedelta(seconds=45),
        cancel_requested=cancelled,
    )


def _event(seq: int, *, terminal: str | None = None) -> RelayEvent:
    event_type = {
        None: "agent.state",
        "completed": "agent.complete",
        "failed": "agent.error",
    }[terminal]
    return RelayEvent(
        run_id=RUN_ID,
        seq=seq,
        event_type=event_type,
        created_at=NOW + timedelta(seconds=seq),
        payload={"text": f"event-{seq}"},
    )


def _callback_body(
    seq: int,
    *,
    terminal: str | None = None,
    run_id: UUID = RUN_ID,
) -> bytes:
    event = _event(seq, terminal=terminal)
    event_type = {
        None: "state",
        "completed": "complete",
        "failed": "error",
    }[terminal]
    return json.dumps(
        {
            "runId": str(run_id),
            "seq": seq,
            "type": event_type,
            "createdAt": event.created_at.isoformat(),
            "bridge": {
                "botName": "hr-bot",
                "executionChatId": (
                    "platform-00000000-0000-4000-8000-000000000102-hr-bot"
                ),
            },
            "payload": event.payload,
        }
    ).encode()


def test_relay_event_requires_timezone_aware_created_at() -> None:
    with pytest.raises(ValueError):
        RelayEvent(
            run_id=RUN_ID,
            seq=1,
            event_type="agent.state",
            created_at=datetime(2026, 8, 21, 4, 0),
            payload={},
        )


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.tokens: dict[UUID, str] = {}
        self.events: dict[UUID, list[RelayEvent]] = {}
        self.delivered: dict[UUID, int] = {}
        self.terminals: dict[UUID, str] = {}
        self.agents: dict[UUID, str] = {}
        self.states: dict[UUID, str] = {}
        self.dispatched: set[UUID] = set()

    def record_lease(self, lease, port, token):
        self.calls.append(("lease", lease.payload.run_id, port))
        self.tokens[lease.payload.run_id] = token
        self.agents[lease.payload.run_id] = lease.payload.agent_id
        self.states[lease.payload.run_id] = "leased"

    def callback_token_matches(self, run_id, token):
        return self.tokens.get(run_id) == token

    def mark_dispatching(self, run_id):
        self.calls.append(("dispatching", run_id))
        self.states[run_id] = "dispatching"

    def mark_dispatched(self, run_id):
        self.calls.append(("dispatched", run_id))
        self.dispatched.add(run_id)
        if self.states[run_id] == "dispatching":
            self.states[run_id] = "dispatched"

    def append_event(self, event):
        rows = self.events.setdefault(event.run_id, [])
        if event.seq <= len(rows):
            if rows[event.seq - 1] == event:
                return False
            raise RuntimeError("worker store conflict")
        if event.seq != len(rows) + 1:
            raise RuntimeError("worker store conflict")
        rows.append(event)
        self.calls.append(("event", event.run_id, event.seq))
        self.states[event.run_id] = "running"
        return True

    def append_terminal_event(self, event, status):
        rows = self.events.setdefault(event.run_id, [])
        if event.seq <= len(rows):
            if rows[event.seq - 1] == event and self.states[event.run_id] == status:
                return False
            raise RuntimeError("worker store conflict")
        if event.seq != len(rows) + 1 or self.states[event.run_id] not in {
            "dispatching",
            "dispatched",
            "running",
        }:
            raise RuntimeError("worker store conflict")
        rows.append(event)
        self.calls.append(("event", event.run_id, event.seq))
        self.calls.append(("local_terminal", event.run_id, status))
        self.terminals[event.run_id] = status
        self.states[event.run_id] = status
        return True

    def contiguous_outbox(self, run_id, limit=100):
        delivered = self.delivered.get(run_id, 0)
        return tuple(self.events.get(run_id, [])[delivered : delivered + limit])

    def mark_delivered(self, run_id, through_seq):
        self.calls.append(("delivered", run_id, through_seq))
        self.delivered[run_id] = through_seq

    def mark_terminal(self, run_id, status):
        self.calls.append(("local_terminal", run_id, status))
        self.terminals[run_id] = status
        self.states[run_id] = status

    def reconcile_forced_terminal(self, run_id, status):
        self.calls.append(("forced_terminal", run_id, status))
        delivered = self.delivered.get(run_id, 0)
        self.events[run_id] = self.events.get(run_id, [])[:delivered]
        self.terminals[run_id] = status
        self.states[run_id] = status

    def has_local_state(self, run_id):
        return (
            run_id in self.states
            or run_id in self.events
            or run_id in self.tokens
        )

    def recoverable_runs(self):
        return tuple(
            WorkerRunRecovery(
                run_id=run_id,
                agent_id=self.agents[run_id],
                state=state,
                dispatched_at=NOW if run_id in self.dispatched else None,
                has_events=bool(self.events.get(run_id)),
            )
            for run_id, state in self.states.items()
            if state != "leased"
        )


class FakeMap:
    def port_for(self, agent_id):
        assert agent_id == "hr-bot"
        return 9200


class FakeMetaBot:
    def __init__(
        self,
        error: Exception | None = None,
        cancel_error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.error = error
        self.cancel_error = cancel_error

    def start_run(self, payload, callback_url):
        self.calls.append(("start", payload.run_id, callback_url))
        if self.error:
            raise self.error

    def cancel_run(self, run_id, agent_id):
        self.calls.append(("cancel", run_id, agent_id))
        if self.cancel_error:
            raise self.cancel_error


class FakeCloud:
    def __init__(self, leases: list[RelayLease | None] | None = None) -> None:
        self.leases = list(leases or [])
        self.calls: list[tuple[object, ...]] = []
        self.offline: set[str] = set()
        self.cancel_ids: tuple[UUID, ...] = ()
        self.closed = False

    async def lease(self):
        self.calls.append(("lease",))
        if "lease" in self.offline:
            raise OSError("cloud secret detail")
        return self.leases.pop(0) if self.leases else None

    async def mark_dispatched(self, run_id):
        self.calls.append(("dispatched", run_id))
        if "dispatched" in self.offline:
            raise OSError("cloud secret detail")

    async def upload_events(self, run_id, events):
        self.calls.append(("events", run_id, tuple(event.seq for event in events)))
        if "events" in self.offline:
            raise OSError("cloud secret detail")

    async def finish(self, run_id, status):
        self.calls.append(("terminal", run_id, status))
        if "terminal" in self.offline:
            raise OSError("cloud secret detail")

    async def acknowledge_stop(self, run_id, status):
        self.calls.append(("stop_ack", run_id, status))
        if "stop_ack" in self.offline:
            raise OSError("cloud secret detail")
        self.cancel_ids = tuple(
            request
            for request in self.cancel_ids
            if not (
                isinstance(request, RelayStopRequest)
                and request.run_id == run_id
                and request.status == status
            )
        )

    async def heartbeat(self):
        self.calls.append(("heartbeat",))
        if "heartbeat" in self.offline:
            raise OSError("cloud secret detail")
        return self.cancel_ids[:100]

    async def aclose(self):
        self.closed = True


def _runtime(
    *,
    cloud: FakeCloud | None = None,
    store: FakeStore | None = None,
    metabot: FakeMetaBot | None = None,
    sleep=None,
    acceptance_hooks=None,
) -> WorkerRuntime:
    return WorkerRuntime(
        worker_id="worker-a",
        cloud=cloud or FakeCloud(),
        store=store or FakeStore(),
        runtime_map=FakeMap(),
        metabot=metabot or FakeMetaBot(),
        callback_port=9120,
        sleep=sleep or (lambda _seconds: asyncio.sleep(0)),
        token_factory=lambda: "A" * 43,
        jitter=lambda _low, _high: 1.0,
        acceptance_hooks=acceptance_hooks,
    )


@pytest.mark.asyncio
async def test_exact_durable_sequence_and_terminal_upload() -> None:
    cloud = FakeCloud([_lease()])
    store = FakeStore()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)

    assert await runtime.lease_once() is True
    token = store.tokens[RUN_ID]
    for event in (_event(1), _event(2, terminal="completed")):
        result = await runtime.accept_callback(
            RUN_ID,
            token,
            _callback_body(
                event.seq,
                terminal="completed" if event.event_type == "agent.complete" else None,
            ),
        )
        assert result is CallbackResult.ACCEPTED
    assert await runtime.upload_once() is True

    assert store.calls == [
        ("lease", RUN_ID, 9200),
        ("dispatching", RUN_ID),
        ("dispatched", RUN_ID),
        ("event", RUN_ID, 1),
        ("event", RUN_ID, 2),
        ("local_terminal", RUN_ID, "completed"),
        ("delivered", RUN_ID, 2),
    ]
    assert [call[0] for call in cloud.calls] == [
        "lease", "dispatched", "events", "terminal"
    ]
    assert len(metabot.calls) == 1


@pytest.mark.asyncio
async def test_acceptance_dispatch_hook_pauses_after_real_post_before_local_dispatch() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Hooks:
        def __init__(self) -> None:
            self.calls = []

        def before_metabot_post(self, run_id):
            self.calls.append(("before_post", run_id))

        async def after_metabot_post(self, run_id):
            self.calls.append(("after_post", run_id))
            entered.set()
            await release.wait()

        async def before_terminal_upload(self, run_id):
            self.calls.append(("before_upload", run_id))

    cloud = FakeCloud([_lease()])
    store = FakeStore()
    metabot = FakeMetaBot()
    hooks = Hooks()
    runtime = _runtime(
        cloud=cloud, store=store, metabot=metabot, acceptance_hooks=hooks
    )

    task = asyncio.create_task(runtime.lease_once())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert len([call for call in metabot.calls if call[0] == "start"]) == 1
    assert ("dispatched", RUN_ID) not in store.calls
    assert ("dispatched", RUN_ID) not in cloud.calls
    release.set()
    assert await asyncio.wait_for(task, timeout=1) is True
    assert hooks.calls[:2] == [("before_post", RUN_ID), ("after_post", RUN_ID)]


@pytest.mark.asyncio
async def test_acceptance_completion_hook_pauses_terminal_outbox_before_cloud_upload() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Hooks:
        def before_metabot_post(self, _run_id):
            pass

        async def after_metabot_post(self, _run_id):
            pass

        async def before_terminal_upload(self, run_id):
            entered.set()
            await release.wait()

    cloud = FakeCloud([_lease()])
    store = FakeStore()
    runtime = _runtime(cloud=cloud, store=store, acceptance_hooks=Hooks())
    await runtime.lease_once()
    assert (
        await runtime.accept_callback(
            RUN_ID,
            store.tokens[RUN_ID],
            _callback_body(1, terminal="completed"),
        )
        is CallbackResult.ACCEPTED
    )

    task = asyncio.create_task(runtime.upload_once())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert store.states[RUN_ID] == "completed"
    assert store.contiguous_outbox(RUN_ID) == (_event(1, terminal="completed"),)
    assert not any(call[0] == "events" for call in cloud.calls)
    release.set()
    assert await asyncio.wait_for(task, timeout=1) is True
    assert ("events", RUN_ID, (1,)) in cloud.calls


@pytest.mark.asyncio
async def test_cloud_offline_before_lease_does_not_touch_metabot() -> None:
    cloud = FakeCloud()
    cloud.offline.add("lease")
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, metabot=metabot)

    assert await runtime.lease_once() is False
    assert metabot.calls == []


@pytest.mark.asyncio
async def test_cloud_offline_after_metabot_completion_preserves_outbox() -> None:
    cloud = FakeCloud([_lease()])
    store = FakeStore()
    runtime = _runtime(cloud=cloud, store=store)
    await runtime.lease_once()
    await runtime.accept_callback(
        RUN_ID,
        store.tokens[RUN_ID],
        _callback_body(1, terminal="completed"),
    )
    cloud.offline.add("events")

    assert await runtime.upload_once() is False
    assert store.delivered == {}
    assert store.contiguous_outbox(RUN_ID) == (_event(1, terminal="completed"),)

    cloud.offline.clear()
    restarted = _runtime(cloud=cloud, store=store)
    await restarted.recover_local_state()
    assert await restarted.upload_once() is True
    assert store.delivered[RUN_ID] == 1


@pytest.mark.asyncio
async def test_duplicate_token_mismatch_gap_and_body_schema_are_rejected() -> None:
    store = FakeStore()
    runtime = _runtime(cloud=FakeCloud([_lease()]), store=store)
    await runtime.lease_once()
    token = store.tokens[RUN_ID]
    body = _callback_body(1)

    assert await runtime.accept_callback(RUN_ID, token, body) is CallbackResult.ACCEPTED
    assert await runtime.accept_callback(RUN_ID, token, body) is CallbackResult.ACCEPTED
    assert (
        await runtime.accept_callback(RUN_ID, "B" * 43, body)
        is CallbackResult.UNAUTHORIZED
    )
    assert (
        await runtime.accept_callback(
            RUN_ID, token, _callback_body(3)
        )
        is CallbackResult.CONFLICT
    )
    assert (
        await runtime.accept_callback(
            RUN_ID, token, _callback_body(2, run_id=UUID(int=0))
        )
        is CallbackResult.INVALID
    )
    assert (
        await runtime.accept_callback(RUN_ID, token, b'{"seq":1}')
        is CallbackResult.INVALID
    )
    assert (
        await runtime.accept_callback(RUN_ID, token, b"x" * 1_048_577)
        is CallbackResult.TOO_LARGE
    )
    assert [call for call in store.calls if call[0] == "event"] == [
        ("event", RUN_ID, 1)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError("401 bearer"), TimeoutError("late")])
async def test_metabot_failure_is_interrupted_and_never_reposted(error) -> None:
    cloud = FakeCloud([_lease(), _lease()])
    metabot = FakeMetaBot(error)
    store = FakeStore()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)

    assert await runtime.lease_once() is False
    assert await runtime.lease_once() is True
    assert len([call for call in metabot.calls if call[0] == "start"]) == 1
    assert store.terminals[RUN_ID] == "interrupted"


@pytest.mark.asyncio
async def test_initial_and_heartbeat_cancellation_never_redispatch() -> None:
    cloud = FakeCloud([_lease(cancelled=True)])
    metabot = FakeMetaBot()
    store = FakeStore()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)

    assert await runtime.lease_once() is True
    assert metabot.calls == []
    assert store.terminals[RUN_ID] == "cancelled"

    cloud2 = FakeCloud([_lease()])
    metabot2 = FakeMetaBot()
    store2 = FakeStore()
    runtime2 = _runtime(cloud=cloud2, store=store2, metabot=metabot2)
    await runtime2.lease_once()
    cloud2.cancel_ids = (RUN_ID,)
    assert await runtime2.heartbeat_once() is True
    assert metabot2.calls[-1] == ("cancel", RUN_ID, "hr-bot")
    assert store2.terminals[RUN_ID] == "cancelled"


@pytest.mark.asyncio
async def test_forced_interrupt_heartbeat_stops_cleans_and_releases_capacity() -> None:
    second_run = UUID("00000000-0000-4000-8000-000000000202")
    second_lease = _lease().model_copy(
        update={"payload": _lease().payload.model_copy(update={"run_id": second_run})}
    )
    cloud = FakeCloud([_lease(), second_lease])
    store = FakeStore()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    assert await runtime.lease_once() is True
    cloud.cancel_ids = (
        RelayStopRequest(run_id=RUN_ID, status="interrupted"),
    )

    assert await runtime.heartbeat_once() is True
    assert store.terminals[RUN_ID] == "interrupted"
    assert metabot.calls[-1] == ("cancel", RUN_ID, "hr-bot")
    assert await runtime.upload_once() is True
    assert RUN_ID not in runtime._runs
    assert await runtime.lease_once() is True
    assert second_run in runtime._runs


@pytest.mark.asyncio
@pytest.mark.parametrize("local_status", ["completed", "failed"])
@pytest.mark.parametrize("cloud_status", ["interrupted", "cancelled"])
async def test_cloud_terminal_overrides_local_terminal_and_discards_outbox(
    local_status, cloud_status
) -> None:
    second_run = UUID("00000000-0000-4000-8000-000000000202")
    second_lease = _lease().model_copy(
        update={"payload": _lease().payload.model_copy(update={"run_id": second_run})}
    )
    cloud = FakeCloud([_lease(), second_lease])
    store = FakeStore()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    assert await runtime.lease_once() is True
    assert (
        await runtime.accept_callback(
            RUN_ID,
            store.tokens[RUN_ID],
            _callback_body(1, terminal=local_status),
        )
        is CallbackResult.ACCEPTED
    )
    assert store.contiguous_outbox(RUN_ID) == (
        _event(1, terminal=local_status),
    )
    cloud.cancel_ids = (
        RelayStopRequest(run_id=RUN_ID, status=cloud_status),
    )

    assert await runtime.heartbeat_once() is True
    assert store.terminals[RUN_ID] == cloud_status
    assert store.contiguous_outbox(RUN_ID) == ()
    assert [call for call in metabot.calls if call[0] == "cancel"] == []
    assert await runtime.upload_once() is True
    assert ("events", RUN_ID, (1,)) not in cloud.calls
    assert ("terminal", RUN_ID, cloud_status) in cloud.calls
    assert RUN_ID not in runtime._runs
    assert await runtime.lease_once() is True
    assert second_run in runtime._runs


@pytest.mark.asyncio
@pytest.mark.parametrize("local_status", ["completed", "failed"])
@pytest.mark.parametrize("cloud_status", ["interrupted", "cancelled"])
@pytest.mark.parametrize(
    "cancel_error",
    [
        RuntimeError("MetaBot 404"),
        RuntimeError("MetaBot 409"),
        OSError("MetaBot unavailable"),
    ],
)
async def test_reliable_local_terminal_never_depends_on_metabot_cancel(
    local_status, cloud_status, cancel_error
) -> None:
    cloud = FakeCloud([_lease()])
    store = FakeStore()
    metabot = FakeMetaBot(cancel_error=cancel_error)
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    assert await runtime.lease_once() is True
    assert await runtime.accept_callback(
        RUN_ID,
        store.tokens[RUN_ID],
        _callback_body(1, terminal=local_status),
    ) is CallbackResult.ACCEPTED
    cloud.cancel_ids = (
        RelayStopRequest(run_id=RUN_ID, status=cloud_status),
    )

    assert await runtime.heartbeat_once() is True
    assert [call for call in metabot.calls if call[0] == "cancel"] == []
    assert store.terminals[RUN_ID] == cloud_status
    assert store.contiguous_outbox(RUN_ID) == ()
    assert await runtime.upload_once() is True
    assert ("terminal", RUN_ID, cloud_status) in cloud.calls
    assert RUN_ID not in runtime._runs


@pytest.mark.asyncio
async def test_active_local_run_requires_successful_metabot_cancel() -> None:
    cloud = FakeCloud([_lease()])
    store = FakeStore()
    metabot = FakeMetaBot(cancel_error=RuntimeError("MetaBot 409"))
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    assert await runtime.lease_once() is True
    cloud.cancel_ids = (
        RelayStopRequest(run_id=RUN_ID, status="cancelled"),
    )

    assert await runtime.heartbeat_once() is False
    assert store.states[RUN_ID] == "dispatched"
    assert RUN_ID in runtime._runs
    assert ("forced_terminal", RUN_ID, "cancelled") not in store.calls


@pytest.mark.asyncio
async def test_more_than_one_hundred_orphan_stops_are_acked_without_starvation() -> None:
    cloud = FakeCloud()
    cloud.cancel_ids = tuple(
        RelayStopRequest(
            run_id=UUID(int=index + 1),
            status="interrupted",
        )
        for index in range(101)
    )
    runtime = _runtime(cloud=cloud)

    assert await runtime.heartbeat_once() is True
    assert len([call for call in cloud.calls if call[0] == "stop_ack"]) == 100
    assert len(cloud.cancel_ids) == 1
    assert await runtime.heartbeat_once() is True
    assert len(runtime._pending_stops) == 0
    assert len([call for call in cloud.calls if call[0] == "stop_ack"]) == 101
    assert cloud.cancel_ids == ()


@pytest.mark.asyncio
async def test_unknown_stop_with_local_state_is_not_orphan_acked() -> None:
    cloud = FakeCloud()
    store = FakeStore()
    store.states[RUN_ID] = "leased"
    cloud.cancel_ids = (
        RelayStopRequest(run_id=RUN_ID, status="cancelled"),
    )
    runtime = _runtime(cloud=cloud, store=store)

    assert await runtime.heartbeat_once() is True
    assert runtime._pending_stops == {RUN_ID: "cancelled"}
    assert not any(call[0] == "stop_ack" for call in cloud.calls)


@pytest.mark.asyncio
async def test_inflight_lease_is_never_mistaken_for_an_orphan_stop() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingStore(FakeStore):
        def record_lease(self, lease, port, token):
            entered.set()
            assert release.wait(timeout=2)
            super().record_lease(lease, port, token)

    cloud = FakeCloud([_lease()])
    cloud.cancel_ids = (
        RelayStopRequest(run_id=RUN_ID, status="cancelled"),
    )
    store = BlockingStore()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)

    lease_task = asyncio.create_task(runtime.lease_once())
    assert await asyncio.to_thread(entered.wait, 1)
    assert await runtime.heartbeat_once() is True
    assert runtime._pending_stops == {RUN_ID: "cancelled"}
    assert not any(call[0] == "stop_ack" for call in cloud.calls)
    release.set()
    assert await asyncio.wait_for(lease_task, timeout=1) is True

    assert metabot.calls == []
    assert store.terminals[RUN_ID] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_observed_while_lease_commits_prevents_metabot_start() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingStore(FakeStore):
        def mark_dispatching(self, run_id):
            super().mark_dispatching(run_id)
            entered.set()
            assert release.wait(timeout=2)

    cloud = FakeCloud([_lease()])
    cloud.cancel_ids = (RUN_ID,)
    store = BlockingStore()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)

    lease_task = asyncio.create_task(runtime.lease_once())
    assert await asyncio.to_thread(entered.wait, 1)
    assert await runtime.heartbeat_once() is True
    release.set()
    assert await asyncio.wait_for(lease_task, timeout=1) is True

    assert metabot.calls == []
    assert store.terminals[RUN_ID] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("start_error", [None, TimeoutError("bounded timeout")])
async def test_run_worker_waits_for_cancelled_blocking_start_and_converges(
    monkeypatch, start_error
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingMetaBot(FakeMetaBot):
        def start_run(self, payload, callback_url):
            self.calls.append(("start", payload.run_id, callback_url))
            entered.set()
            assert release.wait(timeout=2)
            if start_error is not None:
                raise start_error

    cloud = FakeCloud([_lease()])
    store = FakeStore()
    metabot = BlockingMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    runtime.callback_port = 0
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda *_args: None)
    monkeypatch.setattr(loop, "remove_signal_handler", lambda *_args: True)
    runner = asyncio.create_task(run_worker(runtime))
    try:
        await asyncio.wait_for(runtime.callback_ready.wait(), timeout=1)
        assert await asyncio.to_thread(entered.wait, 1)
        runner.cancel()
        await asyncio.sleep(0)
        runner.cancel()
        await asyncio.sleep(0.05)

        assert runner.done() is False
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner, timeout=1)

    assert len([call for call in metabot.calls if call[0] == "start"]) == 1
    expected_cancels = 1 if start_error is None else 0
    assert len([call for call in metabot.calls if call[0] == "cancel"]) == (
        expected_cancels
    )
    assert store.terminals[RUN_ID] == "interrupted"
    if start_error is None:
        assert RUN_ID in store.dispatched
    assert cloud.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal", "expected_status", "expected_cancels"),
    [
        (None, "interrupted", 1),
        ("completed", "completed", 0),
    ],
)
async def test_cancelled_worker_keeps_callback_listener_until_start_converges(
    monkeypatch, terminal, expected_status, expected_cancels
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingMetaBot(FakeMetaBot):
        def start_run(self, payload, callback_url):
            self.calls.append(("start", payload.run_id, callback_url))
            entered.set()
            assert release.wait(timeout=2)

    cloud = FakeCloud([_lease()])
    store = FakeStore()
    metabot = BlockingMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    runtime.callback_port = 0
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda *_args: None)
    monkeypatch.setattr(loop, "remove_signal_handler", lambda *_args: True)
    runner = asyncio.create_task(run_worker(runtime))
    event = _event(1, terminal=terminal)
    try:
        await asyncio.wait_for(runtime.callback_ready.wait(), timeout=1)
        assert await asyncio.to_thread(entered.wait, 1)
        runner.cancel()
        await asyncio.sleep(0)
        runner.cancel()
        await asyncio.sleep(0)
        assert runner.done() is False

        reader, writer = await asyncio.open_connection(
            "127.0.0.1", runtime.callback_port
        )
        body = _callback_body(1, terminal=terminal)
        writer.write(
            f"POST /callbacks/{RUN_ID}/{store.tokens[RUN_ID]} HTTP/1.1\r\n".encode()
            + b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=1)
        writer.close()
        await writer.wait_closed()

        assert response.startswith(b"HTTP/1.1 204")
        assert store.events[RUN_ID] == [event]
        assert runner.done() is False
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner, timeout=1)

    assert store.terminals[RUN_ID] == expected_status
    assert len([call for call in metabot.calls if call[0] == "cancel"]) == (
        expected_cancels
    )
    with pytest.raises(OSError):
        await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", runtime.callback_port),
            timeout=1,
        )


@pytest.mark.asyncio
async def test_interrupt_marks_active_run_and_does_not_repost() -> None:
    cloud = FakeCloud([_lease()])
    metabot = FakeMetaBot()
    store = FakeStore()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    await runtime.lease_once()

    await runtime.interrupt_active()
    await runtime.lease_once()

    assert store.terminals[RUN_ID] == "interrupted"
    assert len([call for call in metabot.calls if call[0] == "start"]) == 1


@pytest.mark.asyncio
async def test_restart_dispatching_interrupts_without_ack_or_repost() -> None:
    store = FakeStore()
    lease = _lease()
    store.record_lease(lease, 9200, "A" * 43)
    store.mark_dispatching(RUN_ID)
    cloud = FakeCloud()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)

    await runtime.recover_local_state()
    assert await runtime.upload_once() is True

    assert metabot.calls == []
    assert ("dispatched", RUN_ID) not in cloud.calls
    assert cloud.calls == [("terminal", RUN_ID, "interrupted")]


@pytest.mark.asyncio
async def test_sigterm_handler_interrupts_uploads_and_stops(monkeypatch) -> None:
    cloud = FakeCloud([_lease()])
    store = FakeStore()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)
    runtime.callback_port = 0
    await runtime.lease_once()
    handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda sig, callback: handlers.setdefault(sig, callback),
    )
    monkeypatch.setattr(
        loop,
        "remove_signal_handler",
        lambda sig: handlers.pop(sig, None) is not None,
    )

    task = asyncio.create_task(run_worker(runtime))
    await asyncio.wait_for(runtime.callback_ready.wait(), timeout=1)
    handlers[signal.SIGTERM]()
    await asyncio.wait_for(task, timeout=1)

    assert store.terminals[RUN_ID] == "interrupted"
    assert ("terminal", RUN_ID, "interrupted") in cloud.calls
    assert len([call for call in metabot.calls if call[0] == "start"]) == 1


def test_backoff_schedule_jitter_and_reset() -> None:
    values = iter((0.8, 1.2, 1.0))
    backoff = ExponentialBackoff(jitter=lambda _low, _high: next(values))
    assert backoff.next_delay() == pytest.approx(0.8)
    assert backoff.next_delay() == pytest.approx(2.4)
    backoff.reset()
    assert backoff.next_delay() == pytest.approx(1.0, abs=0.2)


def test_backoff_saturates_at_full_required_schedule() -> None:
    backoff = ExponentialBackoff(jitter=lambda _low, _high: 1.0)
    assert [backoff.next_delay() for _ in range(8)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        15.0,
        30.0,
        30.0,
        30.0,
    ]


@pytest.mark.asyncio
async def test_signed_cloud_client_signs_exact_raw_body_and_path() -> None:
    signed: list[tuple[str, str, bytes]] = []

    class Signer:
        worker_id = "worker-a"

        def sign(self, method, path, body):
            signed.append((method, path, body))
            return {"X-Orbbec-Worker-Signature": "redacted"}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Orbbec-Worker-Signature"] == "redacted"
        if request.url.path.endswith("/lease"):
            return httpx.Response(204)
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"cancel_requested_run_ids": []})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"accepted": 1, "inserted": 1})
        return httpx.Response(200, json={"status": "accepted"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SignedCloudClient("https://cloud.example", Signer(), client=http)

    assert await client.lease() is None
    assert await client.heartbeat() == ()
    await client.mark_dispatched(RUN_ID)
    await client.upload_events(RUN_ID, (_event(1),))
    await client.acknowledge_stop(RUN_ID, "interrupted")
    await client.finish(RUN_ID, "completed")
    assert [path for _method, path, _body in signed] == [
        "/api/v1/execution-worker/lease",
        "/api/v1/execution-worker/heartbeat",
        f"/api/v1/execution-worker/runs/{RUN_ID}/dispatched",
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
        f"/api/v1/execution-worker/runs/{RUN_ID}/stop-ack",
        f"/api/v1/execution-worker/runs/{RUN_ID}/terminal",
    ]
    assert signed[0] == ("POST", "/api/v1/execution-worker/lease", b"{}")
    assert json.loads(signed[3][2]) == {
        "events": [_event(1).model_dump(mode="json")]
    }
    assert json.loads(signed[4][2]) == {"status": "interrupted"}
    assert json.loads(signed[5][2]) == {"status": "completed"}
    await client.aclose()
    await http.aclose()


@pytest.mark.asyncio
async def test_cloud_heartbeat_parses_typed_stop_requests_strictly() -> None:
    class Signer:
        def sign(self, _method, _path, _body):
            return {"X-Orbbec-Worker-Signature": "redacted"}

    responses = iter(
        (
            {
                "stop_requests": [
                    {"run_id": str(RUN_ID), "status": "interrupted"}
                ]
            },
            {
                "stop_requests": [
                    {"run_id": str(RUN_ID), "status": "invented"}
                ]
            },
        )
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SignedCloudClient("https://cloud.example", Signer(), client=http)

    assert await client.heartbeat() == (
        RelayStopRequest(run_id=RUN_ID, status="interrupted"),
    )
    with pytest.raises(CloudRelayError, match="cloud relay request failed"):
        await client.heartbeat()
    await client.aclose()
    await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "string_max_turns",
        "boolean_max_turns",
        "naive_expiry",
        "top_extra",
        "payload_extra",
        "integer_cancel",
        "integer_run_id",
    ],
)
async def test_cloud_lease_is_strict_and_never_reaches_store_or_metabot(case) -> None:
    response_json = _lease().model_dump(mode="json")
    if case == "string_max_turns":
        response_json["payload"]["max_turns"] = "24"
    elif case == "boolean_max_turns":
        response_json["payload"]["max_turns"] = True
    elif case == "naive_expiry":
        response_json["lease_expires_at"] = "2026-08-21T04:00:45"
    elif case == "top_extra":
        response_json["extra"] = "forbidden"
    elif case == "payload_extra":
        response_json["payload"]["extra"] = "forbidden"
    elif case == "integer_cancel":
        response_json["cancel_requested"] = 1
    else:
        response_json["payload"]["run_id"] = 101

    class Signer:
        def sign(self, _method, _path, _body):
            return {"X-Orbbec-Worker-Signature": "redacted"}

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cloud = SignedCloudClient("https://cloud.example", Signer(), client=http)
    store = FakeStore()
    metabot = FakeMetaBot()
    runtime = _runtime(cloud=cloud, store=store, metabot=metabot)

    assert await runtime.lease_once() is False
    assert store.calls == []
    assert metabot.calls == []
    await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_json",
    [
        {"accepted": True, "inserted": 1},
        {"accepted": 1, "inserted": True},
        {"accepted": 0, "inserted": 0},
    ],
)
async def test_cloud_event_counts_are_strict_integers(response_json) -> None:
    class Signer:
        def sign(self, _method, _path, _body):
            return {"X-Orbbec-Worker-Signature": "redacted"}

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SignedCloudClient("https://cloud.example", Signer(), client=http)
    with pytest.raises(CloudRelayError, match="cloud relay request failed"):
        await client.upload_events(RUN_ID, (_event(1),))
    await http.aclose()


@pytest.mark.asyncio
async def test_invalid_cloud_event_count_never_marks_local_delivery() -> None:
    class Signer:
        def sign(self, _method, _path, _body):
            return {"X-Orbbec-Worker-Signature": "redacted"}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dispatched"):
            return httpx.Response(200, json={"status": "accepted"})
        return httpx.Response(200, json={"accepted": True, "inserted": 1})

    store = FakeStore()
    store.record_lease(_lease(), 9200, "A" * 43)
    store.mark_dispatching(RUN_ID)
    store.mark_dispatched(RUN_ID)
    store.append_event(_event(1))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cloud = SignedCloudClient("https://cloud.example", Signer(), client=http)
    runtime = _runtime(cloud=cloud, store=store)

    await runtime.recover_local_state()
    assert await runtime.upload_once() is False

    assert store.delivered == {}
    await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", [1, True, None, {"hex": str(RUN_ID)}])
async def test_cloud_heartbeat_malformed_ids_are_sanitized(bad_id) -> None:
    class Signer:
        def sign(self, _method, _path, _body):
            return {"X-Orbbec-Worker-Signature": "redacted"}

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"cancel_requested_run_ids": [bad_id]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SignedCloudClient("https://cloud.example", Signer(), client=http)
    with pytest.raises(CloudRelayError, match="cloud relay request failed"):
        await client.heartbeat()
    await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seq", "1"),
        ("seq", True),
        ("seq", 1.0),
        ("type", 7),
        ("type", "completed"),
        ("createdAt", 1_777_777_777),
        ("createdAt", "2026-08-21T04:00:01"),
        ("payload", ["not", "an", "object"]),
    ],
)
async def test_callback_event_fields_do_not_coerce(field, value) -> None:
    store = FakeStore()
    runtime = _runtime(cloud=FakeCloud([_lease()]), store=store)
    await runtime.lease_once()
    event = json.loads(_callback_body(1))
    event[field] = value

    result = await runtime.accept_callback(
        RUN_ID,
        store.tokens[RUN_ID],
        json.dumps(event).encode(),
    )

    assert result is CallbackResult.INVALID
    assert store.events == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unknown_field",
    [
        ("unexpected", True),
        ("bridge.unexpected", True),
    ],
)
async def test_callback_event_rejects_unknown_fields(unknown_field) -> None:
    store = FakeStore()
    runtime = _runtime(cloud=FakeCloud([_lease()]), store=store)
    await runtime.lease_once()
    event = json.loads(_callback_body(1))
    path, value = unknown_field
    if path.startswith("bridge."):
        event["bridge"][path.removeprefix("bridge.")] = value
    else:
        event[path] = value

    result = await runtime.accept_callback(
        RUN_ID,
        store.tokens[RUN_ID],
        json.dumps(event).encode(),
    )

    assert result is CallbackResult.INVALID
    assert store.events == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal", "event_type", "status"),
    [
        ("completed", "agent.complete", "completed"),
        ("failed", "agent.error", "failed"),
    ],
)
async def test_real_core_chat_terminal_callback_is_normalized_and_terminalizes(
    terminal, event_type, status
) -> None:
    store = FakeStore()
    runtime = _runtime(cloud=FakeCloud([_lease()]), store=store)
    await runtime.lease_once()

    result = await runtime.accept_callback(
        RUN_ID,
        store.tokens[RUN_ID],
        _callback_body(1, terminal=terminal),
    )

    assert result is CallbackResult.ACCEPTED
    assert store.events[RUN_ID] == [
        _event(1, terminal=terminal).model_copy(update={"event_type": event_type})
    ]
    assert store.terminals[RUN_ID] == status
    assert store.states[RUN_ID] == status


@pytest.mark.asyncio
async def test_run_worker_closes_cloud_when_recovery_fails() -> None:
    class BrokenRecoveryStore(FakeStore):
        def recoverable_runs(self):
            raise RuntimeError("database-url=protected")

    cloud = FakeCloud()
    runtime = _runtime(cloud=cloud, store=BrokenRecoveryStore())

    with pytest.raises(WorkerRuntimeError, match="worker runtime failed"):
        await run_worker(runtime)

    assert cloud.closed is True


@pytest.mark.asyncio
async def test_run_worker_cleans_handlers_and_cloud_when_callback_bind_fails(
    monkeypatch,
) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    cloud = FakeCloud()
    runtime = _runtime(cloud=cloud)
    runtime.callback_port = listener.getsockname()[1]
    installed: set[signal.Signals] = set()
    removed: set[signal.Signals] = set()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda sig, _callback: installed.add(sig),
    )
    monkeypatch.setattr(
        loop,
        "remove_signal_handler",
        lambda sig: removed.add(sig) is None,
    )
    try:
        with pytest.raises(OSError):
            await run_worker(runtime)
    finally:
        listener.close()

    assert removed == installed == {signal.SIGTERM, signal.SIGINT}
    assert cloud.closed is True


@pytest.mark.asyncio
async def test_run_worker_cancellation_cleans_tasks_handlers_and_cloud(
    monkeypatch,
) -> None:
    cloud = FakeCloud()
    runtime = _runtime(cloud=cloud)
    runtime.callback_port = 0
    installed: set[signal.Signals] = set()
    removed: set[signal.Signals] = set()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda sig, _callback: installed.add(sig),
    )
    monkeypatch.setattr(
        loop,
        "remove_signal_handler",
        lambda sig: removed.add(sig) is None,
    )
    task = asyncio.create_task(run_worker(runtime))
    await asyncio.wait_for(runtime.callback_ready.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert removed == installed == {signal.SIGTERM, signal.SIGINT}
    assert cloud.closed is True


def _private_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def test_owner_private_key_rejects_file_and_parent_symlinks(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    target = secret_dir / "worker.key"
    target.write_bytes(_private_bytes(Ed25519PrivateKey.generate()))
    target.chmod(0o600)
    file_link = secret_dir / "file-link.key"
    file_link.symlink_to(target)
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(secret_dir, target_is_directory=True)

    with pytest.raises(WorkerRuntimeError, match="worker runtime failed"):
        _owner_private_key(file_link)
    with pytest.raises(WorkerRuntimeError, match="worker runtime failed"):
        _owner_private_key(parent_link / "worker.key")


def test_owner_private_key_reads_the_single_opened_inode(
    tmp_path: Path, monkeypatch
) -> None:
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    original = Ed25519PrivateKey.generate()
    replacement = Ed25519PrivateKey.generate()
    path = secret_dir / "worker.key"
    path.write_bytes(_private_bytes(original))
    path.chmod(0o600)
    original_read = Path.read_bytes

    def replace_before_path_read(candidate: Path) -> bytes:
        path.unlink()
        path.write_bytes(_private_bytes(replacement))
        path.chmod(0o600)
        return original_read(candidate)

    monkeypatch.setattr(Path, "read_bytes", replace_before_path_read)
    loaded = _owner_private_key(path)
    loaded_public = loaded.public_key().public_bytes_raw()

    assert loaded_public == original.public_key().public_bytes_raw()


def test_build_runtime_rejects_bad_port_before_owned_cloud_client(monkeypatch) -> None:
    values = {
        "PLATFORM_WORKER_ID": "worker-a",
        "PLATFORM_WORKER_KEY_ID": "worker-v1",
        "PLATFORM_WORKER_PRIVATE_KEY_FILE": "/private/worker.key",
        "PLATFORM_WORKER_DATABASE_URL_FILE": "/private/worker.dsn",
        "PLATFORM_WORKER_CALLBACK_PORT": "70000",
        "PLATFORM_WORKER_CLOUD_URL": "https://cloud.example",
        "PLATFORM_METABOT_RUNTIME_CONTRACT": "/runtime.json",
        "PLATFORM_METABOT_API_SECRET_FILE": "/private/metabot-token",
    }
    cloud_constructions = 0

    class Cloud:
        def __init__(self, *_args):
            nonlocal cloud_constructions
            cloud_constructions += 1

    monkeypatch.setattr(
        worker_module,
        "_required_environment",
        lambda name: values[name],
    )
    monkeypatch.setattr(
        worker_module,
        "_owner_private_key",
        lambda _path: Ed25519PrivateKey.generate(),
    )
    monkeypatch.setattr(
        worker_module.WorkerStore,
        "from_dsn_file",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        worker_module.MetaBotRuntimeMap,
        "from_contract",
        lambda _path: object(),
    )
    monkeypatch.setattr(worker_module, "MetaBotClient", lambda *_args: object())
    monkeypatch.setattr(worker_module, "SignedCloudClient", Cloud)

    with pytest.raises(WorkerRuntimeError, match="worker runtime failed"):
        worker_module.build_runtime_from_environment()

    assert cloud_constructions == 0


@pytest.mark.asyncio
async def test_runtime_logs_never_include_protected_values(caplog) -> None:
    protected = (
        "protected prompt",
        "event-payload-secret",
        "bearer-secret",
        "signature-secret",
        "A" * 43,
        "database-url=protected",
    )
    cloud = FakeCloud()
    cloud.offline.add("lease")
    runtime = _runtime(cloud=cloud)
    with caplog.at_level(logging.WARNING, logger="app.execution_relay.worker"):
        await runtime.lease_once()
        runtime._safe_log("redaction_test", RuntimeError(" ".join(protected)))

    rendered = caplog.text
    assert all(value not in rendered for value in protected)


@pytest.mark.asyncio
async def test_callback_server_binds_loopback_and_commits_before_204() -> None:
    cloud = FakeCloud([_lease()])
    store = FakeStore()
    runtime = _runtime(cloud=cloud, store=store)
    runtime.callback_port = 0
    task = asyncio.create_task(callback_server(runtime))
    await asyncio.wait_for(runtime.callback_ready.wait(), timeout=1)
    await runtime.lease_once()
    token = store.tokens[RUN_ID]
    body = _callback_body(1)

    reader, writer = await asyncio.open_connection("127.0.0.1", runtime.callback_port)
    writer.write(
        f"POST /callbacks/{RUN_ID}/{token} HTTP/1.1\r\n".encode()
        + b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()

    assert response.startswith(b"HTTP/1.1 204")
    assert store.events[RUN_ID] == [_event(1)]
    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_four_public_loops_stop_cleanly() -> None:
    runtime = _runtime()
    runtime.stop()
    await asyncio.gather(
        lease_loop(runtime), upload_loop(runtime), heartbeat_loop(runtime)
    )


@pytest.mark.asyncio
async def test_heartbeat_success_waits_exactly_fifteen_seconds() -> None:
    delays: list[float] = []
    runtime: WorkerRuntime

    async def capture_sleep(seconds: float) -> None:
        delays.append(seconds)
        runtime.stop()

    runtime = _runtime(sleep=capture_sleep)
    await heartbeat_loop(runtime)

    assert delays == [15.0]
