from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import signal
from uuid import UUID

import httpx
import pytest

from app.execution_relay.models import RelayEvent, RelayJobPayload, RelayLease
from app.execution_relay.worker import (
    CallbackResult,
    ExponentialBackoff,
    SignedCloudClient,
    WorkerRuntime,
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
    return RelayEvent(
        run_id=RUN_ID,
        seq=seq,
        event_type="run.terminal" if terminal else "turn",
        created_at=NOW + timedelta(seconds=seq),
        payload={"status": terminal} if terminal else {"text": f"event-{seq}"},
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

    def recoverable_runs(self):
        return tuple(
            (run_id, self.agents[run_id], state)
            for run_id, state in self.states.items()
            if state != "leased"
        )


class FakeMap:
    def port_for(self, agent_id):
        assert agent_id == "hr-bot"
        return 9200


class FakeMetaBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.error = error

    def start_run(self, payload, callback_url):
        self.calls.append(("start", payload.run_id, callback_url))
        if self.error:
            raise self.error

    def cancel_run(self, run_id, agent_id):
        self.calls.append(("cancel", run_id, agent_id))


class FakeCloud:
    def __init__(self, leases: list[RelayLease | None] | None = None) -> None:
        self.leases = list(leases or [])
        self.calls: list[tuple[object, ...]] = []
        self.offline: set[str] = set()
        self.cancel_ids: tuple[UUID, ...] = ()

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

    async def heartbeat(self):
        self.calls.append(("heartbeat",))
        if "heartbeat" in self.offline:
            raise OSError("cloud secret detail")
        return self.cancel_ids

    async def aclose(self):
        return None


def _runtime(
    *,
    cloud: FakeCloud | None = None,
    store: FakeStore | None = None,
    metabot: FakeMetaBot | None = None,
    sleep=None,
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
            RUN_ID, token, event.model_dump_json().encode()
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
        _event(1, terminal="completed").model_dump_json().encode(),
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
    body = _event(1).model_dump_json().encode()

    assert await runtime.accept_callback(RUN_ID, token, body) is CallbackResult.ACCEPTED
    assert await runtime.accept_callback(RUN_ID, token, body) is CallbackResult.ACCEPTED
    assert (
        await runtime.accept_callback(RUN_ID, "B" * 43, body)
        is CallbackResult.UNAUTHORIZED
    )
    assert (
        await runtime.accept_callback(
            RUN_ID, token, _event(3).model_dump_json().encode()
        )
        is CallbackResult.CONFLICT
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
    await client.finish(RUN_ID, "completed")
    assert [path for _method, path, _body in signed] == [
        "/api/v1/execution-worker/lease",
        "/api/v1/execution-worker/heartbeat",
        f"/api/v1/execution-worker/runs/{RUN_ID}/dispatched",
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
        f"/api/v1/execution-worker/runs/{RUN_ID}/terminal",
    ]
    assert signed[0] == ("POST", "/api/v1/execution-worker/lease", b"{}")
    assert json.loads(signed[3][2]) == {
        "events": [_event(1).model_dump(mode="json")]
    }
    assert json.loads(signed[4][2]) == {"status": "completed"}
    await client.aclose()
    await http.aclose()


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
    body = _event(1).model_dump_json().encode()

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
