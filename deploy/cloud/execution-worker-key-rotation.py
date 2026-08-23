#!/usr/bin/python3
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


REQUIRED_UID = 0
WORKER_ID = "agentops-mac-primary"
KEY_ID = re.compile(r"worker-v[1-9][0-9]*\Z")
PLATFORM_ROOT = Path("/opt/orbbec-agent-platform")
INCOMING_ROOT = Path("/root")
PRIVATE_ROOT = PLATFORM_ROOT / "private"
CURRENT_RELEASE = PLATFORM_ROOT / "current"
ENVIRONMENT = PRIVATE_ROOT / "platform.env"
COMPOSE = CURRENT_RELEASE / "deploy/cloud/compose.yaml"
MAINTENANCE_DSN = PRIVATE_ROOT / "control-maintenance-database-url"
KEYRING = PRIVATE_ROOT / "execution-worker-public-keyring.json"
PREVIOUS = PRIVATE_ROOT / "execution-worker-public-keyring.previous.json"
STAGED = PRIVATE_ROOT / "execution-worker-public-keyring.next.json"
STATE = PRIVATE_ROOT / "execution-worker-key-rotation-state.json"
DEPLOY_STATE = PRIVATE_ROOT / "execution-worker-keyring-deploy-state.json"
DEPLOY_STATE_PART = PRIVATE_ROOT / "execution-worker-keyring-deploy-state.json.part"
DEPLOY_BACKUP = PRIVATE_ROOT / "execution-worker-public-keyring.deploy.previous.json"
LOCK = PRIVATE_ROOT / "execution-worker-key-rotation.lock"
KEYRING_PART = PRIVATE_ROOT / "execution-worker-public-keyring.json.part"
PREVIOUS_PART = PRIVATE_ROOT / "execution-worker-public-keyring.previous.json.part"
STAGED_PART = PRIVATE_ROOT / "execution-worker-public-keyring.next.json.part"
STATE_PART = PRIVATE_ROOT / "execution-worker-key-rotation-state.json.part"
DOCKER = "/usr/bin/docker"
PARTS = (KEYRING_PART, PREVIOUS_PART, STAGED_PART, STATE_PART)
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
PHASES = {
    "prepared",
    "adding",
    "cloud_active",
    "accepted",
    "committing",
    "old_revoked",
    "restoring",
    "revoking",
    "revoked",
    "finalizing",
}
INSPECT_SCRIPT = r"""
import json
import psycopg
import sys
from app.execution_relay.register_worker import _secret_file

worker_id, key_id = sys.argv[2:]
with psycopg.connect(_secret_file()) as connection:
    row = connection.execute(
        "select status,encode(sha256(public_key),'hex') "
        "from platform_control.execution_worker_keys "
        "where worker_id=%s and key_id=%s",
        (worker_id, key_id),
    ).fetchone()
value = {
    "key_id": key_id,
    "status": "absent" if row is None else row[0],
    "public_key_sha256": None if row is None else row[1],
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
"""
WORKER_INSPECT_SCRIPT = r"""
import json
import psycopg
import sys
from app.execution_relay.register_worker import _secret_file

worker_id = sys.argv[2]
with psycopg.connect(_secret_file()) as connection:
    row = connection.execute(
        "select status from platform_control.execution_workers where worker_id=%s",
        (worker_id,),
    ).fetchone()
print(json.dumps({"worker_id": worker_id, "status": "absent" if row is None else row[0]}, sort_keys=True))
"""
KEY_INVENTORY_SCRIPT = r"""
import json
import psycopg
import sys
from app.execution_relay.register_worker import _secret_file

worker_id = sys.argv[2]
with psycopg.connect(_secret_file()) as connection:
    rows = connection.execute(
        "select key_id,status,encode(sha256(public_key),'hex') "
        "from platform_control.execution_worker_keys where worker_id=%s order by key_id",
        (worker_id,),
    ).fetchall()
print(json.dumps([
    {"key_id": row[0], "status": row[1], "public_key_sha256": row[2]}
    for row in rows
], sort_keys=True, separators=(",", ":")))
"""
HOST_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}


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


def _secure_code_directory(path: Path, modes: set[int]) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or stat.S_IMODE(metadata.st_mode) not in modes
        or metadata.st_uid != os.getuid()
    ):
        raise RotationError


def _release_compose() -> Path:
    _secure_code_directory(PLATFORM_ROOT, {0o700, 0o755})
    releases = PLATFORM_ROOT / "releases"
    _secure_code_directory(releases, {0o700})
    link = CURRENT_RELEASE.lstat()
    if not stat.S_ISLNK(link.st_mode) or link.st_uid != os.getuid():
        raise RotationError
    raw_target = os.readlink(CURRENT_RELEASE)
    target = Path(raw_target)
    if (
        not target.is_absolute()
        or target.parent != releases
        or re.fullmatch(r"[0-9a-f]{40}", target.name) is None
    ):
        raise RotationError
    _secure_code_directory(target, {0o700})
    deploy = target / "deploy"
    cloud = deploy / "cloud"
    _secure_code_directory(deploy, {0o700})
    _secure_code_directory(cloud, {0o700})
    compose = cloud / "compose.yaml"
    descriptor = os.open(compose, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_size < 1
            or metadata.st_size > 1_048_576
        ):
            raise RotationError
    finally:
        os.close(descriptor)
    return compose


def _secure_file(path: Path, maximum_size: int = 65_536) -> bytes:
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


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink(path: Path) -> None:
    try:
        _secure_file(path)
    except FileNotFoundError:
        return
    path.unlink()
    _fsync_directory(path.parent)


def _unlink_part(path: Path) -> None:
    _secure_directory(path.parent)
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
            or metadata.st_size > 65_536
        ):
            raise RotationError
    finally:
        os.close(descriptor)
    path.unlink()
    _fsync_directory(path.parent)


def _clean_parts() -> None:
    for path in PARTS:
        _unlink_part(path)


def _atomic_write(path: Path, part: Path, value: bytes) -> None:
    _unlink_part(part)
    descriptor = os.open(
        part,
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
    os.replace(part, path)
    _fsync_directory(path.parent)


def _document(path: Path) -> tuple[str, bytes, str, str]:
    raw = _secure_file(path)
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        "worker_id", "key_id", "public_key_base64url", "allowed_agent_ids"
    }:
        raise RotationError
    key_id = value["key_id"]
    encoded = value["public_key_base64url"]
    if (
        value["worker_id"] != WORKER_ID
        or not isinstance(key_id, str)
        or KEY_ID.fullmatch(key_id) is None
        or value["allowed_agent_ids"] != list(AGENTS)
        or not isinstance(encoded, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded) is None
    ):
        raise RotationError
    public = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    if len(public) != 32 or base64.urlsafe_b64encode(public).decode().rstrip("=") != encoded:
        raise RotationError
    return key_id, raw, hashlib.sha256(raw).hexdigest(), hashlib.sha256(public).hexdigest()


def _state() -> dict[str, object]:
    value = json.loads(_secure_file(STATE, 16_384))
    keys = {
        "schema_version", "phase", "from_key_id", "to_key_id",
        "previous_document_sha256", "next_document_sha256",
        "previous_public_sha256", "next_public_sha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["phase"] not in PHASES
        or not isinstance(value["from_key_id"], str)
        or KEY_ID.fullmatch(value["from_key_id"]) is None
        or not isinstance(value["to_key_id"], str)
        or KEY_ID.fullmatch(value["to_key_id"]) is None
        or value["from_key_id"] == value["to_key_id"]
        or any(
            not isinstance(value[name], str)
            or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None
            for name in keys if name.endswith("sha256")
        )
    ):
        raise RotationError
    return value


def _write_state(value: dict[str, object], phase: str) -> None:
    if phase not in PHASES:
        raise RotationError
    updated = {**value, "phase": phase}
    _atomic_write(
        STATE,
        STATE_PART,
        (json.dumps(updated, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    value["phase"] = phase


def _validate_document(path: Path, key_id: object, digest: object, fingerprint: object) -> bytes:
    actual_key_id, raw, actual_digest, actual_fingerprint = _document(path)
    if (
        actual_key_id != key_id
        or actual_digest != digest
        or actual_fingerprint != fingerprint
    ):
        raise RotationError
    return raw


def _image() -> str:
    compose_path = _release_compose()
    compose = [
        DOCKER, "compose", "--env-file", str(ENVIRONMENT), "-f", str(compose_path)
    ]
    container = subprocess.run(
        [*compose, "ps", "-q", "platform-api"],
        check=True, capture_output=True, text=True, env=HOST_ENV,
    ).stdout.strip()
    if not container or "\n" in container:
        raise RotationError
    image = subprocess.run(
        [DOCKER, "inspect", "--format", "{{.Config.Image}}", container],
        check=True, capture_output=True, text=True, env=HOST_ENV,
    ).stdout.strip()
    if not image or "\n" in image:
        raise RotationError
    return image


def _container(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            DOCKER, "run", "--rm", "--pull=never",
            "--network", "orbbec-agent-platform-internal", "--user", "0:0",
            "-v", f"{PRIVATE_ROOT}:/run/control-secrets:ro",
            "-e", "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url",
            _image(), *arguments,
        ],
        check=True, capture_output=True, text=True, env=HOST_ENV,
    )


def _inspect(key_id: object) -> tuple[str, str | None]:
    if not isinstance(key_id, str) or KEY_ID.fullmatch(key_id) is None:
        raise RotationError
    result = _container(
        ["python", "-c", INSPECT_SCRIPT, "--inspect-execution-worker-key", WORKER_ID, key_id]
    )
    value = json.loads(result.stdout)
    if (
        not isinstance(value, dict)
        or set(value) != {"key_id", "status", "public_key_sha256"}
        or value["key_id"] != key_id
        or value["status"] not in {"absent", "active", "revoked"}
        or (
            value["status"] == "absent" and value["public_key_sha256"] is not None
        )
        or (
            value["status"] != "absent"
            and (
                not isinstance(value["public_key_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", value["public_key_sha256"]) is None
            )
        )
    ):
        raise RotationError
    return value["status"], value["public_key_sha256"]


def _inspect_worker() -> str:
    result = _container(
        ["python", "-c", WORKER_INSPECT_SCRIPT, "--inspect-execution-worker", WORKER_ID]
    )
    value = json.loads(result.stdout)
    if (
        not isinstance(value, dict)
        or set(value) != {"worker_id", "status"}
        or value["worker_id"] != WORKER_ID
        or value["status"] not in {"absent", "active", "revoked"}
    ):
        raise RotationError
    return value["status"]


def _inventory() -> dict[str, tuple[str, str]]:
    result = _container(
        ["python", "-c", KEY_INVENTORY_SCRIPT, "--inspect-execution-worker-inventory", WORKER_ID]
    )
    value = json.loads(result.stdout)
    inventory: dict[str, tuple[str, str]] = {}
    if not isinstance(value, list):
        raise RotationError
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"key_id", "status", "public_key_sha256"}
            or not isinstance(item["key_id"], str)
            or KEY_ID.fullmatch(item["key_id"]) is None
            or item["key_id"] in inventory
            or item["status"] not in {"active", "revoked"}
            or not isinstance(item["public_key_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["public_key_sha256"]) is None
        ):
            raise RotationError
        inventory[item["key_id"]] = (item["status"], item["public_key_sha256"])
    return inventory


def _expect_unique_active_key(key_id: str, fingerprint: str) -> None:
    inventory = _inventory()
    if inventory.get(key_id) != ("active", fingerprint):
        raise RotationError
    if any(status != "revoked" for current, (status, _fingerprint) in inventory.items() if current != key_id):
        raise RotationError


def _maintenance(arguments: list[str]) -> None:
    result = _container(
        ["python", "-m", "app.execution_relay.register_worker", *arguments]
    )
    if result.stdout != "EXECUTION_WORKER_MAINTENANCE_OK\n" or result.stderr:
        raise RotationError


def _expect_status(key_id: object, status: str, fingerprint: object) -> None:
    actual_status, actual_fingerprint = _inspect(key_id)
    if actual_status != status or actual_fingerprint != fingerprint:
        raise RotationError


def _prepare(target: str) -> None:
    if STATE.exists() or STATE.is_symlink():
        raise RotationError
    from_id, current, current_digest, current_fingerprint = _document(KEYRING)
    incoming = INCOMING_ROOT / f"execution-worker-public-{target}.json"
    to_id, staged, staged_digest, staged_fingerprint = _document(incoming)
    if from_id == target or to_id != target:
        raise RotationError
    for path, key_id, digest, fingerprint in (
        (PREVIOUS, from_id, current_digest, current_fingerprint),
        (STAGED, target, staged_digest, staged_fingerprint),
    ):
        if path.exists() or path.is_symlink():
            _validate_document(path, key_id, digest, fingerprint)
            _unlink(path)
    _expect_status(from_id, "active", current_fingerprint)
    _expect_status(target, "absent", None)
    _atomic_write(PREVIOUS, PREVIOUS_PART, current)
    _atomic_write(STAGED, STAGED_PART, staged)
    state: dict[str, object] = {
        "schema_version": 1,
        "phase": "prepared",
        "from_key_id": from_id,
        "to_key_id": target,
        "previous_document_sha256": current_digest,
        "next_document_sha256": staged_digest,
        "previous_public_sha256": current_fingerprint,
        "next_public_sha256": staged_fingerprint,
    }
    _write_state(state, "prepared")


def _activate(target: str) -> None:
    value = _state()
    if value["to_key_id"] != target or value["phase"] not in {"prepared", "adding", "cloud_active"}:
        raise RotationError
    staged = _validate_document(
        STAGED, target, value["next_document_sha256"], value["next_public_sha256"]
    )
    if value["phase"] == "cloud_active":
        _validate_document(KEYRING, target, value["next_document_sha256"], value["next_public_sha256"])
        _expect_status(target, "active", value["next_public_sha256"])
        return
    if value["phase"] == "prepared":
        _write_state(value, "adding")
    status, fingerprint = _inspect(target)
    if status == "absent":
        _maintenance([
            "add-key", WORKER_ID,
            "/run/control-secrets/execution-worker-public-keyring.next.json",
            "RELAY_KEY_ROTATION_2026",
        ])
        status, fingerprint = _inspect(target)
    if status != "active" or fingerprint != value["next_public_sha256"]:
        raise RotationError
    _atomic_write(KEYRING, KEYRING_PART, staged)
    _write_state(value, "cloud_active")


def _mark_accepted(target: str) -> None:
    value = _state()
    if value["to_key_id"] != target or value["phase"] not in {"cloud_active", "accepted"}:
        raise RotationError
    _validate_document(KEYRING, target, value["next_document_sha256"], value["next_public_sha256"])
    _expect_status(target, "active", value["next_public_sha256"])
    _expect_status(value["from_key_id"], "active", value["previous_public_sha256"])
    if value["phase"] == "cloud_active":
        _write_state(value, "accepted")


def _commit(target: str) -> None:
    value = _state()
    if value["to_key_id"] != target or value["phase"] not in {"accepted", "committing", "old_revoked"}:
        raise RotationError
    _validate_document(KEYRING, target, value["next_document_sha256"], value["next_public_sha256"])
    _expect_status(target, "active", value["next_public_sha256"])
    if value["phase"] == "old_revoked":
        _expect_status(value["from_key_id"], "revoked", value["previous_public_sha256"])
        return
    if value["phase"] == "accepted":
        _write_state(value, "committing")
    status, fingerprint = _inspect(value["from_key_id"])
    if status == "active" and fingerprint == value["previous_public_sha256"]:
        _maintenance([
            "revoke-key", WORKER_ID, str(value["from_key_id"]),
            "RELAY_KEY_ROTATION_2026",
        ])
        status, fingerprint = _inspect(value["from_key_id"])
    if status != "revoked" or fingerprint != value["previous_public_sha256"]:
        raise RotationError
    _write_state(value, "old_revoked")


def _cleanup(value: dict[str, object]) -> None:
    for path, key_id, digest, fingerprint in (
        (PREVIOUS, value["from_key_id"], value["previous_document_sha256"], value["previous_public_sha256"]),
        (STAGED, value["to_key_id"], value["next_document_sha256"], value["next_public_sha256"]),
    ):
        if path.exists() or path.is_symlink():
            _validate_document(path, key_id, digest, fingerprint)
    _unlink(PREVIOUS)
    _unlink(STAGED)
    _unlink(STATE)


def _rollback(target: str) -> None:
    if not STATE.exists() and not STATE.is_symlink():
        if any(path.exists() or path.is_symlink() for path in (PREVIOUS, STAGED)):
            raise RotationError
        current_id, _raw, _digest, current_fingerprint = _document(KEYRING)
        if current_id == target:
            raise RotationError
        _expect_unique_active_key(current_id, current_fingerprint)
        status, fingerprint = _inspect(target)
        if status == "absent" and fingerprint is None:
            return
        if status == "revoked" and fingerprint is not None:
            return
        raise RotationError
    value = _state()
    allowed = {"prepared", "adding", "cloud_active", "accepted", "restoring", "revoking", "revoked"}
    if value["to_key_id"] != target or value["phase"] not in allowed:
        raise RotationError
    previous: bytes | None = None
    if value["phase"] in {"prepared", "adding", "cloud_active", "accepted", "restoring"}:
        previous = _validate_document(
            PREVIOUS, value["from_key_id"], value["previous_document_sha256"], value["previous_public_sha256"]
        )
    if value["phase"] in {"prepared", "adding", "cloud_active", "accepted"}:
        _write_state(value, "restoring")
    if value["phase"] == "restoring":
        if previous is None:
            raise RotationError
        _atomic_write(KEYRING, KEYRING_PART, previous)
        status, fingerprint = _inspect(target)
        if status == "absent":
            _write_state(value, "revoked")
        elif status == "active" and fingerprint == value["next_public_sha256"]:
            _write_state(value, "revoking")
        elif status == "revoked" and fingerprint == value["next_public_sha256"]:
            _write_state(value, "revoked")
        else:
            raise RotationError
    if value["phase"] == "revoking":
        status, fingerprint = _inspect(target)
        if status == "active" and fingerprint == value["next_public_sha256"]:
            _maintenance([
                "revoke-key", WORKER_ID, target,
                "RELAY_KEY_ROTATION_ROLLBACK_2026",
            ])
            status, fingerprint = _inspect(target)
        if status != "revoked" or fingerprint != value["next_public_sha256"]:
            raise RotationError
        _write_state(value, "revoked")
    if value["phase"] == "revoked":
        _validate_document(KEYRING, value["from_key_id"], value["previous_document_sha256"], value["previous_public_sha256"])
        _expect_status(value["from_key_id"], "active", value["previous_public_sha256"])
        status, fingerprint = _inspect(target)
        if not (
            (status == "absent" and fingerprint is None)
            or (status == "revoked" and fingerprint == value["next_public_sha256"])
        ):
            raise RotationError
        _cleanup(value)


def _finalize(target: str) -> None:
    if not STATE.exists() and not STATE.is_symlink():
        if any(path.exists() or path.is_symlink() for path in (PREVIOUS, STAGED)):
            raise RotationError
        _key_id, _raw, _digest, fingerprint = _document(KEYRING)
        if _key_id != target:
            raise RotationError
        _expect_unique_active_key(target, fingerprint)
        return
    value = _state()
    if value["to_key_id"] != target or value["phase"] not in {"old_revoked", "finalizing"}:
        raise RotationError
    _validate_document(KEYRING, target, value["next_document_sha256"], value["next_public_sha256"])
    _expect_status(target, "active", value["next_public_sha256"])
    _expect_status(value["from_key_id"], "revoked", value["previous_public_sha256"])
    if value["phase"] == "old_revoked":
        _write_state(value, "finalizing")
    _cleanup(value)


def _recover(target: str) -> None:
    value = _state()
    if value["to_key_id"] != target:
        raise RotationError
    if value["phase"] == "adding":
        _activate(target)
    elif value["phase"] == "committing":
        _commit(target)
    elif value["phase"] in {"restoring", "revoking", "revoked"}:
        _rollback(target)
    elif value["phase"] == "finalizing":
        _finalize(target)
    else:
        raise RotationError


def _revoke_worker() -> None:
    if STATE.exists() or STATE.is_symlink():
        raise RotationError
    status = _inspect_worker()
    if status == "revoked":
        return
    if status != "active":
        raise RotationError
    try:
        _maintenance(["revoke-worker", WORKER_ID, "RELAY_WORKER_REVOKE_2026"])
    except Exception:
        if _inspect_worker() != "revoked":
            raise
        return
    if _inspect_worker() != "revoked":
        raise RotationError


def _acquire_lock() -> int:
    descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise RotationError
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    lock = -1
    try:
        worker_revoke = values == ["revoke-worker"]
        rotation_action = (
            len(values) == 2
            and values[0] in {
                "prepare", "activate", "mark-accepted", "commit",
                "rollback", "finalize", "recover",
            }
            and KEY_ID.fullmatch(values[1]) is not None
        )
        if (
            os.getuid() != REQUIRED_UID
            or not (worker_revoke or rotation_action)
        ):
            raise RotationError
        for directory in (PRIVATE_ROOT, INCOMING_ROOT):
            _secure_directory(directory)
        _release_compose()
        for path in (ENVIRONMENT, MAINTENANCE_DSN):
            _secure_file(path)
        lock = _acquire_lock()
        if any(
            path.exists() or path.is_symlink()
            for path in (DEPLOY_STATE, DEPLOY_STATE_PART, DEPLOY_BACKUP)
        ):
            raise RotationError
        _clean_parts()
        if worker_revoke:
            _revoke_worker()
            print("EXECUTION_WORKER_CLOUD_ROTATION_OK action=revoke-worker")
        else:
            action, target = values
            {
                "prepare": _prepare,
                "activate": _activate,
                "mark-accepted": _mark_accepted,
                "commit": _commit,
                "rollback": _rollback,
                "finalize": _finalize,
                "recover": _recover,
            }[action](target)
            print(f"EXECUTION_WORKER_CLOUD_ROTATION_OK action={action} key_id={target}")
        return 0
    except Exception:
        print("EXECUTION_WORKER_CLOUD_ROTATION_FAILED", file=sys.stderr)
        return 1
    finally:
        if lock >= 0:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
