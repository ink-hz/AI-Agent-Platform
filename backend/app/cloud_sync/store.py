"""Owned, atomic publication of complete source snapshots into the encrypted replica."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.store import ReplicaStore, ReplicaStoreError, _canonical, _decode
from app.cloud_sync.models import AgentSnapshot


class CloudSyncStoreError(RuntimeError):
    """Stable, payload-free publication failure."""


@dataclass(frozen=True, slots=True)
class PublishResult:
    status: str
    changed_sessions: int
    deleted_sessions: int
    source_sessions: int
    source_turns: int


class CloudSyncStore:
    def __init__(
        self,
        database_url: str,
        *,
        cipher: FieldCipher,
        connect: Callable[..., Any] = psycopg.connect,
        connect_timeout: int = 3,
        statement_timeout_ms: int = 10_000,
        lock_timeout_ms: int = 5_000,
    ):
        for value, maximum in (
            (connect_timeout, 3),
            (statement_timeout_ms, 10_000),
            (lock_timeout_ms, 5_000),
        ):
            if type(value) is not int or not 0 < value <= maximum:
                raise CloudSyncStoreError("timeout_config_invalid")
        self._connect_timeout = connect_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._lock_timeout_ms = lock_timeout_ms
        self._database_url = database_url
        self._cipher = cipher
        self._connect_factory = connect
        self._replica = ReplicaStore(database_url, cipher=cipher)

    def _connect(self):
        return self._connect_factory(
            self._database_url,
            row_factory=dict_row,
            connect_timeout=self._connect_timeout,
            options=(
                f"-c statement_timeout={self._statement_timeout_ms} "
                f"-c lock_timeout={self._lock_timeout_ms}"
            ),
        )

    def publish_snapshot(
        self, snapshot: AgentSnapshot, records: tuple[dict, ...]
    ) -> PublishResult:
        if snapshot.complete is not True:
            raise CloudSyncStoreError("snapshot_incomplete")
        if len(snapshot.sessions) != len(records):
            raise CloudSyncStoreError("snapshot_record_count_invalid")
        observed = snapshot.source_observed_at
        if not isinstance(observed, datetime) or observed.tzinfo is None:
            raise CloudSyncStoreError("snapshot_invalid")
        if not isinstance(snapshot.elapsed_ms, int) or snapshot.elapsed_ms < 0:
            raise CloudSyncStoreError("snapshot_invalid")
        prepared = []
        keys = set()
        try:
            for record in records:
                item = self._replica.prepare_session(record)
                if (
                    item.agent_id != snapshot.agent_id
                    or item.source_kind != snapshot.source_kind
                ):
                    raise CloudSyncStoreError("snapshot_scope_invalid")
                if item.session_key in keys:
                    raise CloudSyncStoreError("snapshot_duplicate_session")
                keys.add(item.session_key)
                prepared.append(item)
        except ReplicaStoreError:
            raise CloudSyncStoreError("snapshot_record_invalid") from None
        total_turns = sum(len(record["turns"]) for record in records)
        try:
            with self._connect() as connection, connection.transaction():
                cursor = connection.cursor()
                # This boundary also exists before the first authority row.
                cursor.execute(
                    "select platform_replica.lock_agent_writer(%s, true)",
                    (snapshot.agent_id,),
                )
                cursor.execute(
                    "select * from platform_replica.agent_sources where agent_id=%s",
                    (snapshot.agent_id,),
                )
                state = cursor.fetchone()
                if (
                    not state
                    or state["publisher"] != "cloud"
                    or state["mode"] != "cloud_direct"
                    or state["source_kind"] != snapshot.source_kind
                ):
                    raise CloudSyncStoreError("agent_ownership_invalid")
                cursor.execute("select session_user as writer")
                if cursor.fetchone()["writer"] != state["writer_role"]:
                    raise CloudSyncStoreError("agent_ownership_invalid")
                previous = state["last_source_success_at"]
                if previous is not None and observed <= previous:
                    return PublishResult(
                        "stale",
                        0,
                        0,
                        state["source_sessions"] or 0,
                        state["source_turns"] or 0,
                    )
                cursor.execute(
                    "select session_key, payload_sha256 from platform_replica.sessions where agent_id=%s",
                    (snapshot.agent_id,),
                )
                existing = {
                    row["session_key"]: row["payload_sha256"]
                    for row in cursor.fetchall()
                }
                changed = [
                    item
                    for item in prepared
                    if existing.get(item.session_key) != item.payload_sha256
                ]
                if prepared:
                    plaintext = _canonical({"agent_id": snapshot.agent_id})
                    encrypted = self._cipher.encrypt(
                        plaintext, f"1:agent:{snapshot.agent_id}"
                    )
                    cursor.execute(
                        """insert into platform_replica.agents
                        (agent_id, display_payload, payload_nonce, payload_sha256, updated_at)
                        values (%s,%s,%s,%s,now()) on conflict (agent_id) do nothing""",
                        (
                            snapshot.agent_id,
                            _decode(encrypted["ciphertext"]),
                            _decode(encrypted["nonce"]),
                            hashlib.sha256(plaintext.encode()).hexdigest(),
                        ),
                    )
                for item in changed:
                    cursor.execute(
                        """insert into platform_replica.sessions
                        (session_key,user_id,agent_id,source_kind,channel,created_at,last_active_at,
                         expires_at,generation_sequence,display_payload,payload_nonce,payload_sha256,updated_at)
                        values (%s,%s,%s,%s,%s,%s,%s,%s + interval '1 year',0,%s,%s,%s,now())
                        on conflict (session_key) do update set
                        user_id=excluded.user_id,agent_id=excluded.agent_id,source_kind=excluded.source_kind,
                        channel=excluded.channel,created_at=excluded.created_at,last_active_at=excluded.last_active_at,
                        expires_at=excluded.expires_at,generation_sequence=0,display_payload=excluded.display_payload,
                        payload_nonce=excluded.payload_nonce,payload_sha256=excluded.payload_sha256,updated_at=now()""",
                        (
                            item.session_key,
                            item.user_id,
                            item.agent_id,
                            item.source_kind,
                            item.channel,
                            item.created_at,
                            item.last_active_at,
                            item.last_active_at,
                            _decode(item.encrypted["ciphertext"]),
                            _decode(item.encrypted["nonce"]),
                            item.payload_sha256,
                        ),
                    )
                cursor.execute(
                    "delete from platform_replica.sessions where agent_id=%s and not (session_key = any(%s::text[]))",
                    (snapshot.agent_id, list(keys)),
                )
                deleted = cursor.rowcount
                cursor.execute(
                    """update platform_replica.agent_sources set
                    last_attempt_at=greatest(last_attempt_at,%s),last_source_success_at=%s,
                    last_published_at=now(),
                    last_error=case when last_failure_at > %s then last_error else null end,
                    source_sessions=%s,source_turns=%s,last_duration_ms=%s where agent_id=%s""",
                    (
                        observed,
                        observed,
                        observed,
                        len(prepared),
                        total_turns,
                        snapshot.elapsed_ms,
                        snapshot.agent_id,
                    ),
                )
            return PublishResult(
                "published" if changed or deleted else "unchanged",
                len(changed),
                deleted,
                len(prepared),
                total_turns,
            )
        except CloudSyncStoreError:
            raise
        except Exception:  # noqa: BLE001 -- payload-free persistence boundary
            raise CloudSyncStoreError("publish_failed") from None

    def record_failure(self, agent_id: str, code: str, attempted_at: datetime) -> None:
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            raise CloudSyncStoreError("failure_code_invalid")
        if not isinstance(attempted_at, datetime) or attempted_at.tzinfo is None:
            raise CloudSyncStoreError("failure_time_invalid")
        try:
            with self._connect() as connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute(
                    "select platform_replica.lock_agent_writer(%s, true)", (agent_id,)
                )
                cursor.execute(
                    """update platform_replica.agent_sources set last_attempt_at=%s,
                        last_failure_at=%s,last_error=%s where agent_id=%s
                        and (last_attempt_at is null or last_attempt_at <= %s)""",
                    (attempted_at, attempted_at, code, agent_id, attempted_at),
                )
        except Exception:  # noqa: BLE001 -- payload-free persistence boundary
            raise CloudSyncStoreError("failure_record_failed") from None

    def source_statuses(self) -> dict[str, dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "select * from platform_replica.agent_sources"
                ).fetchall()
                return {row["agent_id"]: dict(row) for row in rows}
        except Exception:  # noqa: BLE001 -- payload-free persistence boundary
            raise CloudSyncStoreError("source_status_failed") from None
