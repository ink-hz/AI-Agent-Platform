import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .cluster import routes as cluster_routes
from .cluster.monitor import ClusterMonitor, cluster_poll_loop
from .config import Config, load_config
from .fleet import routes as fleet_routes
from .fleet.cache import UsageCache
from .fleet.catalog import AgentCatalog
from .fleet.database import resolve_flywheel_database_url
from .fleet.repository import (
    PsycopgFlywheelRepository,
    UnavailableFlywheelRepository,
)
from .fleet.service import FleetReadService
from .health import routes as health_routes
from .health.poller import HealthCache, poll_loop
from .observability import routes as observability_routes
from .observability.repository import (
    PsycopgObservabilityRepository,
    UnavailableObservabilityRepository,
)
from .observability.service import ObservabilityService
from .operations.repository import OperationsRepository
from .operations.rules import OperationsRuleEngine
from .operations.scheduler import OperationsScheduler, operations_poll_loop
from .operations.source import PsycopgOperationsSource
from .registry import routes as registry_routes
from .registry.repository import YamlRepository
from .remote_health.monitor import RemoteHealthMonitor, remote_poll_loop
from .spa import SpaStaticFiles


logger = logging.getLogger(__name__)


async def cancel_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def build_operations(
    config: Config,
    fleet_service: FleetReadService | None,
    observability_service: ObservabilityService | None,
    database_url: str | None,
) -> tuple[OperationsRepository | None, OperationsScheduler | None]:
    try:
        database_path = Path(config.operations_database_path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        repository = OperationsRepository(str(database_path))
        repository.migrate()
        source = (
            PsycopgOperationsSource(database_url) if database_url else None
        )
        scheduler = OperationsScheduler(
            repository=repository,
            fleet_service=fleet_service,
            observability_service=observability_service,
            operations_source=source,
            rule_engine=OperationsRuleEngine(repository),
            intervals={
                "runtime": config.cluster_poll_interval_seconds,
                "sync": config.remote_poll_interval_seconds,
                "data_access": config.usage_cache_seconds,
                "usage": config.operations_usage_interval_seconds,
                "execution": config.operations_execution_interval_seconds,
                "lifecycle": config.operations_lifecycle_interval_seconds,
            },
        )
        return repository, scheduler
    except Exception:
        logger.exception("operations initialization failed")
        return None, None


def create_app(
    registry_path: str | None = None,
    cluster_contract_path: str | None = None,
    *,
    start_poller: bool = True,
    fleet_service: FleetReadService | None = None,
    observability_service: ObservabilityService | None = None,
    operations_service=None,
    operations_scheduler: OperationsScheduler | None = None,
) -> FastAPI:
    config = load_config()
    path = registry_path or config.registry_path
    repo = YamlRepository(path)
    agents = repo.list_agents()
    cache = HealthCache([agent.id for agent in agents])
    cluster_monitor = ClusterMonitor(
        cluster_contract_path or config.metabot_contract_path,
        timeout=config.probe_timeout_seconds,
    )
    remote_monitor = RemoteHealthMonitor(
        config.remote_ssh_host,
        config.remote_ssh_key_path,
        timeout=config.probe_timeout_seconds,
    )
    database_url = resolve_flywheel_database_url(config) if start_poller else None
    if fleet_service is None:
        repository = (
            PsycopgFlywheelRepository(database_url)
            if database_url
            else UnavailableFlywheelRepository()
        )
        fleet_service = FleetReadService(
            cluster_monitor,
            AgentCatalog.default(),
            UsageCache(repository, ttl_seconds=config.usage_cache_seconds),
            active_window_minutes=config.active_window_minutes,
            remote_monitor=remote_monitor,
        )
    if observability_service is None:
        observability_repository = (
            PsycopgObservabilityRepository(database_url)
            if database_url
            else UnavailableObservabilityRepository()
        )
        observability_service = ObservabilityService(observability_repository)
    if (
        start_poller
        and operations_service is None
        and operations_scheduler is None
    ):
        operations_service, operations_scheduler = build_operations(
            config,
            fleet_service,
            observability_service,
            database_url,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if start_poller:
            if operations_scheduler is not None:
                try:
                    await operations_scheduler.startup()
                except Exception:
                    logger.exception("operations startup failed")
            tasks = [
                asyncio.create_task(
                    poll_loop(
                        cache,
                        agents,
                        config.poll_interval_seconds,
                        config.probe_timeout_seconds,
                    )
                ),
                asyncio.create_task(
                    cluster_poll_loop(
                        cluster_monitor,
                        config.cluster_poll_interval_seconds,
                    )
                ),
                asyncio.create_task(
                    remote_poll_loop(
                        remote_monitor,
                        config.remote_poll_interval_seconds,
                    )
                ),
            ]
            if operations_scheduler is not None:
                tasks.append(
                    asyncio.create_task(
                        operations_poll_loop(operations_scheduler)
                    )
                )
        try:
            yield
        finally:
            await cancel_tasks(tasks)

    app = FastAPI(title="Orbbec AI Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.repo = repo
    app.state.health_cache = cache
    app.state.cluster_monitor = cluster_monitor
    app.state.fleet_service = fleet_service
    app.state.observability_service = observability_service
    app.state.operations_service = operations_service
    app.state.operations_scheduler = operations_scheduler
    app.state.remote_health_monitor = remote_monitor

    @app.get("/api/health")
    def platform_health() -> dict:
        return {"status": "ok"}

    app.include_router(health_routes.router)
    app.include_router(cluster_routes.router)
    app.include_router(fleet_routes.router)
    app.include_router(observability_routes.router)
    app.include_router(registry_routes.router)

    if os.path.isdir(config.static_dir):
        app.mount("/", SpaStaticFiles(directory=config.static_dir, html=True), name="portal")

    return app


app = create_app() if os.getenv("PLATFORM_EAGER_APP") else None
