from __future__ import annotations

# Pytest fixture is imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
import importlib
from collections.abc import Mapping
from datetime import UTC, datetime
from types import ModuleType
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_control_plane_migration import MIGRATIONS, ROLES, control_database
from test_dingtalk_auth_api import FakeAuth, _app

from app.config import load_config
from app.control_plane.auth import AuthSecrets
from app.control_plane.authorization import AuthorizationService
from app.control_plane.crypto import IdentityKeyring
from app.control_plane.middleware import IdentitySecurityMiddleware, is_public_request
from app.control_plane.models import AuthContext, Role
from app.control_plane.partner_identity_crypto import PartnerProviderIdentityCodec
from app.control_plane.partner_models import (
    PartnerAccessDecision,
    PartnerIdentityError,
    PartnerIdentityResolution,
    VerifiedProviderSubject,
)
from app.control_plane.partner_repository import (
    PartnerRepository,
    PartnerRepositoryError,
)

NOW = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
SUBJECT_ID = UUID("40000000-0000-4000-8000-000000000001")
MIGRATION = MIGRATIONS / "056_partner_authentication.sql"
RAW_SUBJECT = "raw-partner-seat-42@example.invalid"


def _provider_module() -> ModuleType:
    try:
        return importlib.import_module("app.control_plane.partner_provider")
    except ModuleNotFoundError:
        pytest.fail("partner Provider boundary is not implemented")


class FakeProvider:
    kind = "fixture"

    def __init__(self) -> None:
        self.events: list[str] = []
        self.state = ""
        self.begin_error: Exception | None = None
        self.finish_error: Exception | None = None
        self.check_error: Exception | None = None
        self.subject_status = "active"

    def begin_auth(self, state: str) -> str:
        self.events.append("provider.begin")
        if self.begin_error is not None:
            raise self.begin_error
        self.state = state
        return f"https://provider.invalid/login?state={state}"

    async def finish_auth(self, callback: Mapping[str, str]) -> VerifiedProviderSubject:
        self.events.append("provider.finish")
        if self.finish_error is not None:
            raise self.finish_error
        return VerifiedProviderSubject(
            provider_kind=self.kind,
            provider_subject=RAW_SUBJECT,
            display_name="坐席一",
            verified_at=NOW,
        )

    async def check_subject(self, provider_subject: str) -> str:
        self.events.append("provider.check")
        assert provider_subject == RAW_SUBJECT
        if self.check_error is not None:
            raise self.check_error
        return self.subject_status


class FakePartnerService:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider
        self.state_digest: bytes | None = None
        self.state_key_version: int | None = None
        self.consumed = False
        self.consume_error: PartnerIdentityError | None = None
        self.resolution = PartnerIdentityResolution(
            subject_id=None,
            partner_operator_id=None,
            partner_organization_id=None,
            binding_request_id=UUID("50000000-0000-4000-8000-000000000001"),
            status="pending",
        )
        self.access_reason = "active"
        self.calls: list[str] = []

    def create_login_attempt(
        self, *, provider_kind: str, state_digest: bytes, state_key_version: int
    ) -> UUID:
        self.calls.append("state.create")
        assert provider_kind == self.provider.kind
        self.state_digest = state_digest
        self.state_key_version = state_key_version
        return UUID("70000000-0000-4000-8000-000000000001")

    def consume_login_attempt(
        self, *, provider_kind: str, state_digest: bytes, state_key_version: int
    ) -> None:
        self.calls.append("state.consume")
        self.provider.events.append("state.consume")
        if self.consume_error is not None:
            raise self.consume_error
        if self.consumed:
            raise PartnerIdentityError("partner_auth_state_replay", 401)
        if (
            provider_kind != self.provider.kind
            or state_digest != self.state_digest
            or state_key_version != self.state_key_version
        ):
            raise PartnerIdentityError("partner_auth_state_invalid", 401)
        self.consumed = True

    def resolve_verified_identity(
        self, verified: VerifiedProviderSubject
    ) -> PartnerIdentityResolution:
        self.calls.append("identity.resolve")
        assert verified.provider_subject == RAW_SUBJECT
        return self.resolution

    def decide_fae_access(self, subject_id: UUID) -> PartnerAccessDecision:
        self.calls.append("access.decide")
        assert subject_id == SUBJECT_ID
        return PartnerAccessDecision(
            allowed=self.access_reason == "active",
            reason=self.access_reason,
            subject_id=subject_id if self.access_reason == "active" else None,
        )


def _broker(
    *, provider: FakeProvider | None = None, service: FakePartnerService | None = None
):
    module = _provider_module()
    selected_provider = provider or FakeProvider()
    selected_service = service or FakePartnerService(selected_provider)
    return (
        module.PartnerAuthenticationBroker(
            selected_provider,
            selected_service,
            state_secrets=AuthSecrets(b"s" * 32, key_version=7),
        ),
        selected_provider,
        selected_service,
    )


def _router_client(
    *, callback_method: str = "GET", callback_path: str = "/partner-auth/callback"
):
    routes = importlib.import_module("app.control_plane.routes_partner")
    broker, provider, service = _broker()
    app = FastAPI()
    app.include_router(
        routes.build_partner_auth_router(
            broker,
            callback_method=callback_method,
            callback_path=callback_path,
        )
    )
    return TestClient(app), provider, service


@pytest.mark.asyncio
async def test_reference_provider_implements_protocol_without_identity_repr() -> None:
    module = _provider_module()
    provider = module.ReferencePartnerIdentityProvider(
        {"valid-code": (RAW_SUBJECT, "坐席一")}
    )

    redirect = provider.begin_auth("state with spaces")
    verified = await provider.finish_auth({"code": "valid-code"})

    assert redirect == "/partner-auth/reference?state=state%20with%20spaces"
    assert verified.provider_kind == "reference"
    assert verified.provider_subject == RAW_SUBJECT
    assert await provider.check_subject(RAW_SUBJECT) == "active"
    assert await provider.check_subject("unknown-seat") == "inactive"
    assert RAW_SUBJECT not in repr(provider)
    assert RAW_SUBJECT not in repr(verified)
    assert "坐席一" not in repr(verified)


@pytest.mark.asyncio
async def test_reference_provider_rejects_unknown_code_with_stable_error() -> None:
    module = _provider_module()
    provider = module.ReferencePartnerIdentityProvider({})

    with pytest.raises(PartnerIdentityError, match="^partner_auth_invalid$") as caught:
        await provider.finish_auth({"code": RAW_SUBJECT})

    assert caught.value.status_code == 401
    assert RAW_SUBJECT not in repr(caught.value)


def test_reference_provider_is_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", "reference")

    with pytest.raises(ValueError, match="partner_reference_provider_forbidden"):
        load_config()


def test_production_rejects_provider_without_registered_release(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", "unregistered-provider")

    with pytest.raises(ValueError, match="partner_provider_release_not_registered"):
        load_config()


def test_production_accepts_only_explicitly_registered_release(monkeypatch) -> None:
    module = _provider_module()
    module.register_partner_provider(
        "fixture-release",
        FakeProvider,
        production_release=True,
    )
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", "fixture-release")
    monkeypatch.setenv("PLATFORM_PARTNER_CALLBACK_METHOD", "POST")
    monkeypatch.setenv("PLATFORM_PARTNER_CALLBACK_PATH", "/partner-auth/complete")
    try:
        config = load_config()
    finally:
        module.unregister_partner_provider("fixture-release")

    assert config.environment == "production"
    assert config.partner_provider_kind == "fixture-release"
    assert config.partner_callback_method == "POST"
    assert config.partner_callback_path == "/partner-auth/complete"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/partner-auth/callback"),
        ("GET", "/partner-auth/callback/"),
        ("GET", "/partner-auth/{callback}"),
        ("GET", "/partner-auth/callback?code=1"),
        ("GET", "/partner-auth/../callback"),
        ("GET", "/partner-auth//callback"),
        ("GET", "/partner-auth/回调"),
        ("GET", "/another/callback"),
    ],
)
def test_partner_callback_config_rejects_non_exact_boundary(
    monkeypatch, method: str, path: str
) -> None:
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "development")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", "reference")
    monkeypatch.setenv("PLATFORM_PARTNER_CALLBACK_METHOD", method)
    monkeypatch.setenv("PLATFORM_PARTNER_CALLBACK_PATH", path)

    with pytest.raises(ValueError, match="partner_callback"):
        load_config()


def test_begin_auth_stores_only_digest_and_has_fixed_fae_return_path() -> None:
    broker, provider, service = _broker()

    started = broker.begin_auth()

    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    assert started.return_path == "/app/"
    assert state == provider.state
    assert service.state_digest is not None
    assert len(service.state_digest) == 32
    assert state.encode() != service.state_digest
    assert RAW_SUBJECT not in repr(started)


@pytest.mark.asyncio
async def test_callback_consumes_state_before_identity_resolution() -> None:
    broker, provider, service = _broker()
    broker.begin_auth()
    service.resolution = PartnerIdentityResolution(
        subject_id=SUBJECT_ID,
        partner_operator_id=UUID("30000000-0000-4000-8000-000000000001"),
        partner_organization_id=UUID("20000000-0000-4000-8000-000000000001"),
        binding_request_id=None,
        status="linked",
    )

    result = await broker.finish_auth({"state": provider.state, "code": "ok"})

    assert provider.events == [
        "provider.begin",
        "state.consume",
        "provider.finish",
        "provider.check",
    ]
    assert service.calls == [
        "state.create",
        "state.consume",
        "identity.resolve",
        "access.decide",
    ]
    assert result.status == "partner_authenticated"
    assert result.return_path == "/app/"
    assert RAW_SUBJECT not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["partner_auth_state_expired", "partner_auth_state_replay"]
)
async def test_expired_or_replayed_state_stops_before_provider(
    code: str,
) -> None:
    broker, provider, service = _broker()
    broker.begin_auth()
    service.consume_error = PartnerIdentityError(code, 401)

    with pytest.raises(PartnerIdentityError, match=f"^{code}$") as caught:
        await broker.finish_auth({"state": provider.state, "code": "ok"})

    assert caught.value.status_code == 401
    assert provider.events == ["provider.begin", "state.consume"]
    assert "identity.resolve" not in service.calls


@pytest.mark.asyncio
async def test_callback_error_is_explicit_and_state_is_already_consumed() -> None:
    broker, provider, service = _broker()
    broker.begin_auth()

    with pytest.raises(PartnerIdentityError, match="^authentication_cancelled$"):
        await broker.finish_auth(
            {
                "state": provider.state,
                "error": "access_denied",
                "error_description": RAW_SUBJECT,
            }
        )

    assert service.consumed is True
    assert provider.events == ["provider.begin", "state.consume"]
    assert "identity.resolve" not in service.calls


@pytest.mark.asyncio
async def test_provider_failure_is_stable_unavailable_and_redacted(caplog) -> None:
    broker, provider, service = _broker()
    broker.begin_auth()
    provider.finish_error = RuntimeError(RAW_SUBJECT)

    with pytest.raises(
        PartnerIdentityError, match="^partner_identity_unavailable$"
    ) as caught:
        await broker.finish_auth({"state": provider.state, "code": "ok"})

    assert caught.value.status_code == 503
    assert RAW_SUBJECT not in caplog.text
    assert RAW_SUBJECT not in repr(caught.value)
    assert "identity.resolve" not in service.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["begin", "finish", "check"])
async def test_provider_owned_errors_cannot_escape_the_stable_boundary(
    stage: str, caplog
) -> None:
    broker, provider, service = _broker()
    provider_error = PartnerIdentityError(RAW_SUBJECT, 418)
    if stage == "begin":
        provider.begin_error = provider_error
    else:
        broker.begin_auth()
        if stage == "finish":
            provider.finish_error = provider_error
        else:
            provider.check_error = provider_error

    with pytest.raises(
        PartnerIdentityError, match="^partner_identity_unavailable$"
    ) as caught:
        if stage == "begin":
            broker.begin_auth()
        else:
            await broker.finish_auth({"state": provider.state, "code": "ok"})

    assert caught.value.status_code == 503
    assert RAW_SUBJECT not in caplog.text
    assert RAW_SUBJECT not in repr(caught.value)
    assert "identity.resolve" not in service.calls


def test_provider_owned_error_is_redacted_from_callback_response() -> None:
    client, provider, _service = _router_client()
    start = client.get("/partner-auth/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    provider.finish_error = PartnerIdentityError(RAW_SUBJECT, 418)

    response = client.get(
        "/partner-auth/callback",
        params={"state": state, "code": "ok"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "partner_identity_unavailable"}}
    assert RAW_SUBJECT not in response.text


@pytest.mark.asyncio
async def test_inactive_provider_subject_fails_closed() -> None:
    broker, provider, service = _broker()
    broker.begin_auth()
    provider.subject_status = "inactive"

    with pytest.raises(
        PartnerIdentityError, match="^provider_identity_inactive$"
    ) as caught:
        await broker.finish_auth({"state": provider.state, "code": "ok"})

    assert caught.value.status_code == 403
    assert "identity.resolve" not in service.calls


@pytest.mark.asyncio
async def test_locally_revoked_provider_mapping_fails_closed() -> None:
    broker, provider, service = _broker()
    broker.begin_auth()
    service.resolution = PartnerIdentityResolution(
        subject_id=SUBJECT_ID,
        partner_operator_id=UUID("30000000-0000-4000-8000-000000000001"),
        partner_organization_id=UUID("20000000-0000-4000-8000-000000000001"),
        binding_request_id=None,
        status="revoked",
    )

    with pytest.raises(
        PartnerIdentityError, match="^provider_identity_inactive$"
    ) as caught:
        await broker.finish_auth({"state": provider.state, "code": "ok"})

    assert caught.value.status_code == 403
    assert "access.decide" not in service.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "subject_inactive",
        "organization_inactive",
        "operator_inactive",
        "fae_access_denied",
    ],
)
async def test_linked_inactive_layer_fails_closed(reason: str) -> None:
    broker, provider, service = _broker()
    broker.begin_auth()
    service.resolution = PartnerIdentityResolution(
        subject_id=SUBJECT_ID,
        partner_operator_id=UUID("30000000-0000-4000-8000-000000000001"),
        partner_organization_id=UUID("20000000-0000-4000-8000-000000000001"),
        binding_request_id=None,
        status="linked",
    )
    service.access_reason = reason

    with pytest.raises(PartnerIdentityError, match=f"^{reason}$") as caught:
        await broker.finish_auth({"state": provider.state, "code": "ok"})

    assert caught.value.status_code == 403


def test_unknown_identity_returns_binding_required_without_platform_access(
    caplog,
) -> None:
    client, provider, service = _router_client()
    start = client.get("/partner-auth/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    response = client.get(
        "/partner-auth/callback",
        params={"state": state, "code": "ok"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "partner_binding_required"}}
    assert "set-cookie" not in response.headers
    assert "location" not in response.headers
    assert SUBJECT_ID.hex not in response.text
    assert RAW_SUBJECT not in response.text
    assert RAW_SUBJECT not in caplog.text
    assert service.calls == ["state.create", "state.consume", "identity.resolve"]
    assert provider.events[-2:] == ["provider.finish", "provider.check"]


def test_callback_replay_is_rejected_before_provider_is_called_again() -> None:
    client, provider, _service = _router_client()
    start = client.get("/partner-auth/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    first = client.get("/partner-auth/callback", params={"state": state, "code": "ok"})
    second = client.get("/partner-auth/callback", params={"state": state, "code": "ok"})

    assert first.status_code == 403
    assert second.status_code == 401
    assert second.json() == {"detail": {"code": "partner_auth_state_replay"}}
    assert provider.events.count("provider.finish") == 1


def test_configured_post_callback_accepts_form_encoded_provider_fields() -> None:
    client, provider, _service = _router_client(
        callback_method="POST", callback_path="/partner-auth/complete"
    )
    start = client.get("/partner-auth/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    response = client.post(
        "/partner-auth/complete",
        data={"state": state, "code": "ok"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "partner_binding_required"}}
    assert provider.events.count("provider.finish") == 1


def test_start_rejects_arbitrary_return_url() -> None:
    client, _provider, service = _router_client()

    response = client.get(
        "/partner-auth/start",
        params={"return_url": "https://attacker.invalid/"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "partner_return_path_invalid"}}
    assert service.calls == []


def test_main_mounts_only_configured_partner_start_and_callback(
    tmp_path, monkeypatch
) -> None:
    provider = FakeProvider()
    service = FakePartnerService(provider)
    auth = FakeAuth()
    auth.secrets = AuthSecrets(b"s" * 32, key_version=7)
    monkeypatch.setenv("PLATFORM_PARTNER_CALLBACK_METHOD", "POST")
    monkeypatch.setenv("PLATFORM_PARTNER_CALLBACK_PATH", "/partner-auth/complete")

    app = _app(
        tmp_path,
        monkeypatch,
        auth,
        partner_service=service,
        partner_provider=provider,
    )
    client = TestClient(app)

    start = client.get("/partner-auth/start", follow_redirects=False)
    wrong_callback = client.get("/partner-auth/complete")
    exact_callback = client.post("/partner-auth/complete")
    nearby = client.get("/partner-auth/reference")

    assert start.status_code == 302
    assert wrong_callback.status_code == 404
    assert exact_callback.status_code == 401
    assert exact_callback.json() == {"detail": {"code": "partner_auth_state_invalid"}}
    assert nearby.status_code == 404


def test_configured_provider_fails_startup_without_partner_service(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    auth.secrets = AuthSecrets(b"s" * 32, key_version=7)
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", "reference")

    with pytest.raises(RuntimeError, match="^partner_identity_unavailable$"):
        _app(tmp_path, monkeypatch, auth)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/partner-auth/start", True),
        ("HEAD", "/partner-auth/start", False),
        ("GET", "/partner-auth/start/", False),
        ("GET", "/partner-auth/complete", False),
        ("POST", "/partner-auth/complete", True),
        ("GET", "/partner-auth/reference", False),
        ("GET", "/partner-auth", False),
        ("GET", "/office/", False),
        ("GET", "/api/v1/auth/dingtalk/callback", True),
    ],
)
def test_partner_public_route_allowlist_is_exact(
    method: str, path: str, expected: bool
) -> None:
    assert (
        is_public_request(
            method,
            path,
            "/",
            partner_callback_method="POST",
            partner_callback_path="/partner-auth/complete",
        )
        is expected
    )


def test_every_other_partner_auth_route_is_denied_even_with_platform_session() -> None:
    invoked: list[str] = []

    class Auth:
        route_prefix = "/"
        cookie_name = "session"
        csrf_cookie_name = "csrf"
        public_base_url = "https://agent.example.test"
        trusted_proxy_networks = ()
        rate_limiter = None

        def authenticate(self, _token):
            return (
                AuthContext(SUBJECT_ID, Role.PLATFORM_OWNER, SUBJECT_ID, False),
                b"csrf",
            )

        def verify_csrf(self, *_args):
            return True

    app = FastAPI()

    @app.get("/partner-auth/reference")
    def forbidden_reference_route():
        invoked.append("reference")
        return {"unsafe": True}

    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=Auth(),
        public_assets=frozenset(),
        authorization=AuthorizationService(
            type("Grants", (), {"permits": lambda *_: True})()
        ),
        routes=tuple(app.router.routes),
        partner_callback_method="GET",
        partner_callback_path="/partner-auth/callback",
    )
    client = TestClient(app)
    client.cookies.set("session", "platform-owner-session")

    response = client.get("/partner-auth/reference")

    assert response.status_code == 404
    assert invoked == []


def test_partner_public_callback_rejects_percent_encoded_path_alias() -> None:
    invoked: list[str] = []

    class Auth:
        route_prefix = "/"
        cookie_name = "session"
        csrf_cookie_name = "csrf"
        public_base_url = "https://agent.example.test"
        trusted_proxy_networks = ()
        rate_limiter = None

    app = FastAPI()

    @app.get("/partner-auth/callback")
    def partner_callback():
        invoked.append("callback")
        return {"ok": True}

    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=Auth(),
        public_assets=frozenset(),
        routes=tuple(app.router.routes),
        partner_callback_method="GET",
        partner_callback_path="/partner-auth/callback",
    )

    response = TestClient(app).get("/partner-auth/%63allback")

    assert response.status_code == 404
    assert invoked == []


def test_exact_post_callback_does_not_require_platform_origin_or_session() -> None:
    invoked: list[str] = []

    class Auth:
        route_prefix = "/"
        cookie_name = "session"
        csrf_cookie_name = "csrf"
        public_base_url = "https://agent.example.test"
        trusted_proxy_networks = ()
        rate_limiter = None

        def authenticate(self, _token):
            raise AssertionError("Partner callback must not consume Platform Session")

    app = FastAPI()

    @app.post("/partner-auth/complete")
    def partner_callback():
        invoked.append("callback")
        return {"ok": True}

    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=Auth(),
        public_assets=frozenset(),
        routes=tuple(app.router.routes),
        partner_callback_method="POST",
        partner_callback_path="/partner-auth/complete",
    )

    response = TestClient(app).post("/partner-auth/complete")

    assert response.status_code == 200
    assert invoked == ["callback"]
    assert response.headers["cache-control"] == "no-store"


def test_partner_state_migration_has_least_privilege_atomic_boundary() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())
    compact = sql.replace(" ", "")

    assert "create function platform_control.create_partner_login_attempt_v56" in sql
    assert "create function platform_control.consume_partner_login_attempt_v56" in sql
    assert sql.count("security definer") >= 2
    assert sql.count("require_partner_app_v54") >= 2
    assert "interval'10minutes'" in compact
    assert "for update" in sql
    assert (
        "state_digest bytea"
        not in sql.split(
            "create function platform_control.create_partner_login_attempt_v56", 1
        )[0]
    )
    assert (
        "grant execute on function platform_control.create_partner_login_attempt_v56"
        in sql
    )
    assert (
        "grant execute on function platform_control.consume_partner_login_attempt_v56"
        in sql
    )
    assert (
        "revoke all on function platform_control.create_partner_login_attempt_v56"
        in sql
    )
    assert (
        "revoke all on function platform_control.consume_partner_login_attempt_v56"
        in sql
    )


def _partner_repository(database_url: str) -> PartnerRepository:
    encryption = IdentityKeyring(1, "partner-provider-encryption", {1: b"e" * 32})
    lookup = IdentityKeyring(
        1,
        "partner-provider-lookup-hmac",
        {1: b"h" * 32},
        transition_versions=(1,),
    )
    return PartnerRepository(
        database_url,
        identity_codec=PartnerProviderIdentityCodec(encryption, lookup),
    )


@pytest.mark.postgres
def test_partner_login_state_is_digest_only_single_use_and_explicitly_expired(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    repository = _partner_repository(environment["urls"]["platform_control_app"])
    secrets = AuthSecrets(b"s" * 32, key_version=7)
    raw_state = secrets.random_token()
    state_digest = secrets.digest("oauth-state", raw_state)

    attempt_id = repository.create_login_attempt(
        provider_kind="fixture",
        state_digest=state_digest,
        state_key_version=secrets.key_version,
    )
    repository.consume_login_attempt(
        provider_kind="fixture",
        state_digest=state_digest,
        state_key_version=secrets.key_version,
    )
    with pytest.raises(PartnerRepositoryError, match="^partner_auth_state_replay$"):
        repository.consume_login_attempt(
            provider_kind="fixture",
            state_digest=state_digest,
            state_key_version=secrets.key_version,
        )

    expired_state = secrets.random_token()
    expired_digest = secrets.digest("oauth-state", expired_state)
    expired_id = repository.create_login_attempt(
        provider_kind="fixture",
        state_digest=expired_digest,
        state_key_version=secrets.key_version,
    )
    with psycopg.connect(environment["admin"]) as connection:
        stored = connection.execute(
            "select state_digest,status,expires_at-created_at from "
            "platform_control.partner_login_attempts where login_attempt_id=%s",
            (attempt_id,),
        ).fetchone()
        assert bytes(stored[0]) == state_digest
        assert stored[1] == "consumed"
        assert stored[2].total_seconds() == pytest.approx(600, abs=1)
        assert raw_state.encode() not in bytes(stored[0])
        connection.execute(
            "update platform_control.partner_login_attempts "
            "set created_at=clock_timestamp()-interval '11 minutes',"
            "expires_at=clock_timestamp()-interval '1 minute' "
            "where login_attempt_id=%s",
            (expired_id,),
        )
    with pytest.raises(PartnerRepositoryError, match="^partner_auth_state_expired$"):
        repository.consume_login_attempt(
            provider_kind="fixture",
            state_digest=expired_digest,
            state_key_version=secrets.key_version,
        )


@pytest.mark.postgres
def test_partner_login_state_functions_are_app_only(control_database) -> None:
    for environment in control_database["environments"].values():
        selected_app = environment["roles"][1]
        with psycopg.connect(environment["admin"]) as connection:
            rows = connection.execute(
                "select role_name,has_function_privilege(role_name,%s,'EXECUTE'),"
                "has_function_privilege(role_name,%s,'EXECUTE') "
                "from unnest(%s::text[]) roles(role_name)",
                (
                    "platform_control.create_partner_login_attempt_v56(uuid,text,bytea,integer)",
                    "platform_control.consume_partner_login_attempt_v56(text,bytea,integer)",
                    ["public", *ROLES],
                ),
            ).fetchall()
            assert all(
                (create_allowed and consume_allowed)
                if role_name == selected_app
                else (not create_allowed and not consume_allowed)
                for role_name, create_allowed, consume_allowed in rows
            )
            assert not connection.execute(
                "select has_table_privilege(%s,"
                "'platform_control.partner_login_attempts','INSERT,UPDATE,DELETE')",
                (selected_app,),
            ).fetchone()[0]
