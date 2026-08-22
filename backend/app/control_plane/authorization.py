from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .models import AuthContext, Role
from .dsn import validate_control_dsn


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
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/catalog/agents"),
    ("GET", "/api/v1/brain/missions"),
    ("POST", "/api/v1/brain/missions"),
    ("GET", "/api/v1/brain/missions/{mission_id}"),
    ("GET", "/api/v1/brain/missions/{mission_id}/events"),
    ("POST", "/api/v1/brain/missions/{mission_id}/cancel"),
    ("POST", "/api/v1/agents/{agent_id}/missions"),
    ("GET", "/account"),
    ("GET", "/agents"),
    ("GET", "/agents/{client_path:path}"),
    ("GET", "/missions"),
    ("GET", "/missions/{client_path:path}"),
    ("GET", "/sessions"),
    ("GET", "/sessions/{client_path:path}"),
    ("GET", "/review"),
    ("GET", "/activity"),
    ("GET", "/identity"),
    ("GET", "/governance"),
    ("GET", "/flywheel"),
})

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
    ("GET", "/api/review/issues/{issue_id}"),
    ("GET", "/api/operations/brief"),
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
    ("PUT", "/api/v1/manage/viewers/{internal_user_id}/observations/{agent_id}"),
    ("DELETE", "/api/v1/manage/viewers/{internal_user_id}/observations/{agent_id}"),
}) | _MANAGEMENT_SHELL_ROUTES | _AUTHENTICATED_SELF_ROUTES


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
            return AuthorizationDecision(True, 200, "self_service", None)
        if key not in _OWNER_ROUTES:
            return self._deny(403, "route_not_authorized")
        if auth.role is Role.MEMBER:
            return self._deny(403, "member_management_denied")
        if auth.hard_stale_read_only and selected_method not in {
            "GET", "HEAD", "OPTIONS"
        }:
            return self._deny(503, "hard_stale_read_only")
        if (
            self.cloud_mode
            and route_template.startswith("/api/review/")
            and selected_method not in {"GET", "HEAD", "OPTIONS"}
        ):
            return self._deny(403, "cloud_review_read_only")
        if auth.role in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
            return AuthorizationDecision(True, 200, auth.role.value, None)
        if key in _MANAGEMENT_SHELL_ROUTES:
            return AuthorizationDecision(True, 200, "viewer_shell", None)
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
