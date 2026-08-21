from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from ipaddress import ip_network
import json
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.crypto import IdentityKeyring
from app.control_plane.models import IdentityMode
from app.config import load_config
from app.execution_relay.models import RelayEvent, RelayJobPayload, RelayLease
from app.execution_relay.repository import (
    ExecutionRelayConflict,
    ExecutionRelayError,
    ExecutionRelayNotFound,
    ExecutionRelayWorkerUnavailable,
)
from app.execution_relay.routes import (
    ExecutionWorkerRequestLimiter,
    build_execution_relay_router,
)
from app.execution_relay.worker_auth import (
    WorkerAuthenticationError,
    WorkerIdentity,
)


RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
OTHER_RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
VALID_HEADERS = {"X-Orbbec-Worker-Signature": "valid"}
CONTROL_DSN = (
    "postgresql://platform_control_app:secret@localhost/"
    "agent_platform_control"
)


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
    mode = IdentityMode.PRODUCTION
    route_prefix = "/"
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    public_base_url = "https://agent.example.test"
    app_key = "public-app-key"
    corp_id = "public-corp-id"
    trusted_proxy_networks = ()
    rate_limiter = None

    def __init__(self) -> None:
        self.authenticate_calls = 0

    def authenticate(self, _token):
        self.authenticate_calls += 1
        return (SimpleNamespace(internal_user_id=uuid4()), b"csrf")


def _client(*, limit: int = 120, trusted_proxy: bool = False):
    repository = FakeRepository()
    verifier = FakeVerifier()
    auth = BrowserAuth()
    if trusted_proxy:
        auth.trusted_proxy_networks = (ip_network("127.0.0.1/32"),)
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
    return TestClient(app, client=("127.0.0.1", 50000)), repository, verifier, auth


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


@pytest.mark.parametrize("with_cookie", [False, True])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/execution-worker/lease"),
        ("POST", "/api/v1/execution-worker/future"),
        ("POST", "/api/v1/execution-worker"),
        ("POST", "/api/v1/execution-worker/lease/"),
    ],
)
def test_non_exact_worker_namespace_is_generic_no_store_404(
    method, path, with_cookie
):
    client, repository, verifier, auth = _client()
    if with_cookie:
        client.cookies.set("session", "valid")

    response = client.request(method, path, json={})

    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
    assert response.headers["cache-control"] == "no-store"
    assert repository.calls == []
    assert verifier.calls == []
    assert auth.authenticate_calls == 0


@pytest.mark.parametrize("with_cookie", [False, True])
def test_percent_encoded_worker_alias_is_generic_no_store_404(with_cookie):
    client, repository, verifier, auth = _client()
    if with_cookie:
        client.cookies.set("session", "valid")

    response = client.post(
        "/api/v1/execution-worker/%6cease",
        json={},
        headers=VALID_HEADERS,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
    assert response.headers["cache-control"] == "no-store"
    assert repository.calls == []
    assert verifier.calls == []
    assert auth.authenticate_calls == 0


def test_similar_non_namespace_path_keeps_dingtalk_protection():
    client, repository, verifier, auth = _client()

    response = client.post("/api/v1/execution-workerx", json={})

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}
    assert repository.calls == []
    assert verifier.calls == []
    assert auth.authenticate_calls == 0


def test_worker_boundary_preserves_trusted_proxy_rejection_before_signature():
    client, repository, verifier, _auth = _client(trusted_proxy=True)

    rejected = client.post(
        "/api/v1/execution-worker/heartbeat",
        json={},
        headers=VALID_HEADERS,
    )
    accepted = client.post(
        "/api/v1/execution-worker/heartbeat",
        json={},
        headers={
            **VALID_HEADERS,
            "X-Real-IP": "203.0.113.7",
            "X-Forwarded-Proto": "https",
            "Forwarded": "",
        },
    )

    assert rejected.status_code == 400
    assert rejected.headers["cache-control"] == "no-store"
    assert accepted.status_code == 200
    assert len(verifier.calls) == 1
    assert repository.calls == [("heartbeat", "worker-1")]


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


def test_limiter_clock_sampling_is_serialized_with_bucket_updates():
    older_started = threading.Event()
    newer_sampled = threading.Event()

    def clock() -> float:
        if threading.current_thread().name == "older-request":
            older_started.set()
            newer_sampled.wait(timeout=0.25)
            return 10.0
        newer_sampled.set()
        return 20.0

    limiter = ExecutionWorkerRequestLimiter(limit=3, clock=clock)
    older = threading.Thread(
        name="older-request", target=limiter.check, args=("worker-1",)
    )
    newer = threading.Thread(
        name="newer-request", target=limiter.check, args=("worker-1",)
    )

    older.start()
    assert older_started.wait(timeout=1)
    newer.start()
    older.join(timeout=1)
    newer.join(timeout=1)

    assert not older.is_alive() and not newer.is_alive()
    assert list(limiter._buckets["worker-1"]) == [10.0, 20.0]


def test_verifier_and_repository_calls_run_through_threadpool(monkeypatch):
    calls = []

    async def traced(function, *args, **kwargs):
        calls.append(function.__name__)
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "app.execution_relay.routes.run_in_threadpool", traced
    )
    client, repository, _verifier, _auth = _client()

    response = client.post(
        "/api/v1/execution-worker/heartbeat", json={}, headers=VALID_HEADERS
    )

    assert response.status_code == 200
    assert calls == ["verify", "heartbeat"]
    assert repository.calls == [("heartbeat", "worker-1")]


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


class _ProbeCursor:
    def __init__(self, rows):
        self._rows = iter(rows)
        self.queries = []
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.exited = True
        return None

    def execute(self, query):
        self.queries.append(query)
        return self

    def fetchone(self):
        return next(self._rows)


class _ProbeConnection:
    def __init__(self, rows):
        self.cursor_value = _ProbeCursor(rows)
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.exited = True
        return None

    def cursor(self):
        return self.cursor_value


def _ready_rows(*, schema=True, schema_usage=True, privileges=True):
    schema_value = "present" if schema else None
    return (
        {
            "workers": schema_value,
            "worker_keys": schema_value,
            "jobs": schema_value,
            "events": schema_value,
            "nonces": schema_value,
            "touch_worker": schema_value,
        },
        {"schema_usage": schema_usage, "ready": privileges},
    )


@pytest.mark.parametrize(
    "connect",
    [
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("postgresql://secret@database leaked")
        ),
        lambda *_args, **_kwargs: _ProbeConnection(
            _ready_rows(schema=False)
        ),
        lambda *_args, **_kwargs: _ProbeConnection(
            _ready_rows(privileges=False)
        ),
    ],
)
def test_relay_database_readiness_fails_closed_and_sanitized(connect):
    from app.main import _check_execution_relay_database

    with pytest.raises(RuntimeError) as caught:
        _check_execution_relay_database(CONTROL_DSN, connect=connect)

    assert str(caught.value) == "execution relay database unavailable"
    assert caught.value.__cause__ is None
    assert "secret" not in repr(caught.value)


def test_relay_database_readiness_checks_schema_function_and_privileges():
    from app.main import _check_execution_relay_database

    connection = _ProbeConnection(_ready_rows())

    _check_execution_relay_database(
        CONTROL_DSN, connect=lambda *_args, **_kwargs: connection
    )

    combined = " ".join(connection.cursor_value.queries).lower()
    for name in (
        "execution_workers",
        "execution_worker_keys",
        "execution_jobs",
        "execution_events",
        "execution_worker_nonces",
        "touch_execution_worker_v27",
        "has_schema_privilege",
        "has_table_privilege",
        "has_function_privilege",
    ):
        assert name in combined
    assert connection.cursor_value.exited is True
    assert connection.exited is True


def test_relay_database_readiness_closes_contexts_when_schema_usage_missing():
    from app.main import _check_execution_relay_database

    connection = _ProbeConnection(_ready_rows(schema_usage=False))

    with pytest.raises(RuntimeError, match="execution relay database unavailable"):
        _check_execution_relay_database(
            CONTROL_DSN, connect=lambda *_args, **_kwargs: connection
        )

    assert connection.cursor_value.exited is True
    assert connection.exited is True


def _relay_app_config(tmp_path, *, enabled: bool):
    base = load_config()
    return replace(
        base,
        static_dir=str(tmp_path / "missing-static"),
        execution_relay_enabled=enabled,
        content_encryption_keyring_file="content-keyring" if enabled else "",
        control_plane=replace(
            base.control_plane,
            mode=IdentityMode.PRODUCTION,
            control_database_url_file="control-dsn",
            audit_database_url_file="",
            public_base_url="https://agent.example.test",
            route_prefix="/",
            cookie_name="__Host-platform_session",
        ),
    )


def _create_relay_app(
    tmp_path,
    monkeypatch,
    *,
    enabled: bool,
    probe,
    owned_identity: bool = False,
    identity_prefix: str = "/",
):
    from app.main import create_app

    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    config = _relay_app_config(tmp_path, enabled=enabled)
    dsn = CONTROL_DSN
    monkeypatch.setattr("app.main.load_config", lambda: config)
    monkeypatch.setattr("app.main.read_secret_file", lambda _path: dsn)
    monkeypatch.setattr("app.main._check_execution_relay_database", probe)
    monkeypatch.setattr(
        "app.main.IdentityKeyring.from_file",
        lambda *_args, **_kwargs: IdentityKeyring(
            1, "platform-content-encryption", {1: b"k" * 32}
        ),
    )
    identity_auth = None
    if not owned_identity:
        identity_auth = BrowserAuth()
        identity_auth.route_prefix = identity_prefix
    return create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        identity_auth=identity_auth,
    )


def test_create_app_disabled_does_not_probe_or_mount_relay(tmp_path, monkeypatch):
    def forbidden_probe(_dsn):
        raise AssertionError("disabled relay must not probe")

    app = _create_relay_app(
        tmp_path, monkeypatch, enabled=False, probe=forbidden_probe
    )
    response = TestClient(app).post(
        "/api/v1/execution-worker/heartbeat", json={}, headers=VALID_HEADERS
    )

    assert app.state.execution_relay_repository is None
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("identity_prefix", ["/", "/_preview/dingtalk-r1/"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PROPFIND", "/api/v1/execution-worker"),
        ("CUSTOM", "/api/v1/execution-worker/%6cease"),
    ],
)
def test_relay_disabled_reserves_every_method_before_dingtalk_session(
    tmp_path, monkeypatch, identity_prefix, method, path
):
    def forbidden_probe(_dsn):
        raise AssertionError("disabled relay must not probe")

    app = _create_relay_app(
        tmp_path,
        monkeypatch,
        enabled=False,
        probe=forbidden_probe,
        identity_prefix=identity_prefix,
    )
    client = TestClient(app)
    client.cookies.set("session", "valid")

    response = client.request(method, path, json={})

    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
    assert response.headers["cache-control"] == "no-store"
    assert app.state.identity_auth.authenticate_calls == 0


def test_create_app_enabled_probes_mounts_and_exposes_repository(
    tmp_path, monkeypatch
):
    probes = []
    app = _create_relay_app(
        tmp_path,
        monkeypatch,
        enabled=True,
        probe=lambda dsn: probes.append(dsn),
    )
    response = TestClient(app).post(
        "/api/v1/execution-worker/heartbeat", json={}
    )

    assert probes == [CONTROL_DSN]
    assert app.state.execution_relay_repository is not None
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_create_app_enabled_aborts_before_mount_when_probe_fails(
    tmp_path, monkeypatch
):
    def unavailable(_dsn):
        raise RuntimeError("execution relay database unavailable")

    with pytest.raises(RuntimeError, match="execution relay database unavailable"):
        _create_relay_app(
            tmp_path, monkeypatch, enabled=True, probe=unavailable
        )


def test_relay_probe_failure_precedes_owned_identity_client_build(
    tmp_path, monkeypatch
):
    identity_builds = []

    def build_identity(config):
        identity_builds.append(config)
        return BrowserAuth()

    def unavailable(_dsn):
        raise RuntimeError("execution relay database unavailable")

    monkeypatch.setattr("app.main.build_identity_auth", build_identity)

    with pytest.raises(RuntimeError, match="execution relay database unavailable"):
        _create_relay_app(
            tmp_path,
            monkeypatch,
            enabled=True,
            probe=unavailable,
            owned_identity=True,
        )

    assert identity_builds == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/execution-worker"),
        ("POST", "/api/v1/execution-worker"),
        ("PROPFIND", "/api/v1/execution-worker"),
        ("GET", "/api/v1/execution-worker/lease"),
        ("POST", "/api/v1/execution-worker/heartbeat"),
        ("CUSTOM", "/api/v1/execution-worker/%6cease"),
        ("GET", "/api/v1/execution-worker/future"),
        ("POST", "/api/v1/execution-worker/future"),
    ],
)
def test_identity_and_relay_disabled_reserve_worker_namespace_before_spa(
    tmp_path, monkeypatch, method, path
):
    from app.main import create_app

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        "<main>SPA MUST NOT SERVE WORKER PATHS</main>", encoding="utf-8"
    )
    registry = tmp_path / "disabled-registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "disabled-contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    config = replace(
        load_config(),
        static_dir=str(static),
        execution_relay_enabled=False,
        content_encryption_keyring_file="",
    )
    monkeypatch.setattr("app.main.load_config", lambda: config)

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
    )
    response = TestClient(app).request(method, path, json={})

    assert app.state.identity_auth is None
    assert app.state.execution_relay_repository is None
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
    assert response.headers["cache-control"] == "no-store"
    assert "SPA MUST NOT SERVE" not in response.text


def test_identity_and_relay_disabled_do_not_reserve_adjacent_spa_path(
    tmp_path, monkeypatch
):
    from app.main import create_app

    static = tmp_path / "adjacent-static"
    static.mkdir()
    (static / "index.html").write_text(
        "<main>ADJACENT SPA PATH</main>", encoding="utf-8"
    )
    registry = tmp_path / "adjacent-registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "adjacent-contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    config = replace(load_config(), static_dir=str(static))
    monkeypatch.setattr("app.main.load_config", lambda: config)

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
    )
    response = TestClient(app).get("/api/v1/execution-workerx")

    assert response.status_code == 200
    assert "ADJACENT SPA PATH" in response.text
