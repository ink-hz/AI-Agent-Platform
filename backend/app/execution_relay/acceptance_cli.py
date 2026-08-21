from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.control_plane.crypto import IdentityKeyring
from app.local_secrets import read_secret_file

from .content_crypto import ContentCodec, SealedContent
from .models import RelayJobPayload
from .repository import ExecutionRelayRepository


_AGENTS = frozenset({"hr-bot", "marketing-intelligence-bot"})
_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_ROOT_ENV = "PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ROOT"
_MARKER_ENV = "PLATFORM_EXECUTION_RELAY_ACCEPTANCE_MARKER_FILE"
_DATABASE_ENV = "PLATFORM_CONTROL_DATABASE_URL_FILE"
_KEYRING_ENV = "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"


def _secure_file(path: Path, root: Path, *, expected: bytes | None = None) -> None:
    if not path.is_absolute() or path.parent != root or path.is_symlink():
        raise ValueError
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_size > 65_536
    ):
        raise ValueError
    if expected is not None and path.read_bytes() != expected:
        raise ValueError


def _root() -> Path:
    if os.environ.get("PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ENABLED") != "1":
        raise ValueError
    root = Path(os.environ[_ROOT_ENV])
    if not root.is_absolute() or root.is_symlink():
        raise ValueError
    metadata = root.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise ValueError
    marker = Path(os.environ[_MARKER_ENV])
    database = Path(os.environ[_DATABASE_ENV])
    keyring = Path(os.environ[_KEYRING_ENV])
    _secure_file(
        marker,
        root,
        expected=b"AGENT_EXECUTION_RELAY_ACCEPTANCE_V1\n",
    )
    _secure_file(database, root)
    _secure_file(keyring, root)
    return root


def _resources(root: Path) -> tuple[str, ContentCodec]:
    database_path = Path(os.environ[_DATABASE_ENV])
    keyring_path = Path(os.environ[_KEYRING_ENV])
    _secure_file(database_path, root)
    _secure_file(keyring_path, root)
    database_url = read_secret_file(str(database_path))
    keyring = IdentityKeyring.from_file(
        str(keyring_path),
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    return database_url, ContentCodec(keyring)


def _repository(root: Path) -> ExecutionRelayRepository:
    database_url, codec = _resources(root)
    return ExecutionRelayRepository(database_url, content_codec=codec)


def _prompt(run_id: UUID) -> str:
    return f"relay acceptance synthetic run {run_id}"


def _tagged_payload(root: Path, run_id: UUID) -> tuple[dict[str, object], str]:
    database_url, codec = _resources(root)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "select job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,"
            "status,lease_worker_id from platform_control.execution_jobs where run_id=%s",
            (run_id,),
        ).fetchone()
    if row is None or row["agent_id"] not in _AGENTS:
        raise ValueError
    payload = codec.unseal_json(
        f"execution-job:{row['job_id']}:{run_id}",
        SealedContent(bytes(row["payload_ciphertext"]), row["encryption_key_version"]),
    )
    if payload.get("prompt") != _prompt(run_id) or payload.get("agent_id") != row["agent_id"]:
        raise ValueError
    return row, database_url


def _inspect(root: Path, run_id: UUID) -> dict[str, object]:
    job, database_url = _tagged_payload(root, run_id)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        events = connection.execute(
            "select seq,event_type from platform_control.execution_events "
            "where run_id=%s order by seq",
            (run_id,),
        ).fetchall()
    sequence = [row["seq"] for row in events]
    terminal_types = {
        "terminal",
        "run.terminal",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.interrupted",
    }
    ordered_terminal = bool(
        sequence
        and sequence == list(range(1, len(sequence) + 1))
        and events[-1]["event_type"] in terminal_types
        and all(row["event_type"] not in terminal_types for row in events[:-1])
        and job["status"] in _TERMINAL
    )
    return {
        "run_id": str(run_id),
        "agent_id": job["agent_id"],
        "status": job["status"],
        "event_count": len(events),
        "first_seq": sequence[0] if sequence else None,
        "last_seq": sequence[-1] if sequence else None,
        "ordered_terminal": ordered_terminal,
    }


def _interrupt(root: Path, run_id: UUID) -> bool:
    job, _database_url = _tagged_payload(root, run_id)
    worker_id = job["lease_worker_id"]
    if not isinstance(worker_id, str) or job["status"] not in {"leased", "dispatched", "running"}:
        raise ValueError
    _repository(root).finish(worker_id, run_id, "interrupted")
    return True


def _uuid(value: str) -> UUID:
    selected = UUID(value)
    if str(selected) != value:
        raise ValueError
    return selected


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        root = _root()
        if len(values) == 5 and values[0] == "enqueue":
            agent_id = values[1]
            if agent_id not in _AGENTS:
                raise ValueError
            run_id, conversation_id, message_id = map(_uuid, values[2:])
            payload = RelayJobPayload(
                run_id=run_id,
                conversation_id=conversation_id,
                trigger_message_id=message_id,
                agent_id=agent_id,
                prompt=_prompt(run_id),
                max_turns=2,
            )
            job_id = _repository(root).enqueue(payload)
            result = {"job_id": str(job_id), "run_id": str(run_id), "status": "queued"}
        elif len(values) == 2 and values[0] == "inspect":
            result = _inspect(root, _uuid(values[1]))
        elif len(values) == 2 and values[0] == "interrupt":
            run_id = _uuid(values[1])
            if _interrupt(root, run_id) is not True:
                raise ValueError
            result = {"run_id": str(run_id), "status": "interrupted"}
        else:
            raise ValueError
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        print("EXECUTION_RELAY_ACCEPTANCE_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
