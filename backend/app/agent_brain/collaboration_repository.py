from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.agent_brain.collaboration_models import (
    AgentTaskMessageInput,
    AgentTaskMessageRecord,
    AgentTaskPublicEventInput,
    AgentTaskPublicEventRecord,
    AgentTaskSessionRecord,
    BrainThinkingDelta,
    BrainThinkingSummaryRecord,
    EventWakeResult,
    TaskMessageAppendResult,
    UserInterventionRecord,
    WaitSettlementResult,
    WaitSubscriptionRecord,
    WaitSubscriptionSpec,
)
from app.agent_brain.loop_repository import (
    BrainRepositoryConflict,
    BrainRepositoryError,
    BrainRepositoryNotFound,
)
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)

_ADAPTER_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _message_subject(task_id: UUID, seq: int) -> str:
    return f"brain-task:{task_id}:message:{seq}"


def _event_subject(task_id: UUID, seq: int) -> str:
    return f"brain-task:{task_id}:event:{seq}:payload"


def _session_ref_subject(task_id: UUID) -> str:
    return f"brain-task:{task_id}:session-ref"


def _thinking_subject(step_id: UUID, block_index: int) -> str:
    return f"brain-step:{step_id}:thinking:{block_index}"


def _intervention_subject(intervention_id: UUID) -> str:
    return f"brain-intervention:{intervention_id}"


def _tool_result_subject(tool_call_id: UUID) -> str:
    return f"brain-tool-call:{tool_call_id}:result"


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


def _text_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class CollaborationRepository:
    """Durable, encrypted child-Agent collaboration state."""

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
        self._content_codec = content_codec
        self._connect = connect
        self._sleep = time.sleep
        self._random = random.SystemRandom()

    def __repr__(self) -> str:
        return (
            "CollaborationRepository(control_database_url=<redacted>, "
            f"environment={self.environment!r}, content_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def create_task_session(
        self,
        *,
        task_id: UUID,
        child_session_id: str,
        adapter_kind: str,
        adapter_session_ref: Mapping[str, object] | None,
        capability_snapshot: Mapping[str, object],
    ) -> AgentTaskSessionRecord:
        if not isinstance(task_id, UUID):
            raise ValueError("task ID invalid")
        if (
            type(child_session_id) is not str
            or not 16 <= len(child_session_id) <= 256
            or _ADAPTER_KIND.fullmatch(adapter_kind) is None
            or type(capability_snapshot) is not dict
            or (adapter_session_ref is not None and type(adapter_session_ref) is not dict)
        ):
            raise ValueError("task session invalid")
        _canonical_json(capability_snapshot)
        if adapter_session_ref is not None:
            _canonical_json(adapter_session_ref)
        sealed = (
            self._content_codec.seal_json(
                _session_ref_subject(task_id), dict(adapter_session_ref)
            )
            if adapter_session_ref is not None
            else None
        )
        try:
            with self._connection() as connection, connection.transaction():
                connection.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s,45))",
                    (str(task_id),),
                )
                task = connection.execute(
                    "select task_id from platform_brain.agent_tasks "
                    "where task_id=%s",
                    (task_id,),
                ).fetchone()
                if task is None:
                    raise BrainRepositoryNotFound()
                existing = connection.execute(
                    "select * from platform_brain.agent_task_sessions where task_id=%s",
                    (task_id,),
                ).fetchone()
                if existing is not None:
                    record = self._session_from_row(existing)
                    if (
                        record.child_session_id != child_session_id
                        or record.adapter_kind != adapter_kind
                        or dict(record.capability_snapshot) != dict(capability_snapshot)
                        or record.adapter_session_ref
                        != (
                            dict(adapter_session_ref)
                            if adapter_session_ref is not None
                            else None
                        )
                    ):
                        raise BrainRepositoryConflict()
                    return record
                connection.execute(
                    "insert into platform_brain.agent_task_sessions "
                    "(task_id,child_session_id,adapter_kind,"
                    "adapter_session_ref_ciphertext,adapter_session_ref_key_version,"
                    "status,capability_snapshot) values (%s,%s,%s,%s,%s,'active',%s)",
                    (
                        task_id,
                        child_session_id,
                        adapter_kind,
                        sealed.ciphertext if sealed else None,
                        sealed.key_version if sealed else None,
                        Jsonb(dict(capability_snapshot)),
                    ),
                )
            return self.task_session(task_id)
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def task_session(self, task_id: UUID) -> AgentTaskSessionRecord:
        if not isinstance(task_id, UUID):
            raise ValueError("task ID invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_brain.agent_task_sessions where task_id=%s",
                    (task_id,),
                ).fetchone()
            if row is None:
                raise BrainRepositoryNotFound()
            return self._session_from_row(row)
        except BrainRepositoryNotFound:
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def _session_from_row(self, row: Mapping[str, Any]) -> AgentTaskSessionRecord:
        reference = None
        if row["adapter_session_ref_ciphertext"] is not None:
            reference = self._content_codec.unseal_json(
                _session_ref_subject(row["task_id"]),
                SealedContent(
                    bytes(row["adapter_session_ref_ciphertext"]),
                    row["adapter_session_ref_key_version"],
                ),
            )
        return AgentTaskSessionRecord(
            task_id=row["task_id"],
            child_session_id=row["child_session_id"],
            adapter_kind=row["adapter_kind"],
            adapter_session_ref=reference,
            capability_snapshot=dict(row["capability_snapshot"]),
            status=row["status"],
        )

    def append_task_message(
        self, message: AgentTaskMessageInput
    ) -> TaskMessageAppendResult:
        if not isinstance(message, AgentTaskMessageInput):
            raise ValueError("task message invalid")
        digest = _text_hash(message.text)
        sealed = self._content_codec.seal_json(
            _message_subject(message.task_id, message.seq), {"text": message.text}
        )
        try:
            with self._connection() as connection, connection.transaction():
                connection.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s,45))",
                    (str(message.task_id),),
                )
                task = connection.execute(
                    "select task_id from platform_brain.agent_tasks "
                    "where task_id=%s",
                    (message.task_id,),
                ).fetchone()
                if task is None:
                    raise BrainRepositoryNotFound()
                existing = connection.execute(
                    "select * from platform_brain.agent_task_messages "
                    "where task_id=%s and seq=%s",
                    (message.task_id, message.seq),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["sender"] == message.sender
                        and existing["message_kind"] == message.message_kind
                        and bytes(existing["content_sha256"]) == digest
                        and existing["provider_run_ref"] == message.provider_run_ref
                        and existing["created_at"] == message.created_at
                    ):
                        return TaskMessageAppendResult(message, replayed=True)
                    raise BrainRepositoryConflict()
                state = connection.execute(
                    "select coalesce(max(seq),0) as last_seq,"
                    "count(*) filter (where sender='brain' and "
                    "message_kind='followup') as followups "
                    "from platform_brain.agent_task_messages where task_id=%s",
                    (message.task_id,),
                ).fetchone()
                if message.seq != state["last_seq"] + 1:
                    raise BrainRepositoryConflict()
                if message.seq == 1 and message.message_kind != "initial":
                    raise BrainRepositoryConflict()
                if message.seq > 1 and message.message_kind == "initial":
                    raise BrainRepositoryConflict()
                if message.message_kind == "followup" and state["followups"] >= 4:
                    raise BrainRepositoryConflict()
                connection.execute(
                    "insert into platform_brain.agent_task_messages "
                    "(task_id,seq,sender,message_kind,content_ciphertext,"
                    "content_key_version,content_sha256,provider_run_ref,created_at) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        message.task_id,
                        message.seq,
                        message.sender,
                        message.message_kind,
                        sealed.ciphertext,
                        sealed.key_version,
                        digest,
                        message.provider_run_ref,
                        message.created_at,
                    ),
                )
            return TaskMessageAppendResult(message, replayed=False)
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def task_messages(self, task_id: UUID) -> tuple[AgentTaskMessageRecord, ...]:
        if not isinstance(task_id, UUID):
            raise ValueError("task ID invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_brain.agent_task_messages "
                    "where task_id=%s order by seq",
                    (task_id,),
                ).fetchall()
            return tuple(self._message_from_row(row) for row in rows)
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def _message_from_row(self, row: Mapping[str, Any]) -> AgentTaskMessageRecord:
        value = self._content_codec.unseal_json(
            _message_subject(row["task_id"], row["seq"]),
            SealedContent(bytes(row["content_ciphertext"]), row["content_key_version"]),
        )
        text = value.get("text")
        if type(text) is not str or _text_hash(text) != bytes(row["content_sha256"]):
            raise ContentCryptoError("content decrypt failed")
        return AgentTaskMessageRecord(
            task_id=row["task_id"],
            seq=row["seq"],
            sender=row["sender"],
            message_kind=row["message_kind"],
            text=text,
            created_at=row["created_at"],
            provider_run_ref=row["provider_run_ref"],
        )

    def create_wait_subscription(
        self, spec: WaitSubscriptionSpec
    ) -> WaitSubscriptionRecord:
        if not isinstance(spec, WaitSubscriptionSpec):
            raise ValueError("wait subscription invalid")
        try:
            with self._connection() as connection, connection.transaction():
                call = connection.execute(
                    "select call.*,step.loop_id from platform_brain.brain_tool_calls call "
                    "join platform_brain.brain_steps step on step.step_id=call.step_id "
                    "where call.brain_tool_call_id=%s for update of call",
                    (spec.tool_call_id,),
                ).fetchone()
                if call is None:
                    raise BrainRepositoryNotFound()
                if (
                    call["loop_id"] != spec.loop_id
                    or call["tool_name"] != "await_agent_events"
                    or call["status"] != "waiting_result"
                ):
                    raise BrainRepositoryConflict()
                tasks = connection.execute(
                    "select task_id,loop_id from platform_brain.agent_tasks "
                    "where task_id=any(%s)",
                    (list(spec.task_ids),),
                ).fetchall()
                if len(tasks) != len(spec.task_ids) or any(
                    row["loop_id"] != spec.loop_id for row in tasks
                ):
                    raise BrainRepositoryConflict()
                for task_id in spec.task_ids:
                    connection.execute(
                        "insert into platform_brain.brain_task_event_cursors "
                        "(task_id,loop_id,delivered_seq) values (%s,%s,0) "
                        "on conflict (task_id) do nothing",
                        (task_id, spec.loop_id),
                    )
                existing = connection.execute(
                    "select * from platform_brain.brain_wait_subscriptions "
                    "where brain_tool_call_id=%s",
                    (spec.tool_call_id,),
                ).fetchone()
                if existing is not None:
                    record = self._wait_from_row(existing)
                    if (
                        record.loop_id != spec.loop_id
                        or record.task_ids != spec.task_ids
                        or record.wake_on != spec.wake_on
                    ):
                        raise BrainRepositoryConflict()
                    return record
                wait_id = uuid4()
                connection.execute(
                    "insert into platform_brain.brain_wait_subscriptions "
                    "(wait_id,brain_tool_call_id,loop_id,task_ids,wake_on,status) "
                    "values (%s,%s,%s,%s,%s,'active')",
                    (
                        wait_id,
                        spec.tool_call_id,
                        spec.loop_id,
                        list(spec.task_ids),
                        list(spec.wake_on),
                    ),
                )
            return WaitSubscriptionRecord(
                wait_id=wait_id,
                tool_call_id=spec.tool_call_id,
                loop_id=spec.loop_id,
                task_ids=spec.task_ids,
                wake_on=spec.wake_on,
                status="active",
            )
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except psycopg.errors.UniqueViolation:
            raise BrainRepositoryConflict() from None
        except psycopg.Error:
            raise BrainRepositoryError() from None

    @staticmethod
    def _wait_from_row(row: Mapping[str, Any]) -> WaitSubscriptionRecord:
        return WaitSubscriptionRecord(
            wait_id=row["wait_id"],
            tool_call_id=row["brain_tool_call_id"],
            loop_id=row["loop_id"],
            task_ids=tuple(row["task_ids"]),
            wake_on=tuple(row["wake_on"]),
            status=row["status"],
        )

    def settle_if_undelivered(
        self,
        loop_id: UUID,
        *,
        source: str,
    ) -> WaitSettlementResult:
        if not isinstance(loop_id, UUID) or source not in {
            "post_commit",
            "event_append",
            "reaper",
        }:
            raise ValueError("wait settlement input invalid")
        delays = (0.010, 0.025, 0.050)
        for attempt in range(4):
            try:
                return self._settle_once(
                    loop_id,
                    source=source,
                    attempt=attempt,
                )
            except psycopg.errors.SerializationFailure:
                if attempt == 3:
                    return WaitSettlementResult(False, source, (), 3)
                self._sleep(self._random.uniform(0.0, delays[attempt]))
        raise AssertionError("unreachable")

    def _settle_once(
        self,
        loop_id: UUID,
        *,
        source: str,
        attempt: int,
    ) -> WaitSettlementResult:
        try:
            with self._connection() as connection, connection.transaction():
                connection.execute("set transaction isolation level serializable")
                wait = connection.execute(
                    "select * from platform_brain.brain_wait_subscriptions "
                    "where loop_id=%s and status='active' "
                    "order by created_at,wait_id for update limit 1",
                    (loop_id,),
                ).fetchone()
                if wait is None:
                    return WaitSettlementResult(False, source, (), attempt)

                cursor_rows = connection.execute(
                    "select task_id,delivered_seq from "
                    "platform_brain.brain_task_event_cursors "
                    "where loop_id=%s and task_id=any(%s) "
                    "order by task_id for update",
                    (loop_id, list(wait["task_ids"])),
                ).fetchall()
                if {row["task_id"] for row in cursor_rows} != set(wait["task_ids"]):
                    raise BrainRepositoryConflict()
                delivered = {
                    row["task_id"]: row["delivered_seq"] for row in cursor_rows
                }
                event_rows = connection.execute(
                    "select event.* from platform_brain.agent_task_events event "
                    "where event.task_id=any(%s) "
                    "order by event.created_at,event.task_id,event.seq",
                    (list(wait["task_ids"]),),
                ).fetchall()
                pending_rows = tuple(
                    row
                    for row in event_rows
                    if row["seq"] > delivered[row["task_id"]]
                )
                trigger_row = next(
                    (
                        row
                        for row in pending_rows
                        if row["event_type"] in set(wait["wake_on"])
                    ),
                    None,
                )
                if trigger_row is None:
                    return WaitSettlementResult(False, source, (), attempt)
                events = tuple(self._event_from_row(row) for row in pending_rows)
                tool_result = {
                    "status": "events_ready",
                    "triggered_task_id": str(trigger_row["task_id"]),
                    "triggered_event_seq": trigger_row["seq"],
                    "events": [
                        {
                            "task_id": str(item.task_id),
                            "seq": item.seq,
                            "event_type": item.event_type,
                            "payload": dict(item.payload),
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in events
                    ],
                }
                sealed_result = self._content_codec.seal_json(
                    _tool_result_subject(wait["brain_tool_call_id"]), tool_result
                )
                updated = connection.execute(
                    "update platform_brain.brain_wait_subscriptions set "
                    "status='triggered',triggered_task_id=%s,triggered_event_seq=%s,"
                    "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "where wait_id=%s and status='active'",
                    (
                        trigger_row["task_id"],
                        trigger_row["seq"],
                        wait["wait_id"],
                    ),
                ).rowcount
                if updated != 1:
                    raise BrainRepositoryConflict()
                call = connection.execute(
                    "update platform_brain.brain_tool_calls set status='result_ready',"
                    "result_ciphertext=%s,result_key_version=%s,result_sha256=%s,"
                    "updated_at=clock_timestamp() where brain_tool_call_id=%s "
                    "and status='waiting_result' returning step_id",
                    (
                        sealed_result.ciphertext,
                        sealed_result.key_version,
                        _json_hash(tool_result),
                        wait["brain_tool_call_id"],
                    ),
                ).fetchone()
                if call is None:
                    raise BrainRepositoryConflict()
                step = connection.execute(
                    "update platform_brain.brain_steps set status='completed',"
                    "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "where step_id=%s and status='waiting_tool_results' "
                    "returning loop_id,step_seq",
                    (call["step_id"],),
                ).fetchone()
                if step is None:
                    raise BrainRepositoryConflict()
                queued_step_id = uuid4()
                connection.execute(
                    "insert into platform_brain.brain_steps "
                    "(step_id,loop_id,step_seq,status) values (%s,%s,%s,'queued')",
                    (queued_step_id, step["loop_id"], step["step_seq"] + 1),
                )
                loop = connection.execute(
                    "update platform_brain.brain_loops set status='running',"
                    "updated_at=clock_timestamp(),row_version=row_version+1 "
                    "where loop_id=%s and status='waiting_agents' returning turn_id",
                    (step["loop_id"],),
                ).fetchone()
                if loop is None:
                    raise BrainRepositoryConflict()
                connection.execute(
                    "update platform_control.conversation_turns set status='running',"
                    "updated_at=clock_timestamp() where turn_id=%s",
                    (loop["turn_id"],),
                )
                highest: dict[UUID, int] = dict(delivered)
                for row in pending_rows:
                    highest[row["task_id"]] = max(
                        highest[row["task_id"]], row["seq"]
                    )
                for task_id, sequence in highest.items():
                    connection.execute(
                        "update platform_brain.brain_task_event_cursors set "
                        "delivered_seq=%s,updated_at=clock_timestamp() "
                        "where task_id=%s and loop_id=%s",
                        (sequence, task_id, loop_id),
                    )
                return WaitSettlementResult(
                    True,
                    source,
                    events,
                    attempt,
                    wait["wait_id"],
                    queued_step_id,
                )
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except psycopg.errors.SerializationFailure:
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def active_wait_loop_ids(self, *, limit: int) -> tuple[UUID, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("active wait scan limit invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select loop_id from platform_brain.brain_wait_subscriptions "
                    "where status='active' order by updated_at,wait_id limit %s",
                    (limit,),
                ).fetchall()
            return tuple(row["loop_id"] for row in rows)
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def settle_active_waits(self, *, limit: int) -> int:
        return sum(
            self.settle_if_undelivered(loop_id, source="reaper").settled
            for loop_id in self.active_wait_loop_ids(limit=limit)
        )

    def append_task_event_and_wake(
        self, event: AgentTaskPublicEventInput
    ) -> EventWakeResult:
        if not isinstance(event, AgentTaskPublicEventInput):
            raise ValueError("public Agent event invalid")
        payload = dict(event.payload)
        _canonical_json(payload)
        payload_hash = _json_hash(payload)
        sealed_payload = self._content_codec.seal_json(
            _event_subject(event.task_id, event.seq), payload
        )
        try:
            with self._connection() as connection, connection.transaction():
                connection.execute("set transaction isolation level serializable")
                inserted = connection.execute(
                    "select platform_brain.append_agent_task_event_v49("
                    "%s,%s,%s,%s,%s,%s,%s) as inserted",
                    (
                        event.task_id,
                        event.seq,
                        event.event_type,
                        sealed_payload.ciphertext,
                        sealed_payload.key_version,
                        payload_hash,
                        event.created_at,
                    ),
                ).fetchone()["inserted"]
                task = connection.execute(
                    "select loop_id from platform_brain.agent_tasks where task_id=%s",
                    (event.task_id,),
                ).fetchone()
                if task is None:
                    raise BrainRepositoryNotFound()
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except psycopg.errors.CheckViolation:
            raise BrainRepositoryConflict() from None
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None
        try:
            settlement = self.settle_if_undelivered(
                task["loop_id"], source="event_append"
            )
        except BrainRepositoryError:
            settlement = WaitSettlementResult(False, "event_append", (), 0)
        return EventWakeResult(
            not inserted,
            settlement.woken_wait_id,
            settlement.events,
            settlement.queued_step_id,
        )

    def _event_from_row(self, row: Mapping[str, Any]) -> AgentTaskPublicEventRecord:
        payload = self._content_codec.unseal_json(
            _event_subject(row["task_id"], row["seq"]),
            SealedContent(bytes(row["payload_ciphertext"]), row["payload_key_version"]),
        )
        if _json_hash(payload) != bytes(row["payload_sha256"]):
            raise ContentCryptoError("content decrypt failed")
        return AgentTaskPublicEventRecord(
            task_id=row["task_id"],
            seq=row["seq"],
            event_type=row["event_type"],
            payload=payload,
            created_at=row["created_at"],
        )

    def queued_step_count(self, loop_id: UUID) -> int:
        if not isinstance(loop_id, UUID):
            raise ValueError("Loop ID invalid")
        try:
            with self._connection() as connection:
                return connection.execute(
                    "select count(*) as count from platform_brain.brain_steps "
                    "where loop_id=%s and status='queued'",
                    (loop_id,),
                ).fetchone()["count"]
        except psycopg.Error:
            raise BrainRepositoryError() from None

    def append_thinking_delta(
        self, delta: BrainThinkingDelta
    ) -> BrainThinkingSummaryRecord:
        if not isinstance(delta, BrainThinkingDelta):
            raise ValueError("thinking delta invalid")
        try:
            with self._connection() as connection, connection.transaction():
                step = connection.execute(
                    "select step_id from platform_brain.brain_steps "
                    "where step_id=%s for update",
                    (delta.step_id,),
                ).fetchone()
                if step is None:
                    raise BrainRepositoryNotFound()
                row = connection.execute(
                    "select * from platform_brain.brain_thinking_summaries "
                    "where step_id=%s and block_index=%s for update",
                    (delta.step_id, delta.block_index),
                ).fetchone()
                if row is None:
                    if delta.delta_seq != 1:
                        raise BrainRepositoryConflict()
                    text = delta.text
                    sealed = self._content_codec.seal_json(
                        _thinking_subject(delta.step_id, delta.block_index),
                        {"text": text},
                    )
                    connection.execute(
                        "insert into platform_brain.brain_thinking_summaries "
                        "(step_id,block_index,last_delta_seq,summary_ciphertext,"
                        "summary_key_version,source,provider_run_ref,status) "
                        "values (%s,%s,1,%s,%s,'provider',%s,'streaming')",
                        (
                            delta.step_id,
                            delta.block_index,
                            sealed.ciphertext,
                            sealed.key_version,
                            delta.provider_run_ref,
                        ),
                    )
                else:
                    current = self._thinking_from_row(row)
                    if (
                        current.provider_run_ref != delta.provider_run_ref
                        or current.status != "streaming"
                    ):
                        raise BrainRepositoryConflict()
                    if delta.delta_seq == current.last_delta_seq:
                        if not current.text.endswith(delta.text):
                            raise BrainRepositoryConflict()
                        return current
                    if delta.delta_seq != current.last_delta_seq + 1:
                        raise BrainRepositoryConflict()
                    text = current.text + delta.text
                    if len(text.encode("utf-8")) > 512 * 1024:
                        raise BrainRepositoryConflict()
                    sealed = self._content_codec.seal_json(
                        _thinking_subject(delta.step_id, delta.block_index),
                        {"text": text},
                    )
                    connection.execute(
                        "update platform_brain.brain_thinking_summaries set "
                        "last_delta_seq=%s,summary_ciphertext=%s,summary_key_version=%s,"
                        "updated_at=clock_timestamp() where step_id=%s and block_index=%s",
                        (
                            delta.delta_seq,
                            sealed.ciphertext,
                            sealed.key_version,
                            delta.step_id,
                            delta.block_index,
                        ),
                    )
                return BrainThinkingSummaryRecord(
                    delta.step_id,
                    delta.block_index,
                    delta.delta_seq,
                    text,
                    delta.provider_run_ref,
                    "streaming",
                )
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def finalize_thinking_summary(
        self, step_id: UUID, block_index: int, *, interrupted: bool = False
    ) -> BrainThinkingSummaryRecord:
        if (
            not isinstance(step_id, UUID)
            or type(block_index) is not int
            or block_index < 0
            or type(interrupted) is not bool
        ):
            raise ValueError("thinking summary identity invalid")
        status = "interrupted" if interrupted else "completed"
        try:
            with self._connection() as connection, connection.transaction():
                row = connection.execute(
                    "select * from platform_brain.brain_thinking_summaries "
                    "where step_id=%s and block_index=%s for update",
                    (step_id, block_index),
                ).fetchone()
                if row is None:
                    raise BrainRepositoryNotFound()
                if row["status"] == "streaming":
                    connection.execute(
                        "update platform_brain.brain_thinking_summaries set status=%s,"
                        "updated_at=clock_timestamp() where step_id=%s and block_index=%s",
                        (status, step_id, block_index),
                    )
                    row = dict(row)
                    row["status"] = status
                elif row["status"] != status:
                    raise BrainRepositoryConflict()
                return self._thinking_from_row(row)
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None

    def _thinking_from_row(
        self, row: Mapping[str, Any]
    ) -> BrainThinkingSummaryRecord:
        value = self._content_codec.unseal_json(
            _thinking_subject(row["step_id"], row["block_index"]),
            SealedContent(
                bytes(row["summary_ciphertext"]), row["summary_key_version"]
            ),
        )
        text = value.get("text")
        if type(text) is not str:
            raise ContentCryptoError("content decrypt failed")
        return BrainThinkingSummaryRecord(
            step_id=row["step_id"],
            block_index=row["block_index"],
            last_delta_seq=row["last_delta_seq"],
            text=text,
            provider_run_ref=row["provider_run_ref"],
            status=row["status"],
        )

    def claim_intervention(
        self, loop_id: UUID, consuming_step_id: UUID
    ) -> UserInterventionRecord | None:
        if not isinstance(loop_id, UUID) or not isinstance(consuming_step_id, UUID):
            raise ValueError("intervention claim identity invalid")
        try:
            with self._connection() as connection, connection.transaction():
                step = connection.execute(
                    "select loop_id from platform_brain.brain_steps "
                    "where step_id=%s",
                    (consuming_step_id,),
                ).fetchone()
                if step is None:
                    raise BrainRepositoryNotFound()
                if step["loop_id"] != loop_id:
                    raise BrainRepositoryConflict()
                row = connection.execute(
                    "select * from platform_brain.brain_user_interventions "
                    "where loop_id=%s and status='pending' "
                    "order by created_at,intervention_id for update skip locked limit 1",
                    (loop_id,),
                ).fetchone()
                if row is None:
                    return None
                connection.execute(
                    "update platform_brain.brain_user_interventions set "
                    "status='consumed',consumed_by_step_id=%s,"
                    "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "where intervention_id=%s",
                    (consuming_step_id, row["intervention_id"]),
                )
                value = self._content_codec.unseal_json(
                    _intervention_subject(row["intervention_id"]),
                    SealedContent(
                        bytes(row["content_ciphertext"]), row["content_key_version"]
                    ),
                )
                text = value.get("text")
                if type(text) is not str or _text_hash(text) != bytes(
                    row["content_sha256"]
                ):
                    raise ContentCryptoError("content decrypt failed")
                return UserInterventionRecord(
                    intervention_id=row["intervention_id"],
                    loop_id=row["loop_id"],
                    message_id=row["message_id"],
                    text=text,
                    status="consumed",
                    consumed_by_step_id=consuming_step_id,
                    created_at=row["created_at"],
                )
        except (BrainRepositoryConflict, BrainRepositoryNotFound):
            raise
        except (ContentCryptoError, psycopg.Error):
            raise BrainRepositoryError() from None
