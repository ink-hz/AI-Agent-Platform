from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import re
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_models import (
    ConversationCreateResult,
    ConversationEventRecord,
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
class ConversationRepositoryError(RuntimeError):
    """Stable persistence failure that never exposes SQL or protected content."""

    def __init__(self, message: str = "conversation repository unavailable") -> None:
        super().__init__(message)


class ConversationRepositoryConflict(ConversationRepositoryError):
    def __init__(self) -> None:
        super().__init__("conversation repository conflict")


class ConversationRepositoryNotFound(ConversationRepositoryError):
    def __init__(self) -> None:
        super().__init__("conversation not found")


def message_subject(conversation_id: UUID, message_id: UUID) -> str:
    return f"conversation:{conversation_id}:message:{message_id}:content"


def event_subject(conversation_id: UUID, event_id: UUID) -> str:
    return f"conversation:{conversation_id}:event:{event_id}:payload"


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
        if turn is None or turn["mission_id"] is None:
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
    ) -> ConversationEventRecord:
        event_id = uuid4()
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
            "payload_ciphertext,encryption_key_version) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s) returning *",
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
                        "where conversation_id=%s and status in ('accepted','running') "
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
                    "where conversation_id=%s and status in ('accepted','running') "
                    "limit 1",
                    (conversation_id,),
                ).fetchone()
            return self._turn_from_row(row) if row is not None else None
        except ConversationRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRepositoryError() from None

    def list_for_owner(
        self,
        internal_user_id: UUID,
        *,
        limit: int = 50,
        before: tuple[datetime, UUID] | None = None,
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
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                query = (
                    "select * from platform_control.conversations "
                    "where owner_internal_user_id=%s "
                )
                if before is None:
                    parameters: tuple[object, ...] = (internal_user_id, limit)
                else:
                    query += "and (updated_at,conversation_id)<(%s,%s) "
                    parameters = (
                        internal_user_id,
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
                    "where conversation_id=%s and status in ('accepted','running') "
                    "limit 1",
                    (conversation_id,),
                ).fetchone()
                if active is not None:
                    raise ConversationRepositoryConflict()
                row = cursor.execute(
                    "update platform_control.conversations set status='archived',"
                    "archived_at=now(),updated_at=now() where conversation_id=%s "
                    "returning *",
                    (conversation_id,),
                ).fetchone()
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
                    "and turn.status in ('accepted','running') "
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
