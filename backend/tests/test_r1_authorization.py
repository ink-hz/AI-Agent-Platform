from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import psycopg

from app.control_plane.authorization import (
    AuthorizationReadAuditWriter,
    AuthorizationRepository,
    AuthorizationService,
    require_exact_viewer_agent,
)
from app.control_plane.models import AuthContext, Role
from app.control_plane.middleware import (
    IdentitySecurityMiddleware,
    is_execution_worker_request,
)
from test_control_plane_migration import control_database


OWNER = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
ADMIN = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), False)
VIEWER = AuthContext(uuid4(), Role.MANAGEMENT_VIEWER, uuid4(), False)
MEMBER = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
STALE_OWNER = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), True)
STALE_ADMIN = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), True)
STALE_MEMBER = AuthContext(uuid4(), Role.MEMBER, uuid4(), True)
STALE_VIEWER = AuthContext(uuid4(), Role.MANAGEMENT_VIEWER, uuid4(), True)

FAE_ISSUE_READ_ROUTES = (
    ("GET", "/api/admin/fae/issue-overview"),
    ("GET", "/api/admin/fae/issue-inbox"),
    ("GET", "/api/admin/fae/issues"),
    ("GET", "/api/admin/fae/issues/{issue_id}"),
    ("GET", "/api/admin/fae/turn-summaries"),
)
FAE_ISSUE_MUTATION_ROUTES = (
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
)


class Grants:
    def __init__(self, allowed=("hr-bot",)):
        self.allowed = set(allowed)
        self.calls = []

    def permits(self, actor, agent_id):
        self.calls.append((actor, agent_id))
        return agent_id in self.allowed


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
@pytest.mark.parametrize(
    "route",
    [
        "/api/admin/fae/overview",
        "/api/admin/fae/sessions",
        "/api/admin/fae/sessions/{session_key}",
    ],
)
def test_exact_management_roles_can_read_fae_workbench(role, route):
    decision = AuthorizationService(Grants()).decide(
        AuthContext(uuid4(), role, uuid4(), False), "GET", route, ()
    )

    assert decision.allowed is True


@pytest.mark.parametrize(
    "route",
    [
        "/api/admin/fae/overview",
        "/api/admin/fae/sessions",
        "/api/admin/fae/sessions/{session_key}",
    ],
)
def test_fae_workbench_reads_deny_non_management_roles_and_allow_hard_stale(route):
    service = AuthorizationService(Grants())

    assert service.decide(None, "GET", route, ()).status_code == 401
    assert service.decide(MEMBER, "GET", route, ()).status_code == 403
    assert service.decide(VIEWER, "GET", route, ()).status_code == 403
    assert service.decide(STALE_OWNER, "GET", route, ()).allowed is True
    assert service.decide(STALE_ADMIN, "GET", route, ()).allowed is True


@pytest.mark.parametrize("method,route", FAE_ISSUE_READ_ROUTES)
def test_fae_issue_reads_allow_only_owner_admin_and_remain_hard_stale_available(
    method, route
):
    service = AuthorizationService(Grants(), cloud_mode=True)

    assert service.decide(None, method, route, ()).status_code == 401
    assert service.decide(MEMBER, method, route, ()).status_code == 403
    assert service.decide(VIEWER, method, route, ()).status_code == 403
    for context in (OWNER, ADMIN, STALE_OWNER, STALE_ADMIN):
        assert service.decide(context, method, route, ()).allowed is True


@pytest.mark.parametrize("method,route", FAE_ISSUE_MUTATION_ROUTES)
def test_fae_issue_mutations_allow_fresh_local_owner_admin_only(method, route):
    service = AuthorizationService(Grants())

    assert service.decide(None, method, route, ()).status_code == 401
    assert service.decide(MEMBER, method, route, ()).status_code == 403
    assert service.decide(VIEWER, method, route, ()).status_code == 403
    assert service.decide(OWNER, method, route, ()).allowed is True
    assert service.decide(ADMIN, method, route, ()).allowed is True


@pytest.mark.parametrize("method,route", FAE_ISSUE_MUTATION_ROUTES)
def test_fae_issue_mutations_deny_hard_stale_before_cloud_read_only(method, route):
    cloud = AuthorizationService(Grants(), cloud_mode=True)

    stale = cloud.decide(STALE_OWNER, method, route, ())
    fresh = cloud.decide(OWNER, method, route, ())

    assert (stale.status_code, stale.reason) == (503, "hard_stale_read_only")
    assert (fresh.status_code, fresh.reason) == (403, "cloud_review_read_only")


@pytest.mark.parametrize("method,route", FAE_ISSUE_READ_ROUTES + FAE_ISSUE_MUTATION_ROUTES)
@pytest.mark.parametrize("cloud_mode", [False, True])
def test_fae_role_denial_precedes_stale_and_cloud_details(method, route, cloud_mode):
    decision = AuthorizationService(Grants(), cloud_mode=cloud_mode).decide(
        STALE_VIEWER, method, route, ()
    )

    assert (decision.status_code, decision.reason) == (403, "management_role_required")


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/ai-notes",
        "/api/v1/ai-notes/{category_slug}/{article_slug}",
        "/ai-notes",
        "/ai-notes/{client_path:path}",
    ],
)
def test_ai_notes_reads_are_authenticated_self_service(
    role: Role, route: str
) -> None:
    context = AuthContext(uuid4(), role, uuid4(), False)
    decision = AuthorizationService(Grants()).decide(
        context, "GET", route, ()
    )

    assert decision.allowed is True
    assert decision.reason == "self_service"


@pytest.mark.parametrize(
    "route",
    [
        "/api/agents/{agent_id}/runtime",
        "/api/review/overview",
        "/api/review/inbox",
        "/api/review/issues",
        "/api/operations/events",
    ],
)
def test_viewer_requires_one_exact_granted_agent(route):
    grants = Grants()
    service = AuthorizationService(grants)

    permitted = service.decide(VIEWER, "GET", route, ("hr-bot",))
    denied = service.decide(VIEWER, "GET", route, ("HR-BOT",))

    assert permitted.allowed is True and permitted.agent_id == "hr-bot"
    assert denied.allowed is False and denied.status_code == 403


@pytest.mark.parametrize(
    "values",
    [(), ("hr-bot", "hr-bot"), ("hr-bot", "marketing-bot"), ("",)],
)
def test_viewer_agent_scope_rejects_missing_duplicate_or_conflicting(values):
    with pytest.raises(ValueError, match="exactly one Agent scope required"):
        require_exact_viewer_agent(values)


def test_member_and_unknown_route_default_deny_but_owner_reads_known_routes():
    service = AuthorizationService(Grants())

    assert service.decide(
        MEMBER, "GET", "/api/review/issues", ("hr-bot",)
    ).status_code == 403
    assert service.decide(
        VIEWER, "GET", "/api/future/unknown", ("hr-bot",)
    ).status_code == 403
    assert service.decide(
        OWNER, "GET", "/api/review/issues/{issue_id}", ()
    ).allowed is True


@pytest.mark.parametrize("context", [OWNER, ADMIN, VIEWER, MEMBER])
@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("GET", "/partner-auth/start"),
        ("GET", "/partner-auth/callback"),
        ("GET", "/partner-auth/reference"),
        ("POST", "/partner-auth/callback"),
    ],
)
def test_partner_auth_namespace_never_grants_platform_authorization(
    context: AuthContext, method: str, route: str
) -> None:
    decision = AuthorizationService(Grants()).decide(context, method, route, ())

    assert decision.allowed is False
    assert decision.status_code == 403
    assert decision.reason == "route_not_authorized"


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN, Role.MANAGEMENT_VIEWER])
@pytest.mark.parametrize("route", ["/admin", "/admin/{client_path:path}"])
def test_management_shell_is_not_member_self_service_but_remains_available_to_management_roles(
    role, route
):
    service = AuthorizationService(Grants())

    assert service.decide(AuthContext(uuid4(), role, uuid4(), False), "GET", route, ()).allowed is True
    assert service.decide(MEMBER, "GET", route, ()).status_code == 403


def test_hard_stale_owner_is_read_only_and_cloud_review_mutations_are_disabled():
    service = AuthorizationService(Grants(), cloud_mode=True)

    assert service.decide(
        STALE_OWNER, "GET", "/api/review/issues", ("hr-bot",)
    ).allowed is True
    assert service.decide(
        STALE_OWNER, "POST", "/api/review/issues", ()
    ).status_code == 503
    assert service.decide(
        OWNER, "POST", "/api/review/issues", ()
    ).status_code == 403


def test_agent_launch_is_authenticated_self_service_but_hard_stale_is_denied():
    service = AuthorizationService(Grants())
    route = "/api/v1/agents/{agent_id}/launch"

    allowed = service.decide(MEMBER, "POST", route, ("ai-fae-agent",))
    stale = service.decide(STALE_MEMBER, "POST", route, ("ai-fae-agent",))

    assert allowed.allowed is True
    assert allowed.reason == "self_service"
    assert stale.allowed is False
    assert stale.status_code == 503
    assert stale.reason == "hard_stale_read_only"


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("POST", "/api/v1/extensions/voc/drafts"),
        ("PATCH", "/api/v1/extensions/voc/drafts/{draft_id}"),
        ("POST", "/api/v1/extensions/voc/drafts/{draft_id}/cancel"),
        ("POST", "/api/v1/extensions/voc/drafts/{draft_id}/submit"),
        ("POST", "/api/v1/extensions/voc/vocs/{voc_no}/supplements"),
    ],
)
def test_voc_mutations_are_self_service_but_hard_stale_is_read_only(method, route):
    service = AuthorizationService(Grants())

    assert service.decide(MEMBER, method, route, ()).allowed is True
    assert service.decide(STALE_MEMBER, method, route, ()).status_code == 503


@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/extensions/voc/drafts/active",
        "/api/v1/extensions/voc/vocs",
        "/api/v1/extensions/voc/vocs/{voc_no}",
    ],
)
def test_voc_self_reads_remain_available_when_directory_is_hard_stale(route):
    decision = AuthorizationService(Grants()).decide(
        STALE_MEMBER, "GET", route, ()
    )

    assert decision.allowed is True


@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/extensions/voc/admin/vocs",
        "/api/v1/extensions/voc/admin/vocs/{voc_no}",
        "/api/v1/extensions/voc/admin/submitters",
    ],
)
def test_voc_management_reads_allow_exact_management_roles_only(route):
    service = AuthorizationService(Grants())

    assert service.decide(None, "GET", route, ()).status_code == 401
    assert service.decide(MEMBER, "GET", route, ()).status_code == 403
    for context in (VIEWER, ADMIN, OWNER):
        decision = service.decide(context, "GET", route, ())
        assert decision.allowed is True
        assert decision.reason in {"voc_management", context.role.value}


def test_platform_admin_uses_owner_routes_after_fail_closed_gates():
    service = AuthorizationService(Grants(), cloud_mode=True)

    assert service.decide(
        ADMIN, "GET", "/api/sessions/{session_key}", ()
    ).allowed is True
    assert service.decide(
        ADMIN, "GET", "/api/future/unknown", ()
    ).status_code == 403
    assert service.decide(
        ADMIN, "POST", "/api/review/issues", ()
    ).status_code == 403
    assert service.decide(
        STALE_ADMIN,
        "POST",
        "/api/v1/manage/viewers/{internal_user_id}",
        (),
    ).status_code == 503


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_admin_routes_are_allowlisted_but_remain_route_level_owner_only(method):
    service = AuthorizationService(Grants())
    route = "/api/v1/manage/admins/{internal_user_id}"

    assert service.decide(OWNER, method, route, ()).allowed is True
    assert service.decide(ADMIN, method, route, ()).allowed is True
    assert service.decide(MEMBER, method, route, ()).status_code == 403
    assert service.decide(VIEWER, method, route, ()).status_code == 403
    assert service.decide(STALE_OWNER, method, route, ()).status_code == 503


def test_governance_read_is_the_only_viewer_route_without_agent_scope():
    service = AuthorizationService(Grants())

    decision = service.decide(
        VIEWER, "GET", "/api/v1/manage/audit/governance", ()
    )

    assert decision.allowed is True and decision.agent_id is None


def test_middleware_denies_before_service_and_audits_exact_viewer_read():
    invoked = []
    audits = []
    grants = Grants()
    authorization = AuthorizationService(
        grants,
        read_audit=lambda actor, agent, target: audits.append(
            (actor, agent, target)
        ),
    )

    class Auth:
        route_prefix = "/"
        cookie_name = "session"
        csrf_cookie_name = "csrf"
        public_base_url = "https://agent.example.test"
        trusted_proxy_networks = ()
        rate_limiter = None

        def authenticate(self, token):
            return (VIEWER, b"csrf") if token == "valid" else None

        def verify_csrf(self, *_args):
            return True

    app = FastAPI()

    @app.get("/api/review/issues")
    def issues(agent_id: str | None = None):
        invoked.append(agent_id)
        return {"agent_id": agent_id}

    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=Auth(),
        public_assets=frozenset(),
        authorization=authorization,
        routes=tuple(app.router.routes),
    )
    client = TestClient(app)
    client.cookies.set("session", "valid")

    assert client.get("/api/review/issues").status_code == 403
    assert invoked == []
    assert client.get(
        "/api/review/issues?agent_id=hr-bot&agent_id=hr-bot"
    ).status_code == 403
    response = client.get("/api/review/issues?agent_id=hr-bot")

    assert response.status_code == 200
    assert invoked == ["hr-bot"]
    assert audits == [
        (VIEWER.internal_user_id, "hr-bot", "management_projection")
    ]


@pytest.mark.postgres
def test_database_scope_check_and_viewer_read_audit_are_exact_and_immutable(
    control_database,
):
    environment = control_database["environments"]["production"]
    owner = uuid4()
    viewer = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) values "
            "(%s,'Owner','active','platform_owner'),"
            "(%s,'Viewer','active','management_viewer')",
            (owner, viewer),
        )
        connection.execute(
            "insert into platform_control.observation_grants "
            "(observation_grant_id,agent_id,viewer_internal_user_id,created_by) "
            "values (%s,'hr-bot',%s,%s)",
            (uuid4(), viewer, owner),
        )
    repository = AuthorizationRepository(
        environment["urls"]["platform_control_app"]
    )
    audit = AuthorizationReadAuditWriter(
        environment["urls"]["platform_audit_append"]
    )

    assert repository.permits(viewer, "hr-bot") is True
    assert repository.permits(viewer, "HR-BOT") is False
    audit(viewer, "hr-bot", "management_projection")

    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select event_type,target_internal_id,reason_code,"
            "sanitized_before_after from platform_control.audit_events "
            "where actor_internal_user_id=%s",
            (viewer,),
        ).fetchone()
    assert row[:3] == (
        "scoped_management_read_completed",
        "hr-bot",
        "privileged_read",
    )
    assert row[3] == {"agent_id": "hr-bot", "scope_kind": "exact_agent"}


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/execution-worker/lease", True),
        ("POST", "/api/v1/execution-worker/heartbeat", True),
        ("POST", f"/api/v1/execution-worker/runs/{uuid4()}/events", True),
        ("GET", "/api/v1/execution-worker/lease", False),
        ("POST", "/api/v1/execution-worker/lease/", False),
        ("POST", "/api/v1/execution-worker/runs/not-a-uuid/events", False),
        ("POST", f"/prefix/api/v1/execution-worker/runs/{uuid4()}/events", False),
        ("POST", f"/api/v1/execution-worker/runs/{str(uuid4()).upper()}/events", False),
        ("POST", f"/api/v1/execution-worker/runs/{uuid4()}/future", False),
    ],
)
def test_execution_worker_public_boundary_is_exact(method, path, expected):
    assert is_execution_worker_request(method, path) is expected
