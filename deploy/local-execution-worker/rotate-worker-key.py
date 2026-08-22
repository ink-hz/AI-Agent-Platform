#!/usr/bin/env python3
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
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
    "agent-brain-bot",
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
COMPONENTS = ("private", "public", "plist")
CANONICAL_PATHS = (PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
NEXT_PATHS = (NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST)
PREVIOUS_PATHS = (PREVIOUS_PRIVATE_KEY, PREVIOUS_PUBLIC_DOCUMENT, PREVIOUS_PLIST)
ACTIVE_LOCK_FD = -1
PART_PATHS = tuple(
    path.parent / f".{path.name}.part"
    for path in (*CANONICAL_PATHS, *NEXT_PATHS, *PREVIOUS_PATHS, STATE)
)


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
    temporary = path.parent / f".{path.name}.part"
    _unlink_part(temporary)
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
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _unlink_part(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
        or metadata.st_size > 1_048_576
    ):
        raise RotationError
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _cleanup_parts() -> None:
    for path in PART_PATHS:
        _unlink_part(path)


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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode == 0:
        return True
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    if (
        f'Could not find service "{LABEL}"' in output
        and "in domain for user gui:" in output
    ):
        return False
    raise RotationError


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
    expected_keys = {
        "schema_version",
        "phase",
        "from_key_id",
        "to_key_id",
        "previous_sha256",
        "next_sha256",
        "was_loaded",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 2
        or value["phase"] not in {
            "prepared",
            "activating",
            "active",
            "rolled_back",
            "finalized",
        }
        or not isinstance(value["from_key_id"], str)
        or KEY_ID.fullmatch(value["from_key_id"]) is None
        or not isinstance(value["to_key_id"], str)
        or KEY_ID.fullmatch(value["to_key_id"]) is None
        or value["from_key_id"] == value["to_key_id"]
    ):
        raise RotationError
    for name in ("previous_sha256", "next_sha256"):
        digests = value[name]
        if (
            not isinstance(digests, dict)
            or set(digests) != set(COMPONENTS)
            or any(
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for digest in digests.values()
            )
        ):
            raise RotationError
    if value["phase"] == "prepared":
        if value["was_loaded"] is not None:
            raise RotationError
    elif not isinstance(value["was_loaded"], bool):
        raise RotationError
    return value


def _write_state(value: dict[str, object]) -> None:
    _atomic_write(
        STATE,
        (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )


def _digests(values: tuple[bytes, bytes, bytes]) -> dict[str, str]:
    return {
        name: hashlib.sha256(value).hexdigest()
        for name, value in zip(COMPONENTS, values, strict=True)
    }


def _raw_components(paths: tuple[Path, Path, Path]) -> tuple[bytes, bytes, bytes]:
    values = (
        _secure_file(paths[0], maximum_size=32),
        _secure_file(paths[1], maximum_size=65_536),
        _secure_file(paths[2], maximum_size=65_536),
    )
    if len(values[0]) != 32:
        raise RotationError
    return values


def _matches(values: tuple[bytes, bytes, bytes], expected: object) -> bool:
    return isinstance(expected, dict) and _digests(values) == expected


def _validated_identity(
    paths: tuple[Path, Path, Path], expected_key_id: object, expected_digests: object
) -> tuple[bytes, bytes, bytes]:
    key_id, values = _identity(*paths)
    if key_id != expected_key_id or not _matches(values, expected_digests):
        raise RotationError
    return values


def _validate_remaining(
    paths: tuple[Path, Path, Path], expected_digests: object
) -> None:
    if not isinstance(expected_digests, dict):
        raise RotationError
    maximum_sizes = (32, 65_536, 65_536)
    for name, path, maximum_size in zip(
        COMPONENTS, paths, maximum_sizes, strict=True
    ):
        if not path.exists() and not path.is_symlink():
            continue
        value = _secure_file(path, maximum_size=maximum_size)
        if (name == "private" and len(value) != 32) or (
            hashlib.sha256(value).hexdigest() != expected_digests[name]
        ):
            raise RotationError


def _cleanup_transaction(value: dict[str, object]) -> None:
    _validate_remaining(PREVIOUS_PATHS, value["previous_sha256"])
    _validate_remaining(NEXT_PATHS, value["next_sha256"])
    for path in (*NEXT_PATHS, *PREVIOUS_PATHS):
        _unlink(path)
    _unlink(STATE)


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
    current_key_id, current = _identity(PRIVATE_KEY, PUBLIC_DOCUMENT, PLIST)
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
        environment = dict(os.environ)
        environment["PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD"] = str(ACTIVE_LOCK_FD)
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
            env=environment,
            pass_fds=(ACTIVE_LOCK_FD,),
        )
        current_plist = plistlib.loads(_secure_file(PLIST, maximum_size=65_536))
        current_plist["EnvironmentVariables"]["PLATFORM_WORKER_KEY_ID"] = target_key_id
        _atomic_write(NEXT_PLIST, plistlib.dumps(current_plist, fmt=plistlib.FMT_XML))
        staged_key_id, _staged = _identity(
            NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT, NEXT_PLIST
        )
        if staged_key_id != target_key_id:
            raise RotationError
        state = {
            "schema_version": 2,
            "phase": "prepared",
            "from_key_id": current_key_id,
            "to_key_id": target_key_id,
            "previous_sha256": _digests(current),
            "next_sha256": _digests(_staged),
            "was_loaded": None,
        }
        _write_state(state)
    except Exception:
        if not STATE.exists() and not STATE.is_symlink():
            for path in NEXT_PATHS:
                _unlink(path)
        raise


def _abort(target_key_id: str) -> None:
    current_key_id, current = _identity(*CANONICAL_PATHS)
    if current_key_id == target_key_id:
        raise RotationError
    if STATE.exists() or STATE.is_symlink():
        value = _state()
        if (
            value["phase"] != "prepared"
            or value["to_key_id"] != target_key_id
            or value["from_key_id"] != current_key_id
            or not _matches(current, value["previous_sha256"])
        ):
            raise RotationError
        _cleanup_transaction(value)
        return
    _managed_absent(PREVIOUS_PATHS)
    private_exists, public_exists, plist_exists = tuple(
        path.exists() or path.is_symlink() for path in NEXT_PATHS
    )
    if private_exists:
        private = _secure_file(NEXT_PRIVATE_KEY, maximum_size=32)
        if len(private) != 32:
            raise RotationError
    if public_exists:
        document = json.loads(_secure_file(NEXT_PUBLIC_DOCUMENT, maximum_size=65_536))
        if (
            not isinstance(document, dict)
            or set(document) != {
                "worker_id", "key_id", "public_key_base64url", "allowed_agent_ids"
            }
            or document["worker_id"] != WORKER_ID
            or document["key_id"] != target_key_id
            or document["allowed_agent_ids"] != list(AGENTS)
            or not isinstance(document["public_key_base64url"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", document["public_key_base64url"])
            is None
        ):
            raise RotationError
        try:
            public = base64.b64decode(
                document["public_key_base64url"] + "=",
                altchars=b"-_",
                validate=True,
            )
        except Exception as error:
            raise RotationError from error
        if (
            len(public) != 32
            or base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")
            != document["public_key_base64url"]
        ):
            raise RotationError
    if private_exists and public_exists:
        staged_key_id, _staged = _document(NEXT_PRIVATE_KEY, NEXT_PUBLIC_DOCUMENT)
        if staged_key_id != target_key_id:
            raise RotationError
    if plist_exists:
        _plist(NEXT_PLIST, target_key_id)
    for path in NEXT_PATHS:
        _unlink(path)


def _ensure_previous(value: dict[str, object]) -> tuple[bytes, bytes, bytes]:
    canonical = _raw_components(CANONICAL_PATHS)
    expected = value["previous_sha256"]
    if not isinstance(expected, dict):
        raise RotationError
    _validate_remaining(PREVIOUS_PATHS, expected)
    for name, canonical_value, previous_path in zip(
        COMPONENTS, canonical, PREVIOUS_PATHS, strict=True
    ):
        if (
            not previous_path.exists()
            and not previous_path.is_symlink()
            and hashlib.sha256(canonical_value).hexdigest() != expected[name]
        ):
            raise RotationError
    for canonical_value, previous_path in zip(
        canonical, PREVIOUS_PATHS, strict=True
    ):
        if previous_path.exists() or previous_path.is_symlink():
            continue
        _atomic_write(previous_path, canonical_value)
    return _validated_identity(
        PREVIOUS_PATHS, value["from_key_id"], value["previous_sha256"]
    )


def _activate(target_key_id: str) -> None:
    value = _state()
    if value["phase"] != "prepared" or value["to_key_id"] != target_key_id:
        raise RotationError
    current = _validated_identity(
        CANONICAL_PATHS, value["from_key_id"], value["previous_sha256"]
    )
    staged = _validated_identity(
        NEXT_PATHS, target_key_id, value["next_sha256"]
    )
    _managed_absent(PREVIOUS_PATHS)
    was_loaded = _loaded()
    value["phase"] = "activating"
    value["was_loaded"] = was_loaded
    _write_state(value)
    try:
        for path, component in zip(PREVIOUS_PATHS, current, strict=True):
            _atomic_write(path, component)
        _set_loaded(False)
        for path, component in zip(CANONICAL_PATHS, staged, strict=True):
            _atomic_write(path, component)
        _validated_identity(CANONICAL_PATHS, target_key_id, value["next_sha256"])
        _set_loaded(was_loaded)
        value["phase"] = "active"
        _write_state(value)
    except Exception:
        try:
            _rollback(target_key_id)
        except Exception:
            print("EXECUTION_WORKER_KEY_ROTATION_ROLLBACK_FAILED", file=sys.stderr)
            raise RotationError
        raise


def _rollback(target_key_id: str) -> None:
    value = _state()
    if value["to_key_id"] != target_key_id or value["phase"] not in {
        "activating", "active", "rolled_back"
    }:
        raise RotationError
    if value["phase"] == "rolled_back":
        _validated_identity(
            CANONICAL_PATHS, value["from_key_id"], value["previous_sha256"]
        )
        _validate_remaining(PREVIOUS_PATHS, value["previous_sha256"])
        _validate_remaining(NEXT_PATHS, value["next_sha256"])
        _set_loaded(bool(value["was_loaded"]))
        _cleanup_transaction(value)
        return
    staged = _validated_identity(NEXT_PATHS, target_key_id, value["next_sha256"])
    canonical = _raw_components(CANONICAL_PATHS)
    previous_digests = value["previous_sha256"]
    next_digests = value["next_sha256"]
    if not isinstance(previous_digests, dict) or not isinstance(next_digests, dict):
        raise RotationError
    for name, component in zip(COMPONENTS, canonical, strict=True):
        digest = hashlib.sha256(component).hexdigest()
        if digest not in {previous_digests[name], next_digests[name]}:
            raise RotationError
    previous = _ensure_previous(value)
    for component, old, new in zip(canonical, previous, staged, strict=True):
        if component != old and component != new:
            raise RotationError
    _set_loaded(False)
    for path, component in zip(CANONICAL_PATHS, previous, strict=True):
        _atomic_write(path, component)
    _validated_identity(
        CANONICAL_PATHS, value["from_key_id"], value["previous_sha256"]
    )
    _set_loaded(bool(value["was_loaded"]))
    value["phase"] = "rolled_back"
    _write_state(value)
    _cleanup_transaction(value)


def _finalize(target_key_id: str) -> None:
    value = _state()
    if value["to_key_id"] != target_key_id or value["phase"] not in {
        "active", "finalized"
    }:
        raise RotationError
    _validated_identity(CANONICAL_PATHS, target_key_id, value["next_sha256"])
    if value["phase"] == "active":
        _validated_identity(
            PREVIOUS_PATHS, value["from_key_id"], value["previous_sha256"]
        )
        _validated_identity(NEXT_PATHS, target_key_id, value["next_sha256"])
        value["phase"] = "finalized"
        _write_state(value)
    _cleanup_transaction(value)


def main(arguments: list[str] | None = None) -> int:
    global ACTIVE_LOCK_FD
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
        ACTIVE_LOCK_FD = lock
        os.set_inheritable(lock, True)
        try:
            _cleanup_parts()
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
