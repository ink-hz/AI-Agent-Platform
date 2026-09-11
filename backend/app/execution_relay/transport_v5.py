"""Opt-in actual handoff consumer; not a queued Attempt dispatcher."""

import asyncio
import json
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

from .acceptance_v5 import parse_v5_acceptance
from .core_contract import parse_core_command
from .worker_v5_receiver import TrustedV5CallbackBinding


class V5TransportAdapter:
    def __init__(self, runtime):
        self.runtime = runtime
        self._next_poll = 0.0

    async def poll_once(self):
        if (
            not self.runtime.enable_v5_callbacks
            or not self.runtime.callback_ready.is_set()
            or monotonic() < self._next_poll
        ):
            return False
        self._next_poll = monotonic() + 1.0
        result = await self.runtime.cloud.post_v5_bytes(
            "/api/v1/execution-worker/v5/handoff", b"{}"
        )
        if result.status_code == 204 and not result.content:
            return False
        if result.status_code != 200:
            return False
        return await self.deliver(result.json())

    async def deliver(self, handoff):
        runtime = self.runtime
        try:
            if (
                not runtime.enable_v5_callbacks
                or type(handoff) is not dict
                or set(handoff)
                != {"version", "workerId", "jobId", "callbackOrigin", "command"}
                or handoff["version"] != "hr_transport_handoff_v1"
                or handoff["workerId"] != runtime.worker_id
                or handoff["callbackOrigin"]
                != f"http://127.0.0.1:{runtime.callback_port}"
                or str(UUID(handoff["jobId"])) != handoff["jobId"]
            ):
                raise ValueError
            command = parse_core_command(handoff["command"])
            url = urlsplit(command.event_callback_url)
            if f"{url.scheme}://{url.netloc}" != handoff["callbackOrigin"]:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError("v5 handoff invalid") from None
        value = await asyncio.to_thread(
            runtime.metabot.start_v5_run, handoff["command"]
        )
        acceptance = parse_v5_acceptance(value, command)
        if acceptance.launch_lease_epoch is None:
            return False
        binding = TrustedV5CallbackBinding(
            runtime.worker_id,
            command.command_id,
            command.run_id,
            command.attempt_id,
            command.command_hash,
            acceptance.launch_lease_epoch,
            acceptance.transport_lease_epoch,
            handoff["callbackOrigin"],
        )
        # The registration transaction commits before cloud ACK can hide handoff.
        await asyncio.to_thread(
            runtime.store.register_v5_callback, handoff["command"], binding
        )
        ack = await runtime.cloud.post_v5_bytes(
            f"/api/v1/execution-worker/v5/runs/{command.run_id}/acceptance",
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            ),
        )
        receipt = ack.json()
        return (
            ack.status_code == 200
            and type(receipt) is dict
            and set(receipt) == {"acknowledged"}
            and receipt["acknowledged"] is True
        )
