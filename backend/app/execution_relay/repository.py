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
from .models import RelayEvent, RelayJobKind, RelayJobPayload, RelayLease


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
    job_kind: RelayJobKind = "legacy_brain"


@dataclass(frozen=True)
class RelayStopRequest:
    run_id: UUID
    status: Literal["completed", "failed", "cancelled", "interrupted"]


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
        dsn_purpose: str = "app",
    ) -> None:
        if dsn_purpose not in {"app", "brain"}:
            raise ValueError("execution relay DSN purpose invalid")
        validate_control_dsn(control_database_url, purpose=dsn_purpose)
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        self._control_database_url = control_database_url
        self._connect = connect
        self.content_codec = content_codec
        self._dsn_purpose = dsn_purpose

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
                "select platform_control.touch_execution_worker_v28(%s)",
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
            "select job_id,run_id,status,lease_worker_id,cancel_requested,"
            "stop_requested_status,stop_acknowledged_at "
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
                if self._dsn_purpose == "brain":
                    row = cursor.execute(
                        "select platform_control.enqueue_brain_relay_job_v39("
                        "%s,%s,%s,%s,%s) as job_id",
                        (
                            job_id,
                            payload.run_id,
                            payload.agent_id,
                            sealed.ciphertext,
                            sealed.key_version,
                        ),
                    ).fetchone()
                    if row is None or row["job_id"] != job_id:
                        raise ExecutionRelayError("execution relay unavailable")
                    return job_id
                linked_mission = None
                if payload.job_kind != "metabot_local":
                    linked_mission = cursor.execute(
                        "select mission.cancel_requested "
                        "from platform_control.mission_runs run_row "
                        "join platform_control.missions mission "
                        "on mission.mission_id=run_row.mission_id "
                        "where run_row.run_id=%s for update of mission",
                        (payload.run_id,),
                    ).fetchone()
                cancelled = bool(
                    linked_mission
                    and linked_mission["cancel_requested"] is True
                )
                cursor.execute(
                    "insert into platform_control.execution_jobs "
                    "(job_id,run_id,agent_id,payload_ciphertext,"
                    "encryption_key_version,status,cancel_requested,terminal_at,"
                    "job_kind) values (%s,%s,%s,%s,%s,%s,%s,"
                    "case when %s then now() else null end,%s)",
                    (
                        job_id,
                        payload.run_id,
                        payload.agent_id,
                        sealed.ciphertext,
                        sealed.key_version,
                        "cancelled" if cancelled else "queued",
                        cancelled,
                        cancelled,
                        payload.job_kind,
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
        accepted_job_kinds: tuple[RelayJobKind, ...] = (
            "legacy_brain",
            "direct_agent",
            "metabot_local",
        ),
    ) -> RelayLease | None:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
            or not accepted_job_kinds
            or len(set(accepted_job_kinds)) != len(accepted_job_kinds)
            or any(
                kind not in {"legacy_brain", "direct_agent", "metabot_local"}
                for kind in accepted_job_kinds
            )
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
                    "and job_kind=any(%s) "
                    "order by created_at,job_id for update skip locked limit 1",
                    (list(permitted), list(accepted_job_kinds)),
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
                if self._dsn_purpose == "brain":
                    row = cursor.execute(
                        "select platform_control.request_brain_relay_cancel_v39(%s) "
                        "as accepted",
                        (run_id,),
                    ).fetchone()
                    return bool(row and row["accepted"])
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
                        "set cancel_requested=true,"
                        "stop_requested_status='cancelled',"
                        "stop_acknowledged_at=null,updated_at=now() "
                        "where run_id=%s and status=any(%s) returning run_id",
                        (run_id, ["leased", "dispatched", "running"]),
                    ).fetchone()
            return row is not None
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def interrupt(self, run_id: UUID) -> bool:
        """Atomically stop a job when the trusted orchestrator must fail closed."""

        if not isinstance(run_id, UUID):
            raise ExecutionRelayNotFound()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                current = cursor.execute(
                    "select status from platform_control.execution_jobs "
                    "where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if current is None:
                    raise ExecutionRelayNotFound()
                if current["status"] in TERMINAL_STATUSES:
                    return False
                updated = cursor.execute(
                    "update platform_control.execution_jobs set "
                    "status='interrupted',cancel_requested=true,"
                    "stop_requested_status=case when lease_worker_id is null "
                    "then null else 'interrupted' end,"
                    "stop_acknowledged_at=null,"
                    "terminal_at=now(),updated_at=now() where run_id=%s "
                    "and status=any(%s) returning run_id",
                    (run_id, ["queued", "leased", "dispatched", "running"]),
                ).fetchone()
            return updated is not None
        except ExecutionRelayError:
            raise
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
                if self._dsn_purpose == "brain":
                    row = cursor.execute(
                        "select * from platform_control.brain_relay_job_state_v39(%s)",
                        (run_id,),
                    ).fetchone()
                else:
                    row = cursor.execute(
                        "select run_id,status,cancel_requested,created_at,updated_at,"
                        "lease_expires_at,terminal_at,stop_requested_status,"
                        "job_kind,"
                        "now() as database_now "
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
                if (
                    self._dsn_purpose != "brain"
                    and (queued_expired or running_expired)
                ):
                    expired_status = (
                        "cancelled"
                        if row["stop_requested_status"] == "cancelled"
                        else "interrupted"
                    )
                    row = cursor.execute(
                        "update platform_control.execution_jobs set "
                        "status=%s,cancel_requested=true,"
                        "stop_requested_status=case when lease_worker_id is null "
                        "then null else %s end,"
                        "stop_acknowledged_at=null,"
                        "terminal_at=now(),updated_at=now() where run_id=%s "
                        "and status=any(%s) returning run_id,status,"
                        "cancel_requested,created_at,updated_at,lease_expires_at,"
                        "terminal_at,now() as database_now",
                        (
                            expired_status,
                            expired_status,
                            run_id,
                            ["queued", "leased", "dispatched", "running"],
                        ),
                    ).fetchone()
                    if row is None:
                        raise ExecutionRelayConflict()
            row.pop("stop_requested_status", None)
            return RelayJobState(**row)
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def has_active_worker(
        self, agent_id: str, *, freshness_seconds: int = 60
    ) -> bool:
        if (
            not isinstance(agent_id, str)
            or not agent_id
            or isinstance(freshness_seconds, bool)
            or not isinstance(freshness_seconds, int)
            or freshness_seconds <= 0
        ):
            return False
        try:
            with self._connection() as connection:
                if self._dsn_purpose == "brain":
                    row = connection.execute(
                        "select platform_control.brain_relay_worker_available_v39("
                        "%s,%s) as available",
                        (agent_id, freshness_seconds),
                    ).fetchone()
                    return bool(row and row["available"])
                row = connection.execute(
                    "select exists(select 1 from platform_control.execution_workers "
                    "where status='active' and %s=any(allowed_agent_ids) and "
                    "last_seen_at>clock_timestamp()-(%s*interval '1 second'))",
                    (agent_id, freshness_seconds),
                ).fetchone()
            return bool(row["exists"])
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def events(self, run_id: UUID) -> tuple[RelayEvent, ...]:
        """Decrypt ordered relay events for Mission orchestration only."""

        if not isinstance(run_id, UUID):
            raise ExecutionRelayNotFound()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                if self._dsn_purpose == "brain":
                    rows = cursor.execute(
                        "select * from platform_control.brain_relay_events_v39(%s)",
                        (run_id,),
                    ).fetchall()
                else:
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
                cursor.execute(
                    "select mission.mission_id "
                    "from platform_control.mission_runs run_row "
                    "join platform_control.missions mission "
                    "on mission.mission_id=run_row.mission_id "
                    "where run_row.run_id=%s for update of mission",
                    (run_id,),
                ).fetchone()
                row = self._owned_job(cursor, worker_id, run_id)
                current = row["status"]
                requested = row["stop_requested_status"]
                if requested is not None and requested != status:
                    raise ExecutionRelayConflict()
                if current in TERMINAL_STATUSES:
                    if current != status:
                        raise ExecutionRelayConflict()
                    if requested == status and row["stop_acknowledged_at"] is None:
                        cursor.execute(
                            "update platform_control.execution_jobs set "
                            "stop_acknowledged_at=now(),updated_at=now() "
                            "where run_id=%s",
                            (run_id,),
                        )
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
                    "set status=%s,terminal_at=now(),"
                    "stop_acknowledged_at=case when stop_requested_status=%s "
                    "then now() else stop_acknowledged_at end,updated_at=now() "
                    "where run_id=%s",
                    (status, status, run_id),
                )
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def acknowledge_stop(
        self,
        worker_id: str,
        run_id: UUID,
        status: TerminalStatus,
    ) -> None:
        """Converge an assigned stop when this Worker has no local run state."""

        if status not in {"cancelled", "interrupted"}:
            raise ExecutionRelayConflict()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._active_worker(cursor, worker_id)
                row = self._owned_job(cursor, worker_id, run_id)
                if row["stop_requested_status"] != status:
                    raise ExecutionRelayConflict()
                if row["stop_acknowledged_at"] is not None:
                    if row["status"] != status:
                        raise ExecutionRelayConflict()
                    return
                current = row["status"]
                legal = (
                    status == "cancelled"
                    and row["cancel_requested"]
                    and current in {*ACTIVE_STATUSES, "cancelled"}
                ) or (
                    status == "interrupted" and current == "interrupted"
                )
                if not legal:
                    raise ExecutionRelayConflict()
                cursor.execute(
                    "update platform_control.execution_jobs set status=%s,"
                    "cancel_requested=true,terminal_at=coalesce(terminal_at,now()),"
                    "stop_acknowledged_at=now(),updated_at=now() "
                    "where run_id=%s",
                    (status, run_id),
                )
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None

    def heartbeat(
        self, worker_id: str, *, lease_seconds: int = 45
    ) -> tuple[RelayStopRequest, ...]:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
        ):
            raise ValueError("lease seconds invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._active_worker(cursor, worker_id)
                cursor.execute(
                    "update platform_control.execution_jobs set "
                    "lease_expires_at=now()+(%s * interval '1 second') "
                    "where lease_worker_id=%s and status=any(%s)",
                    (lease_seconds, worker_id, ["leased", "dispatched", "running"]),
                )
                rows = cursor.execute(
                    "select run_id,stop_requested_status as stop_status "
                    "from platform_control.execution_jobs "
                    "where lease_worker_id=%s "
                    "and stop_requested_status is not null "
                    "and stop_acknowledged_at is null "
                    "order by run_id limit 100",
                    (worker_id,),
                ).fetchall()
            return tuple(
                RelayStopRequest(
                    run_id=row["run_id"], status=row["stop_status"]
                )
                for row in rows
            )
        except ExecutionRelayError:
            raise
        except psycopg.Error:
            raise ExecutionRelayError("execution relay unavailable") from None
