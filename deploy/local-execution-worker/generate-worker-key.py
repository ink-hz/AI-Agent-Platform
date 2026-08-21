#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
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


def _private_bytes(path: Path) -> bytes:
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError
    parent = path.parent.stat()
    if stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_uid != os.getuid():
        raise ValueError
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError
        value = path.read_bytes()
        if len(value) != 32:
            raise ValueError
        return value
    key = Ed25519PrivateKey.generate()
    value = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        os.write(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        if len(values) != 2:
            raise ValueError
        private_path, public_path = map(Path, values)
        if not public_path.is_absolute() or public_path.is_symlink():
            raise ValueError
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
        temporary = public_path.with_name(f".{public_path.name}.{os.getpid()}.part")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.write(
                descriptor,
                (json.dumps(document, indent=2) + "\n").encode("utf-8"),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, public_path)
        os.chmod(public_path, 0o600)
        print(f"WORKER_KEY_FINGERPRINT={hashlib.sha256(public).hexdigest()}")
        return 0
    except Exception:
        print("WORKER_KEY_GENERATION_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
