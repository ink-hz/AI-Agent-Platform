from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.control_plane.middleware import IdentitySecurityMiddleware
from app.execution_relay.models import RelayEvent, RelayJobPayload, RelayLease
from app.execution_relay.repository import (
    ExecutionRelayConflict,
    ExecutionRelayError,
    ExecutionRelayNotFound,
    ExecutionRelayWorkerUnavailable,
)
from app.execution_relay.routes import build_execution_relay_router
from app.execution_relay.worker_auth import (
    WorkerAuthenticationError,
    WorkerIdentity,
)


RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
OTHER_RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
VALID_HEADERS = {"X-Orbbec-Worker-Signature": "valid"}


class FakeVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def verify(self, method, path_with_query, body, headers):
        self.calls.append((method, path_with_query, body))
        if headers.get("x-orbbec-worker-signature") != "valid":
            raise WorkerAuthenticationError()
        return WorkerIdentity("worker-1", "worker-v1", ("hr-bot", "fae-bot"))


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.lease_result = None
        self.heartbeat_result = (RUN_ID,)
        self.inserted = 0
        self.error: Exception | None = None

    def _record(self, *call):
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    def lease(self, worker_id, allowed_agents, lease_seconds):
        self._record("lease", worker_id, allowed_agents, lease_seconds)
        return self.lease_result

    def heartbeat(self, worker_id):
        self._record("heartbeat", worker_id)
        return self.heartbeat_result

    def mark_dispatched(self, worker_id, run_id):
        self._record("dispatched", worker_id, run_id)

    def append_events(self, worker_id, events):
        self._record("events", worker_id, events)
        return self.inserted

    def finish(self, worker_id, run_id, status):
        self._record("terminal", worker_id, run_id, status)


class BrowserAuth:
    route_prefix = "/"
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None

    def __init__(self) -> None:
        self.authenticate_calls = 0

    def authenticate(self, _token):
        self.authenticate_calls += 1
        return (SimpleNamespace(internal_user_id=uuid4()), b"csrf")


def _client(*, limit: int = 120):
    repository = FakeRepository()
    verifier = FakeVerifier()
    auth = BrowserAuth()
    app = FastAPI()
    app.include_router(
        build_execution_relay_router(
            repository,
            verifier,
            lease_seconds=45,
            max_body_bytes=1_048_576,
            requests_per_window=limit,
        )
    )
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        routes=tuple(app.router.routes),
    )
    return TestClient(app), repository, verifier, auth


def _event(run_id: UUID = RUN_ID, *, seq: int = 1) -> dict[str, object]:
    return {
        "run_id": str(run_id),
        "seq": seq,
        "event_type": "message.delta",
        "created_at": "2026-08-21T00:00:00Z",
        "payload": {"delta": "protected"},
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/execution-worker/lease",
        "/api/v1/execution-worker/heartbeat",
        f"/api/v1/execution-worker/runs/{RUN_ID}/dispatched",
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
        f"/api/v1/execution-worker/runs/{RUN_ID}/terminal",
    ],
)
def test_exact_machine_routes_reject_missing_signature_before_repository(path):
    client, repository, verifier, auth = _client()

    response = client.post(path, json={})

    assert response.status_code == 401
    assert response.json() == {"detail": "worker authentication failed"}
    assert response.headers["cache-control"] == "no-store"
    assert repository.calls == []
    assert len(verifier.calls) == 1
    assert auth.authenticate_calls == 0


def test_browser_cookie_and_origin_do_not_authorize_or_gate_machine_route():
    client, repository, _verifier, auth = _client()
    client.cookies.set("session", "valid")

    denied = client.post("/api/v1/execution-worker/heartbeat", json={})
    accepted = client.post(
        "/api/v1/execution-worker/heartbeat",
        json={},
        headers={**VALID_HEADERS, "Origin": "https://evil.example"},
    )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {"cancel_requested_run_ids": [str(RUN_ID)]}
    assert repository.calls == [("heartbeat", "worker-1")]
    assert auth.authenticate_calls == 0


def test_signature_covers_raw_body_before_signed_malformed_json_is_rejected():
    client, repository, verifier, _auth = _client()
    raw = b'{"broken":'

    response = client.post(
        "/api/v1/execution-worker/heartbeat",
        content=raw,
        headers=VALID_HEADERS,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "request validation failed"}
    assert verifier.calls == [
        ("POST", "/api/v1/execution-worker/heartbeat", raw)
    ]
    assert repository.calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/execution-worker/lease", {"extra": True}),
        ("/api/v1/execution-worker/heartbeat", []),
        (f"/api/v1/execution-worker/runs/{RUN_ID}/dispatched", {"status": "ok"}),
        (f"/api/v1/execution-worker/runs/{RUN_ID}/terminal", {"status": "running"}),
        (f"/api/v1/execution-worker/runs/{RUN_ID}/terminal", {"status": "failed", "extra": 1}),
        (f"/api/v1/execution-worker/runs/{RUN_ID}/events", {"events": []}),
        (f"/api/v1/execution-worker/runs/{RUN_ID}/events", {"events": [_event()], "extra": 1}),
        (f"/api/v1/execution-worker/runs/{RUN_ID}/events", {"events": [{**_event(), "extra": 1}]}),
        (f"/api/v1/execution-worker/runs/{RUN_ID}/events", {"events": [_event(OTHER_RUN_ID)]}),
    ],
)
def test_signed_request_bodies_are_exact_and_strict(path, body):
    client, repository, _verifier, _auth = _client()

    response = client.post(path, json=body, headers=VALID_HEADERS)

    assert response.status_code == 422
    assert response.json() == {"detail": "request validation failed"}
    assert repository.calls == []


def test_event_batch_is_limited_to_one_hundred_events():
    client, repository, _verifier, _auth = _client()
    events = [_event(seq=sequence) for sequence in range(1, 102)]

    response = client.post(
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
        json={"events": events},
        headers=VALID_HEADERS,
    )

    assert response.status_code == 422
    assert repository.calls == []


def test_query_parameters_are_signed_then_rejected_without_repository_access():
    client, repository, verifier, _auth = _client()

    response = client.post(
        "/api/v1/execution-worker/heartbeat?cursor=secret",
        json={},
        headers=VALID_HEADERS,
    )

    assert response.status_code == 422
    assert verifier.calls[0][1] == (
        "/api/v1/execution-worker/heartbeat?cursor=secret"
    )
    assert repository.calls == []


def test_oversized_body_returns_413_without_verifier_or_repository_access():
    client, repository, verifier, _auth = _client()

    response = client.post(
        "/api/v1/execution-worker/lease",
        content=b"x" * 1_048_577,
        headers=VALID_HEADERS,
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}
    assert verifier.calls == []
    assert repository.calls == []


def test_valid_worker_lease_uses_only_authenticated_allowed_agents():
    client, repository, _verifier, _auth = _client()
    repository.lease_result = RelayLease(
        job_id=uuid4(),
        payload=RelayJobPayload(
            run_id=RUN_ID,
            conversation_id=uuid4(),
            trigger_message_id=uuid4(),
            agent_id="hr-bot",
            prompt="protected prompt",
            max_turns=12,
        ),
        lease_expires_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        cancel_requested=False,
    )

    response = client.post(
        "/api/v1/execution-worker/lease", json={}, headers=VALID_HEADERS
    )

    assert response.status_code == 200
    assert response.json() == repository.lease_result.model_dump(mode="json")
    assert repository.calls == [
        ("lease", "worker-1", ("hr-bot", "fae-bot"), 45)
    ]


def test_empty_lease_returns_204_with_no_store():
    client, repository, _verifier, _auth = _client()

    response = client.post(
        "/api/v1/execution-worker/lease", json={}, headers=VALID_HEADERS
    )

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert len(repository.calls) == 1


def test_dispatched_events_and_terminal_use_exact_repository_contracts():
    client, repository, _verifier, _auth = _client()
    repository.inserted = 0

    dispatched = client.post(
        f"/api/v1/execution-worker/runs/{RUN_ID}/dispatched",
        json={},
        headers=VALID_HEADERS,
    )
    events = client.post(
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
        json={"events": [_event()]},
        headers=VALID_HEADERS,
    )
    terminal = client.post(
        f"/api/v1/execution-worker/runs/{RUN_ID}/terminal",
        json={"status": "completed"},
        headers=VALID_HEADERS,
    )

    assert dispatched.json() == {"status": "accepted"}
    assert events.json() == {"accepted": 1, "inserted": 0}
    assert terminal.json() == {"status": "accepted"}
    assert repository.calls == [
        ("dispatched", "worker-1", RUN_ID),
        ("events", "worker-1", (RelayEvent.model_validate(_event()),)),
        ("terminal", "worker-1", RUN_ID, "completed"),
    ]


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ExecutionRelayConflict(), 409, "execution relay conflict"),
        (ExecutionRelayNotFound(), 404, "execution relay resource not found"),
        (ExecutionRelayWorkerUnavailable(), 401, "worker authentication failed"),
        (ExecutionRelayError("database leaked secret"), 503, "execution relay unavailable"),
    ],
)
def test_repository_errors_are_sanitized_and_mapped(error, status, detail):
    client, repository, _verifier, _auth = _client()
    repository.error = error

    response = client.post(
        "/api/v1/execution-worker/heartbeat", json={}, headers=VALID_HEADERS
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert "database leaked secret" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_invalid_signatures_do_not_consume_authenticated_worker_quota():
    client, repository, _verifier, _auth = _client(limit=2)

    assert client.post("/api/v1/execution-worker/heartbeat", json={}).status_code == 401
    assert client.post(
        "/api/v1/execution-worker/heartbeat", json={}, headers=VALID_HEADERS
    ).status_code == 200
    assert client.post(
        "/api/v1/execution-worker/lease", json={}, headers=VALID_HEADERS
    ).status_code == 204
    limited = client.post(
        f"/api/v1/execution-worker/runs/{RUN_ID}/dispatched",
        json={},
        headers=VALID_HEADERS,
    )

    assert limited.status_code == 429
    assert limited.json() == {"detail": "worker rate limit exceeded"}
    assert int(limited.headers["retry-after"]) >= 1
    assert len(repository.calls) == 2


def test_failure_bodies_never_reflect_signed_or_event_content():
    client, repository, _verifier, _auth = _client()
    repository.error = ExecutionRelayError("ciphertext nonce-signature protected-delta")
    body = json.dumps({"events": [_event()]}, separators=(",", ":"))

    response = client.post(
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
        content=body,
        headers={"X-Orbbec-Worker-Signature": "valid"},
    )

    assert response.status_code == 503
    assert "protected" not in response.text
    assert "signature" not in response.text
    assert "ciphertext" not in response.text
