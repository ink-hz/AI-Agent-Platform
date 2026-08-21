#!/usr/bin/python3
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import re
import stat
import sys


REQUIRED_UID = 0
PLATFORM_ROOT = Path("/opt/orbbec-agent-platform")
PRIVATE_ROOT = PLATFORM_ROOT / "private"
LOCK_ROOT = PRIVATE_ROOT / "deploy-input.lock"
STATE = LOCK_ROOT / "owner.json"
STATE_PART = LOCK_ROOT / "owner.json.part"
TRANSACTION_LOCK = PRIVATE_ROOT / "deploy-input.transaction.lock"
RELEASING_PREFIX = "deploy-input.releasing-"
RELEASE = re.compile(r"[0-9a-f]{40}\Z")
DEPLOYMENT = re.compile(r"[0-9a-f]{32}\Z")


class DeployInputError(ValueError):
    pass


def _directory(path: Path, modes: set[int]) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or stat.S_IMODE(metadata.st_mode) not in modes
        or metadata.st_uid != os.getuid()
    ):
        raise DeployInputError


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _expected(release_sha: str, deployment_id: str) -> bytes:
    return (
        json.dumps(
            {"deployment_id": deployment_id, "release_sha": release_sha},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _validate(release_sha: str, deployment_id: str) -> None:
    _directory(PRIVATE_ROOT, {0o700})
    _directory(LOCK_ROOT, {0o700})
    descriptor = os.open(STATE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        raw = os.read(descriptor, 1025)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_size != len(raw)
            or raw != _expected(release_sha, deployment_id)
        ):
            raise DeployInputError
    finally:
        os.close(descriptor)
    if STATE_PART.exists() or STATE_PART.is_symlink():
        raise DeployInputError


def _transaction_lock() -> int:
    try:
        descriptor = os.open(
            TRANSACTION_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
    except FileExistsError:
        descriptor = os.open(
            TRANSACTION_LOCK,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise DeployInputError
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _tombstone(release_sha: str, deployment_id: str) -> Path:
    return PRIVATE_ROOT / f"{RELEASING_PREFIX}{release_sha}-{deployment_id}"


def _validate_tombstone(
    tombstone: Path, release_sha: str, deployment_id: str
) -> Path | None:
    _directory(tombstone, {0o700})
    entries = {path.name for path in tombstone.iterdir()}
    if not entries <= {STATE.name}:
        raise DeployInputError
    tombstone_state = tombstone / STATE.name
    if not entries:
        return None
    descriptor = os.open(
        tombstone_state, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        metadata = os.fstat(descriptor)
        raw = os.read(descriptor, 1025)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_size != len(raw)
            or raw != _expected(release_sha, deployment_id)
        ):
            raise DeployInputError
    finally:
        os.close(descriptor)
    return tombstone_state


def _acquire(release_sha: str, deployment_id: str) -> None:
    if any(
        path.name.startswith(RELEASING_PREFIX)
        for path in PRIVATE_ROOT.iterdir()
    ):
        raise DeployInputError
    try:
        LOCK_ROOT.mkdir(mode=0o700)
    except FileExistsError as error:
        raise DeployInputError from error
    created = True
    try:
        os.chmod(LOCK_ROOT, 0o700)
        _fsync(PRIVATE_ROOT)
        descriptor = os.open(
            STATE_PART,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            raw = _expected(release_sha, deployment_id)
            if os.write(descriptor, raw) != len(raw):
                raise DeployInputError
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(STATE_PART, STATE)
        _fsync(LOCK_ROOT)
        created = False
    finally:
        if created:
            try:
                STATE_PART.unlink()
            except FileNotFoundError:
                pass
            try:
                STATE.unlink()
            except FileNotFoundError:
                pass
            try:
                LOCK_ROOT.rmdir()
            except OSError:
                pass
            _fsync(PRIVATE_ROOT)


def _release(release_sha: str, deployment_id: str) -> None:
    tombstone = _tombstone(release_sha, deployment_id)
    tombstones = {
        path
        for path in PRIVATE_ROOT.iterdir()
        if path.name.startswith(RELEASING_PREFIX)
    }
    if tombstones and tombstones != {tombstone}:
        raise DeployInputError
    active_exists = LOCK_ROOT.exists() or LOCK_ROOT.is_symlink()
    tombstone_exists = tombstone.exists() or tombstone.is_symlink()
    if active_exists and tombstone_exists:
        raise DeployInputError
    if active_exists:
        _validate(release_sha, deployment_id)
        os.replace(LOCK_ROOT, tombstone)
        _fsync(PRIVATE_ROOT)
        tombstone_exists = True
    if tombstone_exists:
        tombstone_state = _validate_tombstone(
            tombstone, release_sha, deployment_id
        )
        if tombstone_state is not None:
            tombstone_state.unlink()
            _fsync(tombstone)
        tombstone.rmdir()
        _fsync(PRIVATE_ROOT)


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        if (
            os.getuid() != REQUIRED_UID
            or len(values) != 3
            or values[0] not in {"acquire", "validate", "release"}
            or RELEASE.fullmatch(values[1]) is None
            or DEPLOYMENT.fullmatch(values[2]) is None
        ):
            raise DeployInputError
        _directory(PLATFORM_ROOT, {0o700, 0o755})
        PRIVATE_ROOT.mkdir(mode=0o700, exist_ok=True)
        _directory(PRIVATE_ROOT, {0o700})
        action, release_sha, deployment_id = values
        transaction = _transaction_lock()
        try:
            {"acquire": _acquire, "validate": _validate, "release": _release}[
                action
            ](release_sha, deployment_id)
        finally:
            fcntl.flock(transaction, fcntl.LOCK_UN)
            os.close(transaction)
        print(f"CLOUD_DEPLOY_INPUT_OK action={action}")
        return 0
    except Exception:
        print("CLOUD_DEPLOY_INPUT_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
