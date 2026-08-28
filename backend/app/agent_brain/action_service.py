from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.action_models import (
    ActionProjection,
    ActionProposal,
    stable_action_id,
)
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)


class ActionCommandError(RuntimeError):
    pass


class ActionCommandDenied(ActionCommandError):
    pass


class ActionCommandConflict(ActionCommandError):
    pass


@dataclass(frozen=True, slots=True)
class ActionRuntimeState:
    projection: ActionProjection
    parameters: dict[str, object]
    execution_result: dict[str, object] | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ActionExecutionLease:
    delivery_id: UUID
    action_id: UUID
    task_id: UUID
    action_kind: str
    parameters: dict[str, object]
    actor_id: UUID
    idempotency_key: str
    worker_id: str
    execution_deadline_at: datetime


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _subject(action_id: UUID, field: str) -> str:
    return f"brain-action:{action_id}:{field}"


class ActionCommandService:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        dsn_purpose: str,
        connect: Callable = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose=dsn_purpose)
        self._database_url = control_database_url
        self._codec = content_codec
        self._connect = connect
        self._dsn_purpose = dsn_purpose

    def _connection(self):
        return self._connect(self._database_url, row_factory=dict_row)

    def _projection_from_row(self, row) -> ActionProjection:
        action_id = row["action_id"]
        summary = self._codec.unseal_json(
            _subject(action_id, "summary"),
            SealedContent(
                bytes(row["summary_ciphertext"]), row["summary_key_version"]
            ),
        )["text"]
        impact = self._codec.unseal_json(
            _subject(action_id, "impact"),
            SealedContent(
                bytes(row["impact_ciphertext"]), row["impact_key_version"]
            ),
        )["text"]
        return ActionProjection(
            action_id=action_id,
            task_id=row["task_id"],
            action_seq=row["action_seq"],
            action_kind=row["action_kind"],
            summary=summary,
            impact=impact,
            action_digest=bytes(row["action_digest"]).hex(),
            status=row["status"],
            expires_at=row["expires_at"],
            execution_status=row["execution_status"],
            confirmed_by_internal_user_id=row["confirmed_by_internal_user_id"],
            confirmed_at=row["confirmed_at"],
            execution_deadline_at=row["execution_deadline_at"],
        )

    def _runtime_from_row(self, row) -> ActionRuntimeState:
        action_id = row["action_id"]
        parameters = self._codec.unseal_json(
            _subject(action_id, "parameters"),
            SealedContent(
                bytes(row["parameters_ciphertext"]),
                row["parameters_key_version"],
            ),
        )
        if not isinstance(parameters, dict):
            raise ContentCryptoError("content decrypt failed")
        result = None
        if row["execution_result_ciphertext"] is not None:
            result = self._codec.unseal_json(
                _subject(action_id, "execution-result"),
                SealedContent(
                    bytes(row["execution_result_ciphertext"]),
                    row["execution_result_key_version"],
                ),
            )
            if not isinstance(result, dict):
                raise ContentCryptoError("content decrypt failed")
        return ActionRuntimeState(
            projection=self._projection_from_row(row),
            parameters=dict(parameters),
            execution_result=None if result is None else dict(result),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _require_brain(self) -> None:
        if self._dsn_purpose != "brain":
            raise ActionCommandDenied()

    def propose(self, proposal: ActionProposal) -> ActionProjection:
        if not isinstance(proposal, ActionProposal):
            raise TypeError("action proposal invalid")
        summary = self._codec.seal_json(
            _subject(proposal.action_id, "summary"), {"text": proposal.summary}
        )
        impact = self._codec.seal_json(
            _subject(proposal.action_id, "impact"), {"text": proposal.impact}
        )
        parameters = self._codec.seal_json(
            _subject(proposal.action_id, "parameters"), proposal.parameters
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.propose_agent_task_action_v51("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        proposal.action_id,
                        proposal.platform_task_id,
                        proposal.action_seq,
                        proposal.action_kind,
                        summary.ciphertext,
                        summary.key_version,
                        hashlib.sha256(proposal.summary.encode()).digest(),
                        impact.ciphertext,
                        impact.key_version,
                        hashlib.sha256(proposal.impact.encode()).digest(),
                        parameters.ciphertext,
                        parameters.key_version,
                        hashlib.sha256(_json_bytes(proposal.parameters)).digest(),
                        bytes.fromhex(proposal.action_digest),
                        proposal.expires_at,
                        proposal.execution_timeout_seconds,
                    ),
                )
            return self.get(proposal.action_id)
        except psycopg.errors.InsufficientPrivilege:
            raise ActionCommandDenied() from None
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation):
            raise ActionCommandConflict() from None
        except (ContentCryptoError, psycopg.Error, ValueError):
            raise ActionCommandError() from None

    def confirm(
        self, owner_id: UUID, action_id: UUID, digest_hex: str
    ) -> ActionProjection:
        if (
            not isinstance(owner_id, UUID)
            or not isinstance(action_id, UUID)
            or type(digest_hex) is not str
            or len(digest_hex) != 64
        ):
            raise ValueError("action confirmation invalid")
        try:
            digest = bytes.fromhex(digest_hex)
            delivery_id = uuid5(action_id, "execution")
            idempotency_key = f"action-execution:{action_id}"
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.confirm_agent_task_action_v51("
                    "%s,%s,%s,%s,%s)",
                    (owner_id, action_id, digest, delivery_id, idempotency_key),
                )
                connection.execute(
                    "select platform_brain.resume_action_resolution_v51(%s)",
                    (action_id,),
                )
            return self.get(action_id)
        except psycopg.errors.InsufficientPrivilege:
            raise ActionCommandDenied() from None
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation):
            raise ActionCommandConflict() from None
        except (ValueError, psycopg.Error):
            raise ActionCommandError() from None

    def reject(self, owner_id: UUID, action_id: UUID) -> ActionProjection:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.reject_agent_task_action_v51(%s,%s)",
                    (owner_id, action_id),
                )
                connection.execute(
                    "select platform_brain.resume_action_resolution_v51(%s)",
                    (action_id,),
                )
            return self.get(action_id)
        except psycopg.errors.InsufficientPrivilege:
            raise ActionCommandDenied() from None
        except psycopg.errors.CheckViolation:
            raise ActionCommandConflict() from None
        except psycopg.Error:
            raise ActionCommandError() from None

    def supersede(self, action_id: UUID) -> ActionProjection:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.supersede_agent_task_action_v51(%s)",
                    (action_id,),
                )
                connection.execute(
                    "select platform_brain.resume_action_resolution_v51(%s)",
                    (action_id,),
                )
            return self.get(action_id)
        except psycopg.Error:
            raise ActionCommandError() from None

    def expire(self, *, limit: int = 100) -> int:
        self._require_brain()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Action expiry limit invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_brain.expire_agent_task_actions_v51(%s) "
                    "as changed",
                    (limit,),
                ).fetchone()
            return row["changed"]
        except psycopg.Error:
            raise ActionCommandError() from None

    def get_for_owner(
        self, owner_id: UUID, conversation_id: UUID, action_id: UUID
    ) -> ActionProjection:
        if not all(
            isinstance(value, UUID)
            for value in (owner_id, conversation_id, action_id)
        ):
            raise ValueError("owner Action lookup invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select action.* from platform_brain.agent_task_actions action "
                    "join platform_brain.agent_tasks task on task.task_id=action.task_id "
                    "join platform_brain.brain_loops loop on loop.loop_id=task.loop_id "
                    "join platform_control.conversations conversation on "
                    "conversation.conversation_id=loop.conversation_id "
                    "where action.action_id=%s and conversation.conversation_id=%s "
                    "and conversation.owner_internal_user_id=%s",
                    (action_id, conversation_id, owner_id),
                ).fetchone()
            if row is None:
                raise ActionCommandDenied()
            return self._projection_from_row(row)
        except ActionCommandDenied:
            raise
        except (ContentCryptoError, psycopg.Error, ValueError, TypeError):
            raise ActionCommandError() from None

    def list_for_owner(
        self, owner_id: UUID, conversation_id: UUID
    ) -> tuple[ActionProjection, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(conversation_id, UUID):
            raise TypeError("owner Action list invalid")
        try:
            with self._connection() as connection:
                owned = connection.execute(
                    "select 1 from platform_control.conversations where "
                    "conversation_id=%s and owner_internal_user_id=%s",
                    (conversation_id, owner_id),
                ).fetchone()
                if owned is None:
                    raise ActionCommandDenied()
                rows = connection.execute(
                    "select action.* from platform_brain.agent_task_actions action "
                    "join platform_brain.agent_tasks task on task.task_id=action.task_id "
                    "join platform_brain.brain_loops loop on loop.loop_id=task.loop_id "
                    "where loop.conversation_id=%s "
                    "order by action.created_at,action.action_id",
                    (conversation_id,),
                ).fetchall()
            return tuple(self._projection_from_row(row) for row in rows)
        except ActionCommandDenied:
            raise
        except (ContentCryptoError, psycopg.Error, ValueError, TypeError):
            raise ActionCommandError() from None

    def for_task(self, task_id: UUID) -> ActionRuntimeState | None:
        self._require_brain()
        if not isinstance(task_id, UUID):
            raise TypeError("Action task lookup invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_brain.agent_task_actions "
                    "where task_id=%s order by action_seq desc limit 1",
                    (task_id,),
                ).fetchone()
            return None if row is None else self._runtime_from_row(row)
        except ActionCommandDenied:
            raise
        except (ContentCryptoError, psycopg.Error, ValueError, TypeError):
            raise ActionCommandError() from None

    def lease_execution(
        self,
        task_id: UUID,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ActionExecutionLease | None:
        self._require_brain()
        if (
            not isinstance(task_id, UUID)
            or type(worker_id) is not str
            or not worker_id
            or len(worker_id) > 128
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 300
        ):
            raise ValueError("Action execution lease invalid")
        try:
            with self._connection() as connection, connection.transaction():
                row = connection.execute(
                    "select action.*,delivery.delivery_id,"
                    "delivery.idempotency_key,delivery.status as delivery_status,"
                    "delivery.lease_expires_at from "
                    "platform_brain.agent_task_actions action join "
                    "platform_brain.agent_action_deliveries delivery on "
                    "delivery.action_id=action.action_id where action.task_id=%s "
                    "and action.status='confirmed' and action.execution_status "
                    "in ('queued','running') order by action.action_seq desc "
                    "limit 1 for update of action,delivery skip locked",
                    (task_id,),
                ).fetchone()
                if row is None:
                    return None
                leased = connection.execute(
                    "update platform_brain.agent_action_deliveries set "
                    "status='leased',lease_worker_id=%s,lease_expires_at="
                    "clock_timestamp()+(%s*interval '1 second'),"
                    "attempt=attempt+case when status='leased' then 1 else 0 end,"
                    "updated_at=clock_timestamp() where delivery_id=%s and "
                    "(status='queued' or (status='leased' and "
                    "lease_expires_at<=clock_timestamp())) returning delivery_id",
                    (worker_id, lease_seconds, row["delivery_id"]),
                ).fetchone()
                if leased is None:
                    return None
                connection.execute(
                    "update platform_brain.agent_task_actions set "
                    "execution_status='running',updated_at=clock_timestamp() "
                    "where action_id=%s and execution_status in ('queued','running')",
                    (row["action_id"],),
                )
            runtime = self._runtime_from_row(row)
            actor_id = row["confirmed_by_internal_user_id"]
            deadline = row["execution_deadline_at"]
            if not isinstance(actor_id, UUID) or not isinstance(deadline, datetime):
                raise ActionCommandError()
            return ActionExecutionLease(
                delivery_id=row["delivery_id"],
                action_id=row["action_id"],
                task_id=row["task_id"],
                action_kind=row["action_kind"],
                parameters=runtime.parameters,
                actor_id=actor_id,
                idempotency_key=row["idempotency_key"],
                worker_id=worker_id,
                execution_deadline_at=deadline,
            )
        except ActionCommandError:
            raise
        except (ContentCryptoError, psycopg.Error, ValueError, TypeError):
            raise ActionCommandError() from None

    def finish_execution(
        self,
        lease: ActionExecutionLease,
        result: dict[str, object],
        *,
        succeeded: bool,
    ) -> ActionRuntimeState:
        self._require_brain()
        if not isinstance(lease, ActionExecutionLease) or type(result) is not dict:
            raise ValueError("Action execution result invalid")
        result_bytes = _json_bytes(result)
        sealed = self._codec.seal_json(
            _subject(lease.action_id, "execution-result"), result
        )
        terminal_status = "completed" if succeeded else "failed"
        try:
            with self._connection() as connection, connection.transaction():
                replayed = False
                delivery = connection.execute(
                    "update platform_brain.agent_action_deliveries set status=%s,"
                    "lease_worker_id=null,lease_expires_at=null,"
                    "terminal_at=coalesce(terminal_at,clock_timestamp()),"
                    "updated_at=clock_timestamp() where delivery_id=%s and "
                    "action_id=%s and status='leased' and lease_worker_id=%s "
                    "returning action_id",
                    (
                        terminal_status,
                        lease.delivery_id,
                        lease.action_id,
                        lease.worker_id,
                    ),
                ).fetchone()
                if delivery is None:
                    existing = connection.execute(
                        "select delivery.status,action.execution_status,"
                        "action.execution_result_sha256 from "
                        "platform_brain.agent_action_deliveries delivery join "
                        "platform_brain.agent_task_actions action on "
                        "action.action_id=delivery.action_id where "
                        "delivery.delivery_id=%s and delivery.action_id=%s",
                        (lease.delivery_id, lease.action_id),
                    ).fetchone()
                    if (
                        existing is None
                        or existing["status"] != terminal_status
                        or existing["execution_status"] != terminal_status
                        or bytes(existing["execution_result_sha256"])
                        != hashlib.sha256(result_bytes).digest()
                    ):
                        raise ActionCommandConflict()
                    replayed = True
                if not replayed:
                    connection.execute(
                        "update platform_brain.agent_task_actions set "
                        "execution_status=%s,execution_result_ciphertext=%s,"
                        "execution_result_key_version=%s,execution_result_sha256=%s,"
                        "updated_at=clock_timestamp() where action_id=%s and "
                        "execution_status='running'",
                        (
                            terminal_status,
                            sealed.ciphertext,
                            sealed.key_version,
                            hashlib.sha256(result_bytes).digest(),
                            lease.action_id,
                        ),
                    )
            state = self.for_task(lease.task_id)
            if state is None:
                raise ActionCommandError()
            return state
        except (ActionCommandConflict, ActionCommandError):
            raise
        except (ContentCryptoError, psycopg.Error, ValueError, TypeError):
            raise ActionCommandError() from None

    def get(self, action_id: UUID) -> ActionProjection:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_brain.agent_task_actions where action_id=%s",
                    (action_id,),
                ).fetchone()
            if row is None:
                raise ActionCommandConflict()
            return self._projection_from_row(row)
        except ActionCommandConflict:
            raise
        except (ContentCryptoError, psycopg.Error, ValueError, TypeError):
            raise ActionCommandError() from None


__all__ = [
    "ActionCommandConflict",
    "ActionCommandDenied",
    "ActionCommandError",
    "ActionCommandService",
    "ActionExecutionLease",
    "ActionRuntimeState",
    "stable_action_id",
]
