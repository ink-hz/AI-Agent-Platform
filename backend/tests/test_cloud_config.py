from pathlib import Path

import pytest

from app.config import load_config


CLOUD_ENV_NAMES = (
    "PLATFORM_DEPLOYMENT_MODE",
    "PLATFORM_CLOUD_AUTH_MODE",
    "PLATFORM_HOST",
    "PLATFORM_PORT",
    "PLATFORM_FLYWHEEL_ENABLED",
    "PLATFORM_REVIEW_ENABLED",
    "PLATFORM_ATTACHMENT_ENABLED",
    "PLATFORM_REPLICA_DATABASE_URL_FILE",
    "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE",
    "PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE",
    "PLATFORM_REPLICA_STALE_SECONDS",
)


def _clear_cloud_environment(monkeypatch) -> None:
    for name in CLOUD_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _private_file(path: Path, value: str = "test-secret") -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def _configure_cloud(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    _clear_cloud_environment(monkeypatch)
    secrets = {
        "database": _private_file(tmp_path / "database-url"),
        "encryption": _private_file(tmp_path / "encryption-key"),
        "signing": _private_file(tmp_path / "signing-public-key"),
    }
    monkeypatch.setenv("PLATFORM_DEPLOYMENT_MODE", "cloud-replica")
    monkeypatch.setenv("PLATFORM_HOST", "127.0.0.1")
    monkeypatch.setenv("PLATFORM_PORT", "8080")
    monkeypatch.setenv("PLATFORM_FLYWHEEL_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_REVIEW_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_ATTACHMENT_ENABLED", "0")
    monkeypatch.setenv(
        "PLATFORM_REPLICA_DATABASE_URL_FILE", str(secrets["database"])
    )
    monkeypatch.setenv(
        "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE", str(secrets["encryption"])
    )
    monkeypatch.setenv(
        "PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE", str(secrets["signing"])
    )
    monkeypatch.setenv("PLATFORM_REPLICA_STALE_SECONDS", "900")
    return secrets


def test_default_deployment_mode_is_local(monkeypatch):
    _clear_cloud_environment(monkeypatch)

    assert load_config().deployment_mode == "local"


def test_loads_exact_cloud_replica_configuration(monkeypatch, tmp_path):
    secrets = _configure_cloud(monkeypatch, tmp_path)

    config = load_config()

    assert config.deployment_mode == "cloud-replica"
    assert config.cloud_auth_mode == "ssh-tunnel"
    assert config.host == "127.0.0.1"
    assert config.port == 8080
    assert config.replica_database_url_file == str(secrets["database"])
    assert config.replica_encryption_key_file == str(secrets["encryption"])
    assert config.replica_signing_public_key_file == str(secrets["signing"])
    assert config.replica_stale_seconds == 900


def test_loads_basic_auth_cloud_entry_mode(monkeypatch, tmp_path):
    _configure_cloud(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_CLOUD_AUTH_MODE", "basic-auth")

    assert load_config().cloud_auth_mode == "basic-auth"


def test_rejects_unknown_cloud_auth_mode(monkeypatch, tmp_path):
    _configure_cloud(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_CLOUD_AUTH_MODE", "anonymous")

    with pytest.raises(RuntimeError, match="cloud authentication mode"):
        load_config()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PLATFORM_HOST", "0.0.0.0", "loopback"),
        ("PLATFORM_FLYWHEEL_ENABLED", "1", "Flywheel"),
        ("PLATFORM_REVIEW_ENABLED", "1", "Review"),
        ("PLATFORM_ATTACHMENT_ENABLED", "1", "attachments"),
        ("PLATFORM_REPLICA_STALE_SECONDS", "901", "900"),
    ],
)
def test_cloud_replica_rejects_unsafe_runtime_settings(
    monkeypatch, tmp_path, name, value, message
):
    _configure_cloud(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        load_config()


def test_cloud_replica_rejects_relative_secret_path(monkeypatch, tmp_path):
    _configure_cloud(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_REPLICA_DATABASE_URL_FILE", "database-url")

    with pytest.raises(RuntimeError, match="absolute"):
        load_config()


def test_cloud_replica_rejects_permissive_secret_mode(monkeypatch, tmp_path):
    secrets = _configure_cloud(monkeypatch, tmp_path)
    secrets["encryption"].chmod(0o644)

    with pytest.raises(RuntimeError, match="0600"):
        load_config()


def test_rejects_unknown_deployment_mode(monkeypatch):
    _clear_cloud_environment(monkeypatch)
    monkeypatch.setenv("PLATFORM_DEPLOYMENT_MODE", "public")

    with pytest.raises(RuntimeError, match="deployment mode"):
        load_config()
