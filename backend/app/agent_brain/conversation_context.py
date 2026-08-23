from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import psycopg

from app.agent_brain.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryError,
    ConversationRepositoryNotFound,
)
from app.execution_relay.content_crypto import ContentCryptoError


MAX_CONTEXT_BYTES = 96 * 1024


class ConversationContextError(ConversationRepositoryError):
    def __init__(self, message: str = "conversation context unavailable") -> None:
        super().__init__(message)


class ConversationContextTooLarge(ConversationContextError):
    def __init__(self) -> None:
        super().__init__("conversation context exceeds limit")


@dataclass(frozen=True)
class ContextMessage:
    role: Literal["user", "assistant", "system"]
    content: str


@dataclass(frozen=True)
class ConversationContext:
    summary: str | None
    messages: tuple[ContextMessage, ...]
    estimated_utf8_bytes: int


def _context_size(summary: str | None, messages: tuple[ContextMessage, ...]) -> int:
    total = 0
    if summary is not None:
        total += len(summary.encode("utf-8")) + len("summary") + 16
    for message in messages:
        total += len(message.content.encode("utf-8")) + len(message.role) + 16
    return total


class ConversationContextBuilder:
    def __init__(self, repository: ConversationRepository) -> None:
        if not isinstance(repository, ConversationRepository):
            raise ValueError("Conversation repository required")
        self.repository = repository

    def build(self, conversation_id: UUID, turn_id: UUID) -> ConversationContext:
        if not isinstance(conversation_id, UUID) or not isinstance(turn_id, UUID):
            raise ValueError("Conversation context identifiers invalid")
        try:
            with self.repository._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select conversation.*,turn.user_message_id,message.seq as user_seq "
                    "from platform_control.conversations conversation "
                    "join platform_control.conversation_turns turn "
                    "on turn.conversation_id=conversation.conversation_id "
                    "join platform_control.conversation_messages message "
                    "on message.conversation_id=turn.conversation_id "
                    "and message.message_id=turn.user_message_id "
                    "where conversation.conversation_id=%s and turn.turn_id=%s",
                    (conversation_id, turn_id),
                ).fetchone()
                if row is None:
                    raise ConversationRepositoryNotFound()
                conversation = self.repository._conversation_from_row(row)
                if conversation.summary_through_seq > row["user_seq"]:
                    raise ConversationContextError()
                message_rows = cursor.execute(
                    "select * from platform_control.conversation_messages "
                    "where conversation_id=%s and seq>%s and seq<=%s "
                    "order by seq",
                    (
                        conversation_id,
                        conversation.summary_through_seq,
                        row["user_seq"],
                    ),
                ).fetchall()
            records = tuple(
                self.repository._message_from_row(message_row)
                for message_row in message_rows
            )
            if (
                not records
                or records[-1].message_id != row["user_message_id"]
                or records[-1].role != "user"
            ):
                raise ConversationContextError()
            messages = tuple(
                ContextMessage(role=record.role, content=record.content)
                for record in records
            )
            estimated = _context_size(conversation.summary, messages)
            if estimated > MAX_CONTEXT_BYTES:
                raise ConversationContextTooLarge()
            return ConversationContext(
                summary=conversation.summary,
                messages=messages,
                estimated_utf8_bytes=estimated,
            )
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
            raise ConversationContextError() from None
