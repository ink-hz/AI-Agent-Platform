#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys


def _fail() -> int:
    print("CONTENT_KEYRING_FAILED", file=sys.stderr)
    return 1


def _safe_directory(path: Path) -> int:
    if not path.is_absolute() or path == Path("/"):
        raise ValueError
    chain: list[Path] = []
    current = path
    while current != current.parent:
        chain.append(current)
        current = current.parent
    for directory in reversed(chain):
        metadata = directory.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (directory == path and stat.S_IMODE(metadata.st_mode) != 0o700)
            or (directory == path and metadata.st_uid != os.getuid())
        ):
            raise ValueError
    return os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )


def _validate(path: Path) -> tuple[bytes, str]:
    parent_fd = _safe_directory(path.parent)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            raw = os.read(descriptor, 65_537)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or len(raw) > 65_536
                or os.read(descriptor, 1)
            ):
                raise ValueError
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)
    document = json.loads(raw.decode("utf-8"))
    if (
        not isinstance(document, dict)
        or set(document) != {"purpose", "active_version", "keys"}
        or document["purpose"] != "platform-content-encryption"
        or document["active_version"] != 1
        or not isinstance(document["keys"], dict)
        or set(document["keys"]) != {"1"}
        or not isinstance(document["keys"]["1"], str)
    ):
        raise ValueError
    key = base64.b64decode(document["keys"]["1"], validate=True)
    if len(key) != 32:
        raise ValueError
    repository_backend = Path(__file__).resolve().parents[2] / "backend"
    if not repository_backend.is_dir():
        raise ValueError
    sys.path.insert(0, str(repository_backend))
    from app.control_plane.crypto import IdentityKeyring
    from app.execution_relay.content_crypto import ContentCodec

    ContentCodec(
        IdentityKeyring.from_file(
            path,
            expected_purpose="platform-content-encryption",
            expected_key_length=32,
        )
    )
    return key, hashlib.sha256(key).hexdigest()


def _create(path: Path) -> str:
    parent_fd = _safe_directory(path.parent)
    part = f".{path.name}.part"
    try:
        if os.path.lexists(path):
            _key, fingerprint = _validate(path)
            return f"CONTENT_KEYRING_VALID fingerprint={fingerprint}"
        descriptor = os.open(
            part,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        key = secrets.token_bytes(32)
        raw = (
            json.dumps(
                {
                    "active_version": 1,
                    "keys": {"1": base64.b64encode(key).decode("ascii")},
                    "purpose": "platform-content-encryption",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        try:
            os.write(descriptor, raw)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(
            part,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        os.unlink(part, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except Exception:
        try:
            os.unlink(part, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(parent_fd)
    _key, fingerprint = _validate(path)
    return f"CONTENT_KEYRING_CREATED fingerprint={fingerprint}"


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    try:
        if len(values) != 1:
            raise ValueError
        target = Path(values[0])
        if not target.is_absolute() or not target.name or target.name in {".", ".."}:
            raise ValueError
        print(_create(target))
        return 0
    except Exception:
        return _fail()


if __name__ == "__main__":
    raise SystemExit(main())
