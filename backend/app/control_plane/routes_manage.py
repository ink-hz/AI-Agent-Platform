from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import re
from typing import Annotated, Any
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .audit import (
    AuditCommand,
    AuditUnavailableError,
    IndeterminateMutationError,
    SensitiveMutationCoordinator,
    sanitize_governance_metadata,
)
from .models import AuthContext, Role


router = APIRouter(prefix="/api/v1/manage", tags=["identity-management"])
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATABASES = frozenset(
    {"agent_platform_control", "agent_platform_control_preview"}
)


class ReasonBody(BaseModel):
    reason: str
    request_id: UUID | None = None


def authenticated_context() -> AuthContext:
    """Task 12 replaces this fail-closed hook with Session authentication."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="authenticated context unavailable",
    )


def csrf_protection() -> bool:
    """Task 8 replaces this fail-closed hook with CSRF verification."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="CSRF verification unavailable",
    )


def fresh_directory() -> bool:
    """Task 6 replaces this fail-closed hook with the freshness policy."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="directory freshness unavailable",
    )


def management_service(request: Request) -> ManagementService:
    service = getattr(request.app.state, "identity_management_service", None)
    if not isinstance(service, ManagementService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="identity management unavailable",
        )
    return service


Auth = Annotated[AuthContext, Depends(authenticated_context)]
Service = Annotated["ManagementService", Depends(management_service)]


def _owner(context: AuthContext) -> None:
    if not isinstance(context, AuthContext):
        raise HTTPException(status_code=401, detail="authentication required")
    if context.role is not Role.PLATFORM_OWNER:
        raise HTTPException(status_code=403, detail="platform owner required")


def _governance_reader(context: AuthContext) -> None:
    if not isinstance(context, AuthContext):
        raise HTTPException(status_code=401, detail="authentication required")
    if context.role not in {Role.PLATFORM_OWNER, Role.MANAGEMENT_VIEWER}:
        raise HTTPException(status_code=403, detail="audit access denied")


def _mutation_guards(
    context: AuthContext,
    csrf_verified: bool,
    directory_is_fresh: bool,
) -> None:
    _owner(context)
    if not csrf_verified:
        raise HTTPException(status_code=403, detail="CSRF verification failed")
    if context.hard_stale_read_only or not directory_is_fresh:
        raise HTTPException(
            status_code=503, detail="fresh directory required"
        )


class ManagementRepository:
    def __init__(self, control_database_url: str, *, connect=psycopg.connect) -> None:
        try:
            database = conninfo_to_dict(control_database_url).get("dbname")
        except (TypeError, ValueError, psycopg.Error):
            raise ValueError("control app database DSN required") from None
        if database not in _DATABASES:
            raise ValueError("control app database DSN required")
        self._database_url = control_database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def list_users(self) -> list[dict[str, Any]]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select users.internal_user_id, users.display_name, "
                    "users.status, users.role::text as role, "
                    "coalesce(scopes.agent_ids, array[]::text[]) as scopes "
                    "from platform_control.internal_users users "
                    "left join lateral (select array_agg(grant_row.agent_id "
                    "order by grant_row.agent_id) as agent_ids from "
                    "platform_control.observation_grants grant_row where "
                    "grant_row.viewer_internal_user_id = users.internal_user_id "
                    "and grant_row.revoked_at is null) scopes on true "
                    "order by users.display_name, users.internal_user_id"
                ).fetchall()
            return [
                {
                    "internal_user_id": row["internal_user_id"],
                    "display_name": row["display_name"],
                    "status": row["status"],
                    "role": row["role"],
                    "scopes": list(row["scopes"]),
                }
                for row in rows
            ]
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def assign_viewer(
        self, actor: UUID, target: UUID, reason: str, audit_event_id: UUID
    ) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "update platform_control.internal_users set "
                    "role = 'management_viewer', "
                    "role_audit_event_id = %s, updated_at = now() "
                    "where internal_user_id = %s and status = 'active' "
                    "and role <> 'platform_owner' returning internal_user_id",
                    (audit_event_id, target),
                ).fetchone()
                if row is None:
                    raise ValueError("viewer target unavailable")
        except ValueError:
            raise
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def revoke_viewer(
        self, actor: UUID, target: UUID, reason: str, audit_event_id: UUID
    ) -> int:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "update platform_control.internal_users set role = 'member', "
                    "role_audit_event_id = %s, updated_at = now() "
                    "where internal_user_id = %s "
                    "and (role = 'management_viewer' "
                    "or (role = 'member' and role_audit_event_id = %s)) "
                    "returning internal_user_id",
                    (audit_event_id, target, audit_event_id),
                ).fetchone()
                if row is None:
                    raise ValueError("viewer target unavailable")
                connection.execute(
                    "update platform_control.observation_grants set "
                    "revoked_at = coalesce(revoked_at, now()), revoked_by = %s, "
                    "revoked_audit_event_id = coalesce(revoked_audit_event_id, %s) "
                    "where viewer_internal_user_id = %s and revoked_at is null",
                    (actor, audit_event_id, target),
                )
                sessions = connection.execute(
                    "update platform_control.web_sessions set revoked_at = now(), "
                    "revoked_reason = %s where internal_user_id = %s "
                    "and revoked_at is null",
                    (reason, target),
                ).rowcount
                return sessions
        except ValueError:
            raise
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def grant_observation(
        self,
        actor: UUID,
        target: UUID,
        agent_id: str,
        reason: str,
        audit_event_id: UUID,
    ) -> None:
        try:
            with self._connection() as connection:
                viewer = connection.execute(
                    "select 1 from platform_control.internal_users where "
                    "internal_user_id = %s and status = 'active' "
                    "and role = 'management_viewer' for update",
                    (target,),
                ).fetchone()
                if viewer is None:
                    raise ValueError("viewer target unavailable")
                existing = connection.execute(
                    "select observation_grant_id, created_audit_event_id from "
                    "platform_control.observation_grants where agent_id = %s "
                    "and viewer_internal_user_id = %s and revoked_at is null "
                    "for update",
                    (agent_id, target),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "insert into platform_control.observation_grants "
                        "(observation_grant_id, agent_id, "
                        "viewer_internal_user_id, created_by, "
                        "created_audit_event_id) values (%s, %s, %s, %s, %s)",
                        (uuid4(), agent_id, target, actor, audit_event_id),
                    )
                elif existing["created_audit_event_id"] != audit_event_id:
                    connection.execute(
                        "update platform_control.observation_grants set "
                        "created_audit_event_id = %s where observation_grant_id = %s",
                        (audit_event_id, existing["observation_grant_id"]),
                    )
        except ValueError:
            raise
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def revoke_observation(
        self,
        actor: UUID,
        target: UUID,
        agent_id: str,
        reason: str,
        audit_event_id: UUID,
    ) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "update platform_control.observation_grants set "
                    "revoked_at = coalesce(revoked_at, now()), revoked_by = %s, "
                    "revoked_audit_event_id = %s where agent_id = %s "
                    "and viewer_internal_user_id = %s "
                    "and (revoked_at is null or revoked_audit_event_id = %s) "
                    "returning observation_grant_id",
                    (actor, audit_event_id, agent_id, target, audit_event_id),
                ).fetchone()
                if row is None:
                    raise ValueError("observation scope unavailable")
        except ValueError:
            raise
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def governance_audit(self) -> list[dict[str, Any]]:
        governance_patterns = (
            "owner_%",
            "viewer_role_%",
            "observation_scope_%",
            "directory_%",
            "governance_audit_read_%",
        )
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select audit_event_id, actor_internal_user_id, event_type, "
                    "target_type, target_internal_id, request_id, result, "
                    "reason_code, sanitized_before_after, occurred_at "
                    "from platform_control.audit_events where "
                    "event_type like any(%s) order by occurred_at desc, "
                    "audit_event_id desc limit 500",
                    (list(governance_patterns),),
                ).fetchall()
            return [
                {
                    "audit_event_id": row["audit_event_id"],
                    "actor_internal_user_id": row["actor_internal_user_id"],
                    "event_type": row["event_type"],
                    "target_type": row["target_type"],
                    "target_internal_id": row["target_internal_id"],
                    "request_id": row["request_id"],
                    "result": row["result"],
                    "reason_code": row["reason_code"],
                    "sanitized_before_after": sanitize_governance_metadata(
                        row["sanitized_before_after"]
                    ),
                    "occurred_at": row["occurred_at"],
                }
                for row in rows
            ]
        except psycopg.Error:
            raise RuntimeError("governance audit unavailable") from None


class ManagementService:
    def __init__(self, repository: Any, audit_writer: Any) -> None:
        self.repository = repository
        self.audit_writer = audit_writer

    @staticmethod
    def _reason(reason: str) -> str:
        if not isinstance(reason, str) or not reason.strip():
            raise HTTPException(status_code=422, detail="reason required")
        return reason.strip()

    def _execute(self, requested: AuditCommand, mutate):
        try:
            return SensitiveMutationCoordinator(self.audit_writer).execute(
                requested=requested, mutate=mutate
            )
        except IndeterminateMutationError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "management_mutation_indeterminate",
                    "request_id": str(error.request_id),
                },
            ) from None
        except AuditUnavailableError:
            raise HTTPException(
                status_code=503, detail="required audit unavailable"
            ) from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except RuntimeError:
            raise HTTPException(
                status_code=503, detail="identity management unavailable"
            ) from None

    @staticmethod
    def _command(
        event_type: str,
        context: AuthContext,
        target_type: str,
        target_id: str,
        reason: str,
        metadata: Mapping[str, str | int | bool],
        request_id: UUID | None = None,
    ) -> AuditCommand:
        return AuditCommand(
            event_type=event_type,
            actor_internal_user_id=context.internal_user_id,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id or uuid4(),
            reason=reason,
            metadata=metadata,
        )

    def list_users(self, context: AuthContext) -> list[dict[str, Any]]:
        requested = self._command(
            "management_user_list_read_requested",
            context,
            "management_user_directory",
            "all",
            "privileged management user list read",
            {"result": "requested"},
        )
        return self._execute(
            requested, lambda audit_event_id: self.repository.list_users()
        )

    def change_viewer(
        self,
        context: AuthContext,
        target: UUID,
        reason: str,
        *,
        revoke: bool,
        request_id: UUID | None = None,
    ) -> None:
        selected_reason = self._reason(reason)
        action = "revocation" if revoke else "assignment"
        requested = self._command(
            f"viewer_role_{action}_requested",
            context,
            "internal_user",
            str(target),
            selected_reason,
            {
                "new_role": "member" if revoke else "management_viewer",
                "result": "requested",
            },
            request_id,
        )
        operation = (
            self.repository.revoke_viewer if revoke else self.repository.assign_viewer
        )
        self._execute(
            requested,
            lambda event_id: operation(
                context.internal_user_id, target, selected_reason, event_id
            ),
        )

    def change_observation(
        self,
        context: AuthContext,
        target: UUID,
        agent_id: str,
        reason: str,
        *,
        revoke: bool,
        request_id: UUID | None = None,
    ) -> None:
        if _AGENT_ID.fullmatch(agent_id) is None:
            raise HTTPException(status_code=422, detail="exact agent ID required")
        selected_reason = self._reason(reason)
        action = "revocation" if revoke else "assignment"
        requested = self._command(
            f"observation_scope_{action}_requested",
            context,
            "agent_observation_scope",
            f"{target}:{agent_id}",
            selected_reason,
            {"agent_id": agent_id, "result": "requested"},
            request_id,
        )
        operation = (
            self.repository.revoke_observation
            if revoke
            else self.repository.grant_observation
        )
        self._execute(
            requested,
            lambda event_id: operation(
                context.internal_user_id,
                target,
                agent_id,
                selected_reason,
                event_id,
            ),
        )

    def governance_audit(self, context: AuthContext) -> list[dict[str, Any]]:
        requested = self._command(
            "governance_audit_read_requested",
            context,
            "governance_audit",
            "sanitized",
            "privileged governance audit read",
            {"result": "requested"},
        )
        return self._execute(
            requested, lambda event_id: self.repository.governance_audit()
        )


@router.get("/users")
def list_users(context: Auth, service: Service) -> dict[str, Any]:
    _owner(context)
    return {"users": service.list_users(context)}


@router.post("/viewers/{internal_user_id}")
def assign_viewer(
    internal_user_id: UUID,
    payload: ReasonBody,
    context: Auth,
    service: Service,
    csrf_verified: Annotated[bool, Depends(csrf_protection)],
    directory_is_fresh: Annotated[bool, Depends(fresh_directory)],
) -> dict[str, str]:
    _mutation_guards(context, csrf_verified, directory_is_fresh)
    service.change_viewer(
        context,
        internal_user_id,
        payload.reason,
        revoke=False,
        request_id=payload.request_id,
    )
    return {"status": "ok"}


@router.delete("/viewers/{internal_user_id}")
def revoke_viewer(
    internal_user_id: UUID,
    payload: Annotated[ReasonBody, Body()],
    context: Auth,
    service: Service,
    csrf_verified: Annotated[bool, Depends(csrf_protection)],
    directory_is_fresh: Annotated[bool, Depends(fresh_directory)],
) -> dict[str, str]:
    _mutation_guards(context, csrf_verified, directory_is_fresh)
    service.change_viewer(
        context,
        internal_user_id,
        payload.reason,
        revoke=True,
        request_id=payload.request_id,
    )
    return {"status": "ok"}


@router.put("/viewers/{internal_user_id}/observations/{agent_id}")
def grant_observation(
    internal_user_id: UUID,
    agent_id: str,
    payload: ReasonBody,
    context: Auth,
    service: Service,
    csrf_verified: Annotated[bool, Depends(csrf_protection)],
    directory_is_fresh: Annotated[bool, Depends(fresh_directory)],
) -> dict[str, str]:
    _mutation_guards(context, csrf_verified, directory_is_fresh)
    service.change_observation(
        context,
        internal_user_id,
        agent_id,
        payload.reason,
        revoke=False,
        request_id=payload.request_id,
    )
    return {"status": "ok"}


@router.delete("/viewers/{internal_user_id}/observations/{agent_id}")
def revoke_observation(
    internal_user_id: UUID,
    agent_id: str,
    payload: Annotated[ReasonBody, Body()],
    context: Auth,
    service: Service,
    csrf_verified: Annotated[bool, Depends(csrf_protection)],
    directory_is_fresh: Annotated[bool, Depends(fresh_directory)],
) -> dict[str, str]:
    _mutation_guards(context, csrf_verified, directory_is_fresh)
    service.change_observation(
        context,
        internal_user_id,
        agent_id,
        payload.reason,
        revoke=True,
        request_id=payload.request_id,
    )
    return {"status": "ok"}


@router.get("/audit/governance")
def governance_audit(context: Auth, service: Service) -> dict[str, Any]:
    _governance_reader(context)
    return {"events": service.governance_audit(context)}
