"""Owned integration subprocess: one real transport action, then await test kill."""

import asyncio
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.execution_relay.metabot_client import (
    _APPROVED_AGENT_IDS,
    MetaBotClient,
    MetaBotClientError,
    MetaBotRuntimeMap,
)
from app.execution_relay.transport_v5 import V5TransportAdapter
from app.execution_relay.upload_v5 import drain_once
from app.execution_relay.worker import CloudRelayError, SignedCloudClient, WorkerRuntime
from app.execution_relay.worker_auth import WorkerRequestSigner
from app.execution_relay.worker_store import WorkerStore


async def run(config):
    signer = WorkerRequestSigner(
        config["worker"],
        "worker-v1",
        Ed25519PrivateKey.from_private_bytes(bytes.fromhex(config["key"])),
    )
    cloud = SignedCloudClient(config["cloud"], signer)
    ports = {name: 20000 + i for i, name in enumerate(sorted(_APPROVED_AGENT_IDS))}
    ports["hr-bot"] = config["metabot_port"]
    store = WorkerStore(config["dsn"])
    runtime = WorkerRuntime(
        worker_id=config["worker"],
        cloud=cloud,
        store=store,
        runtime_map=None,
        metabot=MetaBotClient(MetaBotRuntimeMap(ports), Path(config["secret"])),
        callback_port=config["callback_port"],
        enable_v5_callbacks=True,
    )
    # The owned receiver subprocess's shared loopback listener is already live.
    runtime.callback_ready.set()
    try:
        if config["mode"] == "dispatch":
            result = await V5TransportAdapter(runtime).poll_once()
        else:
            result = await drain_once(store, cloud, config["worker"])
        print("acted" if result else "idle", flush=True)
    except (CloudRelayError, MetaBotClientError):
        print("retryable", flush=True)
    finally:
        await cloud.aclose()


if __name__ == "__main__":
    asyncio.run(run(json.loads(sys.stdin.readline())))
    sys.stdin.readline()  # Parent proves real process death, not object replacement.
