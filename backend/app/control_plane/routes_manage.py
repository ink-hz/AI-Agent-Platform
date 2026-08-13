from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import re
from typing import Annotated, Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .audit import (
    AppliedMutation,
    AuditCommand,
    AuditUnavailableError,
    ControlCommitIndeterminateError,
    IndeterminateMutationError,
    SensitiveMutationCoordinator,
    sanitize_governance_metadata,
)
from .models import AuthContext, Role
from .dsn import validate_control_dsn


router = APIRouter(prefix="/api/v1/manage", tags=["identity-management"])
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
        parsed = validate_control_dsn(control_database_url, purpose="app")
        self._database_url = control_database_url
        self.environment = parsed.environment
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

    def viewer_state(self, target: UUID) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select role::text as role, row_version from "
                    "platform_control.internal_users where internal_user_id = %s",
                    (target,),
                ).fetchone()
                if row is None:
                    raise ValueError("viewer target unavailable")
                scopes = connection.execute(
                    "select agent_id from platform_control.observation_grants "
                    "where viewer_internal_user_id = %s and revoked_at is null "
                    "order by agent_id",
                    (target,),
                ).fetchall()
            return {
                "role": row["role"],
                "row_version": row["row_version"],
                "scopes": [scope["agent_id"] for scope in scopes],
            }
        except ValueError:
            raise
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def mutation_precondition(
        self,
        operation_id: UUID,
        action: str,
        target: UUID,
        agent_id: str | None = None,
    ) -> dict[str, int] | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select action, target_internal_user_id, agent_id, "
                    "expected_target_row_version, expected_causal_row_version "
                    "from platform_control.management_mutations "
                    "where operation_id = %s",
                    (operation_id,),
                ).fetchone()
            if row is None:
                return None
            if (
                row["action"] != action
                or row["target_internal_user_id"] != target
                or row["agent_id"] != agent_id
            ):
                raise ValueError("operation identity collision")
            return {
                "expected_target_row_version": row[
                    "expected_target_row_version"
                ],
                "expected_causal_row_version": row[
                    "expected_causal_row_version"
                ],
            }
        except ValueError:
            raise
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def observation_state(self, target: UUID, agent_id: str) -> dict[str, Any]:
        state = self.viewer_state(target)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select coalesce(max(row_version), 0) as row_version "
                    "from platform_control.observation_grants "
                    "where viewer_internal_user_id = %s and agent_id = %s",
                    (target, agent_id),
                ).fetchone()
            return {**state, "scope_row_version": row["row_version"] if row else 0}
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None

    def _mutate(
        self, query: str, parameters: tuple[Any, ...], operation_id: UUID
    ) -> dict[str, Any]:
        try:
            connection = self._connection()
        except psycopg.Error:
            raise RuntimeError("identity management unavailable") from None
        try:
            try:
                row = connection.execute(query, parameters).fetchone()
            except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as error:
                connection.rollback()
                message = getattr(error.diag, "message_primary", "")
                allowed = {
                    "matching audit intent required",
                    "operation identity collision",
                    "viewer assignment precondition failed",
                    "viewer revocation precondition failed",
                    "scope assignment precondition failed",
                    "scope revocation precondition failed",
                }
                raise ValueError(
                    message if message in allowed else "management mutation rejected"
                ) from None
            except psycopg.Error:
                raise ControlCommitIndeterminateError(operation_id) from None
            if row is None or not isinstance(row["result"], dict):
                connection.rollback()
                raise RuntimeError("identity management unavailable")
            try:
                connection.commit()
            except psycopg.Error:
                raise ControlCommitIndeterminateError(operation_id) from None
            return row["result"]
        finally:
            connection.close()

    def assign_viewer(
        self,
        actor: UUID,
        target: UUID,
        operation_id: UUID,
        expected_row_version: int,
        audit_event_id: UUID,
    ) -> dict[str, Any]:
        return self._mutate(
            "select platform_control.assign_management_viewer("
            "%s,%s,%s,%s,%s) as result",
            (operation_id, actor, target, expected_row_version, audit_event_id),
            operation_id,
        )

    def revoke_viewer(
        self,
        actor: UUID,
        target: UUID,
        operation_id: UUID,
        expected_row_version: int,
        audit_event_id: UUID,
    ) -> dict[str, Any]:
        return self._mutate(
            "select platform_control.revoke_management_viewer("
            "%s,%s,%s,%s,%s) as result",
            (operation_id, actor, target, expected_row_version, audit_event_id),
            operation_id,
        )

    def grant_observation(
        self,
        actor: UUID,
        target: UUID,
        agent_id: str,
        operation_id: UUID,
        expected_user_version: int,
        expected_scope_version: int,
        audit_event_id: UUID,
    ) -> dict[str, Any]:
        return self._mutate(
            "select platform_control.grant_observation_scope("
            "%s,%s,%s,%s,%s,%s,%s) as result",
            (
                operation_id,
                actor,
                target,
                agent_id,
                expected_user_version,
                expected_scope_version,
                audit_event_id,
            ),
            operation_id,
        )

    def revoke_observation(
        self,
        actor: UUID,
        target: UUID,
        agent_id: str,
        operation_id: UUID,
        expected_user_version: int,
        expected_scope_version: int,
        audit_event_id: UUID,
    ) -> dict[str, Any]:
        return self._mutate(
            "select platform_control.revoke_observation_scope("
            "%s,%s,%s,%s,%s,%s,%s) as result",
            (
                operation_id,
                actor,
                target,
                agent_id,
                expected_user_version,
                expected_scope_version,
                audit_event_id,
            ),
            operation_id,
        )

    def governance_audit(self) -> list[dict[str, Any]]:
        governance_patterns = (
            "owner_%",
            "viewer_role_%",
            "observation_scope_%",
            "directory_%",
            "management_user_list_read_%",
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
            projected = []
            for row in rows:
                try:
                    metadata = sanitize_governance_metadata(
                        row["sanitized_before_after"],
                        event_type=row["event_type"],
                    )
                except ValueError:
                    continue
                projected.append({
                    "audit_event_id": row["audit_event_id"],
                    "actor_internal_user_id": row["actor_internal_user_id"],
                    "event_type": row["event_type"],
                    "target_type": row["target_type"],
                    "target_internal_id": row["target_internal_id"],
                    "request_id": row["request_id"],
                    "result": row["result"],
                    "reason_code": row["reason_code"],
                    "sanitized_before_after": metadata,
                    "occurred_at": row["occurred_at"],
                })
            return projected
        except psycopg.Error:
            raise RuntimeError("governance audit unavailable") from None


class ManagementService:
    def __init__(self, repository: Any, audit_writer: Any) -> None:
        repository_environment = getattr(repository, "environment", None)
        audit_environment = getattr(audit_writer, "environment", None)
        if (
            repository_environment is not None
            and audit_environment is not None
            and repository_environment != audit_environment
        ):
            raise ValueError("control and audit environment mismatch")
        self.repository = repository
        self.audit_writer = audit_writer

    @staticmethod
    def _reason(reason: str, expected: str) -> str:
        if reason != expected:
            raise HTTPException(status_code=422, detail="reason code invalid")
        return reason

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
        metadata: Mapping[str, Any],
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
        operation_id = uuid4()
        requested = self._command(
            "management_user_list_read_requested",
            context,
            "management_user_directory",
            "all",
            "privileged_read",
            {"operation_id": str(operation_id), "result": "requested"},
            operation_id,
        )
        return self._execute(
            requested,
            lambda audit_event_id: self._read_result(
                self.repository.list_users(), operation_id
            ),
        )

    @staticmethod
    def _read_result(value, operation_id: UUID) -> AppliedMutation:
        return AppliedMutation(
            value,
            {"operation_id": str(operation_id), "item_count": len(value)},
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
        selected_reason = self._reason(
            reason, "access_revoked" if revoke else "access_approved"
        )
        action = "revocation" if revoke else "assignment"
        operation_id = request_id or uuid4()
        expected_role = "management_viewer" if revoke else "member"
        replay = self.repository.mutation_precondition(
            operation_id,
            "revoke_viewer" if revoke else "assign_viewer",
            target,
        )
        if replay is None:
            state = self.repository.viewer_state(target)
            if state["role"] != expected_role:
                raise HTTPException(status_code=409, detail="viewer target unavailable")
            expected_version = state["row_version"]
        else:
            expected_version = replay["expected_target_row_version"]
        requested = self._command(
            f"viewer_role_{action}_requested",
            context,
            "internal_user",
            str(target),
            selected_reason,
            {
                "operation_id": str(operation_id),
                "previous_role": expected_role,
                "new_role": "member" if revoke else "management_viewer",
                "expected_row_version": expected_version,
                "result": "requested",
            },
            operation_id,
        )
        operation = (
            self.repository.revoke_viewer if revoke else self.repository.assign_viewer
        )
        self._execute(
            requested,
            lambda event_id: AppliedMutation(
                None,
                operation(
                    context.internal_user_id,
                    target,
                    operation_id,
                    expected_version,
                    event_id,
                ),
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
        selected_reason = self._reason(
            reason, "scope_revoked" if revoke else "scope_approved"
        )
        action = "revocation" if revoke else "assignment"
        operation_id = request_id or uuid4()
        replay = self.repository.mutation_precondition(
            operation_id,
            "revoke_scope" if revoke else "grant_scope",
            target,
            agent_id,
        )
        if replay is None:
            state = self.repository.observation_state(target, agent_id)
            if state["role"] != "management_viewer":
                raise HTTPException(status_code=409, detail="viewer target unavailable")
            if revoke and state["scope_row_version"] < 1:
                raise HTTPException(status_code=409, detail="observation scope unavailable")
            if not revoke and state["scope_row_version"] != 0:
                raise HTTPException(status_code=409, detail="observation scope unavailable")
            expected_user_version = state["row_version"]
            expected_scope_version = state["scope_row_version"]
        else:
            expected_user_version = replay["expected_target_row_version"]
            expected_scope_version = replay["expected_causal_row_version"]
        requested = self._command(
            f"observation_scope_{action}_requested",
            context,
            "agent_observation_scope",
            f"{target}:{agent_id}",
            selected_reason,
            {
                "operation_id": str(operation_id),
                "agent_id": agent_id,
                "expected_user_row_version": expected_user_version,
                "expected_scope_row_version": expected_scope_version,
                "result": "requested",
            },
            operation_id,
        )
        operation = (
            self.repository.revoke_observation
            if revoke
            else self.repository.grant_observation
        )
        self._execute(
            requested,
            lambda event_id: AppliedMutation(
                None,
                operation(
                    context.internal_user_id,
                    target,
                    agent_id,
                    operation_id,
                    expected_user_version,
                    expected_scope_version,
                    event_id,
                ),
            ),
        )

    def governance_audit(self, context: AuthContext) -> list[dict[str, Any]]:
        operation_id = uuid4()
        requested = self._command(
            "governance_audit_read_requested",
            context,
            "governance_audit",
            "sanitized",
            "privileged_read",
            {"operation_id": str(operation_id), "result": "requested"},
            operation_id,
        )
        return self._execute(
            requested,
            lambda event_id: self._read_result(
                self.repository.governance_audit(), operation_id
            ),
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
