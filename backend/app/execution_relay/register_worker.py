from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import UUID, uuid4

import psycopg


_CHANGE_REFERENCE = re.compile(r"[A-Z][A-Z0-9_-]{7,63}\Z")
_WORKER_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_KEY_ID = re.compile(r"worker-v[1-9][0-9]*\Z")
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PUBLIC_KEY = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_ACCEPTANCE_WORKER_ID = re.compile(r"relay-acceptance-[0-9a-f]{16}\Z")
_ALLOWED_AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "agent-brain-bot",
)
_DOCUMENT_KEYS = {
    "worker_id",
    "key_id",
    "public_key_base64url",
    "allowed_agent_ids",
}


def _secure_text_file(value: str, *, maximum_size: int) -> str:
    path = Path(value)
    if not value or not path.is_absolute() or not path.name or path.name in {".", ".."}:
        raise ValueError
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_fd = os.open(path.parent, directory_flags)
    try:
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()
        ):
            raise ValueError
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or metadata.st_size > maximum_size
            ):
                raise ValueError
            chunks: list[bytes] = []
            remaining = maximum_size + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 8192))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > maximum_size or os.read(descriptor, 1):
                raise ValueError
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)
    return raw.decode("utf-8")


def _secret_file() -> str:
    value = os.environ.get("PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", "")
    dsn = _secure_text_file(value, maximum_size=16_384).strip()
    if not dsn or "\x00" in dsn or "\n" in dsn or "\r" in dsn:
        raise ValueError
    return dsn


def _public_document(value: str) -> tuple[str, str, bytes, tuple[str, ...]]:
    document = json.loads(_secure_text_file(value, maximum_size=65_536))
    if not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS:
        raise ValueError
    worker_id = document["worker_id"]
    key_id = document["key_id"]
    encoded = document["public_key_base64url"]
    agents = document["allowed_agent_ids"]
    if (
        not isinstance(worker_id, str)
        or _WORKER_ID.fullmatch(worker_id) is None
        or not isinstance(key_id, str)
        or _KEY_ID.fullmatch(key_id) is None
        or not isinstance(encoded, str)
        or _PUBLIC_KEY.fullmatch(encoded) is None
        or not isinstance(agents, list)
        or not agents
        or len(agents) != len(set(agents))
        or any(not isinstance(agent, str) or _AGENT_ID.fullmatch(agent) is None for agent in agents)
        or (
            _ACCEPTANCE_WORKER_ID.fullmatch(worker_id) is not None
            and (key_id != "worker-v1" or tuple(agents) != ("hr-bot",))
        )
        or (
            _ACCEPTANCE_WORKER_ID.fullmatch(worker_id) is None
            and tuple(agents) != _ALLOWED_AGENTS
        )
    ):
        raise ValueError
    public_key = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    if (
        len(public_key) != 32
        or base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=") != encoded
    ):
        raise ValueError
    return worker_id, key_id, public_key, tuple(agents)


def _reference(value: str) -> str:
    if _CHANGE_REFERENCE.fullmatch(value) is None:
        raise ValueError
    return value


def _request_id() -> UUID:
    return uuid4()


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        if not values:
            raise ValueError
        command = values[0]
        if command == "register" and len(values) == 3:
            worker_id, key_id, public_key, agents = _public_document(values[1])
            parameters = (
                worker_id,
                key_id,
                public_key,
                list(agents),
                _reference(values[2]),
                _request_id(),
            )
            function = "register_execution_worker_v28"
            placeholders = "%s,%s,%s,%s,%s,%s"
        elif command == "add-key" and len(values) == 4:
            worker_id, key_id, public_key, _agents = _public_document(values[2])
            if values[1] != worker_id:
                raise ValueError
            parameters = (
                worker_id,
                key_id,
                public_key,
                _reference(values[3]),
                _request_id(),
            )
            function = "add_execution_worker_key_v28"
            placeholders = "%s,%s,%s,%s,%s"
        elif command == "revoke-key" and len(values) == 4:
            if _WORKER_ID.fullmatch(values[1]) is None or _KEY_ID.fullmatch(values[2]) is None:
                raise ValueError
            parameters = (
                values[1],
                values[2],
                _reference(values[3]),
                _request_id(),
            )
            function = "revoke_execution_worker_key_v28"
            placeholders = "%s,%s,%s,%s"
        elif command == "revoke-worker" and len(values) == 3:
            if _WORKER_ID.fullmatch(values[1]) is None:
                raise ValueError
            parameters = (values[1], _reference(values[2]), _request_id())
            function = "revoke_execution_worker_v28"
            placeholders = "%s,%s,%s"
        else:
            raise ValueError
        with psycopg.connect(_secret_file()) as connection:
            connection.execute(
                f"select platform_control.{function}({placeholders})", parameters
            )
        print("EXECUTION_WORKER_MAINTENANCE_OK")
        return 0
    except Exception:
        print("EXECUTION_WORKER_MAINTENANCE_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
