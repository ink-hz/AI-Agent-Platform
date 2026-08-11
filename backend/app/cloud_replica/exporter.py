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
from .sanitize import SanitizationPolicy, sanitize_session


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
        metadata = self.state_path.lstat()
        if (
            self.state_path.is_symlink()
            or not self.state_path.is_file()
            or metadata.st_mode & 0o777 != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise RuntimeError("replica export state is unsafe")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
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
        records = tuple(
            self._record(raw, sanitize_session(raw, self.policy))
            for raw in raw_sessions
        )
        created_at = self.clock()
        if len(raw_sessions) == limit:
            checkpoint = max(
                raw_sessions,
                key=lambda session: (session.last_active_at, session.session_key),
            )
            next_watermark = checkpoint.last_active_at
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
