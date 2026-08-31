import base64
import json
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
    "PLATFORM_RATE_LIMIT_HMAC_KEYRING_FILE",
    "PLATFORM_IDENTITY_RECONCILE_INTERVAL_SECONDS",
    "PLATFORM_IDENTITY_WARNING_AFTER_SECONDS",
    "PLATFORM_IDENTITY_HARD_STALE_AFTER_SECONDS",
    "PLATFORM_TRUSTED_PROXY_CIDRS",
)


OFFICE_RECIPIENT_ENV = (
    "PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED",
    "PLATFORM_OFFICE_RECIPIENT_BEARER_FILE",
    "PLATFORM_OFFICE_RECIPIENT_BEARER",
)


def test_office_recipient_directory_defaults_disabled(monkeypatch) -> None:
    for name in OFFICE_RECIPIENT_ENV:
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.office_recipient_directory_enabled is False
    assert config.office_recipient_bearer_file == ""


def test_office_recipient_directory_rejects_inline_bearer(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_BEARER", "s" * 32)

    with pytest.raises(ValueError, match="secret file"):
        load_config()


def test_enabled_office_recipient_directory_requires_private_bearer_file(
    monkeypatch, tmp_path
) -> None:
    bearer = tmp_path / "office-recipient-bearer"
    bearer.write_bytes(b"s" * 32)
    bearer.chmod(0o600)
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_BEARER_FILE", str(bearer))
    monkeypatch.delenv("PLATFORM_OFFICE_RECIPIENT_BEARER", raising=False)

    config = load_config()

    assert config.office_recipient_directory_enabled is True
    assert config.office_recipient_bearer_file == str(bearer)
    assert "s" * 32 not in repr(config)


@pytest.mark.parametrize("size", [0, 31])
def test_office_recipient_bearer_is_at_least_32_bytes(
    monkeypatch, tmp_path, size
) -> None:
    bearer = tmp_path / "office-recipient-bearer"
    bearer.write_bytes(b"s" * size)
    bearer.chmod(0o600)
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_BEARER_FILE", str(bearer))

    with pytest.raises(RuntimeError):
        load_config()


def test_office_recipient_bearer_length_excludes_file_whitespace(
    monkeypatch, tmp_path
) -> None:
    bearer = tmp_path / "office-recipient-bearer"
    bearer.write_bytes(b"s" * 31 + b"\n")
    bearer.chmod(0o600)
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_BEARER_FILE", str(bearer))

    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        load_config()


def test_office_recipient_bearer_rejects_symlink(monkeypatch, tmp_path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"s" * 32)
    target.chmod(0o600)
    bearer = tmp_path / "office-recipient-bearer"
    bearer.symlink_to(target)
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_BEARER_FILE", str(bearer))

    with pytest.raises(RuntimeError, match="regular mode 0600 file"):
        load_config()


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


def test_brain_model_defaults_disabled_and_never_accepts_inline_api_key(
    monkeypatch,
) -> None:
    for name in (
        "PLATFORM_AGENT_BRAIN_V2_ENABLED",
        "PLATFORM_BRAIN_MODEL_ENABLED",
        "PLATFORM_BRAIN_PROVIDER_BASE_URL",
        "PLATFORM_BRAIN_PROVIDER_API_KEY_FILE",
        "PLATFORM_BRAIN_MODEL_MANIFEST_PATH",
        "PLATFORM_BRAIN_PROVIDER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()
    assert config.agent_brain_v2_enabled is False
    assert not hasattr(config, "agent_brain_collaboration_enabled")
    assert config.brain_model_enabled is False
    assert config.brain_provider_api_key_file == ""
    assert config.brain_provider_base_url == ""

    monkeypatch.setenv("PLATFORM_BRAIN_PROVIDER_API_KEY", "must-not-be-inline")
    with pytest.raises(ValueError, match="secret file"):
        load_config()


def test_legacy_brain_collaboration_environment_is_not_a_config_field(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED", "0")

    config = load_config()

    assert not hasattr(config, "agent_brain_collaboration_enabled")


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


RELAY_ENV = (
    "PLATFORM_EXECUTION_RELAY_ENABLED",
    "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE",
    "PLATFORM_EXECUTION_RELAY_LEASE_SECONDS",
    "PLATFORM_EXECUTION_RELAY_MAX_BODY_BYTES",
)


def _private_file(path, value: str) -> str:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def _enable_production_identity(monkeypatch, tmp_path) -> None:
    files = {
        "PLATFORM_CONTROL_DATABASE_URL_FILE": "postgresql://platform_control_app:secret@db/platform",
        "PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE": "postgresql://platform_audit_append:secret@db/platform",
        "PLATFORM_DINGTALK_APP_SECRET_FILE": "dingtalk-secret",
        "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE": "identity-encryption",
        "PLATFORM_IDENTITY_HMAC_KEYRING_FILE": "identity-hmac",
        "PLATFORM_RATE_LIMIT_HMAC_KEYRING_FILE": "rate-hmac",
    }
    for name, content in files.items():
        monkeypatch.setenv(
            name,
            _private_file(tmp_path / name.lower(), content),
        )
    monkeypatch.setenv("PLATFORM_IDENTITY_MODE", "production")
    monkeypatch.setenv("PLATFORM_PUBLIC_BASE_URL", "https://agent.example.test")
    monkeypatch.setenv("PLATFORM_ROUTE_PREFIX", "/")
    monkeypatch.setenv("PLATFORM_COOKIE_NAME", "__Host-platform_session")
    monkeypatch.setenv("PLATFORM_DINGTALK_APP_KEY", "app-key")
    monkeypatch.setenv("PLATFORM_DINGTALK_AGENT_ID", "agent-id")
    monkeypatch.setenv("PLATFORM_DINGTALK_CORP_ID", "corp-id")


def _content_keyring(path, *, purpose="platform-content-encryption") -> str:
    document = {
        "purpose": purpose,
        "active_version": 1,
        "keys": {"1": base64.b64encode(b"k" * 32).decode("ascii")},
    }
    return _private_file(path, json.dumps(document))


def test_execution_relay_defaults_disabled_without_content_keyring(monkeypatch):
    for name in RELAY_ENV:
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.execution_relay_enabled is False
    assert config.content_encryption_keyring_file == ""
    assert config.execution_relay_lease_seconds == 45
    assert config.execution_relay_max_body_bytes == 1_048_576


def test_execution_relay_enabled_accepts_only_production_root_and_valid_keyring(
    monkeypatch, tmp_path
):
    _enable_production_identity(monkeypatch, tmp_path)
    keyring = _content_keyring(tmp_path / "content-keyring.json")
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE", keyring)
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_LEASE_SECONDS", "30")
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_MAX_BODY_BYTES", "524288")

    config = load_config()

    assert config.execution_relay_enabled is True
    assert config.content_encryption_keyring_file == keyring
    assert config.execution_relay_lease_seconds == 30
    assert config.execution_relay_max_body_bytes == 524_288


def test_direct_agent_execution_is_independent_from_brain_but_requires_relay(
    monkeypatch, tmp_path
) -> None:
    _enable_production_identity(monkeypatch, tmp_path)
    keyring = _content_keyring(tmp_path / "content-keyring.json")
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE", keyring)
    monkeypatch.setenv("PLATFORM_DIRECT_AGENT_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_AGENT_BRAIN_ENABLED", "0")

    config = load_config()

    assert config.direct_agent_enabled is True
    assert config.agent_brain_enabled is False


def test_direct_agent_execution_fails_closed_without_relay(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_DIRECT_AGENT_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_ENABLED", "0")

    with pytest.raises(ValueError, match="Direct Agent requires production identity and relay"):
        load_config()


def test_execution_relay_enabled_requires_production_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_ENABLED", "1")
    monkeypatch.setenv(
        "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE",
        _content_keyring(tmp_path / "content-keyring.json"),
    )
    monkeypatch.setenv("PLATFORM_IDENTITY_MODE", "disabled")

    with pytest.raises(ValueError, match="production identity"):
        load_config()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda monkeypatch, path: monkeypatch.setenv(
            "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE", "relative/keyring"
        ), "absolute"),
        (lambda monkeypatch, path: os.chmod(path, 0o644), "0600"),
        (lambda monkeypatch, path: path.write_text(
            json.dumps({
                "purpose": "wrong-purpose",
                "active_version": 1,
                "keys": {"1": base64.b64encode(b"k" * 32).decode("ascii")},
            }),
            encoding="utf-8",
        ), "content encryption keyring"),
        (lambda monkeypatch, path: monkeypatch.setenv(
            "PLATFORM_EXECUTION_RELAY_LEASE_SECONDS", "0"
        ), "positive"),
        (lambda monkeypatch, path: monkeypatch.setenv(
            "PLATFORM_EXECUTION_RELAY_MAX_BODY_BYTES", "1048577"
        ), "1048576"),
    ],
)
def test_execution_relay_enabled_fails_closed_on_unsafe_configuration(
    monkeypatch, tmp_path, mutation, message
):
    _enable_production_identity(monkeypatch, tmp_path)
    keyring_path = tmp_path / "content-keyring.json"
    _content_keyring(keyring_path)
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE", str(keyring_path))
    mutation(monkeypatch, keyring_path)

    with pytest.raises((ValueError, RuntimeError), match=message):
        load_config()
