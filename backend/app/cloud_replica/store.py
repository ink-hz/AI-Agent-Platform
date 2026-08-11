from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, BinaryIO, Callable

import psycopg
from psycopg.rows import dict_row

from .crypto import BatchVerifier, FieldCipher
from .protocol import (
    BatchLimits,
    SignedBatch,
    decode_and_verify_batch,
)


class ReplicaStoreError(RuntimeError):
    """Stable, payload-free cloud replica persistence error."""


@dataclass(frozen=True, slots=True)
class PreparedSession:
    session_key: str
    user_id: str
    agent_id: str
    source_kind: str
    channel: str | None
    created_at: datetime
    last_active_at: datetime
    encrypted: dict[str, str]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class ReplicaImportResult:
    status: str
    sequence: int
    record_count: int
    digest: str


@dataclass(frozen=True, slots=True)
class ReplicaRetentionResult:
    dry_run: bool
    session_count: int
    agent_count: int


_SESSION_KEYS = {
    "kind",
    "key",
    "user_id",
    "agent_id",
    "source_kind",
    "channel",
    "title",
    "primary_sender_name",
    "primary_sender_department",
    "created_at",
    "last_active_at",
    "turns",
    "sanitizer_policy_version",
}
_SAFE_KEY = re.compile(r"[a-z2-7]{40,64}\Z")
_SAFE_AGENT = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")


def _canonical(record: dict[str, Any]) -> str:
    try:
        return json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError):
        raise ReplicaStoreError("record_invalid") from None


def _parse_time(value: Any) -> datetime:
    try:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        raise ReplicaStoreError("record_invalid") from None


def _decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception:
        raise ReplicaStoreError("record_invalid") from None


class ReplicaStore:
    def __init__(
        self,
        database_url: str,
        *,
        cipher: FieldCipher,
        connection_factory: Callable[..., Any] = psycopg.connect,
        migration_path: str | Path | None = None,
    ):
        self._database_url = database_url
        self._cipher = cipher
        self._connection_factory = connection_factory
        self._migration_path = Path(migration_path) if migration_path else (
            Path(__file__).parents[2] / "migrations" / "008_cloud_replica.sql"
        )

    def _connect(self):
        return self._connection_factory(self._database_url, row_factory=dict_row)

    def migrate(self) -> None:
        try:
            sql = self._migration_path.read_text(encoding="utf-8")
            with self._connect() as connection:
                with connection.transaction():
                    connection.cursor().execute(sql)
        except Exception:
            raise ReplicaStoreError("migration_failed") from None

    def prepare_session(self, record: dict[str, Any]) -> PreparedSession:
        if set(record) != _SESSION_KEYS or record.get("kind") != "session":
            raise ReplicaStoreError("record_invalid")
        session_key = record.get("key")
        user_id = record.get("user_id")
        agent_id = record.get("agent_id")
        source_kind = record.get("source_kind")
        channel = record.get("channel")
        if (
            not isinstance(session_key, str)
            or not _SAFE_KEY.fullmatch(session_key)
            or not isinstance(user_id, str)
            or not _SAFE_KEY.fullmatch(user_id)
            or not isinstance(agent_id, str)
            or not _SAFE_AGENT.fullmatch(agent_id)
            or source_kind not in {"metabot", "fae", "admin", "unknown"}
            or channel not in {None, "feishu", "dingtalk", "web", "api"}
            or not isinstance(record.get("title"), dict)
            or not isinstance(record.get("turns"), list)
        ):
            raise ReplicaStoreError("record_invalid")
        created_at = _parse_time(record["created_at"])
        last_active_at = _parse_time(record["last_active_at"])
        if last_active_at < created_at:
            raise ReplicaStoreError("record_invalid")
        canonical = _canonical(record)
        encrypted = self._cipher.encrypt(
            canonical, f"1:session:{session_key}"
        )
        return PreparedSession(
            session_key=session_key,
            user_id=user_id,
            agent_id=agent_id,
            source_kind=source_kind,
            channel=channel,
            created_at=created_at,
            last_active_at=last_active_at,
            encrypted=encrypted,
            payload_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def _prepared_records(
        self, records: tuple[dict[str, Any], ...]
    ) -> tuple[PreparedSession, ...]:
        by_key: dict[str, PreparedSession] = {}
        for record in records:
            prepared = self.prepare_session(record)
            existing = by_key.get(prepared.session_key)
            if existing and existing.payload_sha256 != prepared.payload_sha256:
                raise ReplicaStoreError("record_conflict")
            by_key[prepared.session_key] = prepared
        return tuple(by_key.values())

    def import_batch(self, batch: SignedBatch) -> ReplicaImportResult:
        prepared_records = self._prepared_records(batch.records)
        try:
            with self._connect() as connection:
                with connection.transaction():
                    cursor = connection.cursor()
                    cursor.execute(
                        """
                        select last_sequence, last_digest
                        from platform_replica.generations
                        where source_instance_id = %s
                        for update
                        """,
                        (batch.header.source_instance_id,),
                    )
                    generation = cursor.fetchone()
                    if generation is None:
                        if batch.header.sequence != 1:
                            raise ReplicaStoreError("sequence_gap")
                    else:
                        last_sequence = generation["last_sequence"]
                        last_digest = generation["last_digest"]
                        if batch.header.sequence <= last_sequence:
                            cursor.execute(
                                """
                                select digest from platform_replica.import_audit
                                where source_instance_id = %s and sequence = %s
                                """,
                                (
                                    batch.header.source_instance_id,
                                    batch.header.sequence,
                                ),
                            )
                            audit = cursor.fetchone()
                            if audit and audit["digest"] == batch.digest:
                                return ReplicaImportResult(
                                    status="replayed",
                                    sequence=batch.header.sequence,
                                    record_count=0,
                                    digest=batch.digest,
                                )
                            raise ReplicaStoreError("replay_conflict")
                        if batch.header.sequence != last_sequence + 1:
                            raise ReplicaStoreError("sequence_gap")
                        if batch.header.previous_digest != last_digest:
                            raise ReplicaStoreError("predecessor_mismatch")
                    if generation is None and batch.header.previous_digest is not None:
                        raise ReplicaStoreError("predecessor_mismatch")
                    for prepared in prepared_records:
                        agent_plaintext = _canonical({"agent_id": prepared.agent_id})
                        agent_encrypted = self._cipher.encrypt(
                            agent_plaintext, f"1:agent:{prepared.agent_id}"
                        )
                        cursor.execute(
                            """
                            insert into platform_replica.agents
                                (agent_id, display_payload, payload_nonce,
                                 payload_sha256, updated_at)
                            values (%s, %s, %s, %s, now())
                            on conflict (agent_id) do update set
                                display_payload = excluded.display_payload,
                                payload_nonce = excluded.payload_nonce,
                                payload_sha256 = excluded.payload_sha256,
                                updated_at = now()
                            """,
                            (
                                prepared.agent_id,
                                _decode(agent_encrypted["ciphertext"]),
                                _decode(agent_encrypted["nonce"]),
                                hashlib.sha256(agent_plaintext.encode()).hexdigest(),
                            ),
                        )
                        cursor.execute(
                            """
                            insert into platform_replica.sessions
                                (session_key, user_id, agent_id, source_kind,
                                 channel, created_at, last_active_at, expires_at,
                                 generation_sequence, display_payload,
                                 payload_nonce, payload_sha256, updated_at)
                            values (%s, %s, %s, %s, %s, %s, %s,
                                    %s + interval '1 year', %s, %s, %s, %s, now())
                            on conflict (session_key) do update set
                                user_id = excluded.user_id,
                                agent_id = excluded.agent_id,
                                source_kind = excluded.source_kind,
                                channel = excluded.channel,
                                created_at = excluded.created_at,
                                last_active_at = excluded.last_active_at,
                                expires_at = excluded.expires_at,
                                generation_sequence = excluded.generation_sequence,
                                display_payload = excluded.display_payload,
                                payload_nonce = excluded.payload_nonce,
                                payload_sha256 = excluded.payload_sha256,
                                updated_at = now()
                            """,
                            (
                                prepared.session_key,
                                prepared.user_id,
                                prepared.agent_id,
                                prepared.source_kind,
                                prepared.channel,
                                prepared.created_at,
                                prepared.last_active_at,
                                prepared.last_active_at,
                                batch.header.sequence,
                                _decode(prepared.encrypted["ciphertext"]),
                                _decode(prepared.encrypted["nonce"]),
                                prepared.payload_sha256,
                            ),
                        )
                    cursor.execute(
                        """
                        insert into platform_replica.generations
                            (source_instance_id, last_sequence, last_digest,
                             upper_watermark, committed_at)
                        values (%s, %s, %s, %s, now())
                        on conflict (source_instance_id) do update set
                            last_sequence = excluded.last_sequence,
                            last_digest = excluded.last_digest,
                            upper_watermark = excluded.upper_watermark,
                            committed_at = now()
                        """,
                        (
                            batch.header.source_instance_id,
                            batch.header.sequence,
                            batch.digest,
                            batch.header.upper_watermark,
                        ),
                    )
                    cursor.execute(
                        """
                        insert into platform_replica.import_audit
                            (source_instance_id, sequence, digest, record_count,
                             upper_watermark)
                        values (%s, %s, %s, %s, %s)
                        """,
                        (
                            batch.header.source_instance_id,
                            batch.header.sequence,
                            batch.digest,
                            len(prepared_records),
                            batch.header.upper_watermark,
                        ),
                    )
            return ReplicaImportResult(
                status="imported",
                sequence=batch.header.sequence,
                record_count=len(prepared_records),
                digest=batch.digest,
            )
        except ReplicaStoreError:
            raise
        except Exception:
            raise ReplicaStoreError("import_failed") from None

    def expire(
        self, *, now: datetime | None = None, dry_run: bool = False
    ) -> ReplicaRetentionResult:
        cutoff = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            with self._connect() as connection:
                with connection.transaction():
                    cursor = connection.cursor()
                    cursor.execute(
                        "select count(*) as count from platform_replica.sessions where expires_at <= %s",
                        (cutoff,),
                    )
                    session_count = cursor.fetchone()["count"]
                    cursor.execute(
                        """
                        select count(*) as count from platform_replica.agents a
                        where not exists (
                            select 1 from platform_replica.sessions s
                            where s.agent_id = a.agent_id and s.expires_at > %s
                        )
                        """,
                        (cutoff,),
                    )
                    agent_count = cursor.fetchone()["count"]
                    if not dry_run:
                        cursor.execute(
                            "delete from platform_replica.sessions where expires_at <= %s",
                            (cutoff,),
                        )
                        cursor.execute(
                            """
                            delete from platform_replica.agents a
                            where not exists (
                                select 1 from platform_replica.sessions s
                                where s.agent_id = a.agent_id
                            )
                            """
                        )
                        cursor.execute(
                            """
                            insert into platform_replica.retention_audit
                                (cutoff_at, deleted_session_count, deleted_agent_count)
                            values (%s, %s, %s)
                            """,
                            (cutoff, session_count, agent_count),
                        )
            return ReplicaRetentionResult(
                dry_run=dry_run,
                session_count=session_count,
                agent_count=agent_count,
            )
        except Exception:
            raise ReplicaStoreError("retention_failed") from None


def import_verified_stream(
    stream: BinaryIO,
    verifier: BatchVerifier,
    limits: BatchLimits,
    store: ReplicaStore,
) -> ReplicaImportResult:
    batch = decode_and_verify_batch(stream, verifier, limits)
    return store.import_batch(batch)
