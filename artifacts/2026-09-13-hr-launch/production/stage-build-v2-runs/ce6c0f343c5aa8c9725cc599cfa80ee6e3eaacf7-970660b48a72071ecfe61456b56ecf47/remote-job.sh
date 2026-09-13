#!/bin/bash
set -euo pipefail
umask 077
release_sha=ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7
deployment_id=970660b48a72071ecfe61456b56ecf47
action_token=dbbf3e24-c80e-4ae8-88a6-a82f884518e7
expected_archive_sha=2e45827d62a31ddc61bb1d234a6e6d45e0a57334b8056af62454f8bcf57827e6
expected_manifest_sha=38544645eb7d2604af3dbe2214949099cbf6f6d85df7756203b4c5db27a04d2e
expected_source_tree=a7489e2a43d09ee8727748488abdcbf64f45b9e9
expected_lock_sha=45c2d3fce8a5ef2e3dd6306eaa15b7cdbfc85072bed164033684f84b425a4eb4
input_root=/opt/orbbec-agent-platform/private/stage-build-inputs/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7-970660b48a72071ecfe61456b56ecf47
archive_path=/opt/orbbec-agent-platform/private/stage-build-inputs/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7-970660b48a72071ecfe61456b56ecf47/source.tar.gz
deploy_lock=/opt/orbbec-agent-platform/private/stage-build-inputs/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7-970660b48a72071ecfe61456b56ecf47/deploy-input-lock.py
stage_root=/data/staging/orbbec-agent-platform/970660b48a72071ecfe61456b56ecf47
metadata_root=/data/orbbec-agent-platform/release-metadata/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7/stage-build-970660b48a72071ecfe61456b56ecf47
release_root=/opt/orbbec-agent-platform/releases/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7
action_lock=/opt/orbbec-agent-platform/private/agent-brain-action.lock
deploy_acquired=0
action_created=0
action_acquired=0
stage_created=0
started_at="$(/usr/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"

write_exit() {
  local selected="$1"
  /usr/bin/printf '%s
' "$selected" > "$metadata_root/exit_code.part"
  /usr/bin/chmod 600 "$metadata_root/exit_code.part"
  /usr/bin/mv -f "$metadata_root/exit_code.part" "$metadata_root/exit_code"
}

release_action_lock() {
  [[ -d "$action_lock" && ! -L "$action_lock" ]] || return 1
  [[ -f "$action_lock/owner" && ! -L "$action_lock/owner" ]] || return 1
  [[ "$(/usr/bin/stat -c '%a %U' "$action_lock/owner")" == '600 root' ]] || return 1
  [[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]] || return 1
  local tombstone="$action_lock.releasing.$action_token"
  [[ ! -e "$tombstone" && ! -L "$tombstone" ]] || return 1
  /usr/bin/mv "$action_lock" "$tombstone" || return 1
  /usr/bin/rm -f -- "$tombstone/owner" || return 1
  /usr/bin/rmdir "$tombstone" || return 1
}

cleanup() {
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
}
trap cleanup EXIT

[[ "$(id -u)" == 0 ]]
[[ "$release_sha" =~ ^[0-9a-f]{40}$ && "$deployment_id" =~ ^[0-9a-f]{32}$ ]]
[[ "$action_token" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
[[ -d /opt/orbbec-agent-platform/private && ! -L /opt/orbbec-agent-platform/private ]]
[[ "$(/usr/bin/stat -c '%a %U' /opt/orbbec-agent-platform/private)" == '700 root' ]]
[[ -d "$input_root" && ! -L "$input_root" ]]
[[ "$(/usr/bin/stat -c '%a %U' "$input_root")" == '700 root' ]]
[[ -f "$archive_path" && ! -L "$archive_path" ]]
[[ -f "$deploy_lock" && ! -L "$deploy_lock" ]]
[[ "$(/usr/bin/stat -c '%a %U' "$archive_path")" == '600 root' ]]
[[ "$(/usr/bin/stat -c '%a %U' "$deploy_lock")" == '700 root' ]]
[[ "$(/usr/bin/sha256sum "$deploy_lock" | /usr/bin/awk '{print $1}')" == "$expected_lock_sha" ]]
[[ "$(/usr/bin/sha256sum "$archive_path" | /usr/bin/awk '{print $1}')" == "$expected_archive_sha" ]]
[[ ! -e "$release_root" && ! -L "$release_root" ]]
[[ -z "$(/usr/bin/docker image ls -q orbbec-agent-platform:$release_sha)" ]]

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
/usr/bin/printf 'DISK_BEFORE root_used=%s root_available=%s root_percent=%s data_used=%s data_available=%s data_percent=%s projected_root_bytes=%s
'   "$root_used_before" "$root_available_before" "$root_percent_before"   "$data_used_before" "$data_available_before" "$data_percent_before"   "$projected_root_bytes"

[[ ! -e "$action_lock" && ! -L "$action_lock" ]]
/usr/bin/mkdir -m 700 "$action_lock"
action_created=1
/usr/bin/printf '%s
' "$action_token" > "$action_lock/owner"
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
            or any(part in {"", ".", ".."} for part in path.parts)
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
[[ "$(/usr/bin/sha256sum "$source_root/MANIFEST.sha256" | /usr/bin/awk '{print $1}')" == "$expected_manifest_sha" ]]
(cd "$source_root" && /usr/bin/sha256sum --check MANIFEST.sha256 >/dev/null)
/usr/bin/python3 "$deploy_lock" validate "$release_sha" "$deployment_id"

/usr/bin/docker build --pull --build-arg RELEASE_SHA=ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7   -t orbbec-agent-platform:ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7   -f "$source_root/deploy/cloud/Dockerfile" "$source_root"
image_id="$(/usr/bin/docker image inspect --format '{{.Id}}' orbbec-agent-platform:$release_sha)"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
/usr/bin/docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image_id"   | /usr/bin/grep -Fxq "PLATFORM_RELEASE_SHA=$release_sha"
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
release_manifest_sha="$(/usr/bin/sha256sum "$release_root/MANIFEST.sha256" | /usr/bin/awk '{print $1}')"
[[ "$release_manifest_sha" == "$expected_manifest_sha" ]]
finished_at="$(/usr/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
/usr/bin/python3 - "$metadata_root/result.json.part"   "$release_sha" "$deployment_id" "$expected_source_tree"   "$expected_archive_sha" "$expected_manifest_sha" "$image_id"   "$root_used_before" "$root_available_before" "$root_percent_before"   "$data_used_before" "$data_available_before" "$data_percent_before"   "$root_used_after" "$root_available_after" "$root_percent_after"   "$data_used_after" "$data_available_after" "$data_percent_after"   "$started_at" "$finished_at" <<'PY'
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
value = {
    "archive_sha256": archive_sha,
    "deployment_id": deployment,
    "disk_after": {
        "data_available_bytes": int(data_available_after),
        "data_used_bytes": int(data_used_after),
        "data_used_percent": int(data_percent_after),
        "root_available_bytes": int(root_available_after),
        "root_used_bytes": int(root_used_after),
        "root_used_percent": int(root_percent_after),
    },
    "disk_before": {
        "data_available_bytes": int(data_available_before),
        "data_used_bytes": int(data_used_before),
        "data_used_percent": int(data_percent_before),
        "root_available_bytes": int(root_available_before),
        "root_used_bytes": int(root_used_before),
        "root_used_percent": int(root_percent_before),
    },
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
}
path = Path(output)
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
path.chmod(0o600)
PY
/usr/bin/mv -f "$metadata_root/result.json.part" "$metadata_root/result.json"
/usr/bin/printf 'BUILD_COMPLETE release=%s image_id=%s services_changed=no
' "$release_sha" "$image_id"
