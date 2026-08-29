from __future__ import annotations

from datetime import UTC, datetime, timedelta
from ipaddress import ip_network
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from test_agent_brain_migration import _insert_grant, _seed_active_directory
from test_control_plane_migration import (  # noqa: F401 - registers pytest fixture
    control_database as _control_database_fixture,
)

from app.control_plane.models import AuthContext, Role

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
USER_ID = UUID("d04d746e-6c19-4bfd-a4ef-0f08666599d0")
SESSION_ID = UUID("714edc72-bbc8-45fd-9077-eec4d9e29091")
BINDING_ID = UUID("53905900-cd7d-4a0e-a29f-35de441fd8c9")
PARTNER_SUBJECT_ID = UUID("41969b1e-223d-47f5-88bc-0ad88b065240")


class FakeSecrets:
    key_version = 7

    @staticmethod
    def random_token() -> str:
        return "single-use-agent-launch-code-123456"

    @staticmethod
    def digest(purpose: str, value: str) -> bytes:
        assert purpose == "agent-launch"
        assert value == "single-use-agent-launch-code-123456"
        return b"h" * 32


class FakeAuthorization:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[UUID, str]] = []

    def decide_for_user_id(self, internal_user_id: UUID, agent_id: str):
        from app.agent_brain.authorization import AgentUseDecision

        self.calls.append((internal_user_id, agent_id))
        return AgentUseDecision(self.allowed, (), uuid4())


class FakeRepository:
    def __init__(self) -> None:
        self.issued: list[dict[str, object]] = []
        self.consumed = False
        self.active = True

    def issue(self, **values):
        self.issued.append(values)
        return NOW + timedelta(seconds=60)

    def exchange(self, *, code_digest: bytes, code_key_version: int, now: datetime):
        assert code_digest == b"h" * 32
        assert code_key_version == 7
        assert now == NOW
        if self.consumed:
            return None
        self.consumed = True
        return (
            USER_ID,
            "enterprise_member",
            BINDING_ID,
            "ai-fae-agent",
            USER_ID,
            "Launch User",
        )

    def validate_binding(self, *, binding_id: UUID, agent_id: str, now: datetime):
        assert binding_id == BINDING_ID
        assert agent_id == "ai-fae-agent"
        assert now == NOW
        return (
            USER_ID,
            "enterprise_member",
            BINDING_ID,
            "ai-fae-agent",
            USER_ID,
            "Launch User",
            self.active,
        )

    def revoke_binding(self, **_values):
        raise AssertionError("enterprise test must not revoke through partner boundary")


class GenericFakeRepository:
    def __init__(self) -> None:
        self.issued: list[dict[str, object]] = []
        self.exchanges: list[tuple[object, ...]] = []
        self.validations: list[tuple[object, ...]] = []
        self.revoked: list[tuple[UUID, str]] = []

    def issue(self, **values):
        self.issued.append(values)
        return NOW + timedelta(seconds=60)

    def exchange(self, **_values):
        return self.exchanges.pop(0) if self.exchanges else None

    def validate_binding(self, **_values):
        return self.validations.pop(0) if self.validations else None

    def revoke_binding(self, *, binding_id: UUID, agent_id: str, now: datetime):
        assert now == NOW
        self.revoked.append((binding_id, agent_id))


class FakePartnerProvider:
    kind = "fixture"


class FakePartnerLaunchService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, object]] = []
        self.error = None

    async def require_active_fae_subject(self, subject_id: UUID, provider):
        self.calls.append((subject_id, provider))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            subject_id=subject_id,
            display_name="Partner Operator",
            partner_display_name="Partner Organization",
        )


def _generic_service():
    from app.control_plane.agent_launch import AgentLaunchService

    repository = GenericFakeRepository()
    partner_service = FakePartnerLaunchService()
    partner_provider = FakePartnerProvider()
    service = AgentLaunchService(
        repository=repository,
        secrets=FakeSecrets(),
        authorization=FakeAuthorization(),
        partner_service=partner_service,
        partner_provider=partner_provider,
        clock=lambda: NOW,
    )
    return service, repository, partner_service, partner_provider


@pytest.mark.asyncio
async def test_partner_launch_contains_only_generic_subject() -> None:
    service, repository, partner_service, partner_provider = _generic_service()
    repository.exchanges.append(
        (
            PARTNER_SUBJECT_ID,
            "partner_operator",
            BINDING_ID,
            "ai-fae-agent",
            None,
            None,
        )
    )

    issued = await service.issue_partner(PARTNER_SUBJECT_ID)
    exchanged = await service.exchange(issued.code)

    assert issued.launch_url == (
        "https://fae.orbbec.com.cn/app/"
        "#partner_launch=single-use-agent-launch-code-123456"
    )
    assert repository.issued == [
        {
            "code_digest": b"h" * 32,
            "code_key_version": 7,
            "subject_id": PARTNER_SUBJECT_ID,
            "subject_type": "partner_operator",
            "source_session_id": None,
            "internal_user_id": None,
            "agent_id": "ai-fae-agent",
            "binding_id": issued.binding_id,
            "now": NOW,
            "ttl_seconds": 60,
        }
    ]
    assert partner_service.calls == [
        (PARTNER_SUBJECT_ID, partner_provider),
        (PARTNER_SUBJECT_ID, partner_provider),
    ]
    assert exchanged.subject_id == PARTNER_SUBJECT_ID
    assert exchanged.subject_type == "partner_operator"
    assert exchanged.internal_user_id is None
    assert exchanged.agent_id == "ai-fae-agent"
    assert exchanged.display_name == "Partner Operator"
    assert exchanged.partner_display_name == "Partner Organization"


@pytest.mark.asyncio
async def test_enterprise_launch_preserves_internal_user_projection() -> None:
    service, repository, partner_service, _provider = _generic_service()
    repository.exchanges.append(
        (
            USER_ID,
            "enterprise_member",
            BINDING_ID,
            "ai-fae-agent",
            USER_ID,
            "Launch User",
        )
    )
    context = AuthContext(USER_ID, Role.MEMBER, SESSION_ID, False)

    exchanged = await service.exchange(service.issue_enterprise(context).code)

    assert repository.issued[0]["subject_id"] == USER_ID
    assert repository.issued[0]["subject_type"] == "enterprise_member"
    assert exchanged.subject_id == USER_ID
    assert exchanged.subject_type == "enterprise_member"
    assert exchanged.internal_user_id == USER_ID
    assert exchanged.display_name == "Launch User"
    assert exchanged.partner_display_name is None
    assert partner_service.calls == []


@pytest.mark.asyncio
async def test_partner_provider_inactive_revokes_binding_and_fails_403() -> None:
    from app.control_plane.agent_launch import AgentLaunchError
    from app.control_plane.partner_models import PartnerIdentityError

    service, repository, partner_service, _provider = _generic_service()
    repository.validations.append(
        (
            PARTNER_SUBJECT_ID,
            "partner_operator",
            BINDING_ID,
            "ai-fae-agent",
            None,
            None,
            True,
        )
    )
    partner_service.error = PartnerIdentityError("provider_identity_inactive", 403)

    with pytest.raises(AgentLaunchError, match="^provider_identity_inactive$") as caught:
        await service.validate_binding(BINDING_ID, "ai-fae-agent")

    assert caught.value.status_code == 403
    assert repository.revoked == [(BINDING_ID, "ai-fae-agent")]


@pytest.mark.asyncio
async def test_partner_provider_unavailable_is_stable_and_never_cached(caplog) -> None:
    from app.control_plane.agent_launch import AgentLaunchError
    from app.control_plane.partner_models import PartnerIdentityError

    service, repository, partner_service, _provider = _generic_service()
    raw_provider_subject = "raw-provider-seat@example.invalid"
    partner_service.error = PartnerIdentityError("partner_identity_unavailable", 503)

    for _attempt in range(2):
        with pytest.raises(
            AgentLaunchError, match="^partner_identity_unavailable$"
        ) as caught:
            await service.issue_partner(PARTNER_SUBJECT_ID)
        assert caught.value.status_code == 503

    assert len(partner_service.calls) == 2
    assert repository.issued == []
    assert raw_provider_subject not in caplog.text
    assert raw_provider_subject not in repr(caught.value)


def _service(*, allowed: bool = True):
    from app.control_plane.agent_launch import AgentLaunchService

    repository = FakeRepository()
    authorization = FakeAuthorization(allowed=allowed)
    service = AgentLaunchService(
        repository=repository,
        secrets=FakeSecrets(),
        authorization=authorization,
        clock=lambda: NOW,
    )
    return service, repository, authorization


@pytest.mark.asyncio
async def test_launch_code_is_opaque_single_use_and_agent_authorized() -> None:
    from app.control_plane.agent_launch import AgentLaunchError

    service, repository, authorization = _service()
    context = AuthContext(USER_ID, Role.MEMBER, SESSION_ID, False)

    issued = service.issue(context, "ai-fae-agent")
    exchanged = await service.exchange(issued.code)

    assert issued.launch_url == (
        "https://fae.orbbec.com.cn/app/"
        "#platform_launch=single-use-agent-launch-code-123456"
    )
    assert issued.code not in repr(issued)
    assert issued.expires_at == NOW + timedelta(seconds=60)
    assert authorization.calls == [(USER_ID, "ai-fae-agent")]
    assert repository.issued == [
        {
            "code_digest": b"h" * 32,
            "code_key_version": 7,
            "subject_id": USER_ID,
            "subject_type": "enterprise_member",
            "source_session_id": SESSION_ID,
            "internal_user_id": USER_ID,
            "agent_id": "ai-fae-agent",
            "binding_id": issued.binding_id,
            "now": NOW,
            "ttl_seconds": 60,
        }
    ]
    assert exchanged.subject_id == USER_ID
    assert exchanged.subject_type == "enterprise_member"
    assert exchanged.internal_user_id == USER_ID
    assert exchanged.identity_binding_id == BINDING_ID
    assert (
        await service.validate_binding(BINDING_ID, "ai-fae-agent")
    ).active is True
    try:
        await service.exchange(issued.code)
    except AgentLaunchError as exc:
        assert exc.code == "launch_code_invalid"
    else:
        raise AssertionError("launch code was accepted twice")


def test_launch_fails_closed_without_agent_grant_or_with_hard_stale_session() -> None:
    from app.control_plane.agent_launch import AgentLaunchError

    denied, _, _ = _service(allowed=False)
    contexts = (
        (denied, AuthContext(USER_ID, Role.MEMBER, SESSION_ID, False), "agent_denied"),
        (
            _service()[0],
            AuthContext(USER_ID, Role.PLATFORM_OWNER, SESSION_ID, True),
            "directory_stale",
        ),
    )
    for service, context, code in contexts:
        try:
            service.issue(context, "ai-fae-agent")
        except AgentLaunchError as exc:
            assert exc.code == code
        else:
            raise AssertionError("unauthorized launch was issued")


@pytest.mark.asyncio
async def test_exchange_rejects_non_urlsafe_launch_code_before_repository_lookup() -> None:
    from app.control_plane.agent_launch import AgentLaunchError

    service, repository, _ = _service()

    with pytest.raises(AgentLaunchError, match="launch_code_invalid"):
        await service.exchange("/" * 43)

    assert repository.consumed is False


def test_launch_routes_return_only_minimal_exchange_identity() -> None:
    from app.control_plane.agent_launch import build_agent_launch_router

    service, _, _ = _service()
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.auth_context = AuthContext(
            USER_ID, Role.MEMBER, SESSION_ID, False
        )
        return await call_next(request)

    app.include_router(build_agent_launch_router(service))
    client = TestClient(app, client=("127.0.0.1", 51000))

    issue = client.post("/api/v1/agents/ai-fae-agent/launch")
    exchange = client.post(
        "/api/v1/internal/agent-launch/exchange",
        json={"code": "single-use-agent-launch-code-123456"},
    )
    validation = client.post(
        f"/api/v1/internal/agent-bindings/{BINDING_ID}/validate",
        json={"agent_id": "ai-fae-agent"},
    )

    assert issue.status_code == 200
    assert issue.headers["cache-control"] == "no-store"
    assert set(issue.json()) == {"launch_url", "expires_at"}
    assert exchange.status_code == 200
    assert exchange.json() == {
        "subject_id": str(USER_ID),
        "subject_type": "enterprise_member",
        "internal_user_id": str(USER_ID),
        "identity_binding_id": str(BINDING_ID),
        "agent_id": "ai-fae-agent",
        "display_name": "Launch User",
        "partner_display_name": None,
    }
    assert validation.json() == {
        "subject_id": str(USER_ID),
        "subject_type": "enterprise_member",
        "internal_user_id": str(USER_ID),
        "identity_binding_id": str(BINDING_ID),
        "agent_id": "ai-fae-agent",
        "display_name": "Launch User",
        "partner_display_name": None,
        "active": True,
    }
    serialized = exchange.text.lower() + validation.text.lower()
    for forbidden in ("department", "role", "dingtalk", "csrf"):
        assert forbidden not in serialized


def test_internal_launch_exchange_is_hidden_from_non_loopback_clients() -> None:
    from app.control_plane.agent_launch import build_agent_launch_router

    service, _, _ = _service()
    app = FastAPI()
    app.include_router(build_agent_launch_router(service))
    client = TestClient(app, client=("203.0.113.8", 51000))

    response = client.post(
        "/api/v1/internal/agent-launch/exchange",
        json={"code": "single-use-agent-launch-code-123456"},
    )

    assert response.status_code == 404


def test_internal_exchange_bypasses_browser_session_only_on_loopback() -> None:
    from app.control_plane.agent_launch import build_agent_launch_router
    from app.control_plane.middleware import IdentitySecurityMiddleware

    class BrowserAuth:
        route_prefix = "/"
        cookie_name = "__Host-platform_session"
        csrf_cookie_name = "__Host-platform_csrf"
        public_base_url = "https://agent.orbbec.com.cn"
        trusted_proxy_networks = (ip_network("127.0.0.1/32"),)

        @staticmethod
        def authenticate(_token):
            return None

    service, _, _ = _service()
    app = FastAPI()
    app.include_router(build_agent_launch_router(service))
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=BrowserAuth(),
        public_assets=frozenset(),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app, client=("127.0.0.1", 51000))

    loopback = client.post(
        "/api/v1/internal/agent-launch/exchange",
        json={"code": "single-use-agent-launch-code-123456"},
        headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-Proto": "http"},
    )
    public_edge = client.post(
        "/api/v1/internal/agent-launch/exchange",
        json={"code": "single-use-agent-launch-code-123456"},
        headers={"X-Real-IP": "203.0.113.8", "X-Forwarded-Proto": "https"},
    )

    assert loopback.status_code == 200
    assert loopback.headers["cache-control"] == "no-store"
    assert public_edge.status_code == 404


def test_create_app_mounts_authenticated_agent_launch(
    tmp_path, monkeypatch
) -> None:
    from test_dingtalk_auth_api import FakeAuth, _app

    auth = FakeAuth()
    service, _, _ = _service()
    client = TestClient(
        _app(
            tmp_path,
            monkeypatch,
            auth,
            agent_launch_service=service,
        )
    )

    response = client.post(
        "/api/v1/agents/ai-fae-agent/launch",
        cookies={auth.cookie_name: "valid-cookie", auth.csrf_cookie_name: auth.csrf},
        headers={"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf},
    )

    assert response.status_code == 200
    assert response.json()["launch_url"].startswith(
        "https://fae.orbbec.com.cn/app/#platform_launch="
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_launch_is_single_use_and_binding_tracks_source_session(
    _control_database_fixture,  # noqa: F811 - imported fixture name
) -> None:
    from app.agent_brain.authorization import AgentUseAuthorization
    from app.control_plane.agent_launch import (
        AgentLaunchError,
        AgentLaunchRepository,
        AgentLaunchService,
    )
    from app.control_plane.auth import AuthSecrets

    environment = _control_database_fixture["environments"]["production"]
    session_id = uuid4()
    actor_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        user_id, _root, _child, _generation = _seed_active_directory(connection)
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Launch Actor','active')",
            (actor_id,),
        )
        grant_id = _insert_grant(
            connection,
            agent_id="ai-fae-agent",
            target_kind="user",
            actor_id=actor_id,
            user_id=user_id,
        )
        connection.execute(
            "insert into platform_control.web_sessions "
            "(session_id,internal_user_id,token_hash,token_hash_key_version,"
            "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
            "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',"
            "now()+interval '2 hours')",
            (session_id, user_id, b"t" * 32, b"c" * 32),
        )

    service = AgentLaunchService(
        repository=AgentLaunchRepository(environment["urls"]["platform_control_app"]),
        secrets=AuthSecrets(b"l" * 32, key_version=1),
        authorization=AgentUseAuthorization(
            environment["urls"]["platform_control_app"]
        ),
    )
    issued = service.issue(AuthContext(user_id, Role.MEMBER, session_id, False), "ai-fae-agent")
    exchanged = await service.exchange(issued.code)

    assert exchanged.subject_id == user_id
    assert exchanged.subject_type == "enterprise_member"
    assert exchanged.internal_user_id == user_id
    assert exchanged.identity_binding_id == issued.binding_id
    assert (
        await service.validate_binding(issued.binding_id, "ai-fae-agent")
    ).active is True
    with pytest.raises(AgentLaunchError, match="launch_code_invalid"):
        await service.exchange(issued.code)

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.agent_use_grants "
            "set revoked_at=now(),revoked_by=%s where agent_use_grant_id=%s",
            (actor_id, grant_id),
        )
    assert (
        await service.validate_binding(issued.binding_id, "ai-fae-agent")
    ).active is False

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.agent_use_grants "
            "set revoked_at=null,revoked_by=null where agent_use_grant_id=%s",
            (grant_id,),
        )
    second = service.issue(
        AuthContext(user_id, Role.MEMBER, session_id, False), "ai-fae-agent"
    )
    await service.exchange(second.code)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.web_sessions set revoked_at=now() "
            "where session_id=%s",
            (session_id,),
        )
    assert (
        await service.validate_binding(second.binding_id, "ai-fae-agent")
    ).active is False
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_control.web_sessions where session_id=%s",
            (session_id,),
        )
        remaining = connection.execute(
            "select count(*) from platform_control.agent_identity_bindings "
            "where identity_binding_id=%s",
            (second.binding_id,),
        ).fetchone()[0]
    assert remaining == 0
