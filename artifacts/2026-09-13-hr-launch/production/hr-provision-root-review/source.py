#!/usr/bin/env python3
"""Provision fixed HR configuration, knowledge and volumes without starting services.

`start` is inert unless `--execute` is present. A started job runs detached on
the fixed production host; use `poll` to retrieve the terminal receipt. Secret
bytes and the provider credential fingerprint are never written to public logs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import time
from typing import Iterable
import uuid


WORKTREE = Path(
    "/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-cloud-loop-e-release"
)
ARTIFACT_ROOT = WORKTREE / "artifacts/2026-09-13-hr-launch/production"
RUNS_ROOT = ARTIFACT_ROOT / "hr-provision-runs"
CONFIG_ROOT = Path(
    "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/"
    "hr-launch-2026-09-13/release-config"
)
KNOWLEDGE_ARCHIVE = Path(
    "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/"
    "hr-launch-2026-09-13/"
    "knowledge-hr-intelligence-57d25a1702f4718737fb7779.tar.gz"
)
RELEASE_SHA = "ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7"
SOURCE_TREE = "a7489e2a43d09ee8727748488abdcbf64f45b9e9"
IMAGE_ID = "sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59"
MANIFEST_SHA256 = "38544645eb7d2604af3dbe2214949099cbf6f6d85df7756203b4c5db27a04d2e"
DEPLOY_LOCK_SHA256 = "45c2d3fce8a5ef2e3dd6306eaa15b7cdbfc85072bed164033684f84b425a4eb4"
EXISTING_API_ID = "3347e61ede7d3ab0bd96c0cdc25cff1d6808c89ab2b8a31f24953bebccd4fcf7"
KNOWLEDGE_RELEASE = "hr-intelligence-57d25a1702f4718737fb7779"
KNOWLEDGE_SHA256 = "1156e8e5b75eabd8d342636b3dfac401758f06cfb5c0311bb448a11cde256652"
KNOWLEDGE_BYTES = 208224
CONFIGURATION_REVISION = "58697a41d705ad332764f81a57011930e78e1b57462293f6cea036c78e4f3280"
REMOTE = "root@47.106.112.69"
SSH_KEY = Path("/Users/neo/.ssh/orbbec_aliyun_ed25519")
SSH_OPTIONS = (
    "-i", str(SSH_KEY), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
)
CONFIG_FILES = (
    "hr-budget-profile.json",
    "hr-content-keyring.json",
    "hr-diagnostic-profile.json",
    "hr-provider-credential",
    "hr-provider-profile.json",
    "hr-release-policy.json",
)
PUBLIC_CONFIG_SHA256 = {
    "hr-budget-profile.json": "fe68be1fbba168e6451c094a4ff1f07df4dcb5dea07a8e30359276c0c7bfd82f",
    "hr-content-keyring.json": "4748b1bbacf7a7aa1bdc529edb4905a455896c3a642655c999dac622c7790c52",
    "hr-diagnostic-profile.json": "7187d1e8e2a4d61b1dc5dfedb22d703a462df21470e0c145365b20fb3ed467c3",
    "hr-provider-profile.json": "c3dacdfd3044ce549500c80d87d0ef594da5dfd6f412aa12f826c73e7290e24f",
    "hr-release-policy.json": "681b6754b16447205e0224945e058653b55661f75b9c5e14d9760c460ac1af49",
}
HEX32 = re.compile(r"[0-9a-f]{32}\Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_regular(path: Path, *, owner: int | None = None) -> os.stat_result:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (owner is not None and metadata.st_uid != owner)
    ):
        raise ValueError("protected input metadata invalid")
    return metadata


def validate_config_directory(root: Path) -> dict[str, str]:
    metadata = root.lstat()
    if (
        root.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
        or {item.name for item in root.iterdir()} != set(CONFIG_FILES)
    ):
        raise ValueError("configuration directory invalid")
    for name in CONFIG_FILES:
        info = _protected_regular(root / name, owner=os.getuid())
        if not 0 < info.st_size <= 65536:
            raise ValueError("configuration file size invalid")
    hashes = {name: sha256_file(root / name) for name in PUBLIC_CONFIG_SHA256}
    if hashes != PUBLIC_CONFIG_SHA256:
        raise ValueError("configuration identity mismatch")
    provider = json.loads((root / "hr-provider-profile.json").read_text())
    if provider.get("credential_file") != "/run/hr-agent-secrets/hr-provider-credential":
        raise ValueError("provider credential location invalid")
    model = provider.get("model")
    if not isinstance(model, str) or re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", model) is None:
        raise ValueError("provider model invalid")
    return hashes


def _safe_archive_members(members: Iterable[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    result = list(members)
    seen: set[str] = set()
    for member in result:
        path = PurePosixPath(member.name)
        if (
            not member.name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or member.name in seen
            or not (member.isdir() or member.isreg())
        ):
            raise ValueError("knowledge archive member invalid")
        seen.add(member.name)
    return result


def validate_knowledge_archive(path: Path, *, verify_hash: bool = True) -> dict[str, object]:
    metadata = _protected_regular(path, owner=os.getuid())
    if verify_hash and (metadata.st_size != KNOWLEDGE_BYTES or sha256_file(path) != KNOWLEDGE_SHA256):
        raise ValueError("knowledge archive identity mismatch")
    expected_prefix = f"releases/{KNOWLEDGE_RELEASE}/"
    with tarfile.open(path, "r:gz") as archive:
        members = _safe_archive_members(archive.getmembers())
        names = {member.name for member in members}
        if "current.json" not in names or f"{expected_prefix}manifest.json" not in names:
            raise ValueError("knowledge release files missing")
        if any(name not in {"current.json", "releases"} and not name.startswith("releases/") for name in names):
            raise ValueError("knowledge archive has unexpected root")
        current_file = archive.extractfile("current.json")
        manifest_file = archive.extractfile(f"{expected_prefix}manifest.json")
        if current_file is None or manifest_file is None:
            raise ValueError("knowledge documents unavailable")
        current = json.load(current_file)
        manifest = json.load(manifest_file)
        if current != {"release_id": KNOWLEDGE_RELEASE}:
            raise ValueError("knowledge pointer invalid")
        resources = manifest.get("resources")
        if manifest.get("release_id") != KNOWLEDGE_RELEASE or not isinstance(resources, list):
            raise ValueError("knowledge manifest invalid")
        paths = [item.get("path") for item in resources if isinstance(item, dict)]
        if len(paths) != 94 or len(set(paths)) != 94 or not all(isinstance(item, str) for item in paths):
            raise ValueError("knowledge resource index invalid")
        intelligence = sum(item.startswith("intelligence/agent/") and item.endswith(".md") for item in paths)
        if intelligence != 85:
            raise ValueError("knowledge intelligence count invalid")
    return {
        "release_id": KNOWLEDGE_RELEASE,
        "intelligence_markdown_count": intelligence,
        "method_resource_count": len(paths) - intelligence,
        "knowledge_sha256": sha256_file(path),
    }


def validate_local_inputs() -> dict[str, object]:
    return {
        "config_sha256": validate_config_directory(CONFIG_ROOT),
        **validate_knowledge_archive(KNOWLEDGE_ARCHIVE),
    }


def facts() -> dict[str, object]:
    return {
        "configuration_revision": CONFIGURATION_REVISION,
        "existing_api_container": EXISTING_API_ID,
        "image_id": IMAGE_ID,
        "knowledge_release": KNOWLEDGE_RELEASE,
        "knowledge_sha256": KNOWLEDGE_SHA256,
        "release_sha": RELEASE_SHA,
        "remote": REMOTE,
        "runs_migrations": False,
        "source_tree": SOURCE_TREE,
        "starts_services": False,
    }


def remote_paths(deployment_id: str) -> dict[str, str]:
    if HEX32.fullmatch(deployment_id) is None:
        raise ValueError("invalid deployment id")
    input_root = f"/opt/orbbec-agent-platform/private/hr-provision-inputs/{RELEASE_SHA}-{deployment_id}"
    metadata_root = f"/data/orbbec-agent-platform/release-metadata/{RELEASE_SHA}/hr-provision-{deployment_id}"
    return {
        "input_root": input_root,
        "metadata_root": metadata_root,
        "remote_job": input_root + "/remote-job.sh",
    }


def render_remote_job(*, deployment_id: str, action_token: str) -> str:
    if HEX32.fullmatch(deployment_id) is None or str(uuid.UUID(action_token)) != action_token:
        raise ValueError("invalid provision identity")
    paths = remote_paths(deployment_id)
    values = {
        "@@DEPLOYMENT@@": deployment_id,
        "@@TOKEN@@": action_token,
        "@@RELEASE@@": RELEASE_SHA,
        "@@TREE@@": SOURCE_TREE,
        "@@IMAGE@@": IMAGE_ID,
        "@@MANIFEST@@": MANIFEST_SHA256,
        "@@DEPLOY_LOCK_SHA@@": DEPLOY_LOCK_SHA256,
        "@@API_ID@@": EXISTING_API_ID,
        "@@KNOWLEDGE_RELEASE@@": KNOWLEDGE_RELEASE,
        "@@KNOWLEDGE_SHA@@": KNOWLEDGE_SHA256,
        "@@KNOWLEDGE_BYTES@@": str(KNOWLEDGE_BYTES),
        "@@CONFIG_REVISION@@": CONFIGURATION_REVISION,
        "@@INPUT_ROOT@@": paths["input_root"],
        "@@METADATA_ROOT@@": paths["metadata_root"],
    }
    template = r'''#!/bin/bash
set -euo pipefail
umask 077
deployment_id=@@DEPLOYMENT@@
action_token=@@TOKEN@@
release_sha=@@RELEASE@@
source_tree=@@TREE@@
image_id=@@IMAGE@@
manifest_sha=@@MANIFEST@@
deploy_lock_sha=@@DEPLOY_LOCK_SHA@@
api_id=@@API_ID@@
knowledge_release=@@KNOWLEDGE_RELEASE@@
knowledge_sha=@@KNOWLEDGE_SHA@@
knowledge_bytes=@@KNOWLEDGE_BYTES@@
configuration_revision=@@CONFIG_REVISION@@
input_root=@@INPUT_ROOT@@
metadata_root=@@METADATA_ROOT@@
release_root=/opt/orbbec-agent-platform/releases/$release_sha
deploy_lock=$release_root/deploy/cloud/deploy-input-lock.py
platform_private=/opt/orbbec-agent-platform/private
platform_env=$platform_private/platform.env
private_target=$platform_private/hr-agent
knowledge_root=/data/orbbec-agent-platform/hr-knowledge
knowledge_target=$knowledge_root/releases/$knowledge_release
knowledge_current=$knowledge_root/current.json
current_temp=$knowledge_root/.current.$deployment_id
work_target=/data/orbbec-agent-platform/hr-work
stage_root=/data/orbbec-agent-platform/provision-staging/$deployment_id
hr_volume=orbbec-agent-platform-hr-agent-secrets
api_volume=orbbec-agent-platform-api-secrets
action_lock=$platform_private/agent-brain-action.lock
action_owner_temp=$platform_private/.hr-provision-action-owner.$deployment_id
release_created=0
releases_parent_created=0
current_created=0
private_created=0
work_created=0
volume_created=0
stage_created=0
action_owned=0
action_created=0
deploy_acquire_attempted=0
current_temp_attempted=0
finished=0

release_action_lock() {
  [[ "$action_owned" == 1 ]] || return 0
  [[ -d "$action_lock" && ! -L "$action_lock" ]] || return 1
  [[ -f "$action_lock/owner" && ! -L "$action_lock/owner" ]] || return 1
  [[ "$(/usr/bin/stat -c '%a %U' "$action_lock/owner")" == '600 root' ]] || return 1
  [[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]] || return 1
  [[ "$(/usr/bin/find "$action_lock" -mindepth 1 -maxdepth 1 -printf '%f\n' | /usr/bin/sort)" == owner ]] || return 1
  tombstone=$action_lock.releasing.$action_token
  [[ ! -e "$tombstone" && ! -L "$tombstone" ]] || return 1
  /bin/mv "$action_lock" "$tombstone" || return 1
  /bin/rm "$tombstone/owner" || return 1
  /bin/rmdir "$tombstone" || return 1
  action_owned=0
  action_created=0
}

cleanup() {
  original=$?
  trap - EXIT INT TERM HUP QUIT
  set +e
  cleanup_ok=1
  if [[ -f "$action_owner_temp" && ! -L "$action_owner_temp" ]] && \
      [[ "$(/usr/bin/stat -c '%a %U' "$action_owner_temp")" == '600 root' ]] && \
      [[ "$(/bin/cat "$action_owner_temp")" == "$action_token" ]]; then
    /bin/rm "$action_owner_temp" || cleanup_ok=0
  elif [[ -e "$action_owner_temp" || -L "$action_owner_temp" ]]; then
    cleanup_ok=0
  fi
  if [[ "$finished" != 1 ]]; then
    if [[ "$current_temp_attempted" == 1 ]]; then
      if [[ ! -e "$current_temp" && ! -L "$current_temp" ]]; then
        current_temp_attempted=0
      elif [[ -f "$current_temp" && ! -L "$current_temp" ]] && \
          [[ "$(/usr/bin/stat -c '%a %u:%g' "$current_temp")" == '644 10001:10001' ]] && \
          [[ "$(/usr/bin/sha256sum "$current_temp" | /usr/bin/cut -d' ' -f1)" == c2df4a8fdf5a4b59acdfe4a97a24c3e8be07ff48de6f0c972f87d5473847e467 ]]; then
        /bin/rm "$current_temp" || cleanup_ok=0
        current_temp_attempted=0
      else
        cleanup_ok=0
      fi
    fi
    if [[ "$current_created" == 1 ]]; then
      [[ -f "$knowledge_current" && ! -L "$knowledge_current" ]] && \
        [[ "$(/usr/bin/sha256sum "$knowledge_current" | /usr/bin/cut -d' ' -f1)" == c2df4a8fdf5a4b59acdfe4a97a24c3e8be07ff48de6f0c972f87d5473847e467 ]] && \
        /bin/rm "$knowledge_current" || cleanup_ok=0
    fi
    if [[ "$release_created" == 1 ]]; then
      [[ -d "$knowledge_target" && ! -L "$knowledge_target" && -d "$stage_root" ]] && \
        /bin/mv "$knowledge_target" "$stage_root/release-cleanup" && \
        /bin/rm -rf "$stage_root/release-cleanup" || cleanup_ok=0
    fi
    if [[ "$releases_parent_created" == 1 ]]; then /bin/rmdir "$knowledge_root/releases" || cleanup_ok=0; fi
    if [[ "$work_created" == 1 ]]; then /bin/rmdir "$work_target" || cleanup_ok=0; fi
    if [[ "$private_created" == 1 ]]; then
      /bin/rm -f "$private_target/hr-budget-profile.json" "$private_target/hr-content-keyring.json" \
        "$private_target/hr-diagnostic-profile.json" "$private_target/hr-provider-credential" \
        "$private_target/hr-provider-profile.json" "$private_target/hr-release-policy.json" \
        "$private_target/runtime.env" "$private_target/api-runtime.env" "$private_target/worker-runtime.env" || cleanup_ok=0
      /bin/rmdir "$private_target" || cleanup_ok=0
    fi
    if [[ "$volume_created" == 1 ]]; then
      label="$(/usr/bin/docker volume inspect --format '{{index .Labels "hr.provision.deployment"}}' "$hr_volume" 2>/dev/null)"
      [[ "$label" == "$deployment_id" ]] && /usr/bin/docker volume rm "$hr_volume" >/dev/null || cleanup_ok=0
    fi
  fi
  if [[ "$stage_created" == 1 && -d "$stage_root" && ! -L "$stage_root" ]]; then /bin/rm -rf "$stage_root" || cleanup_ok=0; fi
  if [[ "$action_created" == 1 && "$action_owned" == 0 ]]; then
    if [[ -d "$action_lock" && ! -L "$action_lock" ]] && [[ -z "$(/usr/bin/find "$action_lock" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      /bin/rmdir "$action_lock" || cleanup_ok=0
      action_created=0
    elif [[ -f "$action_lock/owner" && ! -L "$action_lock/owner" ]] && [[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]]; then
      action_owned=1
    else
      cleanup_ok=0
    fi
  fi
  if [[ "$deploy_acquire_attempted" == 1 ]]; then
    if /usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"; then
      /usr/bin/python3 "$deploy_lock" release "$release_sha" "$deployment_id" || cleanup_ok=0
      deploy_acquire_attempted=0
    else
      cleanup_ok=0
    fi
  fi
  if ! release_action_lock; then cleanup_ok=0; fi
  if [[ -d "$input_root" && ! -L "$input_root" ]]; then /bin/rm -rf "$input_root" || cleanup_ok=0; fi
  if [[ "$cleanup_ok" != 1 ]]; then original=1; fi
  /usr/bin/printf '%s\n' "$original" > "$metadata_root/exit_code.part"
  /bin/chmod 600 "$metadata_root/exit_code.part"
  /bin/mv "$metadata_root/exit_code.part" "$metadata_root/exit_code"
  exit "$original"
}
on_signal() { trap - INT TERM HUP QUIT; exit "$((128 + $1))"; }
trap cleanup EXIT
trap 'on_signal 2' INT
trap 'on_signal 15' TERM
trap 'on_signal 1' HUP
trap 'on_signal 3' QUIT

[[ "$input_root" =~ ^/opt/orbbec-agent-platform/private/hr-provision-inputs/$release_sha-[0-9a-f]{32}$ ]]
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/$release_sha/hr-provision-[0-9a-f]{32}$ ]]
[[ "$(/usr/bin/id -u)" == 0 ]]
[[ -d "$release_root" && ! -L "$release_root" ]]
[[ -f "$deploy_lock" && ! -L "$deploy_lock" ]]
[[ "$(/usr/bin/sha256sum "$deploy_lock" | /usr/bin/cut -d' ' -f1)" == "$deploy_lock_sha" ]]
[[ "$(/usr/bin/docker image inspect --format '{{.Id}}' "$image_id")" == "$image_id" ]]
[[ "$(/usr/bin/docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image_id" | /usr/bin/sed -n 's/^PLATFORM_RELEASE_SHA=//p')" == "$release_sha" ]]
[[ "$(/usr/bin/sha256sum "$release_root/MANIFEST.sha256" | /usr/bin/cut -d' ' -f1)" == "$manifest_sha" ]]
(cd "$release_root" && /usr/bin/sha256sum --strict -c MANIFEST.sha256 >/dev/null)
[[ "$(/usr/bin/docker inspect --format '{{.Id}}' orbbec-agent-platform-platform-api-1)" == "$api_id" ]]
[[ "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$api_id")" == true ]]
[[ "$(/usr/bin/docker inspect --format '{{range .Mounts}}{{if eq .Destination "/run/secrets"}}{{.Name}}{{end}}{{end}}' "$api_id")" == "$api_volume" ]]
[[ -d "$knowledge_root" && ! -L "$knowledge_root" ]]
[[ "$(/usr/bin/stat -c '%a %U:%G' "$platform_private")" == '700 root:root' ]]
[[ -f "$platform_env" && ! -L "$platform_env" && "$(/usr/bin/stat -c '%a %U:%G' "$platform_env")" == '600 root:root' ]]
[[ ! -e "$private_target" && ! -L "$private_target" ]]
[[ ! -e "$work_target" && ! -L "$work_target" ]]
[[ ! -e "$knowledge_current" && ! -L "$knowledge_current" ]]
[[ ! -e "$current_temp" && ! -L "$current_temp" ]]
[[ ! -e "$knowledge_target" && ! -L "$knowledge_target" ]]
! /usr/bin/docker volume inspect "$hr_volume" >/dev/null 2>&1

/usr/bin/printf '%s\n' "$action_token" > "$action_owner_temp"
/bin/chmod 600 "$action_owner_temp"
/bin/mkdir "$action_lock"
action_created=1
/bin/chmod 700 "$action_lock"
/bin/mv "$action_owner_temp" "$action_lock/owner"
action_owned=1

deploy_acquire_attempted=1
/usr/bin/python3 "$deploy_lock" acquire "$release_sha" "$deployment_id"
/usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"

staging_parent=/data/orbbec-agent-platform/provision-staging
if [[ ! -e "$staging_parent" && ! -L "$staging_parent" ]]; then /usr/bin/install -d -o root -g root -m 700 "$staging_parent"; fi
[[ -d "$staging_parent" && ! -L "$staging_parent" && "$(/usr/bin/stat -c '%a %U:%G' "$staging_parent")" == '700 root:root' ]]
/bin/mkdir "$stage_root"
/bin/chmod 700 "$stage_root"
stage_created=1

python3 - "$knowledge_root" "$metadata_root/knowledge-before.json" <<'PY'
import hashlib, json, os, pathlib, stat, sys
root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
root_info = root.lstat()
if root.is_symlink() or not stat.S_ISDIR(root_info.st_mode):
    raise SystemExit('knowledge_root_type_invalid')
knowledge_root_metadata = {
    'type': 'directory', 'uid': root_info.st_uid, 'gid': root_info.st_gid,
    'mode': stat.S_IMODE(root_info.st_mode),
}
def snapshot(root):
    result = {}
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in sorted(dirs + files):
            path = pathlib.Path(base) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise SystemExit('knowledge_tree_type_invalid')
            item = {'type': 'directory' if stat.S_ISDIR(info.st_mode) else 'regular',
                    'uid': info.st_uid, 'gid': info.st_gid,
                    'mode': stat.S_IMODE(info.st_mode)}
            if stat.S_ISREG(info.st_mode):
                item['size'] = info.st_size
                item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[relative] = item
    return result
payload = {'schema_version': 1, 'knowledge_root_metadata': knowledge_root_metadata,
           'items': snapshot(root)}
with output.open('x') as stream:
    json.dump(payload, stream, sort_keys=True, separators=(',', ':'))
    stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
os.chmod(output, 0o600)
PY

/bin/mkdir "$stage_root/secrets" "$stage_root/knowledge" "$stage_root/env" "$stage_root/work"
for name in hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json; do
  /usr/bin/install -o 10001 -g 10001 -m 600 "$input_root/$name" "$stage_root/secrets/$name"
done
/bin/chown 10001:10001 "$stage_root/secrets" "$stage_root/knowledge" "$stage_root/env" "$stage_root/work"
/bin/chmod 700 "$stage_root/secrets" "$stage_root/knowledge" "$stage_root/env" "$stage_root/work"

python3 - "$input_root/knowledge.tar.gz" "$stage_root/knowledge" "$knowledge_release" <<'PY'
import json, pathlib, stat, sys, tarfile
archive_path, destination, release = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
seen = set()
with tarfile.open(archive_path, 'r:gz') as archive:
    for member in archive.getmembers():
        parts = pathlib.PurePosixPath(member.name).parts
        if (not member.name or pathlib.PurePosixPath(member.name).is_absolute()
                or any(part in {'', '.', '..'} for part in parts)
                or member.name in seen or not (member.isdir() or member.isreg())):
            raise SystemExit('knowledge_archive_invalid')
        seen.add(member.name)
        target = destination.joinpath(*parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None: raise SystemExit('knowledge_archive_invalid')
            with target.open('xb') as output:
                output.write(source.read())
if json.loads((destination / 'current.json').read_text()) != {'release_id': release}:
    raise SystemExit('knowledge_pointer_invalid')
expected = destination / 'releases' / release
if not expected.is_dir(): raise SystemExit('knowledge_release_missing')
PY
/bin/chown -R 10001:10001 "$stage_root/knowledge"
/usr/bin/find "$stage_root/knowledge" -type d -exec /bin/chmod 755 {} +
/usr/bin/find "$stage_root/knowledge" -type f -exec /bin/chmod 644 {} +

python3 - "$platform_env" "$stage_root/runtime.env" "$stage_root/secrets/hr-provider-profile.json" "$image_id" <<'PY'
import json, os, pathlib, re, sys
source, target, provider_path, image = map(pathlib.Path, sys.argv[1:])
provider = json.loads(provider_path.read_text())
model = provider.get('model')
if not isinstance(model, str) or re.fullmatch(r'[A-Za-z0-9._:/-]{1,128}', model) is None:
    raise SystemExit('provider_model_invalid')
payload = source.read_bytes()
if b'\x00' in payload: raise SystemExit('platform_env_invalid')
if payload and not payload.endswith(b'\n'): payload += b'\n'
payload += ('PLATFORM_IMAGE=' + str(image) + '\n'
            + 'PLATFORM_HR_AGENT_MODEL=' + model + '\n'
            + 'PLATFORM_HR_AGENT_ATTACHMENTS_ENABLED=1\n'
            + 'PLATFORM_HR_WEB_WORKER_ENABLED=1\n').encode()
with target.open('xb') as stream:
    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
os.chown(target, 10001, 10001); os.chmod(target, 0o600)
PY

compose_json=$stage_root/compose.json
/usr/bin/docker compose --project-name orbbec-agent-platform \
  --env-file "$stage_root/runtime.env" \
  -f "$release_root/deploy/cloud/compose.yaml" \
  -f "$release_root/deploy/cloud/compose.hr-agent.yaml" \
  --profile hr-agent config --format json > "$compose_json"
/bin/chmod 600 "$compose_json"

python3 - "$compose_json" "$stage_root/env" "$image_id" <<'PY'
import json, os, pathlib, sys
document = json.loads(pathlib.Path(sys.argv[1]).read_text())
output = pathlib.Path(sys.argv[2]); image = sys.argv[3]
services = document.get('services')
if not isinstance(services, dict): raise SystemExit('compose_services_invalid')
for service, filename in (('platform-api', 'api-runtime.env'), ('platform-hr-agent-worker', 'worker-runtime.env')):
    value = services.get(service)
    if not isinstance(value, dict) or value.get('image') != image:
        raise SystemExit('compose_image_invalid')
    if value.get('read_only') is not True or str(value.get('user')) != '10001:10001':
        raise SystemExit('compose_confinement_invalid')
    environment = value.get('environment')
    if not isinstance(environment, dict): raise SystemExit('compose_environment_invalid')
    lines = []
    for key in sorted(environment):
        item = environment[key]
        if item is None: item = ''
        item = str(item)
        if '\n' in item or '\r' in item or '\x00' in item: raise SystemExit('compose_environment_invalid')
        lines.append(f'{key}={item}')
    path = output / filename
    with path.open('x') as stream:
        stream.write('\n'.join(lines) + '\n'); stream.flush(); os.fsync(stream.fileno())
    os.chown(path, 10001, 10001); os.chmod(path, 0o600)
PY
/bin/rm "$compose_json"

/usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"
/usr/bin/docker volume create --label "hr.provision.deployment=$deployment_id" "$hr_volume" >/dev/null
volume_created=1
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL --cap-add CHOWN \
  --security-opt no-new-privileges:true --user 0:0 -v "$hr_volume:/out" "$image_id" \
  /bin/sh -ec '/bin/chmod 700 /out && /bin/chown 10001:10001 /out'
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 10001:10001 \
  -v "$stage_root/secrets:/input:ro" -v "$stage_root/env:/env:ro" \
  -v "$api_volume:/api:ro" -v "$hr_volume:/out" \
  "$image_id" /bin/sh -ec '
set -eu; umask 077
for name in hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json; do
  test -f "/input/$name" && test ! -L "/input/$name"; /bin/cp "/input/$name" "/out/$name"
done
for name in control-database-url attachment-s3-access-key attachment-s3-secret-key content-encryption-keyring; do
  test -f "/api/$name" && test ! -L "/api/$name" && test -s "/api/$name"; /bin/cp "/api/$name" "/out/$name"
done
for name in api-runtime.env worker-runtime.env; do
  test -f "/env/$name" && test ! -L "/env/$name" && test -s "/env/$name"; /bin/cp "/env/$name" "/out/$name"
done
/usr/bin/test "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f | /usr/bin/wc -l)" -eq 12
/usr/bin/test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 ! -type f -print -quit)"
/bin/chmod 600 /out/*
'
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 10001:10001 -v "$hr_volume:/out:ro" \
  "$image_id" /bin/sh -ec '
set -eu
expected="api-runtime.env attachment-s3-access-key attachment-s3-secret-key content-encryption-keyring control-database-url hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json worker-runtime.env"
actual="$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f -printf "%f\n" | /usr/bin/sort | /usr/bin/tr "\n" " " | /usr/bin/sed "s/ $//")"
test "$actual" = "$expected"
test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 ! -type f -print -quit)"
test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f ! -user 10001 -print -quit)"
test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f ! -perm 600 -print -quit)"
'

preflight_report=$metadata_root/preflight.json.part
set +e
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 10001:10001 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700 \
  -v "$api_volume:/run/secrets:ro" -v "$hr_volume:/run/hr-agent-secrets:ro" \
  -v "$stage_root/knowledge:/data/hr-knowledge:ro" -v "$stage_root/work:/data/hr-work" \
  "$image_id" python -m tools.hr_agent.preflight --scope public-only \
  --api-env-file /run/hr-agent-secrets/api-runtime.env \
  --worker-env-file /run/hr-agent-secrets/worker-runtime.env > "$preflight_report"
preflight_status=$?
set -e
[[ "$preflight_status" == 1 ]]
python3 - "$preflight_report" "$configuration_revision" "$knowledge_release" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
if report.get('ok') is not False or report.get('blockers') != ['database_not_checked']:
    raise SystemExit('preflight_blockers_invalid')
if report.get('runtime_match') is not True:
    raise SystemExit('preflight_runtime_mismatch')
for name in ('api', 'worker'):
    item = report.get(name)
    if not isinstance(item, dict):
        raise SystemExit('preflight_configuration_invalid')
    if item.get('configuration_fingerprint') != sys.argv[2]:
        raise SystemExit('preflight_revision_invalid')
    if item.get('knowledge', {}).get('release_id') != sys.argv[3]:
        raise SystemExit('preflight_knowledge_invalid')
    if item.get('d7_product_approved') is not True:
        raise SystemExit('preflight_release_policy_invalid')
    attachments = item.get('attachments')
    if not isinstance(attachments, dict) or attachments.get('enabled') is not True \
            or attachments.get('wiring_present') is not True \
            or not isinstance(attachments.get('fingerprint'), str):
        raise SystemExit('preflight_attachment_invalid')
for key in ('configuration_fingerprint', 'hr_content_keyring_fingerprint',
            'profile_fingerprints', 'provider', 'budget', 'knowledge', 'attachments'):
    if report['api'].get(key) != report['worker'].get(key):
        raise SystemExit('preflight_runtime_identity_invalid')
if report.get('database', {}).get('checked') is not False:
    raise SystemExit('preflight_database_scope_invalid')
if report.get('limitations', {}).get('model_or_network_called') is not False:
    raise SystemExit('preflight_network_scope_invalid')
PY
/bin/chmod 600 "$preflight_report"
/bin/mv "$preflight_report" "$metadata_root/preflight.json"

/usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"
/usr/bin/install -d -o root -g root -m 700 "$private_target"
private_created=1
for name in hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json; do
  /usr/bin/install -o root -g root -m 600 "$stage_root/secrets/$name" "$private_target/$name"
done
for name in runtime.env api-runtime.env worker-runtime.env; do
  source=$stage_root/$name
  [[ "$name" == runtime.env ]] || source=$stage_root/env/$name
  /usr/bin/install -o root -g root -m 600 "$source" "$private_target/$name"
done

/usr/bin/install -d -o 10001 -g 10001 -m 700 "$work_target"
work_created=1
if [[ ! -e "$knowledge_root/releases" && ! -L "$knowledge_root/releases" ]]; then
  /usr/bin/install -d -o root -g root -m 755 "$knowledge_root/releases"
  releases_parent_created=1
fi
[[ -d "$knowledge_root/releases" && ! -L "$knowledge_root/releases" ]]
/bin/mv "$stage_root/knowledge/releases/$knowledge_release" "$knowledge_target"
release_created=1
current_temp_attempted=1
/usr/bin/install -o 10001 -g 10001 -m 644 "$stage_root/knowledge/current.json" "$current_temp"
[[ ! -e "$knowledge_current" && ! -L "$knowledge_current" ]]
/bin/mv "$current_temp" "$knowledge_current"
current_temp_attempted=0
current_created=1

python3 - "$knowledge_root" "$metadata_root/knowledge-before.json" "$metadata_root/result.json" \
  "$release_sha" "$image_id" "$configuration_revision" "$knowledge_release" <<'PY'
import hashlib, json, os, pathlib, stat, sys
root = pathlib.Path(sys.argv[1]); before_document = json.load(open(sys.argv[2]))
before = before_document['items']
root_info = root.lstat()
knowledge_root_metadata = {
    'type': 'directory' if stat.S_ISDIR(root_info.st_mode) else 'invalid',
    'uid': root_info.st_uid, 'gid': root_info.st_gid,
    'mode': stat.S_IMODE(root_info.st_mode),
}
if root.is_symlink() or knowledge_root_metadata != before_document['knowledge_root_metadata']:
    raise SystemExit('knowledge_root_metadata_changed')
def snapshot(root):
    result = {}
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in sorted(dirs + files):
            path = pathlib.Path(base) / name; info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise SystemExit('knowledge_tree_type_invalid')
            item = {'type': 'directory' if stat.S_ISDIR(info.st_mode) else 'regular',
                    'uid': info.st_uid, 'gid': info.st_gid,
                    'mode': stat.S_IMODE(info.st_mode)}
            if stat.S_ISREG(info.st_mode):
                item['size'] = info.st_size
                item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[relative] = item
    return result
knowledge_after = snapshot(root)
for name, value in before.items():
    actual = knowledge_after.get(name)
    if value['type'] == 'directory':
        if actual != {key: value[key] for key in ('type', 'uid', 'gid', 'mode')}:
            raise SystemExit('old_knowledge_changed')
    elif actual != value:
        raise SystemExit('old_knowledge_changed')
allowed = {'current.json', 'releases'}
allowed.update(name for name in knowledge_after if name.startswith('releases/' + sys.argv[7]))
if set(knowledge_after) - set(before) - allowed: raise SystemExit('unexpected_knowledge_added')
result = {'schema_version': 1, 'status': 'completed', 'release_sha': sys.argv[4],
          'image_id': sys.argv[5], 'configuration_revision': sys.argv[6],
          'knowledge_release': sys.argv[7], 'knowledge_preserved': True,
          'services_changed': False, 'migrations_run': False,
          'preflight_blockers': ['database_not_checked'],
          'secret_values_logged': False, 'credential_fingerprint_logged': False}
output = pathlib.Path(sys.argv[3])
with output.open('x') as stream:
    json.dump(result, stream, sort_keys=True, separators=(',', ':'))
    stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
os.chmod(output, 0o600)
PY
finished=1
/usr/bin/printf 'HR_PROVISION_OK release=%s image=%s services_changed=false migrations_run=false\n' "$release_sha" "$image_id"
'''
    for old, new in values.items():
        template = template.replace(old, new)
    if "@@" in template:
        raise AssertionError("unexpanded provision template token")
    return template


def _run_logged(arguments: list[str], *, stdin: bytes | None, stdout_path: Path, stderr_path: Path, check: bool = True) -> subprocess.CompletedProcess:
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        result = subprocess.run(arguments, input=stdin, stdout=stdout, stderr=stderr, check=False)
    stdout_path.chmod(0o600); stderr_path.chmod(0o600)
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, arguments)
    return result


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".part")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    temporary.chmod(0o600); temporary.replace(path)


def _remote_prepare() -> str:
    return r'''set -euo pipefail
umask 077
input_root="$1"; metadata_root="$2"
[[ "$input_root" =~ ^/opt/orbbec-agent-platform/private/hr-provision-inputs/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7-[0-9a-f]{32}$ ]]
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7/hr-provision-[0-9a-f]{32}$ ]]
[[ ! -e "$input_root" && ! -L "$input_root" && ! -e "$metadata_root" && ! -L "$metadata_root" ]]
for parent in /opt/orbbec-agent-platform/private/hr-provision-inputs /data/orbbec-agent-platform/release-metadata/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7; do
  if [[ ! -e "$parent" && ! -L "$parent" ]]; then /usr/bin/install -d -o root -g root -m 700 "$parent"; fi
  [[ -d "$parent" && ! -L "$parent" && "$(/usr/bin/stat -c '%a %U:%G' "$parent")" == '700 root:root' ]]
done
/usr/bin/install -d -o root -g root -m 700 "$input_root" "$metadata_root"
'''


def _remote_abandon() -> str:
    return rf'''set -euo pipefail
input_root="$1"; metadata_root="$2"
[[ "$input_root" =~ ^/opt/orbbec-agent-platform/private/hr-provision-inputs/{RELEASE_SHA}-[0-9a-f]{{32}}$ ]]
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/{RELEASE_SHA}/hr-provision-[0-9a-f]{{32}}$ ]]
if [[ -d "$input_root" && ! -L "$input_root" ]]; then /bin/rm -rf "$input_root"; fi
# Retain the metadata directory as evidence that preparation was attempted.
'''


def _remote_launch(job_sha: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", job_sha) is None:
        raise ValueError("invalid job hash")
    checks = "\n".join(
        f"[[ \"$(/usr/bin/sha256sum \"$input_root/{name}.part\" | /usr/bin/cut -d' ' -f1)\" == {digest} ]]"
        for name, digest in PUBLIC_CONFIG_SHA256.items()
    )
    return f'''set -euo pipefail
input_root="$1"; metadata_root="$2"
[[ "$input_root" =~ ^/opt/orbbec-agent-platform/private/hr-provision-inputs/{RELEASE_SHA}-[0-9a-f]{{32}}$ ]]
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/{RELEASE_SHA}/hr-provision-[0-9a-f]{{32}}$ ]]
{checks}
[[ -f "$input_root/hr-provider-credential.part" && ! -L "$input_root/hr-provider-credential.part" ]]
credential_size="$(/usr/bin/stat -c %s "$input_root/hr-provider-credential.part")"
[[ "$credential_size" -gt 0 && "$credential_size" -le 65536 ]]
[[ "$(/usr/bin/stat -c %s "$input_root/knowledge.tar.gz.part")" == {KNOWLEDGE_BYTES} ]]
[[ "$(/usr/bin/sha256sum "$input_root/knowledge.tar.gz.part" | /usr/bin/cut -d' ' -f1)" == {KNOWLEDGE_SHA256} ]]
[[ "$(/usr/bin/sha256sum "$input_root/remote-job.sh.part" | /usr/bin/cut -d' ' -f1)" == {job_sha} ]]
for path in "$input_root"/*.part; do [[ -f "$path" && ! -L "$path" && "$(/usr/bin/stat -c '%a %U:%G' "$path")" == '600 root:root' ]]; done
for name in {' '.join(CONFIG_FILES)} knowledge.tar.gz remote-job.sh; do /bin/mv "$input_root/$name.part" "$input_root/$name"; done
/bin/chmod 700 "$input_root/remote-job.sh"
/usr/bin/nohup /usr/bin/setsid /usr/bin/env -i \
  PATH=/usr/bin:/bin LANG=C.UTF-8 DOCKER_HOST=unix:///var/run/docker.sock /bin/bash -c '
  job="$1"; metadata="$2"
  /usr/bin/printf "%s\\n" "$$" > "$metadata/pid.part"
  /bin/chmod 600 "$metadata/pid.part"; /bin/mv "$metadata/pid.part" "$metadata/pid"
  set +e
  /usr/bin/timeout --signal=TERM --kill-after=10s 300 "$job" >"$metadata/stdout.log" 2>"$metadata/stderr.log"
  status=$?
  if [[ ! -f "$metadata/exit_code" ]]; then /usr/bin/printf "%s\\n" "$status" > "$metadata/exit_code.part"; /bin/chmod 600 "$metadata/exit_code.part"; /bin/mv "$metadata/exit_code.part" "$metadata/exit_code"; fi
  exit "$status"
' _ "$input_root/remote-job.sh" "$metadata_root" </dev/null >/dev/null 2>&1 &
/usr/bin/printf 'STARTED pid=%s\n' "$!"
'''


def start(execute: bool) -> int:
    if not execute:
        print("provision_hr_release.py start requires --execute", file=sys.stderr)
        return 2
    if not SSH_KEY.is_file():
        raise RuntimeError("fixed SSH key unavailable")
    local = validate_local_inputs()
    deployment_id = secrets.token_hex(16); action_token = str(uuid.uuid4())
    run_directory = RUNS_ROOT / f"{RELEASE_SHA}-{deployment_id}"
    run_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = remote_paths(deployment_id)
    state = {"schema_version": 1, "status": "preparing", "deployment_id": deployment_id,
             "action_token": action_token, "release_sha": RELEASE_SHA, "image_id": IMAGE_ID,
             "remote": REMOTE, "remote_paths": paths,
             "config_sha256": local["config_sha256"], "knowledge_sha256": KNOWLEDGE_SHA256}
    _write_json(run_directory / "state.json", state)
    job = run_directory / "remote-job.sh"
    job.write_text(render_remote_job(deployment_id=deployment_id, action_token=action_token))
    job.chmod(0o600); job_sha = sha256_file(job)
    ssh = ["ssh", *SSH_OPTIONS, REMOTE]
    launched = False
    try:
        _run_logged([*ssh, "/bin/bash", "-s", "--", paths["input_root"], paths["metadata_root"]],
                    stdin=_remote_prepare().encode(), stdout_path=run_directory / "prepare.stdout.log",
                    stderr_path=run_directory / "prepare.stderr.log")
        uploads = [(CONFIG_ROOT / name, name) for name in CONFIG_FILES]
        uploads += [(KNOWLEDGE_ARCHIVE, "knowledge.tar.gz"), (job, "remote-job.sh")]
        for source, name in uploads:
            _run_logged(["scp", *SSH_OPTIONS, str(source), f"{REMOTE}:{paths['input_root']}/{name}.part"],
                        stdin=None, stdout_path=run_directory / f"upload-{name}.stdout.log",
                        stderr_path=run_directory / f"upload-{name}.stderr.log")
        launched = True
        result = _run_logged([*ssh, "/bin/bash", "-s", "--", paths["input_root"], paths["metadata_root"]],
                             stdin=_remote_launch(job_sha).encode(), stdout_path=run_directory / "launch.stdout.log",
                             stderr_path=run_directory / "launch.stderr.log")
        started = (run_directory / "launch.stdout.log").read_text().strip()
        if re.fullmatch(r"STARTED pid=[1-9][0-9]*", started) is None:
            raise RuntimeError("remote acknowledgement invalid")
        state.update(status="running", remote_job_sha256=job_sha)
        _write_json(run_directory / "state.json", state)
        print(run_directory)
        return 0
    except Exception:
        if not launched:
            _run_logged([*ssh, "/bin/bash", "-s", "--", paths["input_root"], paths["metadata_root"]],
                        stdin=_remote_abandon().encode(), stdout_path=run_directory / "abandon.stdout.log",
                        stderr_path=run_directory / "abandon.stderr.log", check=False)
        state["status"] = "start_failed_or_uncertain" if launched else "prepare_failed"
        _write_json(run_directory / "state.json", state)
        raise


def _load_state(run_directory: Path) -> dict:
    resolved = run_directory.resolve()
    if resolved.parent != RUNS_ROOT.resolve() or run_directory.is_symlink():
        raise ValueError("run directory invalid")
    state = json.loads((resolved / "state.json").read_text())
    if (state.get("schema_version") != 1 or state.get("release_sha") != RELEASE_SHA
            or state.get("image_id") != IMAGE_ID or state.get("remote") != REMOTE
            or HEX32.fullmatch(str(state.get("deployment_id", ""))) is None
            or state.get("remote_paths") != remote_paths(str(state["deployment_id"]))):
        raise ValueError("run identity invalid")
    return state


def poll(run_directory: Path, wait: bool, interval: int) -> int:
    state = _load_state(run_directory); metadata = state["remote_paths"]["metadata_root"]
    ssh = ["ssh", *SSH_OPTIONS, REMOTE]
    while True:
        script = f'''set -euo pipefail
metadata="$1"
[[ "$metadata" =~ ^/data/orbbec-agent-platform/release-metadata/{RELEASE_SHA}/hr-provision-[0-9a-f]{{32}}$ ]]
if [[ -f "$metadata/exit_code" && ! -L "$metadata/exit_code" ]]; then printf 'COMPLETE '; /bin/cat "$metadata/exit_code";
elif [[ -f "$metadata/pid" && ! -L "$metadata/pid" ]] && /bin/kill -0 "$(/bin/cat "$metadata/pid")" 2>/dev/null; then printf 'RUNNING\n';
else printf 'ORPHANED\n'; fi
'''
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{time.time_ns()}"
        result = _run_logged([*ssh, "/bin/bash", "-s", "--", metadata], stdin=script.encode(),
                             stdout_path=run_directory / f"poll-{stamp}.stdout.log",
                             stderr_path=run_directory / f"poll-{stamp}.stderr.log")
        line = (run_directory / f"poll-{stamp}.stdout.log").read_text().strip()
        if line.startswith("COMPLETE "):
            code = int(line.split()[1])
            for name in ("stdout.log", "stderr.log", "result.json", "knowledge-before.json", "preflight.json"):
                _run_logged(["scp", *SSH_OPTIONS, f"{REMOTE}:{metadata}/{name}", str(run_directory / f"remote-{name}")],
                            stdin=None, stdout_path=run_directory / f"fetch-{name}.stdout.log",
                            stderr_path=run_directory / f"fetch-{name}.stderr.log", check=name in {"stdout.log", "stderr.log"})
            state.update(status="complete" if code == 0 else "failed", exit_code=code)
            _write_json(run_directory / "state.json", state)
            remote_result = None
            if (run_directory / "remote-result.json").is_file():
                remote_result = json.loads((run_directory / "remote-result.json").read_text())
            receipt = {
                "schema_version": 1,
                "deployment_id": state["deployment_id"],
                "release_sha": RELEASE_SHA,
                "image_id": IMAGE_ID,
                "exit_code": code,
                "remote_result": remote_result,
                "stdout_sha256": sha256_file(run_directory / "remote-stdout.log"),
                "stderr_sha256": sha256_file(run_directory / "remote-stderr.log"),
                "credential_fingerprint_logged": False,
            }
            _write_json(run_directory / "receipt.json", receipt)
            print(f"complete exit_code={code}")
            return code
        print(line.lower())
        if line == "ORPHANED" or not wait:
            return 3 if line == "ORPHANED" else 0
        time.sleep(interval)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    start_parser = sub.add_parser("start"); start_parser.add_argument("--execute", action="store_true")
    poll_parser = sub.add_parser("poll"); poll_parser.add_argument("--run-dir", required=True, type=Path)
    poll_parser.add_argument("--wait", action="store_true"); poll_parser.add_argument("--interval", type=int, default=5, choices=range(5, 61))
    sub.add_parser("facts")
    return root


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    if args.command == "facts": print(json.dumps(facts(), sort_keys=True)); return 0
    if args.command == "start": return start(args.execute)
    if args.command == "poll": return poll(args.run_dir, args.wait, args.interval)
    raise AssertionError


if __name__ == "__main__":
    raise SystemExit(main())
