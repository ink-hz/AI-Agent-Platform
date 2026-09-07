"""Owned integration child: fixture config arrives on stdin, no external DSN lookup."""

from __future__ import annotations

import asyncio
import json
import sys
from uuid import UUID

from app.execution_relay.worker import WorkerRuntime, callback_server
from app.execution_relay.worker_store import WorkerStore
from app.execution_relay.worker_v5_receiver import TrustedV5CallbackBinding


async def main():
    config = json.loads(sys.stdin.readline())
    store = WorkerStore(config["dsn"])
    runtime = WorkerRuntime(
        worker_id="synthetic-worker",
        cloud=None,
        store=store,
        runtime_map=None,
        metabot=None,
        callback_port=0,
        enable_v5_callbacks=True,
    )
    server = asyncio.create_task(callback_server(runtime))
    await runtime.callback_ready.wait()
    print(json.dumps({"port": runtime.callback_port}), flush=True)
    while line := await asyncio.to_thread(sys.stdin.readline):
        request = json.loads(line)
        command = request["command"]
        binding = TrustedV5CallbackBinding(
            worker_id="synthetic-worker",
            command_id=UUID(command["commandId"]),
            run_id=UUID(command["runId"]),
            attempt_id=UUID(command["attemptId"]),
            command_hash=command["commandHash"],
            launch_lease_epoch=request["launchEpoch"],
            transport_lease_epoch=command["leaseEpoch"],
            callback_origin=request["origin"],
        )
        store.register_v5_callback(command, binding)
        print(json.dumps({"registered": True}), flush=True)
    runtime.stop_event.set()
    await server


if __name__ == "__main__":
    asyncio.run(main())
