from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from app.control_plane.dsn import validate_control_dsn

from .content_crypto import ContentCodec, ContentCryptoError, SealedContent
from .models import RelayEvent, RelayJobPayload, RelayLease


TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)
ACTIVE_STATUSES = frozenset({"leased", "dispatched", "running"})
TerminalStatus = Literal["completed", "failed", "cancelled", "interrupted"]


@dataclass(frozen=True)
class RelayJobState:
    run_id: UUID
    status: str
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    lease_expires_at: datetime | None
    terminal_at: datetime | None
    database_now: datetime


class ExecutionRelayError(RuntimeError):
    """Stable relay boundary error without identifiers or SQL details."""


class ExecutionRelayConflict(ExecutionRelayError):
    def __init__(self) -> None:
        super().__init__("execution relay conflict")


class ExecutionRelayNotFound(ExecutionRelayError):
    def __init__(self) -> None:
        super().__init__("execution relay resource not found")


class ExecutionRelayWorkerUnavailable(ExecutionRelayError):
    def __init__(self) -> None:
        super().__init__("execution relay worker unavailable")


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _same_datetime(first: datetime, second: datetime) -> bool:
    def normalized(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return normalized(first) == normalized(second)


class ExecutionRelayRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        self._control_database_url = control_database_url
        self._connect = connect
        self.content_codec = content_codec

    def __repr__(self) -> str:
        return (
            "ExecutionRelayRepository(control_database_url=<redacted>, "
            "content_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    @staticmethod
    def _active_worker(cursor, worker_id: str) -> tuple[str, ...]:
        try:
            cursor.execute(
                "select platform_control.touch_execution_worker_v27(%s)",
                (worker_id,),
            )
        except psycopg.errors.CheckViolation:
            raise ExecutionRelayWorkerUnavailable() from None
        row = cursor.execute(
            "select allowed_agent_ids "
            "from platform_control.execution_workers "
            "where worker_id=%s and status='active'",
            (worker_id,),
        ).fetchone()
        if row is None:
            raise ExecutionRelayWorkerUnavailable()
        return tuple(row["allowed_agent_ids"])

    @staticmethod
    def _job_for_update(cursor, run_id: UUID) -> dict[str, Any] | None:
        return cursor.execute(
            "select job_id,run_id,status,lease_worker_id,cancel_requested "
            "from platform_control.execution_jobs where run_id=%s for update",
            (run_id,),
        ).fetchone()

    @staticmethod
    def _owned_job(
        cursor, worker_id: str, run_id: UUID
    ) -> dict[str, Any]:
        row = ExecutionRelayRepository._job_for_update(cursor, run_id)
        if row is None:
            raise ExecutionRelayNotFound()
        if row["status"] == "queued":
            raise ExecutionRelayConflict()
        if row["lease_worker_id"] != worker_id:
            raise ExecutionRelayNotFound()
        return row

    def enqueue(self, payload: RelayJobPayload) -> UUID:
        job_id = uuid4()
        try:
            sealed = self.content_codec.seal_json(
                f"execution-job:{job_id}:{payload.run_id}",
                payload.model_dump(mode="json"),
            )
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "insert into platform_control.execution_jobs "
                    "(job_id,run_id,agent_id,payload_ciphertext,"
                    "encryption_key_version,status) "
                    "values (%s,%s,%s,%s,%s,'queued')",
                    (
                        job_id,
                        payload.run_id,
                        payload.agent_id,
                        sealed.ciphertext,
                        sealed.key_version,
                    ),
                )
            return job_id
        except ContentCryptoError:
            raise ExecutionRelayError("execution relay unavailable") from None
        except psycopg.errors.UniqueViolation:
            raise ExecutionRelayConflict() from None
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def lease(
        self,
        worker_id: str,
        allowed_agents: tuple[str, ...],
        lease_seconds: int,
    ) -> RelayLease | None:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
        ):
            raise ValueError("lease seconds invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                worker_agents = self._active_worker(cursor, worker_id)
                permitted = tuple(
                    agent for agent in worker_agents if agent in allowed_agents
                )
                if not permitted:
                    return None
                row = cursor.execute(
                    "select job_id,run_id,payload_ciphertext,"
                    "encryption_key_version,cancel_requested "
                    "from platform_control.execution_jobs "
                    "where status='queued' and agent_id=any(%s) "
                    "order by created_at,job_id for update skip locked limit 1",
                    (list(permitted),),
                ).fetchone()
                if row is None:
                    return None
                updated = cursor.execute(
                    "update platform_control.execution_jobs set "
                    "status='leased',lease_worker_id=%s,"
                    "lease_expires_at=now()+(%s * interval '1 second'),"
                    "updated_at=now() where job_id=%s "
                    "returning lease_expires_at",
                    (worker_id, lease_seconds, row["job_id"]),
                ).fetchone()
                value = self.content_codec.unseal_json(
                    f"execution-job:{row['job_id']}:{row['run_id']}",
                    SealedContent(
                        bytes(row["payload_ciphertext"]),
                        row["encryption_key_version"],
                    ),
                )
                return RelayLease(
                    job_id=row["job_id"],
                    payload=RelayJobPayload.model_validate(value),
                    lease_expires_at=updated["lease_expires_at"],
                    cancel_requested=row["cancel_requested"],
                )
        except ExecutionRelayError:
            raise
        except ValidationError:
            raise ExecutionRelayError("execution relay unavailable") from None
        except ContentCryptoError:
            raise ExecutionRelayError("execution relay unavailable") from None
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def lease_acceptance(
        self,
        worker_id: str,
        allowed_agents: tuple[str, ...],
        lease_seconds: int,
        run_id: UUID,
    ) -> RelayLease:
        if (
            not isinstance(worker_id, str)
            or re.fullmatch(r"relay-acceptance-[0-9a-f]{16}", worker_id) is None
            or not isinstance(run_id, UUID)
            or isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
        ):
            raise ExecutionRelayConflict()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                worker_agents = self._active_worker(cursor, worker_id)
                permitted = tuple(
                    agent for agent in worker_agents if agent in allowed_agents
                )
                row = cursor.execute(
                    "select job_id,run_id,agent_id,payload_ciphertext,"
                    "encryption_key_version,cancel_requested,status "
                    "from platform_control.execution_jobs "
                    "where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if (
                    row is None
                    or row["status"] != "queued"
                    or row["agent_id"] not in permitted
                ):
                    raise ExecutionRelayConflict()
                value = self.content_codec.unseal_json(
                    f"execution-job:{row['job_id']}:{row['run_id']}",
                    SealedContent(
                        bytes(row["payload_ciphertext"]),
                        row["encryption_key_version"],
                    ),
                )
                payload = RelayJobPayload.model_validate(value)
                if (
                    payload.run_id != run_id
                    or payload.agent_id != row["agent_id"]
                    or payload.prompt
                    != f"relay acceptance synthetic run {run_id}"
                ):
                    raise ExecutionRelayConflict()
                updated = cursor.execute(
                    "update platform_control.execution_jobs set "
                    "status='leased',lease_worker_id=%s,"
                    "lease_expires_at=now()+(%s * interval '1 second'),"
                    "updated_at=now() where job_id=%s and status='queued' "
                    "returning lease_expires_at",
                    (worker_id, lease_seconds, row["job_id"]),
                ).fetchone()
                if updated is None:
                    raise ExecutionRelayConflict()
                return RelayLease(
                    job_id=row["job_id"],
                    payload=payload,
                    lease_expires_at=updated["lease_expires_at"],
                    cancel_requested=row["cancel_requested"],
                )
        except ExecutionRelayError:
            raise
        except ValidationError:
            raise ExecutionRelayError("execution relay unavailable") from None
        except ContentCryptoError:
            raise ExecutionRelayError("execution relay unavailable") from None
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def mark_dispatched(self, worker_id: str, run_id: UUID) -> None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._active_worker(cursor, worker_id)
                row = self._owned_job(cursor, worker_id, run_id)
                if row["status"] == "leased":
                    cursor.execute(
                        "update platform_control.execution_jobs "
                        "set status='dispatched',updated_at=now() where run_id=%s",
                        (run_id,),
                    )
                elif row["status"] not in {
                    "dispatched",
                    "running",
                    *TERMINAL_STATUSES,
                }:
                    raise ExecutionRelayConflict()
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def _same_event(self, row: dict[str, Any], event: RelayEvent) -> bool:
        stored_payload = self.content_codec.unseal_json(
            f"execution-event:{event.run_id}:{event.seq}",
            SealedContent(
                bytes(row["payload_ciphertext"]),
                row["encryption_key_version"],
            ),
        )
        return (
            row["event_type"] == event.event_type
            and _same_datetime(row["created_at"], event.created_at)
            and _canonical_json(stored_payload) == _canonical_json(event.payload)
        )

    def append_events(
        self, worker_id: str, events: tuple[RelayEvent, ...]
    ) -> int:
        if not events:
            return 0
        run_id = events[0].run_id
        sequence = tuple(event.seq for event in events)
        if (
            any(event.run_id != run_id for event in events)
            or sequence != tuple(sorted(sequence))
            or len(sequence) != len(set(sequence))
        ):
            raise ExecutionRelayConflict()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._active_worker(cursor, worker_id)
                job = self._owned_job(cursor, worker_id, run_id)
                existing_rows = cursor.execute(
                    "select seq,event_type,payload_ciphertext,"
                    "encryption_key_version,created_at "
                    "from platform_control.execution_events "
                    "where run_id=%s and seq=any(%s)",
                    (run_id, list(sequence)),
                ).fetchall()
                existing = {row["seq"]: row for row in existing_rows}
                maximum_row = cursor.execute(
                    "select coalesce(max(seq),0) as maximum "
                    "from platform_control.execution_events where run_id=%s",
                    (run_id,),
                ).fetchone()
                expected = maximum_row["maximum"] + 1
                new_events: list[RelayEvent] = []
                for event in events:
                    stored = existing.get(event.seq)
                    if stored is not None:
                        if not self._same_event(stored, event):
                            raise ExecutionRelayConflict()
                        continue
                    if event.seq != expected:
                        raise ExecutionRelayConflict()
                    expected += 1
                    new_events.append(event)
                if job["status"] in TERMINAL_STATUSES and new_events:
                    raise ExecutionRelayConflict()
                if job["status"] not in ACTIVE_STATUSES | TERMINAL_STATUSES:
                    raise ExecutionRelayConflict()

                inserted = 0
                for event in new_events:
                    sealed = self.content_codec.seal_json(
                        f"execution-event:{event.run_id}:{event.seq}",
                        event.payload,
                    )
                    inserted_row = cursor.execute(
                        "insert into platform_control.execution_events "
                        "(run_id,seq,event_type,payload_ciphertext,"
                        "encryption_key_version,created_at) "
                        "values (%s,%s,%s,%s,%s,%s) "
                        "on conflict (run_id,seq) do nothing returning seq",
                        (
                            event.run_id,
                            event.seq,
                            event.event_type,
                            sealed.ciphertext,
                            sealed.key_version,
                            event.created_at,
                        ),
                    ).fetchone()
                    if inserted_row is not None:
                        inserted += 1
                        continue
                    concurrent = cursor.execute(
                        "select seq,event_type,payload_ciphertext,"
                        "encryption_key_version,created_at "
                        "from platform_control.execution_events "
                        "where run_id=%s and seq=%s",
                        (event.run_id, event.seq),
                    ).fetchone()
                    if concurrent is None or not self._same_event(
                        concurrent, event
                    ):
                        raise ExecutionRelayConflict()

                if job["status"] in {"leased", "dispatched"}:
                    cursor.execute(
                        "update platform_control.execution_jobs "
                        "set status='running',updated_at=now() where run_id=%s",
                        (run_id,),
                    )
                elif job["status"] == "running" and inserted:
                    cursor.execute(
                        "update platform_control.execution_jobs "
                        "set updated_at=now() where run_id=%s",
                        (run_id,),
                    )
                return inserted
        except ExecutionRelayError:
            raise
        except ContentCryptoError:
            raise ExecutionRelayError("execution relay unavailable") from None
        except (TypeError, ValueError, UnicodeError):
            raise ExecutionRelayConflict() from None
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def request_cancel(self, run_id: UUID) -> bool:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                current = cursor.execute(
                    "select status from platform_control.execution_jobs "
                    "where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if current is None or current["status"] in TERMINAL_STATUSES:
                    return False
                if current["status"] == "queued":
                    row = cursor.execute(
                        "update platform_control.execution_jobs set "
                        "status='cancelled',cancel_requested=true,"
                        "terminal_at=now(),updated_at=now() "
                        "where run_id=%s and status='queued' returning run_id",
                        (run_id,),
                    ).fetchone()
                else:
                    row = cursor.execute(
                        "update platform_control.execution_jobs "
                        "set cancel_requested=true,updated_at=now() "
                        "where run_id=%s and status=any(%s) returning run_id",
                        (run_id, ["leased", "dispatched", "running"]),
                    ).fetchone()
            return row is not None
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def job_state(
        self,
        run_id: UUID,
        *,
        queued_deadline_seconds: int = 60,
        running_deadline_seconds: int = 300,
    ) -> RelayJobState:
        """Return relay state for the trusted orchestrator only.

        This method is intentionally not used by the worker router.  The
        worker continues to access jobs exclusively through its device-auth
        lease and upload operations.
        """

        if (
            not isinstance(run_id, UUID)
            or isinstance(queued_deadline_seconds, bool)
            or not isinstance(queued_deadline_seconds, int)
            or queued_deadline_seconds <= 0
            or isinstance(running_deadline_seconds, bool)
            or not isinstance(running_deadline_seconds, int)
            or running_deadline_seconds <= 0
        ):
            raise ExecutionRelayNotFound()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select run_id,status,cancel_requested,created_at,updated_at,"
                    "lease_expires_at,terminal_at,now() as database_now "
                    "from platform_control.execution_jobs where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise ExecutionRelayNotFound()
                queued_expired = (
                    row["status"] == "queued"
                    and row["created_at"]
                    <= row["database_now"]
                    - timedelta(seconds=queued_deadline_seconds)
                )
                running_expired = (
                    row["status"] in ACTIVE_STATUSES
                    and (
                        (
                            row["lease_expires_at"] is not None
                            and row["lease_expires_at"] <= row["database_now"]
                        )
                        or row["updated_at"]
                        <= row["database_now"]
                        - timedelta(seconds=running_deadline_seconds)
                    )
                )
                if queued_expired or running_expired:
                    row = cursor.execute(
                        "update platform_control.execution_jobs set "
                        "status='interrupted',cancel_requested=true,"
                        "terminal_at=now(),updated_at=now() where run_id=%s "
                        "and status=any(%s) returning run_id,status,"
                        "cancel_requested,created_at,updated_at,lease_expires_at,"
                        "terminal_at,now() as database_now",
                        (run_id, ["queued", "leased", "dispatched", "running"]),
                    ).fetchone()
                    if row is None:
                        raise ExecutionRelayConflict()
            return RelayJobState(**row)
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def events(self, run_id: UUID) -> tuple[RelayEvent, ...]:
        """Decrypt ordered relay events for Mission orchestration only."""

        if not isinstance(run_id, UUID):
            raise ExecutionRelayNotFound()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                job = cursor.execute(
                    "select run_id from platform_control.execution_jobs "
                    "where run_id=%s",
                    (run_id,),
                ).fetchone()
                if job is None:
                    raise ExecutionRelayNotFound()
                rows = cursor.execute(
                    "select seq,event_type,payload_ciphertext,"
                    "encryption_key_version,created_at "
                    "from platform_control.execution_events where run_id=%s "
                    "order by seq",
                    (run_id,),
                ).fetchall()
            events: list[RelayEvent] = []
            expected = 1
            for row in rows:
                if row["seq"] != expected:
                    raise ExecutionRelayError("execution relay unavailable")
                value = self.content_codec.unseal_json(
                    f"execution-event:{run_id}:{row['seq']}",
                    SealedContent(
                        bytes(row["payload_ciphertext"]),
                        row["encryption_key_version"],
                    ),
                )
                events.append(
                    RelayEvent(
                        run_id=run_id,
                        seq=row["seq"],
                        event_type=row["event_type"],
                        created_at=row["created_at"],
                        payload=value,
                    )
                )
                expected += 1
            return tuple(events)
        except ExecutionRelayError:
            raise
        except (ContentCryptoError, TypeError, ValueError, ValidationError):
            raise ExecutionRelayError("execution relay unavailable") from None
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def finish(
        self,
        worker_id: str,
        run_id: UUID,
        status: TerminalStatus,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ExecutionRelayConflict()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._active_worker(cursor, worker_id)
                row = self._owned_job(cursor, worker_id, run_id)
                current = row["status"]
                if current in TERMINAL_STATUSES:
                    if current != status:
                        raise ExecutionRelayConflict()
                    return
                legal = (
                    status in {"completed", "failed"}
                    and current in {"dispatched", "running"}
                ) or (
                    status == "cancelled"
                    and current in ACTIVE_STATUSES
                    and row["cancel_requested"]
                ) or (
                    status == "interrupted" and current in ACTIVE_STATUSES
                )
                if not legal:
                    raise ExecutionRelayConflict()
                cursor.execute(
                    "update platform_control.execution_jobs "
                    "set status=%s,terminal_at=now(),updated_at=now() "
                    "where run_id=%s",
                    (status, run_id),
                )
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def heartbeat(self, worker_id: str) -> tuple[UUID, ...]:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._active_worker(cursor, worker_id)
                rows = cursor.execute(
                    "select run_id from platform_control.execution_jobs "
                    "where lease_worker_id=%s and cancel_requested=true "
                    "and status=any(%s) order by run_id::text",
                    (worker_id, ["leased", "dispatched", "running"]),
                ).fetchall()
            return tuple(row["run_id"] for row in rows)
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None
