"""Trusted internal Attempt ledger; public access is scoped through Conversation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec


class ActiveAttemptConflict(RuntimeError):
    pass


class AttemptNotFound(RuntimeError):
    pass


class LeaseRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class TerminalEvidence:
    status: str
    result_message_id: UUID | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("terminal status required")
        if (self.status == "completed") != (self.result_message_id is not None):
            raise ValueError("terminal result reference invalid")


@dataclass(frozen=True)
class Outcome:
    attempt_id: UUID
    status: str
    applied: bool


@dataclass(frozen=True)
class Attempt:
    attempt_id: UUID
    turn_id: UUID
    owner_id: UUID
    attempt_no: int
    executor_kind: str
    executor_id: str | None
    lease_epoch: int
    lease_expires_at: datetime | None
    status: str
    cancel_requested_at: datetime | None
    transport_run_id: UUID | None
    result_message_id: UUID | None
    retry_of_attempt_id: UUID | None
    reason_code: str | None
    created_at: datetime
    updated_at: datetime
    readiness_check_after: datetime | None = None
    capability_missing_since: datetime | None = None
    capability_alerted_at: datetime | None = None


@dataclass(frozen=True)
class Lease:
    attempt_id: UUID
    executor_kind: str
    executor_id: UUID
    lease_epoch: int
    expires_at: datetime
    status: str
    admission: dict | None = None


_SELECT = (
    "select a.*,c.owner_internal_user_id as owner_id "
    "from platform_control.turn_attempts a "
    "join platform_control.conversation_turns t using(turn_id) "
    "join platform_control.conversations c using(conversation_id) "
)


class TurnAttemptRepository:
    def __init__(self, dsn: str, codec: ContentCodec) -> None:
        self.environment = validate_control_dsn(dsn, purpose="app").environment
        if not isinstance(codec, ContentCodec):
            raise ValueError("content codec required")  # noqa: TRY004 - repository convention
        self._dsn = dsn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Short local transaction. Do not perform network work while it is open."""
        with psycopg.connect(
            self._dsn,
            row_factory=dict_row,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
        ) as connection:
            yield connection

    def create_queued(
        self,
        turn_id: UUID,
        executor_kind: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> Attempt:
        """Internal only: caller authorizes the Turn before joining its transaction."""
        if connection is None:
            with self.transaction() as owned_connection:
                return self.create_queued(
                    turn_id, executor_kind, connection=owned_connection
                )
        if connection.autocommit:
            raise ValueError("caller-owned transaction required")
        with connection.cursor(row_factory=dict_row) as cursor:
            # Match intake/idempotency lock order: Conversation -> Turn -> Attempt.
            # The first lookup supplies identity only; predicates are rechecked below.
            cursor.execute(
                "select conversation_id from platform_control.conversations "
                "where conversation_id=(select conversation_id from "
                "platform_control.conversation_turns where turn_id=%s) for update",
                (turn_id,),
            )
            turn = cursor.execute(
                "select t.turn_id,c.owner_internal_user_id from "
                "platform_control.conversation_turns t join platform_control.conversations c "
                "using(conversation_id) where t.turn_id=%s and c.execution_owner=%s "
                "and t.status in ('accepted','running') and c.status='active' "
                "and coalesce(to_jsonb(t)->>'execution_owner',c.execution_owner)=c.execution_owner "
                "and coalesce((to_jsonb(t)->>'origin_route_epoch')::bigint,c.route_epoch)=c.route_epoch "
                "for update of t",
                (turn_id, executor_kind),
            ).fetchone()
            if turn is None:
                raise AttemptNotFound("attempt turn unavailable")
            if cursor.execute(
                "select 1 from platform_control.turn_attempts where turn_id=%s "
                "and status in ('queued','running','reconciling')",
                (turn_id,),
            ).fetchone():
                raise ActiveAttemptConflict("active attempt exists")
            row = cursor.execute(
                "insert into platform_control.turn_attempts "
                "(attempt_id,turn_id,attempt_no,executor_kind,status) "
                "select %s,%s,coalesce(max(attempt_no),0)+1,%s,'queued' "
                "from platform_control.turn_attempts where turn_id=%s returning *",
                (uuid4(), turn_id, executor_kind, turn_id),
            ).fetchone()
            return Attempt(**row, owner_id=turn["owner_internal_user_id"])

    def get_for_owner(self, owner_id: UUID, attempt_id: UUID) -> Attempt:
        with self.transaction() as connection:
            row = connection.execute(
                _SELECT + "where a.attempt_id=%s and c.owner_internal_user_id=%s",
                (attempt_id, owner_id),
            ).fetchone()
            if row is None:
                raise AttemptNotFound("attempt unavailable")
            return Attempt(**row)

    def request_cancel(
        self, owner_id: UUID, turn_id: UUID
    ) -> Literal["accepted", "too_late"]:
        """Record intent; adjudication by the holder retains the live slot until terminal."""
        with self.transaction() as connection:
            row = connection.execute(
                _SELECT + "where a.turn_id=%s and c.owner_internal_user_id=%s "
                "order by a.attempt_no desc limit 1 for update of a",
                (turn_id, owner_id),
            ).fetchone()
            if row is None:
                raise AttemptNotFound("attempt unavailable")
            if row["status"] not in {"queued", "running", "reconciling"}:
                return "too_late"
            connection.execute(
                "update platform_control.turn_attempts set cancel_requested_at="
                "coalesce(cancel_requested_at,clock_timestamp()),updated_at=clock_timestamp() "
                "where attempt_id=%s",
                (row["attempt_id"],),
            )
            return "accepted"

    def claim_due(
        self,
        executor_id: UUID,
        lease_seconds: int,
        *,
        executor_kind: str = "worker_direct",
    ) -> Lease | None:
        """Only running means initial dispatch; reconciling must recover/stop, never rerun."""
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("positive integer lease seconds required")
        if not isinstance(executor_id, UUID):
            raise ValueError("executor process UUID required")  # noqa: TRY004 - repository convention
        if executor_kind == "worker_direct":
            from .direct_admission import claim_direct

            return claim_direct(self, executor_id, lease_seconds)
        with self.transaction() as connection:
            row = connection.execute(
                "with due as (select a.attempt_id from platform_control.turn_attempts a "
                "join platform_control.conversation_turns t using(turn_id) "
                "join platform_control.conversations c using(conversation_id) "
                "where a.executor_kind=%s and c.execution_owner=a.executor_kind "
                "and (a.status='queued' or (a.status in ('running','reconciling') "
                "and a.lease_expires_at<=clock_timestamp())) order by a.created_at,a.attempt_id "
                "limit 1 for update of a skip locked) "
                "update platform_control.turn_attempts a set status=case "
                "when a.status='queued' and a.cancel_requested_at is null "
                "then 'running' else 'reconciling' end,"
                "executor_id=%s,lease_epoch=a.lease_epoch+1,"
                "lease_expires_at=clock_timestamp()+make_interval(secs => %s),"
                "updated_at=clock_timestamp() from due where a.attempt_id=due.attempt_id "
                "returning a.*",
                (executor_kind, str(executor_id), lease_seconds),
            ).fetchone()
            if row is None:
                return None
            return Lease(
                row["attempt_id"],
                row["executor_kind"],
                UUID(row["executor_id"]),
                row["lease_epoch"],
                row["lease_expires_at"],
                row["status"],
            )

    def renew(
        self,
        lease: Lease,
        lease_seconds: int,
        *,
        connection: psycopg.Connection | None = None,
    ) -> Lease:
        """Extend the current holder in a short Conversation -> Turn -> Attempt transaction.

        Caller-owned transactions (including future Result publication) must acquire
        these locks in the same order. Network work must occur after commit.
        """
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("positive integer lease seconds required")
        if connection is None:
            with self.transaction() as owned_connection:
                return self.renew(lease, lease_seconds, connection=owned_connection)
        if connection.autocommit:
            raise ValueError("caller-owned transaction required")
        conversation = connection.execute(
            "select conversation_id from platform_control.conversations "
            "where conversation_id=(select t.conversation_id from "
            "platform_control.conversation_turns t join platform_control.turn_attempts a "
            "using(turn_id) where a.attempt_id=%s) for update",
            (lease.attempt_id,),
        ).fetchone()
        if conversation is None:
            raise LeaseRejected("attempt lease rejected")
        turn = connection.execute(
            "select turn_id from platform_control.conversation_turns "
            "where conversation_id=%s and turn_id=(select turn_id from "
            "platform_control.turn_attempts where attempt_id=%s) for update",
            (conversation["conversation_id"], lease.attempt_id),
        ).fetchone()
        if turn is None:
            raise LeaseRejected("attempt lease rejected")
        connection.execute(
            "select attempt_id from platform_control.turn_attempts "
            "where attempt_id=%s and turn_id=%s for update",
            (lease.attempt_id, turn["turn_id"]),
        )
        # Check the clock only after every potentially blocking lock is held.
        row = connection.execute(
            "update platform_control.turn_attempts a set "
            "lease_expires_at=clock_timestamp()+make_interval(secs => %s),"
            "updated_at=clock_timestamp() from platform_control.conversation_turns t "
            "join platform_control.conversations c using(conversation_id) "
            "where a.attempt_id=%s and a.turn_id=t.turn_id and t.turn_id=%s "
            "and c.conversation_id=%s and a.executor_kind=%s and a.executor_id=%s "
            "and a.lease_epoch=%s and a.lease_expires_at>clock_timestamp() "
            "and a.status in ('running','reconciling') and c.status='active' "
            "and c.execution_owner=a.executor_kind "
            "and coalesce(to_jsonb(t)->>'execution_owner',c.execution_owner)=a.executor_kind "
            "and coalesce((to_jsonb(t)->>'origin_route_epoch')::bigint,c.route_epoch)=c.route_epoch "
            "returning a.*",
            (
                lease_seconds, lease.attempt_id, turn["turn_id"],
                conversation["conversation_id"], lease.executor_kind,
                str(lease.executor_id), lease.lease_epoch,
            ),
        ).fetchone()
        if row is None:
            raise LeaseRejected("attempt lease rejected")
        return Lease(
            row["attempt_id"], row["executor_kind"], UUID(row["executor_id"]),
            row["lease_epoch"], row["lease_expires_at"], row["status"],
        )

    def record_terminal(
        self,
        lease: Lease,
        evidence: TerminalEvidence,
        *,
        connection: psycopg.Connection,
    ) -> Outcome:
        """Join P04 publication; caller must roll back the transaction on rejection.

        Stage Result + Turn terminal + event/intents on this connection first.
        This method never commits. An exact replay observes the recorded decision
        without claiming new adjudication authority (applied=False).
        """
        if connection.autocommit:
            raise ValueError("caller-owned transaction required")
        # UPDATE can evaluate its time predicate before waiting for a row lock.
        # Acquire the lock first, then check clock_timestamp() in a new statement.
        connection.execute(
            "select attempt_id from platform_control.turn_attempts "
            "where attempt_id=%s for update",
            (lease.attempt_id,),
        )
        if evidence.status == "completed":
            result = connection.execute(
                "select 1 from platform_control.turn_attempts a "
                "join platform_control.conversation_turns t using(turn_id) "
                "join platform_control.conversation_messages m on m.message_id=%s "
                "and m.conversation_id=t.conversation_id and m.turn_id=t.turn_id "
                "where a.attempt_id=%s and m.role='assistant' and m.delivery_status='completed' "
                "and t.status='completed' and t.assistant_message_id=m.message_id",
                (evidence.result_message_id, lease.attempt_id),
            ).fetchone()
            if result is None:
                raise LeaseRejected("matching published result required")
        row = connection.execute(
            "update platform_control.turn_attempts set status=%s,result_message_id=%s,"
            "reason_code=%s,updated_at=clock_timestamp() where attempt_id=%s "
            "and executor_kind=%s and executor_id=%s and lease_epoch=%s "
            "and lease_expires_at>clock_timestamp() and status in ('running','reconciling') "
            "and exists(select 1 from platform_control.conversation_turns t "
            "join platform_control.conversations c using(conversation_id) "
            "where t.turn_id=turn_attempts.turn_id and c.execution_owner=turn_attempts.executor_kind) "
            "returning attempt_id",
            (
                evidence.status,
                evidence.result_message_id,
                evidence.reason_code,
                lease.attempt_id,
                lease.executor_kind,
                str(lease.executor_id),
                lease.lease_epoch,
            ),
        ).fetchone()
        if row is None:
            replay = connection.execute(
                _SELECT
                + "where a.attempt_id=%s and a.executor_kind=%s and a.executor_id=%s "
                "and a.lease_epoch=%s and a.lease_expires_at>clock_timestamp() "
                "and c.execution_owner=a.executor_kind and a.status=%s "
                "and a.result_message_id is not distinct from %s "
                "and a.reason_code is not distinct from %s",
                (
                    lease.attempt_id,
                    lease.executor_kind,
                    str(lease.executor_id),
                    lease.lease_epoch,
                    evidence.status,
                    evidence.result_message_id,
                    evidence.reason_code,
                ),
            ).fetchone()
            if replay is None:
                raise LeaseRejected("attempt lease rejected")
            return Outcome(lease.attempt_id, evidence.status, False)
        return Outcome(lease.attempt_id, evidence.status, True)
