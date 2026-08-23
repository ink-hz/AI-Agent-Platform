from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)
from app.local_secrets import read_secret_file


_BACKFILL_NAMESPACE = UUID("ee12d960-8e6f-4df9-a05e-64b7cb145f04")
_TERMINAL_STATUSES = frozenset(
    {"completed", "partially_completed", "failed", "cancelled", "interrupted"}
)
_TERMINAL_EVENTS = frozenset(
    {"mission.completed", "mission.failed", "mission.cancelled", "mission.interrupted"}
)


class ConversationBackfillError(RuntimeError):
    """Stable backfill failure that never includes protected content."""


@dataclass(frozen=True)
class ConversationBackfillReport:
    scanned: int
    created: int
    quarantined: int


def _stable_id(mission_id: UUID, kind: str) -> UUID:
    return uuid5(_BACKFILL_NAMESPACE, f"{mission_id}:{kind}")


def _mission_message_subject(mission_id: UUID, message_id: UUID) -> str:
    return f"mission:{mission_id}:message:{message_id}:content"


def _mission_event_subject(mission_id: UUID, event_id: UUID) -> str:
    return f"mission:{mission_id}:event:{event_id}:payload"


def _conversation_message_subject(conversation_id: UUID, message_id: UUID) -> str:
    return f"conversation:{conversation_id}:message:{message_id}:content"


def _title(text: str) -> str:
    selected = " ".join(
        part.strip() for part in text.strip().splitlines() if part.strip()
    )
    if not selected:
        raise ContentCryptoError("legacy content invalid")
    return selected[:160]


def _unseal_text(
    codec: ContentCodec,
    subject: str,
    ciphertext: object,
    key_version: object,
) -> str:
    if not isinstance(key_version, int):
        raise ContentCryptoError("legacy content invalid")
    value = codec.unseal_json(
        subject,
        SealedContent(bytes(ciphertext), key_version),
    )
    if (
        set(value) != {"text"}
        or not isinstance(value["text"], str)
        or not value["text"].strip()
    ):
        raise ContentCryptoError("legacy content invalid")
    return value["text"]


def _terminal_projection(
    codec: ContentCodec, row: dict[str, object]
) -> tuple[str, str, str]:
    status = row["status"]
    if status not in _TERMINAL_STATUSES:
        return (
            "system",
            "failed",
            f"该历史任务在持续对话升级前未完成，已保留当时状态：{status}。",
        )
    event_type = row["terminal_event_type"]
    if event_type not in _TERMINAL_EVENTS:
        raise ContentCryptoError("legacy terminal delivery unavailable")
    text = _unseal_text(
        codec,
        _mission_event_subject(row["mission_id"], row["terminal_event_id"]),
        row["terminal_payload_ciphertext"],
        row["terminal_encryption_key_version"],
    )
    return (
        "assistant" if event_type == "mission.completed" else "system",
        "completed" if event_type == "mission.completed" else "failed",
        text,
    )


def _turn_status(row: dict[str, object]) -> str:
    status = row["status"]
    if status == "completed":
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status == "interrupted":
        return "interrupted"
    if status in {"failed", "partially_completed"}:
        return "failed"
    return "interrupted"


class ConversationBackfill:
    def __init__(
        self,
        maintenance_database_url: str,
        *,
        content_codec: ContentCodec,
        connect=psycopg.connect,
    ) -> None:
        validate_control_dsn(maintenance_database_url, purpose="maintenance")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        self._database_url = maintenance_database_url
        self._codec = content_codec
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=30000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def _batch(
        self, *, after: UUID | None, limit: int
    ) -> tuple[int, int, int, UUID | None]:
        scanned = created = quarantined = 0
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("set constraints all deferred")
            rows = cursor.execute(
                "select mission.*,message.message_id as source_message_id,"
                "message.content_ciphertext as source_content_ciphertext,"
                "message.encryption_key_version as source_encryption_key_version,"
                "terminal.event_id as terminal_event_id,"
                "terminal.event_type as terminal_event_type,"
                "terminal.payload_ciphertext as terminal_payload_ciphertext,"
                "terminal.encryption_key_version as terminal_encryption_key_version "
                "from platform_control.missions mission "
                "left join lateral (select source.* from "
                "platform_control.mission_messages source "
                "where source.mission_id=mission.mission_id and source.role='user' "
                "order by source.seq limit 1) message on true "
                "left join lateral (select source.* from "
                "platform_control.mission_events source "
                "where source.mission_id=mission.mission_id and source.event_type in ("
                "'mission.completed','mission.failed','mission.cancelled',"
                "'mission.interrupted') "
                "order by source.seq desc limit 1) terminal on true "
                "where mission.conversation_id is null "
                "and (%s::uuid is null or mission.mission_id>%s) "
                "order by mission.mission_id limit %s "
                "for update of mission skip locked",
                (after, after, limit),
            ).fetchall()
            if not rows:
                return 0, 0, 0, after
            for row in rows:
                scanned += 1
                mission_id = row["mission_id"]
                try:
                    prompt = _unseal_text(
                        self._codec,
                        _mission_message_subject(mission_id, row["source_message_id"]),
                        row["source_content_ciphertext"],
                        row["source_encryption_key_version"],
                    )
                    response_role, delivery_status, response_text = (
                        _terminal_projection(self._codec, row)
                    )
                    conversation_id = _stable_id(mission_id, "conversation")
                    user_message_id = _stable_id(mission_id, "user-message")
                    assistant_message_id = _stable_id(mission_id, "assistant-message")
                    turn_id = _stable_id(mission_id, "turn")
                    user_content = self._codec.seal_json(
                        _conversation_message_subject(conversation_id, user_message_id),
                        {"text": prompt},
                    )
                    assistant_content = self._codec.seal_json(
                        _conversation_message_subject(
                            conversation_id, assistant_message_id
                        ),
                        {"text": response_text},
                    )
                except (
                    ContentCryptoError,
                    KeyError,
                    TypeError,
                    UnicodeError,
                    ValueError,
                ):
                    quarantined += 1
                    continue
                cursor.execute(
                    "insert into platform_control.conversations "
                    "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
                    "mode,direct_agent_id,title,status,created_at,updated_at) "
                    "values (%s,%s,%s,%s,%s,%s,'active',%s,%s)",
                    (
                        conversation_id,
                        row["owner_internal_user_id"],
                        row["client_request_id"],
                        row["mode"],
                        row["direct_agent_id"],
                        _title(prompt),
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                cursor.execute(
                    "insert into platform_control.conversation_messages "
                    "(message_id,conversation_id,seq,role,content_ciphertext,"
                    "encryption_key_version,turn_id,mission_id,delivery_status,"
                    "created_at,completed_at) values "
                    "(%s,%s,1,'user',%s,%s,%s,%s,'completed',%s,%s),"
                    "(%s,%s,2,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        user_message_id,
                        conversation_id,
                        user_content.ciphertext,
                        user_content.key_version,
                        turn_id,
                        mission_id,
                        row["created_at"],
                        row["created_at"],
                        assistant_message_id,
                        conversation_id,
                        response_role,
                        assistant_content.ciphertext,
                        assistant_content.key_version,
                        turn_id,
                        mission_id,
                        delivery_status,
                        row["updated_at"],
                        row["terminal_at"] or row["updated_at"],
                    ),
                )
                cursor.execute(
                    "insert into platform_control.conversation_turns "
                    "(turn_id,conversation_id,user_message_id,assistant_message_id,"
                    "client_request_id,mission_id,status,created_at,updated_at) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        turn_id,
                        conversation_id,
                        user_message_id,
                        assistant_message_id,
                        row["client_request_id"],
                        mission_id,
                        _turn_status(row),
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                cursor.execute(
                    "update platform_control.missions set conversation_id=%s,"
                    "turn_id=%s,triggering_message_id=%s where mission_id=%s",
                    (conversation_id, turn_id, user_message_id, mission_id),
                )
                created += 1
            return scanned, created, quarantined, rows[-1]["mission_id"]

    def run(
        self,
        *,
        batch_size: int = 100,
        max_batches: int | None = None,
    ) -> ConversationBackfillReport:
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 500
        ):
            raise ValueError("backfill batch size invalid")
        if max_batches is not None and (
            isinstance(max_batches, bool)
            or not isinstance(max_batches, int)
            or max_batches < 1
        ):
            raise ValueError("backfill max batches invalid")
        scanned = created = quarantined = batches = 0
        cursor: UUID | None = None
        try:
            while max_batches is None or batches < max_batches:
                (
                    batch_scanned,
                    batch_created,
                    batch_quarantined,
                    next_cursor,
                ) = self._batch(after=cursor, limit=batch_size)
                if batch_scanned == 0:
                    break
                scanned += batch_scanned
                created += batch_created
                quarantined += batch_quarantined
                batches += 1
                cursor = next_cursor
                if batch_scanned < batch_size:
                    break
            return ConversationBackfillReport(scanned, created, quarantined)
        except psycopg.Error:
            raise ConversationBackfillError(
                "conversation backfill unavailable"
            ) from None


def _resources(database_file: str, keyring_file: str) -> tuple[str, ContentCodec]:
    database_url = read_secret_file(database_file)
    keyring = IdentityKeyring.from_file(
        keyring_file,
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    return database_url, ContentCodec(keyring)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Agent Brain Conversations")
    parser.add_argument(
        "--database-url-file",
        default=os.getenv("PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", ""),
    )
    parser.add_argument(
        "--keyring-file",
        default=os.getenv("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE", ""),
    )
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    if not args.database_url_file or not args.keyring_file:
        raise ValueError("backfill secret files required")
    database_url, codec = _resources(args.database_url_file, args.keyring_file)
    report = ConversationBackfill(database_url, content_codec=codec).run(
        batch_size=args.batch_size
    )
    print(
        "AGENT_BRAIN_CONVERSATION_BACKFILL_OK "
        f"scanned={report.scanned} created={report.created} "
        f"quarantined={report.quarantined}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
