import asyncio
import logging
import os
import ipaddress
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
import psycopg
from psycopg.rows import dict_row

from .attachments import routes as attachment_routes
from .attachments.artifact_service import ArtifactOutputService, ArtifactRepository
from .attachments.citation_service import CitationRepository, CitationService
from .attachments.conversation_routes import build_conversation_attachment_router
from .ai_notes.repository import AiNotesContentError, AiNotesRepository
from .ai_notes.routes import (
    AiNotesReader,
    UnavailableAiNotesReader,
    build_ai_notes_router,
)
from .attachments.logging import install_attachment_ticket_redaction
from .attachments.conversation_repository import ConversationAttachmentRepository
from .attachments.download_service import (
    ConversationAttachmentAccessRepository,
    ConversationAttachmentDownloadService,
    S3ImmutableAttachmentStore,
)
from .attachments.grant_service import AttachmentGrantService, TaskGrantRepository
from .attachments.object_writer import AttachmentObjectWriter
from .attachments.result_projection import ConversationResultProjection
from .attachments.repository import AttachmentRepository
from .attachments.service import AttachmentService
from .attachments.store import AttachmentStore
from .attachments.upload_service import AttachmentUploadService
from .cluster import routes as cluster_routes
from .cluster.monitor import ClusterMonitor, cluster_poll_loop
from .config import Config, is_cloud_mode, load_config
from .cloud_replica.crypto import FieldCipher, read_key_file
from .cloud_replica.repository import (
    ReplicaFlywheelRepository,
    ReplicaObservabilityRepository,
)
from .cloud_replica.management_repository import (
    ReplicaFaeReportRepository,
    ReplicaOperationsRepository,
    ReplicaReviewRepository,
)
from .control_room import routes as control_room_routes
from .control_room.service import ControlRoomService
from .control_plane.agent_launch import (
    AgentLaunchRepository,
    AgentLaunchService,
    build_agent_launch_router,
)
from .control_plane.middleware import (
    DisabledExecutionWorkerNamespaceMiddleware,
    IdentitySecurityMiddleware,
)
from .control_plane.authorization import (
    AuthorizationReadAuditWriter,
    AuthorizationRepository,
    AuthorizationService,
)
from .control_plane.access_history import (
    AccessHistoryRepository,
    UnavailableAccessHistoryRepository,
)
from .control_plane.fae_access import (
    FaeWorkbenchAccessRepository,
    FaeWorkbenchAccessService,
)
from .control_plane import routes_manage, routes_partner
from .control_plane.audit import AuditWriter
from .control_plane.routes_manage import ManagementRepository, ManagementService
from .control_plane.partner_service import PartnerService
from .control_plane.partner_provider import (
    PartnerAuthenticationBroker,
    PartnerIdentityProvider,
    create_registered_partner_provider,
    partner_provider_release_registered,
)
from .control_plane.partner_release import validate_partner_release
from .control_plane.models import DirectoryFreshness, IdentityMode
from .control_plane.routes_auth import build_auth_router
from .control_plane.routes_access_history import build_access_history_router
from .control_plane.office_recipients import (
    OfficeRecipientDirectoryRepository,
    OfficeRecipientDirectoryService,
    build_office_recipient_router,
)
from .control_plane.auth import (
    AuthSecrets,
    DingTalkWebAuth,
    HardStaleAccessAuditWriter,
    InClientAuthProfile,
    SystemHealthAuditWriter,
    WebSessionRepository,
)
from .control_plane.crypto import IdentityKeyring, ProviderIdentityCodec
from .control_plane.dingtalk import DingTalkClient
from .control_plane.dsn import validate_control_dsn
from .control_plane.identity import IdentityResolver
from .control_plane.in_client_apps import load_trusted_in_client_apps
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
from .fae_workbench import routes as fae_workbench_routes
from .fae_workbench.repository import (
    FaeWorkbenchReadError,
    PsycopgFaeWorkbenchRepository,
    ReplicaFaeWorkbenchRepository,
)
from .fae_workbench.service import FaeWorkbenchService
from .fae_reports.repository import PsycopgFaeReportRepository
from .fae_reports.service import FaeReportService
from .health import routes as health_routes
from .health.platform import (
    build_deployment_status,
    build_detailed_platform_health,
    build_public_platform_health,
)
from .health.poller import HealthCache, poll_loop
from .execution_relay.content_crypto import ContentCodec
from .execution_relay.repository import ExecutionRelayRepository
from .execution_relay.routes import build_execution_relay_router
from .execution_relay.worker_auth import WorkerRequestVerifier
from .agent_brain.authorization import AgentUseAuthorization
from .agent_brain.action_service import ActionCommandService
from .agent_brain.conversation_context import ConversationContextBuilder
from .agent_brain.conversation_projection import ConversationProjection
from .agent_brain.conversation_repository import ConversationRepository
from .agent_brain.conversation_service import ConversationCommandService
from .agent_brain.conversation_routes import (
    ConversationCursorCodec,
    build_conversation_router,
)
from .agent_brain.orchestrator import MissionOrchestrator
from .agent_brain.repository import MissionRepository
from .agent_brain.routes import MissionCursorCodec, build_agent_brain_router
from .agent_catalog.routes import build_agent_catalog_router
from .hr.repository import HrPositionRepository
from .hr.context import HrPositionScope
from .hr.routes import build_hr_position_router
from .hr.service import HrPositionService
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
from .voc_extension.client import VocExtensionClient
from .voc_extension.directory import VocSubmitterDirectory
from .voc_extension.identity import PlatformVocTokenSigner
from .voc_extension.internal_identity import (
    PlatformVocBotSubjectResolver,
    VocServiceAuthorizer,
)
from .voc_extension.internal_routes import build_voc_internal_router
from .voc_extension.routes import build_voc_extension_router


logger = logging.getLogger(__name__)


class _UnavailableFaeWorkbenchRepository:
    def snapshot(self, _period_start, _period_end):
        raise FaeWorkbenchReadError("fae_workbench_query_failed")

    def fae_turn_exists(self, _turn_key: str) -> bool:
        raise FaeWorkbenchReadError("fae_workbench_query_failed")

    def fae_turn_keys(self, _turn_keys: list[str]) -> set[str]:
        raise FaeWorkbenchReadError("fae_workbench_query_failed")


class _UnavailableFaeFeedbackProjectionReader:
    def read_fae_feedback(self, _period_start, _period_end):
        return None


async def cancel_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def agent_brain_loop(
    orchestrator: MissionOrchestrator, *, idle_seconds: float = 1.0
) -> None:
    """Run only while this process owns the PostgreSQL advisory leadership."""

    if idle_seconds <= 0:
        raise ValueError("Agent Brain idle interval must be positive")
    while True:
        try:
            with orchestrator.leader_session() as acquired:
                if not acquired:
                    await asyncio.sleep(idle_seconds)
                    continue
                while True:
                    advancement = asyncio.create_task(
                        asyncio.to_thread(orchestrator.advance_pending, limit=50)
                    )
                    try:
                        advanced = await asyncio.shield(advancement)
                    except asyncio.CancelledError as cancellation:
                        try:
                            await advancement
                        except Exception:
                            logger.exception(
                                "Agent Brain pass failed during shutdown"
                            )
                        raise cancellation
                    if advanced == 0:
                        await asyncio.sleep(idle_seconds)
                    else:
                        await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Agent Brain loop unavailable")
            await asyncio.sleep(idle_seconds)


def _check_execution_relay_database(
    control_database_url: str,
    *,
    connect=psycopg.connect,
) -> None:
    try:
        dsn = validate_control_dsn(control_database_url, purpose="app")
        if dsn.environment != "production":
            raise ValueError
        with connect(
            control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        ) as connection, connection.cursor() as cursor:
            objects = cursor.execute(
                "select "
                "to_regclass('platform_control.execution_workers') as workers,"
                "to_regclass('platform_control.execution_worker_keys') "
                "as worker_keys,"
                "to_regclass('platform_control.execution_jobs') as jobs,"
                "to_regclass('platform_control.execution_events') as events,"
                "to_regclass('platform_control.execution_worker_nonces') "
                "as nonces,"
                "to_regprocedure("
                "'platform_control.touch_execution_worker_v28(text)') "
                "as touch_worker"
            ).fetchone()
            if not objects or any(value is None for value in objects.values()):
                raise ValueError
            privileges = cursor.execute(
                "select "
                "has_schema_privilege(current_user,"
                "'platform_control','usage') as schema_usage,("
                "has_table_privilege(current_user,"
                "'platform_control.execution_workers','select') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_worker_keys','select') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_jobs','select') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_jobs','insert') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_jobs','update') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_events','select') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_events','insert') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_events','update') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_worker_nonces','select') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_worker_nonces','insert') and "
                "has_table_privilege(current_user,"
                "'platform_control.execution_worker_nonces','delete') and "
                "has_function_privilege(current_user,"
                "'platform_control.touch_execution_worker_v28(text)',"
                "'execute')) as ready"
            ).fetchone()
            if (
                not privileges
                or privileges.get("schema_usage") is not True
                or privileges.get("ready") is not True
            ):
                raise ValueError
    except Exception:
        raise RuntimeError("execution relay database unavailable") from None


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
        catalog=catalog,
    )
    repository.operations_repository = ReplicaOperationsRepository(
        database_url,
        cipher=FieldCipher(encryption_key),
        stale_seconds=config.replica_stale_seconds,
        catalog=catalog,
        usage_reader=repository.usage_leaders,
    )
    repository.fae_report_repository = ReplicaFaeReportRepository(
        database_url,
        cipher=FieldCipher(encryption_key),
        stale_seconds=config.replica_stale_seconds,
        catalog=catalog,
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
        include_catalog_agents=True,
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


def build_conversation_attachment_services(config: Config):
    keyring = IdentityKeyring.from_file(
        config.content_encryption_keyring_file,
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    codec = ContentCodec(keyring)
    database_url = read_secret_file(config.attachment_control_database_url_file)
    repository = ConversationAttachmentRepository.from_config(
        config, content_codec=codec
    )
    object_writer = AttachmentObjectWriter.from_config(config)
    immutable_store = S3ImmutableAttachmentStore.from_config(config)
    upload_service = AttachmentUploadService(
        repository,
        object_writer,
        max_file_bytes=config.attachment_max_file_bytes,
    )
    download_service = ConversationAttachmentDownloadService(
        ConversationAttachmentAccessRepository(database_url, content_codec=codec),
        immutable_store,
        ticket_secret=keyring.active_key,
        ticket_seconds=config.attachment_ticket_seconds,
    )
    grant_service = AttachmentGrantService(
        TaskGrantRepository(database_url, content_codec=codec), immutable_store
    )
    artifact_service = ArtifactOutputService(
        ArtifactRepository(
            database_url,
            content_codec=codec,
            upload_ttl_seconds=config.attachment_upload_ttl_seconds,
        ),
        object_writer,
    )
    citation_service = CitationService(
        CitationRepository(database_url, content_codec=codec)
    )
    return (
        upload_service,
        download_service,
        grant_service,
        artifact_service,
        citation_service,
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


def build_office_recipient_directory(
    config: Config,
) -> tuple[OfficeRecipientDirectoryService, str]:
    control = config.control_plane
    database_url = read_secret_file(control.control_database_url_file)
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
    repository = OfficeRecipientDirectoryRepository(
        database_url,
        identity_codec=ProviderIdentityCodec(encryption, lookup),
        corp_id=control.dingtalk_corp_id,
    )
    return (
        OfficeRecipientDirectoryService(repository),
        read_secret_file(config.office_recipient_bearer_file),
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
    repository = WebSessionRepository(
        database_url,
        secrets=auth_secrets,
        identity_codec=codec,
        directory_id=control.dingtalk_corp_id,
    )
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

    registered_applications = (
        load_trusted_in_client_apps(
            control.dingtalk_in_client_apps_file,
            route_prefix=control.route_prefix,
        )
        if control.dingtalk_in_client_apps_file
        else ()
    )
    registered_clients: list[DingTalkClient] = []
    in_client_profiles: list[InClientAuthProfile] = []

    def registered_login(client, resolver):
        async def login(code: str, verifier: str):
            return await resolve(client, resolver, code, verifier)

        return login

    for application in registered_applications:
        client = DingTalkClient(
            app_key=application.app_key,
            app_secret=application.app_secret,
            corp_id=control.dingtalk_corp_id,
            login_flow="in_client",
        )
        resolver = IdentityResolver(
            database_url,
            corp_id=control.dingtalk_corp_id,
            client=client,
            identity_codec=codec,
        )
        registered_clients.append(client)
        in_client_profiles.append(
            InClientAuthProfile(
                application.app_id,
                application.app_key,
                application.return_paths,
                registered_login(client, resolver),
            )
        )

    return DingTalkWebAuth(
        repository=repository,
        secrets=auth_secrets,
        qr_login=qr_login,
        in_client_login=in_client_login,
        environment="preview" if control.mode is IdentityMode.PREVIEW else "production",
        route_prefix=control.route_prefix,
        public_base_url=control.public_base_url,
        app_key=control.dingtalk_app_key,
        in_client_profiles=tuple(in_client_profiles),
        corp_id=control.dingtalk_corp_id,
        state_ttl_seconds=control.oauth_state_ttl_seconds,
        mode=control.mode,
        cookie_name=control.cookie_name,
        rate_limiter=rate_limiter,
        trusted_proxy_networks=tuple(
            ipaddress.ip_network(value, strict=True)
            for value in control.trusted_proxy_cidrs
        ),
        close_callbacks=(
            qr_client.aclose,
            in_client.aclose,
            *(client.aclose for client in registered_clients),
        ),
        hard_stale_audit=(
            HardStaleAccessAuditWriter(
                read_secret_file(control.audit_database_url_file)
            )
            if control.audit_database_url_file
            else None
        ),
        warning_after_seconds=control.warning_after_seconds,
        hard_stale_after_seconds=control.hard_stale_after_seconds,
        voc_bot_subject_resolver=PlatformVocBotSubjectResolver(
            identity_resolver=in_client_resolver,
            directory_freshness=lambda: repository.directory_freshness(
                warning_after_seconds=control.warning_after_seconds,
                hard_stale_after_seconds=control.hard_stale_after_seconds,
            ),
        ),
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
    conversation_attachment_upload_service=None,
    conversation_attachment_download_service=None,
    task_attachment_grant_service=None,
    artifact_output_service=None,
    citation_service=None,
    identity_auth=None,
    voc_extension_client=None,
    voc_submitter_directory=None,
    ai_notes_reader: AiNotesReader | None = None,
    agent_launch_service: AgentLaunchService | None = None,
    partner_service: PartnerService | None = None,
    partner_provider: PartnerIdentityProvider | None = None,
    fae_workbench_service=None,
    fae_report_service=None,
    hr_position_service=None,
    agent_use_authorization=None,
    hr_position_scope=None,
    access_history_repository=None,
) -> FastAPI:
    owns_review_service = review_service is None
    owns_identity_auth = identity_auth is None
    config = load_config()
    owns_voc_extension_client = (
        voc_extension_client is None and config.voc_extension_enabled
    )
    identity_enabled = (
        identity_auth is not None
        or config.control_plane.mode is not IdentityMode.DISABLED
    )
    selected_partner_provider = partner_provider
    if selected_partner_provider is None and config.partner_provider_kind:
        selected_partner_provider = create_registered_partner_provider(
            config.partner_provider_kind
        )
    if selected_partner_provider is not None:
        if config.environment == "production":
            if selected_partner_provider.kind == "reference":
                raise ValueError("partner_reference_provider_forbidden")
            if not partner_provider_release_registered(selected_partner_provider.kind):
                raise ValueError("partner_provider_release_not_registered")
            # Production partner login exists only behind validated Provider
            # release evidence. An injected Provider is not an exemption: the
            # gate must be enabled and the evidence must name a registered,
            # non-reference kind, so nothing can be substituted at startup.
            validate_partner_release(config)
        if (
            config.partner_provider_kind
            and selected_partner_provider.kind != config.partner_provider_kind
        ):
            raise ValueError("partner_provider_kind_mismatch")
    partner_auth_broker = None
    execution_relay_repository = None
    execution_relay_router = None
    agent_brain_orchestrator = None
    mission_repository = None
    conversation_repository = None
    conversation_command_service = None
    action_command_service = None
    office_recipient_router = None
    voc_internal_router = None
    voc_service_authorizer = None
    control_database_url = None
    content_codec = None
    v1_mission_modes: list[str] = []
    if config.office_recipient_directory_enabled:
        office_recipient_service, office_recipient_bearer = (
            build_office_recipient_directory(config)
        )
        office_recipient_router = build_office_recipient_router(
            office_recipient_service,
            bearer_secret=office_recipient_bearer,
        )
    if config.execution_relay_enabled:
        control_database_url = read_secret_file(
            config.control_plane.control_database_url_file
        )
        _check_execution_relay_database(control_database_url)
        content_keyring = IdentityKeyring.from_file(
            config.content_encryption_keyring_file,
            expected_purpose="platform-content-encryption",
            expected_key_length=32,
        )
        content_codec = ContentCodec(content_keyring)
        execution_relay_repository = ExecutionRelayRepository(
            control_database_url,
            content_codec=content_codec,
        )
        execution_relay_router = build_execution_relay_router(
            execution_relay_repository,
            WorkerRequestVerifier(control_database_url),
            lease_seconds=config.execution_relay_lease_seconds,
            max_body_bytes=config.execution_relay_max_body_bytes,
        )
    if config.direct_agent_enabled or config.agent_brain_enabled:
        if (
            control_database_url is None
            or content_codec is None
            or execution_relay_repository is None
        ):
            raise RuntimeError("Agent conversation execution unavailable")
        mission_repository = MissionRepository(
            control_database_url, content_codec=content_codec
        )
        conversation_repository = ConversationRepository(
            control_database_url,
            content_codec=content_codec,
            mission_repository=mission_repository,
        )
        conversation_command_service = ConversationCommandService(
            conversation_repository,
            v2_enabled=config.agent_brain_v2_enabled,
        )
        action_command_service = ActionCommandService(
            control_database_url,
            content_codec=content_codec,
            dsn_purpose="app",
        )
        agent_use_authorization = AgentUseAuthorization(control_database_url)
    if config.direct_agent_enabled:
        v1_mission_modes.append("direct_agent")
    if config.agent_brain_enabled and not config.agent_brain_v2_enabled:
        v1_mission_modes.append("brain")
    if identity_enabled and identity_auth is None:
        identity_auth = build_identity_auth(config)
    if config.partner_provider_kind and (
        not identity_enabled or partner_service is None
    ):
        raise RuntimeError("partner_identity_unavailable")
    if (
        identity_enabled
        and selected_partner_provider is not None
        and partner_service is not None
    ):
        partner_auth_broker = PartnerAuthenticationBroker(
            selected_partner_provider,
            partner_service,
            state_secrets=identity_auth.secrets,
        )
    if identity_enabled and agent_launch_service is None and owns_identity_auth:
        if control_database_url is None:
            control_database_url = read_secret_file(
                config.control_plane.control_database_url_file
            )
        if agent_use_authorization is None:
            agent_use_authorization = AgentUseAuthorization(control_database_url)
        agent_launch_service = AgentLaunchService(
            repository=AgentLaunchRepository(control_database_url),
            secrets=identity_auth.secrets,
            authorization=agent_use_authorization,
            partner_service=partner_service,
            partner_provider=selected_partner_provider,
        )
    if identity_enabled and ai_notes_reader is None:
        try:
            ai_notes_reader = AiNotesRepository.load(
                Path(__file__).resolve().parent / "ai_notes" / "content",
                today=date.today(),
            )
        except AiNotesContentError:
            ai_notes_reader = UnavailableAiNotesReader()
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
                # The cloud has one refresh unit — the replica import — instead
                # of the six local poller groups. A quarter of the staleness
                # budget as the interval means the Brief reports `stale` (at
                # half the budget) while the projection reader still serves
                # data, so the warning is visible before reads start failing.
                operations_service = OperationsService(
                    cloud_operations_repository,
                    intervals={
                        "replica_import": max(
                            cloud_operations_repository.stale_after_seconds / 4, 1.0
                        )
                    },
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
    if fae_workbench_service is None:
        if cloud_mode and replica_repository is not None:
            fae_repository = ReplicaFaeWorkbenchRepository(
                replica_repository,
            )
        elif database_url:
            fae_repository = PsycopgFaeWorkbenchRepository(database_url)
        else:
            fae_repository = _UnavailableFaeWorkbenchRepository()
        fae_workbench_service = FaeWorkbenchService(
            fae_repository,
            observability_service,
            review_service,
        )
    if fae_report_service is None:
        if cloud_mode and replica_repository is not None:
            report_repository = getattr(
                replica_repository, "fae_report_repository", None
            )
        else:
            report_repository = (
                PsycopgFaeReportRepository(database_url) if database_url else None
            )
        fae_report_service = (
            FaeReportService(
                report_repository,
                latest_source_sync=getattr(
                    report_repository, "latest_source_sync", None
                ),
            )
            if report_repository is not None
            else None
        )
    if fae_report_service is not None:
        fae_workbench_service.attach_report_service(fae_report_service)
    if attachment_service is None and config.attachment_enabled and not cloud_mode:
        attachment_service = build_attachment_service(config)
    if (
        identity_enabled
        and config.conversation_attachment_enabled
        and all(
            service is None
            for service in (
                conversation_attachment_upload_service,
                conversation_attachment_download_service,
                task_attachment_grant_service,
                artifact_output_service,
                citation_service,
            )
        )
    ):
        (
            conversation_attachment_upload_service,
            conversation_attachment_download_service,
            task_attachment_grant_service,
            artifact_output_service,
            citation_service,
        ) = build_conversation_attachment_services(config)
    attachment_services = (
        conversation_attachment_upload_service,
        conversation_attachment_download_service,
        task_attachment_grant_service,
        artifact_output_service,
        citation_service,
    )
    if any(service is not None for service in attachment_services) and not all(
        service is not None for service in attachment_services
    ):
        raise RuntimeError("conversation attachment services unavailable")
    if v1_mission_modes:
        if (
            mission_repository is None
            or conversation_repository is None
            or execution_relay_repository is None
            or agent_use_authorization is None
        ):
            raise RuntimeError("Agent Brain unavailable")
        agent_brain_orchestrator = MissionOrchestrator(
            mission_repository,
            execution_relay_repository,
            capability_provider=agent_use_authorization.permitted_agents_for_user_id,
            conversation_context_builder=ConversationContextBuilder(
                conversation_repository
            ),
            conversation_projection=ConversationProjection(
                conversation_repository,
                result_projection=(
                    ConversationResultProjection(content_codec=content_codec)
                    if task_attachment_grant_service is not None
                    and citation_service is not None
                    else None
                ),
            ),
            mission_modes=tuple(v1_mission_modes),
            attachment_grants=task_attachment_grant_service,
        )
        agent_brain_orchestrator.check_ready()
    if (
        attachment_service is not None
        or conversation_attachment_download_service is not None
    ):
        install_attachment_ticket_redaction()
    if owns_voc_extension_client:
        voc_extension_client = VocExtensionClient(
            config.voc_extension_base_url,
            PlatformVocTokenSigner.from_file(
                config.voc_extension_signing_key_file
            ),
            timeout_seconds=config.voc_extension_timeout_seconds,
        )
    if (
        voc_extension_client is not None
        and identity_enabled
        and voc_submitter_directory is None
    ):
        if control_database_url is None:
            control_database_url = read_secret_file(
                config.control_plane.control_database_url_file
            )
        voc_submitter_directory = VocSubmitterDirectory(control_database_url)
    if config.voc_extension_enabled and identity_enabled:
        bot_subject_resolver = getattr(
            identity_auth, "voc_bot_subject_resolver", None
        )
        if (
            identity_auth is None
            or voc_submitter_directory is None
            or bot_subject_resolver is None
        ):
            raise RuntimeError("VOC internal identity unavailable")
        voc_service_authorizer = VocServiceAuthorizer(
            read_secret_file(
                config.control_plane.voc_service_bearer_file, max_bytes=16_384
            ).encode("utf-8")
        )
        voc_internal_router = build_voc_internal_router(
            auth=identity_auth,
            directory=voc_submitter_directory,
            bearer=voc_service_authorizer,
            bot_subject_resolver=bot_subject_resolver,
        )

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
        # Brain V2 is executed by the private durable worker. Direct-Agent
        # Missions retain the V1 relay state machine and are filtered at claim
        # time, so they continue while Brain itself is disabled or on V2.
        if agent_brain_orchestrator is not None:
            tasks.append(asyncio.create_task(agent_brain_loop(agent_brain_orchestrator)))
        try:
            yield
        finally:
            await cancel_tasks(tasks)
            if owns_review_service:
                await review_service.close()
            if owns_identity_auth and identity_auth is not None:
                await identity_auth.aclose()
            if owns_voc_extension_client and voc_extension_client is not None:
                await voc_extension_client.aclose()

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
    app.state.conversation_attachment_upload_service = (
        conversation_attachment_upload_service
    )
    app.state.conversation_attachment_download_service = (
        conversation_attachment_download_service
    )
    app.state.task_attachment_grant_service = task_attachment_grant_service
    app.state.artifact_output_service = artifact_output_service
    app.state.citation_service = citation_service
    app.state.replica_repository = replica_repository
    app.state.identity_auth = identity_auth
    app.state.execution_relay_repository = execution_relay_repository
    app.state.agent_brain_orchestrator = agent_brain_orchestrator
    app.state.mission_repository = mission_repository
    app.state.conversation_repository = conversation_repository
    app.state.conversation_command_service = conversation_command_service
    app.state.action_command_service = action_command_service
    app.state.agent_use_authorization = agent_use_authorization
    app.state.voc_extension_client = voc_extension_client
    app.state.voc_submitter_directory = voc_submitter_directory
    app.state.voc_service_authorizer = voc_service_authorizer
    app.state.ai_notes_reader = ai_notes_reader
    app.state.agent_launch_service = agent_launch_service
    app.state.partner_service = partner_service
    app.state.partner_provider = selected_partner_provider
    app.state.partner_auth_broker = partner_auth_broker
    app.state.fae_workbench_service = fae_workbench_service
    app.state.fae_report_service = fae_report_service
    app.state.hr_position_service = hr_position_service
    app.state.fae_access = None
    app.state.fae_session_read_audit = None
    authorization_service = None
    if identity_enabled and config.control_plane.audit_database_url_file:
        control_database_url = read_secret_file(
            config.control_plane.control_database_url_file
        )
        audit_database_url = read_secret_file(
            config.control_plane.audit_database_url_file
        )
        app.state.system_health_audit = SystemHealthAuditWriter(audit_database_url)
        audit_writer = AuditWriter.from_database_url(audit_database_url)
        app.state.fae_session_read_audit = audit_writer
        app.state.identity_management_service = ManagementService(
            ManagementRepository(control_database_url),
            audit_writer,
            hard_stale_audit=identity_auth.hard_stale_audit,
        )
        app.state.fae_access = FaeWorkbenchAccessService(
            FaeWorkbenchAccessRepository(control_database_url),
            audit_writer,
            cloud_mode=cloud_mode,
        )
        authorization_service = AuthorizationService(
            AuthorizationRepository(control_database_url),
            cloud_mode=cloud_mode,
            read_audit=AuthorizationReadAuditWriter(audit_database_url),
        )
        if access_history_repository is None:
            access_history_repository = AccessHistoryRepository(
                control_database_url
            )
        if agent_use_authorization is None:
            agent_use_authorization = AgentUseAuthorization(control_database_url)
            app.state.agent_use_authorization = agent_use_authorization

    if (
        hr_position_service is None
        and identity_enabled
        and control_database_url is not None
        and agent_use_authorization is not None
    ):
        hr_position_repository = HrPositionRepository(control_database_url)
        hr_position_service = HrPositionService(hr_position_repository)
        hr_position_scope = HrPositionScope(hr_position_repository)
    app.state.hr_position_service = hr_position_service
    app.state.hr_position_scope = hr_position_scope
    app.state.agent_use_authorization = agent_use_authorization
    if (
        artifact_output_service is not None
        and hr_position_scope is not None
        and callable(getattr(artifact_output_service, "set_position_linker", None))
    ):
        artifact_output_service.set_position_linker(hr_position_scope)

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
    app.include_router(fae_workbench_routes.router, prefix="/api/fae")
    app.include_router(
        fae_workbench_routes.router,
        prefix="/api/admin/fae",
        include_in_schema=False,
    )
    app.include_router(build_voc_extension_router())
    if voc_internal_router is not None:
        app.include_router(voc_internal_router)
    if office_recipient_router is not None:
        app.include_router(office_recipient_router)
    if agent_launch_service is not None:
        app.include_router(build_agent_launch_router(agent_launch_service))
    if identity_enabled and ai_notes_reader is not None:
        app.include_router(build_ai_notes_router(ai_notes_reader))
    if execution_relay_router is not None:
        app.include_router(execution_relay_router)
    if agent_use_authorization is not None:
        app.include_router(build_agent_catalog_router(agent_use_authorization))
    if hr_position_service is not None and agent_use_authorization is not None:
        app.include_router(
            build_hr_position_router(hr_position_service, agent_use_authorization)
        )
    if mission_repository is not None and agent_use_authorization is not None:
        app.include_router(
            build_conversation_router(
                conversation_repository,
                agent_use_authorization,
                command_service=conversation_command_service,
                action_service=action_command_service,
                cursor_codec=ConversationCursorCodec(identity_auth.secrets),
                session_revalidator=identity_auth.authenticate,
                session_cookie_name=identity_auth.cookie_name,
                brain_enabled=config.agent_brain_enabled,
                hr_position_scope=hr_position_scope,
            )
        )
    if (
        config.agent_brain_enabled
        and mission_repository is not None
        and agent_use_authorization is not None
    ):
        app.include_router(
            build_agent_brain_router(
                mission_repository,
                agent_use_authorization,
                cursor_codec=MissionCursorCodec(identity_auth.secrets),
                session_revalidator=identity_auth.authenticate,
                session_cookie_name=identity_auth.cookie_name,
            )
        )
    if identity_enabled:
        if access_history_repository is None:
            access_history_repository = UnavailableAccessHistoryRepository()
        app.include_router(build_access_history_router(access_history_repository))
        app.include_router(routes_manage.router)
        app.include_router(routes_partner.router)
        if partner_auth_broker is not None:
            app.include_router(
                routes_partner.build_partner_auth_router(
                    partner_auth_broker,
                    agent_launch_service=agent_launch_service,
                    callback_method=config.partner_callback_method,
                    callback_path=config.partner_callback_path,
                )
            )

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
    if (
        conversation_attachment_upload_service is not None
        and conversation_attachment_download_service is not None
        and task_attachment_grant_service is not None
        and artifact_output_service is not None
        and citation_service is not None
    ):
        app.include_router(build_conversation_attachment_router())

    if os.path.isdir(config.static_dir) and not identity_enabled:
        app.mount("/", SpaStaticFiles(directory=config.static_dir, html=True), name="portal")

    if identity_enabled:
        app.add_middleware(
            IdentitySecurityMiddleware,
            auth=identity_auth,
            public_assets=public_assets,
            authorization=authorization_service,
            routes=tuple(app.router.routes),
            voc_service_authorizer=voc_service_authorizer,
            partner_callback_method=(
                config.partner_callback_method
                if partner_auth_broker is not None
                else None
            ),
            partner_callback_path=(
                config.partner_callback_path
                if partner_auth_broker is not None
                else None
            ),
        )
    if not config.execution_relay_enabled:
        app.add_middleware(DisabledExecutionWorkerNamespaceMiddleware)

    return app


app = create_app() if os.getenv("PLATFORM_EAGER_APP") else None
