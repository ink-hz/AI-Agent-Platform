from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.agent_brain.conversation_repository import message_subject
from app.agent_brain.loop_models import (
    AuthorizationSnapshot,
    BrainLoopRecord,
    BrainLoopStatus,
    BrainStepRecord,
    BrainStepStatus,
    NormalizedTaskResult,
)
from app.execution_relay.models import RequesterSubject
from app.agent_brain.tool_protocol import (
    BrainToolBatch,
    DelegateTaskCall,
    ParsedToolCall,
    RequestUserInputCall,
    SubmitAnswerCall,
    stable_runtime_id,
)
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)


_WORKER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "timed_out", "unavailable"}
)


class BrainRepositoryError(RuntimeError):
    """Stable persistence failure that contains no SQL or protected content."""

    def __init__(self, message: str = "Brain repository unavailable") -> None:
        super().__init__(message)


class BrainRepositoryConflict(BrainRepositoryError):
    def __init__(self) -> None:
        super().__init__("Brain repository conflict")


class BrainRepositoryNotFound(BrainRepositoryError):
    def __init__(self) -> None:
        super().__init__("Brain runtime record not found")


@dataclass(frozen=True, slots=True)
class TaskDispatchSpec:
    tool_index: int
    adapter_kind: str
    capability_version: int
    authorization_snapshot_id: UUID
    effective_deadline_at: datetime
    task_context: dict[str, object] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ModelStepCommit:
    provider_request_id: str
    content_blocks: tuple[dict[str, object], ...] = field(repr=False)
    usage: dict[str, int]
    cache_usage: dict[str, int]
    stop_reason: str
    batch: BrainToolBatch = field(repr=False)
    task_specs: tuple[TaskDispatchSpec, ...]
    immediate_results: tuple[ImmediateToolResult, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelStepCommitResult:
    step_id: UUID
    task_ids: tuple[UUID, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class AgentTaskEventInput:
    task_id: UUID
    seq: int
    event_type: str
    created_at: datetime
    payload: dict[str, object] = field(repr=False)
    terminal_status: Literal[
        "completed", "failed", "cancelled", "timed_out", "unavailable"
    ] | None = None
    result: NormalizedTaskResult | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ImmediateToolResult:
    tool_index: int
    value: dict[str, object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class TaskDeliveryLease:
    task_id: UUID
    loop_id: UUID
    agent_id: str
    adapter_kind: str
    context: dict[str, object] = field(repr=False)
    effective_deadline_at: datetime
    delivery_id: UUID
    attempt: int
    idempotency_key: str
    requester_subject: RequesterSubject = field(repr=False)


@dataclass(frozen=True, slots=True)
class CancellationTask:
    task_id: UUID
    loop_id: UUID
    agent_id: str
    adapter_kind: str
    context: dict[str, object] = field(repr=False)
    effective_deadline_at: datetime
    next_event_seq: int


@dataclass(frozen=True, slots=True)
class AdapterReconciliationTask:
    task_id: UUID
    loop_id: UUID
    agent_id: str
    adapter_kind: str
    context: dict[str, object] = field(repr=False)
    effective_deadline_at: datetime
    next_event_seq: int


def _model_config_subject(loop_id: UUID) -> str:
    return f"brain-loop:{loop_id}:model-config"


def _step_response_subject(step_id: UUID) -> str:
    return f"brain-step:{step_id}:provider-response"


def _tool_arguments_subject(tool_call_id: UUID) -> str:
    return f"brain-tool-call:{tool_call_id}:arguments"


def _tool_result_subject(tool_call_id: UUID) -> str:
    return f"brain-tool-call:{tool_call_id}:result"


def _task_context_subject(task_id: UUID) -> str:
    return f"brain-task:{task_id}:context"


def _task_event_subject(task_id: UUID, seq: int) -> str:
    return f"brain-task:{task_id}:event:{seq}:payload"


def _checkpoint_subject(loop_id: UUID, through_step_seq: int) -> str:
    return f"brain-loop:{loop_id}:checkpoint:{through_step_seq}"


class BrainLoopRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="brain")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        self.environment = parsed.environment
        self._control_database_url = control_database_url
        self._connect = connect
        self.content_codec = content_codec

    def __repr__(self) -> str:
        return (
            "BrainLoopRepository(control_database_url=<redacted>, "
            f"environment={self.environment!r}, content_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def heartbeat(
        self,
        worker_name: str,
        *,
        status: Literal["healthy", "degraded"],
        error_code: str | None = None,
    ) -> None:
        if (
            worker_name
            not in {"agent-brain-step", "agent-brain-adapter", "agent-brain-reaper"}
            or status not in {"healthy", "degraded"}
            or (
                error_code is not None
                and re.fullmatch(r"[a-z0-9_]{1,64}", error_code) is None
            )
        ):
            raise ValueError("Brain worker heartbeat invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.upsert_brain_worker_heartbeat_v41("
                    "%s,%s,%s,clock_timestamp()) as accepted",
                    (worker_name, status, error_code),
                ).fetchone()
            if row is None or row["accepted"] is not True:
                raise BrainRepositoryError()
        except BrainRepositoryError:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def create_loop(
        self,
        *,
        loop_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        model_config: dict[str, object],
        max_steps: int,
        max_tasks: int,
        max_duration_seconds: int,
    ) -> BrainLoopRecord:
        _require_uuid(loop_id)
        _require_uuid(conversation_id)
        _require_uuid(turn_id)
        if (
            type(model_config) is not dict
            or type(max_steps) is not int
            or not 1 <= max_steps <= 128
            or type(max_tasks) is not int
            or not 0 <= max_tasks <= 128
            or type(max_duration_seconds) is not int
            or not 1 <= max_duration_seconds <= 86400
        ):
            raise ValueError("Brain Loop configuration invalid")
        try:
            sealed = self.content_codec.seal_json(
                _model_config_subject(loop_id), model_config
            )
            with self._connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        "insert into platform_brain.brain_loops ("
                        "loop_id,conversation_id,turn_id,status,"
                        "model_config_ciphertext,model_config_key_version,"
                        "max_steps,max_tasks,max_duration_seconds,active_budget_ms"
                        ") values (%s,%s,%s,'queued',%s,%s,%s,%s,%s,%s) "
                        "returning *",
                        (
                            loop_id,
                            conversation_id,
                            turn_id,
                            sealed.ciphertext,
                            sealed.key_version,
                            max_steps,
                            max_tasks,
                            max_duration_seconds,
                            max_duration_seconds * 1000,
                        ),
                    ).fetchone()
                    connection.execute(
                        "insert into platform_brain.brain_steps "
                        "(step_id,loop_id,step_seq,status) values (%s,%s,1,'queued')",
                        (uuid4(), loop_id),
                    )
                    turn_update = connection.execute(
                        "update platform_control.conversation_turns "
                        "set status='running',updated_at=clock_timestamp() "
                        "where turn_id=%s and conversation_id=%s and status='accepted'",
                        (turn_id, conversation_id),
                    )
                    if turn_update.rowcount != 1:
                        raise BrainRepositoryConflict()
            if row is None:
                raise BrainRepositoryError()
            return _loop_from_row(row)
        except BrainRepositoryConflict:
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def create_authorization_snapshot(
        self,
        *,
        internal_user_id: UUID,
        agent_id: str,
        allowed: bool,
        grant_ids: tuple[UUID, ...],
        directory_generation_id: UUID | None,
        capability_version: int,
        effective_decision_hash: bytes,
    ) -> UUID:
        snapshot_id = uuid4()
        if (
            not isinstance(internal_user_id, UUID)
            or not isinstance(agent_id, str)
            or not agent_id
            or type(allowed) is not bool
            or type(grant_ids) is not tuple
            or any(not isinstance(item, UUID) for item in grant_ids)
            or (
                directory_generation_id is not None
                and not isinstance(directory_generation_id, UUID)
            )
            or type(capability_version) is not int
            or capability_version <= 0
            or type(effective_decision_hash) is not bytes
            or len(effective_decision_hash) != 32
        ):
            raise ValueError("authorization snapshot invalid")
        try:
            with self._connection() as connection:
                connection.execute(
                    "insert into platform_brain.authorization_snapshots ("
                    "authorization_snapshot_id,internal_user_id,agent_id,allowed,"
                    "grant_ids,directory_generation_id,capability_version,"
                    "effective_decision_hash) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        snapshot_id,
                        internal_user_id,
                        agent_id,
                        allowed,
                        list(grant_ids),
                        directory_generation_id,
                        capability_version,
                        effective_decision_hash,
                    ),
                )
            return snapshot_id
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def lease_step(
        self, worker_id: str, *, lease_seconds: int
    ) -> BrainStepRecord | None:
        _require_worker(worker_id)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Brain Step lease invalid")
        try:
            with self._connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        "select step.* from platform_brain.brain_steps step "
                        "join platform_brain.brain_loops loop on loop.loop_id=step.loop_id "
                        "where not loop.cancel_requested and loop.terminal_at is null and ("
                        "step.status='queued' or (step.status in "
                        "('leased','requesting_model') and step.lease_expires_at<clock_timestamp())"
                        ") order by step.created_at,step.step_id "
                        "for update of step skip locked limit 1"
                    ).fetchone()
                    if row is None:
                        return None
                    leased = connection.execute(
                        "update platform_brain.brain_steps set status='leased',"
                        "lease_worker_id=%s,lease_expires_at=clock_timestamp()+(%s*interval '1 second'),"
                        "attempt=attempt+1,updated_at=clock_timestamp() "
                        "where step_id=%s returning *",
                        (worker_id, lease_seconds, row["step_id"]),
                    ).fetchone()
                    connection.execute(
                        "update platform_brain.brain_loops set status='running',"
                        "active_started_at=coalesce(active_started_at,clock_timestamp()),"
                        "active_deadline_at=coalesce(active_deadline_at,"
                        "clock_timestamp()+((active_budget_ms-active_elapsed_ms)*interval '1 millisecond')) ,"
                        "updated_at=clock_timestamp(),row_version=row_version+1 "
                        "where loop_id=%s and status='queued'",
                        (row["loop_id"],),
                    )
            return _step_from_row(leased)
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def commit_model_step(
        self,
        loop_id: UUID,
        step_seq: int,
        worker_id: str,
        commit: ModelStepCommit,
    ) -> ModelStepCommitResult:
        _require_uuid(loop_id)
        _require_worker(worker_id)
        _validate_commit(step_seq, commit)
        response_value = {
            "provider_request_id": commit.provider_request_id,
            "content": list(commit.content_blocks),
        }
        try:
            with self._connection() as connection:
                with connection.transaction():
                    step = connection.execute(
                        "select * from platform_brain.brain_steps "
                        "where loop_id=%s and step_seq=%s for update",
                        (loop_id, step_seq),
                    ).fetchone()
                    if step is None:
                        raise BrainRepositoryNotFound()
                    if step["model_response_ciphertext"] is not None:
                        existing = self.content_codec.unseal_json(
                            _step_response_subject(step["step_id"]),
                            SealedContent(
                                bytes(step["model_response_ciphertext"]),
                                step["model_response_key_version"],
                            ),
                        )
                        if not _same_json(existing, response_value):
                            raise BrainRepositoryConflict()
                        task_ids = tuple(
                            row["task_id"]
                            for row in connection.execute(
                                "select task.task_id from platform_brain.agent_tasks task "
                                "join platform_brain.brain_tool_calls call "
                                "on call.brain_tool_call_id=task.brain_tool_call_id "
                                "where task.loop_id=%s and call.step_id=%s "
                                "order by call.tool_index",
                                (loop_id, step["step_id"]),
                            )
                        )
                        return ModelStepCommitResult(
                            step_id=step["step_id"],
                            task_ids=task_ids,
                            replayed=True,
                        )
                    if (
                        step["status"] not in ("leased", "requesting_model")
                        or step["lease_worker_id"] != worker_id
                        or step["lease_expires_at"] is None
                    ):
                        raise BrainRepositoryConflict()
                    loop = connection.execute(
                        "select * from platform_brain.brain_loops "
                        "where loop_id=%s for update",
                        (loop_id,),
                    ).fetchone()
                    if loop is None:
                        raise BrainRepositoryNotFound()
                    if loop["task_count"] + len(commit.task_specs) > loop["max_tasks"]:
                        raise BrainRepositoryConflict()
                    sealed_response = self.content_codec.seal_json(
                        _step_response_subject(step["step_id"]), response_value
                    )
                    task_ids = self._insert_tool_calls_locked(
                        connection, loop_id, step["step_id"], commit
                    )
                    pending = connection.execute(
                        "select count(*) from platform_brain.brain_tool_calls "
                        "where step_id=%s and result_ciphertext is null",
                        (step["step_id"],),
                    ).fetchone()["count"]
                    final_call = (
                        commit.batch.calls[0].call
                        if commit.batch.kind == "submit_answer"
                        else None
                    )
                    step_status = "completed" if pending == 0 else "waiting_tool_results"
                    connection.execute(
                        "update platform_brain.brain_steps set "
                        "status=%s,lease_worker_id=null,"
                        "lease_expires_at=null,model_request_id=%s,"
                        "model_response_ciphertext=%s,model_response_key_version=%s,"
                        "response_retention_until=clock_timestamp()+interval '7 days',"
                        "usage=%s,cache_usage=%s,stop_reason=%s,updated_at=clock_timestamp(),"
                        "terminal_at=case when %s='completed' then clock_timestamp() else null end "
                        "where step_id=%s",
                        (
                            step_status,
                            commit.provider_request_id,
                            sealed_response.ciphertext,
                            sealed_response.key_version,
                            Jsonb(commit.usage),
                            Jsonb(commit.cache_usage),
                            commit.stop_reason,
                            step_status,
                            step["step_id"],
                        ),
                    )
                    if isinstance(final_call, SubmitAnswerCall):
                        self._complete_answer_locked(
                            connection, loop, final_call
                        )
                    elif pending == 0:
                        connection.execute(
                            "insert into platform_brain.brain_steps "
                            "(step_id,loop_id,step_seq,status) values (%s,%s,%s,'queued')",
                            (uuid4(), loop_id, step_seq + 1),
                        )
                        connection.execute(
                            "update platform_brain.brain_loops set status='running',"
                            "step_count=step_count+1,task_count=task_count+%s,"
                            "updated_at=clock_timestamp(),row_version=row_version+1 "
                            "where loop_id=%s",
                            (len(task_ids), loop_id),
                        )
                    elif commit.batch.kind == "request_user_input":
                        connection.execute(
                            "update platform_control.conversation_turns set "
                            "status='waiting_user',updated_at=clock_timestamp() "
                            "where turn_id=%s",
                            (loop["turn_id"],),
                        )
                        connection.execute(
                            "update platform_brain.brain_loops set "
                            "status='waiting_user',step_count=step_count+1,"
                            "active_elapsed_ms=least(active_budget_ms,active_elapsed_ms+"
                            "greatest(0,extract(epoch from (clock_timestamp()-"
                            "active_started_at))*1000)::bigint),active_started_at=null,"
                            "active_deadline_at=null,waiting_user_expires_at="
                            "clock_timestamp()+interval '24 hours',"
                            "updated_at=clock_timestamp(),row_version=row_version+1 "
                            "where loop_id=%s",
                            (loop_id,),
                        )
                    else:
                        connection.execute(
                            "update platform_brain.brain_loops set "
                            "status='waiting_agents',step_count=step_count+1,"
                            "task_count=task_count+%s,updated_at=clock_timestamp(),"
                            "row_version=row_version+1 where loop_id=%s",
                            (len(task_ids), loop_id),
                        )
                    return ModelStepCommitResult(
                        step_id=step["step_id"],
                        task_ids=task_ids,
                        replayed=False,
                    )
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def _insert_tool_calls_locked(
        self,
        connection: Any,
        loop_id: UUID,
        step_id: UUID,
        commit: ModelStepCommit,
    ) -> tuple[UUID, ...]:
        specs = {spec.tool_index: spec for spec in commit.task_specs}
        immediate = {result.tool_index: result.value for result in commit.immediate_results}
        task_ids: list[UUID] = []
        for parsed in commit.batch.calls:
            tool_call_id = stable_runtime_id(
                loop_id, self._step_seq_locked(connection, step_id), parsed.tool_index, "tool_call"
            )
            arguments = parsed.call.model_dump(mode="json")
            sealed_arguments = self.content_codec.seal_json(
                _tool_arguments_subject(tool_call_id), arguments
            )
            result: SealedContent | None = None
            result_sha256: bytes | None = None
            status = "waiting_result"
            if parsed.tool_index in immediate:
                rejected = immediate[parsed.tool_index]
                result = self.content_codec.seal_json(
                    _tool_result_subject(tool_call_id), rejected
                )
                result_sha256 = _json_hash(rejected)
                status = "result_ready"
            elif not parsed.accepted:
                rejected = {
                    "status": parsed.result_status,
                    "limit": sum(call.accepted for call in commit.batch.calls),
                }
                result = self.content_codec.seal_json(
                    _tool_result_subject(tool_call_id), rejected
                )
                result_sha256 = _json_hash(rejected)
            connection.execute(
                "insert into platform_brain.brain_tool_calls ("
                "brain_tool_call_id,step_id,tool_index,provider_tool_call_id,"
                "tool_name,arguments_ciphertext,arguments_key_version,public_reason,"
                "status,result_ciphertext,result_key_version,result_sha256) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    tool_call_id,
                    step_id,
                    parsed.tool_index,
                    parsed.provider_tool_call_id,
                    parsed.name,
                    sealed_arguments.ciphertext,
                    sealed_arguments.key_version,
                    parsed.call.public_reason,
                    status,
                    result.ciphertext if result else None,
                    result.key_version if result else None,
                    result_sha256,
                ),
            )
            if (
                parsed.accepted
                and parsed.tool_index not in immediate
                and isinstance(parsed.call, DelegateTaskCall)
            ):
                spec = specs.get(parsed.tool_index)
                if spec is None:
                    raise BrainRepositoryConflict()
                task_id = stable_runtime_id(
                    loop_id,
                    self._step_seq_locked(connection, step_id),
                    parsed.tool_index,
                    "task",
                )
                context_value = spec.task_context or {
                    "agent_id": parsed.call.agent_id,
                    "arguments": arguments,
                }
                sealed_context = self.content_codec.seal_json(
                    _task_context_subject(task_id), context_value
                )
                connection.execute(
                    "insert into platform_brain.agent_tasks ("
                    "task_id,loop_id,brain_tool_call_id,agent_id,adapter_kind,"
                    "capability_version,authorization_snapshot_id,"
                    "task_context_ciphertext,task_context_key_version,status,"
                    "effective_deadline_at) values ("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s)",
                    (
                        task_id,
                        loop_id,
                        tool_call_id,
                        parsed.call.agent_id,
                        spec.adapter_kind,
                        spec.capability_version,
                        spec.authorization_snapshot_id,
                        sealed_context.ciphertext,
                        sealed_context.key_version,
                        spec.effective_deadline_at,
                    ),
                )
                task_ids.append(task_id)
        if set(specs) != {
            call.tool_index
            for call in commit.batch.calls
            if call.accepted
            and call.tool_index not in immediate
            and isinstance(call.call, DelegateTaskCall)
        }:
            raise BrainRepositoryConflict()
        return tuple(task_ids)

    def _complete_answer_locked(
        self, connection: Any, loop: Mapping[str, Any], call: SubmitAnswerCall
    ) -> None:
        message_id = uuid4()
        seq = connection.execute(
            "select coalesce(max(seq),0)+1 as seq from "
            "platform_control.conversation_messages where conversation_id=%s",
            (loop["conversation_id"],),
        ).fetchone()["seq"]
        sealed = self.content_codec.seal_json(
            message_subject(loop["conversation_id"], message_id),
            {"text": call.answer_markdown},
        )
        connection.execute(
            "insert into platform_control.conversation_messages ("
            "message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,delivery_status,completed_at) "
            "values (%s,%s,%s,'assistant',%s,%s,%s,'completed',clock_timestamp())",
            (message_id, loop["conversation_id"], seq, sealed.ciphertext,
             sealed.key_version, loop["turn_id"]),
        )
        connection.execute(
            "update platform_control.conversation_turns set assistant_message_id=%s,"
            "status='completed',updated_at=clock_timestamp() where turn_id=%s",
            (message_id, loop["turn_id"]),
        )
        connection.execute(
            "update platform_brain.brain_loops set status='completed',"
            "outcome=%s,step_count=step_count+1,terminal_at=clock_timestamp(),"
            "active_started_at=null,active_deadline_at=null,updated_at=clock_timestamp(),"
            "row_version=row_version+1 where loop_id=%s",
            (call.outcome, loop["loop_id"]),
        )

    def loop_for_step(self, step: BrainStepRecord) -> BrainLoopRecord:
        if not isinstance(step, BrainStepRecord):
            raise ValueError("Brain Step required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_brain.brain_loops where loop_id=%s",
                    (step.loop_id,),
                ).fetchone()
            if row is None:
                raise BrainRepositoryNotFound()
            return _loop_from_row(row)
        except BrainRepositoryNotFound:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def record_protocol_retry(self, loop_id: UUID) -> bool:
        _require_uuid(loop_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "update platform_brain.brain_loops set protocol_retry_count=1,"
                    "updated_at=clock_timestamp(),row_version=row_version+1 "
                    "where loop_id=%s and protocol_retry_count=0 and terminal_at is null "
                    "returning loop_id",
                    (loop_id,),
                ).fetchone()
            return row is not None
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def fail_with_platform_summary(self, loop_id: UUID, reason_code: str) -> None:
        _require_uuid(loop_id)
        if not isinstance(reason_code, str) or not reason_code:
            raise ValueError("Brain failure reason invalid")
        text = (
            "【平台生成的部分执行摘要】\n\n"
            "Agent 大脑本轮未能正常完成。已停止继续执行；"
            f"原因代码：`{reason_code}`。"
        )
        try:
            with self._connection() as connection, connection.transaction():
                loop = connection.execute(
                    "select * from platform_brain.brain_loops where loop_id=%s "
                    "for update",
                    (loop_id,),
                ).fetchone()
                if loop is None:
                    raise BrainRepositoryNotFound()
                if loop["terminal_at"] is not None:
                    return
                message_id = uuid4()
                seq = connection.execute(
                    "select coalesce(max(seq),0)+1 as seq from "
                    "platform_control.conversation_messages where conversation_id=%s",
                    (loop["conversation_id"],),
                ).fetchone()["seq"]
                sealed = self.content_codec.seal_json(
                    message_subject(loop["conversation_id"], message_id),
                    {"text": text},
                )
                connection.execute(
                    "insert into platform_control.conversation_messages ("
                    "message_id,conversation_id,seq,role,content_ciphertext,"
                    "encryption_key_version,turn_id,delivery_status,completed_at) "
                    "values (%s,%s,%s,'assistant',%s,%s,%s,'failed',clock_timestamp())",
                    (message_id,loop["conversation_id"],seq,sealed.ciphertext,
                     sealed.key_version,loop["turn_id"]),
                )
                connection.execute(
                    "update platform_control.conversation_turns set "
                    "assistant_message_id=%s,status='failed',updated_at=clock_timestamp() "
                    "where turn_id=%s",
                    (message_id, loop["turn_id"]),
                )
                connection.execute(
                    "update platform_brain.brain_steps set status='failed',"
                    "lease_worker_id=null,lease_expires_at=null,"
                    "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "where loop_id=%s and status in "
                    "('queued','leased','requesting_model','waiting_tool_results')",
                    (loop_id,),
                )
                connection.execute(
                    "update platform_brain.brain_tool_calls set status='failed',"
                    "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "where step_id in (select step_id from "
                    "platform_brain.brain_steps where loop_id=%s) "
                    "and status in ('accepted','waiting_result','result_ready')",
                    (loop_id,),
                )
                connection.execute(
                    "update platform_brain.brain_loops set status='failed',"
                    "reason_code=%s,fallback_used=true,"
                    "fallback_kind='platform_partial_summary',terminal_at=clock_timestamp(),"
                    "active_started_at=null,active_deadline_at=null,"
                    "waiting_user_expires_at=null,updated_at=clock_timestamp(),"
                    "row_version=row_version+1 where loop_id=%s",
                    (reason_code, loop_id),
                )
        except BrainRepositoryNotFound:
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    @staticmethod
    def _step_seq_locked(connection: Any, step_id: UUID) -> int:
        row = connection.execute(
            "select step_seq from platform_brain.brain_steps where step_id=%s",
            (step_id,),
        ).fetchone()
        if row is None:
            raise BrainRepositoryNotFound()
        return row["step_seq"]

    def append_task_event(self, event: AgentTaskEventInput) -> bool:
        _validate_task_event(event)
        payload_hash = _json_hash(event.payload)
        result_value = (
            event.result.model_dump(mode="json") if event.result is not None else None
        )
        try:
            sealed_payload = self.content_codec.seal_json(
                _task_event_subject(event.task_id, event.seq), event.payload
            )
            sealed_result = None
            result_hash = None
            if result_value is not None:
                with self._connection() as lookup_connection:
                    identity = lookup_connection.execute(
                        "select brain_tool_call_id from platform_brain.agent_tasks "
                        "where task_id=%s",
                        (event.task_id,),
                    ).fetchone()
                if identity is None:
                    raise BrainRepositoryNotFound()
                sealed_result = self.content_codec.seal_json(
                    _tool_result_subject(identity["brain_tool_call_id"]), result_value
                )
                result_hash = _json_hash(result_value)
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_brain.append_agent_task_event_v41("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) as inserted",
                    (
                        event.task_id,
                        event.seq,
                        event.event_type,
                        sealed_payload.ciphertext,
                        sealed_payload.key_version,
                        payload_hash,
                        event.created_at,
                        event.terminal_status,
                        sealed_result.ciphertext if sealed_result else None,
                        sealed_result.key_version if sealed_result else None,
                        result_hash,
                    ),
                ).fetchone()
            return bool(row["inserted"])
        except BrainRepositoryNotFound:
            raise
        except psycopg.errors.CheckViolation:
            raise BrainRepositoryConflict() from None
        except psycopg.errors.RaiseException:
            raise BrainRepositoryNotFound() from None
        except ContentCryptoError:
            raise BrainRepositoryError() from None
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def settle_batch(self, loop_id: UUID) -> bool:
        _require_uuid(loop_id)
        try:
            with self._connection() as connection:
                with connection.transaction():
                    step = connection.execute(
                        "select * from platform_brain.brain_steps "
                        "where loop_id=%s and status='waiting_tool_results' "
                        "order by step_seq desc for update limit 1",
                        (loop_id,),
                    ).fetchone()
                    if step is None:
                        return False
                    pending = connection.execute(
                        "select count(*) from platform_brain.brain_tool_calls "
                        "where step_id=%s and result_ciphertext is null",
                        (step["step_id"],),
                    ).fetchone()["count"]
                    if pending:
                        return False
                    connection.execute(
                        "update platform_brain.brain_steps set status='completed',"
                        "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                        "where step_id=%s",
                        (step["step_id"],),
                    )
                    connection.execute(
                        "insert into platform_brain.brain_steps "
                        "(step_id,loop_id,step_seq,status) values (%s,%s,%s,'queued')",
                        (uuid4(), loop_id, step["step_seq"] + 1),
                    )
                    connection.execute(
                        "update platform_brain.brain_loops set status='running',"
                        "updated_at=clock_timestamp(),row_version=row_version+1 "
                        "where loop_id=%s and status='waiting_agents'",
                        (loop_id,),
                    )
                    return True
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def settle_ready_batches(self, *, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("settled batch scan limit invalid")
        try:
            with self._connection() as connection:
                loop_ids = tuple(
                    row["loop_id"]
                    for row in connection.execute(
                        "select loop_id from platform_brain.brain_loops "
                        "where status='waiting_agents' order by updated_at,loop_id "
                        "limit %s",
                        (limit,),
                    )
                )
            return sum(self.settle_batch(loop_id) for loop_id in loop_ids)
        except BrainRepositoryError:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def reconstruct_messages(
        self, loop_id: UUID
    ) -> tuple[dict[str, object], ...]:
        _require_uuid(loop_id)
        try:
            with self._connection() as connection:
                loop = connection.execute(
                    "select conversation_id from platform_brain.brain_loops "
                    "where loop_id=%s",
                    (loop_id,),
                ).fetchone()
                if loop is None:
                    raise BrainRepositoryNotFound()
                messages: list[dict[str, object]] = []
                for row in connection.execute(
                    "select * from platform_control.conversation_messages "
                    "where conversation_id=%s and (role<>'user' or exists ("
                    "select 1 from platform_control.conversation_turns turn "
                    "where turn.user_message_id=conversation_messages.message_id)) "
                    "order by seq",
                    (loop["conversation_id"],),
                ):
                    value = self.content_codec.unseal_json(
                        message_subject(row["conversation_id"], row["message_id"]),
                        SealedContent(
                            bytes(row["content_ciphertext"]),
                            row["encryption_key_version"],
                        ),
                    )
                    if set(value) != {"text"} or not isinstance(value["text"], str):
                        raise BrainRepositoryError()
                    messages.append({"role": row["role"], "content": value["text"]})
                for step in connection.execute(
                    "select * from platform_brain.brain_steps where loop_id=%s "
                    "and model_response_ciphertext is not null order by step_seq",
                    (loop_id,),
                ):
                    response = self.content_codec.unseal_json(
                        _step_response_subject(step["step_id"]),
                        SealedContent(
                            bytes(step["model_response_ciphertext"]),
                            step["model_response_key_version"],
                        ),
                    )
                    content = response.get("content")
                    if type(content) is not list:
                        raise BrainRepositoryError()
                    messages.append({"role": "assistant", "content": content})
                    calls = tuple(
                        connection.execute(
                            "select * from platform_brain.brain_tool_calls "
                            "where step_id=%s order by tool_index",
                            (step["step_id"],),
                        )
                    )
                    if calls and all(row["result_ciphertext"] is not None for row in calls):
                        results = []
                        for call in calls:
                            result = self.content_codec.unseal_json(
                                _tool_result_subject(call["brain_tool_call_id"]),
                                SealedContent(
                                    bytes(call["result_ciphertext"]),
                                    call["result_key_version"],
                                ),
                            )
                            results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": call["provider_tool_call_id"],
                                    "content": _canonical_json(result),
                                }
                            )
                        messages.append({"role": "user", "content": results})
            return tuple(messages)
        except (BrainRepositoryNotFound, BrainRepositoryError):
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def put_checkpoint(
        self,
        loop_id: UUID,
        *,
        through_step_seq: int,
        source_hash: bytes,
        value: dict[str, object],
        expires_at: datetime,
    ) -> None:
        if (
            not isinstance(loop_id, UUID)
            or type(through_step_seq) is not int
            or through_step_seq <= 0
            or type(source_hash) is not bytes
            or len(source_hash) != 32
            or type(value) is not dict
            or not isinstance(expires_at, datetime)
        ):
            raise ValueError("Brain checkpoint invalid")
        try:
            sealed = self.content_codec.seal_json(
                _checkpoint_subject(loop_id, through_step_seq), value
            )
            with self._connection() as connection:
                connection.execute(
                    "insert into platform_brain.brain_checkpoints ("
                    "loop_id,through_step_seq,source_hash,checkpoint_ciphertext,"
                    "checkpoint_key_version,expires_at) values (%s,%s,%s,%s,%s,%s) "
                    "on conflict (loop_id,through_step_seq) do update set "
                    "source_hash=excluded.source_hash,"
                    "checkpoint_ciphertext=excluded.checkpoint_ciphertext,"
                    "checkpoint_key_version=excluded.checkpoint_key_version,"
                    "created_at=clock_timestamp(),expires_at=excluded.expires_at",
                    (
                        loop_id,
                        through_step_seq,
                        source_hash,
                        sealed.ciphertext,
                        sealed.key_version,
                        expires_at,
                    ),
                )
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def request_cancel(
        self, loop_id: UUID, *, expected_row_version: int
    ) -> BrainLoopRecord:
        if (
            not isinstance(loop_id, UUID)
            or type(expected_row_version) is not int
            or expected_row_version < 0
        ):
            raise ValueError("Brain cancellation invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "update platform_brain.brain_loops set cancel_requested=true,"
                    "row_version=row_version+1,updated_at=clock_timestamp() "
                    "where loop_id=%s and row_version=%s and terminal_at is null "
                    "returning *",
                    (loop_id, expected_row_version),
                ).fetchone()
            if row is None:
                raise BrainRepositoryConflict()
            return _loop_from_row(row)
        except BrainRepositoryConflict:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def expire_leases(self, *, limit: int) -> int:
        """Return expired, uncommitted model Steps to the durable queue."""

        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Brain lease expiry limit invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "with selected as ("
                    "select step_id from platform_brain.brain_steps "
                    "where status in ('leased','requesting_model') "
                    "and lease_expires_at<clock_timestamp() "
                    "order by lease_expires_at,step_id for update skip locked limit %s"
                    ") update platform_brain.brain_steps step set status='queued',"
                    "lease_worker_id=null,lease_expires_at=null,"
                    "updated_at=clock_timestamp() from selected "
                    "where step.step_id=selected.step_id returning step.step_id",
                    (limit,),
                ).fetchall()
            return len(rows)
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def erase_expired_model_responses(self, *, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Brain response erasure limit invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "with selected as ("
                    "select step.step_id from platform_brain.brain_steps step "
                    "join platform_brain.brain_loops loop on loop.loop_id=step.loop_id "
                    "where loop.terminal_at is not null "
                    "and step.model_response_ciphertext is not null "
                    "and step.response_retention_until<=clock_timestamp() "
                    "order by step.response_retention_until,step.step_id "
                    "for update of step skip locked limit %s"
                    ") update platform_brain.brain_steps step set "
                    "model_response_ciphertext=null,model_response_key_version=null,"
                    "response_erased_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "from selected where step.step_id=selected.step_id returning step.step_id",
                    (limit,),
                ).fetchall()
            return len(rows)
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def expire_waiting_users(self, *, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("waiting-user expiry limit invalid")
        try:
            with self._connection() as connection:
                loop_ids = tuple(
                    row["loop_id"]
                    for row in connection.execute(
                        "select loop_id from platform_brain.brain_loops "
                        "where status='waiting_user' and "
                        "waiting_user_expires_at<=clock_timestamp() "
                        "order by waiting_user_expires_at,loop_id limit %s",
                        (limit,),
                    )
                )
            for loop_id in loop_ids:
                self.fail_with_platform_summary(loop_id, "user_input_timeout")
            return len(loop_ids)
        except BrainRepositoryError:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def task_count(self, loop_id: UUID) -> int:
        _require_uuid(loop_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select count(*) from platform_brain.agent_tasks where loop_id=%s",
                    (loop_id,),
                ).fetchone()
            return int(row["count"])
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def loop_owner(self, loop_id: UUID) -> UUID:
        _require_uuid(loop_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select conversation.owner_internal_user_id from "
                    "platform_brain.brain_loops loop join "
                    "platform_control.conversations conversation on "
                    "conversation.conversation_id=loop.conversation_id "
                    "where loop.loop_id=%s",
                    (loop_id,),
                ).fetchone()
            if row is None:
                raise BrainRepositoryNotFound()
            return row["owner_internal_user_id"]
        except BrainRepositoryNotFound:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def authorization_snapshots_for_loop(
        self, loop_id: UUID
    ) -> tuple[AuthorizationSnapshot, ...]:
        _require_uuid(loop_id)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select distinct on (snapshot.agent_id) snapshot.* from "
                    "platform_brain.authorization_snapshots snapshot join "
                    "platform_brain.agent_tasks task on "
                    "task.authorization_snapshot_id=snapshot.authorization_snapshot_id "
                    "where task.loop_id=%s order by snapshot.agent_id,"
                    "snapshot.computed_at desc",
                    (loop_id,),
                ).fetchall()
            return tuple(
                AuthorizationSnapshot(
                    authorization_snapshot_id=row["authorization_snapshot_id"],
                    internal_user_id=row["internal_user_id"],
                    agent_id=row["agent_id"],
                    allowed=row["allowed"],
                    capability_version=row["capability_version"],
                    effective_decision_hash=bytes(row["effective_decision_hash"]),
                    computed_at=row["computed_at"],
                )
                for row in rows
            )
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def lease_task_delivery(
        self, worker_id: str, *, lease_seconds: int
    ) -> TaskDeliveryLease | None:
        _require_worker(worker_id)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Adapter delivery lease invalid")
        try:
            with self._connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        "select task.*,call.tool_index,step.step_seq,"
                        "snapshot.internal_user_id as requester_user_id,"
                        "requester.display_name as requester_display_name from "
                        "platform_brain.agent_tasks task join "
                        "platform_brain.brain_tool_calls call on "
                        "call.brain_tool_call_id=task.brain_tool_call_id join "
                        "platform_brain.brain_steps step on step.step_id=call.step_id "
                        "join platform_brain.authorization_snapshots snapshot on "
                        "snapshot.authorization_snapshot_id=task.authorization_snapshot_id join "
                        "platform_control.internal_users requester on "
                        "requester.internal_user_id=snapshot.internal_user_id and "
                        "requester.status='active' "
                        "where task.status='queued' and not exists (select 1 from "
                        "platform_brain.adapter_deliveries delivery where "
                        "delivery.task_id=task.task_id and delivery.status in "
                        "('queued','leased','dispatched')) order by task.created_at,task.task_id "
                        "limit 1"
                    ).fetchone()
                    if row is None:
                        return None
                    delivery_id = stable_runtime_id(
                        row["loop_id"], row["step_seq"], row["tool_index"], "delivery"
                    )
                    idempotency_key = f"brain:{row['task_id']}:delivery:1"
                    prior = connection.execute(
                        "select * from platform_brain.adapter_deliveries "
                        "where task_id=%s order by attempt desc limit 1 for update",
                        (row["task_id"],),
                    ).fetchone()
                    attempt = 1 if prior is None else prior["attempt"] + 1
                    if prior is None:
                        connection.execute(
                            "insert into platform_brain.adapter_deliveries ("
                            "delivery_id,task_id,adapter_kind,attempt,status,"
                            "lease_worker_id,lease_expires_at,idempotency_key) values "
                            "(%s,%s,%s,1,'leased',%s,clock_timestamp()+"
                            "(%s*interval '1 second'),%s)",
                            (delivery_id,row["task_id"],row["adapter_kind"],worker_id,
                             lease_seconds,idempotency_key),
                        )
                    else:
                        connection.execute(
                            "update platform_brain.adapter_deliveries set attempt=%s,"
                            "status='leased',lease_worker_id=%s,lease_expires_at="
                            "clock_timestamp()+(%s*interval '1 second'),"
                            "terminal_at=null,updated_at=clock_timestamp() "
                            "where delivery_id=%s and status='expired'",
                            (attempt,worker_id,lease_seconds,delivery_id),
                        )
                    value = self.content_codec.unseal_json(
                        _task_context_subject(row["task_id"]),
                        SealedContent(bytes(row["task_context_ciphertext"]),
                                      row["task_context_key_version"]),
                    )
                    return TaskDeliveryLease(
                        task_id=row["task_id"],loop_id=row["loop_id"],
                        agent_id=row["agent_id"],adapter_kind=row["adapter_kind"],
                        context=value,effective_deadline_at=row["effective_deadline_at"],
                        delivery_id=delivery_id,attempt=attempt,idempotency_key=idempotency_key,
                        requester_subject=RequesterSubject(
                            internal_user_id=row["requester_user_id"],
                            display_name=row["requester_display_name"],
                        ),
                    )
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def complete_delivery(
        self, lease: TaskDeliveryLease, result: NormalizedTaskResult
    ) -> None:
        inserted = self.append_task_event(
            AgentTaskEventInput(
                task_id=lease.task_id,seq=1,event_type=f"agent.{result.status}",
                created_at=datetime.now().astimezone(),payload={"status": result.status},
                terminal_status=result.status,result=result,
            )
        )
        try:
            with self._connection() as connection:
                updated = connection.execute(
                    "update platform_brain.adapter_deliveries set status='completed',"
                    "lease_worker_id=null,lease_expires_at=null,terminal_at=clock_timestamp(),"
                    "updated_at=clock_timestamp() where delivery_id=%s and status='leased'",
                    (lease.delivery_id,),
                ).rowcount
            if updated != 1 and inserted:
                raise BrainRepositoryConflict()
        except BrainRepositoryConflict:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None
        self.settle_batch(lease.loop_id)

    def mark_delivery_dispatched(self, lease: TaskDeliveryLease) -> None:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    delivery = connection.execute(
                        "update platform_brain.adapter_deliveries set "
                        "status='dispatched',lease_worker_id=null,lease_expires_at=null,"
                        "updated_at=clock_timestamp() where delivery_id=%s and "
                        "task_id=%s and status='leased' returning delivery_id",
                        (lease.delivery_id, lease.task_id),
                    ).fetchone()
                    if delivery is None:
                        raise BrainRepositoryConflict()
                    task = connection.execute(
                        "update platform_brain.agent_tasks set status='running',"
                        "started_at=coalesce(started_at,clock_timestamp()),"
                        "updated_at=clock_timestamp(),row_version=row_version+1 "
                        "where task_id=%s and status='queued' returning task_id",
                        (lease.task_id,),
                    ).fetchone()
                    if task is None:
                        raise BrainRepositoryConflict()
        except BrainRepositoryConflict:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def adapter_reconciliation_tasks(
        self, adapter_kind: str, *, limit: int = 100
    ) -> tuple[AdapterReconciliationTask, ...]:
        if (
            not isinstance(adapter_kind, str)
            or _WORKER_ID.fullmatch(adapter_kind) is None
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Adapter reconciliation scan invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select task.*,(select coalesce(max(event.seq),0)+1 from "
                    "platform_brain.agent_task_events event where event.task_id="
                    "task.task_id) as next_event_seq from platform_brain.agent_tasks task "
                    "join platform_brain.adapter_deliveries delivery on "
                    "delivery.task_id=task.task_id and delivery.status='dispatched' "
                    "where task.adapter_kind=%s and task.status='running' "
                    "order by task.updated_at,task.task_id limit %s",
                    (adapter_kind, limit),
                ).fetchall()
            return tuple(
                AdapterReconciliationTask(
                    task_id=row["task_id"],
                    loop_id=row["loop_id"],
                    agent_id=row["agent_id"],
                    adapter_kind=row["adapter_kind"],
                    context=self.content_codec.unseal_json(
                        _task_context_subject(row["task_id"]),
                        SealedContent(
                            bytes(row["task_context_ciphertext"]),
                            row["task_context_key_version"],
                        ),
                    ),
                    effective_deadline_at=row["effective_deadline_at"],
                    next_event_seq=row["next_event_seq"],
                )
                for row in rows
            )
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def complete_reconciled_delivery(self, task_id: UUID, loop_id: UUID) -> None:
        _require_uuid(task_id)
        _require_uuid(loop_id)
        try:
            with self._connection() as connection:
                updated = connection.execute(
                    "update platform_brain.adapter_deliveries set status='completed',"
                    "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "where task_id=%s and status='dispatched'",
                    (task_id,),
                ).rowcount
            if updated != 1:
                raise BrainRepositoryConflict()
        except BrainRepositoryConflict:
            raise
        except psycopg.Error:
            raise BrainRepositoryError() from None
        self.settle_batch(loop_id)

    def expire_delivery_leases(self, *, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Adapter delivery expiry limit invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "with selected as (select delivery.delivery_id,task.status as "
                    "task_status from platform_brain.adapter_deliveries delivery "
                    "join platform_brain.agent_tasks task on task.task_id=delivery.task_id "
                    "where delivery.status='leased' and delivery.lease_expires_at<"
                    "clock_timestamp() order by delivery.lease_expires_at,delivery.delivery_id "
                    "for update of delivery skip locked limit %s) update "
                    "platform_brain.adapter_deliveries delivery set status=case when "
                    "selected.task_status in ('completed','failed','cancelled','timed_out',"
                    "'unavailable') then 'completed' else 'expired' end,"
                    "lease_worker_id=null,lease_expires_at=null,terminal_at=clock_timestamp(),"
                    "updated_at=clock_timestamp() from selected where "
                    "delivery.delivery_id=selected.delivery_id returning delivery.delivery_id",
                    (limit,),
                ).fetchall()
            return len(rows)
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def cancellation_tasks(self, *, limit: int) -> tuple[CancellationTask, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("cancellation scan limit invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select task.*,(select coalesce(max(event.seq),0)+1 from "
                    "platform_brain.agent_task_events event where event.task_id="
                    "task.task_id) as next_event_seq from platform_brain.agent_tasks task join "
                    "platform_brain.brain_loops loop on loop.loop_id=task.loop_id "
                    "where loop.cancel_requested and loop.terminal_at is null and "
                    "task.status in ('queued','running') order by task.created_at limit %s",
                    (limit,),
                ).fetchall()
            return tuple(
                CancellationTask(
                    task_id=row["task_id"],loop_id=row["loop_id"],
                    agent_id=row["agent_id"],adapter_kind=row["adapter_kind"],
                    context=self.content_codec.unseal_json(
                        _task_context_subject(row["task_id"]),
                        SealedContent(bytes(row["task_context_ciphertext"]),
                                      row["task_context_key_version"]),
                    ),
                    effective_deadline_at=row["effective_deadline_at"],
                    next_event_seq=row["next_event_seq"],
                )
                for row in rows
            )
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def terminalize_requested_cancellations(self, *, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("cancellation terminal limit invalid")
        try:
            with self._connection() as connection, connection.transaction():
                loops = connection.execute(
                    "select * from platform_brain.brain_loops where "
                    "cancel_requested and terminal_at is null order by updated_at,loop_id "
                    "for update skip locked limit %s",
                    (limit,),
                ).fetchall()
                for loop in loops:
                    connection.execute(
                        "update platform_brain.brain_steps set status='failed',"
                        "lease_worker_id=null,lease_expires_at=null,"
                        "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                        "where loop_id=%s and status not in ('completed','failed')",
                        (loop["loop_id"],),
                    )
                    connection.execute(
                        "update platform_control.conversation_turns set "
                        "status='cancelled',updated_at=clock_timestamp() where turn_id=%s",
                        (loop["turn_id"],),
                    )
                    connection.execute(
                        "update platform_brain.brain_loops set status='cancelled',"
                        "reason_code='cancelled_by_user',terminal_at=clock_timestamp(),"
                        "active_started_at=null,active_deadline_at=null,"
                        "waiting_user_expires_at=null,updated_at=clock_timestamp(),"
                        "row_version=row_version+1 where loop_id=%s",
                        (loop["loop_id"],),
                    )
            return len(loops)
        except psycopg.Error:
            raise BrainRepositoryError() from None


def _loop_from_row(row: Mapping[str, Any]) -> BrainLoopRecord:
    return BrainLoopRecord(
        loop_id=row["loop_id"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        status=BrainLoopStatus(row["status"]),
        step_count=row["step_count"],
        task_count=row["task_count"],
        max_steps=row["max_steps"],
        max_tasks=row["max_tasks"],
        active_budget_ms=row["active_budget_ms"],
        active_elapsed_ms=row["active_elapsed_ms"],
        row_version=row["row_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        active_deadline_at=row["active_deadline_at"],
        waiting_user_expires_at=row["waiting_user_expires_at"],
        reason_code=row["reason_code"],
        fallback_used=row["fallback_used"],
    )


def _step_from_row(row: Mapping[str, Any]) -> BrainStepRecord:
    return BrainStepRecord(
        step_id=row["step_id"],
        loop_id=row["loop_id"],
        step_seq=row["step_seq"],
        status=BrainStepStatus(row["status"]),
        attempt=row["attempt"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        lease_worker_id=row["lease_worker_id"],
        lease_expires_at=row["lease_expires_at"],
        stop_reason=row["stop_reason"],
    )


def _validate_commit(step_seq: int, commit: ModelStepCommit) -> None:
    if (
        type(step_seq) is not int
        or step_seq <= 0
        or not isinstance(commit, ModelStepCommit)
        or not commit.provider_request_id
        or len(commit.provider_request_id.encode("utf-8")) > 160
        or not commit.content_blocks
        or type(commit.usage) is not dict
        or type(commit.cache_usage) is not dict
        or not commit.stop_reason
    ):
        raise ValueError("model Step commit invalid")
    _canonical_json({"content": list(commit.content_blocks)})
    for values in (commit.usage, commit.cache_usage):
        if any(
            type(key) is not str
            or type(value) is not int
            or value < 0
            for key, value in values.items()
        ):
            raise ValueError("model usage invalid")
    for spec in commit.task_specs:
        if (
            not isinstance(spec, TaskDispatchSpec)
            or type(spec.tool_index) is not int
            or spec.tool_index < 0
            or not isinstance(spec.adapter_kind, str)
            or not spec.adapter_kind
            or type(spec.capability_version) is not int
            or spec.capability_version <= 0
            or not isinstance(spec.authorization_snapshot_id, UUID)
            or not isinstance(spec.effective_deadline_at, datetime)
            or (spec.task_context is not None and type(spec.task_context) is not dict)
        ):
            raise ValueError("task dispatch spec invalid")


def _validate_task_event(event: AgentTaskEventInput) -> None:
    if (
        not isinstance(event, AgentTaskEventInput)
        or not isinstance(event.task_id, UUID)
        or type(event.seq) is not int
        or event.seq <= 0
        or not isinstance(event.event_type, str)
        or not event.event_type
        or not isinstance(event.created_at, datetime)
        or type(event.payload) is not dict
        or ((event.terminal_status is None) != (event.result is None))
        or (
            event.terminal_status is not None
            and event.terminal_status not in _TERMINAL_TASK_STATUSES
        )
        or (
            event.result is not None
            and event.result.status != event.terminal_status
        )
    ):
        raise ValueError("Agent task event invalid")


def _require_worker(worker_id: object) -> str:
    if type(worker_id) is not str or _WORKER_ID.fullmatch(worker_id) is None:
        raise ValueError("Brain worker ID invalid")
    return worker_id


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID required")
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, UnicodeError, ValueError):
        raise ValueError("JSON value invalid") from None


def _json_hash(value: object) -> bytes:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).digest()


def _same_json(left: object, right: object) -> bool:
    return _canonical_json(left) == _canonical_json(right)
