#!/usr/bin/env python3
from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
)


_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _secure_parent(path: Path) -> int:
    if not path.is_absolute() or not path.name or path.name in {".", ".."}:
        raise ValueError
    descriptor = os.open(path.parent, _DIRECTORY_FLAGS)
    try:
        parent = os.fstat(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o700
        or parent.st_uid != os.getuid()
    ):
        os.close(descriptor)
        raise ValueError
    return descriptor


def _private_bytes(path: Path) -> bytes:
    parent_fd = _secure_parent(path)
    try:
        try:
            descriptor = os.open(path.name, _FILE_READ_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            key = Ed25519PrivateKey.generate()
            value = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            descriptor = os.open(
                path.name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.fchmod(descriptor, 0o600)
                _write_all(descriptor, value)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(parent_fd)
            return value
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or metadata.st_size != 32
            ):
                raise ValueError
            value = os.read(descriptor, 33)
            if len(value) != 32 or os.read(descriptor, 1):
                raise ValueError
            return value
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise ValueError
        offset += written


def _validate_optional_target(parent_fd: int, name: str) -> None:
    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return
        raise
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_size > 65_536
        ):
            raise ValueError
    finally:
        os.close(descriptor)


def _write_public(path: Path, value: bytes) -> None:
    parent_fd = _secure_parent(path)
    temporary = f".{path.name}.{secrets.token_hex(16)}.part"
    created = False
    try:
        _validate_optional_target(parent_fd, path.name)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        created = True
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, value)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(
            temporary,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        created = False
        os.fsync(parent_fd)
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        if len(values) != 2:
            raise ValueError
        private_path, public_path = map(Path, values)
        private = _private_bytes(private_path)
        public = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        document = {
            "worker_id": "agentops-mac-primary",
            "key_id": "worker-v1",
            "public_key_base64url": base64.urlsafe_b64encode(public).decode().rstrip("="),
            "allowed_agent_ids": list(AGENTS),
        }
        _write_public(
            public_path,
            (json.dumps(document, indent=2) + "\n").encode("utf-8"),
        )
        print(f"WORKER_KEY_FINGERPRINT={hashlib.sha256(public).hexdigest()}")
        return 0
    except Exception:
        print("WORKER_KEY_GENERATION_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
