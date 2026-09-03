#!/bin/bash
set -euo pipefail
umask 077

root_path="/opt/orbbec-agent-platform"
private_path="$root_path/private"
releases_path="$root_path/releases"
data_path="/data/orbbec-agent-platform"
staging_path="/data/staging/orbbec-agent-platform"
staging_root="/data/staging/orbbec-agent-platform"
archive_releases="/data/archive/orbbec-agent-platform/releases"
release_metadata_root="$data_path/release-metadata"
environment_path="$private_path/platform.env"
rotation_lock="$private_path/execution-worker-key-rotation.lock"
rotation_state="$private_path/execution-worker-key-rotation-state.json"
deploy_state="$private_path/execution-worker-keyring-deploy-state.json"
deploy_state_part="$private_path/execution-worker-keyring-deploy-state.json.part"
worker_keyring="$private_path/execution-worker-public-keyring.json"
worker_keyring_part="$private_path/execution-worker-public-keyring.json.part"
worker_keyring_previous="$private_path/execution-worker-public-keyring.deploy.previous.json"
deploy_input_root="$private_path/deploy-input.lock"
deploy_input_state="$deploy_input_root/owner.json"

fail() {
  echo "CLOUD_PLATFORM_DEPLOY_FAILED" >&2
  exit 1
}

[[ $# -eq 3 ]] || fail
release_sha="$1"
expected_digest="$2"
deployment_id="$3"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ && "$expected_digest" =~ ^[0-9a-f]{64}$ && "$deployment_id" =~ ^[0-9a-f]{32}$ ]] || fail
release_path="$releases_path/$release_sha"
release_metadata_path="$release_metadata_root/$release_sha"
stage_path="$staging_root/$deployment_id"
archive_path="$stage_path/release.tar.gz"
postgres_data="$data_path/postgres"
backup_data="$data_path/backups"

cleanup_stage() {
  case "$stage_path" in
    /data/staging/orbbec-agent-platform/[0-9a-f][0-9a-f]*) ;;
    *) return 1 ;;
  esac
  if [[ -e "$stage_path" || -L "$stage_path" ]]; then
    [[ -d "$stage_path" && ! -L "$stage_path" ]] || return 1
    /usr/bin/find "$stage_path" -depth -delete
  fi
}
trap cleanup_stage EXIT

disk_before="$(/usr/bin/df -B1 / /data)" || fail
/usr/bin/printf '%s\n' "$disk_before" >&2
read -r root_size_before root_used_before root_available_before root_percent_before < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent / | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
read -r data_size_before data_used_before data_available_before data_percent_before < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent /data | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
[[ "$root_available_before" =~ ^[0-9]+$ && "$root_available_before" -ge 26843545600 ]] || fail
projected_root_available=$((root_available_before - 1073741824))
[[ "$projected_root_available" -ge 21474836480 ]] || fail
[[ "$root_percent_before" =~ ^[0-9]+$ && "$root_percent_before" -le 75 ]] || fail
[[ "$data_available_before" =~ ^[0-9]+$ && "$data_available_before" -ge 21474836480 ]] || fail
/usr/bin/printf 'PLATFORM_DISK_BEFORE root_used=%s root_available=%s root_percent=%s data_used=%s data_available=%s data_percent=%s\n' \
  "$root_used_before" "$root_available_before" "$root_percent_before" \
  "$data_used_before" "$data_available_before" "$data_percent_before" >&2

ensure_bind_volume() {
  local volume_name="$1" target="$2" configured_device source_mount
  case "$target" in "$data_path"/*) ;; *) fail ;; esac
  [[ -d "$target" && ! -L "$target" ]] || fail
  configured_device="$(/usr/bin/docker volume inspect --format '{{index .Options "device"}}' "$volume_name" 2>/dev/null || true)"
  if [[ "$configured_device" == "$target" ]]; then
    return
  fi
  [[ -z "$(/usr/bin/docker ps -aq --filter "volume=$volume_name")" ]] || fail
  if /usr/bin/docker volume inspect "$volume_name" >/dev/null 2>&1; then
    [[ -z "$configured_device" ]] || fail
    source_mount="$(/usr/bin/docker volume inspect --format '{{.Mountpoint}}' "$volume_name")"
    [[ "$source_mount" == /var/lib/docker/volumes/*/_data && -d "$source_mount" && ! -L "$source_mount" ]] || fail
    [[ -z "$(/usr/bin/find "$target" -mindepth 1 -print -quit)" ]] || fail
    /usr/bin/docker run --rm --network none \
      -v "$volume_name:/source:ro" -v "$target:/target" alpine:3.22 \
      sh -ceu 'cp -a /source/. /target/; sync'
    /usr/bin/docker volume rm "$volume_name" >/dev/null
  fi
  /usr/bin/docker volume create --driver local --opt type=none --opt o=bind \
    --opt "device=$target" "$volume_name" >/dev/null
  [[ "$(/usr/bin/docker volume inspect --format '{{index .Options "device"}}' "$volume_name")" == "$target" ]] || fail
}

retain_release_history() {
  # Release retention: current + two rollback. Older versions are moved to /data.
  # archive retention: ten releases or thirty days, whichever is stricter.
  local previous_sha previous_metadata second_previous second_sha candidate archive_target index mtime now metadata_sha
  previous_sha="$(/usr/bin/basename "$previous_release")"
  second_previous=""
  previous_metadata="$release_metadata_root/$previous_sha/PREVIOUS_RELEASE"
  if [[ -f "$previous_metadata" && ! -L "$previous_metadata" ]]; then
    second_previous="$(cat "$previous_metadata")"
  fi
  if [[ ! "$second_previous" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ || ! -d "$second_previous" || -L "$second_previous" ]]; then
    second_previous="$(/usr/bin/find "$releases_path" -mindepth 1 -maxdepth 1 -type d \
      ! -path "$release_path" ! -path "$previous_release" -printf '%T@ %p\n' | /usr/bin/sort -nr | /usr/bin/head -1 | /usr/bin/cut -d' ' -f2-)"
  fi
  [[ -n "$second_previous" ]] || fail
  second_sha="$(/usr/bin/basename "$second_previous")"
  [[ "$previous_sha" =~ ^[0-9a-f]{40}$ && "$second_sha" =~ ^[0-9a-f]{40}$ ]] || fail
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    case "$candidate" in "$release_path"|"$previous_release"|"$second_previous") continue ;; esac
    [[ "$candidate" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ && -d "$candidate" && ! -L "$candidate" ]] || fail
    archive_target="$archive_releases/$(/usr/bin/basename "$candidate")"
    [[ ! -e "$archive_target" && ! -L "$archive_target" ]] || fail
    /bin/mv "$candidate" "$archive_target"
  done < <(/usr/bin/find "$releases_path" -mindepth 1 -maxdepth 1 -type d -print)
  now="$(/usr/bin/date +%s)"
  index=0
  while read -r mtime candidate; do
    [[ -n "$candidate" ]] || continue
    index=$((index + 1))
    [[ "$candidate" =~ ^/data/archive/orbbec-agent-platform/releases/[0-9a-f]{40}$ && -d "$candidate" && ! -L "$candidate" ]] || fail
    if [[ "$index" -gt 10 || $((now - ${mtime%.*})) -gt 2592000 ]]; then
      /usr/bin/find "$candidate" -depth -delete
    fi
  done < <(/usr/bin/find "$archive_releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | /usr/bin/sort -nr)
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    metadata_sha="$(/usr/bin/basename "$candidate")"
    [[ "$metadata_sha" =~ ^[0-9a-f]{40}$ && -d "$candidate" && ! -L "$candidate" ]] || fail
    if [[ ! -d "$releases_path/$metadata_sha" && ! -d "$archive_releases/$metadata_sha" ]]; then
      /usr/bin/find "$candidate" -depth -delete
    fi
  done < <(/usr/bin/find "$release_metadata_root" -mindepth 1 -maxdepth 1 -type d -print)
  [[ "$(/usr/bin/find "$releases_path" -mindepth 1 -maxdepth 1 -type d | /usr/bin/wc -l)" -le 3 ]] || fail
  RETAINED_PREVIOUS_SHA="$previous_sha"
  RETAINED_SECOND_SHA="$second_sha"
}

retain_platform_images() {
  local previous_sha="$1" second_sha="$2" tag image_id referenced release_marker
  while read -r tag image_id; do
    [[ -n "$tag" && -n "$image_id" ]] || continue
    case "$tag" in "$release_sha"|"$previous_sha"|"$second_sha") continue ;; esac
    [[ "$tag" =~ ^[0-9a-f]{40}$ ]] || fail
    referenced="$(/usr/bin/docker ps -aq --filter "ancestor=$image_id")"
    [[ -z "$referenced" ]] || continue
    /usr/bin/docker image rm "orbbec-agent-platform:$tag" >/dev/null
  done < <(/usr/bin/docker image ls orbbec-agent-platform --no-trunc --format '{{.Tag}} {{.ID}}')
  while IFS= read -r image_id; do
    [[ -n "$image_id" ]] || continue
    release_marker="$(/usr/bin/docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image_id" 2>/dev/null \
      | /usr/bin/sed -n 's/^PLATFORM_RELEASE_SHA=//p' | /usr/bin/head -1)"
    [[ "$release_marker" =~ ^[0-9a-f]{40}$ ]] || continue
    case "$release_marker" in "$release_sha"|"$previous_sha"|"$second_sha") continue ;; esac
    [[ -z "$(/usr/bin/docker ps -aq --filter "ancestor=$image_id")" ]] || continue
    /usr/bin/docker image rm "$image_id" >/dev/null
  done < <(/usr/bin/docker image ls --filter dangling=true -q --no-trunc | /usr/bin/sort -u)
}

/usr/bin/python3 - "$deploy_input_root" "$deploy_input_state" "$release_sha" "$deployment_id" <<'PY' || fail
import json
import os
import pathlib
import stat
import sys

root, state = map(pathlib.Path, sys.argv[1:3])
release_sha, deployment_id = sys.argv[3:]
root_metadata = root.lstat()
state_descriptor = os.open(state, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    state_metadata = os.fstat(state_descriptor)
    raw = os.read(state_descriptor, 1025)
finally:
    os.close(state_descriptor)
expected = (json.dumps({"deployment_id": deployment_id, "release_sha": release_sha}, sort_keys=True, separators=(",", ":")) + "\n").encode()
if (
    not stat.S_ISDIR(root_metadata.st_mode)
    or root.is_symlink()
    or stat.S_IMODE(root_metadata.st_mode) != 0o700
    or root_metadata.st_uid != os.getuid()
    or not stat.S_ISREG(state_metadata.st_mode)
    or stat.S_IMODE(state_metadata.st_mode) != 0o600
    or state_metadata.st_uid != os.getuid()
    or state_metadata.st_size != len(raw)
    or raw != expected
):
    raise SystemExit(1)
PY
/usr/bin/install -d -m 700 "$private_path" "$releases_path" "$data_path" \
  "$staging_path" "$archive_releases" "$postgres_data" "$backup_data" \
  "$release_metadata_root" "$release_metadata_path" "$stage_path"
staged_worker_keyring="$stage_path/execution-worker-public-keyring.json"
[[ "${PLATFORM_EXECUTION_WORKER_DEPLOY_LOCK_FD:-}" =~ ^[0-9]+$ ]] || fail
/usr/bin/python3 - "$rotation_lock" "$PLATFORM_EXECUTION_WORKER_DEPLOY_LOCK_FD" <<'PY' || fail
import fcntl
import os
import stat
import sys

path, raw_descriptor = sys.argv[1:]
descriptor = int(raw_descriptor)
metadata = os.fstat(descriptor)
named = os.stat(path, follow_symlinks=False)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_uid != os.getuid()
    or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino)
):
    raise SystemExit(1)
fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
PY
[[ ! -e "$rotation_state" && ! -L "$rotation_state" ]] || fail
[[ -f "$staged_worker_keyring" && ! -L "$staged_worker_keyring" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$staged_worker_keyring")" == "600 root" ]] || fail
available_bytes="$root_available_before"

fae_container_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend 2>/dev/null || true)"
fae_image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' ai-fae-backend 2>/dev/null || true)"
fae_image_id="$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend 2>/dev/null || true)"
fae_started_at="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend 2>/dev/null || true)"
fae_restart_count="$(/usr/bin/docker inspect --format '{{.RestartCount}}' ai-fae-backend 2>/dev/null || true)"
fae_config_digest="$(/usr/bin/docker inspect --format '{{json .Config}}' ai-fae-backend 2>/dev/null | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_mounts_digest="$(/usr/bin/docker inspect --format '{{json .Mounts}}' ai-fae-backend 2>/dev/null \
  | /usr/bin/python3 -c 'import hashlib,json,sys; value=json.load(sys.stdin); value=sorted(value,key=lambda item:(item.get("Destination",""),item.get("Source",""),item.get("Type",""))); raw=json.dumps(value,sort_keys=True,separators=(",",":")).encode(); print(hashlib.sha256(raw).hexdigest())')"
fae_health_digest="$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend 2>/dev/null | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_ip_digest="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_domain_digest="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
nginx_digest="$(/usr/sbin/nginx -T 2>&1 | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
public_listener_digest="$(/usr/bin/ss -H -lnt | /usr/bin/awk '$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}' | /usr/bin/sort -u | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
[[ -n "$fae_container_id" && -n "$fae_image" && -n "$fae_image_id" && -n "$fae_started_at" && "$fae_restart_count" =~ ^[0-9]+$ ]] || fail

existing_api="$(/usr/bin/docker ps --filter label=com.docker.compose.project=orbbec-agent-platform --filter label=com.docker.compose.service=platform-api --format '{{.ID}}' | /usr/bin/head -1)"
control_secret_consumer_services=(
  platform-api
  platform-api-preview
  platform-directory
  platform-directory-preview
  platform-dingtalk-stream
  platform-dingtalk-stream-preview
  platform-brain
  platform-attachments
)
previous_control_consumers=()
previous_release=""
if [[ -L "$root_path/current" ]]; then
  previous_release="$(/usr/bin/readlink -f "$root_path/current" 2>/dev/null || true)"
  [[ -n "$previous_release" ]] || fail
  [[ -f "$previous_release/deploy/cloud/compose.yaml" ]] || fail
fi
previous_environment="$stage_path/previous.env"
cloud_auth_mode="ssh-tunnel"
if [[ -f "$environment_path" ]]; then
  /bin/cp -p "$environment_path" "$previous_environment"
  configured_auth_mode="$(/usr/bin/sed -n 's/^PLATFORM_CLOUD_AUTH_MODE=//p' "$environment_path")"
  if [[ -n "$configured_auth_mode" ]]; then
    cloud_auth_mode="$configured_auth_mode"
  fi
fi
[[ "$cloud_auth_mode" == "ssh-tunnel" || "$cloud_auth_mode" == "basic-auth" || "$cloud_auth_mode" == "dingtalk" ]] || fail
if [[ -n "$existing_api" && ( -z "$previous_release" || ! -f "$previous_environment" ) ]]; then
  fail
fi
port_8080_listeners="$(/usr/bin/ss -H -lnt | /usr/bin/awk '$4 ~ /:8080$/ {print $4}')"
if [[ -n "$port_8080_listeners" ]]; then
  [[ -n "$existing_api" ]] || fail
  [[ "$port_8080_listeners" == "127.0.0.1:8080" ]] || fail
fi
forbidden_bind_ipv4="0.0.0.0:8080"
forbidden_bind_ipv6="[::]:8080"

rollback_required=1
api_stopped=0
fsync_private() {
  /usr/bin/python3 - "$private_path" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}
fsync_file() {
  /usr/bin/python3 - "$1" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}
write_deploy_state() {
  phase="$1"
  /usr/bin/printf '{"next_sha256":"%s","phase":"%s","previous_sha256":"%s","release_sha":"%s","schema_version":1}\n' \
    "$next_keyring_sha" "$phase" "$previous_keyring_sha" "$release_sha" > "$deploy_state_part"
  /bin/chmod 600 "$deploy_state_part"
  fsync_file "$deploy_state_part"
  /bin/mv -f "$deploy_state_part" "$deploy_state"
  fsync_private
}
cleanup_worker_keyring_deploy() {
  /bin/rm -f -- "$deploy_state_part"
  fsync_private
  /bin/rm -f -- "$worker_keyring_previous"
  fsync_private
  /bin/rm -f -- "$staged_worker_keyring"
  fsync_private
  /bin/rm -f -- "$deploy_state"
  fsync_private
}
deploy_phase() {
  /usr/bin/python3 - "$deploy_state" "$release_sha" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
release_sha = sys.argv[2]
value = json.loads(path.read_bytes())
if (
    not isinstance(value, dict)
    or set(value) != {"schema_version", "phase", "release_sha", "previous_sha256", "next_sha256"}
    or value["schema_version"] != 1
    or value["phase"] not in {"keyring_switching", "keyring_switched", "completed"}
    or value["release_sha"] != release_sha
    or any(not isinstance(value[name], str) or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None for name in ("previous_sha256", "next_sha256"))
):
    raise SystemExit(1)
print(value["phase"])
PY
}
completed_deploy_identity() {
  /usr/bin/python3 - "$deploy_state" "$deploy_state_part" "$worker_keyring" \
    "$worker_keyring_previous" "$staged_worker_keyring" "$release_sha" <<'PY'
import base64
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

state_path, state_part, canonical, previous, staged = map(pathlib.Path, sys.argv[1:6])
release_sha = sys.argv[6]
state_raw = state_path.read_bytes()
value = json.loads(state_raw)
if value["phase"] != "completed" or value["release_sha"] != release_sha:
    raise SystemExit(1)
for path, digest in (
    (canonical, value["next_sha256"]),
    (previous, value["previous_sha256"]),
    (staged, value["next_sha256"]),
):
    if path == canonical or path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(1)
if state_part.exists() or state_part.is_symlink():
    metadata = state_part.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid() or state_part.read_bytes() != state_raw:
        raise SystemExit(1)
document = json.loads(canonical.read_bytes())
agents = ["hr-bot", "fae-bot", "marketing-prospecting-bot", "marketing-inbound-bot", "marketing-voice-bot", "marketing-intelligence-bot", "marketing-gtm-bot", "agent-brain-bot"]
if not isinstance(document, dict) or set(document) != {"worker_id", "key_id", "public_key_base64url", "allowed_agent_ids"} or document["worker_id"] != "agentops-mac-primary" or re.fullmatch(r"worker-v[1-9][0-9]*", document["key_id"]) is None or document["allowed_agent_ids"] != agents or re.fullmatch(r"[A-Za-z0-9_-]{43}", document["public_key_base64url"]) is None:
    raise SystemExit(1)
public = base64.b64decode(document["public_key_base64url"] + "=", altchars=b"-_", validate=True)
if len(public) != 32:
    raise SystemExit(1)
print(document["key_id"], hashlib.sha256(public).hexdigest())
PY
}
completed_worker_key_active() {
  key_id="$1"
  fingerprint="$2"
  current_compose="$root_path/current/deploy/cloud/compose.yaml"
  [[ -f "$current_compose" && ! -L "$current_compose" && -f "$environment_path" && ! -L "$environment_path" ]] || return 1
  container_id="$(/usr/bin/docker compose --env-file "$environment_path" -f "$current_compose" ps -q platform-api)" || return 1
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
  current_image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' "$container_id")" || return 1
  [[ -n "$current_image" && "$current_image" != *$'\n'* ]] || return 1
  result="$(/usr/bin/docker run --rm --pull=never --network orbbec-agent-platform-internal --user 0:0 \
    -v "$private_path:/run/control-secrets:ro" \
    -e PLATFORM_CONTROL_DATABASE_URL_FILE=/run/control-secrets/control-database-url \
    "$current_image" python -c 'import json,os,pathlib,psycopg,sys; worker_id,key_id=sys.argv[1:]; dsn=pathlib.Path(os.environ["PLATFORM_CONTROL_DATABASE_URL_FILE"]).read_text().strip(); connection=psycopg.connect(dsn); row=connection.execute("select status,encode(sha256(public_key),'"'"'hex'"'"') from platform_control.execution_worker_keys where worker_id=%s and key_id=%s",(worker_id,key_id)).fetchone(); connection.close(); print(json.dumps({"key_id":key_id,"status":"absent" if row is None else row[0],"public_key_sha256":None if row is None else row[1]},sort_keys=True,separators=(",",":")))' \
    agentops-mac-primary "$key_id")" || return 1
  /usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); expected_key,expected_fingerprint=sys.argv[1:]; raise SystemExit(0 if value=={"key_id":expected_key,"status":"active","public_key_sha256":expected_fingerprint} else 1)' \
    "$key_id" "$fingerprint" <<<"$result"
}
restore_worker_keyring() {
  if [[ -e "$deploy_state" || -L "$deploy_state" ]]; then
    [[ -f "$deploy_state" && ! -L "$deploy_state" ]] || return 1
    [[ "$(/usr/bin/stat -c '%a %U' "$deploy_state")" == "600 root" ]] || return 1
    phase="$(deploy_phase)" || return 1
    if [[ "$phase" == "completed" ]]; then
      read -r completed_key_id completed_fingerprint < <(completed_deploy_identity) || return 1
      completed_worker_key_active "$completed_key_id" "$completed_fingerprint" || return 1
      cleanup_worker_keyring_deploy || return 1
      return 0
    fi
    [[ -f "$worker_keyring_previous" && ! -L "$worker_keyring_previous" ]] || return 1
    [[ "$(/usr/bin/stat -c '%a %U' "$worker_keyring_previous")" == "600 root" ]] || return 1
    /usr/bin/python3 - "$deploy_state" "$worker_keyring_previous" "$staged_worker_keyring" "$release_sha" <<'PY' || return 1
import hashlib
import json
import pathlib
import re
import sys

state_path, previous_path, staged_path = map(pathlib.Path, sys.argv[1:4])
release_sha = sys.argv[4]
value = json.loads(state_path.read_bytes())
if (
    not isinstance(value, dict)
    or set(value) != {"schema_version", "phase", "release_sha", "previous_sha256", "next_sha256"}
    or value["schema_version"] != 1
    or value["phase"] not in {"keyring_switching", "keyring_switched"}
    or value["release_sha"] != release_sha
    or any(not isinstance(value[name], str) or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None for name in ("previous_sha256", "next_sha256"))
    or hashlib.sha256(previous_path.read_bytes()).hexdigest() != value["previous_sha256"]
    or hashlib.sha256(staged_path.read_bytes()).hexdigest() != value["next_sha256"]
):
    raise SystemExit(1)
PY
    /usr/bin/install -o root -g root -m 600 "$worker_keyring_previous" "$worker_keyring_part" || return 1
    fsync_file "$worker_keyring_part" || return 1
    /bin/mv -f "$worker_keyring_part" "$worker_keyring" || return 1
    fsync_private || return 1
    cleanup_worker_keyring_deploy || return 1
  elif [[ -e "$worker_keyring_previous" || -L "$worker_keyring_previous" ]]; then
    [[ -f "$worker_keyring_previous" && ! -L "$worker_keyring_previous" ]] || return 1
    /usr/bin/cmp -s "$worker_keyring_previous" "$worker_keyring" || return 1
    /bin/rm -f -- "$worker_keyring_previous" || return 1
    fsync_private || return 1
  fi
}
completed_deploy_recovered=0
if [[ -e "$deploy_state" && ! -L "$deploy_state" && "$(deploy_phase 2>/dev/null || true)" == "completed" ]]; then
  restore_worker_keyring || fail
  completed_deploy_recovered=1
fi
if [[ -e "$deploy_state" || -L "$deploy_state" || -e "$deploy_state_part" || -L "$deploy_state_part" || -e "$worker_keyring_previous" || -L "$worker_keyring_previous" ]]; then
  fail
fi
if [[ "$completed_deploy_recovered" == "1" ]]; then
  echo "CLOUD_PLATFORM_DEPLOY_RECOVERED release=$release_sha"
  exit 0
fi
[[ ! -e "$release_path" ]] || fail
/bin/dd of="$archive_path.part" status=none
actual_digest="$(/usr/bin/sha256sum "$archive_path.part" | /usr/bin/awk '{print $1}')"
[[ "$actual_digest" == "$expected_digest" ]] || fail
/bin/mv -f "$archive_path.part" "$archive_path"
archive_bytes="$(/usr/bin/stat -c '%s' "$archive_path")"
[[ "$archive_bytes" =~ ^[0-9]+$ ]] || fail
projected_root_bytes=$((archive_bytes * 8 + 5368709120))
[[ "$root_available_before" -ge $((projected_root_bytes + 21474836480)) ]] || fail
if /usr/bin/tar -tzf "$archive_path" | /usr/bin/grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  fail
fi
if /usr/bin/tar -tzf "$archive_path" | /usr/bin/grep -Eq '(^|/)(data|uploads|logs|index|answer_reviews|knowledge|\.venv|node_modules)(/|$)|\.(db|sqlite|sqlite3)$'; then
  fail
fi
/usr/bin/install -d -m 700 "$release_path"
rollback() {
  local exit_status=$?
  trap - EXIT
  if [[ "$rollback_required" -eq 1 ]]; then
    if ! restore_worker_keyring; then
      echo "EXECUTION_WORKER_KEYRING_DEPLOY_ROLLBACK_FAILED" >&2
      exit_status=1
    fi
    if [[ "$api_stopped" -eq 1 ]]; then
      if [[ -f "$release_path/deploy/cloud/compose.yaml" && -f "$environment_path" ]]; then
        candidate_services="$(/usr/bin/docker compose --env-file "$environment_path" \
          -f "$release_path/deploy/cloud/compose.yaml" config --services 2>/dev/null || true)"
        candidate_to_stop=()
        for service_name in platform-attachments platform-brain platform-loopback platform-api platform-directory platform-dingtalk-stream; do
          if /usr/bin/grep -Fxq "$service_name" <<<"$candidate_services"; then
            candidate_to_stop+=("$service_name")
          fi
        done
        if [[ "${#candidate_to_stop[@]}" -gt 0 ]]; then
          for service_name in "${candidate_to_stop[@]}"; do
            container_id="$(/usr/bin/docker compose --env-file "$environment_path" \
              -f "$release_path/deploy/cloud/compose.yaml" \
              ps -a -q "$service_name" 2>/dev/null || true)"
            if [[ -n "$container_id" ]]; then
              /usr/bin/docker rm -f "$container_id" >/dev/null 2>&1 || true
            fi
          done
        fi
      fi
      if [[ -n "$previous_release" && -f "$previous_environment" ]]; then
        /bin/cp -p "$previous_environment" "$environment_path"
        /bin/ln -sfn "$previous_release" "$root_path/current"
        if [[ "${#previous_control_consumers[@]}" -gt 0 ]]; then
          /usr/bin/docker compose --env-file "$environment_path" \
            -f "$previous_release/deploy/cloud/compose.yaml" \
            up -d --force-recreate "${previous_control_consumers[@]}" \
            >/dev/null 2>&1 || true
        fi
      else
        /usr/bin/docker rm -f orbbec-agent-platform-platform-loopback-1 >/dev/null 2>&1 || true
        /usr/bin/docker rm -f orbbec-agent-platform-platform-api-1 >/dev/null 2>&1 || true
        /usr/bin/systemctl disable --now orbbec-agent-platform-backup.timer >/dev/null 2>&1 || true
        if [[ -L "$root_path/current" ]]; then
          /usr/bin/unlink "$root_path/current" || true
        fi
      fi
    fi
    if [[ -d "$release_path" && "$release_path" != "$previous_release" ]]; then
      /bin/mv "$release_path" "$stage_path/failed-release-$BASHPID" >/dev/null 2>&1 || true
    fi
  fi
  cleanup_stage || exit_status=1
  exit "$exit_status"
}
trap rollback EXIT
/usr/bin/tar -xzf "$archive_path" -C "$release_path"
if /usr/bin/find "$release_path" -type l -print -quit | /usr/bin/grep -q .; then
  fail
fi
(cd "$release_path" && /usr/bin/sha256sum --check MANIFEST.sha256 >/dev/null) || fail
for bootstrap_helper in \
  "$release_path/deploy/cloud/bootstrap-control-db.sh" \
  "$release_path/deploy/cloud/bootstrap-dingtalk-production-secrets.sh"; do
  [[ -f "$bootstrap_helper" && ! -L "$bootstrap_helper" && -x "$bootstrap_helper" ]] || fail
done

signing_public="$private_path/replica-signing-public-key"
[[ -f "$signing_public" && ! -L "$signing_public" && "$(/usr/bin/stat -c '%a %U %s' "$signing_public")" == "600 root 32" ]] || fail

postgres_password="$private_path/postgres-owner-password"
attachment_s3_access_key="$private_path/attachment-s3-access-key"
attachment_s3_secret_key="$private_path/attachment-s3-secret-key"
read_password="$private_path/replica-read-password"
import_password="$private_path/replica-import-password"
encryption_key="$private_path/replica-encryption-key"
[[ -e "$postgres_password" ]] || /usr/bin/openssl rand -hex 32 > "$postgres_password"
[[ -e "$read_password" ]] || /usr/bin/openssl rand -hex 32 > "$read_password"
[[ -e "$import_password" ]] || /usr/bin/openssl rand -hex 32 > "$import_password"
[[ -e "$encryption_key" ]] || /usr/bin/openssl rand 32 > "$encryption_key"
[[ -e "$attachment_s3_access_key" ]] || /usr/bin/openssl rand -hex 16 > "$attachment_s3_access_key"
[[ -e "$attachment_s3_secret_key" ]] || /usr/bin/openssl rand -hex 32 > "$attachment_s3_secret_key"
for password_file in "$postgres_password" "$read_password" "$import_password"; do
  [[ "$(/usr/bin/tr -d '\n' < "$password_file")" =~ ^[0-9a-f]{64}$ ]] || fail
  /bin/chown root:root "$password_file"
  /bin/chmod 600 "$password_file"
done
for attachment_secret in "$attachment_s3_access_key" "$attachment_s3_secret_key"; do
  [[ "$(/usr/bin/tr -d '\n' < "$attachment_secret")" =~ ^[0-9a-f]{32,64}$ ]] || fail
  /bin/chown root:root "$attachment_secret"
  /bin/chmod 600 "$attachment_secret"
done
[[ "$(/usr/bin/stat -c '%s' "$encryption_key")" == "32" ]] || fail
/bin/chown root:root "$encryption_key"
/bin/chmod 600 "$encryption_key"

owner_password_value="$(/usr/bin/tr -d '\n' < "$postgres_password")"
read_password_value="$(/usr/bin/tr -d '\n' < "$read_password")"
import_password_value="$(/usr/bin/tr -d '\n' < "$import_password")"
owner_dsn="$private_path/replica-owner-database-url"
read_dsn="$private_path/replica-database-url"
import_dsn="$private_path/replica-import-database-url"
/usr/bin/printf 'postgresql://platform_owner:%s@platform-postgres:5432/agent_platform\n' "$owner_password_value" > "$owner_dsn"
/usr/bin/printf 'postgresql://platform_replica_reader:%s@platform-postgres:5432/agent_platform\n' "$read_password_value" > "$read_dsn"
/usr/bin/printf 'postgresql://platform_replica_importer:%s@platform-postgres:5432/agent_platform\n' "$import_password_value" > "$import_dsn"
/bin/chown root:root "$owner_dsn" "$read_dsn" "$import_dsn"
/bin/chmod 600 "$owner_dsn" "$read_dsn" "$import_dsn"

image_name="orbbec-agent-platform:$release_sha"
/usr/bin/docker build --pull --build-arg "RELEASE_SHA=$release_sha" -t "$image_name" -f "$release_path/deploy/cloud/Dockerfile" "$release_path" >/dev/null

for volume_name in \
  orbbec-agent-platform-postgres-secrets \
  orbbec-agent-platform-api-secrets \
  orbbec-agent-platform-migrate-secrets \
  orbbec-agent-platform-import-secrets \
  orbbec-agent-platform-brain-secrets \
  orbbec-agent-platform-attachment-storage-secrets \
  orbbec-agent-platform-attachment-worker-secrets; do
  /usr/bin/docker volume create "$volume_name" >/dev/null
done
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-attachment-storage-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/attachment-s3-access-key /source/attachment-s3-secret-key /target/; chown 0:0 /target/*; chmod 400 /target/attachment-s3-access-key /target/attachment-s3-secret-key'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-postgres-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/postgres-owner-password /target/postgres-owner-password; chown 999:999 /target/postgres-owner-password; chmod 400 /target/postgres-owner-password'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-api-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/replica-database-url /source/replica-encryption-key /source/replica-signing-public-key /target/; chown 10001:10001 /target/*; chmod 600 /target/replica-database-url; chmod 600 /target/replica-encryption-key; chmod 600 /target/replica-signing-public-key'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-migrate-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/replica-owner-database-url /target/replica-database-url; cp /source/replica-encryption-key /source/replica-signing-public-key /target/; chown 10001:10001 /target/*; chmod 600 /target/replica-database-url; chmod 600 /target/replica-encryption-key; chmod 600 /target/replica-signing-public-key'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-import-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/replica-import-database-url /target/replica-database-url; cp /source/replica-encryption-key /source/replica-signing-public-key /target/; chown 10001:10001 /target/*; chmod 600 /target/replica-database-url; chmod 600 /target/replica-encryption-key; chmod 600 /target/replica-signing-public-key'

if [[ -n "$previous_release" && -f "$environment_path" ]]; then
  previous_compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$previous_release/deploy/cloud/compose.yaml")
  previous_services="$("${previous_compose[@]}" config --services)"
  previous_control_consumers=()
  for service_name in "${control_secret_consumer_services[@]}" platform-loopback platform-loopback-preview platform-postgres; do
    if /usr/bin/grep -Fxq "$service_name" <<<"$previous_services"; then
      previous_control_consumers+=("$service_name")
    fi
  done
  if [[ "${#previous_control_consumers[@]}" -gt 0 ]]; then
    "${previous_compose[@]}" stop "${previous_control_consumers[@]}" >/dev/null
    api_stopped=1
  fi
  "${previous_compose[@]}" rm -f platform-postgres >/dev/null
fi
ensure_bind_volume orbbec-agent-platform-postgres-data "$postgres_data"
ensure_bind_volume orbbec-agent-platform-backups "$backup_data"
read_previous_feature() {
  local name="$1"
  local fallback="$2"
  local value=""
  if [[ -f "$environment_path" ]]; then
    value="$(/usr/bin/grep -m1 -E "^${name}=(0|1)$" "$environment_path" | /usr/bin/cut -d= -f2- || true)"
  fi
  [[ "$value" == "0" || "$value" == "1" ]] || value="$fallback"
  /usr/bin/printf '%s' "$value"
}
if [[ -z "${PLATFORM_AGENT_BRAIN_ENABLED+x}" ]]; then
  PLATFORM_AGENT_BRAIN_ENABLED="$(read_previous_feature PLATFORM_AGENT_BRAIN_ENABLED 0)"
fi
if [[ -z "${PLATFORM_AGENT_BRAIN_V2_ENABLED+x}" ]]; then
  PLATFORM_AGENT_BRAIN_V2_ENABLED="$(read_previous_feature PLATFORM_AGENT_BRAIN_V2_ENABLED 0)"
fi
PLATFORM_AGENT_BRAIN_ENABLED="${PLATFORM_AGENT_BRAIN_ENABLED:-0}"
PLATFORM_AGENT_BRAIN_V2_ENABLED="${PLATFORM_AGENT_BRAIN_V2_ENABLED:-0}"
PLATFORM_DIRECT_AGENT_ENABLED="${PLATFORM_DIRECT_AGENT_ENABLED:-1}"
[[ "$PLATFORM_AGENT_BRAIN_ENABLED" == "0" || "$PLATFORM_AGENT_BRAIN_ENABLED" == "1" ]] || fail
[[ "$PLATFORM_AGENT_BRAIN_V2_ENABLED" == "0" || "$PLATFORM_AGENT_BRAIN_V2_ENABLED" == "1" ]] || fail
[[ "$PLATFORM_DIRECT_AGENT_ENABLED" == "1" ]] || fail
/usr/bin/printf 'PLATFORM_IMAGE=%s\nPLATFORM_CLOUD_AUTH_MODE=dingtalk\nPLATFORM_DIRECT_AGENT_ENABLED=%s\nPLATFORM_AGENT_BRAIN_ENABLED=%s\nPLATFORM_AGENT_BRAIN_V2_ENABLED=%s\n' \
  "$image_name" "$PLATFORM_DIRECT_AGENT_ENABLED" "$PLATFORM_AGENT_BRAIN_ENABLED" "$PLATFORM_AGENT_BRAIN_V2_ENABLED" > "$environment_path"
/bin/chown root:root "$environment_path"
/bin/chmod 600 "$environment_path"
export PLATFORM_DIRECT_AGENT_ENABLED PLATFORM_AGENT_BRAIN_ENABLED PLATFORM_AGENT_BRAIN_V2_ENABLED
unset PLATFORM_CLOUD_AUTH_MODE
compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$release_path/deploy/cloud/compose.yaml")

postgres_data_path=/data/orbbec-agent-platform/postgres
/usr/bin/install -d -m 700 /data/orbbec-agent-platform "$postgres_data_path" \
  /data/orbbec-agent-platform/attachments /data/orbbec-agent-platform/clamav
/bin/chown 999:999 "$postgres_data_path"
legacy_postgres_volume=orbbec-agent-platform-postgres-data
if [[ ! -e "$postgres_data_path/PG_VERSION" &&
      -n "$(/usr/bin/docker volume ls -q --filter name="^${legacy_postgres_volume}$")" ]]; then
  legacy_mount="$(/usr/bin/docker volume inspect --format '{{.Mountpoint}}' "$legacy_postgres_volume")" || fail
  [[ "$legacy_mount" == /var/lib/docker/volumes/*/_data && -f "$legacy_mount/PG_VERSION" ]] || fail
  legacy_postgres_id="$(/usr/bin/docker ps --filter label=com.docker.compose.project=orbbec-agent-platform --filter label=com.docker.compose.service=platform-postgres --format '{{.ID}}' | /usr/bin/head -1)"
  if [[ -n "$legacy_postgres_id" ]]; then
    /usr/bin/docker stop "$legacy_postgres_id" >/dev/null || fail
  fi
  [[ -z "$(/usr/bin/find "$postgres_data_path" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail
  /usr/bin/docker run --rm --network none \
    -v "$legacy_postgres_volume:/source:ro" \
    -v "$postgres_data_path:/target" alpine:3.22 \
    sh -ceu 'cp -a /source/. /target/; sync; test -f /target/PG_VERSION' || fail
  source_file_facts="$(/usr/bin/find "$legacy_mount" -type f -printf '%s\n' | /usr/bin/awk '{count+=1; bytes+=$1} END {print count,bytes}')"
  target_file_facts="$(/usr/bin/find "$postgres_data_path" -type f -printf '%s\n' | /usr/bin/awk '{count+=1; bytes+=$1} END {print count,bytes}')"
  [[ "$source_file_facts" == "$target_file_facts" ]] || fail
fi
[[ -f "$postgres_data_path/PG_VERSION" || -z "$(/usr/bin/find "$postgres_data_path" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail
"${compose[@]}" up -d --force-recreate platform-postgres >/dev/null
for _attempt in $(/usr/bin/seq 1 40); do
  postgres_id="$("${compose[@]}" ps -q platform-postgres)"
  [[ -n "$postgres_id" ]] || { /bin/sleep 1; continue; }
  [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$postgres_id")" == "healthy" ]] && break
  /bin/sleep 1
done
[[ "${postgres_id:-}" != "" && "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$postgres_id")" == "healthy" ]] || fail

/usr/bin/docker run --rm --network orbbec-agent-platform-internal \
  -v orbbec-agent-platform-migrate-secrets:/run/secrets:ro \
  -e PLATFORM_REPLICA_DATABASE_URL_FILE=/run/secrets/replica-database-url \
  -e PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/secrets/replica-encryption-key \
  "$image_name" python -m app.cloud_replica.cli migrate >/dev/null

postgres_container="$("${compose[@]}" ps -q platform-postgres)"
control_bootstrap_result="$("$release_path/deploy/cloud/bootstrap-control-db.sh" \
  "$release_path" "$private_path" "$image_name" "$postgres_container")" || fail
[[ "$control_bootstrap_result" == "CONTROL_DATABASE_CREDENTIALS_READY version=2" ]] || fail
conversation_backfill_result="$(/usr/bin/docker run --rm --user 0:0 --read-only \
  --security-opt no-new-privileges:true \
  --network orbbec-agent-platform-internal \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  -v "$private_path:/run/control-secrets:ro" \
  -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url \
  -e PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE=/run/control-secrets/content-encryption-keyring \
  "$image_name" python -m app.agent_brain.conversation_backfill)" || fail
[[ "$conversation_backfill_result" =~ ^AGENT_BRAIN_CONVERSATION_BACKFILL_OK\ scanned=[0-9]+\ created=[0-9]+\ quarantined=0$ ]] || fail
worker_bootstrap_result="$(/usr/bin/docker run --rm --user 0:0 --read-only \
  --security-opt no-new-privileges:true \
  --network orbbec-agent-platform-internal \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  -v "$private_path:/run/control-secrets:ro" \
  -v "$stage_path:/run/bootstrap:ro" \
  -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url \
  -e PLATFORM_AGENT_BRAIN_ENABLED=0 \
  "$image_name" python -m app.execution_relay.bootstrap_registration \
  /run/bootstrap/execution-worker-public-keyring.json)" || fail
[[ "$worker_bootstrap_result" =~ ^EXECUTION_WORKER_BOOTSTRAP_OK\ status=(registered|existing)\ fingerprint=[0-9a-f]{64}$ ]] || fail
/usr/bin/test ! -e "$worker_keyring_previous" || fail
for brain_secret in brain-worker-database-url content-encryption-keyring brain-provider-api-key voc-extension-signing-key; do
  [[ -f "$private_path/$brain_secret" && ! -L "$private_path/$brain_secret" ]] || fail
  [[ "$(/usr/bin/stat -c '%a %U' "$private_path/$brain_secret")" == "600 root" ]] || fail
done
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-brain-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/brain-worker-database-url /source/content-encryption-keyring /source/brain-provider-api-key /source/voc-extension-signing-key /target/; chown 10001:10001 /target/*; chmod 600 /target/brain-worker-database-url /target/content-encryption-keyring /target/brain-provider-api-key /target/voc-extension-signing-key'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-attachment-worker-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/brain-worker-database-url /source/control-maintenance-database-url /source/content-encryption-keyring /source/attachment-s3-access-key /source/attachment-s3-secret-key /target/; chown 10001:10001 /target/*; chmod 600 /target/*'
if [[ -e "$worker_keyring" || -L "$worker_keyring" ]]; then
  /usr/bin/install -o root -g root -m 600 "$worker_keyring" "$worker_keyring_previous"
else
  /usr/bin/install -o root -g root -m 600 "$staged_worker_keyring" "$worker_keyring_previous"
fi
fsync_file "$worker_keyring_previous"
fsync_private
previous_keyring_sha="$(/usr/bin/sha256sum "$worker_keyring_previous" | /usr/bin/awk '{print $1}')"
next_keyring_sha="$(/usr/bin/sha256sum "$staged_worker_keyring" | /usr/bin/awk '{print $1}')"
write_deploy_state keyring_switching
/usr/bin/install -o root -g root -m 600 "$staged_worker_keyring" "$worker_keyring_part"
fsync_file "$worker_keyring_part"
/bin/mv -f "$worker_keyring_part" "$worker_keyring"
fsync_private
write_deploy_state keyring_switched
identity_bootstrap_result="$("$release_path/deploy/cloud/bootstrap-dingtalk-production-secrets.sh" \
  "$private_path")" || fail
[[ "$identity_bootstrap_result" == "DINGTALK_PRODUCTION_SECRETS_OK" ]] || fail
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-api-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/attachment-s3-access-key /source/attachment-s3-secret-key /target/; chown 10001:10001 /target/attachment-s3-access-key /target/attachment-s3-secret-key; chmod 600 /target/attachment-s3-access-key /target/attachment-s3-secret-key'
for protected_secret in \
  control-database-url \
  control-audit-database-url \
  content-encryption-keyring \
  execution-worker-public-keyring.json \
  voc-extension-signing-key \
  voc-service-bearer; do
  [[ "$(/usr/bin/stat -c '%a %U' "$private_path/$protected_secret")" == "600 root" ]] || fail
done
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-api-secrets:/target:ro alpine:3.22 \
  sh -ceu 'for name in control-database-url control-audit-database-url content-encryption-keyring execution-worker-public-keyring.json voc-extension-signing-key voc-service-bearer; do test "$(stat -c "%a %u" "/target/$name")" = "600 10001"; done' || fail
identity_policy_result="$(/usr/bin/docker run --rm --user 0:0 --read-only \
  --security-opt no-new-privileges:true \
  --network orbbec-agent-platform-internal \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  -v "$private_path:/run/control-secrets:ro" \
  "$image_name" python -m app.control_plane.maintenance_cli \
  --database-url-file /run/control-secrets/control-maintenance-database-url \
  sync-identity-policy \
  --keyring-file /run/control-secrets/identity-hmac-keyring)" || fail
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value=={"provider":"dingtalk","status":"ok","transition_versions":[1]}' \
  <<<"$identity_policy_result" || fail
/usr/bin/docker exec -i "$postgres_container" psql -v ON_ERROR_STOP=1 -U platform_owner -d agent_platform >/dev/null <<SQL
do \$\$
begin
  if not exists (select 1 from pg_roles where rolname='platform_replica_reader') then
    create role platform_replica_reader login password '$read_password_value';
  else
    alter role platform_replica_reader password '$read_password_value';
  end if;
  if not exists (select 1 from pg_roles where rolname='platform_replica_importer') then
    create role platform_replica_importer login password '$import_password_value';
  else
    alter role platform_replica_importer password '$import_password_value';
  end if;
end
\$\$;
grant platform_replica_read to platform_replica_reader;
grant platform_replica_import to platform_replica_importer;
SQL

available_release_services="$("${compose[@]}" config --services)"
active_control_secret_consumers=()
for service_name in "${control_secret_consumer_services[@]}"; do
  if /usr/bin/grep -Fxq "$service_name" <<<"$available_release_services"; then
    active_control_secret_consumers+=("$service_name")
  fi
done
if [[ "${#active_control_secret_consumers[@]}" -gt 0 ]]; then
  api_stopped=1
  "${compose[@]}" up -d --force-recreate "${active_control_secret_consumers[@]}" >/dev/null
fi
active_loopback_services=()
for service_name in platform-loopback platform-loopback-preview; do
  if /usr/bin/grep -Fxq "$service_name" <<<"$available_release_services"; then
    active_loopback_services+=("$service_name")
  fi
done
if [[ "${#active_loopback_services[@]}" -gt 0 ]]; then
  "${compose[@]}" up -d --force-recreate "${active_loopback_services[@]}" >/dev/null
fi
loopback_health_streak=0
for _attempt in $(/usr/bin/seq 1 40); do
  if /usr/bin/curl --silent --show-error --fail --max-time 2 http://127.0.0.1:8080/api/health >/dev/null; then
    loopback_health_streak=$((loopback_health_streak + 1))
    if [[ "$loopback_health_streak" -ge 3 ]]; then
      break
    fi
  else
    loopback_health_streak=0
  fi
  /bin/sleep 1
done
[[ "$loopback_health_streak" -ge 3 ]] || fail
api_container="$("${compose[@]}" ps -q platform-api)"
[[ -n "$api_container" ]] || fail
api_environment="$(/usr/bin/docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$api_container")"
for required_runtime_value in \
  PLATFORM_DEPLOYMENT_MODE=cloud-replica \
  PLATFORM_CLOUD_AUTH_MODE=dingtalk \
  PLATFORM_IDENTITY_MODE=production \
  PLATFORM_EXECUTION_RELAY_ENABLED=1 \
  "PLATFORM_DIRECT_AGENT_ENABLED=$PLATFORM_DIRECT_AGENT_ENABLED" \
  "PLATFORM_AGENT_BRAIN_ENABLED=$PLATFORM_AGENT_BRAIN_ENABLED"; do
  /usr/bin/grep -Fxq "$required_runtime_value" <<<"$api_environment" || fail
done
/usr/bin/grep -Fxq "PLATFORM_AGENT_BRAIN_V2_ENABLED=$PLATFORM_AGENT_BRAIN_V2_ENABLED" <<<"$api_environment" || fail
brain_container="$("${compose[@]}" ps -q platform-brain)"
[[ -n "$brain_container" ]] || fail
[[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$brain_container")" == "healthy" ]] || fail
if [[ -n "$previous_release" ]]; then
  /usr/bin/printf '%s\n' "$previous_release" > "$release_metadata_path/PREVIOUS_RELEASE"
  /bin/chown root:root "$release_metadata_path/PREVIOUS_RELEASE"
  /bin/chmod 600 "$release_metadata_path/PREVIOUS_RELEASE"
fi
if [[ -f "$previous_environment" ]]; then
  /bin/cp -p "$previous_environment" "$release_metadata_path/PREVIOUS_PLATFORM_ENV"
  /bin/chown root:root "$release_metadata_path/PREVIOUS_PLATFORM_ENV"
  /bin/chmod 600 "$release_metadata_path/PREVIOUS_PLATFORM_ENV"
fi
/bin/ln -sfn "$release_path" "$root_path/current"
/usr/bin/install -o root -g root -m 644 \
  "$release_path/deploy/cloud/orbbec-agent-platform-backup.service" \
  /etc/systemd/system/orbbec-agent-platform-backup.service
/usr/bin/install -o root -g root -m 644 \
  "$release_path/deploy/cloud/orbbec-agent-platform-backup.timer" \
  /etc/systemd/system/orbbec-agent-platform-backup.timer
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl enable --now orbbec-agent-platform-backup.timer >/dev/null

[[ "$fae_container_id" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$fae_image" == "$(/usr/bin/docker inspect --format '{{.Config.Image}}' ai-fae-backend)" ]] || fail
[[ "$fae_image_id" == "$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend)" ]] || fail
[[ "$fae_started_at" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
[[ "$fae_restart_count" == "$(/usr/bin/docker inspect --format '{{.RestartCount}}' ai-fae-backend)" ]] || fail
[[ "$fae_config_digest" == "$(/usr/bin/docker inspect --format '{{json .Config}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_mounts_digest" == "$(/usr/bin/docker inspect --format '{{json .Mounts}}' ai-fae-backend \
  | /usr/bin/python3 -c 'import hashlib,json,sys; value=json.load(sys.stdin); value=sorted(value,key=lambda item:(item.get("Destination",""),item.get("Source",""),item.get("Type",""))); raw=json.dumps(value,sort_keys=True,separators=(",",":")).encode(); print(hashlib.sha256(raw).hexdigest())')" ]] || fail
[[ "$fae_health_digest" == "$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_ip_digest" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_domain_digest" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$nginx_digest" == "$(/usr/sbin/nginx -T 2>&1 | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$public_listener_digest" == "$(/usr/bin/ss -H -lnt | /usr/bin/awk '$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}' | /usr/bin/sort -u | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fq '127.0.0.1:8080' || fail
if /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq "^(${forbidden_bind_ipv4}|\\[::\\]:8080)$"; then
  fail
fi

write_deploy_state completed
restore_worker_keyring
rollback_required=0
retain_release_history
retain_platform_images "$RETAINED_PREVIOUS_SHA" "$RETAINED_SECOND_SHA"
disk_after="$(/usr/bin/df -B1 / /data)" || fail
/usr/bin/printf '%s\n' "$disk_after" >&2
read -r root_size_after root_used_after root_available_after root_used_percent < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent / | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
read -r data_size_after data_used_after data_available_after data_used_percent < <(
  /usr/bin/df -B1 --output=size,used,avail,pcent /data | /usr/bin/tail -1 | /usr/bin/tr -d '%'
)
[[ "$root_available_after" =~ ^[0-9]+$ && "$root_available_after" -ge 21474836480 ]] || fail
[[ "$root_used_percent" =~ ^[0-9]+$ && "$root_used_percent" -le 75 ]] || fail
root_growth_bytes=$((root_used_after - root_used_before))
if [[ "$root_growth_bytes" -gt 1073741824 ]]; then
  /usr/bin/printf 'PLATFORM_DISK_GROWTH_EXPLANATION bytes=%s cause=platform_release_and_docker_image\n' "$root_growth_bytes" >&2
fi
/usr/bin/printf 'PLATFORM_DISK_AFTER root_used=%s root_available=%s root_percent=%s data_used=%s data_available=%s data_percent=%s root_growth=%s rollback_1=%s rollback_2=%s\n' \
  "$root_used_after" "$root_available_after" "$root_used_percent" \
  "$data_used_after" "$data_available_after" "$data_used_percent" "$root_growth_bytes" \
  "$RETAINED_PREVIOUS_SHA" "$RETAINED_SECOND_SHA" >&2
cleanup_stage || fail
[[ ! -e "$stage_path" && ! -L "$stage_path" ]] || fail
/usr/bin/printf 'staging_cleared=%s current_version=%s rollback_1=%s rollback_2=%s shared_nginx_modified=no other_app_modified=no\n' \
  "$stage_path" "$release_sha" "$RETAINED_PREVIOUS_SHA" "$RETAINED_SECOND_SHA" >&2
trap - EXIT
echo "CLOUD_PLATFORM_DEPLOY_OK release=$release_sha mode=dingtalk"
