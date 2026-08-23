from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import os
from pathlib import Path
import stat
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import RelayEvent, RelayLease


_CONFLICT = "worker store conflict"
_CONFIGURATION_INVALID = "worker store configuration invalid"
_OWNER_FILE_LIMIT = 16_384
_TERMINAL_STATES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)
_FORCED_TERMINAL_STATES = frozenset({"cancelled", "interrupted"})


class WorkerStoreError(RuntimeError):
    """Stable worker-store failure that never includes protected values."""


@dataclass(frozen=True)
class WorkerRunRecovery:
    run_id: UUID
    agent_id: str
    state: str
    dispatched_at: datetime | None
    has_events: bool


def _read_owner_only_file(path: Path) -> str:
    parent_descriptor: int | None = None
    file_descriptor: int | None = None
    value: str | None = None
    failed = False
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError
        common_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        parent_descriptor = os.open(
            candidate.parent,
            common_flags | no_follow | getattr(os, "O_DIRECTORY", 0),
        )
        parent_status = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_status.st_mode)
            or stat.S_IMODE(parent_status.st_mode) != 0o700
            or parent_status.st_uid != os.geteuid()
        ):
            raise ValueError
        file_descriptor = os.open(
            candidate.name,
            common_flags | no_follow,
            dir_fd=parent_descriptor,
        )
        file_status = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(file_status.st_mode)
            or stat.S_IMODE(file_status.st_mode) != 0o600
            or file_status.st_uid != os.geteuid()
            or file_status.st_size > _OWNER_FILE_LIMIT
        ):
            raise ValueError
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(
                file_descriptor,
                min(4096, _OWNER_FILE_LIMIT + 1 - size),
            )
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _OWNER_FILE_LIMIT:
                raise ValueError
        value = b"".join(chunks).decode("utf-8").strip()
        if not value or "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError
    except (OSError, UnicodeError, TypeError, ValueError):
        failed = True
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except Exception:
                failed = True
        if parent_descriptor is not None:
            try:
                os.close(parent_descriptor)
            except Exception:
                failed = True
    if failed or value is None:
        raise WorkerStoreError(_CONFIGURATION_INVALID) from None
    return value


class WorkerStore:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise WorkerStoreError(_CONFIGURATION_INVALID)
        self._database_url = database_url
        self._connect = connect

    @classmethod
    def from_dsn_file(cls, path: Path) -> WorkerStore:
        return cls(_read_owner_only_file(path))

    def __repr__(self) -> str:
        return "WorkerStore(database_url=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    @staticmethod
    def _conflict() -> WorkerStoreError:
        return WorkerStoreError(_CONFLICT)

    def record_lease(
        self,
        lease: RelayLease,
        port: int,
        callback_token: str,
    ) -> None:
        try:
            if (
                not isinstance(lease, RelayLease)
                or isinstance(port, bool)
                or not isinstance(port, int)
                or not 1 <= port <= 65535
                or not isinstance(callback_token, str)
                or not callback_token
            ):
                raise ValueError
            token_hash = hashlib.sha256(callback_token.encode("utf-8")).digest()
            run_id = lease.payload.run_id
            job_id = lease.job_id
            agent_id = lease.payload.agent_id
            with self._connection() as connection:
                inserted = connection.execute(
                    "insert into execution_worker.local_runs "
                    "(run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at) "
                    "values (%s,%s,%s,%s,%s,'leased',now()) "
                    "on conflict do nothing returning run_id",
                    (run_id, job_id, agent_id, port, token_hash),
                ).fetchone()
                if inserted is not None:
                    return
                rows = connection.execute(
                    "select run_id,job_id,agent_id,metabot_port,callback_token_hash "
                    "from execution_worker.local_runs "
                    "where run_id=%s or job_id=%s for update",
                    (run_id, job_id),
                ).fetchall()
                if rows:
                    if len(rows) != 1:
                        raise ValueError
                    row = rows[0]
                    matches = (
                        row["run_id"] == run_id
                        and row["job_id"] == job_id
                        and row["agent_id"] == agent_id
                        and row["metabot_port"] == port
                        and hmac.compare_digest(
                            bytes(row["callback_token_hash"]), token_hash
                        )
                    )
                    if not matches:
                        raise ValueError
                    return
                raise ValueError
        except WorkerStoreError:
            raise
        except (ValueError, UnicodeError, psycopg.Error):
            raise self._conflict() from None

    def callback_token_matches(self, run_id: UUID, token: str) -> bool:
        try:
            if not isinstance(run_id, UUID) or not isinstance(token, str):
                raise ValueError
            candidate_hash = hashlib.sha256(token.encode("utf-8")).digest()
            with self._connection() as connection:
                row = connection.execute(
                    "select callback_token_hash "
                    "from execution_worker.local_runs where run_id=%s",
                    (run_id,),
                ).fetchone()
            if row is None:
                return False
            return hmac.compare_digest(
                bytes(row["callback_token_hash"]), candidate_hash
            )
        except WorkerStoreError:
            raise
        except (ValueError, UnicodeError, psycopg.Error):
            raise self._conflict() from None

    def _transition(
        self,
        run_id: UUID,
        *,
        expected: frozenset[str],
        target: str,
        timestamp_column: str | None = None,
    ) -> None:
        try:
            if not isinstance(run_id, UUID):
                raise ValueError
            with self._connection() as connection:
                row = connection.execute(
                    "select state from execution_worker.local_runs "
                    "where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if row is None or row["state"] not in expected:
                    raise ValueError
                if timestamp_column is None:
                    connection.execute(
                        "update execution_worker.local_runs set state=%s "
                        "where run_id=%s",
                        (target, run_id),
                    )
                elif timestamp_column == "dispatched_at":
                    connection.execute(
                        "update execution_worker.local_runs "
                        "set state=%s,dispatched_at=now() where run_id=%s",
                        (target, run_id),
                    )
                elif timestamp_column == "terminal_at":
                    connection.execute(
                        "update execution_worker.local_runs "
                        "set state=%s,terminal_at=now() where run_id=%s",
                        (target, run_id),
                    )
                else:
                    raise ValueError
        except WorkerStoreError:
            raise
        except (ValueError, psycopg.Error):
            raise self._conflict() from None

    def mark_dispatching(self, run_id: UUID) -> None:
        self._transition(
            run_id, expected=frozenset({"leased"}), target="dispatching"
        )

    def mark_dispatched(self, run_id: UUID) -> None:
        try:
            if not isinstance(run_id, UUID):
                raise ValueError
            with self._connection() as connection:
                row = connection.execute(
                    "select state from execution_worker.local_runs "
                    "where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if row is None or row["state"] not in {
                    "dispatching",
                    "dispatched",
                    "running",
                    *_TERMINAL_STATES,
                }:
                    raise ValueError
                if row["state"] == "dispatching":
                    connection.execute(
                        "update execution_worker.local_runs "
                        "set state='dispatched',dispatched_at=now() "
                        "where run_id=%s",
                        (run_id,),
                    )
                else:
                    connection.execute(
                        "update execution_worker.local_runs "
                        "set dispatched_at=coalesce(dispatched_at,now()) "
                        "where run_id=%s",
                        (run_id,),
                    )
        except WorkerStoreError:
            raise
        except (ValueError, psycopg.Error):
            raise self._conflict() from None

    def recoverable_runs(self) -> tuple[WorkerRunRecovery, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select r.run_id,r.agent_id,r.state,r.dispatched_at,"
                    "exists(select 1 from execution_worker.event_outbox o "
                    "where o.run_id=r.run_id) as has_events "
                    "from execution_worker.local_runs r "
                    "order by r.leased_at,r.run_id"
                ).fetchall()
            return tuple(
                WorkerRunRecovery(
                    run_id=row["run_id"],
                    agent_id=row["agent_id"],
                    state=row["state"],
                    dispatched_at=row["dispatched_at"],
                    has_events=row["has_events"],
                )
                for row in rows
            )
        except WorkerStoreError:
            raise
        except (TypeError, ValueError, psycopg.Error):
            raise self._conflict() from None

    def has_local_state(self, run_id: UUID) -> bool:
        """Return whether this Worker has any durable fact for the run."""

        try:
            if not isinstance(run_id, UUID):
                raise ValueError
            with self._connection() as connection:
                row = connection.execute(
                    "select exists(select 1 from execution_worker.local_runs "
                    "where run_id=%s) or exists(select 1 from "
                    "execution_worker.event_outbox where run_id=%s) as present",
                    (run_id, run_id),
                ).fetchone()
            if row is None or type(row["present"]) is not bool:
                raise ValueError
            return row["present"]
        except WorkerStoreError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise self._conflict() from None

    def append_event(self, event: RelayEvent) -> bool:
        try:
            if not isinstance(event, RelayEvent):
                raise ValueError
            event_json = event.model_dump(mode="json")
            with self._connection() as connection:
                run = connection.execute(
                    "select state from execution_worker.local_runs "
                    "where run_id=%s for update",
                    (event.run_id,),
                ).fetchone()
                if run is None:
                    raise ValueError
                existing = connection.execute(
                    "select event_json=%s::jsonb as exact_replay "
                    "from execution_worker.event_outbox "
                    "where run_id=%s and seq=%s",
                    (Jsonb(event_json), event.run_id, event.seq),
                ).fetchone()
                if existing is not None:
                    if existing["exact_replay"]:
                        return False
                    raise ValueError
                if run["state"] not in {"dispatching", "dispatched", "running"}:
                    raise ValueError
                last = connection.execute(
                    "select coalesce(max(seq),0) as seq "
                    "from execution_worker.event_outbox where run_id=%s",
                    (event.run_id,),
                ).fetchone()
                if event.seq != last["seq"] + 1:
                    raise ValueError
                connection.execute(
                    "insert into execution_worker.event_outbox "
                    "(run_id,seq,event_json) values (%s,%s,%s::jsonb)",
                    (event.run_id, event.seq, Jsonb(event_json)),
                )
                if run["state"] in {"dispatching", "dispatched"}:
                    connection.execute(
                        "update execution_worker.local_runs set state='running' "
                        "where run_id=%s",
                        (event.run_id,),
                    )
            return True
        except WorkerStoreError:
            raise
        except (ValueError, psycopg.Error):
            raise self._conflict() from None

    def append_terminal_event(self, event: RelayEvent, status: str) -> bool:
        try:
            if not isinstance(event, RelayEvent) or status not in _TERMINAL_STATES:
                raise ValueError
            event_json = event.model_dump(mode="json")
            with self._connection() as connection:
                run = connection.execute(
                    "select state from execution_worker.local_runs "
                    "where run_id=%s for update",
                    (event.run_id,),
                ).fetchone()
                if run is None:
                    raise ValueError
                existing = connection.execute(
                    "select event_json=%s::jsonb as exact_replay "
                    "from execution_worker.event_outbox "
                    "where run_id=%s and seq=%s",
                    (Jsonb(event_json), event.run_id, event.seq),
                ).fetchone()
                if existing is not None:
                    if existing["exact_replay"] and run["state"] == status:
                        return False
                    raise ValueError
                if run["state"] not in {
                    "dispatching",
                    "dispatched",
                    "running",
                }:
                    raise ValueError
                last = connection.execute(
                    "select coalesce(max(seq),0) as seq "
                    "from execution_worker.event_outbox where run_id=%s",
                    (event.run_id,),
                ).fetchone()
                if event.seq != last["seq"] + 1:
                    raise ValueError
                connection.execute(
                    "insert into execution_worker.event_outbox "
                    "(run_id,seq,event_json) values (%s,%s,%s::jsonb)",
                    (event.run_id, event.seq, Jsonb(event_json)),
                )
                connection.execute(
                    "update execution_worker.local_runs "
                    "set state=%s,terminal_at=now() where run_id=%s",
                    (status, event.run_id),
                )
            return True
        except WorkerStoreError:
            raise
        except (ValueError, psycopg.Error):
            raise self._conflict() from None

    def contiguous_outbox(
        self, run_id: UUID, limit: int = 100
    ) -> tuple[RelayEvent, ...]:
        try:
            if (
                not isinstance(run_id, UUID)
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or limit <= 0
            ):
                raise ValueError
            with self._connection() as connection:
                rows = connection.execute(
                    "select seq,event_json,delivered_at "
                    "from execution_worker.event_outbox "
                    "where run_id=%s order by seq",
                    (run_id,),
                ).fetchall()
            expected = 1
            undelivered: list[RelayEvent] = []
            found_undelivered = False
            for row in rows:
                if row["seq"] != expected:
                    break
                expected += 1
                if not found_undelivered and row["delivered_at"] is not None:
                    continue
                if row["delivered_at"] is not None:
                    break
                found_undelivered = True
                undelivered.append(RelayEvent.model_validate(row["event_json"]))
                if len(undelivered) == limit:
                    break
            return tuple(undelivered)
        except WorkerStoreError:
            raise
        except (ValueError, psycopg.Error):
            raise self._conflict() from None

    def mark_delivered(self, run_id: UUID, through_seq: int) -> None:
        try:
            if (
                not isinstance(run_id, UUID)
                or isinstance(through_seq, bool)
                or not isinstance(through_seq, int)
                or through_seq <= 0
            ):
                raise ValueError
            with self._connection() as connection:
                run = connection.execute(
                    "select run_id from execution_worker.local_runs "
                    "where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if run is None:
                    raise ValueError
                rows = connection.execute(
                    "select seq,delivered_at from execution_worker.event_outbox "
                    "where run_id=%s and seq<=%s order by seq for update",
                    (run_id, through_seq),
                ).fetchall()
                if len(rows) != through_seq or any(
                    row["seq"] != index
                    for index, row in enumerate(rows, start=1)
                ):
                    raise ValueError
                delivered_prefix = 0
                for row in rows:
                    if row["delivered_at"] is None:
                        break
                    delivered_prefix = row["seq"]
                if any(
                    row["delivered_at"] is not None
                    for row in rows[delivered_prefix:]
                ):
                    raise ValueError
                connection.execute(
                    "update execution_worker.event_outbox set delivered_at=now() "
                    "where run_id=%s and seq>%s and seq<=%s",
                    (run_id, delivered_prefix, through_seq),
                )
        except WorkerStoreError:
            raise
        except (ValueError, psycopg.Error):
            raise self._conflict() from None

    def mark_terminal(self, run_id: UUID, status: str) -> None:
        if status not in _TERMINAL_STATES:
            raise self._conflict()
        expected = (
            frozenset({"running"})
            if status in {"completed", "failed"}
            else frozenset({"leased", "dispatching", "dispatched", "running"})
        )
        self._transition(
            run_id,
            expected=expected,
            target=status,
            timestamp_column="terminal_at",
        )

    def reconcile_forced_terminal(self, run_id: UUID, status: str) -> None:
        """Adopt the cloud terminal and discard events it can no longer accept."""

        try:
            if not isinstance(run_id, UUID) or status not in _FORCED_TERMINAL_STATES:
                raise ValueError
            with self._connection() as connection:
                row = connection.execute(
                    "select state from execution_worker.local_runs "
                    "where run_id=%s for update",
                    (run_id,),
                ).fetchone()
                if row is None or row["state"] not in {
                    "dispatching",
                    "dispatched",
                    "running",
                    *_TERMINAL_STATES,
                }:
                    raise ValueError
                connection.execute(
                    "delete from execution_worker.event_outbox "
                    "where run_id=%s and delivered_at is null",
                    (run_id,),
                )
                connection.execute(
                    "update execution_worker.local_runs set state=%s,"
                    "terminal_at=coalesce(terminal_at,now()) where run_id=%s",
                    (status, run_id),
                )
        except WorkerStoreError:
            raise
        except (ValueError, psycopg.Error):
            raise self._conflict() from None
