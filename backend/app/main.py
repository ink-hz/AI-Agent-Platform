import asyncio
import logging
import os
import ipaddress
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

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
from .cloud_replica.management_repository import (
    ReplicaOperationsRepository,
    ReplicaReviewRepository,
)
from .control_room import routes as control_room_routes
from .control_room.service import ControlRoomService
from .control_plane.middleware import IdentitySecurityMiddleware
from .control_plane.authorization import (
    AuthorizationReadAuditWriter,
    AuthorizationRepository,
    AuthorizationService,
)
from .control_plane import routes_manage
from .control_plane.audit import AuditWriter
from .control_plane.routes_manage import ManagementRepository, ManagementService
from .control_plane.models import DirectoryFreshness, IdentityMode
from .control_plane.routes_auth import build_auth_router
from .control_plane.auth import (
    AuthSecrets,
    DingTalkWebAuth,
    HardStaleAccessAuditWriter,
    SystemHealthAuditWriter,
    WebSessionRepository,
)
from .control_plane.crypto import IdentityKeyring, ProviderIdentityCodec
from .control_plane.dingtalk import DingTalkClient
from .control_plane.identity import IdentityResolver
from .control_plane.rate_limit import ControlRateLimiter
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
from .health.platform import (
    build_deployment_status,
    build_detailed_platform_health,
    build_public_platform_health,
)
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
from .spa import SpaStaticFiles, load_public_asset_manifest


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
        auth_mode=config.cloud_auth_mode,
    )
    repository.check_schema()
    repository.review_repository = ReplicaReviewRepository(
        database_url,
        cipher=FieldCipher(encryption_key),
        stale_seconds=config.replica_stale_seconds,
    )
    repository.operations_repository = ReplicaOperationsRepository(
        database_url,
        cipher=FieldCipher(encryption_key),
        stale_seconds=config.replica_stale_seconds,
    )
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


def build_review_service(
    config: Config,
    registry: YamlRepository,
    analyst_database_url: str | None,
):
    if not analyst_database_url:
        return UnavailableReviewService()

    read_repository = PsycopgReviewRepository(analyst_database_url)
    writer_database_url = resolve_review_database_url(config)
    write_repository = (
        PsycopgReviewRepository(writer_database_url)
        if writer_database_url
        else None
    )
    replay_runner = (
        ReplayRunner(
            write_repository,
            registry,
            request_timeout=config.review_request_timeout_seconds,
        )
        if write_repository is not None
        else None
    )
    return ReviewService(
        read_repository,
        write_repository=write_repository,
        registry=registry,
        replay_runner=replay_runner,
    )


def build_identity_auth(config: Config) -> DingTalkWebAuth:
    control = config.control_plane
    database_url = read_secret_file(control.control_database_url_file)
    app_secret = read_secret_file(control.dingtalk_app_secret_file)
    encryption = IdentityKeyring.from_file(
        control.encryption_keyring_file,
        expected_purpose="provider-encryption",
        expected_key_length=32,
    )
    lookup = IdentityKeyring.from_file(
        control.hmac_keyring_file,
        expected_purpose="provider-lookup-hmac",
        expected_key_length=32,
    )
    rate_lookup = IdentityKeyring.from_file(
        control.rate_limit_hmac_keyring_file,
        expected_purpose="rate-limit-hmac",
        expected_key_length=32,
    )
    codec = ProviderIdentityCodec(encryption, lookup)
    auth_secrets = AuthSecrets(lookup.active_key, key_version=lookup.active_version)
    repository = WebSessionRepository(database_url, secrets=auth_secrets)
    rate_limiter = ControlRateLimiter(
        control_database_url=database_url,
        secrets=auth_secrets,
        rate_secrets=AuthSecrets(
            rate_lookup.active_key, key_version=rate_lookup.active_version
        ),
        login_starts_per_challenge=control.login_starts_per_challenge,
        challenge_window_seconds=control.login_challenge_window_seconds,
        active_login_attempts=control.active_login_attempts,
        edge_login_per_minute=control.edge_login_starts_per_minute,
        edge_login_burst=control.edge_login_burst,
        edge_callbacks_per_minute=control.edge_callbacks_per_minute,
        oauth_exchange_concurrency=control.oauth_exchange_concurrency,
        oauth_exchanges_per_minute=control.oauth_exchanges_per_minute,
        authenticated_reads_per_minute=control.authenticated_reads_per_minute,
        authenticated_mutations_per_minute=control.authenticated_mutations_per_minute,
    )
    qr_client = DingTalkClient(
        app_key=control.dingtalk_app_key,
        app_secret=app_secret,
        corp_id=control.dingtalk_corp_id,
        login_flow="qr",
    )
    in_client = DingTalkClient(
        app_key=control.dingtalk_app_key,
        app_secret=app_secret,
        corp_id=control.dingtalk_corp_id,
        login_flow="in_client",
    )
    qr_resolver = IdentityResolver(
        database_url,
        corp_id=control.dingtalk_corp_id,
        client=qr_client,
        identity_codec=codec,
    )
    in_client_resolver = IdentityResolver(
        database_url,
        corp_id=control.dingtalk_corp_id,
        client=in_client,
        identity_codec=codec,
    )

    async def resolve(client, resolver, code: str, verifier: str):
        result = await client.exchange_login_code(code, verifier)
        freshness = repository.directory_freshness(
            warning_after_seconds=control.warning_after_seconds,
            hard_stale_after_seconds=control.hard_stale_after_seconds,
        )
        return await resolver.resolve_login_identity(result, freshness)

    async def qr_login(code: str, verifier: str):
        return await resolve(qr_client, qr_resolver, code, verifier)

    async def in_client_login(code: str, verifier: str):
        return await resolve(in_client, in_client_resolver, code, verifier)

    return DingTalkWebAuth(
        repository=repository,
        secrets=auth_secrets,
        qr_login=qr_login,
        in_client_login=in_client_login,
        environment="preview" if control.mode is IdentityMode.PREVIEW else "production",
        route_prefix=control.route_prefix,
        public_base_url=control.public_base_url,
        app_key=control.dingtalk_app_key,
        corp_id=control.dingtalk_corp_id,
        state_ttl_seconds=control.oauth_state_ttl_seconds,
        mode=control.mode,
        cookie_name=control.cookie_name,
        rate_limiter=rate_limiter,
        trusted_proxy_networks=tuple(
            ipaddress.ip_network(value, strict=True)
            for value in control.trusted_proxy_cidrs
        ),
        close_callbacks=(qr_client.aclose, in_client.aclose),
        hard_stale_audit=(
            HardStaleAccessAuditWriter(
                read_secret_file(control.audit_database_url_file)
            )
            if control.audit_database_url_file
            else None
        ),
        warning_after_seconds=control.warning_after_seconds,
        hard_stale_after_seconds=control.hard_stale_after_seconds,
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
    identity_auth=None,
) -> FastAPI:
    owns_review_service = review_service is None
    owns_identity_auth = identity_auth is None
    config = load_config()
    identity_enabled = (
        identity_auth is not None
        or config.control_plane.mode is not IdentityMode.DISABLED
    )
    if identity_enabled and identity_auth is None:
        identity_auth = build_identity_auth(config)
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
        if review_service is None:
            cloud_review_repository = getattr(
                replica_repository, "review_repository", None
            )
            if cloud_review_repository is not None:
                review_service = ReviewService(
                    cloud_review_repository,
                    write_repository=None,
                    registry=repo,
                )
        if operations_service is None:
            cloud_operations_repository = getattr(
                replica_repository, "operations_repository", None
            )
            if cloud_operations_repository is not None:
                operations_service = OperationsService(
                    cloud_operations_repository
                )
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
        review_service = build_review_service(
            config,
            repo,
            database_url if runtime_pollers_enabled else None,
        )
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
            if owns_identity_auth and identity_auth is not None:
                await identity_auth.aclose()

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
    app.state.identity_auth = identity_auth
    authorization_service = None
    if identity_enabled and config.control_plane.audit_database_url_file:
        control_database_url = read_secret_file(
            config.control_plane.control_database_url_file
        )
        audit_database_url = read_secret_file(
            config.control_plane.audit_database_url_file
        )
        app.state.system_health_audit = SystemHealthAuditWriter(audit_database_url)
        app.state.identity_management_service = ManagementService(
            ManagementRepository(control_database_url),
            AuditWriter.from_database_url(audit_database_url),
            hard_stale_audit=identity_auth.hard_stale_audit,
        )
        authorization_service = AuthorizationService(
            AuthorizationRepository(control_database_url),
            cloud_mode=cloud_mode,
            read_audit=AuthorizationReadAuditWriter(audit_database_url),
        )

    if not identity_enabled:
        @app.get("/api/health")
        def platform_health() -> dict:
            return build_public_platform_health()
    else:
        public_assets = load_public_asset_manifest(config.static_dir)
        release_sha = os.getenv("PLATFORM_RELEASE_SHA")
        app.include_router(build_auth_router(
            identity_auth,
            static_dir=config.static_dir,
            public_assets=public_assets,
            detailed_health=lambda request: build_detailed_platform_health(
                request.app,
                config,
                release_sha=release_sha,
            ),
        ))

    @app.get("/api/deployment")
    def deployment_status() -> dict:
        return build_deployment_status(app, config)

    app.include_router(health_routes.router)
    app.include_router(cluster_routes.router)
    app.include_router(fleet_routes.router)
    app.include_router(observability_routes.router)
    app.include_router(control_room_routes.router)
    app.include_router(operations_routes.router)
    app.include_router(registry_routes.router)
    app.include_router(review_routes.router)
    if identity_enabled:
        app.include_router(routes_manage.router)
        def request_auth_context(request: Request):
            return request.state.auth_context

        def request_csrf_verified(request: Request) -> bool:
            return bool(request.state.csrf_token)

        def current_directory_is_fresh() -> bool:
            return (
                identity_auth.repository.directory_freshness(
                    warning_after_seconds=(
                        config.control_plane.warning_after_seconds
                    ),
                    hard_stale_after_seconds=(
                        config.control_plane.hard_stale_after_seconds
                    ),
                )
                is DirectoryFreshness.FRESH
            )

        app.dependency_overrides[routes_manage.authenticated_context] = (
            request_auth_context
        )
        app.dependency_overrides[routes_manage.csrf_protection] = (
            request_csrf_verified
        )
        app.dependency_overrides[routes_manage.fresh_directory] = (
            current_directory_is_fresh
        )
    if attachment_service is not None:
        app.include_router(attachment_routes.router)

    if os.path.isdir(config.static_dir) and not identity_enabled:
        app.mount("/", SpaStaticFiles(directory=config.static_dir, html=True), name="portal")

    if identity_enabled:
        app.add_middleware(
            IdentitySecurityMiddleware,
            auth=identity_auth,
            public_assets=public_assets,
            authorization=authorization_service,
            routes=tuple(app.router.routes),
        )

    return app


app = create_app() if os.getenv("PLATFORM_EAGER_APP") else None
