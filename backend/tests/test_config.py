import os

import pytest

from app.config import load_config
from app.control_plane.models import IdentityMode


CONTROL_PLANE_ENV = (
    "PLATFORM_IDENTITY_MODE",
    "PLATFORM_CONTROL_DATABASE_URL_FILE",
    "PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE",
    "PLATFORM_PUBLIC_BASE_URL",
    "PLATFORM_ROUTE_PREFIX",
    "PLATFORM_COOKIE_NAME",
    "PLATFORM_DINGTALK_APP_KEY",
    "PLATFORM_DINGTALK_AGENT_ID",
    "PLATFORM_DINGTALK_CORP_ID",
    "PLATFORM_DINGTALK_APP_SECRET_FILE",
    "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE",
    "PLATFORM_IDENTITY_HMAC_KEYRING_FILE",
    "PLATFORM_IDENTITY_RECONCILE_INTERVAL_SECONDS",
    "PLATFORM_IDENTITY_WARNING_AFTER_SECONDS",
    "PLATFORM_IDENTITY_HARD_STALE_AFTER_SECONDS",
    "PLATFORM_TRUSTED_PROXY_CIDRS",
)


def test_identity_defaults_are_disabled_and_need_no_secret_files(monkeypatch) -> None:
    for name in CONTROL_PLANE_ENV:
        monkeypatch.delenv(name, raising=False)

    control_plane = load_config().control_plane

    assert control_plane.mode is IdentityMode.DISABLED
    assert control_plane.control_database_url_file == ""
    assert control_plane.audit_database_url_file == ""
    assert control_plane.public_base_url == ""
    assert control_plane.route_prefix == "/"
    assert control_plane.cookie_name == ""
    assert control_plane.trusted_proxy_cidrs == ("127.0.0.1/32", "::1/128")


def test_remote_sync_config_defaults(monkeypatch) -> None:
    for name in (
        "PLATFORM_SYNC_DATABASE_URL_FILE",
        "PLATFORM_SYNC_DATABASE_URL",
        "PLATFORM_REMOTE_SSH_HOST",
        "PLATFORM_REMOTE_SSH_KEY_PATH",
        "PLATFORM_REMOTE_POLL_INTERVAL",
        "PLATFORM_FEEDBACK_CLOSURE_OUTBOX_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.sync_database_url_file.endswith(
        "/Library/Application Support/OrbbecAI-Agent-Platform/"
        "secrets/platform-sync-writer-database-url"
    )
    assert config.sync_database_url is None
    assert config.remote_ssh_host == "root@47.106.112.69"
    assert config.remote_ssh_key_path == "/Users/neo/.ssh/orbbec_aliyun_ed25519"
    assert config.remote_poll_interval_seconds == 60
    assert config.feedback_closure_outbox_dir.endswith(
        "/Library/Application Support/OrbbecAI-Agent-Platform/"
        "feedback-closure-outbox"
    )


def test_remote_sync_config_accepts_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_SYNC_DATABASE_URL_FILE", "/tmp/sync-secret")
    monkeypatch.setenv("PLATFORM_SYNC_DATABASE_URL", "postgresql://sync")
    monkeypatch.setenv("PLATFORM_REMOTE_SSH_HOST", "agent@example.test")
    monkeypatch.setenv("PLATFORM_REMOTE_SSH_KEY_PATH", "/tmp/test-key")
    monkeypatch.setenv("PLATFORM_REMOTE_POLL_INTERVAL", "90")
    monkeypatch.setenv(
        "PLATFORM_FEEDBACK_CLOSURE_OUTBOX_DIR",
        "/tmp/platform-feedback-closure-outbox",
    )

    config = load_config()

    assert config.sync_database_url_file == "/tmp/sync-secret"
    assert config.sync_database_url == "postgresql://sync"
    assert config.remote_ssh_host == "agent@example.test"
    assert config.remote_ssh_key_path == "/tmp/test-key"
    assert config.remote_poll_interval_seconds == 90
    assert config.feedback_closure_outbox_dir == (
        "/tmp/platform-feedback-closure-outbox"
    )


def test_feedback_closure_outbox_override_must_be_absolute(monkeypatch) -> None:
    monkeypatch.setenv(
        "PLATFORM_FEEDBACK_CLOSURE_OUTBOX_DIR",
        "relative/outbox",
    )

    with pytest.raises(RuntimeError, match="absolute"):
        load_config()


def test_config_has_stable_operations_defaults(monkeypatch) -> None:
    for name in (
        "PLATFORM_OPERATIONS_DATABASE_PATH",
        "PLATFORM_OPERATIONS_USAGE_INTERVAL",
        "PLATFORM_OPERATIONS_EXECUTION_INTERVAL",
        "PLATFORM_OPERATIONS_LIFECYCLE_INTERVAL",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.operations_database_path == "../data/platform-operations.db"
    assert config.operations_usage_interval_seconds == 300
    assert config.operations_execution_interval_seconds == 300
    assert config.operations_lifecycle_interval_seconds == 600


def test_review_writer_config_defaults(monkeypatch) -> None:
    for name in (
        "PLATFORM_REVIEW_ENABLED",
        "PLATFORM_REVIEW_DATABASE_URL",
        "PLATFORM_REVIEW_DATABASE_URL_FILE",
        "PLATFORM_REVIEW_REQUEST_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.review_enabled is True
    assert config.review_database_url is None
    assert config.review_database_url_file.endswith(
        "/Library/Application Support/OrbbecAI-Agent-Platform/"
        "secrets/platform-review-writer-database-url"
    )
    assert config.review_request_timeout_seconds == 1200


ATTACHMENT_ENV = (
    "PLATFORM_ATTACHMENT_ENABLED",
    "PLATFORM_ATTACHMENT_S3_ENDPOINT",
    "PLATFORM_ATTACHMENT_S3_BUCKET",
    "PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE",
    "PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE",
    "PLATFORM_ATTACHMENT_TICKET_SECONDS",
    "PLATFORM_TRUSTED_ATTACHMENT_PROXY",
)


def test_attachment_defaults_are_disabled_and_need_no_secret_files(monkeypatch) -> None:
    for name in ATTACHMENT_ENV:
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.attachment_enabled is False
    assert config.attachment_s3_bucket == "orbbec-agent-attachments"
    assert config.attachment_ticket_seconds == 300
    assert config.trusted_attachment_proxy is False


def _enable_attachments(monkeypatch, tmp_path, *, host="127.0.0.1"):
    access = tmp_path / "s3-access"
    secret = tmp_path / "s3-secret"
    analyst = tmp_path / "flywheel-analyst-database-url"
    access.write_text("archive-access", encoding="utf-8")
    secret.write_text("archive-secret", encoding="utf-8")
    analyst.write_text(
        "postgresql://flywheel_analyst:db-secret@127.0.0.1/flywheel",
        encoding="utf-8",
    )
    for path in (access, secret, analyst):
        path.chmod(0o600)
    monkeypatch.setenv("PLATFORM_ATTACHMENT_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_ATTACHMENT_S3_ENDPOINT", "http://127.0.0.1:9000")
    monkeypatch.setenv("PLATFORM_ATTACHMENT_S3_BUCKET", "orbbec-agent-attachments")
    monkeypatch.setenv("PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE", str(access))
    monkeypatch.setenv("PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE", str(secret))
    monkeypatch.setenv("PLATFORM_FLYWHEEL_DATABASE_URL_FILE", str(analyst))
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    monkeypatch.setenv("PLATFORM_HOST", host)
    return access, secret, analyst


def test_enabled_attachment_config_accepts_loopback_and_mode_0600_files(
    monkeypatch, tmp_path
) -> None:
    access, secret, _analyst = _enable_attachments(monkeypatch, tmp_path)

    config = load_config()

    assert config.attachment_enabled is True
    assert config.attachment_s3_endpoint == "http://127.0.0.1:9000"
    assert config.attachment_s3_access_key_file == str(access)
    assert config.attachment_s3_secret_key_file == str(secret)
    assert "archive-access" not in repr(config)
    assert "archive-secret" not in repr(config)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda monkeypatch, paths: monkeypatch.setenv(
            "PLATFORM_ATTACHMENT_S3_ENDPOINT", "http://192.168.1.2:9000"
        ), "loopback"),
        (lambda monkeypatch, paths: monkeypatch.setenv(
            "PLATFORM_ATTACHMENT_S3_BUCKET", "wrong-bucket"
        ), "bucket"),
        (lambda monkeypatch, paths: os.chmod(paths[0], 0o644), "0600"),
        (lambda monkeypatch, paths: os.chmod(paths[1], 0o644), "0600"),
        (lambda monkeypatch, paths: paths[2].write_text(
            "postgresql://admin:secret@127.0.0.1/flywheel", encoding="utf-8"
        ), "analyst"),
    ],
)
def test_enabled_attachment_config_rejects_unsafe_storage_or_database(
    monkeypatch, tmp_path, mutate, message
) -> None:
    paths = _enable_attachments(monkeypatch, tmp_path)
    mutate(monkeypatch, paths)

    with pytest.raises(RuntimeError, match=message):
        load_config()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.10.20"])
def test_enabled_attachment_config_requires_loopback_platform_host(
    monkeypatch, tmp_path, host
) -> None:
    _enable_attachments(monkeypatch, tmp_path, host=host)

    with pytest.raises(RuntimeError, match="trusted attachment proxy"):
        load_config()


def test_trusted_proxy_explicitly_allows_non_loopback_host(monkeypatch, tmp_path) -> None:
    _enable_attachments(monkeypatch, tmp_path, host="0.0.0.0")
    monkeypatch.setenv("PLATFORM_TRUSTED_ATTACHMENT_PROXY", "1")

    assert load_config().trusted_attachment_proxy is True


def test_enabled_attachments_reject_inline_database_credentials(
    monkeypatch, tmp_path
) -> None:
    _enable_attachments(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "PLATFORM_FLYWHEEL_DATABASE_URL",
        "postgresql://flywheel_analyst:inline-secret@127.0.0.1/flywheel",
    )

    with pytest.raises(RuntimeError, match="mode 0600"):
        load_config()
