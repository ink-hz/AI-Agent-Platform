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
from app.review.repository import ReviewNotFound


ISSUE_ID = uuid4()
TARGET_ID = uuid4()
LINK_ID = uuid4()
EVIDENCE_ID = uuid4()
REPLAY_ID = uuid4()


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
    def __init__(self) -> None:
        self.issue_agent_id = "ai-fae-agent"
        self.target_agent_id = "ai-fae-agent"
        self.calls = []
        self.evidence_owner = ISSUE_ID
        self.replay_owner = ISSUE_ID

    async def agent_issue_scope_valid(self, agent_id):
        assert agent_id == "ai-fae-agent"
        return True

    async def overview(self, *, agent_id):
        assert agent_id == "ai-fae-agent"
        return {"statuses": {"open": 1}}

    async def inbox(self, *, agent_id, limit, offset):
        self.calls.append(("inbox", agent_id, limit, offset))
        return []

    async def list_issues(self, *, agent_id, limit, offset):
        self.calls.append(("issues", agent_id, limit, offset))
        return []

    async def turn_summaries(self, *, turn_keys):
        self.calls.append(("turn_summaries", turn_keys))
        return [{"turn_key": key} for key in turn_keys]

    async def issue_detail(self, issue_id):
        agent_id = (
            self.target_agent_id if issue_id == TARGET_ID else self.issue_agent_id
        )
        if agent_id is None:
            raise ReviewNotFound("issue not found")
        return {"issue": {"id": issue_id, "agent_id": agent_id}}

    async def evidence_issue_id(self, evidence_id):
        self.calls.append(("evidence_owner", evidence_id))
        if self.evidence_owner is None:
            raise ReviewNotFound("evidence not found")
        return self.evidence_owner

    async def replay_issue_id(self, replay_id):
        self.calls.append(("replay_owner", replay_id))
        if self.replay_owner is None:
            raise ReviewNotFound("replay not found")
        return self.replay_owner

    def __getattr__(self, name):
        async def record(*args, **kwargs):
            self.calls.append((name, *args, kwargs))
            issue_id = args[0] if args else ISSUE_ID
            return {"issue": {"id": issue_id, "agent_id": "ai-fae-agent"}}

        return record


class _Repository:
    def __init__(self) -> None:
        self.missing_turns = set()

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

    def fae_turn_exists(self, turn_key):
        return turn_key.startswith("fae:") and turn_key not in self.missing_turns

    def fae_turn_keys(self, turn_keys):
        return {key for key in turn_keys if self.fae_turn_exists(key)}


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
    cloud_mode: bool = False,
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
    repository = _Repository()
    review = _Review()
    app.state.fae_workbench_service = FaeWorkbenchService(
        repository, observability, review
    )
    app.state.fae_session_read_audit = AuditWriter(audit_repository)
    app.include_router(fae_workbench_routes.router)
    context = AuthContext(uuid4(), role, uuid4(), hard_stale)
    auth = _Auth({"valid": context})
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(_Grants(), cloud_mode=cloud_mode),
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
    repository = _Repository()
    review = _Review()
    app = FastAPI()
    app.state.fae_workbench_service = FaeWorkbenchService(
        repository, observability, review
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


def test_fae_issue_mutation_denials_report_hard_stale_before_cloud_read_only():
    stale_client, *_ = _protected_app(
        Role.PLATFORM_OWNER, hard_stale=True, cloud_mode=True
    )
    stale_client.cookies.set("session", "valid")
    cloud_client, *_ = _protected_app(Role.PLATFORM_OWNER, cloud_mode=True)
    cloud_client.cookies.set("session", "valid")

    stale = stale_client.post("/api/admin/fae/issues", json={"title": "issue"})
    cloud = cloud_client.post("/api/admin/fae/issues", json={"title": "issue"})

    assert (stale.status_code, stale.json()["detail"]) == (
        503,
        "hard_stale_read_only",
    )
    assert (cloud.status_code, cloud.json()["detail"]) == (
        403,
        "cloud_review_read_only",
    )


@pytest.mark.parametrize("cloud_mode", [False, True])
@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/admin/fae/issues"), ("post", "/api/admin/fae/issues")],
)
def test_stale_viewer_role_denial_precedes_freshness_and_cloud(method, path, cloud_mode):
    client, *_ = _protected_app(
        Role.MANAGEMENT_VIEWER, hard_stale=True, cloud_mode=cloud_mode
    )
    client.cookies.set("session", "valid")

    response = (
        client.post(path, json={"title": "issue"})
        if method == "post"
        else client.get(path)
    )

    assert (response.status_code, response.json()["detail"]) == (
        403,
        "management_role_required",
    )


def test_fae_issue_facade_exposes_exact_route_templates() -> None:
    routes = {
        (next(iter(route.methods)), route.path)
        for route in fae_workbench_routes.router.routes
        if getattr(route, "methods", None)
        and route.path.startswith("/api/admin/fae/")
    }

    issue_routes = routes - {
        ("GET", "/api/admin/fae/overview"),
        ("GET", "/api/admin/fae/sessions"),
        ("GET", "/api/admin/fae/sessions/{session_key}"),
    }

    assert issue_routes == {
        ("GET", "/api/admin/fae/issue-overview"),
        ("GET", "/api/admin/fae/issue-inbox"),
        ("GET", "/api/admin/fae/issues"),
        ("GET", "/api/admin/fae/issues/{issue_id}"),
        ("GET", "/api/admin/fae/turn-summaries"),
        ("POST", "/api/admin/fae/issues"),
        ("PATCH", "/api/admin/fae/issues/{issue_id}"),
        ("POST", "/api/admin/fae/issues/{issue_id}/links"),
        ("POST", "/api/admin/fae/issues/{issue_id}/links/{link_id}/move"),
        ("POST", "/api/admin/fae/issues/{issue_id}/merge"),
        ("POST", "/api/admin/fae/issues/{issue_id}/fix-ready"),
        ("POST", "/api/admin/fae/issues/{issue_id}/evidence"),
        ("POST", "/api/admin/fae/evidence/{evidence_id}/verify"),
        ("POST", "/api/admin/fae/issues/{issue_id}/replays"),
        ("POST", "/api/admin/fae/replays/{replay_id}/semantic-review"),
        ("POST", "/api/admin/fae/issues/{issue_id}/disposition"),
    }


def test_fae_issue_reads_are_scoped_and_cross_agent_detail_is_hidden() -> None:
    app, _observability = _direct_app(
        AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    )
    client = TestClient(app)
    review = app.state.fae_workbench_service._review

    assert client.get("/api/admin/fae/issue-overview").status_code == 200
    assert client.get("/api/admin/fae/issue-inbox").status_code == 200
    assert client.get("/api/admin/fae/issues").status_code == 200
    response = client.get(
        "/api/admin/fae/turn-summaries",
        params=[("turn_key", "fae:turn-1"), ("turn_key", "admin:turn-1")],
    )
    assert response.status_code == 200
    assert response.json() == [{"turn_key": "fae:turn-1"}]
    assert review.calls == [
        ("inbox", "ai-fae-agent", 100, 0),
        ("issues", "ai-fae-agent", 100, 0),
        ("turn_summaries", ["fae:turn-1"]),
    ]

    review.issue_agent_id = "ai-admin-agent"
    assert client.get(f"/api/admin/fae/issues/{ISSUE_ID}").status_code == 404


def test_fae_create_and_link_reject_browser_agent_scope() -> None:
    context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    app, _observability = _direct_app(context)
    client = TestClient(app)
    review = app.state.fae_workbench_service._review

    create = client.post(
        "/api/admin/fae/issues",
        json={"agent_id": "ai-admin-agent", "title": "issue"},
        headers={"X-Review-Actor": "codex"},
    )
    link = client.post(
        f"/api/admin/fae/issues/{ISSUE_ID}/links",
        json={
            "agent_id": "ai-admin-agent",
            "source_turn_key": "fae:turn-1",
            "source_feedback_keys": [],
        },
        headers={"X-Review-Actor": "codex"},
    )

    assert create.status_code == 422
    assert link.status_code == 422
    assert all(call[0] not in {"create_issue", "link_turn"} for call in review.calls)


def test_fae_create_uses_authenticated_actor_and_fixed_agent_scope() -> None:
    context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    app, _observability = _direct_app(context)
    client = TestClient(app)
    review = app.state.fae_workbench_service._review

    response = client.post(
        "/api/admin/fae/issues",
        json={"title": "issue", "priority": "P1"},
        headers={"X-Review-Actor": "codex"},
    )

    assert response.status_code == 201
    name, payload, kwargs = review.calls[-1]
    assert name == "create_issue"
    assert payload.agent_id == "ai-fae-agent"
    assert kwargs["actor"] == f"corp:{context.internal_user_id}"


def test_fae_link_accepts_real_turn_without_feedback() -> None:
    context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    app, _observability = _direct_app(context)
    client = TestClient(app)
    review = app.state.fae_workbench_service._review

    response = client.post(
        f"/api/admin/fae/issues/{ISSUE_ID}/links",
        json={
            "source_turn_key": "fae:turn-ordinary",
            "source_feedback_keys": [],
            "link_role": "primary",
            "reason": "create from inspected answer",
        },
        headers={"X-Review-Actor": "codex"},
    )

    assert response.status_code == 201
    name, issue_id, payload, kwargs = review.calls[-1]
    assert (name, issue_id, payload.agent_id) == (
        "link_turn",
        ISSUE_ID,
        "ai-fae-agent",
    )
    assert payload.source_feedback_keys == []
    assert kwargs["actor"] == f"corp:{context.internal_user_id}"


def test_fae_link_rejects_unknown_turn_before_review_write() -> None:
    context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    app, _observability = _direct_app(context)
    app.state.fae_workbench_service._repository.missing_turns.add("fae:missing")
    client = TestClient(app)
    review = app.state.fae_workbench_service._review

    response = client.post(
        f"/api/admin/fae/issues/{ISSUE_ID}/links",
        json={
            "source_turn_key": "fae:missing",
            "source_feedback_keys": [],
            "link_role": "primary",
            "reason": "inspect",
        },
    )

    assert response.status_code == 404
    assert all(call[0] != "link_turn" for call in review.calls)


def test_fae_semantic_review_uses_authenticated_actor_not_header() -> None:
    context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    app, _observability = _direct_app(context)
    client = TestClient(app)
    review = app.state.fae_workbench_service._review
    actor = f"corp:{context.internal_user_id}"

    response = client.post(
        f"/api/admin/fae/replays/{REPLAY_ID}/semantic-review",
        json={
            "verdict": "passed",
            "method": "human_fae",
            "reviewer": actor,
            "reason": "independent review",
        },
        headers={"X-Review-Actor": "codex"},
    )

    assert response.status_code == 200
    name, replay_id, payload, kwargs = review.calls[-1]
    assert (name, replay_id, payload.reviewer) == (
        "semantic_review",
        REPLAY_ID,
        actor,
    )
    assert kwargs["actor"] == actor


@pytest.mark.parametrize(
    ("entity", "path", "payload", "write_name"),
    [
        (
            "evidence",
            f"/api/admin/fae/evidence/{EVIDENCE_ID}/verify",
            {"reason": "verify"},
            "verify_evidence",
        ),
        (
            "replay",
            f"/api/admin/fae/replays/{REPLAY_ID}/semantic-review",
            {
                "verdict": "passed",
                "method": "human_fae",
                "reviewer": "fae:spoofed",
                "reason": "review",
            },
            "semantic_review",
        ),
    ],
)
def test_cross_agent_evidence_and_replay_return_404_without_review_write(
    entity, path, payload, write_name
) -> None:
    context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    app, _observability = _direct_app(context)
    review = app.state.fae_workbench_service._review
    review.issue_agent_id = "ai-admin-agent"
    client = TestClient(app)

    response = client.post(path, json=payload)

    assert response.status_code == 404, entity
    assert response.json() == {"detail": "fae resource not found"}
    assert all(call[0] != write_name for call in review.calls)


@pytest.mark.parametrize(
    ("owner_attribute", "path", "payload", "write_name"),
    [
        (
            "evidence_owner",
            f"/api/admin/fae/evidence/{EVIDENCE_ID}/verify",
            {"reason": "verify"},
            "verify_evidence",
        ),
        (
            "replay_owner",
            f"/api/admin/fae/replays/{REPLAY_ID}/semantic-review",
            {
                "verdict": "passed",
                "method": "human_fae",
                "reviewer": "fae:spoofed",
                "reason": "review",
            },
            "semantic_review",
        ),
    ],
)
def test_unknown_evidence_and_replay_return_404_without_review_write(
    owner_attribute, path, payload, write_name
) -> None:
    context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    app, _observability = _direct_app(context)
    review = app.state.fae_workbench_service._review
    setattr(review, owner_attribute, None)
    client = TestClient(app)

    response = client.post(path, json=payload)

    assert response.status_code == 404
    assert response.json() == {"detail": "fae resource not found"}
    assert all(call[0] != write_name for call in review.calls)


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
