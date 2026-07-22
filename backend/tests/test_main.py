import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.config import load_config
from app.main import build_operations, cancel_tasks, create_app
from app.operations.repository import OperationsRepository


@pytest.mark.asyncio
async def test_cancel_tasks_waits_for_task_cleanup():
    cleaned = asyncio.Event()

    async def worker():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)

    await cancel_tasks([task])

    assert task.cancelled()
    assert cleaned.is_set()


def test_operations_migration_failure_leaves_existing_health_route_available(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        OperationsRepository,
        "migrate",
        Mock(side_effect=OSError("disk unavailable")),
    )
    config = replace(
        load_config(), operations_database_path=str(tmp_path / "operations.db")
    )

    service, scheduler = build_operations(config, None, None, None)

    assert service is None
    assert scheduler is None
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        operations_service=service,
        operations_scheduler=scheduler,
    )
    client = TestClient(app)

    assert client.get("/api/health").json() == {"status": "ok"}
    assert app.state.operations_service is None
    assert app.state.operations_scheduler is None


def test_create_app_without_pollers_does_not_create_default_operations_database(
    tmp_path, monkeypatch
):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    database = tmp_path / "data" / "operations.db"
    monkeypatch.setenv("PLATFORM_OPERATIONS_DATABASE_PATH", str(database))

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
    )

    assert app.state.operations_service is None
    assert app.state.operations_scheduler is None
    assert not database.exists()


def test_operations_startup_failure_does_not_break_platform_lifespan(
    tmp_path, monkeypatch
):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    monkeypatch.setenv("PLATFORM_FLYWHEEL_ENABLED", "0")

    async def idle(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.main.poll_loop", idle)
    monkeypatch.setattr("app.main.cluster_poll_loop", idle)
    monkeypatch.setattr("app.main.remote_poll_loop", idle)
    class BrokenScheduler:
        async def startup(self):
            raise RuntimeError("scheduler unavailable")

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=True,
        operations_service=object(),
        operations_scheduler=BrokenScheduler(),
    )

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_platform_lifespan_and_health_start_while_operations_baseline_blocks(
    tmp_path, monkeypatch
):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    baseline_started = asyncio.Event()
    baseline_cleaned = asyncio.Event()

    async def idle(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.main.poll_loop", idle)
    monkeypatch.setattr("app.main.cluster_poll_loop", idle)
    monkeypatch.setattr("app.main.remote_poll_loop", idle)

    class BlockingScheduler:
        async def startup(self):
            baseline_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                baseline_cleaned.set()

        async def run_due(self, _now):
            raise AssertionError("periodic evaluation ran before baseline")

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=True,
        operations_service=object(),
        operations_scheduler=BlockingScheduler(),
    )
    lifespan = app.router.lifespan_context(app)
    entering = asyncio.create_task(lifespan.__aenter__())
    try:
        await asyncio.wait_for(baseline_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert entering.done()
        await entering
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/health")).json() == {"status": "ok"}
    finally:
        if not entering.done():
            entering.cancel()
            await asyncio.gather(entering, return_exceptions=True)
        else:
            await lifespan.__aexit__(None, None, None)

    assert baseline_cleaned.is_set()
