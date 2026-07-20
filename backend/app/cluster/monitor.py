import asyncio
import time
from datetime import datetime, timezone

import httpx

from .contract import ContractLoadError, load_targets
from .models import (
    ClusterSnapshot,
    ClusterSummary,
    InstanceStatus,
    MonitorTarget,
    SourceStatus,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity(target: MonitorTarget) -> dict:
    return {
        "id": target.id,
        "name": target.name,
        "pm2_name": target.pm2_name,
        "port": target.port,
    }


def _checking(target: MonitorTarget) -> InstanceStatus:
    return InstanceStatus(**_identity(target))


async def probe_target(
    client: httpx.AsyncClient, target: MonitorTarget, timeout: float
) -> InstanceStatus:
    started = time.perf_counter()
    try:
        response = await client.get(target.health_url, timeout=timeout)
    except httpx.TimeoutException:
        return InstanceStatus(
            **_identity(target),
            status="offline",
            checked_at=_now_iso(),
            error="timeout",
        )
    except Exception:
        return InstanceStatus(
            **_identity(target),
            status="offline",
            checked_at=_now_iso(),
            error="connection_failed",
        )

    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    base = {
        **_identity(target),
        "latency_ms": latency_ms,
        "checked_at": _now_iso(),
    }
    if response.status_code != 200:
        return InstanceStatus(
            **base, status="degraded", error=f"http_{response.status_code}"
        )
    try:
        payload = response.json()
    except Exception:
        return InstanceStatus(**base, status="degraded", error="invalid_json")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return InstanceStatus(**base, status="degraded", error="status_not_ok")

    uptime = payload.get("uptime")
    uptime_seconds = (
        uptime
        if isinstance(uptime, int) and not isinstance(uptime, bool) and uptime >= 0
        else None
    )
    return InstanceStatus(
        **base,
        status="healthy",
        uptime_seconds=uptime_seconds,
    )


def build_snapshot(
    statuses: list[InstanceStatus], source: SourceStatus
) -> ClusterSnapshot:
    rank = {"offline": 0, "degraded": 1, "checking": 2, "healthy": 3}
    ordered = sorted(statuses, key=lambda item: (rank[item.status], item.port))
    counts = {
        state: sum(item.status == state for item in ordered)
        for state in ("healthy", "degraded", "offline", "checking")
    }
    return ClusterSnapshot(
        summary=ClusterSummary(total=len(ordered), **counts),
        source=source,
        instances=ordered,
    )


class ClusterMonitor:
    def __init__(self, contract_path: str, *, timeout: float = 3.0) -> None:
        self._contract_path = contract_path
        self._timeout = timeout
        self._targets: dict[str, MonitorTarget] = {}
        self._statuses: dict[str, InstanceStatus] = {}
        self._source = SourceStatus(
            healthy=False, checked_at=None, error="not_checked"
        )
        self._reload_targets()

    def _reload_targets(self) -> None:
        checked_at = _now_iso()
        try:
            targets = load_targets(self._contract_path)
        except ContractLoadError as error:
            self._source = SourceStatus(
                healthy=False, checked_at=checked_at, error=str(error)
            )
            return

        previous_targets = self._targets
        previous_statuses = self._statuses
        self._targets = {target.id: target for target in targets}
        self._statuses = {}
        for target in targets:
            if previous_targets.get(target.id) == target and target.id in previous_statuses:
                self._statuses[target.id] = previous_statuses[target.id]
            else:
                self._statuses[target.id] = _checking(target)
        self._source = SourceStatus(healthy=True, checked_at=checked_at, error=None)

    async def poll_once(self, client: httpx.AsyncClient) -> None:
        self._reload_targets()
        targets = list(self._targets.values())
        if not targets:
            return
        results = await asyncio.gather(
            *(probe_target(client, target, self._timeout) for target in targets)
        )
        for result in results:
            if result.id in self._targets:
                self._statuses[result.id] = result

    def snapshot(self) -> ClusterSnapshot:
        return build_snapshot(list(self._statuses.values()), self._source)


def create_cluster_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False)


async def cluster_poll_loop(
    monitor: ClusterMonitor, interval: float
) -> None:
    async with create_cluster_client() as client:
        while True:
            await monitor.poll_once(client)
            await asyncio.sleep(interval)
