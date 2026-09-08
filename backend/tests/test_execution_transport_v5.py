"""Disposable PG plus real signed request verification; no provider invocation."""

import asyncio
import json

import httpx
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_execution_acceptance_v5 import response
from test_execution_worker_v5_receiver import event
from test_hr_direct_command_binding import (
    attempt_repository as attempt_repository,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    bindings as bindings,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    control_database as control_database,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    conversation_database as conversation_database,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    direct_database as direct_database,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    prepared as prepared,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    repository as repository,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    transport_worker as transport_worker,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    worker_conversation as worker_conversation,  # noqa: PLC0414 - pytest fixture re-export
)
from test_hr_direct_command_binding import (
    worker_turn as worker_turn,  # noqa: PLC0414 - pytest fixture re-export
)

from app.execution_relay.contracts_v5 import parse_v5_command
from app.execution_relay.routes import build_execution_relay_router
from app.execution_relay.transport_v5 import V5TransportAdapter
from app.execution_relay.worker import SignedCloudClient
from app.execution_relay.worker_auth import WorkerRequestSigner, WorkerRequestVerifier

pytestmark = pytest.mark.postgres
PREFIX = "/api/v1/execution-worker/v5"


@pytest.fixture()
def signed_api(bindings, direct_database, transport_worker):
    environment, _, _ = direct_database
    key = Ed25519PrivateKey.generate()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_worker_keys(worker_id,key_id,public_key,status) "
            "values(%s,'worker-v1',%s,'active')",
            (
                transport_worker,
                key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            ),
        )
    signer = WorkerRequestSigner(transport_worker, "worker-v1", key)
    app = FastAPI()
    app.include_router(
        build_execution_relay_router(
            bindings.relay,
            WorkerRequestVerifier(environment["urls"]["platform_control_app"]),
            lease_seconds=60,
            max_body_bytes=1_048_576,
            v5_bindings=bindings,
        )
    )
    with TestClient(app) as client:
        yield client, signer, app


def post(client, signer, path, body):
    return client.post(
        path,
        content=body,
        headers={**signer.sign("POST", path, body), "content-type": "application/json"},
    )


def test_signed_handoff_acceptance_and_exact_raw_source(
    signed_api, bindings, prepared, attempt_repository, transport_worker
):
    client, signer, _ = signed_api
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(
            lease, transport_worker, "http://127.0.0.1:19191", connection=connection
        )
    handoff = post(client, signer, PREFIX + "/handoff", b"{}")
    assert handoff.status_code == 200
    command = parse_v5_command(handoff.json()["command"])
    accepted = post(
        client,
        signer,
        f"{PREFIX}/runs/{binding.run_id}/acceptance",
        json.dumps(response(command)).encode(),
    )
    assert accepted.status_code == 200
    raw = json.dumps(
        event(handoff.json()["command"], 1, "cancelled"), indent=2
    ).encode()
    path = f"{PREFIX}/runs/{binding.run_id}/events"
    assert client.post(path, content=raw).status_code == 401
    first = post(client, signer, path, raw)
    assert first.status_code == 200
    assert first.json()["acceptedThrough"] == 1
    assert post(client, signer, path, raw).json()["status"] == "duplicate"


def test_only_exact_v5_paths_enter_signed_middleware_lane():
    from uuid import uuid4

    from app.control_plane.middleware import is_execution_worker_request

    assert is_execution_worker_request("POST", PREFIX + "/handoff")
    assert is_execution_worker_request("POST", f"{PREFIX}/runs/{uuid4()}/events")
    assert not is_execution_worker_request("GET", PREFIX + "/handoff")
    assert not is_execution_worker_request("POST", PREFIX + "/anything")


def test_signed_event_batch_recovers_lost_ack_without_replacing_raw_source(
    signed_api, bindings, prepared, attempt_repository, transport_worker,
):
    from app.execution_relay.content_crypto import SealedContent
    client, signer, _ = signed_api
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(lease, transport_worker, "http://127.0.0.1:19191", connection=connection)
    command = post(client, signer, PREFIX + "/handoff", b"{}").json()["command"]
    bindings.acknowledge_transport(transport_worker, binding.run_id, response(parse_v5_command(command)))
    originals = [json.dumps(event(command, seq), indent=2) for seq in (1, 2)]
    originals.append(json.dumps(event(command, 3, "result"), indent=2))
    path = f"{PREFIX}/runs/{binding.run_id}/event-batches"
    body = json.dumps({"events": originals}, separators=(",", ":")).encode()
    assert client.post(path, content=body).status_code == 401
    first = post(client, signer, path, body)
    assert first.status_code == 200
    assert first.json()["acceptedThrough"] == 3
    assert post(client, signer, path, body).json()["status"] == "duplicate"
    with bindings.relay._connection() as connection:
        rows = connection.execute("select * from platform_control.v5_source_events where run_id=%s order by seq", (binding.run_id,)).fetchall()
    assert [bindings.relay.content_codec.unseal_json(f"execution-v5-source:{binding.run_id}:{row['seq']}", SealedContent(bytes(row["payload_ciphertext"]), row["encryption_key_version"]))["raw"] for row in rows] == originals


def test_raw_cloud_client_signs_original_one_mib_event_bytes(
    signed_api, bindings, prepared, attempt_repository, transport_worker
):
    client, signer, app = signed_api
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(
            lease, transport_worker, "http://127.0.0.1:19191", connection=connection
        )
    handoff = post(client, signer, PREFIX + "/handoff", b"{}").json()
    bindings.acknowledge_transport(
        transport_worker, binding.run_id, response(parse_v5_command(handoff["command"]))
    )
    raw = json.dumps(event(handoff["command"], 1, "cancelled"), indent=2).encode()
    raw += b" " * (1_048_576 - len(raw))

    async def send_raw():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            cloud = SignedCloudClient("http://127.0.0.1", signer, client=http)
            result = await cloud.post_v5_bytes(
                f"{PREFIX}/runs/{binding.run_id}/events", raw
            )
            assert result is not None
            assert result.status_code == 200
            assert result.json()["acceptedThrough"] == 1

    asyncio.run(send_raw())


@pytest.mark.parametrize("fault", ["malformed", "gap_in_batch", "wrong_run", "too_many", "oversize"])
def test_signed_batch_rejects_whole_invalid_envelope_before_first_source(
    signed_api, bindings, prepared, attempt_repository, transport_worker, fault,
):
    from uuid import uuid4
    client, signer, _ = signed_api
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(lease, transport_worker, "http://127.0.0.1:19191", connection=connection)
    command = post(client, signer, PREFIX + "/handoff", b"{}").json()["command"]
    bindings.acknowledge_transport(transport_worker, binding.run_id, response(parse_v5_command(command)))
    first, second = event(command, 1), event(command, 2)
    if fault == "gap_in_batch":
        second["seq"] = 3
    if fault == "wrong_run":
        second["runId"] = str(uuid4())
    originals = [json.dumps(first), "{" if fault == "malformed" else json.dumps(second)]
    if fault == "too_many":
        originals = [json.dumps(event(command, seq)) for seq in range(1, 102)]
    body = json.dumps({"events": originals}).encode()
    if fault == "oversize":
        body += b" " * (1_048_577 - len(body))
    result = post(client, signer, f"{PREFIX}/runs/{binding.run_id}/event-batches", body)
    assert result.status_code == (413 if fault == "oversize" else 400)
    with bindings.relay._connection() as connection:
        assert connection.execute("select count(*) as n from platform_control.v5_source_events where run_id=%s", (binding.run_id,)).fetchone()["n"] == 0


def test_sender_commits_trusted_registration_before_cloud_ack():
    from types import SimpleNamespace
    from uuid import uuid4

    from test_execution_contract_v5 import _v5_command

    command = _v5_command()
    sequence = []

    class Meta:
        def start_v5_run(self, submitted):
            return response(parse_v5_command(submitted))

    class Store:
        def register_v5_callback(self, submitted, binding):
            sequence.append("registration_committed")

    class Cloud:
        async def post_v5_bytes(self, path, body):
            sequence.append("cloud_ack")
            return httpx.Response(200, json={"acknowledged": True})

    from urllib.parse import urlsplit

    origin = urlsplit(command["eventCallbackUrl"])
    runtime = SimpleNamespace(
        worker_id="synthetic-worker",
        store=Store(),
        metabot=Meta(),
        cloud=Cloud(),
        callback_port=origin.port,
        enable_v5_callbacks=True,
    )
    handoff = {
        "version": "hr_transport_handoff_v1",
        "workerId": runtime.worker_id,
        "jobId": str(uuid4()),
        "callbackOrigin": f"http://127.0.0.1:{origin.port}",
        "command": command,
    }
    assert asyncio.run(V5TransportAdapter(runtime).deliver(handoff)) is True
    assert sequence == ["registration_committed", "cloud_ack"]
    runtime.enable_v5_callbacks = False
    with pytest.raises(ValueError, match="v5 handoff invalid"):
        asyncio.run(V5TransportAdapter(runtime).deliver(handoff))
    assert len(sequence) == 2
    runtime.enable_v5_callbacks = True

    class FailedStore:
        def register_v5_callback(self, submitted, binding):
            raise RuntimeError("synthetic local commit failure")

    runtime.store = FailedStore()
    with pytest.raises(RuntimeError, match="synthetic local commit failure"):
        asyncio.run(V5TransportAdapter(runtime).deliver(handoff))
    assert len(sequence) == 2

    class InvalidAckCloud:
        async def post_v5_bytes(self, path, body):
            return httpx.Response(200, json={"acknowledged": 1})

    runtime.store, runtime.cloud = Store(), InvalidAckCloud()
    assert asyncio.run(V5TransportAdapter(runtime).deliver(handoff)) is False


def test_metabot_client_uses_actual_loopback_bearer_and_strict_acceptance(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from test_execution_contract_v5 import _v5_command

    from app.execution_relay.metabot_client import (
        _APPROVED_AGENT_IDS,
        MetaBotClient,
        MetaBotRuntimeMap,
    )

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append((self.path, self.headers.get("Authorization")))
            submitted = parse_v5_command(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps(response(submitted)).encode()
            self.send_response(202)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    secret = tmp_path / "machine-secret"
    tmp_path.chmod(0o700)
    secret.write_text("synthetic-machine-secret")
    secret.chmod(0o600)
    ports = {name: 20000 + i for i, name in enumerate(sorted(_APPROVED_AGENT_IDS))}
    ports["hr-bot"] = server.server_port
    try:
        client = MetaBotClient(MetaBotRuntimeMap(ports), secret)
        result = client.start_v5_run(_v5_command())
        assert result is not None
        assert result["acceptance"]["launchLeaseEpoch"] == 1
        assert seen == [("/api/core-chat/runs", "Bearer synthetic-machine-secret")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_poll_is_opt_in_listener_gated_and_bounded():
    from types import SimpleNamespace

    calls = []

    class Cloud:
        async def post_v5_bytes(self, path, body):
            calls.append(path)
            return httpx.Response(204)

    ready = asyncio.Event()
    runtime = SimpleNamespace(
        enable_v5_callbacks=False, callback_ready=ready, cloud=Cloud()
    )
    adapter = V5TransportAdapter(runtime)

    async def scenario():
        assert await adapter.poll_once() is False
        runtime.enable_v5_callbacks = True
        assert await adapter.poll_once() is False
        ready.set()
        assert await adapter.poll_once() is False
        assert len(calls) == 1
        assert await adapter.poll_once() is False
        assert len(calls) == 1

    asyncio.run(scenario())
