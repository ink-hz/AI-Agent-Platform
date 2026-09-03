from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .dsn import validate_control_dsn
from .models import AuthContext, Role

_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

VIEWER_R1_ROUTES = frozenset({
    ("GET", "/api/agents/{agent_id}/runtime"),
    ("GET", "/api/review/overview"),
    ("GET", "/api/review/inbox"),
    ("GET", "/api/review/issues"),
    ("GET", "/api/operations/events"),
    ("GET", "/api/v1/manage/audit/governance"),
})

_MANAGEMENT_SHELL_ROUTES = frozenset({
    ("GET", "/admin"),
    ("GET", "/admin/{client_path:path}"),
})

_AUTHENTICATED_SELF_ROUTES = frozenset({
    ("GET", "/api/v1/account"),
    ("GET", "/api/v1/internal/session/subject"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/catalog/agents"),
    ("POST", "/api/v1/agents/{agent_id}/launch"),
    ("GET", "/api/v1/brain/missions"),
    ("POST", "/api/v1/brain/missions"),
    ("GET", "/api/v1/brain/missions/{mission_id}"),
    ("GET", "/api/v1/brain/missions/{mission_id}/events"),
    ("POST", "/api/v1/brain/missions/{mission_id}/cancel"),
    ("POST", "/api/v1/agents/{agent_id}/missions"),
    ("POST", "/api/v1/conversations"),
    ("GET", "/api/v1/conversations"),
    ("GET", "/api/v1/conversations/{conversation_id}"),
    ("PATCH", "/api/v1/conversations/{conversation_id}"),
    ("GET", "/api/v1/conversations/{conversation_id}/messages"),
    ("POST", "/api/v1/conversations/{conversation_id}/read-state"),
    ("POST", "/api/v1/conversations/{conversation_id}/messages"),
    ("GET", "/api/v1/conversations/{conversation_id}/events"),
    ("GET", "/api/v1/conversations/{conversation_id}/actions"),
    ("POST", "/api/v1/conversations/{conversation_id}/actions/{action_id}/confirm"),
    ("POST", "/api/v1/conversations/{conversation_id}/actions/{action_id}/reject"),
    ("POST", "/api/v1/conversations/{conversation_id}/turns/current/cancel"),
    ("POST", "/api/v1/conversations/{conversation_id}/turns/{turn_id}/retry"),
    ("POST", "/api/v1/conversations/{conversation_id}/turns/{turn_id}/resume"),
    ("POST", "/api/v1/conversations/{conversation_id}/archive"),
    ("POST", "/api/v1/conversations/{conversation_id}/restore"),
    ("POST", "/api/v1/attachments/uploads"),
    ("PUT", "/api/v1/attachments/uploads/{upload_id}/content"),
    ("POST", "/api/v1/attachments/uploads/{upload_id}/complete"),
    ("DELETE", "/api/v1/attachments/uploads/{upload_id}"),
    ("GET", "/api/v1/attachments/{attachment_id}"),
    ("POST", "/api/v1/attachments/{attachment_id}/ticket"),
    ("GET", "/api/v1/attachments/content/{ticket}"),
    ("DELETE", "/api/v1/attachments/{attachment_id}"),
    ("GET", "/api/v1/conversations/{conversation_id}/attachments"),
    ("POST", "/api/v1/conversations/{conversation_id}/artifacts/download"),
    ("POST", "/api/v1/agents/{agent_id}/conversations"),
    ("POST", "/api/v1/messages/{message_id}/feedback"),
    ("POST", "/api/v1/extensions/voc/drafts"),
    ("GET", "/api/v1/extensions/voc/drafts/active"),
    ("PATCH", "/api/v1/extensions/voc/drafts/{draft_id}"),
    ("POST", "/api/v1/extensions/voc/drafts/{draft_id}/cancel"),
    ("POST", "/api/v1/extensions/voc/drafts/{draft_id}/submit"),
    ("GET", "/api/v1/extensions/voc/vocs"),
    ("GET", "/api/v1/extensions/voc/vocs/{voc_no}"),
    ("POST", "/api/v1/extensions/voc/vocs/{voc_no}/supplements"),
    ("GET", "/api/v1/ai-notes"),
    ("GET", "/api/v1/ai-notes/{category_slug}/{article_slug}"),
    ("GET", "/account"),
    ("GET", "/agents"),
    ("GET", "/agents/{client_path:path}"),
    ("GET", "/missions"),
    ("GET", "/missions/{client_path:path}"),
    ("GET", "/conversations"),
    ("GET", "/conversations/{client_path:path}"),
    ("GET", "/hr"),
    ("GET", "/hr/"),
    ("GET", "/hr/{client_path:path}"),
    ("GET", "/marketing"),
    ("GET", "/marketing/"),
    ("GET", "/marketing/{client_path:path}"),
    ("GET", "/ai-notes"),
    ("GET", "/ai-notes/{client_path:path}"),
    ("GET", "/sessions"),
    ("GET", "/sessions/{client_path:path}"),
    ("GET", "/review"),
    ("GET", "/activity"),
    ("GET", "/identity"),
    ("GET", "/governance"),
    ("GET", "/flywheel"),
})

_VOC_MUTATION_ROUTES = frozenset({
    ("POST", "/api/v1/extensions/voc/drafts"),
    ("PATCH", "/api/v1/extensions/voc/drafts/{draft_id}"),
    ("POST", "/api/v1/extensions/voc/drafts/{draft_id}/cancel"),
    ("POST", "/api/v1/extensions/voc/drafts/{draft_id}/submit"),
    ("POST", "/api/v1/extensions/voc/vocs/{voc_no}/supplements"),
})

_HR_POSITION_ROUTES = frozenset({
    ("GET", "/api/hr/positions"),
    ("GET", "/api/hr/positions/{position_id}"),
    ("GET", "/api/hr/position-drafts"),
    ("POST", "/api/hr/position-drafts"),
    ("POST", "/api/hr/position-drafts/{draft_id}/confirm"),
    ("POST", "/api/hr/position-drafts/{draft_id}/merge"),
    ("POST", "/api/hr/position-drafts/{draft_id}/dismiss"),
    (
        "POST",
        "/api/hr/positions/{position_id}/conversations/{conversation_id}",
    ),
    ("POST", "/api/hr/positions/{position_id}/materials/{attachment_id}"),
    ("DELETE", "/api/hr/positions/{position_id}/materials/{attachment_id}"),
})

_HR_POSITION_MUTATION_ROUTES = frozenset(
    route for route in _HR_POSITION_ROUTES if route[0] not in {"GET", "HEAD", "OPTIONS"}
)

_HARD_STALE_SELF_MUTATION_ROUTES = _VOC_MUTATION_ROUTES | frozenset({
    ("POST", "/api/v1/agents/{agent_id}/launch"),
})

_VOC_MANAGEMENT_ROUTES = frozenset({
    ("GET", "/api/v1/extensions/voc/admin/vocs"),
    ("GET", "/api/v1/extensions/voc/admin/vocs/{voc_no}"),
    ("GET", "/api/v1/extensions/voc/admin/submitters"),
})

_PARTNER_OWNER_ROUTES = frozenset({
    ("GET", "/api/v1/manage/partners/organizations"),
    ("POST", "/api/v1/manage/partners/organizations"),
    (
        "PATCH",
        "/api/v1/manage/partners/organizations/{organization_id}/status",
    ),
    ("GET", "/api/v1/manage/partners/operators"),
    ("POST", "/api/v1/manage/partners/operators"),
    ("PATCH", "/api/v1/manage/partners/operators/{operator_id}/status"),
    ("PUT", "/api/v1/manage/partners/operators/{operator_id}/fae-grant"),
    ("DELETE", "/api/v1/manage/partners/operators/{operator_id}/fae-grant"),
    ("GET", "/api/v1/manage/partners/binding-requests"),
    (
        "POST",
        "/api/v1/manage/partners/binding-requests/{request_id}/link",
    ),
    (
        "POST",
        "/api/v1/manage/partners/binding-requests/{request_id}/reject",
    ),
    ("GET", "/api/review/conversation-feedback"),
    ("PATCH", "/api/review/conversation-feedback/{feedback_id}"),
    ("GET", "/api/review/conversations/{conversation_id}/attachments"),
    ("POST", "/api/review/attachments/{attachment_id}/ticket"),
})

_CONVERSATION_REVIEW_MUTATION_ROUTES = frozenset({
    ("PATCH", "/api/review/conversation-feedback/{feedback_id}"),
    ("POST", "/api/review/attachments/{attachment_id}/ticket"),
})

def _fae_routes(prefix: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    read_routes = {
        ("GET", f"{prefix}/overview"),
        ("GET", f"{prefix}/sessions"),
        ("GET", f"{prefix}/sessions/{{session_key}}"),
        ("GET", f"{prefix}/issue-overview"),
        ("GET", f"{prefix}/issue-inbox"),
        ("GET", f"{prefix}/issues"),
        ("GET", f"{prefix}/issues/{{issue_id}}"),
        ("GET", f"{prefix}/turn-summaries"),
        ("GET", f"{prefix}/reports"),
        ("GET", f"{prefix}/reports/latest"),
        ("GET", f"{prefix}/reports/{{report_id}}"),
    }
    mutation_routes = {
        ("POST", f"{prefix}/issues"),
        ("PATCH", f"{prefix}/issues/{{issue_id}}"),
        ("POST", f"{prefix}/issues/{{issue_id}}/links"),
        ("POST", f"{prefix}/issues/{{issue_id}}/links/{{link_id}}/move"),
        ("POST", f"{prefix}/issues/{{issue_id}}/merge"),
        ("POST", f"{prefix}/issues/{{issue_id}}/fix-ready"),
        ("POST", f"{prefix}/issues/{{issue_id}}/evidence"),
        ("POST", f"{prefix}/evidence/{{evidence_id}}/verify"),
        ("POST", f"{prefix}/issues/{{issue_id}}/replays"),
        ("POST", f"{prefix}/replays/{{replay_id}}/semantic-review"),
        ("POST", f"{prefix}/issues/{{issue_id}}/disposition"),
    }
    return read_routes, mutation_routes


_CANONICAL_FAE_READ_ROUTES, _CANONICAL_FAE_MUTATION_ROUTES = _fae_routes(
    "/api/fae"
)
_COMPAT_FAE_READ_ROUTES, _COMPAT_FAE_MUTATION_ROUTES = _fae_routes(
    "/api/admin/fae"
)
_FAE_WORKBENCH_READ_ROUTES = frozenset(
    _CANONICAL_FAE_READ_ROUTES | _COMPAT_FAE_READ_ROUTES
)
_FAE_WORKBENCH_MUTATION_ROUTES = frozenset(
    _CANONICAL_FAE_MUTATION_ROUTES | _COMPAT_FAE_MUTATION_ROUTES
)

_OWNER_ROUTES = frozenset({
    *(route for route in VIEWER_R1_ROUTES),
    ("GET", "/api/deployment"),
    ("GET", "/api/health"),
    ("GET", "/api/agents/health"),
    ("GET", "/api/agents/{agent_id}/health"),
    ("GET", "/api/cluster/status"),
    ("GET", "/api/fleet/overview"),
    ("GET", "/api/registry"),
    ("GET", "/api/registry/{agent_id}"),
    ("GET", "/api/agents"),
    ("GET", "/api/agents/{agent_id}"),
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/{session_key}"),
    ("GET", "/api/turns/{turn_key}/trace"),
    ("GET", "/api/flywheel/overview"),
    ("GET", "/api/flywheel/items"),
    ("GET", "/api/sync/status"),
    ("GET", "/api/review/turn-summaries"),
    ("GET", "/api/review/conversation-feedback"),
    ("GET", "/api/review/issues/{issue_id}"),
    ("GET", "/api/operations/brief"),
    ("GET", "/api/operations/conversation-metrics"),
    ("GET", "/api/v1/manage/users"),
    ("GET", "/api/v1/manage/system-health"),
    ("GET", "/api/attachments/content/{ticket}"),
    ("POST", "/api/attachments/{attachment_id}/ticket"),
    ("POST", "/api/review/issues"),
    ("PATCH", "/api/review/issues/{issue_id}"),
    ("POST", "/api/review/issues/{issue_id}/links"),
    ("POST", "/api/review/issues/{issue_id}/links/{link_id}/move"),
    ("POST", "/api/review/issues/{issue_id}/merge"),
    ("POST", "/api/review/issues/{issue_id}/fix-ready"),
    ("POST", "/api/review/issues/{issue_id}/evidence"),
    ("POST", "/api/review/evidence/{evidence_id}/verify"),
    ("POST", "/api/review/issues/{issue_id}/replays"),
    ("POST", "/api/review/replays/{replay_id}/semantic-review"),
    ("POST", "/api/review/issues/{issue_id}/disposition"),
    ("POST", "/api/v1/manage/viewers/{internal_user_id}"),
    ("DELETE", "/api/v1/manage/viewers/{internal_user_id}"),
    ("POST", "/api/v1/manage/admins/{internal_user_id}"),
    ("DELETE", "/api/v1/manage/admins/{internal_user_id}"),
    ("GET", "/api/v1/manage/fae-workbench/grants"),
    ("POST", "/api/v1/manage/fae-workbench/grants"),
    ("DELETE", "/api/v1/manage/fae-workbench/grants/{internal_user_id}"),
    ("PUT", "/api/v1/manage/viewers/{internal_user_id}/observations/{agent_id}"),
    ("DELETE", "/api/v1/manage/viewers/{internal_user_id}/observations/{agent_id}"),
}) | (
    _MANAGEMENT_SHELL_ROUTES
    | _AUTHENTICATED_SELF_ROUTES
    | _VOC_MANAGEMENT_ROUTES
    | _PARTNER_OWNER_ROUTES
    | _FAE_WORKBENCH_READ_ROUTES
    | _FAE_WORKBENCH_MUTATION_ROUTES
)


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    status_code: Literal[200, 401, 403, 503]
    reason: str
    agent_id: str | None


def require_exact_viewer_agent(values: tuple[str, ...]) -> str:
    selected = tuple(value for value in values if value)
    if len(selected) != 1 or not _AGENT_ID.fullmatch(selected[0]):
        raise ValueError("exactly one Agent scope required")
    return selected[0]


class AuthorizationService:
    def __init__(self, grants, *, cloud_mode: bool = False, read_audit=None) -> None:
        self.grants = grants
        self.cloud_mode = cloud_mode
        self.read_audit = read_audit

    @staticmethod
    def _deny(status_code: Literal[401, 403, 503], reason: str):
        return AuthorizationDecision(False, status_code, reason, None)

    def decide(
        self,
        auth: AuthContext | None,
        method: str,
        route_template: str,
        agent_ids: tuple[str, ...],
    ) -> AuthorizationDecision:
        selected_method = method.upper()
        key = (selected_method, route_template)
        if auth is None:
            return self._deny(401, "authentication_required")
        if key in _AUTHENTICATED_SELF_ROUTES:
            if auth.hard_stale_read_only and key in _HARD_STALE_SELF_MUTATION_ROUTES:
                return self._deny(503, "hard_stale_read_only")
            return AuthorizationDecision(True, 200, "self_service", None)
        if key in _HR_POSITION_ROUTES:
            if auth.hard_stale_read_only and key in _HR_POSITION_MUTATION_ROUTES:
                return self._deny(503, "hard_stale_read_only")
            # Exact HR Agent entitlement and owner scope are resolved by the
            # router dependency after central authentication succeeds.
            return AuthorizationDecision(True, 200, "hr_position_route", None)
        if key in (_FAE_WORKBENCH_READ_ROUTES | _FAE_WORKBENCH_MUTATION_ROUTES):
            # The independent FAE grant is resolved by the router dependency.
            # Freshness/cloud mutation guards run there only after that grant is
            # proven, so an ungranted identity is never misreported as stale.
            return AuthorizationDecision(True, 200, "fae_workbench_route", None)
        if key not in _OWNER_ROUTES:
            return self._deny(403, "route_not_authorized")
        if key in _PARTNER_OWNER_ROUTES and auth.role is not Role.PLATFORM_OWNER:
            return self._deny(403, "platform_owner_required")
        if auth.role is Role.MEMBER:
            return self._deny(403, "member_management_denied")
        if auth.hard_stale_read_only and selected_method not in {
            "GET", "HEAD", "OPTIONS"
        }:
            return self._deny(503, "hard_stale_read_only")
        if (
            self.cloud_mode
            and (
                route_template.startswith("/api/review/")
                or key in _FAE_WORKBENCH_MUTATION_ROUTES
            )
            and key not in _CONVERSATION_REVIEW_MUTATION_ROUTES
            and selected_method not in {"GET", "HEAD", "OPTIONS"}
        ):
            return self._deny(403, "cloud_review_read_only")
        if auth.role in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
            return AuthorizationDecision(True, 200, auth.role.value, None)
        if key in _MANAGEMENT_SHELL_ROUTES:
            return AuthorizationDecision(True, 200, "viewer_shell", None)
        if key in _VOC_MANAGEMENT_ROUTES:
            return AuthorizationDecision(True, 200, "voc_management", None)
        if key not in VIEWER_R1_ROUTES:
            return self._deny(403, "viewer_route_denied")
        if route_template == "/api/v1/manage/audit/governance":
            return AuthorizationDecision(True, 200, "viewer_governance", None)
        try:
            agent_id = require_exact_viewer_agent(agent_ids)
        except ValueError:
            return self._deny(403, "exact_agent_scope_required")
        if not self.grants.permits(auth.internal_user_id, agent_id):
            return self._deny(403, "observation_scope_denied")
        return AuthorizationDecision(True, 200, "observation_scope", agent_id)

    def audit_permitted(
        self,
        auth: AuthContext,
        route_template: str,
        decision: AuthorizationDecision,
    ) -> None:
        if auth.role is not Role.MANAGEMENT_VIEWER:
            return
        if decision.reason not in {"observation_scope", "viewer_governance"}:
            return
        if self.read_audit is None:
            raise RuntimeError("required audit unavailable")
        self.read_audit(
            auth.internal_user_id,
            decision.agent_id,
            "governance_audit"
            if route_template == "/api/v1/manage/audit/governance"
            else "management_projection",
        )


class AuthorizationRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(database_url, purpose="app")
        self.environment = parsed.environment
        self._database_url = database_url
        self._connect = connect

    def permits(self, actor: UUID, agent_id: str) -> bool:
        if not _AGENT_ID.fullmatch(agent_id):
            return False
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select platform_control.has_observation_scope_v23(%s,%s) "
                    "as allowed",
                    (actor, agent_id),
                ).fetchone()
            return bool(row and row["allowed"])
        except psycopg.Error:
            return False


class AuthorizationReadAuditWriter:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(database_url, purpose="audit")
        self.environment = parsed.environment
        self._database_url = database_url
        self._connect = connect

    def __call__(
        self, actor: UUID, agent_id: str | None, target: str
    ) -> None:
        if target not in {"governance_audit", "management_projection"}:
            raise RuntimeError("required audit unavailable")
        try:
            event_id = uuid4()
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select platform_control.append_authorized_read_v23("
                    "%s,%s,%s,%s,%s) as event_id",
                    (event_id, actor, agent_id, target, uuid4()),
                ).fetchone()
            if row is None or row["event_id"] != event_id:
                raise RuntimeError("required audit unavailable")
        except RuntimeError:
            raise
        except psycopg.Error:
            raise RuntimeError("required audit unavailable") from None
