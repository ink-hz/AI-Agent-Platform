#!/bin/bash
set -euo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' 'DEMO_PREVIEW_SECRET_BOOTSTRAP_FAILED' >&2
  exit 1
}

[[ "${EUID:-$(/usr/bin/id -u)}" -eq 0 && $# -eq 0 ]] || fail
private_path=/opt/orbbec-agent-platform/private/demo-preview
volume_name=orbbec-agent-platform-demo-preview-secrets
platform_image="${PLATFORM_IMAGE:-}"
[[ -n "$platform_image" && ! -L "$private_path" && -d "$private_path" ]] || fail
[[ ! -L "$private_path" ]] || fail
[[ "$(/usr/bin/stat -c '%u:%a:%F' "$private_path" 2>/dev/null)" == "0:700:directory" ]] || fail
/usr/bin/docker image inspect "$platform_image" >/dev/null 2>&1 || fail

/usr/bin/docker volume inspect "$volume_name" >/dev/null 2>&1 || \
  /usr/bin/docker volume create "$volume_name" >/dev/null

if ! /usr/bin/docker run --rm -i --network none --user 0:0 \
  --read-only --security-opt no-new-privileges \
  --cap-drop ALL --cap-add CHOWN \
  --mount "type=bind,src=$private_path,dst=/source,readonly" \
  --mount "type=volume,src=$volume_name,dst=/target" \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  "$platform_image" python - /source /target <<'PY'
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from uuid import uuid4

from psycopg.conninfo import conninfo_to_dict

from app.control_plane.crypto import IdentityKeyring


SOURCE = Path(sys.argv[1])
TARGET = Path(sys.argv[2])
EXPECTED = (
    "dingtalk-app-key",
    "dingtalk-agent-id",
    "dingtalk-corp-id",
    "dingtalk-app-secret",
    "preview-control-database-url",
    "preview-control-audit-database-url",
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "preview-identity-hmac-keyring",
    "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring",
    "demo-userids",
)
RUNTIME_NAMES = (
    "dingtalk-app-key",
    "dingtalk-agent-id",
    "dingtalk-corp-id",
    "dingtalk-app-secret",
    "preview-control-database-url",
    "preview-control-audit-database-url",
    "preview-identity-hmac-keyring",
    "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring",
)
OFFLINE_NAMES = (
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "demo-userids",
)
RUNNER_NAMES = (
    "dingtalk-app-key",
    "dingtalk-corp-id",
    "dingtalk-app-secret",
    "preview-identity-encryption-keyring",
    "preview-identity-hmac-keyring",
    "preview-control-migrator-database-url",
    "preview-control-directory-worker-database-url",
    "demo-userids",
)
DSNS = {
    "preview-control-database-url": "platform_control_app_preview",
    "preview-control-audit-database-url": "platform_audit_append_preview",
    "preview-control-directory-worker-database-url":
        "platform_directory_worker_preview",
    "preview-control-migrator-database-url":
        "platform_control_migrator_preview",
}


def reject() -> None:
    raise RuntimeError("invalid demo preview secret input")


def checked_file(name: str, source_fd: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=source_fd,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size <= 0
            or opened.st_size > 65_536
        ):
            reject()
        chunks: list[bytes] = []
        remaining = 65_537
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size or len(payload) > 65_536:
            reject()
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def one_line(payload: bytes, *, maximum: int = 4096) -> str:
    if len(payload) > maximum or b"\0" in payload:
        reject()
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        reject()
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
        reject()
    return lines[0]


def read_source_payloads() -> dict[str, bytes]:
    source_fd = -1
    try:
        source_fd = os.open(
            SOURCE,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        source_metadata = os.fstat(source_fd)
        if (
            not stat.S_ISDIR(source_metadata.st_mode)
            or source_metadata.st_uid != 0
            or stat.S_IMODE(source_metadata.st_mode) != 0o700
        ):
            reject()
        return {name: checked_file(name, source_fd) for name in EXPECTED}
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def validate(payloads: dict[str, bytes]) -> None:
    for name in (
        "dingtalk-app-key",
        "dingtalk-agent-id",
        "dingtalk-corp-id",
        "dingtalk-app-secret",
    ):
        one_line(payloads[name])
    try:
        userids = payloads["demo-userids"].decode("utf-8").splitlines()
    except UnicodeError:
        reject()
    if (
        not 1 <= len(userids) <= 3
        or len(set(userids)) != len(userids)
        or any(
            not value
            or value != value.strip()
            or len(value.encode("utf-8")) > 512
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
            for value in userids
        )
    ):
        reject()
    for name, expected_role in DSNS.items():
        parsed = conninfo_to_dict(one_line(payloads[name], maximum=16_384))
        if (
            parsed.get("user") != expected_role
            or parsed.get("dbname") != "agent_platform_control_preview"
        ):
            reject()
    with tempfile.TemporaryDirectory(prefix="keyring-", dir="/tmp") as temporary:
        keyring_paths = {}
        for name in (
            "preview-identity-encryption-keyring",
            "preview-identity-hmac-keyring",
            "preview-rate-limit-hmac-keyring",
        ):
            keyring_path = Path(temporary) / name
            with keyring_path.open("xb") as writer:
                writer.write(payloads[name])
            keyring_path.chmod(0o600)
            keyring_paths[name] = keyring_path
        encryption = IdentityKeyring.from_file(
            keyring_paths["preview-identity-encryption-keyring"],
            expected_purpose="provider-encryption",
            expected_key_length=32,
        )
        lookup = IdentityKeyring.from_file(
            keyring_paths["preview-identity-hmac-keyring"],
            expected_purpose="provider-lookup-hmac",
            expected_key_length=32,
        )
        rate = IdentityKeyring.from_file(
            keyring_paths["preview-rate-limit-hmac-keyring"],
            expected_purpose="rate-limit-hmac",
            expected_key_length=32,
        )
    if encryption.overlaps(lookup) or encryption.overlaps(rate) or lookup.overlaps(rate):
        reject()


def write_secret(
    destination: Path, payload: bytes, uid: int, gid: int
) -> None:
    with destination.open("xb") as writer:
        writer.write(payload)
        writer.flush()
        os.fsync(writer.fileno())
    os.chmod(destination, 0o400)
    os.chown(destination, uid, gid)


def remove_stale_entries(directory: Path, names: tuple[str, ...]) -> None:
    expected = set(names)
    for entry in directory.iterdir():
        if entry.name in expected:
            continue
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        else:
            reject()


def publish(
    staging_directory: Path,
    target_directory: Path,
    names: tuple[str, ...],
) -> None:
    for name in names:
        os.replace(staging_directory / name, target_directory / name)
    remove_stale_entries(target_directory, names)


try:
    payloads = read_source_payloads()
    validate(payloads)
    for entry in TARGET.iterdir():
        if not entry.name.startswith(".stage-"):
            continue
        if entry.is_symlink() or not entry.is_dir():
            reject()
        shutil.rmtree(entry)
    staging = TARGET / (".stage-" + uuid4().hex)
    staging.mkdir(mode=0o700)
    try:
        runtime_staging = staging / "runtime"
        offline_staging = staging / "offline"
        runner_staging = staging / "runner"
        runtime_staging.mkdir(mode=0o700)
        offline_staging.mkdir(mode=0o700)
        runner_staging.mkdir(mode=0o700)
        for name in RUNTIME_NAMES:
            write_secret(runtime_staging / name, payloads[name], 10001, 10001)
        for name in OFFLINE_NAMES:
            write_secret(offline_staging / name, payloads[name], 0, 0)
        for name in RUNNER_NAMES:
            write_secret(runner_staging / name, payloads[name], 0, 0)
        runtime = TARGET / "runtime"
        offline = TARGET / "offline"
        runner = TARGET / "runner"
        for directory in (runtime, offline, runner):
            if directory.exists() and (
                directory.is_symlink() or not directory.is_dir()
            ):
                reject()
            directory.mkdir(mode=0o700, exist_ok=True)
        os.chown(runtime, 0, 10001)
        os.chmod(runtime, 0o750)
        os.chown(offline, 0, 0)
        os.chmod(offline, 0o700)
        os.chown(runner, 0, 0)
        os.chmod(runner, 0o700)
        publish(runtime_staging, runtime, RUNTIME_NAMES)
        publish(offline_staging, offline, OFFLINE_NAMES)
        publish(runner_staging, runner, RUNNER_NAMES)
        # Remove entries written by the pre-split draft, if any. A failed
        # upgrade therefore cannot leave privileged credentials API-readable.
        for name in EXPECTED:
            legacy = TARGET / name
            if legacy.exists() or legacy.is_symlink():
                if legacy.is_symlink() or not legacy.is_file():
                    reject()
                legacy.unlink()
        for directory in (runtime, offline, runner):
            descriptor = os.open(
                directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        directory = os.open(TARGET, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
except BaseException:
    raise SystemExit(1) from None
PY
then
  fail
fi

/usr/bin/printf '%s\n' 'DEMO_PREVIEW_SECRETS_READY files=12'
