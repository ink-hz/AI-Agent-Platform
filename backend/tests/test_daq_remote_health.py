import asyncio
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.remote_health.monitor import RemoteHealthMonitor, daq_poll_loop
from app.remote_health.probes import CommandResult


NOW = datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc)
DAQ_HEALTH = {
    "status": "ok",
    "agent_id": "ai-daq-fae-agent",
    "environment": "production",
    "provider_mode": "anthropic",
    "runtime_release": "daq-5e86db35722f",
    "knowledge_release": "reviewed-knowledge-sha256",
    "sdk_knowledge_release": "reviewed-sdk-sha256",
    "local_dev_only": False,
    "knowledge_status": "reviewed_or_empty",
    "source_checked_records": 0,
    "platform_task_enabled": False,
}


@pytest.fixture
def health_server():
    class Server(ThreadingHTTPServer):
        daemon_threads = True

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.requests.append((self.command, self.path))
            self.server.requested.set()
            self.server.release.wait(timeout=1)
            self.send_response(self.server.response_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(self.server.response_body)
            except BrokenPipeError:
                pass

        def log_message(self, *_args):
            pass

    server = Server(("127.0.0.1", 0), Handler)
    server.response_status = 200
    server.response_body = json.dumps(DAQ_HEALTH).encode()
    server.requests = []
    server.requested = threading.Event()
    server.release = threading.Event()
    server.release.set()
    server.health_url = f"http://127.0.0.1:{server.server_port}/health"
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True,
    )
    thread.start()
    yield server
    server.release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


async def _ops_ok(_command, _timeout):
    return CommandResult(0, json.dumps({
        "admin_health": {"status": "ok"},
        "units": {"ai-admin-dingtalk-bot": "active"},
    }), "")


def _monitor(url):
    return RemoteHealthMonitor(
        "unused@example.test",
        "/unused/key",
        daq_health_url=url,
        now=lambda: NOW,
    )


def _daq(monitor):
    return next(
        agent for agent in monitor.snapshot().agents
        if agent.id == "ai-daq-fae-agent"
    )


def test_enabled_daq_starts_unknown_and_disabled_keeps_legacy_roster():
    enabled = _monitor("http://127.0.0.1:8081/health")
    disabled = RemoteHealthMonitor("unused@example.test", "/unused/key")

    assert _daq(enabled).status == "unknown"
    assert _daq(enabled).error == "not_checked"
    assert [agent.id for agent in disabled.snapshot().agents] == [
        "ai-fae-agent", "ai-admin-agent",
    ]


@pytest.mark.asyncio
async def test_daq_http_health_preserves_actual_release_evidence(health_server):
    monitor = _monitor(health_server.health_url)
    async with httpx.AsyncClient(trust_env=False) as client:
        await monitor.poll_daq_once(client)

    status = _daq(monitor)
    assert status.status == "healthy"
    assert status.error is None
    assert status.checked_at == NOW
    assert status.backend == "anthropic"
    assert status.model is None
    assert status.engine is None
    assert status.uptime_seconds is None
    assert status.details == DAQ_HEALTH
    assert status.channel == "WebUI"
    assert status.channel_status == "connected"
    assert health_server.requests == [("GET", "/health")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"agent_id": "ai-fae-agent"}, "identity_mismatch"),
        ({"agent_id": None}, "identity_mismatch"),
        ({"environment": "development"}, "environment_mismatch"),
        ({"environment": None}, "environment_mismatch"),
        ({"local_dev_only": True}, "environment_mismatch"),
        ({"status": "error"}, "service_status"),
    ],
)
async def test_daq_http_health_rejects_untrusted_service(health_server, changes, error):
    health_server.response_body = json.dumps({**DAQ_HEALTH, **changes}).encode()
    monitor = _monitor(health_server.health_url)
    async with httpx.AsyncClient(trust_env=False) as client:
        await monitor.poll_daq_once(client)

    status = _daq(monitor)
    assert status.status == "degraded"
    assert status.error == error
    assert status.channel_status == "failed"
    assert monitor.snapshot().healthy is False
    assert health_server.requests == [("GET", "/health")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "body", "error"),
    [(301, b"{}", "http_status"), (400, b"{}", "http_status"), (503, b"{}", "http_status"),
     (200, b"not-json", "invalid_response"), (200, b"[]", "invalid_response")],
)
async def test_daq_http_health_exposes_protocol_failure(
    health_server, http_status, body, error,
):
    health_server.response_status = http_status
    health_server.response_body = body
    monitor = _monitor(health_server.health_url)
    async with httpx.AsyncClient(trust_env=False) as client:
        await monitor.poll_daq_once(client)

    status = _daq(monitor)
    assert status.status == "degraded"
    assert status.error == error
    assert status.channel_status == "failed"
    if http_status != 200:
        assert status.details == {"http_status": http_status}


@pytest.mark.asyncio
async def test_daq_http_timeout_replaces_previous_health_without_stale_fallback(health_server):
    monitor = _monitor(health_server.health_url)
    async with httpx.AsyncClient(trust_env=False) as client:
        await monitor.poll_daq_once(client)
        assert _daq(monitor).status == "healthy"
        health_server.release.clear()
        monitor._timeout = 0.02
        await monitor.poll_daq_once(client)

    status = _daq(monitor)
    assert status.status == "unknown"
    assert status.error == "timeout"
    assert status.details == {}
    assert status.backend is None
    assert status.channel_status == "unknown"


@pytest.mark.asyncio
async def test_daq_http_transport_failure_is_unknown(health_server):
    monitor = _monitor(health_server.health_url)
    health_server.shutdown()
    health_server.server_close()
    async with httpx.AsyncClient(trust_env=False) as client:
        await monitor.poll_daq_once(client)

    assert _daq(monitor).status == "unknown"
    assert _daq(monitor).error == "transport"


@pytest.mark.asyncio
async def test_daq_http_uses_only_an_explicit_model_field(health_server):
    health_server.response_body = json.dumps({
        **DAQ_HEALTH, "llm_model": "observed-health-model",
    }).encode()
    monitor = _monitor(health_server.health_url)
    async with httpx.AsyncClient(trust_env=False) as client:
        await monitor.poll_daq_once(client)

    assert _daq(monitor).model == "observed-health-model"


@pytest.mark.asyncio
async def test_full_poll_includes_optional_daq_without_changing_legacy_agents(health_server):
    monitor = RemoteHealthMonitor(
        "unused@example.test", "/unused/key",
        fae_health_url=health_server.health_url,
        daq_health_url=health_server.health_url,
        now=lambda: NOW,
    )
    async with httpx.AsyncClient(trust_env=False) as client:
        await monitor.poll_once(client, runner=_ops_ok)

    snapshot = monitor.snapshot()
    assert snapshot.healthy is True
    assert [agent.id for agent in snapshot.agents] == [
        "ai-fae-agent", "ai-admin-agent", "ai-daq-fae-agent",
    ]
    assert all(agent.status == "healthy" for agent in snapshot.agents)
    assert health_server.requests == [("GET", "/health"), ("GET", "/health")]


@pytest.mark.asyncio
async def test_disabled_daq_poll_never_calls_http():
    def forbidden(_request):
        raise AssertionError("disabled DAQ must not send a request")

    monitor = RemoteHealthMonitor("unused@example.test", "/unused/key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        await monitor.poll_daq_once(client)

    assert [agent.id for agent in monitor.snapshot().agents] == [
        "ai-fae-agent", "ai-admin-agent",
    ]


@pytest.mark.asyncio
async def test_daq_only_loop_reads_health_and_cancels_cleanly(health_server):
    monitor = _monitor(health_server.health_url)
    task = asyncio.create_task(daq_poll_loop(monitor, interval=60))
    try:
        async with asyncio.timeout(2):
            while _daq(monitor).status == "unknown":
                await asyncio.sleep(0.01)
        assert _daq(monitor).status == "healthy"
        assert health_server.requests == [("GET", "/health")]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
