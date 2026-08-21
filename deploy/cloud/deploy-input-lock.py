#!/usr/bin/python3
from __future__ import annotations

import json
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


def _acquire(release_sha: str, deployment_id: str) -> None:
    PRIVATE_ROOT.mkdir(mode=0o700, exist_ok=True)
    _directory(PRIVATE_ROOT, {0o700})
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
    _validate(release_sha, deployment_id)
    STATE.unlink()
    _fsync(LOCK_ROOT)
    LOCK_ROOT.rmdir()
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
        action, release_sha, deployment_id = values
        {"acquire": _acquire, "validate": _validate, "release": _release}[action](
            release_sha, deployment_id
        )
        print(f"CLOUD_DEPLOY_INPUT_OK action={action}")
        return 0
    except Exception:
        print("CLOUD_DEPLOY_INPUT_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
