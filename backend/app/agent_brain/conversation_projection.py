from __future__ import annotations

from uuid import UUID, uuid4

import psycopg

from app.agent_brain.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryError,
    event_subject,
    message_subject,
)
from app.agent_brain.repository import MissionRepositoryError
from app.execution_relay.content_crypto import ContentCryptoError


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
