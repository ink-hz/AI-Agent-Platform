from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import re
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)
from app.execution_relay.models import RelayEvent


TERMINAL_MISSION_STATUSES = frozenset(
    {"completed", "partially_completed", "failed", "cancelled", "interrupted"}
)
TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)
_MISSION_STATUSES = frozenset(
    {
        "planning",
        "delegated",
        "synthesizing",
        *TERMINAL_MISSION_STATUSES,
    }
)
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PUBLIC_PROGRESS_CODE = re.compile(r"[a-z][a-z0-9._-]{0,63}")

MAX_PAYLOAD_BYTES = 64 * 1024
MAX_PAYLOAD_DEPTH = 16
MAX_PAYLOAD_ITEMS = 2_048
MAX_PAYLOAD_STRING_BYTES = 32 * 1024
MAX_PAYLOAD_KEY_BYTES = 256
MAX_EVENT_TEXT_BYTES = 8 * 1024
MAX_EVENT_LIST_ITEMS = 100

_EVENT_PAYLOAD_SCHEMAS = {
    "mission.started": (
        {"text": "text"},
        frozenset({"text"}),
        frozenset(),
    ),
    "brain.responding": (
        {"text": "text", "stage": "short_text"},
        frozenset({"text"}),
        frozenset(),
    ),
    "plan.created": (
        {
            "text": "text",
            "selected_agent_id": "agent_id",
            "objective": "text",
            "rationale_summary": "text",
        },
        frozenset({"text"}),
        frozenset(),
    ),
    "task.dispatched": (
        {"text": "text", "agent_id": "agent_id"},
        frozenset({"agent_id"}),
        frozenset(),
    ),
    "agent.accepted": (
        {"text": "text", "agent_id": "agent_id"},
        frozenset({"agent_id"}),
        frozenset(),
    ),
    "agent.progress": (
        {
            "text": "text",
            "agent_id": "agent_id",
            "progress": "unit_number",
            "current": "nonnegative_int",
            "total": "positive_int",
            "index": "nonnegative_int",
            "state": "short_text",
            "stage": "short_text",
        },
        frozenset(),
        frozenset({"text", "progress", "current", "index", "state", "stage"}),
    ),
    "agent.result": (
        {
            "text": "text",
            "agent_id": "agent_id",
            "summary": "text",
            "result": "text",
            "items": "text_list",
            "evidence": "text_list",
            "gaps": "text_list",
            "rationale_summary": "text",
        },
        frozenset(),
        frozenset({"text", "summary", "result", "items", "evidence", "gaps"}),
    ),
    "task.reviewed": (
        {
            "text": "text",
            "summary": "text",
            "accepted": "bool",
            "gaps": "text_list",
        },
        frozenset(),
        frozenset({"text", "summary"}),
    ),
    "synthesis.started": (
        {"text": "text"},
        frozenset({"text"}),
        frozenset(),
    ),
    "mission.completed": (
        {
            "text": "text",
            "summary": "text",
            "result": "text",
            "items": "text_list",
            "evidence": "text_list",
            "gaps": "text_list",
        },
        frozenset({"text"}),
        frozenset(),
    ),
    "mission.failed": (
        {
            "text": "text",
            "summary": "text",
            "reason": "text",
            "reason_code": "code",
            "code": "code",
            "partial_result": "text",
        },
        frozenset({"text"}),
        frozenset(),
    ),
    "mission.cancelled": (
        {"text": "text", "reason": "text", "reason_code": "code"},
        frozenset({"text"}),
        frozenset(),
    ),
    "mission.interrupted": (
        {
            "text": "text",
            "reason": "text",
            "reason_code": "code",
            "partial_result": "text",
        },
        frozenset({"text"}),
        frozenset(),
    ),
}

_VALID_COMPLETIONS = frozenset(
    {
        ("brain", "planning", "summary", "completed", None, "brain.responding"),
        ("brain", "planning", "summary", "failed", "failed", "mission.failed"),
        ("brain", "planning", "summary", "cancelled", "cancelled", "mission.cancelled"),
        ("brain", "planning", "summary", "interrupted", "interrupted", "mission.interrupted"),
        ("direct_agent", "delegated", "summary", "completed", None, "brain.responding"),
        ("direct_agent", "delegated", "summary", "failed", "failed", "mission.failed"),
        ("direct_agent", "delegated", "summary", "cancelled", "cancelled", "mission.cancelled"),
        ("direct_agent", "delegated", "summary", "interrupted", "interrupted", "mission.interrupted"),
        ("brain", "planning", "planning", "completed", "delegated", "plan.created"),
        (
            "brain",
            "planning",
            "planning",
            "completed",
            "completed",
            "mission.completed",
        ),
        ("brain", "planning", "planning", "failed", "failed", "mission.failed"),
        (
            "brain",
            "planning",
            "planning",
            "cancelled",
            "cancelled",
            "mission.cancelled",
        ),
        (
            "brain",
            "planning",
            "planning",
            "interrupted",
            "interrupted",
            "mission.interrupted",
        ),
        ("brain", "delegated", "professional", "completed", None, "agent.result"),
        (
            "brain",
            "delegated",
            "professional",
            "failed",
            "failed",
            "mission.failed",
        ),
        (
            "brain",
            "delegated",
            "professional",
            "failed",
            "partially_completed",
            "mission.failed",
        ),
        (
            "brain",
            "delegated",
            "professional",
            "cancelled",
            "cancelled",
            "mission.cancelled",
        ),
        (
            "brain",
            "delegated",
            "professional",
            "interrupted",
            "interrupted",
            "mission.interrupted",
        ),
        (
            "brain",
            "delegated",
            "professional",
            "interrupted",
            "partially_completed",
            "mission.interrupted",
        ),
        (
            "brain",
            "synthesizing",
            "synthesis",
            "completed",
            "completed",
            "mission.completed",
        ),
        (
            "brain",
            "synthesizing",
            "synthesis",
            "failed",
            "failed",
            "mission.failed",
        ),
        (
            "brain",
            "synthesizing",
            "synthesis",
            "failed",
            "partially_completed",
            "mission.failed",
        ),
        (
            "brain",
            "synthesizing",
            "synthesis",
            "cancelled",
            "cancelled",
            "mission.cancelled",
        ),
        (
            "brain",
            "synthesizing",
            "synthesis",
            "interrupted",
            "interrupted",
            "mission.interrupted",
        ),
        (
            "brain",
            "synthesizing",
            "synthesis",
            "interrupted",
            "partially_completed",
            "mission.interrupted",
        ),
        (
            "direct_agent",
            "delegated",
            "direct",
            "completed",
            "completed",
            "mission.completed",
        ),
        (
            "direct_agent",
            "delegated",
            "direct",
            "failed",
            "failed",
            "mission.failed",
        ),
        (
            "direct_agent",
            "delegated",
            "direct",
            "cancelled",
            "cancelled",
            "mission.cancelled",
        ),
        (
            "direct_agent",
            "delegated",
            "direct",
            "interrupted",
            "interrupted",
            "mission.interrupted",
        ),
    }
)

_VALID_CREATIONS = frozenset(
    {
        ("brain", "planning", "summary", "brain.responding"),
        ("brain", "planning", "planning", "brain.responding"),
        ("brain", "delegated", "professional", "task.dispatched"),
        ("brain", "delegated", "synthesis", "synthesis.started"),
        ("direct_agent", "delegated", "direct", "task.dispatched"),
        ("direct_agent", "delegated", "summary", "brain.responding"),
    }
)
_CREATION_PREDECESSORS = {
    "summary": frozenset({frozenset()}),
    "planning": frozenset({frozenset(), frozenset({("summary", "completed")})}),
    "professional": frozenset({frozenset({("planning", "completed")})}),
    "synthesis": frozenset({
        frozenset({("planning", "completed"), ("professional", "completed")})
    }),
    "direct": frozenset({frozenset(), frozenset({("summary", "completed")})}),
}


class MissionRepositoryError(RuntimeError):
    """Stable persistence failure without protected values or SQL details."""

    def __init__(self, message: str = "mission repository unavailable") -> None:
        super().__init__(message)


class MissionRepositoryConflict(MissionRepositoryError):
    def __init__(self) -> None:
        super().__init__("mission repository conflict")


class MissionRepositoryNotFound(MissionRepositoryError):
    def __init__(self) -> None:
        super().__init__("mission not found")


class MissionContentUnavailable(MissionRepositoryError):
    """A deterministic missing/corrupt Mission message, safe to quarantine."""

    def __init__(self) -> None:
        super().__init__("mission content unavailable")


@dataclass(frozen=True)
class MissionRecord:
    mission_id: UUID
    owner_internal_user_id: UUID
    client_request_id: UUID
    mode: Literal["brain", "direct_agent"]
    direct_agent_id: str | None
    status: str
    cancel_requested: bool
    row_version: int
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None
    prompt: str = field(repr=False)
    content_available: bool = True
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    triggering_message_id: UUID | None = None


@dataclass(frozen=True)
class MissionCreateResult:
    mission: MissionRecord
    created: bool


@dataclass(frozen=True)
class MissionClaim:
    """Minimal, non-secret work claim used by the orchestration scanner."""

    mission_id: UUID
    owner_internal_user_id: UUID
    status: str
    row_version: int


@dataclass(frozen=True)
class MissionEvent:
    event_id: UUID
    mission_id: UUID
    run_id: UUID | None
    seq: int
    event_type: str
    payload: dict[str, object] = field(repr=False)
    created_at: datetime = field(compare=True)


@dataclass(frozen=True)
class MissionRun:
    run_id: UUID
    mission_id: UUID
    task_id: UUID | None
    phase: str
    agent_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    relay_event_cursor: int
    input_payload: dict[str, object] = field(repr=False)
    output_payload: dict[str, object] | None = field(default=None, repr=False)


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID required")
    return value


def _validate_event_payload(event_type: str, payload: dict[str, object]) -> None:
    schema = _EVENT_PAYLOAD_SCHEMAS.get(event_type)
    if schema is None:
        raise ValueError
    fields, required, any_of = schema
    keys = frozenset(payload)
    if (
        not required <= keys
        or not keys <= frozenset(fields)
        or (
            any_of
            and not any(
                key in payload and payload[key] != [] for key in any_of
            )
        )
    ):
        raise ValueError
    for key, value in payload.items():
        kind = fields[key]
        if kind in {"text", "short_text", "code", "agent_id"}:
            if type(value) is not str or not value.strip():
                raise ValueError
            limit = (
                128
                if kind in {"short_text", "code", "agent_id"}
                else MAX_EVENT_TEXT_BYTES
            )
            if len(value.encode("utf-8")) > limit:
                raise ValueError
            if kind == "agent_id" and _AGENT_ID.fullmatch(value) is None:
                raise ValueError
        elif kind == "bool":
            if type(value) is not bool:
                raise ValueError
        elif kind in {"nonnegative_int", "positive_int"}:
            minimum = 1 if kind == "positive_int" else 0
            if type(value) is not int or value < minimum:
                raise ValueError
        elif kind == "unit_number":
            if type(value) not in {int, float} or not 0 <= value <= 1:
                raise ValueError
        elif kind == "text_list":
            if type(value) is not list or len(value) > MAX_EVENT_LIST_ITEMS:
                raise ValueError
            for member in value:
                if type(member) is not str or not member.strip():
                    raise ValueError
                if len(member.encode("utf-8")) > MAX_EVENT_TEXT_BYTES:
                    raise ValueError
        else:
            raise ValueError
    if event_type == "agent.progress" and {
        "current",
        "total",
    } <= keys and payload["current"] > payload["total"]:
        raise ValueError


def _require_event_agent(
    payload: dict[str, object], actual_agent_id: str
) -> None:
    claimed_agent_id = payload.get("agent_id")
    if claimed_agent_id is not None and claimed_agent_id != actual_agent_id:
        raise MissionRepositoryConflict()


def _canonical_payload(
    value: object, *, event_type: str | None = None
) -> tuple[dict[str, object], bytes]:
    item_count = 0
    if event_type is not None and event_type not in _EVENT_PAYLOAD_SCHEMAS:
        raise ValueError

    def normalize(node: object, depth: int) -> object:
        nonlocal item_count
        if depth > MAX_PAYLOAD_DEPTH:
            raise ValueError
        if node is None or type(node) in {bool, int}:
            return node
        if type(node) is float:
            if not math.isfinite(node):
                raise ValueError
            return node
        if type(node) is str:
            if len(node.encode("utf-8")) > MAX_PAYLOAD_STRING_BYTES:
                raise ValueError
            return node
        if type(node) is list:
            item_count += len(node)
            if item_count > MAX_PAYLOAD_ITEMS:
                raise ValueError
            return [normalize(member, depth + 1) for member in node]
        if type(node) is dict:
            item_count += len(node)
            if item_count > MAX_PAYLOAD_ITEMS:
                raise ValueError
            normalized: dict[str, object] = {}
            for key, member in node.items():
                if type(key) is not str:
                    raise ValueError
                if len(key.encode("utf-8")) > MAX_PAYLOAD_KEY_BYTES:
                    raise ValueError
                normalized[key] = normalize(member, depth + 1)
            return normalized
        raise ValueError

    normalized = normalize(value, 0)
    if type(normalized) is not dict:
        raise ValueError
    if event_type is not None:
        _validate_event_payload(event_type, normalized)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError
    return normalized, encoded


def _require_agent_id(value: object) -> str:
    if not isinstance(value, str) or _AGENT_ID.fullmatch(value) is None:
        raise ValueError("Agent ID invalid")
    return value


def _require_expected_mission(
    status: str | None, row_version: int | None
) -> None:
    if status is None and row_version is None:
        return
    if (
        status not in _MISSION_STATUSES
        or isinstance(row_version, bool)
        or not isinstance(row_version, int)
        or row_version < 0
    ):
        raise ValueError("expected Mission state invalid")


def _require_locked_completion(
    mission: dict[str, Any],
    run: dict[str, Any],
    run_status: str,
    mission_status: str | None,
    event_type: str,
) -> None:
    transition = (
        mission["mode"],
        mission["status"],
        run["phase"],
        run_status,
        mission_status,
        event_type,
    )
    if transition not in _VALID_COMPLETIONS:
        raise MissionRepositoryConflict()


def _require_locked_creation(
    cursor: Any,
    mission: dict[str, Any],
    mission_id: UUID,
    phase: str,
    event_type: str,
) -> None:
    transition = (
        mission["mode"],
        mission["status"],
        phase,
        event_type,
    )
    if transition not in _VALID_CREATIONS:
        raise MissionRepositoryConflict()
    predecessors = cursor.execute(
        "select phase,status from platform_control.mission_runs "
        "where mission_id=%s for update",
        (mission_id,),
    ).fetchall()
    observed = frozenset((row["phase"], row["status"]) for row in predecessors)
    if (
        len(observed) != len(predecessors)
        or observed not in _CREATION_PREDECESSORS[phase]
    ):
        raise MissionRepositoryConflict()


def _message_subject(mission_id: UUID, message_id: UUID) -> str:
    return f"mission:{mission_id}:message:{message_id}:content"


def _task_subject(mission_id: UUID, task_id: UUID) -> str:
    return f"mission:{mission_id}:task:{task_id}:objective"


def _run_subject(mission_id: UUID, run_id: UUID, field_name: str) -> str:
    return f"mission:{mission_id}:run:{run_id}:{field_name}"


def _event_subject(mission_id: UUID, event_id: UUID) -> str:
    return f"mission:{mission_id}:event:{event_id}:payload"


class MissionRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        self.environment = parsed.environment
        self._control_database_url = control_database_url
        self._connect = connect
        self.content_codec = content_codec
        self._content_keys_ready = False

    def __repr__(self) -> str:
        return (
            "MissionRepository(control_database_url=<redacted>, "
            f"environment={self.environment!r}, content_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def _ensure_key_canary(self, cursor: Any, version: int) -> None:
        if not self.content_codec.supports_key_version(version):
            raise MissionRepositoryError()
        row = cursor.execute(
            "select canary_ciphertext from platform_control.content_key_canaries "
            "where key_version=%s",
            (version,),
        ).fetchone()
        if row is None:
            sealed = self.content_codec.seal_key_canary(version)
            cursor.execute(
                "insert into platform_control.content_key_canaries "
                "(key_version,canary_ciphertext) values (%s,%s) "
                "on conflict (key_version) do nothing",
                (version, sealed.ciphertext),
            )
            row = cursor.execute(
                "select canary_ciphertext from "
                "platform_control.content_key_canaries where key_version=%s",
                (version,),
            ).fetchone()
        if row is None:
            raise MissionRepositoryError()
        try:
            self.content_codec.verify_key_canary(
                SealedContent(bytes(row["canary_ciphertext"]), version)
            )
        except (ContentCryptoError, KeyError, TypeError, ValueError):
            raise MissionRepositoryError() from None

    def _assert_key_canary(self, version: int) -> None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select canary_ciphertext from "
                    "platform_control.content_key_canaries where key_version=%s",
                    (version,),
                ).fetchone()
            if row is None:
                raise MissionRepositoryError()
            self.content_codec.verify_key_canary(
                SealedContent(bytes(row["canary_ciphertext"]), version)
            )
        except MissionRepositoryError:
            raise
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise MissionRepositoryError() from None

    def check_content_keys(self) -> None:
        """Validate exact key bytes before any content may be quarantined."""

        if self._content_keys_ready:
            return
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                versions = {
                    row["encryption_key_version"]
                    for row in cursor.execute(
                        "select distinct encryption_key_version from "
                        "platform_control.mission_messages"
                    ).fetchall()
                }
                versions.add(self.content_codec.active_key_version)
                for version in sorted(versions):
                    canary = cursor.execute(
                        "select canary_ciphertext from "
                        "platform_control.content_key_canaries where key_version=%s",
                        (version,),
                    ).fetchone()
                    if canary is None:
                        samples = cursor.execute(
                            "select mission_id,message_id,content_ciphertext from "
                            "platform_control.mission_messages "
                            "where encryption_key_version=%s "
                            "order by mission_id,message_id limit 100",
                            (version,),
                        ).fetchall()
                        if samples:
                            verified = False
                            for sample in samples:
                                try:
                                    self.content_codec.unseal_json(
                                        _message_subject(
                                            sample["mission_id"], sample["message_id"]
                                        ),
                                        SealedContent(
                                            bytes(sample["content_ciphertext"]), version
                                        ),
                                    )
                                    verified = True
                                    break
                                except ContentCryptoError:
                                    continue
                            if not verified:
                                raise MissionRepositoryError()
                        self._ensure_key_canary(cursor, version)
                    else:
                        self.content_codec.verify_key_canary(
                            SealedContent(bytes(canary["canary_ciphertext"]), version)
                        )
            self._content_keys_ready = True
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None

    @staticmethod
    def _owned_mission_for_update(
        cursor: Any, internal_user_id: UUID, mission_id: UUID
    ) -> dict[str, Any]:
        row = cursor.execute(
            "select mission_id,mode,direct_agent_id,status,cancel_requested,row_version "
            "from platform_control.missions where mission_id=%s "
            "and owner_internal_user_id=%s for update",
            (mission_id, internal_user_id),
        ).fetchone()
        if row is None:
            raise MissionRepositoryNotFound()
        return row

    def _mission_from_row(self, row: dict[str, Any]) -> MissionRecord:
        key_version: int | None = None
        try:
            if row.get("message_id") is None:
                raise MissionContentUnavailable()
            key_version = row["encryption_key_version"]
            if not self.content_codec.supports_key_version(key_version):
                raise MissionRepositoryError()
            value = self.content_codec.unseal_json(
                _message_subject(row["mission_id"], row["message_id"]),
                SealedContent(
                    bytes(row["content_ciphertext"]),
                    key_version,
                ),
            )
            if set(value) != {"text"} or not isinstance(value["text"], str):
                raise MissionContentUnavailable()
        except MissionContentUnavailable:
            raise
        except ContentCryptoError:
            if isinstance(key_version, int):
                self._assert_key_canary(key_version)
            raise MissionContentUnavailable() from None
        except (KeyError, TypeError, ValueError):
            raise MissionContentUnavailable() from None
        return MissionRecord(
            mission_id=row["mission_id"],
            owner_internal_user_id=row["owner_internal_user_id"],
            client_request_id=row["client_request_id"],
            mode=row["mode"],
            direct_agent_id=row["direct_agent_id"],
            status=row["status"],
            cancel_requested=row["cancel_requested"],
            row_version=row["row_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            terminal_at=row["terminal_at"],
            prompt=value["text"],
            conversation_id=row.get("conversation_id"),
            turn_id=row.get("turn_id"),
            triggering_message_id=row.get("triggering_message_id"),
        )

    @staticmethod
    def _mission_tombstone(row: dict[str, Any]) -> MissionRecord:
        return MissionRecord(
            mission_id=row["mission_id"],
            owner_internal_user_id=row["owner_internal_user_id"],
            client_request_id=row["client_request_id"],
            mode=row["mode"],
            direct_agent_id=row["direct_agent_id"],
            status=row["status"],
            cancel_requested=row["cancel_requested"],
            row_version=row["row_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            terminal_at=row["terminal_at"],
            prompt="[任务内容不可用]",
            content_available=False,
            conversation_id=row.get("conversation_id"),
            turn_id=row.get("turn_id"),
            triggering_message_id=row.get("triggering_message_id"),
        )

    def _create_mission_result(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        prompt: str,
        *,
        mode: Literal["brain", "direct_agent"] = "brain",
        direct_agent_id: str | None = None,
    ) -> MissionCreateResult:
        _require_uuid(internal_user_id)
        _require_uuid(client_request_id)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Mission prompt invalid")
        try:
            prompt_size = len(prompt.encode("utf-8"))
        except UnicodeError:
            raise MissionRepositoryError() from None
        if prompt_size > 32 * 1024:
            raise ValueError("Mission prompt invalid")
        if mode == "brain":
            if direct_agent_id is not None:
                raise ValueError("Brain Mission cannot name a direct Agent")
            status = "planning"
        elif mode == "direct_agent":
            direct_agent_id = _require_agent_id(direct_agent_id)
            status = "delegated"
        else:
            raise ValueError("Mission mode invalid")

        mission_id = uuid4()
        message_id = uuid4()
        started_event_id = uuid4()
        try:
            sealed = self.content_codec.seal_json(
                _message_subject(mission_id, message_id), {"text": prompt}
            )
            started_payload, _started_canonical = _canonical_payload(
                {"text": "任务已创建"}, event_type="mission.started"
            )
            sealed_started = self.content_codec.seal_json(
                _event_subject(mission_id, started_event_id), started_payload
            )
            with self._connection() as connection, connection.cursor() as cursor:
                self._ensure_key_canary(cursor, sealed.key_version)
                inserted = cursor.execute(
                    "insert into platform_control.missions "
                    "(mission_id,owner_internal_user_id,client_request_id,mode,"
                    "direct_agent_id,status) values (%s,%s,%s,%s,%s,%s) "
                    "on conflict (owner_internal_user_id,client_request_id) "
                    "do nothing returning created_at,updated_at",
                    (
                        mission_id,
                        internal_user_id,
                        client_request_id,
                        mode,
                        direct_agent_id,
                        status,
                    ),
                ).fetchone()
                if inserted is not None:
                    cursor.execute(
                        "insert into platform_control.mission_messages "
                        "(message_id,mission_id,seq,role,content_ciphertext,"
                        "encryption_key_version) values (%s,%s,1,'user',%s,%s)",
                        (
                            message_id,
                            mission_id,
                            sealed.ciphertext,
                            sealed.key_version,
                        ),
                    )
                    self._append_event_locked(
                        cursor,
                        mission_id,
                        None,
                        started_event_id,
                        "mission.started",
                        sealed_started,
                    )
                    return MissionCreateResult(
                        MissionRecord(
                            mission_id=mission_id,
                            owner_internal_user_id=internal_user_id,
                            client_request_id=client_request_id,
                            mode=mode,
                            direct_agent_id=direct_agent_id,
                            status=status,
                            cancel_requested=False,
                            row_version=0,
                            created_at=inserted["created_at"],
                            updated_at=inserted["updated_at"],
                            terminal_at=None,
                            prompt=prompt,
                        ),
                        created=True,
                    )

                existing = cursor.execute(
                    "select m.*,message.message_id,message.content_ciphertext,"
                    "message.encryption_key_version from platform_control.missions m "
                    "join platform_control.mission_messages message "
                    "on message.mission_id=m.mission_id and message.seq=1 "
                    "where m.owner_internal_user_id=%s and m.client_request_id=%s "
                    "for update of m",
                    (internal_user_id, client_request_id),
                ).fetchone()
                if existing is None:
                    raise MissionRepositoryError()
                replay = self._mission_from_row(existing)
                if (
                    replay.prompt != prompt
                    or replay.mode != mode
                    or replay.direct_agent_id != direct_agent_id
                ):
                    raise MissionRepositoryConflict()
                return MissionCreateResult(replay, created=False)
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def create_mission(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        prompt: str,
        *,
        mode: Literal["brain", "direct_agent"] = "brain",
        direct_agent_id: str | None = None,
    ) -> MissionRecord:
        return self._create_mission_result(
            internal_user_id,
            client_request_id,
            prompt,
            mode=mode,
            direct_agent_id=direct_agent_id,
        ).mission

    def insert_for_conversation(
        self,
        cursor: Any,
        *,
        mission_id: UUID,
        mission_message_id: UUID,
        started_event_id: UUID,
        internal_user_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        triggering_message_id: UUID,
        prompt: str,
        mode: Literal["brain", "direct_agent"] = "brain",
        direct_agent_id: str | None = None,
    ) -> MissionRecord:
        """Insert one linked Mission inside the caller's open transaction."""

        for value in (
            mission_id,
            mission_message_id,
            started_event_id,
            internal_user_id,
            conversation_id,
            turn_id,
            triggering_message_id,
        ):
            _require_uuid(value)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Mission prompt invalid")
        try:
            prompt_size = len(prompt.encode("utf-8"))
        except UnicodeError:
            raise MissionRepositoryError() from None
        if prompt_size > 32 * 1024:
            raise ValueError("Mission prompt invalid")
        if mode == "brain":
            if direct_agent_id is not None:
                raise ValueError("Brain Mission cannot name a direct Agent")
            status = "planning"
        elif mode == "direct_agent":
            direct_agent_id = _require_agent_id(direct_agent_id)
            status = "delegated"
        else:
            raise ValueError("Mission mode invalid")

        sealed = self.content_codec.seal_json(
            _message_subject(mission_id, mission_message_id), {"text": prompt}
        )
        started_payload, _canonical = _canonical_payload(
            {"text": "任务已创建"}, event_type="mission.started"
        )
        sealed_started = self.content_codec.seal_json(
            _event_subject(mission_id, started_event_id), started_payload
        )
        self._ensure_key_canary(cursor, sealed.key_version)
        inserted = cursor.execute(
            "insert into platform_control.missions "
            "(mission_id,owner_internal_user_id,client_request_id,mode,"
            "direct_agent_id,status,conversation_id,turn_id,triggering_message_id) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "returning created_at,updated_at",
            (
                mission_id,
                internal_user_id,
                turn_id,
                mode,
                direct_agent_id,
                status,
                conversation_id,
                turn_id,
                triggering_message_id,
            ),
        ).fetchone()
        if inserted is None:
            raise MissionRepositoryError()
        cursor.execute(
            "insert into platform_control.mission_messages "
            "(message_id,mission_id,seq,role,content_ciphertext,"
            "encryption_key_version) values (%s,%s,1,'user',%s,%s)",
            (
                mission_message_id,
                mission_id,
                sealed.ciphertext,
                sealed.key_version,
            ),
        )
        self._append_event_locked(
            cursor,
            mission_id,
            None,
            started_event_id,
            "mission.started",
            sealed_started,
        )
        return MissionRecord(
            mission_id=mission_id,
            owner_internal_user_id=internal_user_id,
            client_request_id=turn_id,
            mode=mode,
            direct_agent_id=direct_agent_id,
            status=status,
            cancel_requested=False,
            row_version=0,
            created_at=inserted["created_at"],
            updated_at=inserted["updated_at"],
            terminal_at=None,
            prompt=prompt,
            conversation_id=conversation_id,
            turn_id=turn_id,
            triggering_message_id=triggering_message_id,
        )

    def create_mission_for_api(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        prompt: str,
        *,
        mode: Literal["brain", "direct_agent"] = "brain",
        direct_agent_id: str | None = None,
    ) -> MissionCreateResult:
        """Create or replay one Mission while preserving HTTP idempotency status."""

        return self._create_mission_result(
            internal_user_id,
            client_request_id,
            prompt,
            mode=mode,
            direct_agent_id=direct_agent_id,
        )

    def mission_for_owner(
        self, internal_user_id: UUID, mission_id: UUID
    ) -> MissionRecord:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select m.*,message.message_id,message.content_ciphertext,"
                    "message.encryption_key_version from platform_control.missions m "
                    "left join platform_control.mission_messages message "
                    "on message.mission_id=m.mission_id and message.seq=1 "
                    "where m.mission_id=%s and m.owner_internal_user_id=%s",
                    (mission_id, internal_user_id),
                ).fetchone()
            if row is None:
                raise MissionRepositoryNotFound()
            try:
                return self._mission_from_row(row)
            except MissionContentUnavailable:
                return self._mission_tombstone(row)
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def mission_for_orchestration(
        self, internal_user_id: UUID, mission_id: UUID
    ) -> MissionRecord:
        """Read one owned Mission strictly so only deterministic content fails."""

        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select m.*,message.message_id,message.content_ciphertext,"
                    "message.encryption_key_version from platform_control.missions m "
                    "left join platform_control.mission_messages message "
                    "on message.mission_id=m.mission_id and message.seq=1 "
                    "where m.mission_id=%s and m.owner_internal_user_id=%s",
                    (mission_id, internal_user_id),
                ).fetchone()
            if row is None:
                raise MissionRepositoryNotFound()
            return self._mission_from_row(row)
        except MissionRepositoryError:
            raise
        except psycopg.Error:
            raise MissionRepositoryError() from None

    def list_missions_for_owner(
        self,
        internal_user_id: UUID,
        *,
        limit: int = 50,
        before: tuple[datetime, UUID] | None = None,
    ) -> tuple[MissionRecord, ...]:
        _require_uuid(internal_user_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 101
        ):
            raise ValueError("Mission list limit invalid")
        if before is not None and (
            not isinstance(before, tuple)
            or len(before) != 2
            or not isinstance(before[0], datetime)
            or before[0].tzinfo is None
            or not isinstance(before[1], UUID)
        ):
            raise ValueError("Mission list cursor invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                query = (
                    "select m.*,message.message_id,message.content_ciphertext,"
                    "message.encryption_key_version from platform_control.missions m "
                    "left join platform_control.mission_messages message "
                    "on message.mission_id=m.mission_id and message.seq=1 "
                    "where m.owner_internal_user_id=%s "
                )
                parameters: tuple[object, ...]
                if before is None:
                    parameters = (internal_user_id, limit)
                else:
                    query += "and (m.created_at,m.mission_id)<(%s,%s) "
                    parameters = (
                        internal_user_id,
                        before[0],
                        before[1],
                        limit,
                    )
                rows = cursor.execute(
                    query
                    + "order by m.created_at desc,m.mission_id desc limit %s",
                    parameters,
                ).fetchall()
            missions: list[MissionRecord] = []
            for row in rows:
                try:
                    missions.append(self._mission_from_row(row))
                except MissionContentUnavailable:
                    missions.append(self._mission_tombstone(row))
            return tuple(missions)
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def request_cancel(
        self, internal_user_id: UUID, mission_id: UUID
    ) -> MissionRecord:
        """Set the owned Mission cancellation flag once; terminal replay is stable."""

        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                relay_jobs = cursor.execute(
                    "select job.run_id,job.status "
                    "from platform_control.mission_runs run_row "
                    "join platform_control.execution_jobs job "
                    "on job.run_id=run_row.run_id "
                    "where run_row.mission_id=%s "
                    "and run_row.status<>all(%s) "
                    "order by run_row.created_at,run_row.run_id "
                    "for update of job",
                    (mission_id, list(TERMINAL_RUN_STATUSES)),
                ).fetchall()
                relay_terminal_observed = any(
                    job["status"] in TERMINAL_RUN_STATUSES
                    for job in relay_jobs
                )
                if (
                    mission["status"] not in TERMINAL_MISSION_STATUSES
                    and not mission["cancel_requested"]
                    and not relay_terminal_observed
                ):
                    cursor.execute(
                        "update platform_control.missions set "
                        "cancel_requested=true,row_version=row_version+1,"
                        "updated_at=now() where mission_id=%s",
                        (mission_id,),
                    )
                if not relay_terminal_observed:
                    cursor.execute(
                        "update platform_control.execution_jobs job set "
                        "status=case when job.status='queued' then 'cancelled' "
                        "else job.status end,cancel_requested=true,"
                        "terminal_at=case when job.status='queued' then now() "
                        "else job.terminal_at end,"
                        "stop_requested_status=case when job.status='queued' "
                        "then null else 'cancelled' end,"
                        "stop_acknowledged_at=null,updated_at=now() "
                        "from platform_control.mission_runs run_row "
                        "where run_row.mission_id=%s "
                        "and run_row.run_id=job.run_id "
                        "and job.status=any(%s)",
                        (
                            mission_id,
                            ["queued", "leased", "dispatched", "running"],
                        ),
                    )
            return self.mission_for_owner(internal_user_id, mission_id)
        except MissionRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise MissionRepositoryError() from None

    def claim_pending(
        self,
        limit: int,
        *,
        modes: tuple[Literal["brain", "direct_agent"], ...] | None = None,
    ) -> tuple[MissionClaim, ...]:
        """Select an internal orchestration batch without blocking peers."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("Mission claim limit invalid")
        if modes is not None and (
            not isinstance(modes, tuple)
            or not modes
            or len(set(modes)) != len(modes)
            or any(mode not in {"brain", "direct_agent"} for mode in modes)
        ):
            raise ValueError("Mission claim modes invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                mode_predicate = ""
                parameters: tuple[object, ...]
                if modes is None:
                    parameters = (limit,)
                else:
                    mode_predicate = "and m.mode=any(%s) "
                    parameters = (list(modes), limit)
                rows = cursor.execute(
                    "select m.mission_id,m.owner_internal_user_id,m.status,m.row_version "
                    "from platform_control.missions m "
                    "where m.status not in "
                    "('completed','partially_completed','failed','cancelled','interrupted') "
                    + mode_predicate
                    + "order by m.updated_at,m.mission_id "
                    "for update of m skip locked limit %s",
                    parameters,
                ).fetchall()
                return tuple(MissionClaim(**row) for row in rows)
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def quarantine_claim(
        self,
        claim: MissionClaim,
        *,
        reason_code: str = "mission_content_unavailable",
    ) -> bool:
        """Terminalize one unreadable Mission without touching its ciphertext."""

        if not isinstance(claim, MissionClaim):
            raise ValueError("Mission claim invalid")
        event_id = uuid4()
        payload = {
            "text": "任务内容不可读取，已安全终止",
            "reason_code": reason_code,
        }
        try:
            normalized, _canonical = _canonical_payload(
                payload, event_type="mission.failed"
            )
            sealed = self.content_codec.seal_json(
                _event_subject(claim.mission_id, event_id), normalized
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, claim.owner_internal_user_id, claim.mission_id
                )
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    return False
                if (
                    mission["status"] != claim.status
                    or mission["row_version"] != claim.row_version
                ):
                    raise MissionRepositoryConflict()
                self._append_event_locked(
                    cursor,
                    claim.mission_id,
                    None,
                    event_id,
                    "mission.failed",
                    sealed,
                )
                cursor.execute(
                    "update platform_control.missions set status='failed',"
                    "row_version=row_version+1,updated_at=now(),terminal_at=now() "
                    "where mission_id=%s",
                    (claim.mission_id,),
                )
            return True
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def terminate_mission(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        *,
        status: Literal["failed", "cancelled", "interrupted"],
        event_type: Literal[
            "mission.failed", "mission.cancelled", "mission.interrupted"
        ],
        event_payload: dict[str, object],
        expected_mission_status: str | None = None,
        expected_row_version: int | None = None,
    ) -> bool:
        """Persist a terminal Mission transition that has no phase run."""

        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        _require_expected_mission(expected_mission_status, expected_row_version)
        if (status, event_type) not in {
            ("failed", "mission.failed"),
            ("cancelled", "mission.cancelled"),
            ("interrupted", "mission.interrupted"),
        }:
            raise ValueError("Mission terminal transition invalid")
        event_id = uuid4()
        try:
            normalized, _canonical = _canonical_payload(
                event_payload, event_type=event_type
            )
            sealed = self.content_codec.seal_json(
                _event_subject(mission_id, event_id), normalized
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    return False
                if expected_mission_status is not None and (
                    mission["status"] != expected_mission_status
                    or mission["row_version"] != expected_row_version
                ):
                    raise MissionRepositoryConflict()
                self._append_event_locked(
                    cursor, mission_id, None, event_id, event_type, sealed
                )
                cursor.execute(
                    "update platform_control.missions set status=%s,"
                    "row_version=row_version+1,updated_at=now(),terminal_at=now() "
                    "where mission_id=%s",
                    (status, mission_id),
                )
            return True
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def runs_for_owner(
        self, internal_user_id: UUID, mission_id: UUID
    ) -> tuple[MissionRun, ...]:
        """Read phase state after applying the Mission owner predicate."""

        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                owned = cursor.execute(
                    "select mission_id from platform_control.missions "
                    "where mission_id=%s and owner_internal_user_id=%s",
                    (mission_id, internal_user_id),
                ).fetchone()
                if owned is None:
                    raise MissionRepositoryNotFound()
                rows = cursor.execute(
                    "select run_row.* from platform_control.mission_runs run_row "
                    "join platform_control.missions mission "
                    "on mission.mission_id=run_row.mission_id "
                    "where run_row.mission_id=%s and mission.owner_internal_user_id=%s "
                    "order by run_row.created_at,run_row.run_id",
                    (mission_id, internal_user_id),
                ).fetchall()
            return tuple(self._run_from_row(row) for row in rows)
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    @staticmethod
    def _append_event_locked(
        cursor: Any,
        mission_id: UUID,
        run_id: UUID | None,
        event_id: UUID,
        event_type: str,
        sealed: SealedContent,
    ) -> dict[str, Any]:
        sequence = cursor.execute(
            "select coalesce(max(seq),0)+1 as next_seq "
            "from platform_control.mission_events where mission_id=%s",
            (mission_id,),
        ).fetchone()["next_seq"]
        inserted = cursor.execute(
            "insert into platform_control.mission_events "
            "(event_id,mission_id,run_id,seq,event_type,payload_ciphertext,"
            "encryption_key_version) values (%s,%s,%s,%s,%s,%s,%s) "
            "returning created_at",
            (
                event_id,
                mission_id,
                run_id,
                sequence,
                event_type,
                sealed.ciphertext,
                sealed.key_version,
            ),
        ).fetchone()
        return {"seq": sequence, "created_at": inserted["created_at"]}

    def append_event(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        event_type: str,
        payload: dict[str, object],
        *,
        run_id: UUID | None = None,
    ) -> MissionEvent:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        if run_id is not None:
            _require_uuid(run_id)
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Mission event type invalid")
        if event_type != "agent.progress" or run_id is None:
            raise MissionRepositoryConflict()
        event_id = uuid4()
        try:
            normalized_payload, _canonical = _canonical_payload(
                payload, event_type=event_type
            )
            sealed = self.content_codec.seal_json(
                _event_subject(mission_id, event_id), normalized_payload
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    raise MissionRepositoryConflict()
                run = cursor.execute(
                    "select phase,agent_id,status from platform_control.mission_runs "
                    "where mission_id=%s and run_id=%s for update",
                    (mission_id, run_id),
                ).fetchone()
                if run is None:
                    raise MissionRepositoryNotFound()
                if (
                    run["phase"] not in {"professional", "direct"}
                    or run["status"] not in {"queued", "running"}
                ):
                    raise MissionRepositoryConflict()
                _require_event_agent(normalized_payload, run["agent_id"])
                inserted = self._append_event_locked(
                    cursor, mission_id, run_id, event_id, event_type, sealed
                )
            return MissionEvent(
                event_id=event_id,
                mission_id=mission_id,
                run_id=run_id,
                seq=inserted["seq"],
                event_type=event_type,
                payload=normalized_payload,
                created_at=inserted["created_at"],
            )
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def _run_from_row(self, row: dict[str, Any]) -> MissionRun:
        input_payload = self.content_codec.unseal_json(
            _run_subject(row["mission_id"], row["run_id"], "input"),
            SealedContent(
                bytes(row["input_ciphertext"]), row["encryption_key_version"]
            ),
        )
        input_payload, _input_canonical = _canonical_payload(input_payload)
        output_payload = None
        if row["output_ciphertext"] is not None:
            output_payload = self.content_codec.unseal_json(
                _run_subject(row["mission_id"], row["run_id"], "output"),
                SealedContent(
                    bytes(row["output_ciphertext"]),
                    row["output_encryption_key_version"],
                ),
            )
            output_payload, _output_canonical = _canonical_payload(output_payload)
        return MissionRun(
            run_id=row["run_id"],
            mission_id=row["mission_id"],
            task_id=row["task_id"],
            phase=row["phase"],
            agent_id=row["agent_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            terminal_at=row["terminal_at"],
            relay_event_cursor=row["relay_event_cursor"],
            input_payload=input_payload,
            output_payload=output_payload,
        )

    def _existing_phase_run(
        self,
        cursor: Any,
        mission_id: UUID,
        phase: str,
        *,
        agent_id: str,
        input_payload: dict[str, object],
        objective: str | None,
        event_type: str,
        event_payload: dict[str, object],
    ) -> MissionRun | None:
        rows = cursor.execute(
            "select run_row.*,task.objective_ciphertext,"
            "task.encryption_key_version as objective_key_version,"
            "initial_event.event_id as initial_event_id,"
            "initial_event.event_type as initial_event_type,"
            "initial_event.payload_ciphertext as initial_event_ciphertext,"
            "initial_event.encryption_key_version as initial_event_key_version "
            "from platform_control.mission_runs run_row "
            "left join platform_control.mission_tasks task "
            "on task.mission_id=run_row.mission_id "
            "and task.task_id=run_row.task_id "
            "left join lateral (select event_id,event_type,payload_ciphertext,"
            "encryption_key_version from platform_control.mission_events "
            "where mission_id=run_row.mission_id and run_id=run_row.run_id "
            "order by seq limit 1) initial_event on true "
            "where run_row.mission_id=%s and run_row.phase=%s "
            "order by run_row.created_at,run_row.run_id for update of run_row",
            (mission_id, phase),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise MissionRepositoryConflict()
        row = rows[0]
        recovered = self._run_from_row(row)
        recovered_objective = None
        if recovered.task_id is not None:
            objective_value = self.content_codec.unseal_json(
                _task_subject(mission_id, recovered.task_id),
                SealedContent(
                    bytes(row["objective_ciphertext"]),
                    row["objective_key_version"],
                ),
            )
            if (
                set(objective_value) != {"text"}
                or not isinstance(objective_value["text"], str)
            ):
                raise ContentCryptoError("content decrypt failed")
            recovered_objective = objective_value["text"]
        if row["initial_event_id"] is None:
            raise MissionRepositoryError()
        recovered_event = self.content_codec.unseal_json(
            _event_subject(mission_id, row["initial_event_id"]),
            SealedContent(
                bytes(row["initial_event_ciphertext"]),
                row["initial_event_key_version"],
            ),
        )
        recovered_event, recovered_event_canonical = _canonical_payload(
            recovered_event, event_type=row["initial_event_type"]
        )
        _recovered_input, recovered_input_canonical = _canonical_payload(
            recovered.input_payload
        )
        _caller_input, caller_input_canonical = _canonical_payload(input_payload)
        _caller_event, caller_event_canonical = _canonical_payload(
            event_payload, event_type=event_type
        )
        if (
            recovered.agent_id != agent_id
            or recovered_input_canonical != caller_input_canonical
            or recovered_objective != objective
            or row["initial_event_type"] != event_type
            or recovered_event_canonical != caller_event_canonical
        ):
            raise MissionRepositoryConflict()
        return recovered

    def create_run(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        *,
        phase: Literal["summary", "planning", "professional", "synthesis", "direct"],
        agent_id: str,
        input_payload: dict[str, object],
        event_type: str,
        event_payload: dict[str, object],
        objective: str | None = None,
        expected_mission_status: str | None = None,
        expected_row_version: int | None = None,
    ) -> MissionRun:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        agent_id = _require_agent_id(agent_id)
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Mission event type invalid")
        _require_expected_mission(
            expected_mission_status, expected_row_version
        )
        if phase in {"summary", "planning", "synthesis"}:
            if agent_id != "agent-brain-bot" or objective is not None:
                raise ValueError("Brain run shape invalid")
            task_id = None
        elif phase in {"professional", "direct"}:
            if (
                agent_id == "agent-brain-bot"
                or not isinstance(objective, str)
                or not objective.strip()
            ):
                raise ValueError("Professional run shape invalid")
            task_id = uuid4()
        else:
            raise ValueError("Mission run phase invalid")

        run_id = uuid4()
        event_id = uuid4()
        try:
            normalized_input, _input_canonical = _canonical_payload(input_payload)
            normalized_event, _event_canonical = _canonical_payload(
                event_payload, event_type=event_type
            )
            sealed_input = self.content_codec.seal_json(
                _run_subject(mission_id, run_id, "input"), normalized_input
            )
            sealed_event = self.content_codec.seal_json(
                _event_subject(mission_id, event_id), normalized_event
            )
            sealed_objective = (
                self.content_codec.seal_json(
                    _task_subject(mission_id, task_id), {"text": objective}
                )
                if task_id is not None
                else None
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                recovered = self._existing_phase_run(
                    cursor,
                    mission_id,
                    phase,
                    agent_id=agent_id,
                    input_payload=normalized_input,
                    objective=objective,
                    event_type=event_type,
                    event_payload=normalized_event,
                )
                if recovered is not None:
                    return recovered
                if expected_mission_status is not None and (
                    mission["status"] != expected_mission_status
                    or mission["row_version"] != expected_row_version
                ):
                    raise MissionRepositoryConflict()
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    raise MissionRepositoryConflict()
                _require_locked_creation(
                    cursor, mission, mission_id, phase, event_type
                )
                if phase == "direct" and mission["direct_agent_id"] != agent_id:
                    raise MissionRepositoryConflict()
                _require_event_agent(normalized_event, agent_id)
                if task_id is not None:
                    if cursor.execute(
                        "select task_id from platform_control.mission_tasks "
                        "where mission_id=%s for update",
                        (mission_id,),
                    ).fetchone() is not None:
                        raise MissionRepositoryConflict()
                    cursor.execute(
                        "insert into platform_control.mission_tasks "
                        "(task_id,mission_id,agent_id,objective_ciphertext,"
                        "encryption_key_version,status) "
                        "values (%s,%s,%s,%s,%s,'queued')",
                        (
                            task_id,
                            mission_id,
                            agent_id,
                            sealed_objective.ciphertext,
                            sealed_objective.key_version,
                        ),
                    )
                inserted = cursor.execute(
                    "insert into platform_control.mission_runs "
                    "(run_id,mission_id,task_id,phase,agent_id,status,input_ciphertext,"
                    "encryption_key_version) values (%s,%s,%s,%s,%s,'queued',%s,%s) "
                    "returning created_at,updated_at",
                    (
                        run_id,
                        mission_id,
                        task_id,
                        phase,
                        agent_id,
                        sealed_input.ciphertext,
                        sealed_input.key_version,
                    ),
                ).fetchone()
                if phase in {"professional", "direct"}:
                    next_status = "delegated"
                elif phase == "synthesis":
                    next_status = "synthesizing"
                else:
                    next_status = mission["status"]
                cursor.execute(
                    "update platform_control.missions set status=%s,row_version="
                    "row_version+1,updated_at=now() where mission_id=%s",
                    (next_status, mission_id),
                )
                self._append_event_locked(
                    cursor,
                    mission_id,
                    run_id,
                    event_id,
                    event_type,
                    sealed_event,
                )
            return MissionRun(
                run_id=run_id,
                mission_id=mission_id,
                task_id=task_id,
                phase=phase,
                agent_id=agent_id,
                status="queued",
                created_at=inserted["created_at"],
                updated_at=inserted["updated_at"],
                started_at=None,
                terminal_at=None,
                relay_event_cursor=0,
                input_payload=normalized_input,
            )
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    @staticmethod
    def _relay_progress_payload(
        event: RelayEvent, agent_id: str
    ) -> dict[str, object] | None:
        if event.event_type != "agent.state":
            return None
        payload: dict[str, object] = {"agent_id": agent_id}
        for key in ("state", "stage"):
            value = event.payload.get(key)
            if (
                type(value) is str
                and value.strip()
                and _PUBLIC_PROGRESS_CODE.fullmatch(value) is not None
            ):
                try:
                    if len(value.encode("utf-8")) <= 128:
                        payload[key] = value
                except UnicodeError:
                    pass
        progress = event.payload.get("progress")
        if type(progress) in {int, float} and 0 <= progress <= 1:
            payload["progress"] = progress
        for key in ("current", "total"):
            value = event.payload.get(key)
            minimum = 1 if key == "total" else 0
            if type(value) is int and value >= minimum:
                payload[key] = value
        if set(payload) == {"agent_id"}:
            return None
        if {"current", "total"} <= payload.keys() and payload["current"] > payload["total"]:
            payload.pop("current")
            payload.pop("total")
        return payload

    def apply_relay_events(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        run_id: UUID,
        events: tuple[RelayEvent, ...],
    ) -> int:
        """Bridge new Relay events once, persisting only the public projection."""

        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        _require_uuid(run_id)
        if not isinstance(events, tuple) or any(
            not isinstance(event, RelayEvent) for event in events
        ):
            raise ValueError("Relay events invalid")
        allowed_types = {
            "agent.state",
            "agent.question",
            "agent.file",
            "agent.log",
            "agent.complete",
            "agent.error",
        }
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                run = cursor.execute(
                    "select * from platform_control.mission_runs "
                    "where mission_id=%s and run_id=%s for update",
                    (mission_id, run_id),
                ).fetchone()
                if run is None:
                    raise MissionRepositoryNotFound()
                cursor_value = run["relay_event_cursor"]
                new_events = [event for event in events if event.seq > cursor_value]
                expected = cursor_value + 1
                for event in new_events:
                    if (
                        event.run_id != run_id
                        or event.seq != expected
                        or event.event_type not in allowed_types
                    ):
                        raise MissionRepositoryConflict()
                    expected += 1
                if not new_events:
                    return 0
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    raise MissionRepositoryConflict()
                if run["status"] in TERMINAL_RUN_STATUSES:
                    raise MissionRepositoryConflict()

                started = run["status"] == "running"
                public_phase = run["phase"] in {"professional", "direct"}
                public_accepted = False
                if public_phase:
                    accepted_row = cursor.execute(
                        "select exists(select 1 from "
                        "platform_control.mission_events where mission_id=%s "
                        "and run_id=%s and event_type='agent.accepted') as accepted",
                        (mission_id, run_id),
                    ).fetchone()
                    if (
                        accepted_row is None
                        or type(accepted_row["accepted"]) is not bool
                    ):
                        raise MissionRepositoryError()
                    public_accepted = accepted_row["accepted"]
                for event in new_events:
                    if event.event_type in {"agent.complete", "agent.error"}:
                        continue
                    public_state = public_phase and event.event_type == "agent.state"
                    public_payload = (
                        self._relay_progress_payload(event, run["agent_id"])
                        if public_phase
                        else None
                    )
                    if not started:
                        started = True
                    if public_state and not public_accepted:
                        event_id = uuid4()
                        payload = {
                            "agent_id": run["agent_id"],
                            "text": "专业 Agent 已开始执行",
                        }
                        normalized, _canonical = _canonical_payload(
                            payload, event_type="agent.accepted"
                        )
                        sealed = self.content_codec.seal_json(
                            _event_subject(mission_id, event_id), normalized
                        )
                        self._append_event_locked(
                            cursor,
                            mission_id,
                            run_id,
                            event_id,
                            "agent.accepted",
                            sealed,
                        )
                        public_accepted = True
                    if public_payload is not None:
                        event_id = uuid4()
                        normalized, _canonical = _canonical_payload(
                            public_payload, event_type="agent.progress"
                        )
                        sealed = self.content_codec.seal_json(
                            _event_subject(mission_id, event_id), normalized
                        )
                        self._append_event_locked(
                            cursor,
                            mission_id,
                            run_id,
                            event_id,
                            "agent.progress",
                            sealed,
                        )

                run_status = "running" if started else run["status"]
                cursor.execute(
                    "update platform_control.mission_runs set status=%s,"
                    "started_at=case when %s then coalesce(started_at,now()) "
                    "else started_at end,relay_event_cursor=%s,updated_at=now() "
                    "where run_id=%s",
                    (run_status, started, new_events[-1].seq, run_id),
                )
                if run["task_id"] is not None and started:
                    cursor.execute(
                        "update platform_control.mission_tasks set status='running',"
                        "started_at=coalesce(started_at,now()),updated_at=now() "
                        "where mission_id=%s and task_id=%s",
                        (mission_id, run["task_id"]),
                    )
            return len(new_events)
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def complete_run(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        run_id: UUID,
        *,
        status: Literal["completed", "failed", "cancelled", "interrupted"],
        output_payload: dict[str, object],
        event_type: str,
        event_payload: dict[str, object],
        mission_status: str | None = None,
        expected_mission_status: str | None = None,
        expected_row_version: int | None = None,
    ) -> MissionRun:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        _require_uuid(run_id)
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError("Mission run terminal status invalid")
        if mission_status is not None and mission_status not in _MISSION_STATUSES:
            raise ValueError("Mission status invalid")
        _require_expected_mission(
            expected_mission_status, expected_row_version
        )
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Mission event type invalid")
        event_id = uuid4()
        try:
            normalized_output, _output_canonical = _canonical_payload(output_payload)
            normalized_event, _event_canonical = _canonical_payload(
                event_payload, event_type=event_type
            )
            sealed_output = self.content_codec.seal_json(
                _run_subject(mission_id, run_id, "output"), normalized_output
            )
            sealed_event = self.content_codec.seal_json(
                _event_subject(mission_id, event_id), normalized_event
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                if expected_mission_status is not None and (
                    mission["status"] != expected_mission_status
                    or mission["row_version"] != expected_row_version
                ):
                    raise MissionRepositoryConflict()
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    raise MissionRepositoryConflict()
                run = cursor.execute(
                    "select run_id,mission_id,task_id,phase,agent_id,status,"
                    "input_ciphertext,encryption_key_version,created_at,updated_at,"
                    "started_at,terminal_at,relay_event_cursor "
                    "from platform_control.mission_runs "
                    "where mission_id=%s and run_id=%s for update",
                    (mission_id, run_id),
                ).fetchone()
                if run is None:
                    raise MissionRepositoryNotFound()
                if run["status"] in TERMINAL_RUN_STATUSES:
                    raise MissionRepositoryConflict()
                _require_locked_completion(
                    mission, run, status, mission_status, event_type
                )
                _require_event_agent(normalized_event, run["agent_id"])
                updated = cursor.execute(
                    "update platform_control.mission_runs set status=%s,"
                    "output_ciphertext=%s,output_encryption_key_version=%s,"
                    "updated_at=now(),terminal_at=now() where run_id=%s "
                    "returning updated_at,terminal_at",
                    (
                        status,
                        sealed_output.ciphertext,
                        sealed_output.key_version,
                        run_id,
                    ),
                ).fetchone()
                if run["task_id"] is not None:
                    cursor.execute(
                        "update platform_control.mission_tasks set status=%s,"
                        "updated_at=now(),terminal_at=now() where mission_id=%s "
                        "and task_id=%s",
                        (status, mission_id, run["task_id"]),
                    )
                next_mission_status = mission_status or mission["status"]
                terminal = next_mission_status in TERMINAL_MISSION_STATUSES
                cursor.execute(
                    "update platform_control.missions set status=%s,"
                    "row_version=row_version+1,updated_at=now(),"
                    "terminal_at=case when %s then now() else null end "
                    "where mission_id=%s",
                    (next_mission_status, terminal, mission_id),
                )
                self._append_event_locked(
                    cursor,
                    mission_id,
                    run_id,
                    event_id,
                    event_type,
                    sealed_event,
                )
                input_value = self.content_codec.unseal_json(
                    _run_subject(mission_id, run_id, "input"),
                    SealedContent(
                        bytes(run["input_ciphertext"]), run["encryption_key_version"]
                    ),
                )
                input_value, _input_canonical = _canonical_payload(input_value)
            return MissionRun(
                run_id=run_id,
                mission_id=mission_id,
                task_id=run["task_id"],
                phase=run["phase"],
                agent_id=run["agent_id"],
                status=status,
                created_at=run["created_at"],
                updated_at=updated["updated_at"],
                started_at=run["started_at"],
                terminal_at=updated["terminal_at"],
                relay_event_cursor=run["relay_event_cursor"],
                input_payload=input_value,
                output_payload=normalized_output,
            )
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def events_after(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        *,
        after: int = 0,
        limit: int = 500,
    ) -> tuple[MissionEvent, ...]:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
            raise ValueError("Mission event cursor invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                owned = cursor.execute(
                    "select mission_id from platform_control.missions "
                    "where mission_id=%s and owner_internal_user_id=%s",
                    (mission_id, internal_user_id),
                ).fetchone()
                if owned is None:
                    raise MissionRepositoryNotFound()
                rows = cursor.execute(
                    "select event.event_id,event.mission_id,event.run_id,event.seq,"
                    "event.event_type,event.payload_ciphertext,"
                    "event.encryption_key_version,event.created_at "
                    "from platform_control.mission_events event "
                    "join platform_control.missions mission "
                    "on mission.mission_id=event.mission_id "
                    "where event.mission_id=%s and mission.owner_internal_user_id=%s "
                    "and event.seq>%s order by event.seq limit %s",
                    (mission_id, internal_user_id, after, limit),
                ).fetchall()
            events: list[MissionEvent] = []
            for row in rows:
                payload = self.content_codec.unseal_json(
                    _event_subject(row["mission_id"], row["event_id"]),
                    SealedContent(
                        bytes(row["payload_ciphertext"]),
                        row["encryption_key_version"],
                    ),
                )
                payload, _canonical = _canonical_payload(
                    payload, event_type=row["event_type"]
                )
                events.append(
                    MissionEvent(
                        event_id=row["event_id"],
                        mission_id=row["mission_id"],
                        run_id=row["run_id"],
                        seq=row["seq"],
                        event_type=row["event_type"],
                        payload=payload,
                        created_at=row["created_at"],
                    )
                )
            return tuple(events)
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None

    def terminal_delivery_for_projection(
        self, cursor: Any, mission_id: UUID
    ) -> tuple[str, str]:
        """Read the final public Mission event inside a projection transaction."""

        _require_uuid(mission_id)
        try:
            row = cursor.execute(
                "select event_id,mission_id,event_type,payload_ciphertext,"
                "encryption_key_version from platform_control.mission_events "
                "where mission_id=%s and event_type in ("
                "'mission.completed','mission.failed','mission.cancelled',"
                "'mission.interrupted') order by seq desc limit 1",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionRepositoryError()
            payload = self.content_codec.unseal_json(
                _event_subject(row["mission_id"], row["event_id"]),
                SealedContent(
                    bytes(row["payload_ciphertext"]),
                    row["encryption_key_version"],
                ),
            )
            payload, _canonical = _canonical_payload(
                payload, event_type=row["event_type"]
            )
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise MissionRepositoryError()
            return row["event_type"], text
        except MissionRepositoryError:
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
            raise MissionRepositoryError() from None
