from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg

from app.agent_brain.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryError,
    event_subject,
    message_subject,
)
from app.agent_brain.repository import MissionRepositoryError
from app.execution_relay.content_crypto import (
    ContentCryptoError,
    SealedContent,
)


PUBLIC_BRAIN_EVENT_TYPES = frozenset(
    {
        "brain.started",
        "brain.step_started",
        "agent.task_dispatched",
        "agent.task_accepted",
        "agent.task_progress",
        "agent.task_completed",
        "agent.task_failed",
        "agent.task_timed_out",
        "agent.task_unavailable",
        "brain.batch_settled",
        "brain.resumed",
        "brain.user_input_requested",
        "brain.answer_submitted",
        "brain.failed",
        "brain.thinking_summary",
        "brain.waiting_agents",
        "brain.user_intervention",
        "brain.agent_message_sent",
        "brain.agent_stop_requested",
        "agent.thinking_summary",
        "agent.message",
        "agent.work_update",
        "agent.artifact",
        "agent.question",
        "agent.input_required",
        "agent.action_required",
        "agent.cancelled",
        "agent.task_recovered",
    }
)
PUBLIC_BRAIN_PAYLOAD_KEYS = frozenset(
    {
        "agent_id",
        "agent_name",
        "objective_summary",
        "public_reason",
        "status",
        "duration_ms",
        "reason_code",
        "task_id",
        "child_session_id",
        "source",
        "source_ref",
        "kind",
        "summary",
        "evidence_refs",
        "action_id",
        "action_kind",
        "impact",
        "execution_status",
        "action_digest",
        "action_digest_prefix",
        "expires_at",
        "confirmed_at",
        "confirmed_by",
        "created_at",
    }
)
NON_DATA_PRODUCT_EVENT_TYPES = frozenset(
    {"brain.thinking_summary", "agent.thinking_summary"}
)


@dataclass(frozen=True, slots=True)
class PrivateBrainEvent:
    event_type: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class PublicBrainEvent:
    event_type: str
    payload: dict[str, object]


_TERMINAL_MISSIONS = frozenset(
    {"completed", "partially_completed", "failed", "cancelled", "interrupted"}
)
_TURN_BY_EVENT = {
    "mission.completed": "completed",
    "mission.failed": "failed",
    "mission.cancelled": "cancelled",
    "mission.interrupted": "interrupted",
}


class ConversationProjection:
    def __init__(self, repository: ConversationRepository) -> None:
        if not isinstance(repository, ConversationRepository):
            raise ValueError("Conversation repository required")
        self.repository = repository
        self.missions = repository._missions

    @staticmethod
    def project(event: PrivateBrainEvent) -> PublicBrainEvent:
        if event.event_type not in PUBLIC_BRAIN_EVENT_TYPES:
            raise ValueError("public Brain event type invalid")
        payload = {
            key: value
            for key, value in event.payload.items()
            if key in PUBLIC_BRAIN_PAYLOAD_KEYS
        }
        if event.event_type in {"brain.thinking_summary", "agent.thinking_summary"}:
            if (
                payload.get("source") != "provider"
                or type(payload.get("source_ref")) is not str
                or type(payload.get("summary")) is not str
            ):
                raise ValueError("public Brain event payload invalid")
        return PublicBrainEvent(event.event_type, payload)

    @staticmethod
    def public_payload(
        event_type: str, payload: dict[str, object]
    ) -> dict[str, object]:
        if event_type not in PUBLIC_BRAIN_EVENT_TYPES:
            return payload
        try:
            return ConversationProjection.project(
                PrivateBrainEvent(event_type, payload)
            ).payload
        except ValueError:
            return {"status": "public_event_unavailable"}

    @staticmethod
    def data_product_payload(
        event_type: str, payload: dict[str, object]
    ) -> dict[str, object]:
        if event_type in NON_DATA_PRODUCT_EVENT_TYPES:
            raise ValueError("Brain thinking event is not exportable")
        return ConversationProjection.project(
            PrivateBrainEvent(event_type, payload)
        ).payload

    @staticmethod
    def searchable_text(event_type: str, payload: dict[str, object]) -> str:
        if event_type in NON_DATA_PRODUCT_EVENT_TYPES:
            raise ValueError("Brain thinking event is not searchable")
        projected = ConversationProjection.project(
            PrivateBrainEvent(event_type, payload)
        ).payload
        return "\n".join(
            value
            for key in ("objective_summary", "public_reason", "summary")
            if isinstance((value := projected.get(key)), str) and value.strip()
        )

    @staticmethod
    def _source_event_id(source_key: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"orbbec-agent-brain-public:{source_key}")

    def _append_public_brain_event_locked(
        self,
        cursor,
        conversation_id: UUID,
        turn_id: UUID,
        source_key: str,
        event_type: str,
        payload: dict[str, object],
        created_at: datetime,
    ) -> bool:
        projected = self.project(PrivateBrainEvent(event_type, payload))
        event_id = self._source_event_id(source_key)
        if cursor.execute(
            "select 1 from platform_control.conversation_events where event_id=%s",
            (event_id,),
        ).fetchone() is not None:
            return False
        sealed = self.repository.content_codec.seal_json(
            event_subject(conversation_id, event_id), projected.payload
        )
        sequence = cursor.execute(
            "select coalesce(max(seq),0)+1 as next_seq from "
            "platform_control.conversation_events where conversation_id=%s",
            (conversation_id,),
        ).fetchone()["next_seq"]
        cursor.execute(
            "insert into platform_control.conversation_events "
            "(event_id,conversation_id,seq,turn_id,mission_id,event_type,"
            "payload_ciphertext,encryption_key_version,created_at) "
            "values (%s,%s,%s,%s,null,%s,%s,%s,%s)",
            (
                event_id,
                conversation_id,
                sequence,
                turn_id,
                event_type,
                sealed.ciphertext,
                sealed.key_version,
                created_at,
            ),
        )
        return True

    def project_brain_pending(
        self, conversation_id: UUID, *, limit: int = 100
    ) -> int:
        if not isinstance(conversation_id, UUID) or not 1 <= limit <= 500:
            raise ValueError("Brain projection input invalid")
        try:
            with self.repository._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select conversation_id from platform_control.conversations "
                    "where conversation_id=%s for update",
                    (conversation_id,),
                )
                loops = cursor.execute(
                    "select * from platform_brain.brain_loops where conversation_id=%s "
                    "order by created_at,loop_id",
                    (conversation_id,),
                ).fetchall()
                candidates: list[
                    tuple[datetime, str, UUID, str, dict[str, object]]
                ] = []
                for loop in loops:
                    loop_id = loop["loop_id"]
                    turn_id = loop["turn_id"]
                    candidates.append(
                        (
                            loop["created_at"],
                            f"loop:{loop_id}:started",
                            turn_id,
                            "brain.started",
                            {"status": "running"},
                        )
                    )
                    steps = cursor.execute(
                        "select * from platform_brain.brain_steps where loop_id=%s "
                        "order by step_seq",
                        (loop_id,),
                    ).fetchall()
                    for step in steps:
                        candidates.append(
                            (
                                step["created_at"],
                                f"step:{step['step_id']}:started",
                                turn_id,
                                "brain.step_started",
                                {"status": step["status"]},
                            )
                        )
                        if step["step_seq"] > 1:
                            candidates.append(
                                (
                                    step["created_at"],
                                    f"step:{step['step_id']}:resumed",
                                    turn_id,
                                    "brain.resumed",
                                    {"status": "running"},
                                )
                            )
                        summaries = cursor.execute(
                            "select * from platform_brain.brain_thinking_summaries "
                            "where step_id=%s and status in ('completed','interrupted') "
                            "order by block_index",
                            (step["step_id"],),
                        ).fetchall()
                        for summary in summaries:
                            value = self.repository.content_codec.unseal_json(
                                f"brain-step:{step['step_id']}:thinking:"
                                f"{summary['block_index']}",
                                SealedContent(
                                    bytes(summary["summary_ciphertext"]),
                                    summary["summary_key_version"],
                                ),
                            )
                            text = value.get("text")
                            if not isinstance(text, str) or not text:
                                continue
                            candidates.append(
                                (
                                    summary["updated_at"],
                                    f"step:{step['step_id']}:thinking:"
                                    f"{summary['block_index']}",
                                    turn_id,
                                    "brain.thinking_summary",
                                    {
                                        "source": "provider",
                                        "source_ref": summary["provider_run_ref"],
                                        "summary": text[:4096],
                                        "status": summary["status"],
                                        "created_at": summary["updated_at"].isoformat(),
                                    },
                                )
                            )
                    tasks = cursor.execute(
                        "select task.*,call.public_reason,session.child_session_id from "
                        "platform_brain.agent_tasks task join "
                        "platform_brain.brain_tool_calls call on "
                        "call.brain_tool_call_id=task.brain_tool_call_id left join "
                        "platform_brain.agent_task_sessions session on "
                        "session.task_id=task.task_id "
                        "where task.loop_id=%s order by task.created_at,task.task_id",
                        (loop_id,),
                    ).fetchall()
                    for task in tasks:
                        context = self.repository.content_codec.unseal_json(
                            f"brain-task:{task['task_id']}:context",
                            SealedContent(
                                bytes(task["task_context_ciphertext"]),
                                task["task_context_key_version"],
                            ),
                        )
                        objective = context.get("objective")
                        base_payload = {
                            "task_id": str(task["task_id"]),
                            "child_session_id": task["child_session_id"],
                            "agent_id": task["agent_id"],
                            "objective_summary": (
                                objective[:512]
                                if isinstance(objective, str)
                                else "已分派专业任务"
                            ),
                            "public_reason": task["public_reason"],
                            "status": task["status"],
                        }
                        candidates.append(
                            (
                                task["created_at"],
                                f"task:{task['task_id']}:dispatched",
                                turn_id,
                                "agent.task_dispatched",
                                base_payload,
                            )
                        )
                        if task["status"] != "queued":
                            candidates.append(
                                (
                                    task["started_at"] or task["updated_at"],
                                    f"task:{task['task_id']}:accepted",
                                    turn_id,
                                    "agent.task_accepted",
                                    base_payload,
                                )
                            )
                        task_events = cursor.execute(
                            "select * from platform_brain.agent_task_events "
                            "where task_id=%s order by seq",
                            (task["task_id"],),
                        ).fetchall()
                        for task_event in task_events:
                            value = self.repository.content_codec.unseal_json(
                                f"brain-task:{task['task_id']}:event:"
                                f"{task_event['seq']}:payload",
                                SealedContent(
                                    bytes(task_event["payload_ciphertext"]),
                                    task_event["payload_key_version"],
                                ),
                            )
                            stored_type = task_event["event_type"]
                            if stored_type == "action_required":
                                # Action cards come only from the verified,
                                # encrypted Action record below. The raw
                                # Adapter event is a wake signal, not a UI
                                # projection authority.
                                continue
                            event_type = {
                                "thinking_summary": "agent.thinking_summary",
                                "message": "agent.message",
                                "work_update": "agent.work_update",
                                "finding": "agent.work_update",
                                "artifact": "agent.artifact",
                                "question": "agent.question",
                                "input_required": "agent.input_required",
                                "result": "agent.task_completed",
                                "failed": "agent.task_failed",
                                "timeout": "agent.task_timed_out",
                                "cancelled": "agent.cancelled",
                            }.get(stored_type, "agent.task_progress")
                            summary = value.get("summary") or value.get("text")
                            event_payload = {
                                **base_payload,
                                "source": value.get("source", "adapter"),
                                "source_ref": value.get(
                                    "source_ref", f"event:{task_event['seq']}"
                                ),
                                "kind": (
                                    "finding" if stored_type == "finding" else stored_type
                                ),
                                "summary": (
                                    summary[:2048]
                                    if isinstance(summary, str)
                                    else "专业 Agent 已更新任务状态"
                                ),
                                "status": value.get("status", task["status"]),
                                "evidence_refs": value.get("evidence_refs", []),
                                "artifact_refs": value.get("artifact_refs", []),
                                "created_at": task_event["created_at"].isoformat(),
                            }
                            candidates.append(
                                (
                                    task_event["created_at"],
                                    f"task:{task['task_id']}:event:{task_event['seq']}",
                                    turn_id,
                                    event_type,
                                    event_payload,
                                )
                            )
                        actions = cursor.execute(
                            "select action.*,confirmed.display_name as confirmed_by "
                            "from platform_brain.agent_task_actions action left join "
                            "platform_control.internal_users confirmed on "
                            "confirmed.internal_user_id="
                            "action.confirmed_by_internal_user_id "
                            "where action.task_id=%s order by "
                            "action.created_at,action.action_id",
                            (task["task_id"],),
                        ).fetchall()
                        for action in actions:
                            action_id = action["action_id"]
                            summary = self.repository.content_codec.unseal_json(
                                f"brain-action:{action_id}:summary",
                                SealedContent(
                                    bytes(action["summary_ciphertext"]),
                                    action["summary_key_version"],
                                ),
                            ).get("text")
                            impact = self.repository.content_codec.unseal_json(
                                f"brain-action:{action_id}:impact",
                                SealedContent(
                                    bytes(action["impact_ciphertext"]),
                                    action["impact_key_version"],
                                ),
                            ).get("text")
                            if not isinstance(summary, str) or not isinstance(
                                impact, str
                            ):
                                raise ValueError("Action projection content invalid")
                            digest = bytes(action["action_digest"]).hex()
                            candidates.append(
                                (
                                    action["updated_at"],
                                    f"action:{action_id}:{action['status']}:"
                                    f"{action['execution_status']}",
                                    turn_id,
                                    "agent.action_required",
                                    {
                                        "action_id": str(action_id),
                                        "task_id": str(task["task_id"]),
                                        "action_kind": action["action_kind"],
                                        "summary": summary,
                                        "impact": impact,
                                        "status": action["status"],
                                        "execution_status": action[
                                            "execution_status"
                                        ],
                                        "action_digest": digest,
                                        "action_digest_prefix": digest[:12],
                                        "expires_at": action[
                                            "expires_at"
                                        ].isoformat(),
                                        "confirmed_at": (
                                            action["confirmed_at"].isoformat()
                                            if action["confirmed_at"] is not None
                                            else None
                                        ),
                                        "confirmed_by": action["confirmed_by"],
                                    },
                                )
                            )
                    for step in steps:
                        batch = cursor.execute(
                            "select count(*) as total,"
                            "count(*) filter (where task.status in "
                            "('completed','failed','cancelled','timed_out','unavailable')) "
                            "as terminal,max(task.terminal_at) as settled_at from "
                            "platform_brain.agent_tasks task join "
                            "platform_brain.brain_tool_calls call on "
                            "call.brain_tool_call_id=task.brain_tool_call_id "
                            "where call.step_id=%s",
                            (step["step_id"],),
                        ).fetchone()
                        if batch["total"] and batch["total"] == batch["terminal"]:
                            candidates.append(
                                (
                                    batch["settled_at"] or step["updated_at"],
                                    f"step:{step['step_id']}:batch-settled",
                                    turn_id,
                                    "brain.batch_settled",
                                    {"status": "completed"},
                                )
                            )
                    request_calls = cursor.execute(
                        "select call.brain_tool_call_id,call.public_reason,"
                        "call.created_at from platform_brain.brain_tool_calls call "
                        "join platform_brain.brain_steps step on "
                        "step.step_id=call.step_id where step.loop_id=%s "
                        "and call.tool_name='request_user_input'",
                        (loop_id,),
                    ).fetchall()
                    for call in request_calls:
                        candidates.append(
                            (
                                call["created_at"],
                                f"call:{call['brain_tool_call_id']}:waiting-user",
                                turn_id,
                                "brain.user_input_requested",
                                {
                                    "objective_summary": call["public_reason"],
                                    "public_reason": call["public_reason"],
                                    "status": "waiting_user",
                                },
                            )
                        )
                    terminal_type = {
                        "completed": "brain.answer_submitted",
                        "failed": "brain.failed",
                        "interrupted": "brain.failed",
                        "cancelled": "brain.failed",
                    }.get(loop["status"])
                    if terminal_type:
                        candidates.append(
                            (
                                loop["terminal_at"] or loop["updated_at"],
                                f"loop:{loop_id}:terminal",
                                turn_id,
                                terminal_type,
                                {
                                    "status": loop["status"],
                                    "reason_code": loop["reason_code"],
                                },
                            )
                        )
                inserted = 0
                for created_at, source, turn_id, event_type, payload in sorted(
                    candidates, key=lambda item: (item[0], item[1])
                ):
                    if inserted >= limit:
                        break
                    inserted += int(
                        self._append_public_brain_event_locked(
                            cursor,
                            conversation_id,
                            turn_id,
                            source,
                            event_type,
                            payload,
                            created_at,
                        )
                    )
                return inserted
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    @staticmethod
    def _append_event(
        cursor,
        repository: ConversationRepository,
        conversation_id: UUID,
        turn_id: UUID,
        mission_id: UUID,
        event_type: str,
        *,
        message_id: UUID,
    ) -> None:
        event_id = uuid4()
        sealed = repository.content_codec.seal_json(
            event_subject(conversation_id, event_id),
            {
                "turn_id": str(turn_id),
                "mission_id": str(mission_id),
                "message_id": str(message_id),
                "status": event_type.rsplit(".", 1)[-1],
            },
        )
        sequence = cursor.execute(
            "select coalesce(max(seq),0)+1 as next_seq from "
            "platform_control.conversation_events where conversation_id=%s",
            (conversation_id,),
        ).fetchone()["next_seq"]
        cursor.execute(
            "insert into platform_control.conversation_events "
            "(event_id,conversation_id,seq,turn_id,mission_id,event_type,"
            "payload_ciphertext,encryption_key_version) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                event_id,
                conversation_id,
                sequence,
                turn_id,
                mission_id,
                event_type,
                sealed.ciphertext,
                sealed.key_version,
            ),
        )

    def project_terminal(self, mission_id: UUID) -> bool:
        if not isinstance(mission_id, UUID):
            raise ValueError("Mission ID invalid")
        message_id = uuid4()
        try:
            with self.repository._connection() as connection, connection.cursor() as cursor:
                cursor.execute("set constraints all deferred")
                row = cursor.execute(
                    "select mission.mission_id,mission.status as mission_status,"
                    "mission.conversation_id,mission.turn_id,"
                    "turn.status as turn_status,turn.assistant_message_id "
                    "from platform_control.missions mission "
                    "join platform_control.conversation_turns turn "
                    "on turn.conversation_id=mission.conversation_id "
                    "and turn.turn_id=mission.turn_id "
                    "join platform_control.conversations conversation "
                    "on conversation.conversation_id=mission.conversation_id "
                    "where mission.mission_id=%s "
                    "for update of mission,turn,conversation",
                    (mission_id,),
                ).fetchone()
                if row is None or row["mission_status"] not in _TERMINAL_MISSIONS:
                    return False
                if (
                    row["assistant_message_id"] is not None
                    or row["turn_status"] not in {"accepted", "running"}
                ):
                    return False
                event_type, text = self.missions.terminal_delivery_for_projection(
                    cursor, mission_id
                )
                turn_status = _TURN_BY_EVENT[event_type]
                role = "assistant" if event_type == "mission.completed" else "system"
                delivery_status = (
                    "completed" if event_type == "mission.completed" else "failed"
                )
                conversation_id = row["conversation_id"]
                turn_id = row["turn_id"]
                sequence = cursor.execute(
                    "select coalesce(max(seq),0)+1 as next_seq from "
                    "platform_control.conversation_messages "
                    "where conversation_id=%s",
                    (conversation_id,),
                ).fetchone()["next_seq"]
                sealed = self.repository.content_codec.seal_json(
                    message_subject(conversation_id, message_id), {"text": text}
                )
                cursor.execute(
                    "insert into platform_control.conversation_messages "
                    "(message_id,conversation_id,seq,role,content_ciphertext,"
                    "encryption_key_version,turn_id,mission_id,delivery_status,"
                    "completed_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())",
                    (
                        message_id,
                        conversation_id,
                        sequence,
                        role,
                        sealed.ciphertext,
                        sealed.key_version,
                        turn_id,
                        mission_id,
                        delivery_status,
                    ),
                )
                cursor.execute(
                    "update platform_control.conversation_turns set "
                    "assistant_message_id=%s,status=%s,updated_at=now() "
                    "where conversation_id=%s and turn_id=%s",
                    (message_id, turn_status, conversation_id, turn_id),
                )
                cursor.execute(
                    "update platform_control.conversations set updated_at=now() "
                    "where conversation_id=%s",
                    (conversation_id,),
                )
                self._append_event(
                    cursor,
                    self.repository,
                    conversation_id,
                    turn_id,
                    mission_id,
                    "message.completed"
                    if delivery_status == "completed"
                    else "message.failed",
                    message_id=message_id,
                )
                self._append_event(
                    cursor,
                    self.repository,
                    conversation_id,
                    turn_id,
                    mission_id,
                    f"turn.{turn_status}",
                    message_id=message_id,
                )
            return True
        except ConversationRepositoryError:
            raise
        except MissionRepositoryError:
            raise ConversationRepositoryError() from None
        except (
            ContentCryptoError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    def project_pending(self, *, limit: int = 50) -> int:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise ValueError("Projection limit invalid")
        try:
            with self.repository._connection() as connection, connection.cursor() as cursor:
                mission_ids = [
                    row["mission_id"]
                    for row in cursor.execute(
                        "select mission.mission_id from platform_control.missions mission "
                        "join platform_control.conversation_turns turn "
                        "on turn.conversation_id=mission.conversation_id "
                        "and turn.turn_id=mission.turn_id "
                        "where mission.status in ("
                        "'completed','partially_completed','failed','cancelled','interrupted') "
                        "and turn.status in ('accepted','running') "
                        "and turn.assistant_message_id is null "
                        "order by mission.updated_at,mission.mission_id limit %s",
                        (limit,),
                    ).fetchall()
                ]
            return sum(self.project_terminal(mission_id) for mission_id in mission_ids)
        except ConversationRepositoryError:
            raise
        except psycopg.Error:
            raise ConversationRepositoryError() from None
