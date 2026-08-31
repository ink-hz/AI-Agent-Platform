from datetime import UTC, datetime, timedelta
import hashlib
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.control_plane.audit import AuditUnavailableError, AuditWriter
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.fae_workbench import routes as fae_workbench_routes
from app.fae_workbench.models import FaeOperationalSnapshot
from app.fae_workbench.service import FaeWorkbenchService
from app.observability.models import SessionDetail, TurnDetail


class _Observability:
    def __init__(self, *, before_detail_load=None) -> None:
        self.filters = None
        self.detail_loads = []
        self.before_detail_load = before_detail_load

    async def list_sessions(self, filters, limit, offset):
        self.filters = filters
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    async def get_session(self, session_key):
        if self.before_detail_load is not None:
            self.before_detail_load()
        self.detail_loads.append(session_key)
        agent_id, source_kind = (
            ("ai-admin-agent", "admin")
            if session_key == "admin:session-1"
            else ("ai-fae-agent", "fae")
        )
        now = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
        return SessionDetail(
            session_key=session_key,
            agent_id=agent_id,
            source_kind=source_kind,
            channel="dingtalk",
            title="Sensitive session",
            created_at=now - timedelta(minutes=1),
            last_active_at=now,
            turn_count=1,
            feedback_count=0,
            review_count=0,
            freshness="fresh",
            turns=[
                TurnDetail(
                    turn_key=f"{session_key}:turn-1",
                    session_key=session_key,
                    agent_id=agent_id,
                    source_kind=source_kind,
                    turn_index=1,
                    question="sensitive question",
                    answer="sensitive answer",
                    created_at=now,
                )
            ],
        )


class _Review:
    async def overview(self, *, agent_id):
        assert agent_id == "ai-fae-agent"
        return {"statuses": {"open": 1}}


class _Repository:
    def snapshot(self, period_start, period_end):
        return FaeOperationalSnapshot(
            period_start=period_start,
            period_end=period_end,
            data_as_of=period_end - timedelta(hours=1),
            session_count=1,
            active_subject_count=1,
            negative_feedback_events=0,
            negative_turn_count=0,
            abnormal_session_count=0,
            p50_duration_ms=10,
            p95_duration_ms=10,
        )


class _Grants:
    def permits(self, _actor, _agent_id):  # pragma: no cover - denied before lookup
        raise AssertionError("FAE workbench must not use viewer grants")


class _AuditRepository:
    def __init__(self, fail_result: str | None = None) -> None:
        self.events = []
        self.sanitized = []
        self.fail_result = fail_result

    def append(self, event_id, command, sanitized):
        if sanitized["result"] == self.fail_result:
            raise AuditUnavailableError("required audit unavailable")
        self.events.append(command)
        self.sanitized.append(sanitized)
        return event_id


class _Auth:
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    route_prefix = "/"
    public_base_url = "https://testserver"
    trusted_proxy_networks = ()
    rate_limiter = None

    def __init__(self, sessions):
        self.sessions = sessions
        self.hard_stale_reads = []

    def authenticate(self, token):
        context = self.sessions.get(token)
        return (context, "csrf") if context is not None else None

    def verify_csrf(self, *_args):
        return True

    def hard_stale_audit(self, actor, access_kind, target):
        self.hard_stale_reads.append((actor, access_kind, target))


def _protected_app(
    role: Role,
    *,
    hard_stale: bool = False,
    fail_audit_result: str | None = None,
):
    audit_repository = _AuditRepository(fail_audit_result)
    detail_load_audit_counts = []
    observability = _Observability(
        before_detail_load=lambda: detail_load_audit_counts.append(
            len(audit_repository.events)
        )
    )
    app = FastAPI()
    app.state.fae_workbench_service = FaeWorkbenchService(
        _Repository(), observability, _Review()
    )
    app.state.fae_session_read_audit = AuditWriter(audit_repository)
    app.include_router(fae_workbench_routes.router)
    context = AuthContext(uuid4(), role, uuid4(), hard_stale)
    auth = _Auth({"valid": context})
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(_Grants()),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app)
    return (
        client,
        observability,
        audit_repository,
        detail_load_audit_counts,
        auth,
    )


def _direct_app(context: AuthContext | None):
    observability = _Observability()
    app = FastAPI()
    app.state.fae_workbench_service = FaeWorkbenchService(
        _Repository(), observability, _Review()
    )
    app.state.fae_session_read_audit = AuditWriter(_AuditRepository())
    app.include_router(fae_workbench_routes.router)
    if context is not None:
        @app.middleware("http")
        async def inject_auth_context(request, call_next):
            request.state.auth_context = context
            return await call_next(request)
    return app, observability


def test_fae_session_api_ignores_conflicting_scope() -> None:
    client, observability, *_ = _protected_app(Role.PLATFORM_OWNER)
    client.cookies.set("session", "valid")

    response = client.get(
        "/api/admin/fae/sessions"
        "?agent_id=ai-admin-agent&source_kind=admin&source_environment=staging&q=335"
    )

    assert response.status_code == 200
    assert observability.filters.agent_id == "ai-fae-agent"
    assert observability.filters.source_kind == "fae"
    assert observability.filters.query == "335"


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [(None, 401), (Role.MEMBER, 403), (Role.MANAGEMENT_VIEWER, 403)],
)
def test_router_itself_rejects_missing_member_and_viewer_contexts(
    role: Role | None, expected_status: int
) -> None:
    context = (
        AuthContext(uuid4(), role, uuid4(), False) if role is not None else None
    )
    app, _observability = _direct_app(context)

    assert (
        TestClient(app).get("/api/admin/fae/sessions").status_code
        == expected_status
    )


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_router_itself_allows_exact_management_contexts(role: Role) -> None:
    context = AuthContext(uuid4(), role, uuid4(), False)
    app, _observability = _direct_app(context)

    assert TestClient(app).get("/api/admin/fae/sessions").status_code == 200


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_owner_and_admin_can_read_fae_workbench(role: Role) -> None:
    client, *_ = _protected_app(role)
    client.cookies.set("session", "valid")

    assert client.get("/api/admin/fae/overview").status_code == 200
    assert client.get("/api/admin/fae/sessions").status_code == 200


def test_fae_workbench_rejects_unauthenticated_member_and_viewer() -> None:
    owner_client, *_ = _protected_app(Role.PLATFORM_OWNER)
    assert owner_client.get("/api/admin/fae/sessions").status_code == 401

    for role in (Role.MEMBER, Role.MANAGEMENT_VIEWER):
        client, *_ = _protected_app(role)
        client.cookies.set("session", "valid")
        assert client.get("/api/admin/fae/sessions").status_code == 403


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_hard_stale_owner_and_admin_keep_read_access(role: Role) -> None:
    client, _observability, _audit, _counts, auth = _protected_app(
        role, hard_stale=True
    )
    client.cookies.set("session", "valid")

    assert client.get("/api/admin/fae/overview").status_code == 200
    assert len(auth.hard_stale_reads) == 1


def test_fae_detail_returns_404_for_other_agent() -> None:
    client, _observability, audit, counts, _auth = _protected_app(
        Role.PLATFORM_OWNER
    )
    client.cookies.set("session", "valid")

    response = client.get("/api/admin/fae/sessions/admin%3Asession-1")

    assert response.status_code == 404
    assert counts == [1]
    assert [event.event_type for event in audit.events] == [
        "fae_session_detail_read_requested",
        "fae_session_detail_read_failed",
    ]


def test_fae_detail_records_privileged_read_without_content_or_raw_key() -> None:
    client, _observability, audit, counts, _auth = _protected_app(
        Role.PLATFORM_OWNER
    )
    client.cookies.set("session", "valid")

    response = client.get("/api/admin/fae/sessions/fae%3Asession-1")

    assert response.status_code == 200
    assert counts == [1]
    assert [event.event_type for event in audit.events] == [
        "fae_session_detail_read_requested",
        "fae_session_detail_read_completed",
    ]
    assert audit.events[-1].target_type == "fae_session"
    assert audit.events[-1].target_id == hashlib.sha256(
        b"fae:session-1"
    ).hexdigest()
    assert "fae:session-1" not in repr(audit.events)
    assert "sensitive question" not in repr(audit.events)
    assert set(audit.sanitized[-1]) == {
        "operation_id",
        "linked_audit_event_id",
        "result",
    }


@pytest.mark.parametrize("failed_result", ["requested", "completed"])
def test_fae_detail_fails_closed_when_required_audit_is_unavailable(
    failed_result: str,
) -> None:
    client, observability, _audit, _counts, _auth = _protected_app(
        Role.PLATFORM_OWNER, fail_audit_result=failed_result
    )
    client.cookies.set("session", "valid")

    response = client.get("/api/admin/fae/sessions/fae%3Asession-1")

    assert response.status_code == 503
    assert "sensitive question" not in response.text
    assert observability.detail_loads == (
        [] if failed_result == "requested" else ["fae:session-1"]
    )
