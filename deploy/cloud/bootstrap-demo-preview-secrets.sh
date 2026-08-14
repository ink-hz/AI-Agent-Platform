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
[[ -n "$platform_image" && -d "$private_path" ]] || fail
[[ "$(/usr/bin/stat -c '%u:%a:%F' "$private_path" 2>/dev/null)" == "0:700:directory" ]] || fail
/usr/bin/docker image inspect "$platform_image" >/dev/null 2>&1 || fail

/usr/bin/docker volume inspect "$volume_name" >/dev/null 2>&1 || \
  /usr/bin/docker volume create "$volume_name" >/dev/null

if ! /usr/bin/docker run --rm -i --network none --user 0:0 \
  --read-only --security-opt no-new-privileges \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
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
from uuid import uuid4

from psycopg.conninfo import conninfo_to_dict

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.demo_bootstrap import read_demo_userids


SOURCE = Path(sys.argv[1])
TARGET = Path(sys.argv[2])
EXPECTED = (
    "dingtalk-app-key",
    "dingtalk-agent-id",
    "dingtalk-corp-id",
    "dingtalk-app-secret",
    "preview-control-database-url",
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
    "preview-identity-hmac-keyring",
    "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring",
)
OFFLINE_NAMES = (
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "demo-userids",
)
DSNS = {
    "preview-control-database-url": "platform_control_app_preview",
    "preview-control-directory-worker-database-url":
        "platform_directory_worker_preview",
    "preview-control-migrator-database-url":
        "platform_control_migrator_preview",
}


def reject() -> None:
    raise RuntimeError("invalid demo preview secret input")


def checked_file(name: str) -> Path:
    path = SOURCE / name
    metadata = path.lstat()
    if (
        path.is_symlink()
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
        or metadata.st_size > 65_536
    ):
        reject()
    return path


def one_line(path: Path, *, maximum: int = 4096) -> str:
    payload = path.read_bytes()
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


def validate() -> dict[str, Path]:
    source_metadata = SOURCE.lstat()
    if (
        SOURCE.is_symlink()
        or not stat.S_ISDIR(source_metadata.st_mode)
        or source_metadata.st_uid != 0
        or stat.S_IMODE(source_metadata.st_mode) != 0o700
    ):
        reject()
    files = {name: checked_file(name) for name in EXPECTED}
    for name in (
        "dingtalk-app-key",
        "dingtalk-agent-id",
        "dingtalk-corp-id",
        "dingtalk-app-secret",
    ):
        one_line(files[name])
    read_demo_userids(files["demo-userids"])
    for name, expected_role in DSNS.items():
        parsed = conninfo_to_dict(one_line(files[name], maximum=16_384))
        if (
            parsed.get("user") != expected_role
            or parsed.get("dbname") != "agent_platform_control_preview"
        ):
            reject()
    encryption = IdentityKeyring.from_file(
        files["preview-identity-encryption-keyring"],
        expected_purpose="provider-encryption",
        expected_key_length=32,
    )
    lookup = IdentityKeyring.from_file(
        files["preview-identity-hmac-keyring"],
        expected_purpose="provider-lookup-hmac",
        expected_key_length=32,
    )
    rate = IdentityKeyring.from_file(
        files["preview-rate-limit-hmac-keyring"],
        expected_purpose="rate-limit-hmac",
        expected_key_length=32,
    )
    if encryption.overlaps(lookup) or encryption.overlaps(rate) or lookup.overlaps(rate):
        reject()
    return files


try:
    files = validate()
    staging = TARGET / (".stage-" + uuid4().hex)
    staging.mkdir(mode=0o700)
    try:
        runtime_staging = staging / "runtime"
        offline_staging = staging / "offline"
        runtime_staging.mkdir(mode=0o700)
        offline_staging.mkdir(mode=0o700)
        for name, source in files.items():
            destination = (
                runtime_staging if name in RUNTIME_NAMES else offline_staging
            ) / name
            with source.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=64 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            os.chown(destination, 10001, 10001)
            os.chmod(destination, 0o400)
        runtime = TARGET / "runtime"
        offline = TARGET / "offline"
        for directory in (runtime, offline):
            if directory.exists() and (
                directory.is_symlink() or not directory.is_dir()
            ):
                reject()
            directory.mkdir(mode=0o700, exist_ok=True)
        os.chown(runtime, 0, 10001)
        os.chmod(runtime, 0o750)
        os.chown(offline, 0, 0)
        os.chmod(offline, 0o700)
        for name in RUNTIME_NAMES:
            os.replace(runtime_staging / name, runtime / name)
        for name in OFFLINE_NAMES:
            os.replace(offline_staging / name, offline / name)
        # Remove entries written by the pre-split draft, if any. A failed
        # upgrade therefore cannot leave privileged credentials API-readable.
        for name in EXPECTED:
            legacy = TARGET / name
            if legacy.exists() or legacy.is_symlink():
                if legacy.is_symlink() or not legacy.is_file():
                    reject()
                legacy.unlink()
        for directory in (runtime, offline):
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

/usr/bin/printf '%s\n' 'DEMO_PREVIEW_SECRETS_READY files=11'
