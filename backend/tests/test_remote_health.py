import json
from datetime import datetime, timezone

import httpx
import pytest

from app.remote_health.monitor import RemoteHealthMonitor
from app.remote_health.probes import CommandResult, build_remote_ops_program, probe_fae


NOW = datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fae_probe_normalizes_public_health() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://47.106.112.69/health"
        return httpx.Response(200, json={"status": "ok", "llm_model": "claude-opus-4-8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        status = await probe_fae(
            client,
            "http://47.106.112.69/health",
            timeout=3,
            now=lambda: NOW,
        )

    assert status.id == "ai-fae-agent"
    assert status.status == "healthy"
    assert status.details["llm_model"] == "claude-opus-4-8"
    assert status.model == "claude-opus-4-8"
    assert status.channel == "WebUI"
    assert status.channel_status == "connected"


def test_admin_probe_program_checks_api_units_and_fae_start_time() -> None:
    program = build_remote_ops_program()

    assert "http://127.0.0.1:8011/health" in program
    for unit in ("ai-admin-agent", "ai-admin-job-worker", "ai-admin-dingtalk-bot"):
        assert unit in program
    assert "ai-fae-backend" in program
    assert "docker" in program


@pytest.mark.asyncio
async def test_monitor_combines_fae_http_and_one_admin_ssh_command() -> None:
    calls = []

    async def runner(command, timeout):
        calls.append(command)
        payload = {
            "admin_health": {"status": "ok", "llm_model": "glm-5.2"},
            "units": {
                "ai-admin-agent": "active",
                "ai-admin-job-worker": "active",
                "ai-admin-dingtalk-bot": "active",
            },
            "admin_started_at": "2026-07-21T01:00:00+00:00",
            "fae_started_at": "2026-07-20T01:00:00+00:00",
        }
        return CommandResult(0, json.dumps(payload), "")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "llm_model": "claude-opus-4-8"})

    monitor = RemoteHealthMonitor(
        ssh_host="root@example.test",
        ssh_key_path="/tmp/key",
        now=lambda: NOW,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await monitor.poll_once(client, runner=runner)

    snapshot = monitor.snapshot()
    assert len(calls) == 1
    assert [status.id for status in snapshot.agents] == ["ai-fae-agent", "ai-admin-agent"]
    assert all(status.status == "healthy" for status in snapshot.agents)
    assert snapshot.agents[0].uptime_seconds == 115200
    assert snapshot.agents[1].uptime_seconds == 28800
    assert snapshot.agents[0].model == "claude-opus-4-8"
    assert snapshot.agents[0].channel == "WebUI"
    assert snapshot.agents[0].channel_status == "connected"
    assert snapshot.agents[1].model == "glm-5.2"
    assert snapshot.agents[1].channel == "DingTalk"
    assert snapshot.agents[1].channel_status == "connected"


@pytest.mark.asyncio
async def test_transport_failure_is_unknown_and_keeps_last_uptime() -> None:
    responses = [
        CommandResult(0, json.dumps({
            "admin_health": {"status": "ok"},
            "units": {
                "ai-admin-agent": "active",
                "ai-admin-job-worker": "active",
                "ai-admin-dingtalk-bot": "active",
            },
            "admin_started_at": "2026-07-21T01:00:00+00:00",
            "fae_started_at": "2026-07-20T01:00:00+00:00",
        }), ""),
        CommandResult(255, "", "connection failed"),
    ]

    async def runner(_command, _timeout):
        return responses.pop(0)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    monitor = RemoteHealthMonitor("root@example.test", "/tmp/key", now=lambda: NOW)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await monitor.poll_once(client, runner=runner)
        before = monitor.snapshot().agents[1]
        await monitor.poll_once(client, runner=runner)
        after = monitor.snapshot().agents[1]

    assert before.status == "healthy"
    assert after.status == "unknown"
    assert after.error == "transport"
    assert after.uptime_seconds == before.uptime_seconds
    assert after.channel_status == "unknown"
