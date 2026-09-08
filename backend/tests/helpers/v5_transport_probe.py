"""Explicitly invoked by the actual MetaBot HTTP fixture, never a live-service test."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
import uvicorn
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from test_execution_transport_v5 import (
    attempt_repository as attempt_repository,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    bindings as bindings,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    control_database as control_database,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    conversation_database as conversation_database,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    direct_database as direct_database,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    prepared as prepared,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    repository as repository,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    signed_api as signed_api,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    transport_worker as transport_worker,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    worker_conversation as worker_conversation,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_transport_v5 import (
    worker_turn as worker_turn,  # noqa: PLC0414 - pytest fixture re-export
)
from test_execution_worker_v5_receiver import MIGRATION

from app.execution_relay.metabot_client import (
    _APPROVED_AGENT_IDS,
    MetaBotClient,
    MetaBotRuntimeMap,
)
from app.execution_relay.transport_v5 import V5TransportAdapter
from app.execution_relay.worker import SignedCloudClient, WorkerRuntime, callback_server
from app.execution_relay.worker_store import WorkerStore
from tests.test_execution_worker_store import (
    worker_database as worker_database,  # noqa: PLC0414 - pytest fixture re-export
)


def test_actual_cross_repo_signed_bearer_socket_and_process_recovery(
    signed_api,
    bindings,
    prepared,
    attempt_repository,
    transport_worker,
    worker_database,
    tmp_path,
    direct_database,
):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    _, signer, app = signed_api
    lease, binding = prepared
    callback_port = int(os.environ["P03B2_CALLBACK_PORT"])
    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "drop table if exists execution_worker.v5_callback_events, execution_worker.v5_callback_runs, execution_worker.v5_callback_metadata"
        )
        connection.execute(MIGRATION.read_text())
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(
            lease,
            transport_worker,
            f"http://127.0.0.1:{callback_port}",
            connection=connection,
        )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(
        target=lambda: server.run(sockets=[listener]), daemon=True
    )
    thread.start()
    upstream = f"http://127.0.0.1:{listener.getsockname()[1]}"
    lost_upload = threading.Event()

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            with httpx.Client(trust_env=False, timeout=10) as client:
                result = client.post(
                    upstream + self.path,
                    content=body,
                    headers={
                        k: v for k, v in self.headers.items() if k.lower() != "host"
                    },
                )
            if self.path.endswith("/events") and not lost_upload.is_set():
                lost_upload.set()
                self.send_response(result.status_code)
                self.send_header("Content-Length", str(len(result.content)))
                self.end_headers()
                self.wfile.write(result.content[:12])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(result.status_code)
            self.send_header("Content-Length", str(len(result.content)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result.content)

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    cloud_url = f"http://127.0.0.1:{proxy.server_port}"
    meta_origin = os.environ["P03B2_METABOT_PROXY"]
    tmp_path.chmod(0o700)
    secret = tmp_path / "machine-secret"
    secret.write_text("fixture-machine-secret")
    secret.chmod(0o600)
    config = {
        "worker": transport_worker,
        "key": signer._private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        ).hex(),
        "dsn": worker_database,
        "cloud": cloud_url,
        "metabot_port": urlsplit(meta_origin).port,
        "secret": str(secret),
        "callback_port": callback_port,
    }
    children = []

    async def child(mode):
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "tests/helpers/v5_transport_child.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append(process)
        process.stdin.write(json.dumps({**config, "mode": mode}) + "\n")
        process.stdin.flush()
        line = await asyncio.wait_for(
            asyncio.to_thread(process.stdout.readline), timeout=20
        )
        assert line.strip() in {"acted", "retryable"}
        process.kill()
        await asyncio.to_thread(process.wait, 5)
        assert process.returncode < 0

    async def scenario():
        cloud = SignedCloudClient(cloud_url, signer)
        store = WorkerStore(worker_database)
        ports = {name: 20000 + i for i, name in enumerate(sorted(_APPROVED_AGENT_IDS))}
        ports["hr-bot"] = urlsplit(meta_origin).port
        runtime = WorkerRuntime(
            worker_id=transport_worker,
            cloud=cloud,
            store=store,
            runtime_map=None,
            metabot=MetaBotClient(MetaBotRuntimeMap(ports), secret),
            callback_port=callback_port,
            enable_v5_callbacks=True,
        )
        callback = asyncio.create_task(callback_server(runtime))
        await asyncio.wait_for(runtime.callback_ready.wait(), 5)
        try:
            await child(
                "dispatch"
            )  # Actual accepted response is destroyed by MetaBot proxy.
            with store._connection() as connection:
                assert (
                    connection.execute(
                        "select count(*) as n from execution_worker.v5_callback_runs"
                    ).fetchone()["n"]
                    == 0
                )
            handoff = (
                await cloud.post_v5_bytes("/api/v1/execution-worker/v5/handoff", b"{}")
            ).json()
            adapter = V5TransportAdapter(runtime)
            assert await asyncio.gather(
                adapter.deliver(handoff), adapter.deliver(handoff)
            ) == [True, True]
            assert (
                await cloud.post_v5_bytes("/api/v1/execution-worker/v5/handoff", b"{}")
            ).status_code == 204
            environment, _, _ = direct_database
            with psycopg.connect(environment["admin"]) as connection:
                connection.execute(
                    "update platform_control.turn_attempts set lease_expires_at=now()-interval '1 second' where attempt_id=%s",
                    (lease.attempt_id,),
                )
            current = attempt_repository.claim_due(uuid4(), 60)
            assert current.lease_epoch == 2
            with attempt_repository.transaction() as connection:
                bindings.authorize_transport(
                    current,
                    transport_worker,
                    f"http://127.0.0.1:{callback_port}",
                    connection=connection,
                )
            rotated = (
                await cloud.post_v5_bytes("/api/v1/execution-worker/v5/handoff", b"{}")
            ).json()
            assert (
                rotated["command"]["eventCallbackUrl"]
                == handoff["command"]["eventCallbackUrl"]
            )
            assert await adapter.deliver(rotated)
            with store._connection() as connection:
                assert connection.execute(
                    "select launch_lease_epoch,transport_lease_epoch from execution_worker.v5_callback_runs"
                ).fetchone() == {"launch_lease_epoch": 1, "transport_lease_epoch": 2}
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(20):
                    result = await client.post(meta_origin + "/probe/flush")
                    if result.json()["cursor"] == 1:
                        break
                    await asyncio.sleep(0.1)
                assert result.json()["cursor"] == 1
            await child("upload")  # Cloud commits source, then proxy destroys response.
            assert lost_upload.is_set()
            with store._connection() as connection:
                row = connection.execute(
                    "select uploaded_through,upload_failures from execution_worker.v5_callback_runs"
                ).fetchone()
                assert row == {"uploaded_through": 0, "upload_failures": 1}
            await asyncio.sleep(0.3)
            await child(
                "upload"
            )  # Real new process replays durable source, receives duplicate.
            with store._connection() as connection:
                assert (
                    connection.execute(
                        "select uploaded_through from execution_worker.v5_callback_runs"
                    ).fetchone()["uploaded_through"]
                    == 1
                )
            with attempt_repository.transaction() as connection:
                assert (
                    connection.execute(
                        "select count(*) as n from platform_control.v5_source_events where run_id=%s",
                        (binding.run_id,),
                    ).fetchone()["n"]
                    == 1
                )
        finally:
            runtime.stop_event.set()
            await callback
            await cloud.aclose()

    try:
        asyncio.run(scenario())
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        proxy.shutdown()
        proxy.server_close()
        proxy_thread.join()
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
