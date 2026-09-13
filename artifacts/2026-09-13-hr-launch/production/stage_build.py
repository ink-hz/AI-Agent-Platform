#!/usr/bin/env python3
"""Build the fixed HR cloud release without changing running services.

`start` is inert unless `--execute` is present.  A started build runs under a
remote, detached process.  Use `poll --run-dir ...` to retrieve its terminal
exit status and complete receipt.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Iterable, NamedTuple
import uuid


WORKTREE = Path(
    "/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/"
    "hr-cloud-loop-e-release"
)
ARTIFACT_ROOT = WORKTREE / "artifacts/2026-09-13-hr-launch/production"
RUNS_ROOT = ARTIFACT_ROOT / "stage-build-runs"
RELEASE_SHA = "ac8c27588b2962ba08d00c11545eada370613908"
EXPECTED_SOURCE_TREE = "0023f22ce779bd0ff11d455fc229a71da67a11d9"
DEPLOY_LOCK_SHA256 = (
    "45c2d3fce8a5ef2e3dd6306eaa15b7cdbfc85072bed164033684f84b425a4eb4"
)
REMOTE = "root@47.106.112.69"
SSH_KEY = Path("/Users/neo/.ssh/orbbec_aliyun_ed25519")
SSH_OPTIONS = (
    "-i",
    str(SSH_KEY),
    "-o",
    "BatchMode=yes",
    "-o",
    "IdentitiesOnly=yes",
    "-o",
    "ConnectTimeout=8",
    "-o",
    "StrictHostKeyChecking=yes",
)
PRIVATE_INPUT_PARENT = Path(
    "/opt/orbbec-agent-platform/private/stage-build-inputs"
)
STAGING_PARENT = Path("/data/staging/orbbec-agent-platform")
METADATA_PARENT = Path("/data/orbbec-agent-platform/release-metadata")
RELEASE_PARENT = Path("/opt/orbbec-agent-platform/releases")
IMAGE_REPOSITORY = "orbbec-agent-platform"
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ArchiveInfo(NamedTuple):
    archive_path: Path
    archive_sha256: str
    manifest_sha256: str
    source_tree: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*arguments: str, stdout: int | None = subprocess.PIPE) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=WORKTREE,
        check=True,
        stdout=stdout,
        stderr=subprocess.PIPE,
    ).stdout


def validate_archive_members(members: Iterable[tarfile.TarInfo]) -> None:
    seen: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (
            not member.name
            or path.is_absolute()
            or not path.parts
            or path.parts[0] != "source"
            or any(part in {"", ".", ".."} for part in path.parts)
            or member.name in seen
            or not (member.isdir() or member.isreg())
        ):
            raise ValueError(f"unsafe archive member: {member.name!r}")
        seen.add(member.name)


def build_source_package(output_directory: Path) -> ArchiveInfo:
    """Create a private archive from the fixed Git object only."""
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = _git("rev-parse", f"{RELEASE_SHA}^{{commit}}").decode().strip()
    tree = _git("rev-parse", f"{RELEASE_SHA}^{{tree}}").decode().strip()
    if resolved != RELEASE_SHA or tree != EXPECTED_SOURCE_TREE:
        raise RuntimeError("fixed source object identity mismatch")
    link_rows = _git("ls-tree", "-r", RELEASE_SHA).decode().splitlines()
    if any(row.startswith("120000 ") for row in link_rows):
        raise RuntimeError("fixed source unexpectedly contains a symbolic link")

    raw_tar = output_directory / "source.tar"
    archive_path = output_directory / f"platform-{RELEASE_SHA}.tar.gz"
    with raw_tar.open("wb") as handle:
        subprocess.run(
            [
                "git",
                "archive",
                "--format=tar",
                "--prefix=source/",
                RELEASE_SHA,
            ],
            cwd=WORKTREE,
            check=True,
            stdout=handle,
            stderr=subprocess.PIPE,
        )

    with tarfile.open(raw_tar, "r:") as archive:
        members = archive.getmembers()
        validate_archive_members(members)
        manifest_lines: list[str] = []
        for member in sorted(members, key=lambda item: item.name):
            if not member.isreg():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError(f"could not read {member.name}")
            relative = PurePosixPath(member.name).relative_to("source").as_posix()
            manifest_lines.append(
                f"{hashlib.sha256(handle.read()).hexdigest()}  {relative}"
            )
    manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")
    with tarfile.open(raw_tar, "a:") as archive:
        member = tarfile.TarInfo("source/MANIFEST.sha256")
        member.size = len(manifest)
        member.mode = 0o600
        member.mtime = 0
        member.uid = 0
        member.gid = 0
        member.uname = "root"
        member.gname = "root"
        archive.addfile(member, io.BytesIO(manifest))

    with raw_tar.open("rb") as source, archive_path.open("wb") as raw_output:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_output, mtime=0
        ) as compressed:
            shutil.copyfileobj(source, compressed)
    raw_tar.unlink()
    archive_path.chmod(0o600)
    return ArchiveInfo(
        archive_path=archive_path,
        archive_sha256=sha256_file(archive_path),
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        source_tree=tree,
    )


def _checked_identity(
    deployment_id: str,
    action_token: str,
    archive_sha256: str,
    manifest_sha256: str,
    source_tree: str,
) -> None:
    if (
        HEX32.fullmatch(deployment_id) is None
        or HEX64.fullmatch(archive_sha256) is None
        or HEX64.fullmatch(manifest_sha256) is None
        or HEX40.fullmatch(source_tree) is None
        or str(uuid.UUID(action_token)) != action_token
    ):
        raise ValueError("invalid build identity")


def remote_paths(deployment_id: str) -> dict[str, str]:
    if HEX32.fullmatch(deployment_id) is None:
        raise ValueError("invalid deployment id")
    input_root = PRIVATE_INPUT_PARENT / f"{RELEASE_SHA}-{deployment_id}"
    metadata_root = (
        METADATA_PARENT / RELEASE_SHA / f"stage-build-{deployment_id}"
    )
    return {
        "input_root": str(input_root),
        "source_archive": str(input_root / "source.tar.gz"),
        "deploy_lock": str(input_root / "deploy-input-lock.py"),
        "remote_job": str(input_root / "remote-job.sh"),
        "staging_root": str(STAGING_PARENT / deployment_id),
        "metadata_root": str(metadata_root),
        "release_root": str(RELEASE_PARENT / RELEASE_SHA),
    }


def render_remote_job(
    *,
    deployment_id: str,
    action_token: str,
    archive_sha256: str,
    manifest_sha256: str,
    source_tree: str,
) -> str:
    _checked_identity(
        deployment_id,
        action_token,
        archive_sha256,
        manifest_sha256,
        source_tree,
    )
    paths = remote_paths(deployment_id)
    # All substituted fields above are fixed constants or strict hexadecimal/UUID.
    return f"""#!/bin/bash
set -euo pipefail
umask 077
release_sha={RELEASE_SHA}
deployment_id={deployment_id}
action_token={action_token}
expected_archive_sha={archive_sha256}
expected_manifest_sha={manifest_sha256}
expected_source_tree={source_tree}
expected_lock_sha={DEPLOY_LOCK_SHA256}
input_root={paths['input_root']}
archive_path={paths['source_archive']}
deploy_lock={paths['deploy_lock']}
stage_root={paths['staging_root']}
metadata_root={paths['metadata_root']}
release_root={paths['release_root']}
action_lock=/opt/orbbec-agent-platform/private/agent-brain-action.lock
deploy_acquired=0
action_created=0
action_acquired=0
stage_created=0
started_at="$(/usr/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"

write_exit() {{
  local selected="$1"
  /usr/bin/printf '%s\n' "$selected" > "$metadata_root/exit_code.part"
  /usr/bin/chmod 600 "$metadata_root/exit_code.part"
  /usr/bin/mv -f "$metadata_root/exit_code.part" "$metadata_root/exit_code"
}}

release_action_lock() {{
  [[ -d "$action_lock" && ! -L "$action_lock" ]] || return 1
  [[ -f "$action_lock/owner" && ! -L "$action_lock/owner" ]] || return 1
  [[ "$(/usr/bin/stat -c '%a %U' "$action_lock/owner")" == '600 root' ]] || return 1
  [[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]] || return 1
  local tombstone="$action_lock.releasing.$action_token"
  [[ ! -e "$tombstone" && ! -L "$tombstone" ]] || return 1
  /usr/bin/mv "$action_lock" "$tombstone" || return 1
  /usr/bin/rm -f -- "$tombstone/owner" || return 1
  /usr/bin/rmdir "$tombstone" || return 1
}}

cleanup() {{
  local selected=$?
  trap - EXIT
  set +e
  if [[ "$deploy_acquired" == 1 ]]; then
    # deploy-input-lock.py validate/release bind cleanup to this exact release/deployment.
    /usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"
    if [[ $? != 0 ]]; then
      selected=1
    elif ! /usr/bin/python3 "$deploy_lock" release "$release_sha" "$deployment_id"; then
      selected=1
    fi
  fi
  if [[ "$action_acquired" == 1 ]]; then
    release_action_lock || selected=1
  elif [[ "$action_created" == 1 && -d "$action_lock" && ! -L "$action_lock" ]]; then
    if [[ -f "$action_lock/owner" ]] && [[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]]; then
      release_action_lock || selected=1
    else
      /usr/bin/rmdir "$action_lock" 2>/dev/null || selected=1
    fi
  fi
  if [[ "$stage_created" == 1 && -d "$stage_root" && ! -L "$stage_root" ]]; then
    /usr/bin/find "$stage_root" -depth -mindepth 1 -delete || selected=1
    /usr/bin/rmdir "$stage_root" || selected=1
  fi
  if [[ -d "$input_root" && ! -L "$input_root" ]]; then
    /usr/bin/rm -f -- "$archive_path" "$deploy_lock" "$input_root/remote-job.sh"
    /usr/bin/rmdir "$input_root" || selected=1
  fi
  write_exit "$selected"
  exit "$selected"
}}
trap cleanup EXIT

[[ "$(id -u)" == 0 ]]
[[ "$release_sha" =~ ^[0-9a-f]{{40}}$ && "$deployment_id" =~ ^[0-9a-f]{{32}}$ ]]
[[ "$action_token" =~ ^[0-9a-f]{{8}}-[0-9a-f]{{4}}-4[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$ ]]
[[ -d /opt/orbbec-agent-platform/private && ! -L /opt/orbbec-agent-platform/private ]]
[[ "$(/usr/bin/stat -c '%a %U' /opt/orbbec-agent-platform/private)" == '700 root' ]]
[[ -d "$input_root" && ! -L "$input_root" ]]
[[ "$(/usr/bin/stat -c '%a %U' "$input_root")" == '700 root' ]]
[[ -f "$archive_path" && ! -L "$archive_path" ]]
[[ -f "$deploy_lock" && ! -L "$deploy_lock" ]]
[[ "$(/usr/bin/stat -c '%a %U' "$archive_path")" == '600 root' ]]
[[ "$(/usr/bin/stat -c '%a %U' "$deploy_lock")" == '700 root' ]]
[[ "$(/usr/bin/sha256sum "$deploy_lock" | /usr/bin/awk '{{print $1}}')" == "$expected_lock_sha" ]]
[[ "$(/usr/bin/sha256sum "$archive_path" | /usr/bin/awk '{{print $1}}')" == "$expected_archive_sha" ]]
[[ ! -e "$release_root" && ! -L "$release_root" ]]
[[ -z "$(/usr/bin/docker image ls -q {IMAGE_REPOSITORY}:$release_sha)" ]]

read -r root_size_before root_used_before root_available_before root_percent_before < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent / | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
read -r data_size_before data_used_before data_available_before data_percent_before < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent /data | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
[[ "$root_available_before" =~ ^[0-9]+$ && "$root_available_before" -ge 26843545600 ]]
[[ "$root_percent_before" =~ ^[0-9]+$ && "$root_percent_before" -le 75 ]]
[[ "$data_available_before" =~ ^[0-9]+$ && "$data_available_before" -ge 21474836480 ]]
archive_bytes="$(/usr/bin/stat -c '%s' "$archive_path")"
projected_root_bytes=$((archive_bytes * 8 + 5368709120))
[[ "$root_available_before" -ge $((projected_root_bytes + 21474836480)) ]]
/usr/bin/printf 'DISK_BEFORE root_used=%s root_available=%s root_percent=%s data_used=%s data_available=%s data_percent=%s projected_root_bytes=%s\n' \
  "$root_used_before" "$root_available_before" "$root_percent_before" \
  "$data_used_before" "$data_available_before" "$data_percent_before" \
  "$projected_root_bytes"

[[ ! -e "$action_lock" && ! -L "$action_lock" ]]
/usr/bin/mkdir -m 700 "$action_lock"
action_created=1
/usr/bin/printf '%s\n' "$action_token" > "$action_lock/owner"
/usr/bin/chmod 600 "$action_lock/owner"
[[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]]
action_acquired=1
/usr/bin/python3 "$deploy_lock" acquire "$release_sha" "$deployment_id"
deploy_acquired=1
/usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"

[[ ! -e "$stage_root" && ! -L "$stage_root" ]]
/usr/bin/install -d -m 700 "$stage_root"
stage_created=1
/usr/bin/python3 - "$archive_path" "$stage_root" <<'PY'
from pathlib import Path, PurePosixPath
import os
import shutil
import stat
import sys
import tarfile

archive_path, destination = map(Path, sys.argv[1:])
seen = set()
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    for member in members:
        path = PurePosixPath(member.name)
        if (
            not member.name or path.is_absolute() or not path.parts
            or path.parts[0] != "source"
            or any(part in {{"", ".", ".."}} for part in path.parts)
            or member.name in seen
            or not (member.isdir() or member.isreg())
        ):
            raise SystemExit("unsafe source archive")
        seen.add(member.name)
    for member in members:
        target = destination.joinpath(*PurePosixPath(member.name).parts)
        if member.isdir():
            target.mkdir(mode=member.mode & 0o777, parents=True, exist_ok=False)
            os.chmod(target, member.mode & 0o777)
            continue
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit("unreadable source archive")
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            member.mode & 0o777,
        )
        with os.fdopen(descriptor, "wb") as output:
            shutil.copyfileobj(source, output)
        os.chmod(target, member.mode & 0o777)
PY
source_root="$stage_root/source"
[[ -d "$source_root" && ! -L "$source_root" ]]
[[ -f "$source_root/MANIFEST.sha256" && ! -L "$source_root/MANIFEST.sha256" ]]
[[ "$(/usr/bin/sha256sum "$source_root/MANIFEST.sha256" | /usr/bin/awk '{{print $1}}')" == "$expected_manifest_sha" ]]
(cd "$source_root" && /usr/bin/sha256sum --check MANIFEST.sha256 >/dev/null)
/usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"

/usr/bin/docker build --pull --build-arg RELEASE_SHA={RELEASE_SHA} \
  -t {IMAGE_REPOSITORY}:{RELEASE_SHA} \
  -f "$source_root/deploy/cloud/Dockerfile" "$source_root"
image_id="$(/usr/bin/docker image inspect --format '{{{{.Id}}}}' {IMAGE_REPOSITORY}:$release_sha)"
[[ "$image_id" =~ ^sha256:[0-9a-f]{{64}}$ ]]
/usr/bin/docker image inspect --format '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' "$image_id" \
  | /usr/bin/grep -Fxq "PLATFORM_RELEASE_SHA=$release_sha"
/usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"
/usr/bin/mv "$source_root" "$release_root"

read -r root_size_after root_used_after root_available_after root_percent_after < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent / | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
read -r data_size_after data_used_after data_available_after data_percent_after < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent /data | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
[[ "$root_available_after" =~ ^[0-9]+$ && "$root_available_after" -ge 21474836480 ]]
[[ "$root_percent_after" =~ ^[0-9]+$ && "$root_percent_after" -le 75 ]]
release_manifest_sha="$(/usr/bin/sha256sum "$release_root/MANIFEST.sha256" | /usr/bin/awk '{{print $1}}')"
[[ "$release_manifest_sha" == "$expected_manifest_sha" ]]
finished_at="$(/usr/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
/usr/bin/python3 - "$metadata_root/result.json.part" \
  "$release_sha" "$deployment_id" "$expected_source_tree" \
  "$expected_archive_sha" "$expected_manifest_sha" "$image_id" \
  "$root_used_before" "$root_available_before" "$root_percent_before" \
  "$data_used_before" "$data_available_before" "$data_percent_before" \
  "$root_used_after" "$root_available_after" "$root_percent_after" \
  "$data_used_after" "$data_available_after" "$data_percent_after" \
  "$started_at" "$finished_at" <<'PY'
import json
from pathlib import Path
import sys

(
    output, release, deployment, tree, archive_sha, manifest_sha, image_id,
    root_used_before, root_available_before, root_percent_before,
    data_used_before, data_available_before, data_percent_before,
    root_used_after, root_available_after, root_percent_after,
    data_used_after, data_available_after, data_percent_after,
    started_at, finished_at,
) = sys.argv[1:]
value = {{
    "archive_sha256": archive_sha,
    "deployment_id": deployment,
    "disk_after": {{
        "data_available_bytes": int(data_available_after),
        "data_used_bytes": int(data_used_after),
        "data_used_percent": int(data_percent_after),
        "root_available_bytes": int(root_available_after),
        "root_used_bytes": int(root_used_after),
        "root_used_percent": int(root_percent_after),
    }},
    "disk_before": {{
        "data_available_bytes": int(data_available_before),
        "data_used_bytes": int(data_used_before),
        "data_used_percent": int(data_percent_before),
        "root_available_bytes": int(root_available_before),
        "root_used_bytes": int(root_used_before),
        "root_used_percent": int(root_percent_before),
    }},
    "image_id": image_id,
    "image_tag": "orbbec-agent-platform:" + release,
    "manifest_sha256": manifest_sha,
    "finished_at": finished_at,
    "release_path": "/opt/orbbec-agent-platform/releases/" + release,
    "release_sha": release,
    "schema_version": 1,
    "services_changed": False,
    "source_tree": tree,
    "started_at": started_at,
}}
path = Path(output)
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\\n")
path.chmod(0o600)
PY
/usr/bin/mv -f "$metadata_root/result.json.part" "$metadata_root/result.json"
/usr/bin/printf 'BUILD_COMPLETE release=%s image_id=%s services_changed=no\n' "$release_sha" "$image_id"
"""


def _remote_prepare_script() -> str:
    return """set -euo pipefail
umask 077
input_root="$1"; metadata_root="$2"; stage_root="$3"; release_root="$4"
[[ "$input_root" =~ ^/opt/orbbec-agent-platform/private/stage-build-inputs/ac8c27588b2962ba08d00c11545eada370613908-[0-9a-f]{32}$ ]]
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/ac8c27588b2962ba08d00c11545eada370613908/stage-build-[0-9a-f]{32}$ ]]
[[ "$stage_root" =~ ^/data/staging/orbbec-agent-platform/[0-9a-f]{32}$ ]]
[[ "$release_root" == /opt/orbbec-agent-platform/releases/ac8c27588b2962ba08d00c11545eada370613908 ]]
[[ ! -e "$input_root" && ! -L "$input_root" ]]
[[ ! -e "$metadata_root" && ! -L "$metadata_root" ]]
[[ ! -e "$stage_root" && ! -L "$stage_root" ]]
[[ ! -e "$release_root" && ! -L "$release_root" ]]
install -d -m 700 /opt/orbbec-agent-platform/private/stage-build-inputs
install -d -m 700 /data/staging/orbbec-agent-platform
install -d -m 700 /data/orbbec-agent-platform/release-metadata/ac8c27588b2962ba08d00c11545eada370613908
install -d -m 700 "$input_root" "$metadata_root"
"""


def _remote_launch_script() -> str:
    return """set -euo pipefail
umask 077
input_root="$1"; metadata_root="$2"; archive_sha="$3"; lock_sha="$4"; job_sha="$5"
started=0
cleanup_unlaunched() {
  selected=$?
  trap - EXIT
  if [[ "$started" == 0 ]]; then
    if [[ -d "$input_root" && ! -L "$input_root" ]]; then
      find "$input_root" -depth -mindepth 1 -delete
      rmdir "$input_root"
    fi
    if [[ -d "$metadata_root" && ! -L "$metadata_root" ]]; then
      find "$metadata_root" -depth -mindepth 1 -delete
      rmdir "$metadata_root"
    fi
  fi
  exit "$selected"
}
trap cleanup_unlaunched EXIT
[[ -d "$input_root" && ! -L "$input_root" && -d "$metadata_root" && ! -L "$metadata_root" ]]
[[ "$(sha256sum "$input_root/source.tar.gz.part" | awk '{print $1}')" == "$archive_sha" ]]
[[ "$(sha256sum "$input_root/deploy-input-lock.py.part" | awk '{print $1}')" == "$lock_sha" ]]
[[ "$(sha256sum "$input_root/remote-job.sh.part" | awk '{print $1}')" == "$job_sha" ]]
chmod 600 "$input_root/source.tar.gz.part"
chmod 700 "$input_root/deploy-input-lock.py.part" "$input_root/remote-job.sh.part"
mv "$input_root/source.tar.gz.part" "$input_root/source.tar.gz"
mv "$input_root/deploy-input-lock.py.part" "$input_root/deploy-input-lock.py"
mv "$input_root/remote-job.sh.part" "$input_root/remote-job.sh"
nohup setsid /bin/bash "$input_root/remote-job.sh" >"$metadata_root/stdout.log" 2>"$metadata_root/stderr.log" < /dev/null &
pid=$!
started=1
printf '%s\n' "$pid" > "$metadata_root/pid.part"
chmod 600 "$metadata_root/pid.part"
mv "$metadata_root/pid.part" "$metadata_root/pid"
printf 'STARTED pid=%s\n' "$pid"
trap - EXIT
"""


def _remote_abandon_prepare_script() -> str:
    return """set -euo pipefail
input_root="$1"; metadata_root="$2"
[[ "$input_root" =~ ^/opt/orbbec-agent-platform/private/stage-build-inputs/ac8c27588b2962ba08d00c11545eada370613908-[0-9a-f]{32}$ ]]
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/ac8c27588b2962ba08d00c11545eada370613908/stage-build-[0-9a-f]{32}$ ]]
[[ ! -e "$metadata_root/pid" && ! -L "$metadata_root/pid" ]]
if [[ -d "$input_root" && ! -L "$input_root" ]]; then
  find "$input_root" -depth -mindepth 1 -delete
  rmdir "$input_root"
fi
if [[ -d "$metadata_root" && ! -L "$metadata_root" ]]; then
  find "$metadata_root" -depth -mindepth 1 -delete
  rmdir "$metadata_root"
fi
"""


def _run_logged(
    arguments: list[str],
    *,
    stdin: bytes | None,
    stdout_path: Path,
    stderr_path: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        arguments,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    stdout_path.chmod(0o600)
    stderr_path.chmod(0o600)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            arguments,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def start(execute: bool) -> int:
    if not execute:
        print("stage_build.py start requires --execute", file=sys.stderr)
        return 2
    if not SSH_KEY.is_file():
        raise RuntimeError("fixed SSH key is unavailable")
    deployment_id = secrets.token_hex(16)
    action_token = str(uuid.uuid4())
    run_directory = RUNS_ROOT / f"{RELEASE_SHA}-{deployment_id}"
    run_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = remote_paths(deployment_id)
    state_path = run_directory / "state.json"
    state = {
        "action_token": action_token,
        "deployment_id": deployment_id,
        "release_sha": RELEASE_SHA,
        "remote": REMOTE,
        "remote_paths": paths,
        "schema_version": 1,
        "status": "preparing",
    }
    _write_json(state_path, state)
    launch_attempted = False
    try:
        archive = build_source_package(run_directory)
        deploy_lock = run_directory / "deploy-input-lock.py"
        deploy_lock.write_bytes(
            _git("show", f"{RELEASE_SHA}:deploy/cloud/deploy-input-lock.py")
        )
        deploy_lock.chmod(0o700)
        if sha256_file(deploy_lock) != DEPLOY_LOCK_SHA256:
            raise RuntimeError("fixed deploy-input lock helper hash mismatch")
        remote_job = run_directory / "remote-job.sh"
        remote_job.write_text(
            render_remote_job(
                deployment_id=deployment_id,
                action_token=action_token,
                archive_sha256=archive.archive_sha256,
                manifest_sha256=archive.manifest_sha256,
                source_tree=archive.source_tree,
            ),
            encoding="utf-8",
        )
        remote_job.chmod(0o700)
        state.update(
            {
                "archive_sha256": archive.archive_sha256,
                "manifest_sha256": archive.manifest_sha256,
                "source_tree": archive.source_tree,
                "status": "uploading",
            }
        )
        _write_json(state_path, state)

        ssh = ["ssh", *SSH_OPTIONS, REMOTE]
        _run_logged(
            [
                *ssh,
                "/bin/bash",
                "-s",
                "--",
                paths["input_root"],
                paths["metadata_root"],
                paths["staging_root"],
                paths["release_root"],
            ],
            stdin=_remote_prepare_script().encode(),
            stdout_path=run_directory / "prepare.stdout.log",
            stderr_path=run_directory / "prepare.stderr.log",
        )
        uploads = (
            (archive.archive_path, paths["source_archive"] + ".part", "archive"),
            (deploy_lock, paths["deploy_lock"] + ".part", "deploy-lock"),
            (remote_job, paths["remote_job"] + ".part", "remote-job"),
        )
        for local_path, remote_path, label in uploads:
            _run_logged(
                ["scp", *SSH_OPTIONS, str(local_path), f"{REMOTE}:{remote_path}"],
                stdin=None,
                stdout_path=run_directory / f"upload-{label}.stdout.log",
                stderr_path=run_directory / f"upload-{label}.stderr.log",
            )
        job_sha = sha256_file(remote_job)
        launch_attempted = True
        launch = _run_logged(
            [
                *ssh,
                "/bin/bash",
                "-s",
                "--",
                paths["input_root"],
                paths["metadata_root"],
                archive.archive_sha256,
                DEPLOY_LOCK_SHA256,
                job_sha,
            ],
            stdin=_remote_launch_script().encode(),
            stdout_path=run_directory / "launch.stdout.log",
            stderr_path=run_directory / "launch.stderr.log",
        )
        started = launch.stdout.decode("ascii", "strict").strip()
        if re.fullmatch(r"STARTED pid=[1-9][0-9]*", started) is None:
            raise RuntimeError("remote launch acknowledgement invalid")
        state.update({"remote_job_sha256": job_sha, "status": "running"})
        _write_json(state_path, state)
        print(str(run_directory))
        return 0
    except Exception:
        if not launch_attempted:
            _run_logged(
                [
                    "ssh",
                    *SSH_OPTIONS,
                    REMOTE,
                    "/bin/bash",
                    "-s",
                    "--",
                    paths["input_root"],
                    paths["metadata_root"],
                ],
                stdin=_remote_abandon_prepare_script().encode(),
                stdout_path=run_directory / "abandon-prepare.stdout.log",
                stderr_path=run_directory / "abandon-prepare.stderr.log",
                check=False,
            )
        state["status"] = "start_failed_or_uncertain"
        _write_json(state_path, state)
        print(f"START_FAILED_OR_UNCERTAIN run_dir={run_directory}", file=sys.stderr)
        raise


def _load_state(run_directory: Path) -> dict[str, object]:
    resolved_root = RUNS_ROOT.resolve()
    resolved = run_directory.resolve()
    if resolved.parent != resolved_root or run_directory.is_symlink():
        raise ValueError("run directory is outside the fixed receipt root")
    value = json.loads((resolved / "state.json").read_text(encoding="utf-8"))
    if (
        value.get("schema_version") != 1
        or value.get("release_sha") != RELEASE_SHA
        or HEX32.fullmatch(str(value.get("deployment_id", ""))) is None
        or value.get("remote") != REMOTE
        or value.get("remote_paths") != remote_paths(str(value["deployment_id"]))
    ):
        raise ValueError("run state identity mismatch")
    return value


def _poll_once(run_directory: Path) -> tuple[str, int | None]:
    state = _load_state(run_directory)
    paths = state["remote_paths"]
    assert isinstance(paths, dict)
    metadata_root = str(paths["metadata_root"])
    poll_script = """set -euo pipefail
metadata_root="$1"
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/ac8c27588b2962ba08d00c11545eada370613908/stage-build-[0-9a-f]{32}$ ]]
if [[ -f "$metadata_root/exit_code" && ! -L "$metadata_root/exit_code" ]]; then
  value="$(cat "$metadata_root/exit_code")"
  [[ "$value" =~ ^[0-9]+$ ]]
  printf 'COMPLETE %s\n' "$value"
elif [[ -f "$metadata_root/pid" && ! -L "$metadata_root/pid" ]]; then
  pid="$(cat "$metadata_root/pid")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]]
  if kill -0 "$pid" 2>/dev/null; then printf 'RUNNING %s\n' "$pid"; else printf 'ORPHANED %s\n' "$pid"; fi
else
  printf 'NOT_LAUNCHED\n'
fi
"""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    completed = _run_logged(
        [
            "ssh",
            *SSH_OPTIONS,
            REMOTE,
            "/bin/bash",
            "-s",
            "--",
            metadata_root,
        ],
        stdin=poll_script.encode(),
        stdout_path=run_directory / f"poll-{stamp}.stdout.log",
        stderr_path=run_directory / f"poll-{stamp}.stderr.log",
    )
    line = completed.stdout.decode("ascii", "strict").strip()
    if line.startswith("COMPLETE "):
        exit_code = int(line.split()[1])
        return "complete", exit_code
    if line.startswith("RUNNING "):
        return "running", None
    if line.startswith("ORPHANED "):
        return "orphaned", None
    if line == "NOT_LAUNCHED":
        return "not_launched", None
    raise RuntimeError("invalid remote poll response")


def _fetch_completion(run_directory: Path, exit_code: int) -> None:
    state = _load_state(run_directory)
    paths = state["remote_paths"]
    assert isinstance(paths, dict)
    metadata_root = str(paths["metadata_root"])
    for name in ("stdout.log", "stderr.log", "exit_code"):
        _run_logged(
            [
                "scp",
                *SSH_OPTIONS,
                f"{REMOTE}:{metadata_root}/{name}",
                str(run_directory / f"remote-{name}"),
            ],
            stdin=None,
            stdout_path=run_directory / f"fetch-{name}.stdout.log",
            stderr_path=run_directory / f"fetch-{name}.stderr.log",
        )
    remote_exit = int((run_directory / "remote-exit_code").read_text().strip())
    if remote_exit != exit_code:
        raise RuntimeError("remote exit status changed while fetching")
    result: object | None = None
    result_fetch = _run_logged(
        [
            "scp",
            *SSH_OPTIONS,
            f"{REMOTE}:{metadata_root}/result.json",
            str(run_directory / "remote-result.json"),
        ],
        stdin=None,
        stdout_path=run_directory / "fetch-result.stdout.log",
        stderr_path=run_directory / "fetch-result.stderr.log",
        check=False,
    )
    if result_fetch.returncode == 0:
        result = json.loads(
            (run_directory / "remote-result.json").read_text(encoding="utf-8")
        )
    receipt = {
        "deployment_id": state["deployment_id"],
        "exit_code": exit_code,
        "local_archive_sha256": state.get("archive_sha256"),
        "local_manifest_sha256": state.get("manifest_sha256"),
        "release_sha": RELEASE_SHA,
        "remote_result": result,
        "schema_version": 1,
        "stderr_sha256": sha256_file(run_directory / "remote-stderr.log"),
        "stdout_sha256": sha256_file(run_directory / "remote-stdout.log"),
    }
    _write_json(run_directory / "receipt.json", receipt)
    state["status"] = "complete" if exit_code == 0 else "failed"
    state["exit_code"] = exit_code
    _write_json(run_directory / "state.json", state)


def poll(run_directory: Path, wait: bool, interval: int) -> int:
    while True:
        status, exit_code = _poll_once(run_directory)
        print(status if exit_code is None else f"{status} exit_code={exit_code}")
        if status == "complete":
            assert exit_code is not None
            _fetch_completion(run_directory, exit_code)
            return exit_code
        if status in {"orphaned", "not_launched"}:
            return 3
        if not wait:
            return 0
        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--execute", action="store_true")
    poll_parser = subparsers.add_parser("poll")
    poll_parser.add_argument("--run-dir", type=Path, required=True)
    poll_parser.add_argument("--wait", action="store_true")
    poll_parser.add_argument("--interval", type=int, default=10, choices=range(5, 61))
    subparsers.add_parser("facts")
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if args.command == "start":
        return start(args.execute)
    if args.command == "poll":
        return poll(args.run_dir, args.wait, args.interval)
    if args.command == "facts":
        print(
            json.dumps(
                {
                    "release_sha": RELEASE_SHA,
                    "source_tree": EXPECTED_SOURCE_TREE,
                    "remote": REMOTE,
                    "private_input_pattern": str(PRIVATE_INPUT_PARENT)
                    + f"/{RELEASE_SHA}-<deployment_id>/source.tar.gz",
                    "service_changes": False,
                },
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError


if __name__ == "__main__":
    raise SystemExit(main())
