import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.config import load_config
from app.main import (
    agent_brain_loop,
    build_operations,
    build_review_service,
    cancel_tasks,
    create_app,
)
from app.control_plane.models import ControlPlaneConfig, IdentityMode
from app.operations.repository import OperationsRepository


def test_build_identity_auth_builds_registered_in_client_profile(
    tmp_path, monkeypatch
) -> None:
    from app import main as app_main

    registry = tmp_path / "dingtalk-in-client-apps.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "apps": [
                    {
                        "id": "office",
                        "app_key": "office-key",
                        "app_secret": "office-secret",
                        "return_paths": ["/office/"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry.chmod(0o600)
    control = ControlPlaneConfig(
        mode=IdentityMode.PRODUCTION,
        control_database_url_file="/private/control-database-url",
        audit_database_url_file="",
        public_base_url="https://agent.example.test",
        route_prefix="/",
        cookie_name="__Host-platform_session",
        dingtalk_app_key="platform-key",
        dingtalk_agent_id="platform-agent",
        dingtalk_corp_id="corp-id",
        dingtalk_app_secret_file="/private/platform-secret",
        encryption_keyring_file="/private/encryption",
        hmac_keyring_file="/private/lookup",
        rate_limit_hmac_keyring_file="/private/rate",
        dingtalk_in_client_apps_file=str(registry),
    )

    class Keyring:
        active_key = b"k" * 32
        active_version = 1

        @classmethod
        def from_file(cls, *_args, **_kwargs):
            return cls()

    class Repository:
        def __init__(self, *_args, **_kwargs):
            pass

        def directory_freshness(self, **_kwargs):
            return "fresh"

    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

        async def exchange_login_code(self, code, verifier):
            return SimpleNamespace(code=code, verifier=verifier)

        async def aclose(self):
            return None

    resolvers = []

    class Resolver:
        def __init__(self, database_url, **kwargs):
            self.database_url = database_url
            self.kwargs = kwargs
            resolvers.append(self)

        async def resolve_login_identity(self, _result, _freshness):
            return "same-internal-user-id"

    captured = {}

    def auth_factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(
        app_main,
        "read_secret_file",
        lambda path: "postgresql://platform" if "database" in path else "platform-secret",
    )
    monkeypatch.setattr(app_main, "IdentityKeyring", Keyring)
    monkeypatch.setattr(app_main, "ProviderIdentityCodec", lambda *_args: object())
    monkeypatch.setattr(app_main, "WebSessionRepository", Repository)
    monkeypatch.setattr(app_main, "ControlRateLimiter", lambda **_kwargs: object())
    monkeypatch.setattr(app_main, "DingTalkClient", Client)
    monkeypatch.setattr(app_main, "IdentityResolver", Resolver)
    monkeypatch.setattr(app_main, "DingTalkWebAuth", auth_factory)

    built = app_main.build_identity_auth(SimpleNamespace(control_plane=control))

    assert built.app_key == "platform-key"
    assert [client.kwargs["app_key"] for client in clients] == [
        "platform-key",
        "platform-key",
        "office-key",
    ]
    assert [client.kwargs["login_flow"] for client in clients] == [
        "qr",
        "in_client",
        "in_client",
    ]
    assert all(client.kwargs["corp_id"] == "corp-id" for client in clients)
    assert len(resolvers) == 3
    assert all(resolver.database_url == "postgresql://platform" for resolver in resolvers)
    assert len(captured["in_client_profiles"]) == 1
    office = captured["in_client_profiles"][0]
    assert (office.app_id, office.app_key, office.return_paths) == (
        "office",
        "office-key",
        ("/office/",),
    )
    assert len(captured["close_callbacks"]) == 3


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


@pytest.mark.asyncio
async def test_agent_brain_loop_starts_as_single_leader_and_cleans_up():
    entered = asyncio.Event()
    released = asyncio.Event()

    class LeaderContext:
        def __enter__(self):
            entered.set()
            return True

        def __exit__(self, *_args):
            released.set()

    class Orchestrator:
        def leader_session(self):
            return LeaderContext()

        def advance_pending(self, *, limit):
            assert limit == 50
            return 0

    task = asyncio.create_task(agent_brain_loop(Orchestrator(), idle_seconds=0.01))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert released.is_set()


@pytest.mark.asyncio
async def test_agent_brain_shutdown_holds_leadership_until_inflight_pass_finishes():
    entered = asyncio.Event()
    worker_started = asyncio.Event()
    worker_release = asyncio.Event()
    leader_released = asyncio.Event()
    loop = asyncio.get_running_loop()

    class LeaderContext:
        def __enter__(self):
            entered.set()
            return True

        def __exit__(self, *_args):
            leader_released.set()

    class Orchestrator:
        def leader_session(self):
            return LeaderContext()

        def advance_pending(self, *, limit):
            assert limit == 50
            loop.call_soon_threadsafe(worker_started.set)
            future = asyncio.run_coroutine_threadsafe(worker_release.wait(), loop)
            future.result(timeout=2)
            return 0

    task = asyncio.create_task(agent_brain_loop(Orchestrator(), idle_seconds=0.01))
    await asyncio.wait_for(entered.wait(), timeout=1)
    await asyncio.wait_for(worker_started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)

    assert leader_released.is_set() is False
    worker_release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert leader_released.is_set() is True


@pytest.mark.asyncio
async def test_agent_brain_shutdown_preserves_cancellation_if_inflight_pass_fails():
    worker_started = asyncio.Event()
    worker_release = asyncio.Event()
    leader_released = asyncio.Event()
    loop = asyncio.get_running_loop()

    class LeaderContext:
        def __enter__(self):
            return True

        def __exit__(self, *_args):
            leader_released.set()

    class Orchestrator:
        def leader_session(self):
            return LeaderContext()

        def advance_pending(self, *, limit):
            loop.call_soon_threadsafe(worker_started.set)
            future = asyncio.run_coroutine_threadsafe(worker_release.wait(), loop)
            future.result(timeout=2)
            raise RuntimeError("pass failed during shutdown")

    task = asyncio.create_task(agent_brain_loop(Orchestrator(), idle_seconds=0.01))
    await asyncio.wait_for(worker_started.wait(), timeout=1)
    task.cancel()
    worker_release.set()
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled() is True
    assert leader_released.is_set() is True


def test_agent_brain_feature_defaults_disabled_and_requires_all_three_gates(
    monkeypatch,
):
    monkeypatch.delenv("PLATFORM_AGENT_BRAIN_ENABLED", raising=False)
    assert load_config().agent_brain_enabled is False

    monkeypatch.setenv("PLATFORM_AGENT_BRAIN_ENABLED", "1")
    with pytest.raises(ValueError, match="Agent Brain requires production identity and relay"):
        load_config()

    monkeypatch.setenv("PLATFORM_AGENT_BRAIN_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_AGENT_BRAIN_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="V2 requires Agent Brain"):
        load_config()


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


def test_review_writer_unavailable_isolated_from_platform_health(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
    )

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/review/overview").status_code == 503


def test_review_service_uses_analyst_for_reads_and_optional_writer_for_mutations(
    monkeypatch,
):
    repositories = []

    class Repository:
        def __init__(self, database_url):
            self.database_url = database_url
            repositories.append(self)

    class Runner:
        def __init__(self, repository, _registry, *, request_timeout):
            self.repository = repository
            self.request_timeout = request_timeout

    monkeypatch.setattr("app.main.PsycopgReviewRepository", Repository)
    monkeypatch.setattr("app.main.ReplayRunner", Runner)
    config = replace(
        load_config(),
        review_enabled=True,
        review_database_url="postgresql://platform_review_writer@db/review",
    )

    service = build_review_service(
        config,
        registry=object(),
        analyst_database_url="postgresql://flywheel_analyst@db/flywheel",
    )

    assert service.read_repository.database_url.endswith("/flywheel")
    assert service.write_repository.database_url.endswith("/review")
    assert service.replay_runner.repository is service.write_repository
    assert repositories == [service.read_repository, service.write_repository]

    read_only = build_review_service(
        replace(config, review_enabled=False, review_database_url=None),
        registry=object(),
        analyst_database_url="postgresql://flywheel_analyst@db/flywheel",
    )

    assert read_only.read_repository.database_url.endswith("/flywheel")
    assert read_only.write_repository is None
    assert read_only.replay_runner is None


def test_injected_review_service_lifecycle_remains_owned_by_caller(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")

    class InjectedReviewService:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    review_service = InjectedReviewService()
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        review_service=review_service,
    )

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}

    assert review_service.closed is False


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
