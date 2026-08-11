import os
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import stat
from typing import Literal
from urllib.parse import urlparse

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


def _validate_cloud_config(config: Config) -> None:
    if config.deployment_mode not in {"local", "cloud-replica"}:
        raise RuntimeError("unsupported deployment mode")
    if config.deployment_mode != "cloud-replica":
        return
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


def is_cloud_mode(config: Config) -> bool:
    return config.deployment_mode == "cloud-replica"


def load_config() -> Config:
    config = Config(
        deployment_mode=os.getenv("PLATFORM_DEPLOYMENT_MODE", "local"),
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
    )
    _validate_cloud_config(config)
    _validate_attachment_config(config)
    return config
