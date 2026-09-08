"""Only invoked by the owned actual MetaBot service test; no live endpoints."""
# ruff: noqa: PLC0414

import asyncio
import os
import socket
import threading
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import uvicorn
from test_execution_readiness_v5 import (
    attempt_repository as attempt_repository,
)
from test_execution_readiness_v5 import (
    bindings as bindings,
)
from test_execution_readiness_v5 import (
    control_database as control_database,
)
from test_execution_readiness_v5 import (
    conversation_database as conversation_database,
)
from test_execution_readiness_v5 import (
    direct_database as direct_database,
)
from test_execution_readiness_v5 import (
    repository as repository,
)
from test_execution_readiness_v5 import (
    signed_api as signed_api,
)
from test_execution_readiness_v5 import (
    transport_worker as transport_worker,
)
from test_execution_readiness_v5 import (
    without_fixture_observation as without_fixture_observation,
)
from test_execution_readiness_v5 import (
    worker_conversation as worker_conversation,
)
from test_execution_readiness_v5 import (
    worker_database as worker_database,
)
from test_execution_readiness_v5 import (
    worker_turn as worker_turn,
)
from test_execution_worker_v5_receiver import MIGRATION

from app.execution_relay.metabot_client import (
    _APPROVED_AGENT_IDS,
    MetaBotClient,
    MetaBotRuntimeMap,
)
from app.execution_relay.worker import SignedCloudClient, WorkerRuntime, callback_server
from app.execution_relay.worker_readiness_v5 import V5WorkerService
from app.execution_relay.worker_store import WorkerStore


def test_actual_service_probe_signed_report_and_original_pg_claim(
    signed_api,
    worker_turn,
    attempt_repository,
    transport_worker,
    worker_database,
    tmp_path,
):
    _, signer, app = signed_api
    with psycopg.connect(worker_database) as connection:
        connection.execute(
            "drop table if exists execution_worker.v5_callback_events,execution_worker.v5_callback_runs,execution_worker.v5_callback_metadata"
        )
        connection.execute(MIGRATION.read_text())
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    secret = directory / "machine"
    secret.write_text("fixture-machine-secret")
    secret.chmod(0o600)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    cloud_origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(
        target=lambda: server.run(sockets=[listener]), daemon=True
    )
    thread.start()

    async def scenario():
        cloud = SignedCloudClient(cloud_origin, signer)
        ports = {name: 20000 + i for i, name in enumerate(sorted(_APPROVED_AGENT_IDS))}
        ports["hr-bot"] = urlsplit(os.environ["P03B2B_METABOT_ORIGIN"]).port
        runtime = WorkerRuntime(
            worker_id=transport_worker,
            cloud=cloud,
            store=WorkerStore(worker_database),
            runtime_map=None,
            metabot=MetaBotClient(MetaBotRuntimeMap(ports), secret),
            callback_port=int(os.environ["P03B2B_CALLBACK_PORT"]),
            enable_v5_callbacks=True,
        )
        callback = asyncio.create_task(callback_server(runtime))
        await asyncio.wait_for(runtime.callback_ready.wait(), 5)
        service = V5WorkerService(runtime)
        service.start()
        try:
            for _ in range(20):
                sample = await service.sample()
                if (
                    sample["uploadReady"]
                    and sample["handoffReady"]
                    and sample["receiverReady"]
                ):
                    break
                await asyncio.sleep(0.1)
            assert sample["service"]["healthy"] is True
            assert (
                sample["receiverReady"]
                and sample["uploadReady"]
                and sample["handoffReady"]
            )
            assert (await service.publish_once()).status_code == 200
            lease = await asyncio.to_thread(attempt_repository.claim_due, uuid4(), 60)
            assert lease is not None and lease.status == "running"
            assert lease.admission["workerId"] == transport_worker
            assert lease.admission["service"]["config"]["toolPolicy"] == "none"
            assert (
                await asyncio.to_thread(attempt_repository.claim_due, uuid4(), 60)
                is None
            )
            with runtime.store._connection() as connection:
                assert (
                    connection.execute(
                        "select count(*) as n from execution_worker.v5_callback_runs"
                    ).fetchone()["n"]
                    == 0
                )
            with attempt_repository.transaction() as connection:
                row = connection.execute(
                    "select transport_run_id from platform_control.turn_attempts where turn_id=%s",
                    (worker_turn.turn.turn_id,),
                ).fetchone()
                assert row["transport_run_id"] is None
        finally:
            await service.close()
            runtime.stop()
            await callback
            await cloud.aclose()

    try:
        asyncio.run(scenario())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
