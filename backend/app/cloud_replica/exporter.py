from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable

from .crypto import BatchSigner, stable_id
from .models import RawSession, SanitizedSessionRecord
from .protocol import BatchState, encode_batch
from .sanitize import (
    SanitizationPolicy,
    sanitize_management_projection,
    sanitize_session,
)


@dataclass(frozen=True, slots=True)
class ExportState:
    source_instance_id: str
    next_sequence: int
    previous_digest: str | None
    upper_watermark: datetime
    cursor_session_key: str


@dataclass(frozen=True, slots=True)
class ExportResult:
    sequence: int
    record_count: int
    lower_watermark: datetime
    upper_watermark: datetime
    digest: str
    batch_path: Path


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _read_export_state(path: Path) -> ExportState:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not path.is_file()
        or metadata.st_mode & 0o777 != 0o600
        or metadata.st_uid != os.getuid()
    ):
        raise RuntimeError("replica export state is unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if set(value) != {
        "source_instance_id",
        "next_sequence",
        "previous_digest",
        "upper_watermark",
        "cursor_session_key",
    }:
        raise ValueError
    state = ExportState(
        source_instance_id=value["source_instance_id"],
        next_sequence=value["next_sequence"],
        previous_digest=value["previous_digest"],
        upper_watermark=_parse_timestamp(value["upper_watermark"]),
        cursor_session_key=value["cursor_session_key"],
    )
    if (
        not isinstance(state.source_instance_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", state.source_instance_id)
        or type(state.next_sequence) is not int
        or state.next_sequence < 1
        or not isinstance(state.cursor_session_key, str)
        or (state.next_sequence == 1 and state.previous_digest is not None)
        or (
            state.next_sequence > 1
            and (
                not isinstance(state.previous_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", state.previous_digest)
            )
        )
    ):
        raise ValueError
    return state


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def rewind_export_state(
    *,
    state_path: str | Path,
    queue_dir: str | Path,
    target: datetime,
    expected_next_sequence: int,
    now: datetime,
) -> ExportState:
    state_path = Path(state_path)
    queue_dir = Path(queue_dir)
    try:
        state = _read_export_state(state_path)
        if (
            queue_dir.is_symlink()
            or not queue_dir.is_dir()
            or any(queue_dir.glob("batch-*.jsonl"))
            or type(expected_next_sequence) is not int
            or expected_next_sequence != state.next_sequence
            or target.tzinfo is None
            or target.utcoffset() != timedelta(0)
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
            or not now - timedelta(days=365) <= target < state.upper_watermark
        ):
            raise ValueError
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, RuntimeError):
        raise RuntimeError("replica export rewind rejected") from None
    rewound = ExportState(
        source_instance_id=state.source_instance_id,
        next_sequence=state.next_sequence,
        previous_digest=state.previous_digest,
        upper_watermark=target.astimezone(UTC),
        cursor_session_key="",
    )
    payload = (
        json.dumps(
            {
                "source_instance_id": rewound.source_instance_id,
                "next_sequence": rewound.next_sequence,
                "previous_digest": rewound.previous_digest,
                "upper_watermark": _timestamp(rewound.upper_watermark),
                "cursor_session_key": rewound.cursor_session_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    _atomic_replace(state_path, payload)
    return rewound


def build_session_record(
    raw: RawSession,
    sanitized: SanitizedSessionRecord,
    identity_key: bytes,
) -> dict[str, Any]:
    safe_session_id = stable_id("session", raw.session_key, identity_key)
    raw_user = raw.user_identity or f"anonymous:{raw.session_key}"
    user_id = stable_id(f"user:{raw.source_kind}", raw_user, identity_key)
    safe_turns = []
    raw_turns = sorted(raw.turns, key=lambda item: item.turn_index)
    for raw_turn, safe_turn in zip(raw_turns, sanitized.turns, strict=True):
        turn_value = _json_safe(asdict(safe_turn))
        turn_value["key"] = stable_id("turn", raw_turn.turn_key, identity_key)
        safe_turns.append(turn_value)
    return {
        "kind": "session",
        "key": safe_session_id,
        "user_id": user_id,
        "agent_id": sanitized.agent_id,
        "source_kind": sanitized.source_kind,
        "channel": sanitized.channel,
        "title": _json_safe(asdict(sanitized.title)),
        "primary_sender_name": sanitized.primary_sender_name,
        "primary_sender_department": sanitized.primary_sender_department,
        "created_at": _timestamp(sanitized.created_at),
        "last_active_at": _timestamp(sanitized.last_active_at),
        "turns": safe_turns,
        "sanitizer_policy_version": sanitized.sanitizer_policy_version,
    }


class ReplicaExporter:
    def __init__(
        self,
        *,
        source,
        policy: SanitizationPolicy,
        identity_key: bytes,
        signer: BatchSigner,
        source_instance_id: str,
        state_path: str | Path,
        queue_dir: str | Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", source_instance_id):
            raise ValueError("invalid source instance")
        if len(identity_key) != 32:
            raise ValueError("invalid identity key")
        self.source = source
        self.policy = policy
        self.identity_key = identity_key
        self.signer = signer
        self.source_instance_id = source_instance_id
        self.state_path = Path(state_path)
        self.queue_dir = Path(queue_dir)
        self.clock = clock

    def _load_state(self, after: datetime) -> ExportState:
        if not self.state_path.exists():
            return ExportState(
                source_instance_id=self.source_instance_id,
                next_sequence=1,
                previous_digest=None,
                upper_watermark=after,
                cursor_session_key="",
            )
        try:
            state = _read_export_state(self.state_path)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise RuntimeError("replica export state is invalid") from None
        if (
            state.source_instance_id != self.source_instance_id
            or type(state.next_sequence) is not int
            or state.next_sequence < 1
            or state.upper_watermark != after
            or not isinstance(state.cursor_session_key, str)
            or (
                state.next_sequence == 1
                and state.previous_digest is not None
            )
            or (
                state.next_sequence > 1
                and (
                    not isinstance(state.previous_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", state.previous_digest)
                )
            )
        ):
            raise RuntimeError("replica export state is invalid")
        return state

    def _record(
        self, raw: RawSession, sanitized: SanitizedSessionRecord
    ) -> dict[str, Any]:
        return build_session_record(raw, sanitized, self.identity_key)

    def export_batch(
        self, *, after: datetime, through: datetime, limit: int
    ) -> ExportResult:
        state = self._load_state(after)
        raw_sessions = self.source.fetch_sessions(
            after=after,
            after_key=state.cursor_session_key,
            through=through,
            limit=limit,
        )
        session_records = tuple(
            self._record(raw, sanitize_session(raw, self.policy))
            for raw in raw_sessions
        )
        fetch_management = getattr(
            self.source, "fetch_management_projections", None
        )
        management = (
            fetch_management(through=through)
            if callable(fetch_management)
            else ()
        )
        management_records = tuple(
            _json_safe(
                sanitize_management_projection(
                    raw, self.policy, self.identity_key
                )
            )
            for raw in management
        )
        records = session_records + management_records
        created_at = self.clock()
        if len(raw_sessions) == limit:
            checkpoint = max(
                raw_sessions,
                key=lambda session: (
                    session.replication_cursor_at,
                    session.session_key,
                ),
            )
            next_watermark = checkpoint.replication_cursor_at
            next_cursor_key = checkpoint.session_key
        else:
            next_watermark = through
            next_cursor_key = ""
        batch_state = BatchState(
            source_instance_id=state.source_instance_id,
            sequence=state.next_sequence,
            previous_digest=state.previous_digest,
            lower_watermark=after,
            upper_watermark=next_watermark,
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=15),
            sanitizer_policy_version=self.policy.version,
        )
        payload = encode_batch(records, batch_state, self.signer)
        trailer = json.loads(payload.splitlines()[-1])
        digest = trailer["digest"]
        batch_path = self.queue_dir / f"batch-{state.next_sequence:020d}.jsonl"
        created_batch = False
        try:
            _atomic_create(batch_path, payload)
            created_batch = True
            next_state = {
                "source_instance_id": state.source_instance_id,
                "next_sequence": state.next_sequence + 1,
                "previous_digest": digest,
                "upper_watermark": _timestamp(next_watermark),
                "cursor_session_key": next_cursor_key,
            }
            state_payload = (
                json.dumps(next_state, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            _atomic_replace(self.state_path, state_payload)
        except Exception:
            if created_batch:
                batch_path.unlink(missing_ok=True)
            raise
        return ExportResult(
            sequence=state.next_sequence,
            record_count=len(records),
            lower_watermark=after,
            upper_watermark=next_watermark,
            digest=digest,
            batch_path=batch_path,
        )
