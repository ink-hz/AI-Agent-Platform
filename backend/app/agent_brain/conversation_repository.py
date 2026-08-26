from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_models import (
    ConversationCreateResult,
    ConversationEventRecord,
    ConversationFeedbackRecord,
    ConversationFeedbackResult,
    ConversationInterventionResult,
    ConversationMetrics,
    ConversationMessageRecord,
    ConversationRecord,
    ConversationTurnRecord,
)
from app.agent_brain.repository import (
    MissionRecord,
    MissionRepository,
    MissionRepositoryError,
)
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)


_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PROJECTED_MISSION_EVENT_TYPES = (
    "brain.responding",
    "plan.created",
    "task.dispatched",
    "agent.accepted",
    "agent.progress",
    "agent.result",
    "task.reviewed",
    "synthesis.started",
)


class ConversationRepositoryError(RuntimeError):
    """Stable persistence failure that never exposes SQL or protected content."""

    def __init__(self, message: str = "conversation repository unavailable") -> None:
        super().__init__(message)


class ConversationRepositoryConflict(ConversationRepositoryError):
    def __init__(self, message: str = "conversation repository conflict") -> None:
        super().__init__(message)


class ConversationTurnInProgress(ConversationRepositoryConflict):
    def __init__(self) -> None:
        super().__init__("conversation turn in progress")


class ConversationRepositoryNotFound(ConversationRepositoryError):
    def __init__(self) -> None:
        super().__init__("conversation not found")


def message_subject(conversation_id: UUID, message_id: UUID) -> str:
    return f"conversation:{conversation_id}:message:{message_id}:content"


def event_subject(conversation_id: UUID, event_id: UUID) -> str:
    return f"conversation:{conversation_id}:event:{event_id}:payload"


def feedback_subject(feedback_id: UUID) -> str:
    return f"conversation-feedback:{feedback_id}:comment"


def _mission_event_subject(mission_id: UUID, event_id: UUID) -> str:
    return f"mission:{mission_id}:event:{event_id}:payload"


def _summary_subject(conversation_id: UUID, key_version: int) -> str:
    return f"conversation:{conversation_id}:summary:v{key_version}"


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID required")
    return value


def _require_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Conversation text invalid")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ConversationRepositoryError() from None
    if size > 32 * 1024:
        raise ValueError("Conversation text invalid")
    return value


def _title_for(text: str) -> str:
    title = " ".join(part.strip() for part in text.strip().splitlines() if part.strip())
    return title[:160]


def _require_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Conversation title invalid")
    selected = value.strip()
    if not selected or len(selected) > 160:
        raise ValueError("Conversation title invalid")
    return selected


def _require_mode(
    mode: object, direct_agent_id: object
) -> tuple[Literal["brain", "direct_agent"], str | None]:
    if mode == "brain":
        if direct_agent_id is not None:
            raise ValueError("Brain Conversation cannot name a direct Agent")
        return "brain", None
    if mode == "direct_agent":
        if (
            not isinstance(direct_agent_id, str)
            or _AGENT_ID.fullmatch(direct_agent_id) is None
        ):
            raise ValueError("direct Agent invalid")
        return "direct_agent", direct_agent_id
    raise ValueError("Conversation mode invalid")


class ConversationRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        mission_repository: MissionRepository | None = None,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        selected_missions = mission_repository or MissionRepository(
            control_database_url,
            content_codec=content_codec,
            connect=connect,
        )
        if (
            not isinstance(selected_missions, MissionRepository)
            or selected_missions.environment != parsed.environment
            or selected_missions.content_codec is not content_codec
        ):
            raise ValueError("Mission repository boundary invalid")
        self.environment = parsed.environment
        self._control_database_url = control_database_url
        self._connect = connect
        self.content_codec = content_codec
        self._missions = selected_missions

    def __repr__(self) -> str:
        return (
            "ConversationRepository(control_database_url=<redacted>, "
            f"environment={self.environment!r}, content_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def _conversation_from_row(self, row: dict[str, Any]) -> ConversationRecord:
        summary: str | None = None
        key_version = row["summary_key_version"]
        ciphertext = row["summary_ciphertext"]
        if ciphertext is not None or key_version is not None:
            if not isinstance(key_version, int):
                raise ConversationRepositoryError()
            value = self.content_codec.unseal_json(
                _summary_subject(row["conversation_id"], key_version),
                SealedContent(bytes(ciphertext), key_version),
            )
            if set(value) != {"summary"} or not isinstance(value["summary"], str):
                raise ConversationRepositoryError()
            summary = value["summary"]
        return ConversationRecord(
            conversation_id=row["conversation_id"],
            owner_internal_user_id=row["owner_internal_user_id"],
            started_by_client_request_id=row["started_by_client_request_id"],
            mode=row["mode"],
            direct_agent_id=row["direct_agent_id"],
            title=row["title"],
            status=row["status"],
            summary_through_seq=row["summary_through_seq"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
            summary=summary,
            summary_key_version=key_version,
        )

    def _message_from_row(self, row: dict[str, Any]) -> ConversationMessageRecord:
        value = self.content_codec.unseal_json(
            message_subject(row["conversation_id"], row["message_id"]),
            SealedContent(
                bytes(row["content_ciphertext"]),
                row["encryption_key_version"],
            ),
        )
        if set(value) != {"text"} or not isinstance(value["text"], str):
            raise ConversationRepositoryError()
        return ConversationMessageRecord(
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            seq=row["seq"],
            role=row["role"],
            turn_id=row["turn_id"],
            mission_id=row["mission_id"],
            delivery_status=row["delivery_status"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            content=value["text"],
        )

    def _event_from_row(self, row: dict[str, Any]) -> ConversationEventRecord:
        value = self.content_codec.unseal_json(
            event_subject(row["conversation_id"], row["event_id"]),
            SealedContent(
                bytes(row["payload_ciphertext"]),
                row["encryption_key_version"],
            ),
        )
        if not isinstance(value, dict):
            raise ConversationRepositoryError()
        return ConversationEventRecord(
            event_id=row["event_id"],
            conversation_id=row["conversation_id"],
            seq=row["seq"],
            turn_id=row["turn_id"],
            mission_id=row["mission_id"],
            event_type=row["event_type"],
            created_at=row["created_at"],
            payload=value,
        )

    @staticmethod
    def _turn_from_row(row: dict[str, Any]) -> ConversationTurnRecord:
        return ConversationTurnRecord(
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            user_message_id=row["user_message_id"],
            assistant_message_id=row["assistant_message_id"],
            client_request_id=row["client_request_id"],
            mission_id=row["mission_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            retry_of_turn_id=row.get("retry_of_turn_id"),
        )

    def _replay_locked(
        self,
        cursor: Any,
        conversation_row: dict[str, Any],
        client_request_id: UUID,
        text: str,
        *,
        expected_mode: str | None = None,
        expected_direct_agent_id: str | None = None,
    ) -> tuple[
        ConversationRecord,
        ConversationMessageRecord,
        ConversationTurnRecord,
        UUID,
    ]:
        if expected_mode is not None and (
            conversation_row["mode"] != expected_mode
            or conversation_row["direct_agent_id"] != expected_direct_agent_id
        ):
            raise ConversationRepositoryConflict()
        turn = cursor.execute(
            "select * from platform_control.conversation_turns "
            "where conversation_id=%s and client_request_id=%s for update",
            (conversation_row["conversation_id"], client_request_id),
        ).fetchone()
        if turn is None:
            raise ConversationRepositoryError()
        message = cursor.execute(
            "select * from platform_control.conversation_messages "
            "where conversation_id=%s and message_id=%s",
            (conversation_row["conversation_id"], turn["user_message_id"]),
        ).fetchone()
        if message is None:
            raise ConversationRepositoryError()
        message_record = self._message_from_row(message)
        if message_record.content != text:
            raise ConversationRepositoryConflict()
        return (
            self._conversation_from_row(conversation_row),
            message_record,
            self._turn_from_row(turn),
            turn["mission_id"],
        )

    def _replay_v2_locked(
        self,
        cursor: Any,
        conversation_row: dict[str, Any],
        client_request_id: UUID,
        text: str,
        *,
        retry_of_turn_id: UUID | None = None,
    ) -> ConversationCreateResult:
        conversation, message, turn, mission_id = self._replay_locked(
            cursor, conversation_row, client_request_id, text
        )
        if (
            mission_id is not None
            or turn.retry_of_turn_id != retry_of_turn_id
            or cursor.execute(
                "select 1 from platform_brain.brain_loops where turn_id=%s",
                (turn.turn_id,),
            ).fetchone()
            is None
        ):
            raise ConversationRepositoryConflict()
        return ConversationCreateResult(
            conversation=conversation,
            message=message,
            turn=turn,
            mission=None,
            created=False,
        )

    def _insert_v2_loop_locked(
        self,
        cursor: Any,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        model_config: dict[str, object],
        max_steps: int,
        max_tasks: int,
        max_duration_seconds: int,
    ) -> UUID:
        loop_id = uuid4()
        step_id = uuid4()
        sealed = self.content_codec.seal_json(
            f"brain-loop:{loop_id}:model-config", model_config
        )
        cursor.execute(
            "insert into platform_brain.brain_loops ("
            "loop_id,conversation_id,turn_id,status,model_config_ciphertext,"
            "model_config_key_version,max_steps,max_tasks,max_duration_seconds,"
            "active_budget_ms) values (%s,%s,%s,'queued',%s,%s,%s,%s,%s,%s)",
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
        )
        cursor.execute(
            "insert into platform_brain.brain_steps "
            "(step_id,loop_id,step_seq,status) values (%s,%s,1,'queued')",
            (step_id, loop_id),
        )
        return loop_id

    def _new_v2_turn_locked(
        self,
        cursor: Any,
        conversation_row: dict[str, Any],
        client_request_id: UUID,
        text: str,
        *,
        message_seq: int,
        retry_of_turn_id: UUID | None,
        model_config: dict[str, object],
        max_steps: int,
        max_tasks: int,
        max_duration_seconds: int,
    ) -> ConversationCreateResult:
        conversation_id = conversation_row["conversation_id"]
        message_id = uuid4()
        turn_id = uuid4()
        sealed = self.content_codec.seal_json(
            message_subject(conversation_id, message_id), {"text": text}
        )
        message_row = cursor.execute(
            "insert into platform_control.conversation_messages "
            "(message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,mission_id,delivery_status) "
            "values (%s,%s,%s,'user',%s,%s,%s,null,'accepted') returning *",
            (
                message_id,
                conversation_id,
                message_seq,
                sealed.ciphertext,
                sealed.key_version,
                turn_id,
            ),
        ).fetchone()
        turn_row = cursor.execute(
            "insert into platform_control.conversation_turns "
            "(turn_id,conversation_id,user_message_id,client_request_id,"
            "mission_id,status,retry_of_turn_id) "
            "values (%s,%s,%s,%s,null,'accepted',%s) returning *",
            (
                turn_id,
                conversation_id,
                message_id,
                client_request_id,
                retry_of_turn_id,
            ),
        ).fetchone()
        loop_id = self._insert_v2_loop_locked(
            cursor,
            conversation_id,
            turn_id,
            model_config=model_config,
            max_steps=max_steps,
            max_tasks=max_tasks,
            max_duration_seconds=max_duration_seconds,
        )
        common_payload = {
            "turn_id": str(turn_id),
            "mission_id": None,
            "message_id": str(message_id),
            "loop_id": str(loop_id),
            "status": "accepted",
        }
        if message_seq == 1:
            self._append_event_locked(
                cursor,
                conversation_id,
                turn_id,
                None,
                "conversation.started",
                common_payload,
            )
        for event_type in ("message.accepted", "turn.accepted", "brain.started"):
            self._append_event_locked(
                cursor,
                conversation_id,
                turn_id,
                None,
                event_type,
                common_payload,
            )
        conversation_row = cursor.execute(
            "update platform_control.conversations set updated_at=now() "
            "where conversation_id=%s returning *",
            (conversation_id,),
        ).fetchone()
        return ConversationCreateResult(
            conversation=self._conversation_from_row(conversation_row),
            message=self._message_from_row(message_row),
            turn=self._turn_from_row(turn_row),
            mission=None,
            created=True,
        )

    def _new_turn_locked(
        self,
        cursor: Any,
        conversation_row: dict[str, Any],
        client_request_id: UUID,
        text: str,
        *,
        message_seq: int,
    ) -> tuple[
        ConversationRecord,
        ConversationMessageRecord,
        ConversationTurnRecord,
        MissionRecord,
    ]:
        conversation_id = conversation_row["conversation_id"]
        message_id = uuid4()
        turn_id = uuid4()
        mission_id = uuid4()
        mission_message_id = uuid4()
        mission_event_id = uuid4()
        sealed = self.content_codec.seal_json(
            message_subject(conversation_id, message_id), {"text": text}
        )
        message_row = cursor.execute(
            "insert into platform_control.conversation_messages "
            "(message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,mission_id,delivery_status) "
            "values (%s,%s,%s,'user',%s,%s,%s,%s,'accepted') returning *",
            (
                message_id,
                conversation_id,
                message_seq,
                sealed.ciphertext,
                sealed.key_version,
                turn_id,
                mission_id,
            ),
        ).fetchone()
        turn_row = cursor.execute(
            "insert into platform_control.conversation_turns "
            "(turn_id,conversation_id,user_message_id,client_request_id,"
            "mission_id,status) values (%s,%s,%s,%s,%s,'accepted') returning *",
            (
                turn_id,
                conversation_id,
                message_id,
                client_request_id,
                mission_id,
            ),
        ).fetchone()
        mission = self._missions.insert_for_conversation(
            cursor,
            mission_id=mission_id,
            mission_message_id=mission_message_id,
            started_event_id=mission_event_id,
            internal_user_id=conversation_row["owner_internal_user_id"],
            conversation_id=conversation_id,
            turn_id=turn_id,
            triggering_message_id=message_id,
            prompt=text,
            mode=conversation_row["mode"],
            direct_agent_id=conversation_row["direct_agent_id"],
        )
        common_payload = {
            "turn_id": str(turn_id),
            "mission_id": str(mission_id),
            "message_id": str(message_id),
            "status": "accepted",
        }
        if message_seq == 1:
            self._append_event_locked(
                cursor,
                conversation_id,
                turn_id,
                mission_id,
                "conversation.started",
                common_payload,
            )
        self._append_event_locked(
            cursor,
            conversation_id,
            turn_id,
            mission_id,
            "message.accepted",
            common_payload,
        )
        self._append_event_locked(
            cursor,
            conversation_id,
            turn_id,
            mission_id,
            "turn.accepted",
            common_payload,
        )
        conversation_row = cursor.execute(
            "update platform_control.conversations set updated_at=now() "
            "where conversation_id=%s returning *",
            (conversation_id,),
        ).fetchone()
        return (
            self._conversation_from_row(conversation_row),
            self._message_from_row(message_row),
            self._turn_from_row(turn_row),
            mission,
        )

    def _append_event_locked(
        self,
        cursor: Any,
        conversation_id: UUID,
        turn_id: UUID | None,
        mission_id: UUID | None,
        event_type: str,
        payload: dict[str, object],
        *,
        event_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ConversationEventRecord:
        event_id = event_id or uuid4()
        sealed = self.content_codec.seal_json(
            event_subject(conversation_id, event_id), payload
        )
        sequence = cursor.execute(
            "select coalesce(max(seq),0)+1 as next_seq from "
            "platform_control.conversation_events where conversation_id=%s",
            (conversation_id,),
        ).fetchone()["next_seq"]
        row = cursor.execute(
            "insert into platform_control.conversation_events "
            "(event_id,conversation_id,seq,turn_id,mission_id,event_type,"
            "payload_ciphertext,encryption_key_version,created_at) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s,now())) returning *",
            (
                event_id,
                conversation_id,
                sequence,
                turn_id,
                mission_id,
                event_type,
                sealed.ciphertext,
                sealed.key_version,
                created_at,
            ),
        ).fetchone()
        return self._event_from_row(row)

    def start(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        text: str,
        *,
        mode: Literal["brain", "direct_agent"] = "brain",
        direct_agent_id: str | None = None,
    ) -> ConversationCreateResult:
        _require_uuid(internal_user_id)
        _require_uuid(client_request_id)
        text = _require_text(text)
        mode, direct_agent_id = _require_mode(mode, direct_agent_id)
        conversation_id = uuid4()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("set constraints all deferred")
                existing = cursor.execute(
                    "select * from platform_control.conversations "
                    "where owner_internal_user_id=%s "
                    "and started_by_client_request_id=%s for update",
                    (internal_user_id, client_request_id),
                ).fetchone()
                if existing is not None:
                    conversation, message, turn, mission_id = self._replay_locked(
                        cursor,
                        existing,
                        client_request_id,
                        text,
                        expected_mode=mode,
                        expected_direct_agent_id=direct_agent_id,
                    )
                    created = False
                    mission = None
                else:
                    conversation_row = cursor.execute(
                        "insert into platform_control.conversations "
                        "(conversation_id,owner_internal_user_id,"
                        "started_by_client_request_id,mode,direct_agent_id,title,status) "
                        "values (%s,%s,%s,%s,%s,%s,'active') returning *",
                        (
                            conversation_id,
                            internal_user_id,
                            client_request_id,
                            mode,
                            direct_agent_id,
                            _title_for(text),
                        ),
                    ).fetchone()
                    conversation, message, turn, mission = self._new_turn_locked(
                        cursor,
                        conversation_row,
                        client_request_id,
                        text,
                        message_seq=1,
                    )
                    mission_id = mission.mission_id
                    created = True
            if mission is None:
                mission = self._missions.mission_for_owner(
                    internal_user_id, mission_id
                )
            return ConversationCreateResult(
                conversation=conversation,
                message=message,
                turn=turn,
                mission=mission,
                created=created,
            )
        except psycopg.errors.UniqueViolation:
            return self._replay_start_after_race(
                internal_user_id,
                client_request_id,
                text,
                mode=mode,
                direct_agent_id=direct_agent_id,
            )
        except ConversationRepositoryError:
            raise
        except MissionRepositoryError:
            raise ConversationRepositoryError() from None
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    def _replay_start_after_race(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        text: str,
        *,
        mode: Literal["brain", "direct_agent"],
        direct_agent_id: str | None,
    ) -> ConversationCreateResult:
        """Resolve a committed winner after the start uniqueness race."""

        try:
            with self._connection() as connection, connection.cursor() as cursor:
                conversation_row = cursor.execute(
                    "select * from platform_control.conversations "
                    "where owner_internal_user_id=%s "
                    "and started_by_client_request_id=%s for update",
                    (internal_user_id, client_request_id),
                ).fetchone()
                if conversation_row is None:
                    raise ConversationRepositoryError()
                conversation, message, turn, mission_id = self._replay_locked(
                    cursor,
                    conversation_row,
                    client_request_id,
                    text,
                    expected_mode=mode,
                    expected_direct_agent_id=direct_agent_id,
                )
            mission = self._missions.mission_for_owner(
                internal_user_id, mission_id
            )
            return ConversationCreateResult(
                conversation=conversation,
                message=message,
                turn=turn,
                mission=mission,
                created=False,
            )
        except ConversationRepositoryError:
            raise
        except MissionRepositoryError:
            raise ConversationRepositoryError() from None
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    def append_turn(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        client_request_id: UUID,
        text: str,
    ) -> ConversationCreateResult:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        _require_uuid(client_request_id)
        text = _require_text(text)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("set constraints all deferred")
                conversation_row = cursor.execute(
                    "select * from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s "
                    "for update",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if conversation_row is None:
                    raise ConversationRepositoryNotFound()
                existing = cursor.execute(
                    "select turn_id from platform_control.conversation_turns "
                    "where conversation_id=%s and client_request_id=%s",
                    (conversation_id, client_request_id),
                ).fetchone()
                if existing is not None:
                    conversation, message, turn, mission_id = self._replay_locked(
                        cursor, conversation_row, client_request_id, text
                    )
                    mission = None
                    created = False
                else:
                    if conversation_row["status"] != "active":
                        raise ConversationRepositoryConflict()
                    active = cursor.execute(
                        "select 1 from platform_control.conversation_turns "
                        "where conversation_id=%s and status in "
                        "('accepted','running','waiting_agents','waiting_user','completing') "
                        "limit 1",
                        (conversation_id,),
                    ).fetchone()
                    if active is not None:
                        raise ConversationRepositoryConflict()
                    next_seq = cursor.execute(
                        "select coalesce(max(seq),0)+1 as next_seq from "
                        "platform_control.conversation_messages "
                        "where conversation_id=%s",
                        (conversation_id,),
                    ).fetchone()["next_seq"]
                    conversation, message, turn, mission = self._new_turn_locked(
                        cursor,
                        conversation_row,
                        client_request_id,
                        text,
                        message_seq=next_seq,
                    )
                    mission_id = mission.mission_id
                    created = True
            if mission is None:
                mission = self._missions.mission_for_owner(
                    internal_user_id, mission_id
                )
            return ConversationCreateResult(
                conversation=conversation,
                message=message,
                turn=turn,
                mission=mission,
                created=created,
            )
        except ConversationRepositoryError:
            raise
        except MissionRepositoryError:
            raise ConversationRepositoryError() from None
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    @staticmethod
    def _require_v2_limits(
        model_config: object,
        max_steps: object,
        max_tasks: object,
        max_duration_seconds: object,
    ) -> dict[str, object]:
        if (
            type(model_config) is not dict
            or type(max_steps) is not int
            or not 1 <= max_steps <= 128
            or type(max_tasks) is not int
            or not 0 <= max_tasks <= 128
            or type(max_duration_seconds) is not int
            or not 1 <= max_duration_seconds <= 86400
        ):
            raise ValueError("V2 Conversation configuration invalid")
        return dict(model_config)

    def start_v2(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        text: str,
        *,
        model_config: dict[str, object],
        max_steps: int,
        max_tasks: int,
        max_duration_seconds: int,
    ) -> ConversationCreateResult:
        _require_uuid(internal_user_id)
        _require_uuid(client_request_id)
        text = _require_text(text)
        config = self._require_v2_limits(
            model_config, max_steps, max_tasks, max_duration_seconds
        )
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("set constraints all deferred")
                existing = cursor.execute(
                    "select * from platform_control.conversations "
                    "where owner_internal_user_id=%s "
                    "and started_by_client_request_id=%s for update",
                    (internal_user_id, client_request_id),
                ).fetchone()
                if existing is not None:
                    if existing["mode"] != "brain":
                        raise ConversationRepositoryConflict()
                    return self._replay_v2_locked(
                        cursor, existing, client_request_id, text
                    )
                conversation_id = uuid4()
                conversation_row = cursor.execute(
                    "insert into platform_control.conversations "
                    "(conversation_id,owner_internal_user_id,"
                    "started_by_client_request_id,mode,direct_agent_id,title,status) "
                    "values (%s,%s,%s,'brain',null,%s,'active') returning *",
                    (
                        conversation_id,
                        internal_user_id,
                        client_request_id,
                        _title_for(text),
                    ),
                ).fetchone()
                return self._new_v2_turn_locked(
                    cursor,
                    conversation_row,
                    client_request_id,
                    text,
                    message_seq=1,
                    retry_of_turn_id=None,
                    model_config=config,
                    max_steps=max_steps,
                    max_tasks=max_tasks,
                    max_duration_seconds=max_duration_seconds,
                )
        except psycopg.errors.UniqueViolation:
            return self._replay_v2_start_after_race(
                internal_user_id, client_request_id, text
            )
        except ConversationRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    def _replay_v2_start_after_race(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        text: str,
    ) -> ConversationCreateResult:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select * from platform_control.conversations "
                    "where owner_internal_user_id=%s "
                    "and started_by_client_request_id=%s for update",
                    (internal_user_id, client_request_id),
                ).fetchone()
                if row is None or row["mode"] != "brain":
                    raise ConversationRepositoryConflict()
                return self._replay_v2_locked(
                    cursor, row, client_request_id, text
                )
        except ConversationRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    def append_turn_v2(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        client_request_id: UUID,
        text: str,
        *,
        model_config: dict[str, object],
        max_steps: int,
        max_tasks: int,
        max_duration_seconds: int,
    ) -> ConversationCreateResult:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        _require_uuid(client_request_id)
        text = _require_text(text)
        config = self._require_v2_limits(
            model_config, max_steps, max_tasks, max_duration_seconds
        )
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("set constraints all deferred")
                conversation_row = cursor.execute(
                    "select * from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s "
                    "for update",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if conversation_row is None:
                    raise ConversationRepositoryNotFound()
                if conversation_row["mode"] != "brain":
                    raise ConversationRepositoryConflict()
                existing = cursor.execute(
                    "select 1 from platform_control.conversation_turns "
                    "where conversation_id=%s and client_request_id=%s",
                    (conversation_id, client_request_id),
                ).fetchone()
                if existing is not None:
                    return self._replay_v2_locked(
                        cursor, conversation_row, client_request_id, text
                    )
                if conversation_row["status"] != "active":
                    raise ConversationRepositoryConflict()
                if self._active_turn_locked(cursor, conversation_id) is not None:
                    raise ConversationTurnInProgress()
                next_seq = cursor.execute(
                    "select coalesce(max(seq),0)+1 as next_seq from "
                    "platform_control.conversation_messages "
                    "where conversation_id=%s",
                    (conversation_id,),
                ).fetchone()["next_seq"]
                return self._new_v2_turn_locked(
                    cursor,
                    conversation_row,
                    client_request_id,
                    text,
                    message_seq=next_seq,
                    retry_of_turn_id=None,
                    model_config=config,
                    max_steps=max_steps,
                    max_tasks=max_tasks,
                    max_duration_seconds=max_duration_seconds,
                )
        except ConversationRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    @staticmethod
    def _active_turn_locked(cursor: Any, conversation_id: UUID):
        return cursor.execute(
            "select * from platform_control.conversation_turns "
            "where conversation_id=%s and status in "
            "('accepted','running','waiting_agents','waiting_user','completing') "
            "limit 1",
            (conversation_id,),
        ).fetchone()

    def retry_turn_v2(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        failed_turn_id: UUID,
        client_request_id: UUID,
        *,
        model_config: dict[str, object],
        max_steps: int,
        max_tasks: int,
        max_duration_seconds: int,
    ) -> ConversationCreateResult:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        _require_uuid(failed_turn_id)
        _require_uuid(client_request_id)
        config = self._require_v2_limits(
            model_config, max_steps, max_tasks, max_duration_seconds
        )
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("set constraints all deferred")
                conversation_row = cursor.execute(
                    "select * from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s "
                    "for update",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if conversation_row is None:
                    raise ConversationRepositoryNotFound()
                if (
                    conversation_row["mode"] != "brain"
                    or conversation_row["status"] != "active"
                ):
                    raise ConversationRepositoryConflict()
                original = cursor.execute(
                    "select * from platform_control.conversation_turns "
                    "where conversation_id=%s and turn_id=%s for update",
                    (conversation_id, failed_turn_id),
                ).fetchone()
                if original is None:
                    raise ConversationRepositoryNotFound()
                existing = cursor.execute(
                    "select 1 from platform_control.conversation_turns "
                    "where conversation_id=%s and client_request_id=%s",
                    (conversation_id, client_request_id),
                ).fetchone()
                source_message = cursor.execute(
                    "select * from platform_control.conversation_messages "
                    "where conversation_id=%s and message_id=%s",
                    (conversation_id, original["user_message_id"]),
                ).fetchone()
                if source_message is None:
                    raise ConversationRepositoryError()
                text = self._message_from_row(source_message).content
                if existing is not None:
                    return self._replay_v2_locked(
                        cursor,
                        conversation_row,
                        client_request_id,
                        text,
                        retry_of_turn_id=failed_turn_id,
                    )
                if original["status"] not in (
                    "failed",
                    "cancelled",
                    "interrupted",
                ):
                    raise ConversationRepositoryConflict()
                if self._active_turn_locked(cursor, conversation_id) is not None:
                    raise ConversationTurnInProgress()
                next_seq = cursor.execute(
                    "select coalesce(max(seq),0)+1 as next_seq from "
                    "platform_control.conversation_messages "
                    "where conversation_id=%s",
                    (conversation_id,),
                ).fetchone()["next_seq"]
                return self._new_v2_turn_locked(
                    cursor,
                    conversation_row,
                    client_request_id,
                    text,
                    message_seq=next_seq,
                    retry_of_turn_id=failed_turn_id,
                    model_config=config,
                    max_steps=max_steps,
                    max_tasks=max_tasks,
                    max_duration_seconds=max_duration_seconds,
                )
        except ConversationRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    def resume_waiting_user_v2(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        client_request_id: UUID,
        text: str,
    ) -> ConversationCreateResult:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        _require_uuid(client_request_id)
        text = _require_text(text)
        try:
            with self._connection() as connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute("set constraints all deferred")
                conversation_row = cursor.execute(
                    "select * from platform_control.conversations where "
                    "conversation_id=%s and owner_internal_user_id=%s for update",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if conversation_row is None:
                    raise ConversationRepositoryNotFound()
                existing = cursor.execute(
                    "select * from platform_control.conversation_messages "
                    "where message_id=%s and conversation_id=%s",
                    (client_request_id, conversation_id),
                ).fetchone()
                if existing is not None:
                    message = self._message_from_row(existing)
                    if message.content != text or message.turn_id is None:
                        raise ConversationRepositoryConflict()
                    turn_row = cursor.execute(
                        "select * from platform_control.conversation_turns "
                        "where turn_id=%s and conversation_id=%s",
                        (message.turn_id, conversation_id),
                    ).fetchone()
                    if turn_row is None:
                        raise ConversationRepositoryError()
                    return ConversationCreateResult(
                        self._conversation_from_row(conversation_row),
                        message,
                        self._turn_from_row(turn_row),
                        None,
                        False,
                    )
                active = cursor.execute(
                    "select loop.*,turn.user_message_id from "
                    "platform_brain.brain_loops loop join "
                    "platform_control.conversation_turns turn "
                    "on turn.turn_id=loop.turn_id where loop.conversation_id=%s "
                    "and loop.status='waiting_user' and turn.status='waiting_user' "
                    "for update of loop,turn",
                    (conversation_id,),
                ).fetchone()
                if (
                    active is None
                    or active["waiting_user_expires_at"] is None
                    or active["waiting_user_expires_at"] <= datetime.now().astimezone()
                ):
                    raise ConversationTurnInProgress()
                step = cursor.execute(
                    "select * from platform_brain.brain_steps where loop_id=%s "
                    "and status='waiting_tool_results' for update",
                    (active["loop_id"],),
                ).fetchone()
                call = cursor.execute(
                    "select * from platform_brain.brain_tool_calls where step_id=%s "
                    "and tool_name='request_user_input' and result_ciphertext is null "
                    "for update",
                    (step["step_id"] if step else None,),
                ).fetchone()
                if step is None or call is None:
                    raise ConversationRepositoryConflict()
                seq = cursor.execute(
                    "select coalesce(max(seq),0)+1 as seq from "
                    "platform_control.conversation_messages where conversation_id=%s",
                    (conversation_id,),
                ).fetchone()["seq"]
                sealed_message = self.content_codec.seal_json(
                    message_subject(conversation_id, client_request_id), {"text": text}
                )
                message_row = cursor.execute(
                    "insert into platform_control.conversation_messages ("
                    "message_id,conversation_id,seq,role,content_ciphertext,"
                    "encryption_key_version,turn_id,delivery_status) "
                    "values (%s,%s,%s,'user',%s,%s,%s,'accepted') returning *",
                    (client_request_id,conversation_id,seq,sealed_message.ciphertext,
                     sealed_message.key_version,active["turn_id"]),
                ).fetchone()
                result_value = {
                    "status": "answered",
                    "user_message_id": str(client_request_id),
                    "answer": text,
                }
                result_bytes = json.dumps(
                    result_value,ensure_ascii=False,sort_keys=True,
                    separators=(",", ":"),allow_nan=False,
                ).encode("utf-8")
                sealed_result = self.content_codec.seal_json(
                    f"brain-tool-call:{call['brain_tool_call_id']}:result",
                    result_value,
                )
                cursor.execute(
                    "update platform_brain.brain_tool_calls set status='result_ready',"
                    "result_ciphertext=%s,result_key_version=%s,result_sha256=%s,"
                    "updated_at=clock_timestamp() where brain_tool_call_id=%s",
                    (sealed_result.ciphertext,sealed_result.key_version,
                     hashlib.sha256(result_bytes).digest(),call["brain_tool_call_id"]),
                )
                cursor.execute(
                    "update platform_brain.brain_steps set status='completed',"
                    "terminal_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "where step_id=%s",
                    (step["step_id"],),
                )
                cursor.execute(
                    "insert into platform_brain.brain_steps "
                    "(step_id,loop_id,step_seq,status) values (%s,%s,%s,'queued')",
                    (uuid4(),active["loop_id"],step["step_seq"]+1),
                )
                cursor.execute(
                    "update platform_brain.brain_loops set status='running',"
                    "active_started_at=clock_timestamp(),active_deadline_at="
                    "clock_timestamp()+((active_budget_ms-active_elapsed_ms)*"
                    "interval '1 millisecond'),waiting_user_expires_at=null,"
                    "updated_at=clock_timestamp(),row_version=row_version+1 "
                    "where loop_id=%s",
                    (active["loop_id"],),
                )
                turn_row = cursor.execute(
                    "update platform_control.conversation_turns set status='running',"
                    "updated_at=clock_timestamp() where turn_id=%s returning *",
                    (active["turn_id"],),
                ).fetchone()
                conversation_row = cursor.execute(
                    "update platform_control.conversations set updated_at=clock_timestamp() "
                    "where conversation_id=%s returning *",
                    (conversation_id,),
                ).fetchone()
                return ConversationCreateResult(
                    self._conversation_from_row(conversation_row),
                    self._message_from_row(message_row),
                    self._turn_from_row(turn_row),
                    None,
                    True,
                )
        except ConversationRepositoryError:
            raise
        except psycopg.errors.UniqueViolation:
            raise ConversationRepositoryConflict() from None
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def request_cancel_v2(
        self, internal_user_id: UUID, conversation_id: UUID
    ) -> ConversationTurnRecord:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select turn.* from platform_control.conversation_turns turn "
                    "join platform_control.conversations conversation "
                    "on conversation.conversation_id=turn.conversation_id "
                    "where turn.conversation_id=%s "
                    "and conversation.owner_internal_user_id=%s "
                    "and turn.status in "
                    "('accepted','running','waiting_agents','waiting_user','completing') "
                    "order by turn.created_at desc limit 1 for update of turn",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if row is None:
                    owned = cursor.execute(
                        "select 1 from platform_control.conversations "
                        "where conversation_id=%s and owner_internal_user_id=%s",
                        (conversation_id, internal_user_id),
                    ).fetchone()
                    if owned is None:
                        raise ConversationRepositoryNotFound()
                    raise ConversationRepositoryConflict()
                updated = cursor.execute(
                    "update platform_brain.brain_loops set cancel_requested=true,"
                    "updated_at=clock_timestamp(),row_version=row_version+1 "
                    "where turn_id=%s returning loop_id",
                    (row["turn_id"],),
                ).fetchone()
                if updated is None:
                    raise ConversationRepositoryConflict()
            return self._turn_from_row(row)
        except ConversationRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def conversation_for_owner(
        self, internal_user_id: UUID, conversation_id: UUID
    ) -> ConversationRecord:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select * from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s",
                    (conversation_id, internal_user_id),
                ).fetchone()
            if row is None:
                raise ConversationRepositoryNotFound()
            return self._conversation_from_row(row)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def messages_after(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> tuple[ConversationMessageRecord, ...]:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 201
        ):
            raise ValueError("Conversation message cursor invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                rows = cursor.execute(
                    "select message.* from platform_control.conversation_messages message "
                    "join platform_control.conversations conversation "
                    "on conversation.conversation_id=message.conversation_id "
                    "where message.conversation_id=%s "
                    "and conversation.owner_internal_user_id=%s and message.seq>%s "
                    "order by message.seq limit %s",
                    (conversation_id, internal_user_id, after, limit),
                ).fetchall()
                if not rows and cursor.execute(
                    "select 1 from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s",
                    (conversation_id, internal_user_id),
                ).fetchone() is None:
                    raise ConversationRepositoryNotFound()
            return tuple(self._message_from_row(row) for row in rows)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def create_feedback(
        self,
        internal_user_id: UUID,
        message_id: UUID,
        rating: Literal["helpful", "unhelpful"],
        reason: Literal["inaccurate", "incomplete", "unclear", "unresolved", "other"] | None = None,
        comment: str | None = None,
    ) -> ConversationFeedbackResult:
        _require_uuid(internal_user_id)
        _require_uuid(message_id)
        if rating not in {"helpful", "unhelpful"}:
            raise ValueError("Conversation feedback rating invalid")
        if rating == "helpful" and (reason is not None or comment is not None):
            raise ValueError("Helpful feedback cannot include detail")
        if rating == "unhelpful" and reason not in {
            "inaccurate", "incomplete", "unclear", "unresolved", "other",
        }:
            raise ValueError("Improvement feedback reason invalid")
        if comment is not None:
            comment = comment.strip() or None
            if comment is not None and len(comment.encode("utf-8")) > 1000:
                raise ValueError("Feedback comment invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                target = cursor.execute(
                    "select message.conversation_id,message.role,turn.turn_id,"
                    "turn.mission_id from platform_control.conversation_messages message "
                    "join platform_control.conversations conversation "
                    "on conversation.conversation_id=message.conversation_id "
                    "left join platform_control.conversation_turns turn "
                    "on turn.conversation_id=message.conversation_id "
                    "and turn.assistant_message_id=message.message_id "
                    "where message.message_id=%s "
                    "and conversation.owner_internal_user_id=%s for update of message",
                    (message_id, internal_user_id),
                ).fetchone()
                if target is None:
                    raise ConversationRepositoryNotFound()
                if target["role"] != "assistant" or target["turn_id"] is None:
                    raise ConversationRepositoryConflict()
                existing = cursor.execute(
                    "select * from platform_control.conversation_feedback "
                    "where owner_internal_user_id=%s and message_id=%s",
                    (internal_user_id, message_id),
                ).fetchone()
                if existing is not None:
                    existing_record = self._feedback_from_row(existing)
                    if (
                        existing_record.rating != rating
                        or existing_record.reason != reason
                        or existing_record.comment != comment
                    ):
                        raise ConversationRepositoryConflict()
                    row = existing
                    created = False
                else:
                    feedback_id = uuid4()
                    sealed_comment = (
                        self.content_codec.seal_json(
                            feedback_subject(feedback_id), {"text": comment}
                        )
                        if comment is not None
                        else None
                    )
                    row = cursor.execute(
                        "insert into platform_control.conversation_feedback "
                        "(feedback_id,owner_internal_user_id,conversation_id,"
                        "message_id,turn_id,mission_id,rating,reason,"
                        "comment_ciphertext,comment_key_version) "
                        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                        (
                            feedback_id,
                            internal_user_id,
                            target["conversation_id"],
                            message_id,
                            target["turn_id"],
                            target["mission_id"],
                            rating,
                            reason,
                            sealed_comment.ciphertext if sealed_comment else None,
                            sealed_comment.key_version if sealed_comment else None,
                        ),
                    ).fetchone()
                    created = True
            return ConversationFeedbackResult(
                feedback=self._feedback_from_row(row),
                created=created,
            )
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, UnicodeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def _feedback_from_row(self, row: dict[str, Any]) -> ConversationFeedbackRecord:
        comment = None
        if row.get("comment_ciphertext") is not None:
            document = self.content_codec.unseal_json(
                feedback_subject(row["feedback_id"]),
                SealedContent(
                    bytes(row["comment_ciphertext"]), row["comment_key_version"]
                ),
            )
            selected = document.get("text")
            if not isinstance(selected, str):
                raise ConversationRepositoryError()
            comment = selected
        return ConversationFeedbackRecord(
            feedback_id=row["feedback_id"],
            owner_internal_user_id=row["owner_internal_user_id"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            turn_id=row["turn_id"],
            mission_id=row["mission_id"],
            rating=row["rating"],
            reason=row.get("reason"),
            created_at=row["created_at"],
            comment=comment,
        )

    def conversation_metrics(self) -> ConversationMetrics:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "with per_conversation as ("
                    "select conversation.conversation_id,"
                    "count(turn.turn_id)::bigint as turn_count,"
                    "count(turn.turn_id) filter "
                    "(where turn.status='completed')::bigint as completed_count "
                    "from platform_control.conversations conversation "
                    "left join platform_control.conversation_turns turn "
                    "on turn.conversation_id=conversation.conversation_id "
                    "group by conversation.conversation_id"
                    "), conversation_totals as ("
                    "select count(*)::bigint as conversations,"
                    "count(*) filter (where turn_count >= 2)::bigint "
                    "as multi_turn_conversations,"
                    "coalesce(sum(turn_count),0)::bigint as turns,"
                    "coalesce(sum(completed_count),0)::bigint as completed_turns "
                    "from per_conversation"
                    "), mission_totals as ("
                    "select count(*)::bigint as missions from platform_control.missions"
                    "), quality_totals as ("
                    "select count(distinct mission_id)::bigint as rated_missions,"
                    "count(distinct mission_id) filter (where rating='helpful')::bigint "
                    "as helpful_missions from platform_control.conversation_feedback "
                    "where mission_id is not null"
                    ") select * from conversation_totals cross join mission_totals "
                    "cross join quality_totals"
                ).fetchone()
            conversations = int(row["conversations"])
            multi_turn = int(row["multi_turn_conversations"])
            turns = int(row["turns"])
            completed = int(row["completed_turns"])
            rated = int(row["rated_missions"])
            helpful = int(row["helpful_missions"])
            return ConversationMetrics(
                conversations=conversations,
                multi_turn_conversations=multi_turn,
                multi_turn_rate=(multi_turn / conversations if conversations else 0.0),
                turns=turns,
                completed_turns=completed,
                turn_completion_rate=(completed / turns if turns else 0.0),
                missions=int(row["missions"]),
                rated_missions=rated,
                helpful_missions=helpful,
                mission_quality_rate=(helpful / rated if rated else None),
            )
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def store_summary(
        self,
        conversation_id: UUID,
        current_turn_id: UUID,
        through_seq: int,
        summary: str,
    ) -> ConversationRecord:
        _require_uuid(conversation_id)
        _require_uuid(current_turn_id)
        if (
            isinstance(through_seq, bool)
            or not isinstance(through_seq, int)
            or through_seq <= 0
            or not isinstance(summary, str)
            or not summary.strip()
        ):
            raise ValueError("Conversation summary invalid")
        try:
            if len(summary.encode("utf-8")) > 32 * 1024:
                raise ValueError("Conversation summary invalid")
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select conversation.*,message.seq as current_user_seq "
                    "from platform_control.conversations conversation "
                    "join platform_control.conversation_turns turn "
                    "on turn.conversation_id=conversation.conversation_id "
                    "and turn.turn_id=%s "
                    "join platform_control.conversation_messages message "
                    "on message.conversation_id=turn.conversation_id "
                    "and message.message_id=turn.user_message_id "
                    "where conversation.conversation_id=%s for update of conversation",
                    (current_turn_id, conversation_id),
                ).fetchone()
                if row is None:
                    raise ConversationRepositoryNotFound()
                selected = cursor.execute(
                    "select max(message.seq) as through_seq from "
                    "platform_control.conversation_messages message "
                    "join platform_control.conversation_turns turn "
                    "on turn.conversation_id=message.conversation_id "
                    "and turn.assistant_message_id=message.message_id "
                    "where message.conversation_id=%s and message.role='assistant' "
                    "and message.delivery_status='completed' "
                    "and message.seq<%s and turn.status='completed'",
                    (conversation_id, row["current_user_seq"]),
                ).fetchone()["through_seq"]
                if selected != through_seq:
                    raise ConversationRepositoryConflict()
                existing = self._conversation_from_row(row)
                if existing.summary_through_seq == through_seq:
                    if existing.summary != summary:
                        raise ConversationRepositoryConflict()
                    return existing
                if existing.summary_through_seq >= through_seq:
                    raise ConversationRepositoryConflict()
                key_version = self.content_codec.active_key_version
                sealed = self.content_codec.seal_json(
                    _summary_subject(conversation_id, key_version),
                    {"summary": summary},
                )
                updated = cursor.execute(
                    "update platform_control.conversations set "
                    "summary_ciphertext=%s,summary_key_version=%s,"
                    "summary_through_seq=%s,updated_at=now() "
                    "where conversation_id=%s returning *",
                    (
                        sealed.ciphertext,
                        sealed.key_version,
                        through_seq,
                        conversation_id,
                    ),
                ).fetchone()
            return self._conversation_from_row(updated)
        except ConversationRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
            psycopg.Error,
        ):
            raise ConversationRepositoryError() from None

    def list_feedback(
        self, limit: int = 100, offset: int = 0
    ) -> tuple[tuple[ConversationFeedbackRecord, ...], int]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise ValueError("Conversation feedback page invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                rows = cursor.execute(
                    "select * from platform_control.conversation_feedback "
                    "order by created_at desc,feedback_id limit %s offset %s",
                    (limit, offset),
                ).fetchall()
                total = cursor.execute(
                    "select count(*)::bigint as total from "
                    "platform_control.conversation_feedback"
                ).fetchone()["total"]
            return (
                tuple(self._feedback_from_row(row) for row in rows),
                int(total),
            )
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def events_after(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        *,
        after: int = 0,
        limit: int = 500,
    ) -> tuple[ConversationEventRecord, ...]:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
            raise ValueError("Conversation event cursor invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                rows = cursor.execute(
                    "select event.* from platform_control.conversation_events event "
                    "join platform_control.conversations conversation "
                    "on conversation.conversation_id=event.conversation_id "
                    "where event.conversation_id=%s "
                    "and conversation.owner_internal_user_id=%s and event.seq>%s "
                    "order by event.seq limit %s",
                    (conversation_id, internal_user_id, after, limit),
                ).fetchall()
                if not rows and cursor.execute(
                    "select 1 from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s",
                    (conversation_id, internal_user_id),
                ).fetchone() is None:
                    raise ConversationRepositoryNotFound()
            return tuple(self._event_from_row(row) for row in rows)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def sync_mission_events(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        *,
        limit: int = 100,
    ) -> int:
        """Persist safe Mission execution events in the Conversation stream."""

        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("Mission event projection limit invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                owned = cursor.execute(
                    "select conversation_id from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s "
                    "for update",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if owned is None:
                    raise ConversationRepositoryNotFound()
                rows = cursor.execute(
                    "select event.event_id,event.mission_id,event.event_type,"
                    "event.payload_ciphertext,event.encryption_key_version,"
                    "event.created_at,turn.turn_id "
                    "from platform_control.mission_events event "
                    "join platform_control.missions mission "
                    "on mission.mission_id=event.mission_id "
                    "join platform_control.conversation_turns turn "
                    "on turn.conversation_id=mission.conversation_id "
                    "and turn.turn_id=mission.turn_id "
                    "where mission.conversation_id=%s "
                    "and mission.owner_internal_user_id=%s "
                    "and event.event_type=any(%s) "
                    "and not exists (select 1 from "
                    "platform_control.conversation_events projected "
                    "where projected.event_id=event.event_id) "
                    "order by turn.created_at,event.seq,event.event_id limit %s",
                    (
                        conversation_id,
                        internal_user_id,
                        list(_PROJECTED_MISSION_EVENT_TYPES),
                        limit,
                    ),
                ).fetchall()
                for row in rows:
                    payload = self.content_codec.unseal_json(
                        _mission_event_subject(
                            row["mission_id"], row["event_id"]
                        ),
                        SealedContent(
                            bytes(row["payload_ciphertext"]),
                            row["encryption_key_version"],
                        ),
                    )
                    if not isinstance(payload, dict):
                        raise ConversationRepositoryError()
                    self._append_event_locked(
                        cursor,
                        conversation_id,
                        row["turn_id"],
                        row["mission_id"],
                        row["event_type"],
                        payload,
                        event_id=row["event_id"],
                        created_at=row["created_at"],
                    )
            return len(rows)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def latest_turn_for_owner(
        self, internal_user_id: UUID, conversation_id: UUID
    ) -> ConversationTurnRecord | None:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                owned = cursor.execute(
                    "select 1 from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if owned is None:
                    raise ConversationRepositoryNotFound()
                row = cursor.execute(
                    "select * from platform_control.conversation_turns "
                    "where conversation_id=%s order by created_at desc,turn_id desc "
                    "limit 1",
                    (conversation_id,),
                ).fetchone()
            return self._turn_from_row(row) if row is not None else None
        except ConversationRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def active_turn_for_owner(
        self, internal_user_id: UUID, conversation_id: UUID
    ) -> ConversationTurnRecord | None:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                owned = cursor.execute(
                    "select 1 from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if owned is None:
                    raise ConversationRepositoryNotFound()
                row = cursor.execute(
                    "select * from platform_control.conversation_turns "
                    "where conversation_id=%s and status in "
                    "('accepted','running','waiting_agents','waiting_user','completing') "
                    "limit 1",
                    (conversation_id,),
                ).fetchone()
            return self._turn_from_row(row) if row is not None else None
        except ConversationRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def replay_message_for_owner(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        client_request_id: UUID,
        text: str,
    ) -> ConversationCreateResult | None:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        _require_uuid(client_request_id)
        text = _require_text(text)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select message.* from platform_control.conversation_messages "
                    "message join platform_control.conversations conversation on "
                    "conversation.conversation_id=message.conversation_id where "
                    "message.message_id=%s and message.conversation_id=%s and "
                    "conversation.owner_internal_user_id=%s",
                    (client_request_id, conversation_id, internal_user_id),
                ).fetchone()
                if row is None:
                    return None
                message = self._message_from_row(row)
                if message.content != text or message.turn_id is None:
                    raise ConversationRepositoryConflict()
                turn = connection.execute(
                    "select * from platform_control.conversation_turns where turn_id=%s",
                    (message.turn_id,),
                ).fetchone()
                conversation = connection.execute(
                    "select * from platform_control.conversations where conversation_id=%s",
                    (conversation_id,),
                ).fetchone()
            if turn is None or conversation is None:
                raise ConversationRepositoryError()
            return ConversationCreateResult(
                self._conversation_from_row(conversation),
                message,
                self._turn_from_row(turn),
                None,
                False,
            )
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def append_brain_intervention(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        client_request_id: UUID,
        text: str,
    ) -> ConversationInterventionResult:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        _require_uuid(client_request_id)
        text = _require_text(text)
        try:
            with self._connection() as connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute("set constraints all deferred")
                conversation = cursor.execute(
                    "select * from platform_control.conversations where "
                    "conversation_id=%s and owner_internal_user_id=%s and mode='brain' "
                    "and status='active' for update",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if conversation is None:
                    raise ConversationRepositoryNotFound()
                existing = cursor.execute(
                    "select message.*,intervention.status as intervention_status "
                    "from platform_control.conversation_messages message left join "
                    "platform_brain.brain_user_interventions intervention on "
                    "intervention.message_id=message.message_id where "
                    "message.message_id=%s and message.conversation_id=%s",
                    (client_request_id, conversation_id),
                ).fetchone()
                if existing is not None:
                    message = self._message_from_row(existing)
                    if message.content != text or existing["intervention_status"] is None:
                        raise ConversationRepositoryConflict()
                    turn = cursor.execute(
                        "select * from platform_control.conversation_turns "
                        "where turn_id=%s",
                        (message.turn_id,),
                    ).fetchone()
                    return ConversationInterventionResult(
                        message,
                        self._turn_from_row(turn),
                        existing["intervention_status"],
                        False,
                    )
                active = cursor.execute(
                    "select loop.*,turn.status as turn_status from "
                    "platform_brain.brain_loops loop join "
                    "platform_control.conversation_turns turn on "
                    "turn.turn_id=loop.turn_id where loop.conversation_id=%s and "
                    "loop.status in ('queued','running','waiting_agents') and "
                    "turn.status in ('accepted','running','waiting_agents') "
                    "order by loop.created_at desc limit 1",
                    (conversation_id,),
                ).fetchone()
                if active is None:
                    raise ConversationTurnInProgress()
                seq = cursor.execute(
                    "select coalesce(max(seq),0)+1 as seq from "
                    "platform_control.conversation_messages where conversation_id=%s",
                    (conversation_id,),
                ).fetchone()["seq"]
                sealed_message = self.content_codec.seal_json(
                    message_subject(conversation_id, client_request_id), {"text": text}
                )
                message_row = cursor.execute(
                    "insert into platform_control.conversation_messages ("
                    "message_id,conversation_id,seq,role,content_ciphertext,"
                    "encryption_key_version,turn_id,delivery_status) values "
                    "(%s,%s,%s,'user',%s,%s,%s,'accepted') returning *",
                    (
                        client_request_id,
                        conversation_id,
                        seq,
                        sealed_message.ciphertext,
                        sealed_message.key_version,
                        active["turn_id"],
                    ),
                ).fetchone()
                intervention_id = uuid4()
                sealed_intervention = self.content_codec.seal_json(
                    f"brain-intervention:{intervention_id}", {"text": text}
                )
                cursor.execute(
                    "insert into platform_brain.brain_user_interventions ("
                    "intervention_id,loop_id,message_id,content_ciphertext,"
                    "content_key_version,content_sha256,status) values "
                    "(%s,%s,%s,%s,%s,%s,'pending')",
                    (
                        intervention_id,
                        active["loop_id"],
                        client_request_id,
                        sealed_intervention.ciphertext,
                        sealed_intervention.key_version,
                        hashlib.sha256(text.encode("utf-8")).digest(),
                    ),
                )
                self._append_event_locked(
                    cursor,
                    conversation_id,
                    active["turn_id"],
                    None,
                    "brain.user_intervention",
                    {"status": "pending", "summary": text[:512]},
                )
                if active["status"] == "waiting_agents":
                    wait = cursor.execute(
                        "select brain_tool_call_id from "
                        "platform_brain.brain_wait_subscriptions where loop_id=%s "
                        "and status='active'",
                        (active["loop_id"],),
                    ).fetchone()
                    if wait is None:
                        raise ConversationRepositoryConflict()
                    result_value = {
                        "status": "user_intervention",
                        "user_message_id": str(client_request_id),
                        "message": text,
                    }
                    result_bytes = json.dumps(
                        result_value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    sealed_result = self.content_codec.seal_json(
                        f"brain-tool-call:{wait['brain_tool_call_id']}:result",
                        result_value,
                    )
                    cursor.execute(
                        "select platform_brain.wake_loop_for_user_intervention_v46("
                        "%s,%s,%s,%s,%s,%s)",
                        (
                            active["loop_id"],
                            wait["brain_tool_call_id"],
                            sealed_result.ciphertext,
                            sealed_result.key_version,
                            hashlib.sha256(result_bytes).digest(),
                            uuid4(),
                        ),
                    )
                cursor.execute(
                    "update platform_control.conversations set "
                    "updated_at=clock_timestamp() where conversation_id=%s",
                    (conversation_id,),
                )
                turn_row = cursor.execute(
                    "select * from platform_control.conversation_turns where turn_id=%s",
                    (active["turn_id"],),
                ).fetchone()
                return ConversationInterventionResult(
                    self._message_from_row(message_row),
                    self._turn_from_row(turn_row),
                    "pending",
                    True,
                )
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def task_detail_for_owner(
        self,
        internal_user_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        task_id: UUID,
    ) -> dict[str, object]:
        for value in (internal_user_id, conversation_id, turn_id, task_id):
            _require_uuid(value)
        try:
            with self._connection() as connection:
                task = connection.execute(
                    "select task.*,session.child_session_id,session.status as "
                    "session_status from platform_brain.agent_tasks task join "
                    "platform_brain.brain_loops loop on loop.loop_id=task.loop_id join "
                    "platform_control.conversations conversation on "
                    "conversation.conversation_id=loop.conversation_id left join "
                    "platform_brain.agent_task_sessions session on "
                    "session.task_id=task.task_id where task.task_id=%s and "
                    "loop.conversation_id=%s and loop.turn_id=%s and "
                    "conversation.owner_internal_user_id=%s",
                    (task_id, conversation_id, turn_id, internal_user_id),
                ).fetchone()
                if task is None:
                    raise ConversationRepositoryNotFound()
                messages = []
                for row in connection.execute(
                    "select * from platform_brain.agent_task_messages "
                    "where task_id=%s order by seq",
                    (task_id,),
                ):
                    value = self.content_codec.unseal_json(
                        f"brain-task:{task_id}:message:{row['seq']}",
                        SealedContent(
                            bytes(row["content_ciphertext"]),
                            row["content_key_version"],
                        ),
                    )
                    messages.append(
                        {
                            "seq": row["seq"],
                            "sender": row["sender"],
                            "kind": row["message_kind"],
                            "text": value.get("text", ""),
                            "created_at": row["created_at"].isoformat(),
                        }
                    )
                events = []
                for row in connection.execute(
                    "select * from platform_brain.agent_task_events "
                    "where task_id=%s order by seq",
                    (task_id,),
                ):
                    value = self.content_codec.unseal_json(
                        f"brain-task:{task_id}:event:{row['seq']}:payload",
                        SealedContent(
                            bytes(row["payload_ciphertext"]),
                            row["payload_key_version"],
                        ),
                    )
                    events.append(
                        {
                            "seq": row["seq"],
                            "kind": row["event_type"],
                            "source": value.get("source", "adapter"),
                            "source_ref": value.get("source_ref", f"event:{row['seq']}"),
                            "summary": value.get("summary") or value.get("text") or "",
                            "status": value.get("status"),
                            "evidence_refs": value.get("evidence_refs", []),
                            "artifact_refs": value.get("artifact_refs", []),
                            "created_at": row["created_at"].isoformat(),
                        }
                    )
            return {
                "task_id": str(task_id),
                "child_session_id": task["child_session_id"],
                "agent_id": task["agent_id"],
                "status": task["status"],
                "session_status": task["session_status"],
                "messages": messages,
                "events": events,
            }
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def list_for_owner(
        self,
        internal_user_id: UUID,
        *,
        limit: int = 50,
        before: tuple[datetime, UUID] | None = None,
        direct_agent_id: str | None = None,
        status: Literal["active", "archived"] = "active",
    ) -> tuple[ConversationRecord, ...]:
        _require_uuid(internal_user_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 101
        ):
            raise ValueError("Conversation list limit invalid")
        if before is not None and (
            not isinstance(before, tuple)
            or len(before) != 2
            or not isinstance(before[0], datetime)
            or before[0].tzinfo is None
            or not isinstance(before[1], UUID)
        ):
            raise ValueError("Conversation list cursor invalid")
        if direct_agent_id is not None:
            if (
                not isinstance(direct_agent_id, str)
                or _AGENT_ID.fullmatch(direct_agent_id) is None
            ):
                raise ValueError("direct Agent invalid")
        if status not in {"active", "archived"}:
            raise ValueError("Conversation status invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                query = (
                    "select * from platform_control.conversations "
                    "where owner_internal_user_id=%s and status=%s "
                )
                base_parameters: tuple[object, ...] = (internal_user_id, status)
                if direct_agent_id is not None:
                    query += "and mode='direct_agent' and direct_agent_id=%s "
                    base_parameters += (direct_agent_id,)
                if before is None:
                    parameters = (*base_parameters, limit)
                else:
                    query += "and (updated_at,conversation_id)<(%s,%s) "
                    parameters = (
                        *base_parameters,
                        before[0],
                        before[1],
                        limit,
                    )
                rows = cursor.execute(
                    query + "order by updated_at desc,conversation_id desc limit %s",
                    parameters,
                ).fetchall()
            return tuple(self._conversation_from_row(row) for row in rows)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def rename(
        self, internal_user_id: UUID, conversation_id: UUID, title: str
    ) -> ConversationRecord:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        selected_title = _require_title(title)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "update platform_control.conversations set title=%s,"
                    "updated_at=now() where conversation_id=%s "
                    "and owner_internal_user_id=%s returning *",
                    (selected_title, conversation_id, internal_user_id),
                ).fetchone()
            if row is None:
                raise ConversationRepositoryNotFound()
            return self._conversation_from_row(row)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def archive(
        self, internal_user_id: UUID, conversation_id: UUID
    ) -> ConversationRecord:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select * from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s "
                    "for update",
                    (conversation_id, internal_user_id),
                ).fetchone()
                if row is None:
                    raise ConversationRepositoryNotFound()
                if row["status"] == "archived":
                    return self._conversation_from_row(row)
                active = cursor.execute(
                    "select 1 from platform_control.conversation_turns "
                    "where conversation_id=%s and status in "
                    "('accepted','running','waiting_agents','waiting_user','completing') "
                    "limit 1",
                    (conversation_id,),
                ).fetchone()
                if active is not None:
                    raise ConversationRepositoryConflict()
                row = cursor.execute(
                    "update platform_control.conversations set status='archived',"
                    "archived_at=now(),updated_at=now() where conversation_id=%s "
                    "and owner_internal_user_id=%s "
                    "returning *",
                    (conversation_id, internal_user_id),
                ).fetchone()
            return self._conversation_from_row(row)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def restore(
        self, internal_user_id: UUID, conversation_id: UUID
    ) -> ConversationRecord:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "update platform_control.conversations set status='active',"
                    "archived_at=null,updated_at=now() where conversation_id=%s "
                    "and owner_internal_user_id=%s "
                    "and title<>'[内容已按保留策略清除]' returning *",
                    (conversation_id, internal_user_id),
                ).fetchone()
            if row is None:
                raise ConversationRepositoryNotFound()
            return self._conversation_from_row(row)
        except ConversationRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def request_cancel(
        self, internal_user_id: UUID, conversation_id: UUID
    ) -> MissionRecord:
        _require_uuid(internal_user_id)
        _require_uuid(conversation_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select turn.mission_id from platform_control.conversation_turns turn "
                    "join platform_control.conversations conversation "
                    "on conversation.conversation_id=turn.conversation_id "
                    "where turn.conversation_id=%s "
                    "and conversation.owner_internal_user_id=%s "
                    "and turn.status in "
                    "('accepted','running','waiting_agents','waiting_user','completing') "
                    "order by turn.created_at desc limit 1",
                    (conversation_id, internal_user_id),
                ).fetchone()
            if row is None or row["mission_id"] is None:
                raise ConversationRepositoryConflict()
            return self._missions.request_cancel(internal_user_id, row["mission_id"])
        except ConversationRepositoryError:
            raise
        except MissionRepositoryError:
            raise ConversationRepositoryError() from None
        except psycopg.Error:
            raise ConversationRepositoryError() from None
