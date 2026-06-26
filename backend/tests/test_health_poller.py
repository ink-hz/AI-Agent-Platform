import httpx
import pytest
import respx

from app.health.poller import HealthCache, probe_agent, poll_once
from app.registry.models import AgentEntry


def _agent(agent_id, url, health_type):
    return AgentEntry(
        id=agent_id,
        name=agent_id,
        entry_url=f"http://{agent_id}/app/",
        health={"url": url, "type": health_type},
    )


def test_cache_starts_unknown():
    cache = HealthCache(["fae", "admin"])
    assert cache.get("fae").online is None
    assert {status.id for status in cache.all()} == {"fae", "admin"}


@pytest.mark.asyncio
@respx.mock
async def test_probe_online_normalizes_metrics():
    respx.get("http://fae/health").mock(
        return_value=httpx.Response(200, json={"qa_indexed": True, "products_loaded": 7})
    )
    async with httpx.AsyncClient() as client:
        status = await probe_agent(
            client, _agent("fae", "http://fae/health", "fae"), 3.0
        )
    assert status.online is True
    assert status.latency_ms is not None
    assert {"label": "产品数", "value": 7} in status.metrics
    assert status.raw == {"qa_indexed": True, "products_loaded": 7}


@pytest.mark.asyncio
@respx.mock
async def test_probe_offline_on_error():
    respx.get("http://admin/health").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as client:
        status = await probe_agent(
            client, _agent("admin", "http://admin/health", "admin"), 3.0
        )
    assert status.online is False
    assert status.metrics == []


@pytest.mark.asyncio
@respx.mock
async def test_probe_offline_on_non_200():
    respx.get("http://fae/health").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        status = await probe_agent(
            client, _agent("fae", "http://fae/health", "fae"), 3.0
        )
    assert status.online is False


@pytest.mark.asyncio
@respx.mock
async def test_poll_once_updates_all():
    respx.get("http://fae/health").mock(
        return_value=httpx.Response(200, json={"products_loaded": 1})
    )
    respx.get("http://admin/health").mock(
        return_value=httpx.Response(200, json={"chunks_loaded": 2})
    )
    agents = [
        _agent("fae", "http://fae/health", "fae"),
        _agent("admin", "http://admin/health", "admin"),
    ]
    cache = HealthCache([agent.id for agent in agents])
    async with httpx.AsyncClient() as client:
        await poll_once(cache, agents, client, 3.0)
    assert cache.get("fae").online is True
    assert cache.get("admin").online is True
