"""Explicit machine-side composition on the existing Relay; no cloud executor."""

import asyncio
import json
import os
import re
from datetime import datetime, timezone

import httpx
import psycopg

from .recovery_v5 import V5RecoveryAdapter
from .transport_v5 import V5TransportAdapter
from .upload_v5 import drain_once
from .worker import SignedCloudClient
from .worker_v5_receiver import preflight

_FAILURES = (OSError, ValueError, RuntimeError, httpx.HTTPError, psycopg.Error)


def probe_store(store):
    with store._connection() as connection:
        connection.execute("set local statement_timeout='1000ms'")
        connection.execute("set transaction read only")
        preflight(connection)
        if os.environ.get('PLATFORM_WORKER_HR_V6_ENABLED')=='1':
            connection.execute('select core_contract_version,business_grant_id,business_token_hash from execution_worker.v5_callback_runs limit 0')
        row = connection.execute(
            "select bool_and(has_table_privilege(current_user,'execution_worker.'||name,privilege)) as allowed "
            "from unnest(array['v5_callback_runs']) name "
            "cross join unnest(array['SELECT','INSERT','UPDATE']) privilege"
        ).fetchone()
        if row["allowed"] is not True:
            raise ValueError("v5 receiver unavailable")
        source = connection.execute(
            "select has_table_privilege(current_user,'execution_worker.v5_callback_events','SELECT') and has_table_privilege(current_user,'execution_worker.v5_callback_events','INSERT') as allowed"
        ).fetchone()
        if source["allowed"] is not True:
            raise ValueError("v5 receiver unavailable")
        connection.execute(
            "select run_id,token_hash,accepted_through,uploaded_through,upload_next_seq,upload_revision,upload_next_at,upload_isolation_code from execution_worker.v5_callback_runs limit 0"
        )
        connection.execute(
            "select run_id,seq,event_json from execution_worker.v5_callback_events limit 0"
        )
    return True


async def challenge(runtime, headers, body, writer):
    # Non-event loopback request. It cannot register a run or manufacture evidence.
    authorized = (
        runtime.enable_v5_callbacks
        and runtime.metabot is not None
        and runtime.metabot.authenticates_v5_machine(headers.get("authorization", ""))
    )
    nonce = headers.get("x-v5-challenge", "")
    status, payload = 401, b""
    if authorized:
        if body != b"{}" or not re.fullmatch(r"[0-9a-f]{32}", nonce):
            status = 400
        elif runtime.shutdown_event.is_set() or not runtime.callback_ready.is_set():
            status = 503
        else:
            status, payload = (
                200,
                json.dumps({"challenge": nonce}, separators=(",", ":")).encode(),
            )
    writer.write(
        f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".encode()
        + payload
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


class V5WorkerService:
    def __init__(self, runtime):
        if not runtime.enable_v5_callbacks or runtime.metabot is None:
            raise ValueError("v5 service unavailable")
        self.runtime = runtime
        self.tasks = {}
        self._closed = False
        self._last = {}

    def start(self):
        if self.tasks or self._closed:
            raise ValueError("v5 service unavailable")
        if isinstance(self.runtime.cloud, SignedCloudClient):
            self.runtime.cloud.enable_v5_budget()
        adapter = V5TransportAdapter(self.runtime)
        self.tasks["handoff"] = asyncio.create_task(
            self._loop("handoff", adapter.poll_once)
        )
        self.tasks["upload"] = asyncio.create_task(
            self._loop(
                "upload",
                lambda: drain_once(
                    self.runtime.store, self.runtime.cloud, self.runtime.worker_id
                ),
            )
        )
        self.tasks["readiness"] = asyncio.create_task(self._reports())
        self.tasks["recovery"] = asyncio.create_task(self._loop("recovery", V5RecoveryAdapter(self.runtime).poll_once))

    async def _loop(self, name, operation):
        while not self._closed and not self.runtime.shutdown_event.is_set():
            try:
                async with asyncio.timeout(12):
                    await operation()
                self._last[name] = asyncio.get_running_loop().time()
            except _FAILURES:
                self._last.pop(name, None)
            await self.runtime.pause(1 if name == "upload" else 5 if name == "recovery" else 10)

    async def sample(self):
        runtime, metabot = self.runtime, self.runtime.metabot
        origin = f"http://127.0.0.1:{runtime.callback_port}"
        value = {
            "version": "hr_v6_readiness_v1" if os.environ.get("PLATFORM_WORKER_HR_V6_ENABLED")=="1" else "hr_v5_readiness_v1",
            "sampledAt": datetime.now(timezone.utc).isoformat(),
            "callbackOrigin": origin,
            "service": None,
            "receiverReady": False,
            "uploadReady": False,
            "handoffReady": False,
            "reason": "unavailable",
        }
        if self._closed or runtime.shutdown_event.is_set():
            value["reason"] = "stopping"
            return value
        try:
            async with asyncio.timeout(3):
                service, receiver, storage = await asyncio.gather(
                    metabot.probe_v5_service(),
                    metabot.probe_v5_receiver(origin),
                    asyncio.to_thread(probe_store, runtime.store),
                )
            value.update(service=service, receiverReady=receiver and storage)
            if service["callbackOrigin"] != origin:
                raise ValueError("v5 readiness unavailable")
            now = asyncio.get_running_loop().time()
            for name, key in (("upload", "uploadReady"), ("handoff", "handoffReady")):
                task = self.tasks.get(name)
                value[key] = bool(
                    task
                    and not task.done()
                    and now - self._last.get(name, float("-inf")) < 15
                )
            value["reason"] = (
                "ready"
                if receiver
                and storage
                and value["uploadReady"]
                and value["handoffReady"]
                else "unavailable"
            )
        except _FAILURES:
            value.update(
                service=None, receiverReady=False, uploadReady=False, handoffReady=False
            )
        if self._closed or runtime.shutdown_event.is_set():
            value.update(
                service=None,
                receiverReady=False,
                uploadReady=False,
                handoffReady=False,
                reason="stopping",
            )
        return value

    async def publish_once(self):
        value = await self.sample()
        return await self.runtime.cloud.post_v5_bytes(
            "/api/v1/execution-worker/v5/readiness",
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(),
        )

    async def _reports(self):
        while not self._closed and not self.runtime.shutdown_event.is_set():
            next_sample = asyncio.get_running_loop().time() + 10
            try:
                await self.publish_once()
            except _FAILURES:
                self.runtime.logger.warning("v5 readiness publication unavailable")
            await self.runtime.pause(max(0, next_sample - asyncio.get_running_loop().time()))

    async def close(self):
        self._closed = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        try:
            async with asyncio.timeout(3):
                await self.publish_once()
        except _FAILURES:
            self.runtime.logger.warning("v5 readiness shutdown publication unavailable")
