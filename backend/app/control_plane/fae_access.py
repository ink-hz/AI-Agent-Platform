from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID, uuid5

import psycopg
from fastapi import Depends, HTTPException, Request
from psycopg.rows import dict_row

from .audit import (
    AppliedMutation,
    AuditCommand,
    AuditUnavailableError,
    ControlCommitIndeterminateError,
    IndeterminateMutationError,
    SensitiveMutationCoordinator,
)
from .dsn import validate_control_dsn
from .models import AuthContext, Role


_FAE_UUID_NAMESPACE = UUID("5a67e9de-9f4d-4e49-a3c3-7b34c1c8f6e2")


class FaeWorkbenchAccessUnavailable(RuntimeError):
    """Stable fail-closed FAE access lookup failure."""


class FaeWorkbenchAccessRepository:
    def __init__(self, control_database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        self.environment = parsed.environment
        self._database_url = control_database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def allows(self, internal_user_id: UUID) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.has_fae_workbench_access_v63(%s) "
                    "as allowed",
                    (internal_user_id,),
                ).fetchone()
            return bool(row and row["allowed"])
        except psycopg.Error:
            raise FaeWorkbenchAccessUnavailable(
                "fae workbench access unavailable"
            ) from None

    def active_fae_workbench_member(self, display_name: str) -> dict[str, UUID]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select state.active_generation_id as generation_id, "
                    "member.member_key from platform_control.directory_state state "
                    "join platform_control.directory_generations generation "
                    "on generation.generation_id = state.active_generation_id "
                    "and generation.status = 'complete' "
                    "join platform_control.directory_members member "
                    "on member.generation_id = generation.generation_id "
                    "where state.singleton and member.subject_kind = 'employee' "
                    "and member.status = 'active' and member.display_name = %s",
                    (display_name,),
                ).fetchall()
            if len(rows := list(row)) != 1:
                raise ValueError(
                    "directory_member_not_found"
                    if not rows
                    else "directory_name_not_unique"
                )
            return {
                "generation_id": rows[0]["generation_id"],
                "member_key": rows[0]["member_key"],
            }
        except ValueError:
            raise
        except psycopg.Error:
            raise FaeWorkbenchAccessUnavailable(
                "fae workbench access unavailable"
            ) from None

    def list_fae_workbench_grants(self) -> list[dict[str, Any]]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select grant_id, internal_user_id, display_name, "
                    "user_status as status, permission, created_at, row_version "
                    "from platform_control.read_fae_workbench_grants_v63()"
                ).fetchall()
            return [dict(row) for row in rows]
        except psycopg.Error:
            raise FaeWorkbenchAccessUnavailable(
                "fae workbench access unavailable"
            ) from None

    def fae_workbench_grant_replay(
        self,
        actor: UUID,
        display_name: str,
        operation_id: UUID,
    ) -> dict[str, Any] | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.replay_fae_workbench_grant_v63("
                    "%s,%s,%s) as replay",
                    (operation_id, actor, display_name),
                ).fetchone()
            replay = None if row is None else row["replay"]
            if replay is None:
                return None
            if not isinstance(replay, dict) or not isinstance(
                replay.get("result"), dict
            ):
                raise FaeWorkbenchAccessUnavailable(
                    "fae workbench access unavailable"
                )
            return {
                "generation_id": UUID(replay["generation_id"]),
                "member_key": UUID(replay["member_key"]),
                "result": replay["result"],
            }
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as error:
            message = getattr(error.diag, "message_primary", "")
            if message == "operation identity collision":
                raise ValueError(message) from None
            raise FaeWorkbenchAccessUnavailable(
                "fae workbench access unavailable"
            ) from None
        except (KeyError, TypeError, ValueError):
            raise FaeWorkbenchAccessUnavailable(
                "fae workbench access unavailable"
            ) from None
        except psycopg.Error:
            raise FaeWorkbenchAccessUnavailable(
                "fae workbench access unavailable"
            ) from None

    def _mutate(
        self, query: str, parameters: tuple[Any, ...], operation_id: UUID
    ) -> dict[str, Any]:
        try:
            connection = self._connection()
        except psycopg.Error:
            raise FaeWorkbenchAccessUnavailable(
                "fae workbench access unavailable"
            ) from None
        try:
            try:
                row = connection.execute(query, parameters).fetchone()
            except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as error:
                connection.rollback()
                message = getattr(error.diag, "message_primary", "")
                allowed = {
                    "directory_member_not_found",
                    "directory_generation_changed",
                    "directory_name_not_unique",
                    "directory_member_inactive",
                    "verified_identity_collision",
                    "fae_workbench_already_granted",
                    "fae_workbench_not_granted",
                    "matching_audit_intent_required",
                    "operation identity collision",
                }
                raise ValueError(
                    message if message in allowed else "fae workbench mutation rejected"
                ) from None
            except psycopg.Error:
                raise ControlCommitIndeterminateError(operation_id) from None
            if row is None or not isinstance(row["result"], dict):
                connection.rollback()
                raise FaeWorkbenchAccessUnavailable(
                    "fae workbench access unavailable"
                )
            try:
                connection.commit()
            except psycopg.Error:
                raise ControlCommitIndeterminateError(operation_id) from None
            return row["result"]
        finally:
            connection.close()

    def grant_fae_workbench(
        self,
        actor: UUID,
        display_name: str,
        operation_id: UUID,
        expected_generation_id: UUID,
        expected_member_key: UUID,
        new_user_id: UUID,
        corporate_identity_id: UUID,
        union_identity_id: UUID,
        audit_event_id: UUID,
    ) -> dict[str, Any]:
        return self._mutate(
            "select platform_control.grant_fae_workbench_access_v63("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s) as result",
            (
                operation_id,
                actor,
                display_name,
                expected_generation_id,
                expected_member_key,
                new_user_id,
                corporate_identity_id,
                union_identity_id,
                audit_event_id,
            ),
            operation_id,
        )

    def revoke_fae_workbench(
        self,
        actor: UUID,
        target: UUID,
        operation_id: UUID,
        expected_row_version: int,
        audit_event_id: UUID,
    ) -> dict[str, Any]:
        return self._mutate(
            "select platform_control.revoke_fae_workbench_access_v63("
            "%s,%s,%s,%s,%s) as result",
            (operation_id, actor, target, expected_row_version, audit_event_id),
            operation_id,
        )


class FaeWorkbenchAccessService:
    def __init__(
        self,
        repository: Any,
        audit_writer: Any,
        *,
        cloud_mode: bool = False,
    ) -> None:
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
        self.cloud_mode = cloud_mode

    def allows(self, context: AuthContext) -> bool:
        if context.role is Role.PLATFORM_OWNER:
            return True
        return bool(self.repository.allows(context.internal_user_id))

    @staticmethod
    def _reason(reason: str, expected: str) -> str:
        if reason != expected:
            raise HTTPException(status_code=422, detail="reason code invalid")
        return reason

    @staticmethod
    def _stable_uuid(
        operation_id: UUID,
        display_name: str,
        generation_id: UUID,
        member_key: UUID,
        purpose: str,
    ) -> UUID:
        return uuid5(
            _FAE_UUID_NAMESPACE,
            "|".join((
                str(operation_id),
                display_name,
                str(generation_id),
                str(member_key),
                purpose,
            )),
        )

    @staticmethod
    def _command(
        event_type: str,
        context: AuthContext,
        target_type: str,
        target_id: str,
        reason: str,
        metadata: Mapping[str, Any],
        request_id: UUID,
    ) -> AuditCommand:
        return AuditCommand(
            event_type=event_type,
            actor_internal_user_id=context.internal_user_id,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
            reason=reason,
            metadata=metadata,
        )

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
        except FaeWorkbenchAccessUnavailable:
            raise HTTPException(
                status_code=503, detail="fae workbench access unavailable"
            ) from None

    def list_grants(self) -> list[dict[str, Any]]:
        try:
            return self.repository.list_fae_workbench_grants()
        except FaeWorkbenchAccessUnavailable:
            raise HTTPException(
                status_code=503, detail="fae workbench access unavailable"
            ) from None

    def grant(
        self,
        context: AuthContext,
        display_name: str,
        reason: str,
        request_id: UUID,
    ) -> dict[str, Any]:
        selected_reason = self._reason(reason, "fae_workbench_access_approved")
        try:
            replay = self.repository.fae_workbench_grant_replay(
                context.internal_user_id,
                display_name,
                request_id,
            )
            member = replay or self.repository.active_fae_workbench_member(
                display_name
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except FaeWorkbenchAccessUnavailable:
            raise HTTPException(
                status_code=503, detail="fae workbench access unavailable"
            ) from None
        generation_id = member["generation_id"]
        member_key = member["member_key"]
        requested = self._command(
            "fae_workbench_grant_requested",
            context,
            "directory_member",
            str(member_key),
            selected_reason,
            {
                "operation_id": str(request_id),
                "expected_generation_id": str(generation_id),
                "expected_member_key": str(member_key),
                "result": "requested",
            },
            request_id,
        )
        identities = (
            self._stable_uuid(request_id, display_name, generation_id, member_key, "user"),
            self._stable_uuid(request_id, display_name, generation_id, member_key, "corporate"),
            self._stable_uuid(request_id, display_name, generation_id, member_key, "union"),
        )
        return self._execute(
            requested,
            lambda event_id: (
                lambda result: AppliedMutation(result, result)
            )(
                self.repository.grant_fae_workbench(
                    context.internal_user_id,
                    display_name,
                    request_id,
                    generation_id,
                    member_key,
                    identities[0],
                    identities[1],
                    identities[2],
                    event_id,
                )
            ),
        )

    def revoke(
        self,
        context: AuthContext,
        target: UUID,
        reason: str,
        request_id: UUID,
        expected_row_version: int,
    ) -> dict[str, Any]:
        selected_reason = self._reason(reason, "fae_workbench_access_revoked")
        requested = self._command(
            "fae_workbench_revoke_requested",
            context,
            "internal_user",
            str(target),
            selected_reason,
            {
                "operation_id": str(request_id),
                "expected_row_version": expected_row_version,
                "result": "requested",
            },
            request_id,
        )
        return self._execute(
            requested,
            lambda event_id: (
                lambda result: AppliedMutation(result, result)
            )(
                self.repository.revoke_fae_workbench(
                    context.internal_user_id,
                    target,
                    request_id,
                    expected_row_version,
                    event_id,
                )
            ),
        )


def fae_access_service(request: Request) -> FaeWorkbenchAccessService:
    service = getattr(request.app.state, "fae_access", None)
    if service is None or not callable(getattr(service, "allows", None)):
        raise HTTPException(
            status_code=503, detail="fae workbench access unavailable"
        )
    return service


def fae_workbench_context(
    request: Request,
    access: Annotated[FaeWorkbenchAccessService, Depends(fae_access_service)],
) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        allowed = access.allows(context)
    except FaeWorkbenchAccessUnavailable:
        raise HTTPException(
            status_code=503, detail="fae workbench access unavailable"
        ) from None
    if not allowed:
        raise HTTPException(status_code=403, detail="fae workbench access required")
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        if context.hard_stale_read_only:
            raise HTTPException(status_code=503, detail="hard_stale_read_only")
        if bool(getattr(access, "cloud_mode", False)):
            raise HTTPException(status_code=403, detail="cloud_review_read_only")
    return context


FaeWorkbenchContext = Annotated[AuthContext, Depends(fae_workbench_context)]
