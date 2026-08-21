#!/usr/bin/env python3
from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys


REQUIRED_UID = 0
PLATFORM_ROOT = Path("/opt/orbbec-agent-platform")
PRIVATE_ROOT = PLATFORM_ROOT / "private"
KEYRING = PRIVATE_ROOT / "execution-worker-public-keyring.json"
PART = PRIVATE_ROOT / "execution-worker-public-keyring.json.part"
STATE = PRIVATE_ROOT / "execution-worker-key-rotation-state.json"
LOCK = PRIVATE_ROOT / "execution-worker-key-rotation.lock"
AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
)


class InstallError(ValueError):
    pass


def _directory(path: Path, modes: set[int]) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or stat.S_IMODE(metadata.st_mode) not in modes
        or metadata.st_uid != os.getuid()
    ):
        raise InstallError


def _optional_file(path: Path, *, allow_empty: bool) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or (not allow_empty and metadata.st_size < 1)
            or metadata.st_size > 65_536
        ):
            raise InstallError
    finally:
        os.close(descriptor)


def _fsync_private() -> None:
    descriptor = os.open(PRIVATE_ROOT, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_part() -> None:
    _optional_file(PART, allow_empty=True)
    try:
        PART.unlink()
    except FileNotFoundError:
        return
    _fsync_private()


def _validate_document(raw: bytes) -> None:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        "worker_id", "key_id", "public_key_base64url", "allowed_agent_ids"
    }:
        raise InstallError
    encoded = value["public_key_base64url"]
    if (
        value["worker_id"] != "agentops-mac-primary"
        or not isinstance(value["key_id"], str)
        or re.fullmatch(r"worker-v[1-9][0-9]*", value["key_id"]) is None
        or value["allowed_agent_ids"] != list(AGENTS)
        or not isinstance(encoded, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded) is None
    ):
        raise InstallError
    public = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    if len(public) != 32 or base64.urlsafe_b64encode(public).decode().rstrip("=") != encoded:
        raise InstallError


def _lock() -> int:
    descriptor = os.open(
        LOCK,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise InstallError
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def main() -> int:
    descriptor = -1
    try:
        if os.getuid() != REQUIRED_UID or len(sys.argv) != 1:
            raise InstallError
        _directory(PLATFORM_ROOT, {0o700, 0o755})
        _directory(PRIVATE_ROOT, {0o700})
        raw = sys.stdin.buffer.read(65_537)
        if not raw or len(raw) > 65_536:
            raise InstallError
        _validate_document(raw)
        descriptor = _lock()
        if STATE.exists() or STATE.is_symlink():
            raise InstallError
        _optional_file(KEYRING, allow_empty=False)
        _unlink_part()
        output = os.open(
            PART,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(output, 0o600)
            offset = 0
            while offset < len(raw):
                written = os.write(output, raw[offset:])
                if written <= 0:
                    raise InstallError
                offset += written
            os.fsync(output)
        finally:
            os.close(output)
        if STATE.exists() or STATE.is_symlink():
            raise InstallError
        os.replace(PART, KEYRING)
        _fsync_private()
        print("EXECUTION_WORKER_KEYRING_INSTALLED")
        return 0
    except Exception:
        print("EXECUTION_WORKER_KEYRING_INSTALL_FAILED", file=sys.stderr)
        return 1
    finally:
        if descriptor >= 0:
            try:
                _unlink_part()
            except Exception:
                pass
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
