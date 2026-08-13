from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import load_config
from app.control_plane.models import (
    AuthContext,
    ControlPlaneConfig,
    DirectoryFreshness,
    IdentityMode,
    IssuedWebSession,
    Role,
)


SECRET_FILE_ENV = {
    "PLATFORM_CONTROL_DATABASE_URL_FILE": "control-database-url",
    "PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE": "audit-database-url",
    "PLATFORM_DINGTALK_APP_SECRET_FILE": "dingtalk-app-secret",
    "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE": "identity-encryption-keyring",
    "PLATFORM_IDENTITY_HMAC_KEYRING_FILE": "identity-hmac-keyring",
}

INLINE_SECRET_ENV = (
    "PLATFORM_CONTROL_DATABASE_URL",
    "PLATFORM_CONTROL_AUDIT_DATABASE_URL",
    "PLATFORM_DINGTALK_APP_SECRET",
    "PLATFORM_IDENTITY_ENCRYPTION_KEYRING",
    "PLATFORM_IDENTITY_HMAC_KEYRING",
)

RATE_LIMIT_ENV = (
    "PLATFORM_LOGIN_STARTS_PER_CHALLENGE",
    "PLATFORM_LOGIN_CHALLENGE_WINDOW_SECONDS",
    "PLATFORM_ACTIVE_LOGIN_ATTEMPTS",
    "PLATFORM_OAUTH_STATE_TTL_SECONDS",
    "PLATFORM_EDGE_LOGIN_STARTS_PER_MINUTE",
    "PLATFORM_EDGE_LOGIN_BURST",
    "PLATFORM_EDGE_CALLBACKS_PER_MINUTE",
    "PLATFORM_OAUTH_EXCHANGE_CONCURRENCY",
    "PLATFORM_OAUTH_EXCHANGES_PER_MINUTE",
    "PLATFORM_AUTHENTICATED_READS_PER_MINUTE",
    "PLATFORM_AUTHENTICATED_MUTATIONS_PER_MINUTE",
)


def install_control_secret_files(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    paths = {}
    for environment_name, filename in SECRET_FILE_ENV.items():
        path = tmp_path / filename
        path.write_text(f"test-only-{filename}\n", encoding="utf-8")
        path.chmod(0o600)
        monkeypatch.setenv(environment_name, str(path))
        paths[environment_name] = path
    return paths


def install_required_identity_environment(
    tmp_path: Path, monkeypatch, *, mode: str
) -> dict[str, Path]:
    paths = install_control_secret_files(tmp_path, monkeypatch)
    monkeypatch.setenv("PLATFORM_IDENTITY_MODE", mode)
    monkeypatch.setenv("PLATFORM_PUBLIC_BASE_URL", "https://agent.example.test")
    monkeypatch.setenv("PLATFORM_DINGTALK_APP_KEY", "test-app-key")
    monkeypatch.setenv("PLATFORM_DINGTALK_AGENT_ID", "test-agent-id")
    monkeypatch.setenv("PLATFORM_DINGTALK_CORP_ID", "test-corp-id")
    if mode == "preview":
        monkeypatch.setenv("PLATFORM_ROUTE_PREFIX", "/_preview/dingtalk-r1")
        monkeypatch.setenv("PLATFORM_COOKIE_NAME", "platform_preview_session")
    else:
        monkeypatch.setenv("PLATFORM_ROUTE_PREFIX", "/")
        monkeypatch.setenv("PLATFORM_COOKIE_NAME", "__Host-platform_session")
    return paths


def test_control_plane_models_are_explicit_and_immutable() -> None:
    internal_user_id = uuid4()
    session_id = uuid4()
    auth = AuthContext(
        internal_user_id=internal_user_id,
        role=Role.MANAGEMENT_VIEWER,
        session_id=session_id,
        hard_stale_read_only=True,
    )
    issued = IssuedWebSession(
        session_id=session_id,
        cookie_token="test-cookie-token",
        csrf_token="test-csrf-token",
        idle_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        absolute_expires_at=datetime(2030, 1, 2, tzinfo=UTC),
    )

    assert tuple(Role) == (
        Role.MEMBER,
        Role.MANAGEMENT_VIEWER,
        Role.PLATFORM_OWNER,
    )
    assert tuple(IdentityMode) == (
        IdentityMode.DISABLED,
        IdentityMode.PREVIEW,
        IdentityMode.PRODUCTION,
    )
    assert tuple(DirectoryFreshness) == (
        DirectoryFreshness.FRESH,
        DirectoryFreshness.WARNING,
        DirectoryFreshness.HARD_STALE,
    )
    assert auth.internal_user_id == internal_user_id
    assert issued.session_id == session_id
    with pytest.raises(FrozenInstanceError):
        auth.hard_stale_read_only = False


@pytest.mark.parametrize("mode", ["preview", "production"])
def test_enabled_identity_requires_every_explicit_field(tmp_path, monkeypatch, mode) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode=mode)
    required_names = (
        *SECRET_FILE_ENV,
        "PLATFORM_PUBLIC_BASE_URL",
        "PLATFORM_ROUTE_PREFIX",
        "PLATFORM_COOKIE_NAME",
        "PLATFORM_DINGTALK_APP_KEY",
        "PLATFORM_DINGTALK_AGENT_ID",
        "PLATFORM_DINGTALK_CORP_ID",
    )

    for name in required_names:
        with monkeypatch.context() as isolated:
            isolated.delenv(name)
            with pytest.raises(ValueError, match=name):
                load_config()


def test_preview_configuration_is_path_scoped_and_uses_initial_limits(
    tmp_path, monkeypatch
) -> None:
    paths = install_required_identity_environment(tmp_path, monkeypatch, mode="preview")

    control_plane = load_config().control_plane

    assert control_plane == ControlPlaneConfig(
        mode=IdentityMode.PREVIEW,
        control_database_url_file=str(paths["PLATFORM_CONTROL_DATABASE_URL_FILE"]),
        audit_database_url_file=str(
            paths["PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE"]
        ),
        public_base_url="https://agent.example.test",
        route_prefix="/_preview/dingtalk-r1/",
        cookie_name="platform_preview_session",
        dingtalk_app_key="test-app-key",
        dingtalk_agent_id="test-agent-id",
        dingtalk_corp_id="test-corp-id",
        dingtalk_app_secret_file=str(paths["PLATFORM_DINGTALK_APP_SECRET_FILE"]),
        encryption_keyring_file=str(
            paths["PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE"]
        ),
        hmac_keyring_file=str(paths["PLATFORM_IDENTITY_HMAC_KEYRING_FILE"]),
    )
    assert control_plane.login_starts_per_challenge == 5
    assert control_plane.login_challenge_window_seconds == 600
    assert control_plane.active_login_attempts == 3
    assert control_plane.oauth_state_ttl_seconds == 300
    assert control_plane.edge_login_starts_per_minute == 600
    assert control_plane.edge_login_burst == 1_200
    assert control_plane.edge_callbacks_per_minute == 1_200
    assert control_plane.oauth_exchange_concurrency == 100
    assert control_plane.oauth_exchanges_per_minute == 3_000
    assert control_plane.authenticated_reads_per_minute == 300
    assert control_plane.authenticated_mutations_per_minute == 60


def test_production_configuration_uses_root_host_cookie(tmp_path, monkeypatch) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")

    control_plane = load_config().control_plane

    assert control_plane.mode is IdentityMode.PRODUCTION
    assert control_plane.route_prefix == "/"
    assert control_plane.cookie_name == "__Host-platform_session"


def test_preview_and_production_cookie_names_are_fixed(tmp_path, monkeypatch) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="preview")
    monkeypatch.setenv("PLATFORM_COOKIE_NAME", "shared_platform_session")
    with pytest.raises(ValueError, match="preview Cookie name"):
        load_config()

    monkeypatch.setenv("PLATFORM_IDENTITY_MODE", "production")
    monkeypatch.setenv("PLATFORM_ROUTE_PREFIX", "/")
    monkeypatch.setenv("PLATFORM_COOKIE_NAME", "platform_preview_session")
    with pytest.raises(ValueError, match="production Cookie name"):
        load_config()


def test_preview_rejects_host_cookie_because_it_is_path_scoped(tmp_path, monkeypatch) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="preview")
    monkeypatch.setenv("PLATFORM_COOKIE_NAME", "__Host-platform-preview")

    with pytest.raises(ValueError, match="__Host- cookies require Path=/"):
        load_config()


@pytest.mark.parametrize(
    ("configured", "normalized"),
    [
        ("/_preview/dingtalk-r1", "/_preview/dingtalk-r1/"),
        ("/_preview//dingtalk-r1///", "/_preview/dingtalk-r1/"),
    ],
)
def test_route_prefix_is_normalized(tmp_path, monkeypatch, configured, normalized) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="preview")
    monkeypatch.setenv("PLATFORM_ROUTE_PREFIX", configured)

    assert load_config().control_plane.route_prefix == normalized


@pytest.mark.parametrize(
    "route_prefix",
    ["preview/dingtalk-r1", "/../production", "/_preview/./dingtalk-r1"],
)
def test_route_prefix_rejects_ambiguous_paths(
    tmp_path, monkeypatch, route_prefix
) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="preview")
    monkeypatch.setenv("PLATFORM_ROUTE_PREFIX", route_prefix)

    with pytest.raises(ValueError, match="PLATFORM_ROUTE_PREFIX"):
        load_config()


def test_freshness_defaults_are_six_eight_and_twenty_four_hours(
    tmp_path, monkeypatch
) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")

    control_plane = load_config().control_plane

    assert control_plane.reconcile_interval_seconds == 6 * 60 * 60
    assert control_plane.warning_after_seconds == 8 * 60 * 60
    assert control_plane.hard_stale_after_seconds == 24 * 60 * 60
    assert (
        control_plane.reconcile_interval_seconds
        < control_plane.warning_after_seconds
        < control_plane.hard_stale_after_seconds
    )


def test_freshness_thresholds_must_remain_ordered(tmp_path, monkeypatch) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.setenv("PLATFORM_IDENTITY_WARNING_AFTER_SECONDS", "86400")

    with pytest.raises(ValueError, match="freshness thresholds"):
        load_config()


@pytest.mark.parametrize(
    "name",
    [
        "PLATFORM_OAUTH_STATE_TTL_SECONDS",
        "PLATFORM_LOGIN_STARTS_PER_CHALLENGE",
        "PLATFORM_IDENTITY_WARNING_AFTER_SECONDS",
    ],
)
def test_invalid_integer_names_the_setting(tmp_path, monkeypatch, name) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.setenv(name, "not-an-integer")

    with pytest.raises(ValueError, match=name):
        load_config()


def test_trusted_proxy_defaults_are_loopback_only(tmp_path, monkeypatch) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")

    assert load_config().control_plane.trusted_proxy_cidrs == (
        "127.0.0.1/32",
        "::1/128",
    )


@pytest.mark.parametrize("cidrs", ["10.0.0.0/8", "127.0.0.1/32,192.168.1.0/24"])
def test_trusted_proxy_cidrs_must_be_loopback(tmp_path, monkeypatch, cidrs) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.setenv("PLATFORM_TRUSTED_PROXY_CIDRS", cidrs)

    with pytest.raises(ValueError, match="loopback"):
        load_config()


@pytest.mark.parametrize(
    "public_base_url",
    [
        "https://agent.example.test",
        "https://agent.example.test:8443",
        "https://127.0.0.1",
        "https://127.0.0.1:8443",
        "https://[2001:db8::1]",
        "https://[2001:db8::1]:8443",
    ],
)
def test_public_base_url_accepts_https_dns_and_ip_origins(
    tmp_path, monkeypatch, public_base_url
) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.setenv("PLATFORM_PUBLIC_BASE_URL", public_base_url)

    assert load_config().control_plane.public_base_url == public_base_url


@pytest.mark.parametrize(
    "public_base_url",
    [
        "http://agent.example.test",
        "https://user:password@agent.example.test",
        "https://agent.example.test/path",
        "https://agent.example.test?query=yes",
        "https://agent.example.test/#fragment",
        "https://agent.example.test:notaport",
        "https://agent.example.test:65536",
        "https://agent.example.test:",
        "https://agent.example.test:0",
        "https://agent.example.test\\evil.example",
        "https://agent.example.test%2fevil.example",
        "https://agent.example.test\t.evil.example",
        "https://agent.example.test\n.evil.example",
        "https://agent .example.test",
        "https://-agent.example.test",
        "https://agent-.example.test",
        "https://agent..example.test",
        "https://agent_name.example.test",
        "https://999.999.999.999",
        "https://[not-an-ip]",
        "https://[2001:db8::1",
    ],
)
def test_public_base_url_must_be_a_safe_https_origin(
    tmp_path, monkeypatch, public_base_url
) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.setenv("PLATFORM_PUBLIC_BASE_URL", public_base_url)

    with pytest.raises(ValueError, match="PLATFORM_PUBLIC_BASE_URL"):
        load_config()


@pytest.mark.parametrize("secret_environment", INLINE_SECRET_ENV)
def test_inline_identity_secrets_are_rejected(
    tmp_path, monkeypatch, secret_environment
) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.setenv(secret_environment, "test-inline-secret")

    with pytest.raises(ValueError, match="secret files"):
        load_config()


def test_inline_identity_secrets_are_rejected_while_identity_is_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PLATFORM_IDENTITY_MODE", "disabled")
    monkeypatch.setenv("PLATFORM_DINGTALK_APP_SECRET", "test-inline-secret")

    with pytest.raises(ValueError, match="secret files"):
        load_config()


@pytest.mark.parametrize("rate_limit_environment", RATE_LIMIT_ENV)
def test_rate_limit_configuration_must_be_positive(
    tmp_path, monkeypatch, rate_limit_environment
) -> None:
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.setenv(rate_limit_environment, "0")

    with pytest.raises(ValueError, match=rate_limit_environment):
        load_config()


@pytest.mark.parametrize("secret_file_environment", SECRET_FILE_ENV)
def test_identity_secret_files_must_be_regular_mode_0600_files(
    tmp_path, monkeypatch, secret_file_environment
) -> None:
    paths = install_required_identity_environment(
        tmp_path, monkeypatch, mode="production"
    )
    os.chmod(paths[secret_file_environment], 0o644)

    with pytest.raises(RuntimeError, match="0600"):
        load_config()
