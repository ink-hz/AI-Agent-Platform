from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.main import create_app


def test_daq_health_config_defaults_disabled(monkeypatch):
    monkeypatch.delenv("PLATFORM_DAQ_HEALTH_URL", raising=False)

    assert load_config().daq_health_url is None


@pytest.mark.parametrize("value", ["", "  "])
def test_blank_daq_health_url_is_disabled(monkeypatch, value):
    monkeypatch.setenv("PLATFORM_DAQ_HEALTH_URL", value)

    assert load_config().daq_health_url is None


def test_daq_health_config_accepts_explicit_internal_health_url(monkeypatch):
    monkeypatch.setenv("PLATFORM_DAQ_HEALTH_URL", "http://172.31.0.9:8081/health")

    assert load_config().daq_health_url == "http://172.31.0.9:8081/health"


@pytest.mark.parametrize("value", [
    "http://172.31.0.9:8081/chat", "file:///health",
    "http://user:secret@172.31.0.9:8081/health",
    "http://172.31.0.9:8081/health?token=secret",
    "http://172.31.0.9:8081/health#fragment",
    "http://172.31.0.9:invalid/health", "http://[::1/health",
    "http://172.31.0.9:8081/health;unexpected",
])
def test_daq_health_config_rejects_non_health_or_credential_urls(monkeypatch, value):
    monkeypatch.setenv("PLATFORM_DAQ_HEALTH_URL", value)

    with pytest.raises(ValueError, match="DAQ health URL"):
        load_config()


def _app(tmp_path, **kwargs):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    return create_app(
        registry_path=str(registry), cluster_contract_path=str(contract), **kwargs,
    )


def test_app_passes_optional_daq_url_to_runtime_monitor(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAQ_HEALTH_URL", "http://127.0.0.1:8081/health")
    app = _app(tmp_path, start_poller=False)

    statuses = app.state.remote_health_monitor.snapshot().agents
    assert next(agent for agent in statuses if agent.id == "ai-daq-fae-agent").status == "unknown"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("start_poller", [False, True])
def test_cloud_only_starts_explicit_daq_poller_and_cancels_it(
    tmp_path, monkeypatch, enabled, start_poller,
):
    import asyncio

    from app import main as app_main

    monkeypatch.delenv("PLATFORM_DAQ_HEALTH_URL", raising=False)
    config = replace(
        load_config(), deployment_mode="cloud-replica",
        daq_health_url="http://127.0.0.1:8081/health" if enabled else None,
    )
    monkeypatch.setattr(app_main, "load_config", lambda: config)
    monkeypatch.setattr(
        app_main, "build_cloud_replica_services",
        lambda *_args: (object(), object(), object()),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cloud DAQ integration started a legacy poller")

    monkeypatch.setattr(app_main, "poll_loop", forbidden)
    monkeypatch.setattr(app_main, "cluster_poll_loop", forbidden)
    monkeypatch.setattr(app_main, "remote_poll_loop", forbidden)
    calls = []
    cancelled = []

    async def daq_loop(monitor, interval):
        calls.append((monitor, interval))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(app_main, "daq_poll_loop", daq_loop)
    app = _app(tmp_path, start_poller=start_poller)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert len(calls) == int(enabled and start_poller)

    assert len(cancelled) == int(enabled and start_poller)
