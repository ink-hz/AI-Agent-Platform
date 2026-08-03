from app.config import load_config
from app.fleet.database import resolve_flywheel_database_url
from app.local_secrets import SecretFileUnavailable


def test_flywheel_config_defaults(monkeypatch):
    monkeypatch.delenv("PLATFORM_FLYWHEEL_ENABLED", raising=False)
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL_FILE", raising=False)
    monkeypatch.delenv("PLATFORM_USAGE_CACHE_SECONDS", raising=False)
    monkeypatch.delenv("PLATFORM_ACTIVE_WINDOW_MINUTES", raising=False)

    config = load_config()

    assert config.flywheel_enabled is True
    assert config.flywheel_database_url is None
    assert config.flywheel_database_url_file.endswith(
        "/Library/Application Support/OrbbecAI-Agent-Platform/"
        "secrets/flywheel-analyst-database-url"
    )
    assert config.usage_cache_seconds == 60
    assert config.active_window_minutes == 15


def test_environment_database_url_wins(monkeypatch):
    monkeypatch.setenv("PLATFORM_FLYWHEEL_DATABASE_URL", "postgresql://example")
    config = load_config()

    def must_not_read(*_args, **_kwargs):
        raise AssertionError("secret file must not be read when the environment is set")

    assert resolve_flywheel_database_url(config, reader=must_not_read) == (
        "postgresql://example"
    )


def test_secret_file_value_is_used(monkeypatch):
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    config = load_config()
    seen = []

    def read(path):
        seen.append(path)
        return "postgresql://analyst"

    assert resolve_flywheel_database_url(config, reader=read) == (
        "postgresql://analyst"
    )
    assert seen == [config.flywheel_database_url_file]


def test_secret_file_failure_disables_usage_without_leaking(monkeypatch):
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    config = load_config()

    def failed(*_args, **_kwargs):
        raise SecretFileUnavailable("secret file unavailable")

    assert resolve_flywheel_database_url(config, reader=failed) is None


def test_disabled_flywheel_does_not_read_secret_file(monkeypatch):
    monkeypatch.setenv("PLATFORM_FLYWHEEL_ENABLED", "0")
    config = load_config()

    def must_not_read(*_args, **_kwargs):
        raise AssertionError("disabled flywheel must not read a secret file")

    assert resolve_flywheel_database_url(config, reader=must_not_read) is None
