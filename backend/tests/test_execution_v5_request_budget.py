"""Actual loop timing and signed client against the existing server limiter.

Only operation payloads/readiness probes are substituted in this timing test;
source integrity and HTTP authentication are covered by the real PG tests.
"""

import asyncio
from itertools import pairwise
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.execution_relay.routes import ExecutionWorkerRequestLimiter
from app.execution_relay.worker import (
    SignedCloudClient,
    V5BudgetDeferred,
    WorkerRuntime,
    heartbeat_loop,
    lease_loop,
)
from app.execution_relay.worker_auth import WorkerRequestSigner
from app.execution_relay.worker_readiness_v5 import V5WorkerService


def test_active_v5_and_legacy_keep_quota_and_drain_over_three_windows(monkeypatch):
    import time

    from app.execution_relay import worker_readiness_v5

    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    limiter = ExecutionWorkerRequestLimiter(clock=lambda: now[0])
    requests, rejections, uploaded = [], [], []
    prefix = "/api/v1/execution-worker"
    run_id = uuid4()

    async def handle(request):
        path = request.url.path
        retry = limiter.check("owned-budget-worker")
        requests.append((now[0], path))
        if retry:
            rejections.append((now[0], path))
            return httpx.Response(429)
        if path == prefix + "/lease":
            return httpx.Response(204)
        if path == prefix + "/heartbeat":
            return httpx.Response(200, json={"stop_requests": []})
        if path.endswith("/event-batches"):
            uploaded.append(len(uploaded) + 1)
        return httpx.Response(200, json={})

    async def handoff(adapter):
        await adapter.runtime.cloud.post_v5_bytes(prefix + "/v5/handoff", b"{}")
        await adapter.runtime.cloud.post_v5_bytes(
            f"{prefix}/v5/runs/{run_id}/acceptance", b"{}"
        )

    async def recovery(adapter):
        await adapter.runtime.cloud.post_v5_bytes(prefix + "/v5/recovery", b"{}")
        await adapter.runtime.cloud.post_v5_bytes(
            f"{prefix}/v5/runs/{run_id}/recovery", b"{}"
        )

    async def upload(_store, cloud, _worker):
        if len(uploaded) < 40:
            await cloud.post_v5_bytes(f"{prefix}/v5/runs/{run_id}/event-batches", b"{}")

    async def sample(_service):
        return {"owned_timing_probe": True}

    monkeypatch.setattr(worker_readiness_v5.V5TransportAdapter, "poll_once", handoff)
    monkeypatch.setattr(worker_readiness_v5.V5RecoveryAdapter, "poll_once", recovery)
    monkeypatch.setattr(worker_readiness_v5, "drain_once", upload)
    monkeypatch.setattr(V5WorkerService, "sample", sample)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            cloud = SignedCloudClient(
                "http://127.0.0.1",
                WorkerRequestSigner(
                    "owned-budget-worker", "worker-v1", Ed25519PrivateKey.generate()
                ),
                client=http,
            )
            runtime = WorkerRuntime(
                worker_id="owned-budget-worker",
                cloud=cloud,
                store=None,
                runtime_map=None,
                metabot=SimpleNamespace(),
                callback_port=12345,
                enable_v5_callbacks=True,
            )
            service = V5WorkerService(runtime)
            service.start()
            legacy = [
                asyncio.create_task(lease_loop(runtime)),
                asyncio.create_task(heartbeat_loop(runtime)),
            ]
            try:
                for tick in range(720):
                    now[0] = tick / 4
                    for _ in range(10):
                        await asyncio.sleep(0)
            finally:
                runtime.stop()
                for task in legacy:
                    task.cancel()
                await asyncio.gather(*legacy, return_exceptions=True)
                await service.close()

    asyncio.run(scenario())
    assert not rejections, f"own polling exhausted quota: {len(rejections)} rejected"
    lease_times = [stamp for stamp, path in requests if path == prefix + "/lease"]
    assert lease_times == list(range(180)), "legacy cadence must remain unchanged"
    readiness = [stamp for stamp, path in requests if path.endswith("/readiness")]
    assert all(later - earlier <= 15 for earlier, later in pairwise(readiness))
    assert uploaded == list(range(1, 41)), "finite source backlog must drain in order"


def test_legacy_burst_defers_v5_before_network_and_recovers_next_window(monkeypatch):
    import time

    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    limiter = ExecutionWorkerRequestLimiter(clock=lambda: now[0])
    seen = []

    async def handle(request):
        assert limiter.check("owned-burst-worker") is None
        seen.append(request.url.path)
        return httpx.Response(
            204 if request.url.path.endswith("/lease") else 200, json=None
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            cloud = SignedCloudClient(
                "http://127.0.0.1",
                WorkerRequestSigner(
                    "owned-burst-worker", "worker-v1", Ed25519PrivateKey.generate()
                ),
                client=http,
            )
            cloud.enable_v5_budget()
            # Legacy remains unchanged even at a burst; v5 cannot truthfully
            # promise live readiness when this identity has exhausted its quota.
            for _ in range(114):
                await cloud.lease()
            assert not cloud.can_upload_v5()
            for path in (
                "readiness",
                "handoff",
                "recovery",
                f"runs/{uuid4()}/event-batches",
            ):
                with pytest.raises(V5BudgetDeferred):
                    await cloud.post_v5_bytes(
                        f"/api/v1/execution-worker/v5/{path}", b"{}"
                    )
            assert len(seen) == 114
            now[0] = 60.01
            assert cloud.can_upload_v5()
            await cloud.post_v5_bytes("/api/v1/execution-worker/v5/readiness", b"{}")
            assert len(seen) == 115

    asyncio.run(scenario())
