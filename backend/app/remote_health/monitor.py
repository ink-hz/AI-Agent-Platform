from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from .models import RemoteAgentStatus, RemoteHealthSnapshot
from .probes import Runner, probe_fae, probe_remote_ops, run_command


def _uptime(now: datetime, started_at: datetime | None) -> int | None:
    if started_at is None:
        return None
    return max(0, int((now - started_at).total_seconds()))


class RemoteHealthMonitor:
    def __init__(
        self,
        ssh_host: str,
        ssh_key_path: str,
        *,
        fae_health_url: str = "http://47.106.112.69/health",
        timeout: float = 4,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._ssh_host = ssh_host
        self._ssh_key_path = ssh_key_path
        self._fae_health_url = fae_health_url
        self._timeout = timeout
        self._now = now
        self._snapshot = RemoteHealthSnapshot(
            healthy=False,
            error="not_checked",
            agents=[
                RemoteAgentStatus(id="ai-fae-agent", name="AI FAE Agent"),
                RemoteAgentStatus(id="ai-admin-agent", name="AI ADMIN Agent"),
            ],
        )

    async def poll_once(
        self,
        client: httpx.AsyncClient,
        *,
        runner: Runner = run_command,
    ) -> None:
        checked_at = self._now()
        previous = {agent.id: agent for agent in self._snapshot.agents}
        fae, ops_result = await asyncio.gather(
            probe_fae(
                client,
                self._fae_health_url,
                self._timeout,
                now=self._now,
            ),
            probe_remote_ops(
                self._ssh_host,
                self._ssh_key_path,
                runner=runner,
                timeout=max(12, self._timeout),
            ),
        )
        ops, ops_error = ops_result
        if ops is not None:
            fae.uptime_seconds = _uptime(checked_at, ops.fae_started_at)
            units_active = all(value == "active" for value in ops.units.values())
            admin_ok = ops.admin_health.get("status") == "ok"
            admin = RemoteAgentStatus(
                id="ai-admin-agent",
                name="AI ADMIN Agent",
                status="healthy" if admin_ok and units_active else "degraded",
                uptime_seconds=_uptime(checked_at, ops.admin_started_at),
                checked_at=checked_at,
                error=None if admin_ok and units_active else "service_inactive",
                details={"health": ops.admin_health, "units": ops.units},
            )
        else:
            old_admin = previous["ai-admin-agent"]
            admin = RemoteAgentStatus(
                id=old_admin.id,
                name=old_admin.name,
                status="unknown",
                uptime_seconds=old_admin.uptime_seconds,
                checked_at=checked_at,
                error=ops_error,
                details=old_admin.details,
            )
            if fae.uptime_seconds is None:
                fae.uptime_seconds = previous["ai-fae-agent"].uptime_seconds
        agents = [fae, admin]
        errors = [agent.error for agent in agents if agent.error]
        self._snapshot = RemoteHealthSnapshot(
            healthy=not errors,
            checked_at=checked_at,
            error=errors[0] if errors else None,
            agents=agents,
        )

    def snapshot(self) -> RemoteHealthSnapshot:
        return self._snapshot.model_copy(deep=True)


async def remote_poll_loop(monitor: RemoteHealthMonitor, interval: float) -> None:
    async with httpx.AsyncClient(trust_env=False) as client:
        while True:
            await monitor.poll_once(client)
            await asyncio.sleep(interval)
