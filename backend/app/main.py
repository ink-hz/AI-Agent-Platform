import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .attachments import routes as attachment_routes
from .attachments.logging import install_attachment_ticket_redaction
from .attachments.repository import AttachmentRepository
from .attachments.service import AttachmentService
from .attachments.store import AttachmentStore
from .cluster import routes as cluster_routes
from .cluster.monitor import ClusterMonitor, cluster_poll_loop
from .config import Config, is_cloud_mode, load_config
from .cloud_replica.crypto import FieldCipher, read_key_file
from .cloud_replica.repository import (
    ReplicaFlywheelRepository,
    ReplicaObservabilityRepository,
)
from .control_room import routes as control_room_routes
from .control_room.service import ControlRoomService
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
from .local_secrets import read_secret_file
from .observability import routes as observability_routes
from .observability.repository import (
    PsycopgObservabilityRepository,
    UnavailableObservabilityRepository,
)
from .observability.service import ObservabilityService
from .operations import routes as operations_routes
from .operations.repository import OperationsRepository
from .operations.rules import OperationsRuleEngine
from .operations.scheduler import OperationsScheduler, operations_poll_loop
from .operations.service import OperationsService
from .operations.source import PsycopgOperationsSource
from .registry import routes as registry_routes
from .registry.repository import YamlRepository
from .remote_health.monitor import RemoteHealthMonitor, remote_poll_loop
from .review import routes as review_routes
from .review.database import resolve_review_database_url
from .review.repository import PsycopgReviewRepository
from .review.replay import ReplayRunner
from .review.service import ReviewService, UnavailableReviewService
from .spa import SpaStaticFiles


logger = logging.getLogger(__name__)


async def cancel_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def build_cloud_replica_services(
    config: Config,
    cluster_monitor: ClusterMonitor,
    remote_monitor: RemoteHealthMonitor,
    catalog: AgentCatalog,
):
    database_url = read_secret_file(config.replica_database_url_file)
    encryption_key = read_key_file(
        config.replica_encryption_key_file, expected_size=32
    )
    repository = ReplicaObservabilityRepository(
        database_url,
        cipher=FieldCipher(encryption_key),
        stale_seconds=config.replica_stale_seconds,
        catalog=catalog,
    )
    repository.check_schema()
    observability_service = ObservabilityService(repository)
    fleet_service = FleetReadService(
        cluster_monitor,
        catalog,
        UsageCache(
            ReplicaFlywheelRepository(repository),
            ttl_seconds=config.usage_cache_seconds,
        ),
        active_window_minutes=config.active_window_minutes,
        remote_monitor=remote_monitor,
    )
    return fleet_service, observability_service, repository


def build_operations(
    config: Config,
    fleet_service: FleetReadService | None,
    observability_service: ObservabilityService | None,
    database_url: str | None,
) -> tuple[OperationsService | None, OperationsScheduler | None]:
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
        service = OperationsService(
            repository,
            intervals={
                "runtime": config.cluster_poll_interval_seconds,
                "sync": config.remote_poll_interval_seconds,
                "data_access": config.usage_cache_seconds,
                "usage": config.operations_usage_interval_seconds,
                "execution": config.operations_execution_interval_seconds,
                "lifecycle": config.operations_lifecycle_interval_seconds,
            },
        )
        return service, scheduler
    except Exception:
        logger.exception("operations initialization failed")
        return None, None


def build_attachment_service(config: Config) -> AttachmentService:
    database_url = resolve_flywheel_database_url(config)
    if not database_url:
        raise RuntimeError("attachment access requires the Flywheel analyst DSN")
    return AttachmentService(
        AttachmentRepository(database_url),
        AttachmentStore.from_config(config),
        config.attachment_ticket_seconds,
    )


def create_app(
    registry_path: str | None = None,
    cluster_contract_path: str | None = None,
    *,
    start_poller: bool = True,
    fleet_service: FleetReadService | None = None,
    observability_service: ObservabilityService | None = None,
    operations_service=None,
    operations_scheduler: OperationsScheduler | None = None,
    control_room_service: ControlRoomService | None = None,
    review_service=None,
    attachment_service=None,
) -> FastAPI:
    owns_review_service = review_service is None
    config = load_config()
    cloud_mode = is_cloud_mode(config)
    runtime_pollers_enabled = start_poller and not cloud_mode
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
    catalog = AgentCatalog.default()
    replica_repository = None
    if cloud_mode and (fleet_service is None or observability_service is None):
        cloud_fleet, cloud_observability, replica_repository = (
            build_cloud_replica_services(
                config,
                cluster_monitor,
                remote_monitor,
                catalog,
            )
        )
        fleet_service = fleet_service or cloud_fleet
        observability_service = observability_service or cloud_observability
    database_url = (
        resolve_flywheel_database_url(config) if runtime_pollers_enabled else None
    )
    if fleet_service is None:
        repository = (
            PsycopgFlywheelRepository(database_url)
            if database_url
            else UnavailableFlywheelRepository()
        )
        fleet_service = FleetReadService(
            cluster_monitor,
            catalog,
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
    if control_room_service is None:
        control_room_service = ControlRoomService(
            catalog,
            cluster_monitor,
            remote_monitor,
            observability_service,
        )
    if (
        runtime_pollers_enabled
        and operations_service is None
        and operations_scheduler is None
    ):
        operations_service, operations_scheduler = build_operations(
            config,
            fleet_service,
            observability_service,
            database_url,
        )
    if review_service is None:
        review_database_url = (
            resolve_review_database_url(config) if runtime_pollers_enabled else None
        )
        if review_database_url:
            review_repository = PsycopgReviewRepository(review_database_url)
            review_service = ReviewService(
                review_repository,
                registry=repo,
                replay_runner=ReplayRunner(
                    review_repository,
                    repo,
                    request_timeout=config.review_request_timeout_seconds,
                ),
            )
        else:
            review_service = UnavailableReviewService()
    if attachment_service is None and config.attachment_enabled and not cloud_mode:
        attachment_service = build_attachment_service(config)
    if attachment_service is not None:
        install_attachment_ticket_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if runtime_pollers_enabled:
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
            if owns_review_service:
                await review_service.close()

    app = FastAPI(title="Orbbec AI Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.repo = repo
    app.state.health_cache = cache
    app.state.cluster_monitor = cluster_monitor
    app.state.fleet_service = fleet_service
    app.state.observability_service = observability_service
    app.state.operations_service = operations_service
    app.state.operations_scheduler = operations_scheduler
    app.state.remote_health_monitor = remote_monitor
    app.state.control_room_service = control_room_service
    app.state.review_service = review_service
    app.state.attachment_service = attachment_service
    app.state.replica_repository = replica_repository

    @app.get("/api/health")
    def platform_health() -> dict:
        return {"status": "ok"}

    @app.get("/api/deployment")
    def deployment_status() -> dict:
        if cloud_mode:
            if app.state.replica_repository is None:
                return {
                    "mode": "cloud-replica",
                    "read_only": True,
                    "auth": "ssh-tunnel",
                    "freshness": "unavailable",
                    "last_success_at": None,
                }
            return app.state.replica_repository.deployment_status()
        return {
            "mode": "local",
            "read_only": False,
            "auth": "local",
            "freshness": "current",
            "last_success_at": None,
        }

    app.include_router(health_routes.router)
    app.include_router(cluster_routes.router)
    app.include_router(fleet_routes.router)
    app.include_router(observability_routes.router)
    app.include_router(control_room_routes.router)
    app.include_router(operations_routes.router)
    app.include_router(registry_routes.router)
    app.include_router(review_routes.router)
    if attachment_service is not None:
        app.include_router(attachment_routes.router)

    if os.path.isdir(config.static_dir):
        app.mount("/", SpaStaticFiles(directory=config.static_dir, html=True), name="portal")

    return app


app = create_app() if os.getenv("PLATFORM_EAGER_APP") else None
