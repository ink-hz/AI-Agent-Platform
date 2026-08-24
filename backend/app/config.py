import os
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import stat
from typing import Literal
from urllib.parse import urlparse

from .control_plane.models import ControlPlaneConfig, IdentityMode
from .control_plane.crypto import IdentityCryptoError, IdentityKeyring
from .local_secrets import SecretFileUnavailable, read_secret_file


DEFAULT_SECRETS_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "OrbbecAI-Agent-Platform"
    / "secrets"
)


@dataclass(frozen=True)
class Config:
    deployment_mode: Literal["local", "cloud-replica"]
    cloud_auth_mode: Literal["ssh-tunnel", "basic-auth", "dingtalk"]
    registry_path: str
    metabot_contract_path: str
    poll_interval_seconds: float
    cluster_poll_interval_seconds: float
    probe_timeout_seconds: float
    static_dir: str
    host: str
    port: int
    flywheel_enabled: bool
    flywheel_database_url: str | None
    flywheel_database_url_file: str
    review_enabled: bool
    review_database_url: str | None
    review_database_url_file: str
    review_request_timeout_seconds: float
    usage_cache_seconds: float
    active_window_minutes: int
    sync_database_url: str | None
    sync_database_url_file: str
    remote_ssh_host: str
    remote_ssh_key_path: str
    remote_poll_interval_seconds: float
    feedback_closure_outbox_dir: str
    operations_database_path: str
    operations_usage_interval_seconds: float
    operations_execution_interval_seconds: float
    operations_lifecycle_interval_seconds: float
    attachment_enabled: bool
    attachment_s3_endpoint: str
    attachment_s3_bucket: str
    attachment_s3_access_key_file: str
    attachment_s3_secret_key_file: str
    attachment_ticket_seconds: int
    trusted_attachment_proxy: bool
    replica_database_url_file: str
    replica_encryption_key_file: str
    replica_signing_public_key_file: str
    replica_stale_seconds: int
    execution_relay_enabled: bool
    agent_brain_enabled: bool
    agent_brain_v2_enabled: bool
    brain_model_enabled: bool
    brain_provider_base_url: str
    brain_provider_api_key_file: str
    brain_model_manifest_path: str
    content_encryption_keyring_file: str
    execution_relay_lease_seconds: int
    execution_relay_max_body_bytes: int
    control_plane: ControlPlaneConfig


def _enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default) not in {"0", "false", "False"}


def _loopback(value: str) -> bool:
    host = value.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _mode_0600_secret(path: str) -> str:
    try:
        if stat.S_IMODE(Path(path).lstat().st_mode) != 0o600:
            raise RuntimeError("attachment secret files must use mode 0600")
        return read_secret_file(path)
    except RuntimeError:
        raise
    except (OSError, SecretFileUnavailable) as error:
        raise RuntimeError("attachment secret files must use mode 0600") from error


def _validate_attachment_config(config: Config) -> None:
    if not config.attachment_enabled:
        return
    endpoint = urlparse(config.attachment_s3_endpoint)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
        raise RuntimeError("attachment S3 endpoint must be a loopback URL")
    if not _loopback(endpoint.hostname):
        raise RuntimeError("attachment S3 endpoint must be loopback")
    if config.attachment_s3_bucket != "orbbec-agent-attachments":
        raise RuntimeError("attachment S3 bucket must be orbbec-agent-attachments")
    _mode_0600_secret(config.attachment_s3_access_key_file)
    _mode_0600_secret(config.attachment_s3_secret_key_file)
    if not config.trusted_attachment_proxy and not _loopback(config.host):
        raise RuntimeError(
            "non-loopback Platform host requires a trusted attachment proxy"
        )
    if config.flywheel_database_url:
        raise RuntimeError(
            "attachment database credentials must use a mode 0600 file"
        )
    try:
        database_url = _mode_0600_secret(config.flywheel_database_url_file)
    except RuntimeError as error:
        raise RuntimeError(
            "attachment access requires the Flywheel analyst DSN"
        ) from error
    if urlparse(database_url).username != "flywheel_analyst":
        raise RuntimeError("attachment access requires the Flywheel analyst DSN")


def _validate_private_file(path_value: str, label: str) -> None:
    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeError(f"{label} must use an absolute path")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError(f"{label} must be a regular mode 0600 file") from error
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"{label} must be a regular mode 0600 file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError(f"{label} must use mode 0600")
    if metadata.st_uid != os.getuid():
        raise RuntimeError(f"{label} must be owned by the service user")


_INLINE_CONTROL_SECRET_ENV = (
    "PLATFORM_CONTROL_DATABASE_URL",
    "PLATFORM_CONTROL_AUDIT_DATABASE_URL",
    "PLATFORM_DINGTALK_APP_SECRET",
    "PLATFORM_IDENTITY_ENCRYPTION_KEYRING",
    "PLATFORM_IDENTITY_HMAC_KEYRING",
    "PLATFORM_RATE_LIMIT_HMAC_KEYRING",
)


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required when identity is enabled")
    return value


def _positive_environment_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _normalize_route_prefix(value: str) -> str:
    if not value.startswith("/"):
        raise ValueError("PLATFORM_ROUTE_PREFIX must be an absolute URL path")
    parts = value.split("/")
    if any(part in {".", ".."} for part in parts):
        raise ValueError("PLATFORM_ROUTE_PREFIX must not contain dot segments")
    normalized = "/" + "/".join(part for part in parts if part)
    return "/" if normalized == "/" else f"{normalized}/"


_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _validate_public_hostname(hostname: str, *, bracketed: bool) -> None:
    if bracketed:
        ipaddress.IPv6Address(hostname)
        return

    try:
        ipaddress.IPv4Address(hostname)
        return
    except ValueError:
        pass

    dns_hostname = hostname[:-1] if hostname.endswith(".") else hostname
    if (
        not dns_hostname
        or len(dns_hostname) > 253
        or all(character in "0123456789." for character in dns_hostname)
        or any(_DNS_LABEL.fullmatch(label) is None for label in dns_hostname.split("."))
    ):
        raise ValueError("invalid public hostname")


def _validate_public_authority(authority: str) -> None:
    if (
        not authority
        or "@" in authority
        or "\\" in authority
        or "%" in authority
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in authority)
    ):
        raise ValueError("invalid public authority")

    bracketed = authority.startswith("[")
    if bracketed:
        closing_bracket = authority.find("]")
        if closing_bracket < 0:
            raise ValueError("invalid public authority")
        hostname = authority[1:closing_bracket]
        suffix = authority[closing_bracket + 1 :]
        if "[" in hostname or "]" in suffix:
            raise ValueError("invalid public authority")
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("invalid public authority")
            port = suffix[1:]
        else:
            port = None
    else:
        if "[" in authority or "]" in authority or authority.count(":") > 1:
            raise ValueError("invalid public authority")
        hostname, separator, port = authority.partition(":")
        if not separator:
            port = None

    _validate_public_hostname(hostname, bracketed=bracketed)
    if port is not None and (
        not port.isascii()
        or not port.isdecimal()
        or not 1 <= int(port) <= 65_535
    ):
        raise ValueError("invalid public port")


def _validate_public_base_url(value: str) -> None:
    try:
        if any(
            character in "\\%" or ord(character) <= 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise ValueError("invalid public URL character")
        parsed = urlparse(value)
        _validate_public_authority(parsed.netloc)
    except ValueError as error:
        raise ValueError(
            "PLATFORM_PUBLIC_BASE_URL must be a safe HTTPS origin"
        ) from error
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PLATFORM_PUBLIC_BASE_URL must be a safe HTTPS origin")


def _trusted_proxy_cidrs(value: str) -> tuple[str, ...]:
    cidrs = tuple(part.strip() for part in value.split(",") if part.strip())
    if not cidrs:
        raise ValueError("PLATFORM_TRUSTED_PROXY_CIDRS must not be empty")
    try:
        networks = tuple(ipaddress.ip_network(cidr, strict=True) for cidr in cidrs)
    except ValueError as error:
        raise ValueError("PLATFORM_TRUSTED_PROXY_CIDRS must contain valid CIDRs") from error
    if not all(network.num_addresses == 1 for network in networks):
        raise ValueError("trusted proxy CIDRs must be exact host networks")
    return cidrs


def _load_control_plane_config() -> ControlPlaneConfig:
    try:
        mode = IdentityMode(os.getenv("PLATFORM_IDENTITY_MODE", "disabled"))
    except ValueError as error:
        raise ValueError(
            "PLATFORM_IDENTITY_MODE must be disabled, preview, or production"
        ) from error

    inline_secrets = [name for name in _INLINE_CONTROL_SECRET_ENV if os.getenv(name)]
    if inline_secrets:
        raise ValueError("identity credentials must use secret files, not environment values")

    if mode is IdentityMode.DISABLED:
        return ControlPlaneConfig(
            mode=mode,
            control_database_url_file="",
            audit_database_url_file="",
            public_base_url="",
            route_prefix="/",
            cookie_name="",
            dingtalk_app_key="",
            dingtalk_agent_id="",
            dingtalk_corp_id="",
            dingtalk_app_secret_file="",
            encryption_keyring_file="",
            hmac_keyring_file="",
        )

    control_database_url_file = _required_environment(
        "PLATFORM_CONTROL_DATABASE_URL_FILE"
    )
    audit_database_url_file = _required_environment(
        "PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE"
    )
    dingtalk_app_secret_file = _required_environment(
        "PLATFORM_DINGTALK_APP_SECRET_FILE"
    )
    encryption_keyring_file = _required_environment(
        "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE"
    )
    hmac_keyring_file = _required_environment("PLATFORM_IDENTITY_HMAC_KEYRING_FILE")
    rate_limit_hmac_keyring_file = _required_environment(
        "PLATFORM_RATE_LIMIT_HMAC_KEYRING_FILE"
    )
    if Path(rate_limit_hmac_keyring_file) == Path(hmac_keyring_file):
        raise ValueError(
            "rate limit HMAC keyring must be distinct from identity HMAC keyring"
        )
    public_base_url = _required_environment("PLATFORM_PUBLIC_BASE_URL")
    route_prefix = _normalize_route_prefix(
        _required_environment("PLATFORM_ROUTE_PREFIX")
    )
    cookie_name = _required_environment("PLATFORM_COOKIE_NAME")
    _validate_public_base_url(public_base_url)

    if cookie_name.startswith("__Host-") and route_prefix != "/":
        raise ValueError("__Host- cookies require Path=/")
    if mode is IdentityMode.PREVIEW:
        if route_prefix != "/_preview/dingtalk-r1/":
            raise ValueError("preview PLATFORM_ROUTE_PREFIX must be /_preview/dingtalk-r1/")
        if cookie_name != "platform_preview_session":
            raise ValueError("preview Cookie name must be platform_preview_session")
    elif route_prefix != "/":
        raise ValueError("production PLATFORM_ROUTE_PREFIX must be /")
    elif cookie_name != "__Host-platform_session":
        raise ValueError("production Cookie name must be __Host-platform_session")

    private_files = (
        (control_database_url_file, "control database secret"),
        (audit_database_url_file, "control audit database secret"),
        (dingtalk_app_secret_file, "DingTalk AppSecret"),
        (encryption_keyring_file, "identity encryption keyring"),
        (hmac_keyring_file, "identity HMAC keyring"),
        (rate_limit_hmac_keyring_file, "rate limit HMAC keyring"),
    )
    for path, label in private_files:
        _validate_private_file(path, label)
    if Path(rate_limit_hmac_keyring_file).samefile(hmac_keyring_file):
        raise ValueError(
            "rate limit HMAC keyring must be distinct from identity HMAC keyring"
        )

    reconcile_interval_seconds = _positive_environment_int(
        "PLATFORM_IDENTITY_RECONCILE_INTERVAL_SECONDS", 21_600
    )
    warning_after_seconds = _positive_environment_int(
        "PLATFORM_IDENTITY_WARNING_AFTER_SECONDS", 28_800
    )
    hard_stale_after_seconds = _positive_environment_int(
        "PLATFORM_IDENTITY_HARD_STALE_AFTER_SECONDS", 86_400
    )
    if not (
        0
        < reconcile_interval_seconds
        < warning_after_seconds
        < hard_stale_after_seconds
    ):
        raise ValueError("identity freshness thresholds must be positive and ordered")

    return ControlPlaneConfig(
        mode=mode,
        control_database_url_file=control_database_url_file,
        audit_database_url_file=audit_database_url_file,
        public_base_url=public_base_url.rstrip("/"),
        route_prefix=route_prefix,
        cookie_name=cookie_name,
        dingtalk_app_key=_required_environment("PLATFORM_DINGTALK_APP_KEY"),
        dingtalk_agent_id=_required_environment("PLATFORM_DINGTALK_AGENT_ID"),
        dingtalk_corp_id=_required_environment("PLATFORM_DINGTALK_CORP_ID"),
        dingtalk_app_secret_file=dingtalk_app_secret_file,
        encryption_keyring_file=encryption_keyring_file,
        hmac_keyring_file=hmac_keyring_file,
        rate_limit_hmac_keyring_file=rate_limit_hmac_keyring_file,
        reconcile_interval_seconds=reconcile_interval_seconds,
        warning_after_seconds=warning_after_seconds,
        hard_stale_after_seconds=hard_stale_after_seconds,
        trusted_proxy_cidrs=_trusted_proxy_cidrs(
            os.getenv("PLATFORM_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128")
        ),
        login_starts_per_challenge=_positive_environment_int(
            "PLATFORM_LOGIN_STARTS_PER_CHALLENGE", 5
        ),
        login_challenge_window_seconds=_positive_environment_int(
            "PLATFORM_LOGIN_CHALLENGE_WINDOW_SECONDS", 600
        ),
        active_login_attempts=_positive_environment_int(
            "PLATFORM_ACTIVE_LOGIN_ATTEMPTS", 3
        ),
        oauth_state_ttl_seconds=_positive_environment_int(
            "PLATFORM_OAUTH_STATE_TTL_SECONDS", 300
        ),
        edge_login_starts_per_minute=_positive_environment_int(
            "PLATFORM_EDGE_LOGIN_STARTS_PER_MINUTE", 600
        ),
        edge_login_burst=_positive_environment_int(
            "PLATFORM_EDGE_LOGIN_BURST", 1_200
        ),
        edge_callbacks_per_minute=_positive_environment_int(
            "PLATFORM_EDGE_CALLBACKS_PER_MINUTE", 1_200
        ),
        oauth_exchange_concurrency=_positive_environment_int(
            "PLATFORM_OAUTH_EXCHANGE_CONCURRENCY", 100
        ),
        oauth_exchanges_per_minute=_positive_environment_int(
            "PLATFORM_OAUTH_EXCHANGES_PER_MINUTE", 3_000
        ),
        authenticated_reads_per_minute=_positive_environment_int(
            "PLATFORM_AUTHENTICATED_READS_PER_MINUTE", 300
        ),
        authenticated_mutations_per_minute=_positive_environment_int(
            "PLATFORM_AUTHENTICATED_MUTATIONS_PER_MINUTE", 60
        ),
    )


def _validate_cloud_config(config: Config) -> None:
    if config.deployment_mode not in {"local", "cloud-replica"}:
        raise RuntimeError("unsupported deployment mode")
    if config.deployment_mode != "cloud-replica":
        return
    if config.cloud_auth_mode not in {"ssh-tunnel", "basic-auth", "dingtalk"}:
        raise RuntimeError("unsupported cloud authentication mode")
    if not _loopback(config.host):
        raise RuntimeError("cloud replica host must be loopback")
    if config.flywheel_enabled:
        raise RuntimeError("cloud replica must not enable Flywheel access")
    if config.review_enabled:
        raise RuntimeError("cloud replica must not enable Review")
    if config.attachment_enabled:
        raise RuntimeError("cloud replica must not enable attachments")
    if config.replica_stale_seconds != 900:
        raise RuntimeError("cloud replica stale threshold must be exactly 900 seconds")
    _validate_private_file(
        config.replica_database_url_file, "replica database secret"
    )
    _validate_private_file(
        config.replica_encryption_key_file, "replica encryption secret"
    )
    _validate_private_file(
        config.replica_signing_public_key_file, "replica signing public key"
    )


def _execution_relay_settings() -> tuple[bool, str, int, int]:
    enabled = _enabled("PLATFORM_EXECUTION_RELAY_ENABLED")
    if not enabled:
        return False, "", 45, 1_048_576
    keyring_file = os.getenv(
        "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE", ""
    ).strip()
    if not keyring_file:
        raise ValueError(
            "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE is required when "
            "execution relay is enabled"
        )
    lease_seconds = _positive_environment_int(
        "PLATFORM_EXECUTION_RELAY_LEASE_SECONDS", 45
    )
    max_body_bytes = _positive_environment_int(
        "PLATFORM_EXECUTION_RELAY_MAX_BODY_BYTES", 1_048_576
    )
    if max_body_bytes > 1_048_576:
        raise ValueError(
            "PLATFORM_EXECUTION_RELAY_MAX_BODY_BYTES must not exceed 1048576"
        )
    return enabled, keyring_file, lease_seconds, max_body_bytes


def _validate_execution_relay_config(config: Config) -> None:
    if not config.execution_relay_enabled:
        return
    if (
        config.control_plane.mode is not IdentityMode.PRODUCTION
        or config.control_plane.route_prefix != "/"
    ):
        raise ValueError("execution relay requires production identity at root")
    _validate_private_file(
        config.content_encryption_keyring_file,
        "content encryption keyring",
    )
    try:
        IdentityKeyring.from_file(
            config.content_encryption_keyring_file,
            expected_purpose="platform-content-encryption",
            expected_key_length=32,
        )
    except IdentityCryptoError:
        raise RuntimeError("content encryption keyring unavailable") from None


def _validate_agent_brain_config(config: Config) -> None:
    if config.agent_brain_v2_enabled and not config.agent_brain_enabled:
        raise ValueError("Agent Brain V2 requires Agent Brain")
    if not config.agent_brain_enabled:
        return
    if (
        config.control_plane.mode is not IdentityMode.PRODUCTION
        or not config.execution_relay_enabled
    ):
        raise ValueError(
            "Agent Brain requires production identity and relay"
        )


def _validate_brain_model_config(config: Config) -> None:
    if os.getenv("PLATFORM_BRAIN_PROVIDER_API_KEY"):
        raise ValueError("Brain Provider credentials must use a secret file")
    if not config.brain_model_enabled:
        return
    if not config.agent_brain_enabled:
        raise ValueError("Brain model runtime requires Agent Brain")
    try:
        _validate_public_base_url(config.brain_provider_base_url)
    except ValueError:
        raise ValueError("Brain Provider base URL must be a safe HTTPS origin") from None
    _validate_private_file(
        config.brain_provider_api_key_file,
        "Brain Provider API key",
    )
    manifest = Path(config.brain_model_manifest_path)
    if not manifest.is_absolute() or not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError("Brain model manifest must be an absolute regular file")


def is_cloud_mode(config: Config) -> bool:
    return config.deployment_mode == "cloud-replica"


def _feedback_closure_outbox_dir() -> str:
    configured = os.getenv("PLATFORM_FEEDBACK_CLOSURE_OUTBOX_DIR")
    path = (
        Path(configured).expanduser()
        if configured
        else Path.home()
        / "Library/Application Support/OrbbecAI-Agent-Platform"
        / "feedback-closure-outbox"
    )
    if not path.is_absolute():
        raise RuntimeError("feedback closure outbox path must be absolute")
    return str(path)


def load_config() -> Config:
    (
        execution_relay_enabled,
        content_encryption_keyring_file,
        execution_relay_lease_seconds,
        execution_relay_max_body_bytes,
    ) = _execution_relay_settings()
    config = Config(
        deployment_mode=os.getenv("PLATFORM_DEPLOYMENT_MODE", "local"),
        cloud_auth_mode=os.getenv("PLATFORM_CLOUD_AUTH_MODE", "ssh-tunnel"),
        registry_path=os.getenv("PLATFORM_REGISTRY_PATH", "../registry.yaml"),
        metabot_contract_path=os.getenv(
            "PLATFORM_METABOT_CONTRACT_PATH",
            "/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json",
        ),
        poll_interval_seconds=float(os.getenv("PLATFORM_POLL_INTERVAL", "30")),
        cluster_poll_interval_seconds=float(
            os.getenv("PLATFORM_CLUSTER_POLL_INTERVAL", "10")
        ),
        probe_timeout_seconds=float(os.getenv("PLATFORM_PROBE_TIMEOUT", "3")),
        static_dir=os.getenv("PLATFORM_STATIC_DIR", "app/static"),
        host=os.getenv("PLATFORM_HOST", "0.0.0.0"),
        port=int(os.getenv("PLATFORM_PORT", "80")),
        flywheel_enabled=os.getenv("PLATFORM_FLYWHEEL_ENABLED", "1") not in {
            "0",
            "false",
            "False",
        },
        flywheel_database_url=os.getenv("PLATFORM_FLYWHEEL_DATABASE_URL"),
        flywheel_database_url_file=os.getenv(
            "PLATFORM_FLYWHEEL_DATABASE_URL_FILE",
            str(DEFAULT_SECRETS_DIR / "flywheel-analyst-database-url"),
        ),
        review_enabled=os.getenv("PLATFORM_REVIEW_ENABLED", "1") not in {
            "0",
            "false",
            "False",
        },
        review_database_url=os.getenv("PLATFORM_REVIEW_DATABASE_URL"),
        review_database_url_file=os.getenv(
            "PLATFORM_REVIEW_DATABASE_URL_FILE",
            str(DEFAULT_SECRETS_DIR / "platform-review-writer-database-url"),
        ),
        review_request_timeout_seconds=float(
            os.getenv("PLATFORM_REVIEW_REQUEST_TIMEOUT", "1200")
        ),
        usage_cache_seconds=float(os.getenv("PLATFORM_USAGE_CACHE_SECONDS", "60")),
        active_window_minutes=int(os.getenv("PLATFORM_ACTIVE_WINDOW_MINUTES", "15")),
        sync_database_url=os.getenv("PLATFORM_SYNC_DATABASE_URL"),
        sync_database_url_file=os.getenv(
            "PLATFORM_SYNC_DATABASE_URL_FILE",
            str(DEFAULT_SECRETS_DIR / "platform-sync-writer-database-url"),
        ),
        remote_ssh_host=os.getenv(
            "PLATFORM_REMOTE_SSH_HOST",
            "root@47.106.112.69",
        ),
        remote_ssh_key_path=os.getenv(
            "PLATFORM_REMOTE_SSH_KEY_PATH",
            "/Users/neo/.ssh/orbbec_aliyun_ed25519",
        ),
        remote_poll_interval_seconds=float(
            os.getenv("PLATFORM_REMOTE_POLL_INTERVAL", "60")
        ),
        feedback_closure_outbox_dir=_feedback_closure_outbox_dir(),
        operations_database_path=os.getenv(
            "PLATFORM_OPERATIONS_DATABASE_PATH", "../data/platform-operations.db"
        ),
        operations_usage_interval_seconds=float(
            os.getenv("PLATFORM_OPERATIONS_USAGE_INTERVAL", "300")
        ),
        operations_execution_interval_seconds=float(
            os.getenv("PLATFORM_OPERATIONS_EXECUTION_INTERVAL", "300")
        ),
        operations_lifecycle_interval_seconds=float(
            os.getenv("PLATFORM_OPERATIONS_LIFECYCLE_INTERVAL", "600")
        ),
        attachment_enabled=_enabled("PLATFORM_ATTACHMENT_ENABLED"),
        attachment_s3_endpoint=os.getenv(
            "PLATFORM_ATTACHMENT_S3_ENDPOINT", "http://127.0.0.1:9000"
        ),
        attachment_s3_bucket=os.getenv(
            "PLATFORM_ATTACHMENT_S3_BUCKET", "orbbec-agent-attachments"
        ),
        attachment_s3_access_key_file=os.getenv(
            "PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE",
            str(DEFAULT_SECRETS_DIR / "attachment-s3-access-key"),
        ),
        attachment_s3_secret_key_file=os.getenv(
            "PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE",
            str(DEFAULT_SECRETS_DIR / "attachment-s3-secret-key"),
        ),
        attachment_ticket_seconds=int(
            os.getenv("PLATFORM_ATTACHMENT_TICKET_SECONDS", "300")
        ),
        trusted_attachment_proxy=_enabled("PLATFORM_TRUSTED_ATTACHMENT_PROXY"),
        replica_database_url_file=os.getenv(
            "PLATFORM_REPLICA_DATABASE_URL_FILE",
            str(DEFAULT_SECRETS_DIR / "replica-database-url"),
        ),
        replica_encryption_key_file=os.getenv(
            "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE",
            str(DEFAULT_SECRETS_DIR / "replica-encryption-key"),
        ),
        replica_signing_public_key_file=os.getenv(
            "PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE",
            str(DEFAULT_SECRETS_DIR / "replica-signing-public-key"),
        ),
        replica_stale_seconds=int(
            os.getenv("PLATFORM_REPLICA_STALE_SECONDS", "900")
        ),
        execution_relay_enabled=execution_relay_enabled,
        agent_brain_enabled=_enabled("PLATFORM_AGENT_BRAIN_ENABLED"),
        agent_brain_v2_enabled=_enabled("PLATFORM_AGENT_BRAIN_V2_ENABLED"),
        brain_model_enabled=_enabled("PLATFORM_BRAIN_MODEL_ENABLED"),
        brain_provider_base_url=os.getenv(
            "PLATFORM_BRAIN_PROVIDER_BASE_URL", ""
        ).strip(),
        brain_provider_api_key_file=os.getenv(
            "PLATFORM_BRAIN_PROVIDER_API_KEY_FILE", ""
        ).strip(),
        brain_model_manifest_path=os.getenv(
            "PLATFORM_BRAIN_MODEL_MANIFEST_PATH", ""
        ).strip(),
        content_encryption_keyring_file=content_encryption_keyring_file,
        execution_relay_lease_seconds=execution_relay_lease_seconds,
        execution_relay_max_body_bytes=execution_relay_max_body_bytes,
        control_plane=_load_control_plane_config(),
    )
    _validate_cloud_config(config)
    _validate_attachment_config(config)
    _validate_execution_relay_config(config)
    _validate_agent_brain_config(config)
    _validate_brain_model_config(config)
    return config
