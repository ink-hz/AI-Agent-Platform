from subprocess import CompletedProcess

from app.config import load_config
from app.fleet.database import resolve_flywheel_database_url


def test_flywheel_config_defaults(monkeypatch):
    monkeypatch.delenv("PLATFORM_FLYWHEEL_ENABLED", raising=False)
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    monkeypatch.delenv("PLATFORM_FLYWHEEL_KEYCHAIN_SERVICE", raising=False)
    monkeypatch.delenv("PLATFORM_FLYWHEEL_KEYCHAIN_ACCOUNT", raising=False)
    monkeypatch.delenv("PLATFORM_USAGE_CACHE_SECONDS", raising=False)
    monkeypatch.delenv("PLATFORM_ACTIVE_WINDOW_MINUTES", raising=False)

    config = load_config()

    assert config.flywheel_enabled is True
    assert config.flywheel_database_url is None
    assert config.flywheel_keychain_service == "flywheel-analyst-database-url"
    assert config.flywheel_keychain_account == "neo"
    assert config.usage_cache_seconds == 60
    assert config.active_window_minutes == 15


def test_environment_database_url_wins(monkeypatch):
    monkeypatch.setenv("PLATFORM_FLYWHEEL_DATABASE_URL", "postgresql://example")
    config = load_config()

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("Keychain must not run when the environment is set")

    assert resolve_flywheel_database_url(config, runner=must_not_run) == (
        "postgresql://example"
    )


def test_keychain_failure_disables_usage_without_leaking(monkeypatch):
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    config = load_config()

    def failed(*_args, **_kwargs):
        return CompletedProcess([], 44, stdout="", stderr="secret detail")

    assert resolve_flywheel_database_url(config, runner=failed) is None


def test_disabled_flywheel_does_not_read_keychain(monkeypatch):
    monkeypatch.setenv("PLATFORM_FLYWHEEL_ENABLED", "0")
    config = load_config()

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("disabled flywheel must not read Keychain")

    assert resolve_flywheel_database_url(config, runner=must_not_run) is None
