#!/usr/bin/env python3
from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import secrets
import stat
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


REQUIRED_USER = "agentops"
WORKER_ID = "agentops-mac-primary"
LABEL = "com.orbbec.agent-execution-worker"
KEY_ID = re.compile(r"worker-v[1-9][0-9]*\Z")
AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
)
RUNTIME_ROOT = Path("/Users/agentops/AgentRuntime")
PRIVATE_ROOT = RUNTIME_ROOT / "private"
LAUNCH_ROOT = Path("/Users/agentops/Library/LaunchAgents")
PRIVATE_KEY = PRIVATE_ROOT / "execution-worker-ed25519.key"
PUBLIC_DOCUMENT = RUNTIME_ROOT / "execution-worker-public.json"
PLIST = LAUNCH_ROOT / f"{LABEL}.plist"
NEXT_PRIVATE_KEY = PRIVATE_ROOT / "execution-worker-ed25519.next.key"
NEXT_PUBLIC_DOCUMENT = RUNTIME_ROOT / "execution-worker-public.next.json"
NEXT_PLIST = LAUNCH_ROOT / f"{LABEL}.next.plist"
PREVIOUS_PRIVATE_KEY = PRIVATE_ROOT / "execution-worker-ed25519.previous.key"
PREVIOUS_PUBLIC_DOCUMENT = RUNTIME_ROOT / "execution-worker-public.previous.json"
PREVIOUS_PLIST = LAUNCH_ROOT / f"{LABEL}.previous.plist"
STATE = PRIVATE_ROOT / "execution-worker-key-rotation-state.json"
LOCK = PRIVATE_ROOT / "execution-worker-key-rotation.lock"
GENERATOR = Path(__file__).with_name("generate-worker-key.py")
LAUNCHCTL = "/bin/launchctl"


class RotationError(ValueError):
    pass


def _secure_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise RotationError


def _secure_file(path: Path, *, maximum_size: int = 1_048_576) -> bytes:
    _secure_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_size < 1
            or metadata.st_size > maximum_size
        ):
            raise RotationError
        value = os.read(descriptor, maximum_size + 1)
        if len(value) != metadata.st_size or os.read(descriptor, 1):
            raise RotationError
        return value
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, value: bytes) -> None:
    _secure_directory(path.parent)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(16)}.part"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise RotationError
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unlink(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or metadata.st_uid != os.getuid():
        raise RotationError
    path.unlink()


def _document(private_path: Path, public_path: Path) -> tuple[str, bytes]:
    private = _secure_file(private_path, maximum_size=32)
    if len(private) != 32:
        raise RotationError
    value = json.loads(_secure_file(public_path, maximum_size=65_536))
    if not isinstance(value, dict) or set(value) != {
        "worker_id",
        "key_id",
        "public_key_base64url",
        "allowed_agent_ids",
    }:
        raise RotationError
    key_id = value["key_id"]
    if (
        value["worker_id"] != WORKER_ID
        or not isinstance(key_id, str)
        or KEY_ID.fullmatch(key_id) is None
        or value["allowed_agent_ids"] != list(AGENTS)
    ):
        raise RotationError
    public = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    encoded = base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")
    if value["public_key_base64url"] != encoded:
        raise RotationError
    return key_id, private


def _plist(path: Path, expected_key_id: str) -> bytes:
    raw = _secure_file(path, maximum_size=65_536)
    value = plistlib.loads(raw)
    environment = value.get("EnvironmentVariables") if isinstance(value, dict) else None
    if (
        value.get("Label") != LABEL
        or not isinstance(environment, dict)
        or environment.get("PLATFORM_WORKER_ID") != WORKER_ID
        or environment.get("PLATFORM_WORKER_KEY_ID") != expected_key_id
        or environment.get("PLATFORM_WORKER_PRIVATE_KEY_FILE") != str(PRIVATE_KEY)
    ):
        raise RotationError
    return raw


def _identity(
    private_path: Path, public_path: Path, plist_path: Path
) -> tuple[str, tuple[bytes, bytes, bytes]]:
    key_id, _private = _document(private_path, public_path)
    values = (
        _secure_file(private_path, maximum_size=32),
        _secure_file(public_path, maximum_size=65_536),
        _plist(plist_path, key_id),
    )
    return key_id, values


def _loaded() -> bool:
    result = subprocess.run(
        [LAUNCHCTL, "print", f"gui/{os.getuid()}/{LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _launch(arguments: list[str]) -> None:
    subprocess.run(
        [LAUNCHCTL, *arguments],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def _set_loaded(desired: bool) -> None:
    domain = f"gui/{os.getuid()}"
    if _loaded():
        _launch(["bootout", f"{domain}/{LABEL}"])
    if desired:
        _launch(["bootstrap", domain, str(PLIST)])
        _launch(["enable", f"{domain}/{LABEL}"])
        _launch(["kickstart", "-k", f"{domain}/{LABEL}"])


def _state() -> dict[str, object]:
    value = json.loads(_secure_file(STATE, maximum_size=4096))
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "from_key_id", "to_key_id", "was_loaded"}
        or value["schema_version"] != 1
        or not isinstance(value["from_key_id"], str)
        or KEY_ID.fullmatch(value["from_key_id"]) is None
        or not isinstance(value["to_key_id"], str)
        or KEY_ID.fullmatch(value["to_key_id"]) is None
        or not isinstance(value["was_loaded"], bool)
        or value["from_key_id"] == value["to_key_id"]
    ):
        raise RotationError
    return value


def _managed_absent(paths: tuple[Path, ...]) -> None:
    if any(path.exists() or path.is_symlink() for path in paths):
        raise RotationError


def _acquire_lock() -> int:
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
            raise RotationError
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        value = os.read(descriptor, 64)
        if metadata.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"rotation-lock\n")
            os.fsync(descriptor)
        elif value != b"rotation-lock\n" or os.read(descriptor, 1):
            raise RotationError
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _prepare(target_key_id: str) -> None:
    current_key_id, _current = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
    if current_key_id == target_key_id:
        raise RotationError
    _managed_absent((
        NEXT_PRIVATE_KEY,
        NEXT_PUBLIC_DOCUMENT,
        NEXT_PLIST,
        PREVIOUS_PRIVATE_KEY,
        PREVIOUS_PUBLIC_DOCUMENT,
        PREVIOUS_PLIST,
        STATE,
    ))
    try:
        subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                str(NEXT_PRIVATE_KEY),
                str(NEXT_PUBLIC_DOCUMENT),
                target_key_id,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        current_plist = plistlib.loads(_secure_file(PLIST, maximum_size=65_536))
        current_plist["EnvironmentVariables"]["PLATFORM_WORKER_KEY_ID"] = target_key_id
        _atomic_write(NEXT_PLIST, plistlib.dumps(current_plist, fmt=plistlib.FMT_XML))
        staged_key_id, _staged = _identity(
            NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST
        )
        if staged_key_id != target_key_id:
            raise RotationError
    except Exception:
        for path in (NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST):
            _unlink(path)
        raise


def _abort(target_key_id: str) -> None:
    _managed_absent((
        PREVIOUS_PRIVATE_KEY,
        PREVIOUS_PUBLIC_DOCUMENT,
        PREVIOUS_PLIST,
        STATE,
    ))
    current_key_id, _current = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
    staged_key_id, _staged = _identity(
        NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST
    )
    if current_key_id == target_key_id or staged_key_id != target_key_id:
        raise RotationError
    for path in (NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST):
        _unlink(path)


def _cleanup_previous() -> None:
    for path in (PREVIOUS_PRIVATE_KEY, PREVIOUS_PUBLIC_DOCUMENT, PREVIOUS_PLIST, STATE):
        _unlink(path)


def _restore_previous(values: dict[str, object]) -> None:
    from_key_id = values["from_key_id"]
    previous_key_id, previous = _identity(
        PREVIOUS_PRIVATE_KEY, PREVIOUS_PUBLIC_DOCUMENT, PREVIOUS_PLIST
    )
    if previous_key_id != from_key_id:
        raise RotationError
    _set_loaded(False)
    for path, value in zip((PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST), previous, strict=True):
        _atomic_write(path, value)
    restored_key_id, _restored = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
    if restored_key_id != from_key_id:
        raise RotationError
    _set_loaded(bool(values["was_loaded"]))
    _cleanup_previous()


def _activate(target_key_id: str) -> None:
    _managed_absent((PREVIOUS_PRIVATE_KEY, PREVIOUS_PUBLIC_DOCUMENT, PREVIOUS_PLIST, STATE))
    current_key_id, current = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
    staged_key_id, staged = _identity(NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST)
    if staged_key_id != target_key_id or current_key_id == target_key_id:
        raise RotationError
    was_loaded = _loaded()
    state = {
        "schema_version": 1,
        "from_key_id": current_key_id,
        "to_key_id": target_key_id,
        "was_loaded": was_loaded,
    }
    try:
        for path, value in zip(
            (PREVIOUS_PRIVATE_KEY, PREVIOUS_PUBLIC_DOCUMENT, PREVIOUS_PLIST),
            current,
            strict=True,
        ):
            _atomic_write(path, value)
        _atomic_write(
            STATE,
            (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        _set_loaded(False)
        for path, value in zip((PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST), staged, strict=True):
            _atomic_write(path, value)
        active_key_id, _active = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
        if active_key_id != target_key_id:
            raise RotationError
        _set_loaded(was_loaded)
    except Exception:
        try:
            if STATE.exists() and all(
                path.exists()
                for path in (PREVIOUS_PRIVATE_KEY, PREVIOUS_PUBLIC_DOCUMENT, PREVIOUS_PLIST)
            ):
                _restore_previous(state)
            else:
                _cleanup_previous()
        except Exception:
            print("EXECUTION_WORKER_KEY_ROTATION_ROLLBACK_FAILED", file=sys.stderr)
            raise RotationError
        raise
    for path in (NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST):
        _unlink(path)


def _rollback(target_key_id: str) -> None:
    state = _state()
    active_key_id, _active = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
    if state["to_key_id"] != target_key_id or active_key_id != target_key_id:
        raise RotationError
    _restore_previous(state)


def _finalize(target_key_id: str) -> None:
    state = _state()
    active_key_id, _active = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
    previous_key_id, _previous = _identity(
        PREVIOUS_PRIVATE_KEY, PREVIOUS_PUBLIC_DOCUMENT, PREVIOUS_PLIST
    )
    if (
        state["to_key_id"] != target_key_id
        or active_key_id != target_key_id
        or previous_key_id != state["from_key_id"]
    ):
        raise RotationError
    _cleanup_previous()


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        if (
            pwd.getpwuid(os.getuid()).pw_name != REQUIRED_USER
            or len(values) != 2
            or values[0] not in {"prepare", "abort", "activate", "rollback", "finalize"}
            or KEY_ID.fullmatch(values[1]) is None
        ):
            raise RotationError
        for directory in (RUNTIME_ROOT, PRIVATE_ROOT, LAUNCH_ROOT):
            _secure_directory(directory)
        action, target_key_id = values
        lock = _acquire_lock()
        try:
            {
                "prepare": _prepare,
                "abort": _abort,
                "activate": _activate,
                "rollback": _rollback,
                "finalize": _finalize,
            }[action](target_key_id)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
        completed = {
            "prepare": "PREPARED",
            "abort": "ABORTED",
            "activate": "ACTIVATED",
            "rollback": "ROLLED_BACK",
            "finalize": "FINALIZED",
        }[action]
        print(f"EXECUTION_WORKER_KEY_{completed} key_id={target_key_id}")
        return 0
    except Exception:
        print("EXECUTION_WORKER_KEY_ROTATION_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
