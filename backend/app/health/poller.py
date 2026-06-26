import asyncio
import time
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field

from ..registry.models import AgentEntry
from .normalizer import normalize


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HealthStatus(BaseModel):
    id: str
    online: bool | None = None
    checked_at: str | None = None
    latency_ms: int | None = None
    version: str | None = None
    metrics: list[dict] = Field(default_factory=list)
    raw: dict | None = None


class HealthCache:
    def __init__(self, agent_ids: list[str]) -> None:
        self._data: dict[str, HealthStatus] = {
            agent_id: HealthStatus(id=agent_id) for agent_id in agent_ids
        }

    def get(self, agent_id: str) -> HealthStatus | None:
        return self._data.get(agent_id)

    def all(self) -> list[HealthStatus]:
        return list(self._data.values())

    def set(self, status: HealthStatus) -> None:
        self._data[status.id] = status


async def probe_agent(
    client: httpx.AsyncClient, agent: AgentEntry, timeout: float
) -> HealthStatus:
    start = time.perf_counter()
    try:
        response = await client.get(agent.health.url, timeout=timeout)
    except Exception:
        return HealthStatus(id=agent.id, online=False, checked_at=_now_iso())
    latency_ms = int((time.perf_counter() - start) * 1000)
    if response.status_code != 200:
        return HealthStatus(
            id=agent.id, online=False, checked_at=_now_iso(), latency_ms=latency_ms
        )
    try:
        raw = response.json()
    except Exception:
        raw = {}
    raw_dict = raw if isinstance(raw, dict) else {}
    return HealthStatus(
        id=agent.id,
        online=True,
        checked_at=_now_iso(),
        latency_ms=latency_ms,
        version=agent.version or None,
        metrics=normalize(agent.health.type, raw_dict),
        raw=raw_dict,
    )


async def poll_once(
    cache: HealthCache,
    agents: list[AgentEntry],
    client: httpx.AsyncClient,
    timeout: float,
) -> None:
    results = await asyncio.gather(
        *(probe_agent(client, agent, timeout) for agent in agents)
    )
    for status in results:
        cache.set(status)


async def poll_loop(
    cache: HealthCache,
    agents: list[AgentEntry],
    interval: float,
    timeout: float,
) -> None:
    async with httpx.AsyncClient() as client:
        while True:
            await poll_once(cache, agents, client, timeout)
            await asyncio.sleep(interval)
