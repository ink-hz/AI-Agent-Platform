from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg

from app.agent_brain.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryError,
    ConversationRepositoryNotFound,
)
from app.execution_relay.content_crypto import ContentCryptoError
from app.hr.position_intelligence_models import HrPositionContextEnvelope
from app.hr.task_context import HrTaskContextError, canonical_hash

MAX_CONTEXT_BYTES = 96 * 1024
COMPACTION_TRIGGER_BYTES = 64 * 1024


class ConversationContextError(ConversationRepositoryError):
    def __init__(self, message: str = "conversation context unavailable") -> None:
        super().__init__(message)


class ConversationContextTooLarge(ConversationContextError):
    def __init__(self) -> None:
        super().__init__("conversation context exceeds limit")


class ConversationSummaryProtocolError(ConversationContextError):
    def __init__(self) -> None:
        super().__init__("conversation summary protocol invalid")


@dataclass(frozen=True)
class ContextMessage:
    role: Literal["user", "assistant", "system"]
    content: str


@dataclass(frozen=True)
class ConversationContext:
    summary: str | None
    messages: tuple[ContextMessage, ...]
    estimated_utf8_bytes: int
    active_attachment_ids: tuple[UUID, ...] = ()
    hr_position_context: HrPositionContextEnvelope | None = None


@dataclass(frozen=True)
class ConversationCompactionCandidate:
    previous_summary: str | None
    messages: tuple[ContextMessage, ...]
    through_seq: int


@dataclass(frozen=True)
class ConversationSummaryResult:
    summary: str
    through_seq: int


def parse_summary_result(
    value: str, *, expected_through_seq: int
) -> ConversationSummaryResult:
    if (
        not isinstance(value, str)
        or isinstance(expected_through_seq, bool)
        or not isinstance(expected_through_seq, int)
        or expected_through_seq <= 0
    ):
        raise ConversationSummaryProtocolError()
    try:
        document = json.loads(value)
        if (
            type(document) is not dict
            or set(document) != {"summary", "through_seq"}
            or type(document["summary"]) is not str
            or not document["summary"].strip()
            or len(document["summary"].encode("utf-8")) > 32 * 1024
            or type(document["through_seq"]) is not int
            or document["through_seq"] != expected_through_seq
        ):
            raise ValueError
        return ConversationSummaryResult(
            summary=document["summary"],
            through_seq=document["through_seq"],
        )
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ConversationSummaryProtocolError() from None


def _context_size(
    summary: str | None,
    messages: tuple[ContextMessage, ...],
    hr_position_context: HrPositionContextEnvelope | None = None,
) -> int:
    total = 0
    if summary is not None:
        total += len(summary.encode("utf-8")) + len("summary") + 16
    for message in messages:
        total += len(message.content.encode("utf-8")) + len(message.role) + 16
    if hr_position_context is not None:
        total += len(hr_position_context.prompt_context.encode("utf-8")) + 512
    return total


class ConversationContextBuilder:
    def __init__(
        self,
        repository: ConversationRepository,
        *,
        hr_task_context_provider: object | None = None,
        candidate_parser_input_provider: object | None = None,
    ) -> None:
        if not isinstance(repository, ConversationRepository):
            raise ValueError("Conversation repository required")
        if hr_task_context_provider is not None and not callable(
            getattr(hr_task_context_provider, "build_for_turn", None)
        ):
            raise ValueError("HR task context provider invalid")
        if candidate_parser_input_provider is not None and not callable(
            getattr(candidate_parser_input_provider, "for_turn", None)
        ):
            raise ValueError("candidate parser input provider invalid")
        self.repository = repository
        self._hr_task_context_provider = hr_task_context_provider
        self._candidate_parser_input_provider = candidate_parser_input_provider

    def _load(
        self, conversation_id: UUID, turn_id: UUID
    ) -> tuple[ConversationContext, int, tuple[tuple[int, ContextMessage], ...]]:
        if not isinstance(conversation_id, UUID) or not isinstance(turn_id, UUID):
            raise ValueError("Conversation context identifiers invalid")
        with self.repository._connection() as connection, connection.cursor() as cursor:
            row = cursor.execute(
                "select conversation.*,turn.user_message_id,message.seq as user_seq,"
                "exists(select 1 from platform_hr.position_conversations binding "
                "where binding.conversation_id=conversation.conversation_id "
                "and binding.owner_internal_user_id=conversation.owner_internal_user_id) "
                "as verified_hr_position "
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
            active_rows = cursor.execute(
                "select attachment.attachment_id,attachment.state,"
                "attachment.retained_until,attachment.immutable_locator,"
                "exists(select 1 from platform_attachments.erasure_jobs erasure "
                "where erasure.attachment_id=attachment.attachment_id) "
                "as erasure_pending from platform_attachments.bindings binding "
                "join platform_attachments.attachments attachment using (attachment_id) "
                "where binding.conversation_id=%s and binding.turn_id=%s "
                "and binding.kind='turn_input' order by attachment.attachment_id",
                (conversation_id, turn_id),
            ).fetchall()
            if any(
                item["state"] != "ready"
                or item["retained_until"] <= datetime.now().astimezone()
                or item["immutable_locator"] is None
                or item["erasure_pending"]
                for item in active_rows
            ):
                raise ConversationContextError()
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
        sequenced = tuple(
            (record.seq, ContextMessage(role=record.role, content=record.content))
            for record in records
        )
        messages = tuple(message for _seq, message in sequenced)
        hr_position_context = None
        is_hr_agent = (
            row["mode"] == "direct_agent"
            and row["direct_agent_id"] == "hr-bot"
        )
        is_hr_position = is_hr_agent and row["verified_hr_position"] is True
        if is_hr_position:
            if self._hr_task_context_provider is None:
                raise ConversationContextError()
            try:
                hr_position_context = (
                    self._hr_task_context_provider.build_for_turn(
                        row["owner_internal_user_id"], conversation_id, turn_id
                    )
                )
                if (
                    not isinstance(hr_position_context, HrPositionContextEnvelope)
                    or canonical_hash(hr_position_context)
                    != hr_position_context.canonical_sha256
                ):
                    raise ValueError
            except (HrTaskContextError, ValueError):
                raise ConversationContextError() from None
        candidate_parser_attachment_id = None
        if (
            is_hr_agent
            and not is_hr_position
            and self._candidate_parser_input_provider is not None
        ):
            try:
                candidate_parser_attachment_id = (
                    self._candidate_parser_input_provider.for_turn(
                        row["owner_internal_user_id"], conversation_id, turn_id
                    )
                )
                if candidate_parser_attachment_id is not None and not isinstance(
                    candidate_parser_attachment_id, UUID
                ):
                    raise ValueError
                if candidate_parser_attachment_id is not None and any(
                    item["attachment_id"] != candidate_parser_attachment_id
                    for item in active_rows
                ):
                    raise ValueError
            except ValueError:
                raise ConversationContextError() from None
        active_attachment_ids = [item["attachment_id"] for item in active_rows]
        if candidate_parser_attachment_id is not None:
            active_attachment_ids.append(candidate_parser_attachment_id)
        return (
            ConversationContext(
                summary=conversation.summary,
                messages=messages,
                estimated_utf8_bytes=_context_size(
                    conversation.summary, messages, hr_position_context
                ),
                active_attachment_ids=tuple(dict.fromkeys(active_attachment_ids)),
                hr_position_context=hr_position_context,
            ),
            row["user_seq"],
            sequenced,
        )

    def build(self, conversation_id: UUID, turn_id: UUID) -> ConversationContext:
        try:
            context, _user_seq, _sequenced = self._load(
                conversation_id, turn_id
            )
            estimated = context.estimated_utf8_bytes
            if estimated > MAX_CONTEXT_BYTES:
                raise ConversationContextTooLarge()
            return context
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

    def compaction_candidate(
        self, conversation_id: UUID, turn_id: UUID
    ) -> ConversationCompactionCandidate | None:
        try:
            context, user_seq, sequenced = self._load(
                conversation_id, turn_id
            )
            if context.estimated_utf8_bytes <= COMPACTION_TRIGGER_BYTES:
                return None
            completed_assistant_sequences = [
                seq
                for seq, message in sequenced
                if seq < user_seq and message.role == "assistant"
            ]
            if not completed_assistant_sequences:
                raise ConversationContextTooLarge()
            through_seq = max(completed_assistant_sequences)
            messages = tuple(
                message for seq, message in sequenced if seq <= through_seq
            )
            if not messages or messages[-1].role != "assistant":
                raise ConversationContextError()
            return ConversationCompactionCandidate(
                previous_summary=context.summary,
                messages=messages,
                through_seq=through_seq,
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
