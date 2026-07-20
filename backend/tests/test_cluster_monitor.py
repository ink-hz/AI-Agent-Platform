import json

import httpx
import pytest
import respx

from app.cluster.models import InstanceStatus, MonitorTarget, SourceStatus
from app.cluster.monitor import ClusterMonitor, build_snapshot, probe_target


def _target(port: int, name: str | None = None) -> MonitorTarget:
    bot_name = name or f"bot-{port}"
    return MonitorTarget(
        id=bot_name,
        name=bot_name,
        pm2_name=f"metabot-{bot_name}",
        port=port,
        health_url=f"http://127.0.0.1:{port}/api/health",
    )


def _instance(state: str, port: int) -> InstanceStatus:
    target = _target(port)
    return InstanceStatus(
        id=target.id,
        name=target.name,
        pm2_name=target.pm2_name,
        port=port,
        status=state,
    )


def _bot(name: str, port: int) -> dict:
    return {
        "name": name,
        "workdir": f"/runtime/{name}",
        "instance": {"pm2Name": f"metabot-{name}", "apiPort": port},
    }


def _write_contract(path, bots: list[dict]) -> None:
    path.write_text(json.dumps({"bots": bots}), encoding="utf-8")


@pytest.mark.asyncio
@respx.mock
async def test_probe_healthy_includes_uptime_and_latency():
    target = _target(9100)
    respx.get(target.health_url).mock(
        return_value=httpx.Response(200, json={"status": "ok", "uptime": 42})
    )

    async with httpx.AsyncClient() as client:
        status = await probe_target(client, target, 3.0)

    assert status.status == "healthy"
    assert status.uptime_seconds == 42
    assert status.latency_ms is not None
    assert status.checked_at is not None
    assert status.error is None


@pytest.mark.asyncio
@respx.mock
async def test_probe_reachable_failures_are_degraded():
    non_200 = _target(9101)
    invalid_json = _target(9102)
    bad_status = _target(9103)
    respx.get(non_200.health_url).mock(return_value=httpx.Response(503))
    respx.get(invalid_json.health_url).mock(
        return_value=httpx.Response(200, text="not-json")
    )
    respx.get(bad_status.health_url).mock(
        return_value=httpx.Response(200, json={"status": "starting"})
    )

    async with httpx.AsyncClient() as client:
        results = [
            await probe_target(client, non_200, 3.0),
            await probe_target(client, invalid_json, 3.0),
            await probe_target(client, bad_status, 3.0),
        ]

    assert [(item.status, item.error) for item in results] == [
        ("degraded", "http_503"),
        ("degraded", "invalid_json"),
        ("degraded", "status_not_ok"),
    ]


@pytest.mark.asyncio
@respx.mock
async def test_probe_connection_error_is_offline_without_leaking_exception():
    target = _target(9104)
    respx.get(target.health_url).mock(
        side_effect=httpx.ConnectError("secret internal address")
    )

    async with httpx.AsyncClient() as client:
        status = await probe_target(client, target, 3.0)

    assert status.status == "offline"
    assert status.error == "connection_failed"
    assert "secret" not in status.model_dump_json()


def test_snapshot_sorts_failures_first_and_summarizes():
    snapshot = build_snapshot(
        [
            _instance("healthy", 9100),
            _instance("checking", 9103),
            _instance("offline", 9102),
            _instance("degraded", 9101),
        ],
        SourceStatus(healthy=True, checked_at="2026-07-20T10:00:00Z"),
    )

    assert [item.status for item in snapshot.instances] == [
        "offline",
        "degraded",
        "checking",
        "healthy",
    ]
    assert snapshot.summary.model_dump() == {
        "total": 4,
        "healthy": 1,
        "degraded": 1,
        "offline": 1,
        "checking": 1,
    }


@pytest.mark.asyncio
async def test_invalid_contract_keeps_last_good_targets(tmp_path):
    path = tmp_path / "contract.json"
    _write_contract(path, [_bot("hr-bot", 9101)])
    monitor = ClusterMonitor(str(path), timeout=3.0)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"status": "ok", "uptime": 5}
            )
        )
    ) as client:
        await monitor.poll_once(client)
        path.write_text("{", encoding="utf-8")
        await monitor.poll_once(client)

    snapshot = monitor.snapshot()
    assert snapshot.source.healthy is False
    assert snapshot.source.error == "runtime contract is unreadable"
    assert [item.id for item in snapshot.instances] == ["hr-bot"]
    assert snapshot.instances[0].status == "healthy"


@pytest.mark.asyncio
async def test_contract_reload_adds_and_removes_targets(tmp_path):
    path = tmp_path / "contract.json"
    _write_contract(path, [_bot("one", 9100)])
    monitor = ClusterMonitor(str(path), timeout=3.0)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "ok", "uptime": 5})
    )

    async with httpx.AsyncClient(transport=transport) as client:
        await monitor.poll_once(client)
        _write_contract(path, [_bot("two", 9102)])
        await monitor.poll_once(client)

    assert [item.id for item in monitor.snapshot().instances] == ["two"]
