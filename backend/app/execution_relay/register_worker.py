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
_ALLOWED_AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
)
_DOCUMENT_KEYS = {
    "worker_id",
    "key_id",
    "public_key_base64url",
    "allowed_agent_ids",
}


def _secret_file() -> str:
    value = os.environ.get("PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", "")
    path = Path(value)
    if not value or not path.is_absolute() or path.is_symlink():
        raise ValueError
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
        or metadata.st_size > 16_384
    ):
        raise ValueError
    dsn = path.read_text(encoding="utf-8").strip()
    if not dsn or "\x00" in dsn or "\n" in dsn or "\r" in dsn:
        raise ValueError
    return dsn


def _public_document(value: str) -> tuple[str, str, bytes, tuple[str, ...]]:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError
    document = json.loads(path.read_text(encoding="utf-8"))
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
        or tuple(agents) != _ALLOWED_AGENTS
    ):
        raise ValueError
    public_key = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    if len(public_key) != 32:
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
            function = "register_execution_worker_v27"
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
            function = "add_execution_worker_key_v27"
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
            function = "revoke_execution_worker_key_v27"
            placeholders = "%s,%s,%s,%s"
        elif command == "revoke-worker" and len(values) == 3:
            if _WORKER_ID.fullmatch(values[1]) is None:
                raise ValueError
            parameters = (values[1], _reference(values[2]), _request_id())
            function = "revoke_execution_worker_v27"
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
