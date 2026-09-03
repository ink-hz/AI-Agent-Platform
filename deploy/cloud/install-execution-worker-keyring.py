#!/usr/bin/python3
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
STAGING_ROOT = Path("/data/staging/orbbec-agent-platform")
REMOTE_STAGE = Path("/opt/orbbec-agent-platform/bin/remote-stage.sh")
STATE = PRIVATE_ROOT / "execution-worker-key-rotation-state.json"
DEPLOY_STATE = PRIVATE_ROOT / "execution-worker-keyring-deploy-state.json"
DEPLOY_STATE_PART = PRIVATE_ROOT / "execution-worker-keyring-deploy-state.json.part"
DEPLOY_BACKUP = PRIVATE_ROOT / "execution-worker-public-keyring.deploy.previous.json"
DEPLOY_INPUT_ROOT = PRIVATE_ROOT / "deploy-input.lock"
DEPLOY_INPUT_STATE = DEPLOY_INPUT_ROOT / "owner.json"
LOCK = PRIVATE_ROOT / "execution-worker-key-rotation.lock"
RELEASE = re.compile(r"[0-9a-f]{40}\Z")
DEPLOYMENT = re.compile(r"[0-9a-f]{32}\Z")
AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "agent-brain-bot",
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


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_part(part: Path) -> None:
    _optional_file(part, allow_empty=True)
    try:
        part.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(part.parent)


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


def _validate_deploy_input(release_sha: str, deployment_id: str) -> None:
    _directory(DEPLOY_INPUT_ROOT, {0o700})
    raw = _secure_value(DEPLOY_INPUT_STATE)
    if raw != (
        json.dumps(
            {"deployment_id": deployment_id, "release_sha": release_sha},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode():
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


def _stage(release_sha: str, deployment_id: str) -> None:
    descriptor = _lock()
    try:
        _validate_deploy_input(release_sha, deployment_id)
        if any(
            path.exists() or path.is_symlink()
            for path in (STATE, DEPLOY_STATE, DEPLOY_STATE_PART, DEPLOY_BACKUP)
        ):
            raise InstallError
        if STAGING_ROOT == Path(
            "/data/staging/orbbec-agent-platform"
        ) and not os.path.ismount("/data"):
            raise InstallError
        STAGING_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(STAGING_ROOT, 0o700)
        _directory(STAGING_ROOT, {0o700})
        release_root = STAGING_ROOT / deployment_id
        release_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(release_root, 0o700)
        _directory(release_root, {0o700})
        target = release_root / "execution-worker-public-keyring.json"
        part = release_root / ".execution-worker-public-keyring.json.part"
        raw = sys.stdin.buffer.read(65_537)
        if not raw or len(raw) > 65_536:
            raise InstallError
        _validate_document(raw)
        _unlink_part(part)
        output = os.open(
            part,
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
        os.replace(part, target)
        _fsync_directory(release_root)
    finally:
        os.close(descriptor)


def _discard(release_sha: str, deployment_id: str) -> None:
    descriptor = _lock()
    try:
        _validate_deploy_input(release_sha, deployment_id)
        if any(
            path.exists() or path.is_symlink()
            for path in (STATE, DEPLOY_STATE, DEPLOY_STATE_PART, DEPLOY_BACKUP)
        ):
            raise InstallError
        release_root = STAGING_ROOT / deployment_id
        target = release_root / "execution-worker-public-keyring.json"
        part = release_root / ".execution-worker-public-keyring.json.part"
        if not (release_root.exists() or release_root.is_symlink()):
            return
        _directory(STAGING_ROOT, {0o700})
        _directory(release_root, {0o700})
        if target.exists() or target.is_symlink():
            _validate_document(_secure_value(target))
        _unlink_part(part)
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(release_root)
        release_root.rmdir()
        _fsync_directory(STAGING_ROOT)
    finally:
        os.close(descriptor)


def _cutover(release_sha: str, digest: str, deployment_id: str) -> None:
    staged = STAGING_ROOT / deployment_id / "execution-worker-public-keyring.json"
    descriptor = _lock()
    try:
        _validate_deploy_input(release_sha, deployment_id)
        if STATE.exists() or STATE.is_symlink():
            raise InstallError
        if not (DEPLOY_STATE.exists() or DEPLOY_STATE.is_symlink()):
            _validate_document(_secure_value(staged))
        remote = REMOTE_STAGE.lstat()
        if (
            not stat.S_ISREG(remote.st_mode)
            or REMOTE_STAGE.is_symlink()
            or stat.S_IMODE(remote.st_mode) != 0o700
            or remote.st_uid != os.getuid()
        ):
            raise InstallError
        os.set_inheritable(descriptor, True)
        os.execve(
            "/bin/bash",
            ["/bin/bash", str(REMOTE_STAGE), release_sha, digest, deployment_id],
            {
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C.UTF-8",
                "PLATFORM_EXECUTION_WORKER_DEPLOY_LOCK_FD": str(descriptor),
            },
        )
    finally:
        os.close(descriptor)


def _secure_value(path: Path) -> bytes:
    _directory(path.parent, {0o700})
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_size < 1
            or metadata.st_size > 65_536
        ):
            raise InstallError
        raw = os.read(descriptor, 65_537)
        if len(raw) != metadata.st_size or os.read(descriptor, 1):
            raise InstallError
        return raw
    finally:
        os.close(descriptor)


def main() -> int:
    try:
        values = sys.argv[1:]
        if (
            os.getuid() != REQUIRED_UID
            or not values
            or values[0] not in {"stage", "discard", "cutover"}
            or len(values) != (4 if values[0] == "cutover" else 3)
            or RELEASE.fullmatch(values[1]) is None
            or DEPLOYMENT.fullmatch(values[-1]) is None
            or (values[0] == "cutover" and re.fullmatch(r"[0-9a-f]{64}", values[2]) is None)
        ):
            raise InstallError
        _directory(PLATFORM_ROOT, {0o700, 0o755})
        _directory(PRIVATE_ROOT, {0o700})
        if values[0] == "stage":
            _stage(values[1], values[2])
            print("EXECUTION_WORKER_KEYRING_STAGED")
        elif values[0] == "discard":
            _discard(values[1], values[2])
            print("EXECUTION_WORKER_KEYRING_DISCARDED")
        else:
            _cutover(values[1], values[2], values[3])
        return 0
    except Exception:
        print("EXECUTION_WORKER_KEYRING_INSTALL_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
