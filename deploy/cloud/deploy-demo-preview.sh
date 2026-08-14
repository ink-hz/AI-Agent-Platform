#!/bin/bash
set -euo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' 'DEMO_PREVIEW_DEPLOY_FAILED' >&2
  exit 1
}

[[ $# -eq 2 ]] || fail
release_sha="$1"
archive_sha256="$2"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] || fail

target_host=root@47.106.112.69
ssh_key=/Users/neo/.ssh/orbbec_aliyun_ed25519
expected_live_sha256=382d733e1a581569f4ceedd03ce24ab9113f61a595015bc0449e1319026c1e97
repository_root="$(cd "$(/usr/bin/dirname "$0")/../.." && /bin/pwd -P)"
temporary_root=""
archive_path=""
ssh_options=(-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=8 -i "$ssh_key")

cleanup_local() {
  [[ -z "$temporary_root" ]] || /bin/rm -rf -- "$temporary_root"
}
trap cleanup_local EXIT
trap 'exit 1' HUP INT TERM

prepare_release() {
  [[ -f "$ssh_key" && ! -L "$ssh_key" ]] || fail
  [[ -z "$(/usr/bin/git -C "$repository_root" status --porcelain=v1 --untracked-files=all)" ]] || fail
  [[ "$(/usr/bin/git -C "$repository_root" rev-parse HEAD)" == "$release_sha" ]] || fail
  /usr/bin/git -C "$repository_root" cat-file -e "$release_sha^{commit}" || fail

  temporary_root="$(/usr/bin/mktemp -d /tmp/orbbec-demo-preview.XXXXXX)"
  archive_path="$temporary_root/release.tar"
  # The immutable source operation is deliberately equivalent to
  # `git archive --format=tar <release_sha>`; the absolute binary avoids PATH
  # substitution on the release workstation.
  /usr/bin/git -C "$repository_root" archive --format=tar "$release_sha" > "$archive_path"
  [[ "$(/usr/bin/shasum -a 256 "$archive_path" | /usr/bin/awk '{print $1}')" == "$archive_sha256" ]] || fail
  /usr/bin/tar -tf "$archive_path" > "$temporary_root/archive-files"
  [[ -s "$temporary_root/archive-files" ]] || fail
  /usr/bin/printf 'release_sha=%s\narchive_sha256=%s\n' \
    "$release_sha" "$archive_sha256" > "$temporary_root/release-manifest"
  /usr/bin/shasum -a 256 "$archive_path" > "$temporary_root/archive.sha256"
  /usr/bin/shasum -a 256 "$temporary_root/release-manifest" > "$temporary_root/manifest.sha256"
}

remote_program() {
  /usr/bin/sed -n '/^__DEMO_PREVIEW_REMOTE__$/,$p' "$0" | /usr/bin/tail -n +2
}

remote_execute() {
  local phase="$1" result
  result="$(remote_program | /usr/bin/ssh "${ssh_options[@]}" "$target_host" \
    /bin/bash -s -- "$phase" "$release_sha" "$archive_sha256" \
    "$expected_live_sha256" 2>/dev/null)" || fail
  case "$phase:$result" in
    verify:DEMO_PREVIEW_VERIFY_OK|activate:DEMO_PREVIEW_ACTIVATE_OK) ;;
    *) fail ;;
  esac
}

verify_release() {
  local incoming="/opt/orbbec-agent-platform/incoming/demo-$release_sha.tar"
  /usr/bin/ssh "${ssh_options[@]}" "$target_host" \
    /usr/bin/install -d -o root -g root -m 700 \
    /opt/orbbec-agent-platform/incoming >/dev/null 2>&1 || fail
  /usr/bin/scp "${ssh_options[@]}" "$archive_path" \
    "$target_host:$incoming.part" >/dev/null 2>&1 || fail
  /usr/bin/ssh "${ssh_options[@]}" "$target_host" /bin/bash -s -- \
    "$incoming.part" "$incoming" "$archive_sha256" >/dev/null 2>&1 <<'MOVE'
set -euo pipefail
part="$1"
target="$2"
digest="$3"
[[ "$part" == "$target.part" && "$target" =~ ^/opt/orbbec-agent-platform/incoming/demo-[0-9a-f]{40}\.tar$ ]]
[[ -f "$part" && ! -L "$part" ]]
[[ "$(/usr/bin/sha256sum "$part" | /usr/bin/awk '{print $1}')" == "$digest" ]]
/bin/chown root:root "$part"
/bin/chmod 600 "$part"
/bin/mv -f -- "$part" "$target"
MOVE
  remote_execute verify
}

activate_release() {
  remote_execute activate
}

prepare_release
verify_release
activate_release
trap - HUP INT TERM
trap - EXIT
cleanup_local
/usr/bin/printf '%s\n' 'DEMO_PREVIEW_DEPLOY_OK'
exit 0

__DEMO_PREVIEW_REMOTE__
set -euo pipefail
umask 077

remote_fail() {
  exit 1
}

[[ "$EUID" -eq 0 && $# -eq 4 ]] || remote_fail
phase="$1"
release_sha="$2"
archive_sha256="$3"
expected_live_sha256="$4"
[[ "$phase" == verify || "$phase" == activate ]] || remote_fail
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || remote_fail
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] || remote_fail
[[ "$expected_live_sha256" =~ ^[0-9a-f]{64}$ ]] || remote_fail

platform_root=/opt/orbbec-agent-platform
incoming="$platform_root/incoming/demo-$release_sha.tar"
release_path="$platform_root/releases/$release_sha"
private_path=/opt/orbbec-agent-platform/private/demo-preview
state_path=/opt/orbbec-agent-platform/private/.demo-preview-prerequisite-state
platform_environment="$platform_root/private/platform.env"
state_dir=/var/lib/orbbec-agent-demo-preview
verified_state="$state_dir/verified-release"
baseline_dir="$state_dir/release-baseline"
image_ref="orbbec-agent-platform-demo-preview:$release_sha"
base_compose="$release_path/deploy/cloud/compose.yaml"
preview_base_compose="$release_path/deploy/cloud/compose.demo-preview-base.yaml"
preview_compose="$release_path/deploy/cloud/compose.demo-preview.yaml"
production_compose=(/usr/bin/docker compose --env-file "$platform_environment" \
  -f "$base_compose")
preview_stack=(/usr/bin/docker compose --env-file "$platform_environment" \
  -f "$preview_base_compose" -f "$preview_compose")
postgres_container=""
postgres_address=""
expected_files=(
  dingtalk-app-key
  dingtalk-agent-id
  dingtalk-corp-id
  dingtalk-app-secret
  preview-control-database-url
  preview-control-audit-database-url
  preview-control-directory-worker-database-url
  preview-control-migrator-database-url
  preview-identity-hmac-keyring
  preview-identity-encryption-keyring
  preview-rate-limit-hmac-keyring
  demo-userids
)
[[ "${#expected_files[@]}" -eq 12 ]] || remote_fail
expected_secret_result='DEMO_PREVIEW_SECRETS_READY files=12'
preview_database=agent_platform_control_preview
preview_migrator_role=platform_control_migrator_preview
preview_directory_role=platform_directory_worker_preview
locked_nginx_sha256=382d733e1a581569f4ceedd03ce24ab9113f61a595015bc0449e1319026c1e97
[[ "$expected_live_sha256" == "$locked_nginx_sha256" ]] || remote_fail

/usr/bin/install -d -o root -g root -m 700 "$state_dir"
exec 9> /run/lock/orbbec-demo-preview.lock
/usr/bin/flock -n 9 || remote_fail

protected_container_invariants() {
  local name
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    case "$name" in
      *platform-api-demo-preview*|*platform-loopback-demo-preview*) continue ;;
    esac
    /usr/bin/docker inspect --format \
      '{{.Name}}|{{.Id}}|{{.Config.Image}}|{{.State.StartedAt}}|{{.RestartCount}}' \
      "$name"
  done < <(/usr/bin/docker ps --format '{{.Names}}' | /usr/bin/sort)
}

public_listener_invariants() {
  /usr/bin/ss -H -lnt | /usr/bin/awk \
    '$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}' | /usr/bin/sort -u
}

preview_listener_set() {
  /usr/bin/ss -H -lnt | /usr/bin/awk '$4 ~ /:8081$/ {print $4}' | /usr/bin/sort
}

response_code() {
  local url="$1" resolve_value="${2:-}" command
  command=(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 8)
  [[ -z "$resolve_value" ]] || command+=(--resolve "$resolve_value")
  "${command[@]}" "$url"
}

capture_responses() {
  /usr/bin/printf 'agent_root=%s\n' "$(response_code https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1)"
  /usr/bin/printf 'agent_admin=%s\n' "$(response_code https://agent.orbbec.com.cn/admin/ agent.orbbec.com.cn:443:127.0.0.1)"
  /usr/bin/printf 'fae_domain=%s\n' "$(response_code https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1)"
  /usr/bin/printf 'fae_ip=%s\n' "$(response_code http://47.106.112.69/)"
}

compose_preview() {
  resolve_postgres_endpoint || return 1
  PLATFORM_IMAGE="$image_ref" \
    PLATFORM_POSTGRES_PREVIEW_ADDRESS="$postgres_address" \
    "${preview_stack[@]}" "$@"
}

resolve_postgres_endpoint() {
  local candidate address
  if [[ -n "$postgres_container" && -n "$postgres_address" ]]; then
    return 0
  fi
  candidate="$("${production_compose[@]}" ps -q platform-postgres)" || return 1
  [[ "$candidate" =~ ^[0-9a-f]{12,64}$ ]] || return 1
  [[ "$(/usr/bin/docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' \
    "$candidate" 2>/dev/null)" == platform-postgres ]] || return 1
  [[ "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$candidate")" == true ]] || return 1
  address="$(/usr/bin/docker inspect --format \
    '{{with index .NetworkSettings.Networks "orbbec-agent-platform-internal"}}{{.IPAddress}}{{end}}' \
    "$candidate")" || return 1
  /usr/bin/python3 - "$address" <<'PY' || return 1
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
network = ipaddress.ip_network("172.30.0.0/28")
if address not in network or address in {
    network.network_address,
    network.broadcast_address,
    ipaddress.ip_address("172.30.0.5"),
    ipaddress.ip_address("172.30.0.6"),
}:
    raise SystemExit(1)
PY
  postgres_container="$candidate"
  postgres_address="$address"
}

stop_preview_services() {
  if [[ -f "$base_compose" && -f "$preview_base_compose" && \
        -f "$preview_compose" && -f "$platform_environment" ]]; then
    compose_preview stop platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1 || true
    compose_preview rm -f platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1 || true
  fi
}

stop_preview_services_strict() {
  [[ -f "$base_compose" && ! -L "$base_compose" ]] || return 1
  [[ -f "$preview_base_compose" && ! -L "$preview_base_compose" ]] || return 1
  [[ -f "$preview_compose" && ! -L "$preview_compose" ]] || return 1
  [[ -f "$platform_environment" && ! -L "$platform_environment" ]] || return 1
  compose_preview stop platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1
  compose_preview rm -f platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1
}

resolve_current_release() {
  [[ -L "$platform_root/current" ]] || return 1
  local target
  target="$(/usr/bin/readlink -f "$platform_root/current")" || return 1
  [[ "$target" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ ]] || return 1
  [[ -d "$target" && ! -L "$target" ]] || return 1
  /usr/bin/printf '%s\n' "$target"
}

validate_secret_prerequisites() {
  [[ -d "$private_path" && ! -L "$private_path" ]] || remote_fail
  [[ "$(/usr/bin/stat -c '%u:%a:%F' "$private_path")" == "0:700:directory" ]] || remote_fail
  local name
  for name in "${expected_files[@]}"; do
    [[ -f "$private_path/$name" && ! -L "$private_path/$name" ]] || remote_fail
    [[ "$(/usr/bin/stat -c '%u:%a:%F' "$private_path/$name")" == "0:600:regular file" ]] || remote_fail
  done
  /usr/bin/python3 - "$private_path" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = {
    "dingtalk-app-key", "dingtalk-agent-id", "dingtalk-corp-id",
    "dingtalk-app-secret", "preview-control-database-url",
    "preview-control-audit-database-url",
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "preview-identity-hmac-keyring", "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring", "demo-userids",
}
actual = {path.name for path in root.iterdir() if path.is_file()}
if actual != expected:
    raise SystemExit(1)
userids = (root / "demo-userids").read_text(encoding="utf-8").splitlines()
if not 1 <= len(userids) <= 3 or len(set(userids)) != len(userids):
    raise SystemExit(1)
if any(not value or value != value.strip() for value in userids):
    raise SystemExit(1)
PY
}

validate_operator_prerequisites() {
  [[ -d "$private_path" && ! -L "$private_path" ]] || remote_fail
  [[ "$(/usr/bin/stat -c '%u:%a:%F' "$private_path")" == "0:700:directory" ]] || remote_fail
  /usr/bin/python3 - "$private_path" "$state_path" <<'PY'
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
state = pathlib.Path(sys.argv[2])
operator = {
    "dingtalk-app-key", "dingtalk-agent-id", "dingtalk-corp-id",
    "dingtalk-app-secret", "demo-userids",
}
generated = {
    "preview-control-database-url", "preview-control-audit-database-url",
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "preview-identity-hmac-keyring", "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring",
}
def checked_file(path: pathlib.Path, mode: int) -> None:
    item = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(item.st_mode):
        raise SystemExit(1)
    if item.st_uid != 0 or stat.S_IMODE(item.st_mode) != mode:
        raise SystemExit(1)

actual = {path.name for path in root.iterdir()}
if not operator.issubset(actual) or not actual.issubset(operator | generated):
    raise SystemExit(1)
for path in root.iterdir():
    checked_file(path, 0o600)

published_generated = actual - operator
state_exists = state.exists() or state.is_symlink()
if published_generated == generated:
    if state_exists:
        raise SystemExit(1)
elif state_exists:
    if state.is_symlink() or not state.is_dir():
        raise SystemExit(1)
    state_metadata = state.lstat()
    if state_metadata.st_uid != 0 or stat.S_IMODE(state_metadata.st_mode) != 0o700:
        raise SystemExit(1)
    staged_generated = {path.name for path in state.iterdir()}
    if not published_generated.isdisjoint(staged_generated):
        raise SystemExit(1)
    if published_generated | staged_generated != generated:
        raise SystemExit(1)
    for name in staged_generated:
        checked_file(state / name, 0o600)
elif published_generated:
    raise SystemExit(1)

if actual != operator | published_generated:
    raise SystemExit(1)
userids = (root / "demo-userids").read_text(encoding="utf-8").splitlines()
if not 1 <= len(userids) <= 3 or len(userids) != len(set(userids)):
    raise SystemExit(1)
if any(not value or value != value.strip() for value in userids):
    raise SystemExit(1)
PY
}

validate_read_only_preflight() {
  local preflight_listeners live_target
  [[ -f "$incoming" && ! -L "$incoming" ]] || remote_fail
  [[ "$(/usr/bin/sha256sum "$incoming" | /usr/bin/awk '{print $1}')" == "$archive_sha256" ]] || remote_fail
  [[ -f "$platform_environment" && ! -L "$platform_environment" ]] || remote_fail
  current_before="$(resolve_current_release)" || remote_fail
  validate_operator_prerequisites
  [[ "$(( $(/usr/bin/df -Pk "$platform_root" | /usr/bin/awk 'NR==2 {print $4}') ))" -ge 2097152 ]] || remote_fail
  preflight_listeners="$(preview_listener_set)" || remote_fail
  [[ -z "$preflight_listeners" ]] || remote_fail
  [[ "$(response_code https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1)" == 401 ]] || remote_fail
  [[ "$(response_code https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1)" == 200 ]] || remote_fail
  [[ "$(response_code http://47.106.112.69/)" == 200 ]] || remote_fail
  live_target="$(/usr/bin/readlink -f /etc/nginx/sites-enabled/agent-domain.conf)"
  [[ "$live_target" == /etc/nginx/* && -f "$live_target" ]] || remote_fail
  [[ "$(/usr/bin/sha256sum "$live_target" | /usr/bin/awk '{print $1}')" == "$expected_live_sha256" ]] || remote_fail
}

extract_release() {
  local staging="$platform_root/releases/.stage-$release_sha"
  [[ ! -e "$staging" ]] || /bin/rm -rf -- "$staging"
  /usr/bin/install -d -o root -g root -m 700 "$staging"
  /usr/bin/tar -tf "$incoming" > "$staging/archive-files"
  [[ -s "$staging/archive-files" ]] || remote_fail
  /usr/bin/python3 - "$staging/archive-files" <<'PY'
import pathlib
import sys

for item in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    path = pathlib.PurePosixPath(item)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(1)
PY
  /usr/bin/tar -xf "$incoming" -C "$staging"
  /bin/rm -f -- "$staging/archive-files"
  /usr/bin/printf 'release_sha=%s\narchive_sha256=%s\n' \
    "$release_sha" "$archive_sha256" > "$staging/release-manifest"
  /usr/bin/sha256sum "$incoming" > "$staging/archive.sha256"
  /usr/bin/sha256sum "$staging/release-manifest" > "$staging/manifest.sha256"
  /bin/chown -R root:root "$staging"
  /bin/chmod 600 "$staging/release-manifest" "$staging/archive.sha256" "$staging/manifest.sha256"
  if [[ -e "$release_path" ]]; then
    [[ -f "$release_path/release-manifest" ]] || remote_fail
    /usr/bin/cmp -s "$staging/release-manifest" "$release_path/release-manifest" || remote_fail
    /bin/rm -rf -- "$staging"
  else
    /bin/mv -- "$staging" "$release_path"
  fi
}

validate_release_contract() {
  local source="$release_path/deploy/cloud/bootstrap-demo-preview-secrets.sh"
  [[ -f "$source" && ! -L "$source" ]] || remote_fail
  [[ -f "$release_path/deploy/cloud/bootstrap-demo-preview-prerequisites.sh" && \
    ! -L "$release_path/deploy/cloud/bootstrap-demo-preview-prerequisites.sh" ]] || remote_fail
  for required in \
    "$preview_database" "$preview_migrator_role" "$preview_directory_role"; do
    /usr/bin/grep -Fq -- "$required" "$source" || remote_fail
  done
}

capture_baseline() {
  [[ ! -e "$baseline_dir" ]] || /bin/rm -rf -- "$baseline_dir"
  /usr/bin/install -d -o root -g root -m 700 "$baseline_dir"
  protected_container_invariants > "$baseline_dir/containers.before"
  public_listener_invariants > "$baseline_dir/listeners.before"
  capture_responses > "$baseline_dir/responses.before"
  current_before="$(resolve_current_release)" || remote_fail
  /usr/bin/printf '%s\n' "$current_before" > "$baseline_dir/current.before"
  /usr/bin/printf '%s\n' "$release_sha" > "$baseline_dir/release-sha"
  /usr/bin/printf '%s\n' "$archive_sha256" > "$baseline_dir/archive-sha256"
  /usr/bin/printf '%s\n' "$image_ref" > "$baseline_dir/image-ref"
  /bin/chown -R root:root "$baseline_dir"
  /bin/chmod 600 "$baseline_dir"/*
}

run_preview_migration() {
  compose_preview run --rm --no-deps platform-demo-preview-runner /bin/sh -ec '
      install -d -m 0700 /tmp/migrate
      install -m 0600 /run/demo-preview-secrets/runner/preview-control-migrator-database-url /tmp/migrate/database-url
      export PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/tmp/migrate/database-url
      export PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner_preview
      export PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations
      exec python -m app.control_plane.migrate
    ' >/dev/null 2>&1 || remote_fail
}

run_preview_bootstrap() {
  local result
  result="$(compose_preview run --rm --no-deps \
    platform-demo-preview-runner /bin/sh -ec '
      install -d -m 0700 /tmp/bootstrap
      for name in dingtalk-app-key dingtalk-corp-id dingtalk-app-secret preview-identity-encryption-keyring preview-identity-hmac-keyring preview-control-directory-worker-database-url demo-userids; do
        install -m 0600 "/run/demo-preview-secrets/runner/$name" "/tmp/bootstrap/$name"
      done
      export PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE=/tmp/bootstrap/preview-control-directory-worker-database-url
      export PLATFORM_DINGTALK_APP_KEY_FILE=/tmp/bootstrap/dingtalk-app-key
      export PLATFORM_DINGTALK_CORP_ID_FILE=/tmp/bootstrap/dingtalk-corp-id
      export PLATFORM_DINGTALK_APP_SECRET_FILE=/tmp/bootstrap/dingtalk-app-secret
      export PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE=/tmp/bootstrap/preview-identity-encryption-keyring
      export PLATFORM_IDENTITY_HMAC_KEYRING_FILE=/tmp/bootstrap/preview-identity-hmac-keyring
      exec python -m app.control_plane.demo_bootstrap --userid-file /tmp/bootstrap/demo-userids
    ' 2>/dev/null)" || remote_fail
  [[ "$result" =~ ^DEMO_DIRECTORY_READY\ generation=[0-9a-f-]{36}\ members=[1-3]$ ]] || remote_fail
  /usr/bin/printf '%s\n' "$result" > "$baseline_dir/bootstrap-result"
  /bin/chown root:root "$baseline_dir/bootstrap-result"
  /bin/chmod 600 "$baseline_dir/bootstrap-result"
}

wait_preview_health() {
  local service container_id health attempt
  for service in platform-api-demo-preview platform-loopback-demo-preview; do
    container_id="$(compose_preview ps -q "$service")"
    [[ -n "$container_id" ]] || remote_fail
    for attempt in $(/usr/bin/seq 1 30); do
      health="$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$container_id")"
      [[ "$health" == healthy ]] && break
      [[ "$health" != unhealthy ]] || remote_fail
      /bin/sleep 2
    done
    [[ "$health" == healthy ]] || remote_fail
  done
  [[ "$(/usr/bin/curl --noproxy '*' -sS -o /tmp/demo-health -w '%{http_code}' --max-time 5 \
    http://127.0.0.1:8081/_preview/dingtalk-r1/api/health)" == 200 ]] || remote_fail
  /usr/bin/python3 - /tmp/demo-health <<'PY'
import json
import pathlib
import sys
if json.loads(pathlib.Path(sys.argv[1]).read_text()) != {"status": "ok"}:
    raise SystemExit(1)
PY
  /bin/rm -f -- /tmp/demo-health
}

verify_invariants() {
  local verify_listeners
  protected_container_invariants > "$baseline_dir/containers.after-verify"
  public_listener_invariants > "$baseline_dir/listeners.after-verify"
  capture_responses > "$baseline_dir/responses.after-verify"
  /usr/bin/cmp -s "$baseline_dir/containers.before" "$baseline_dir/containers.after-verify" || remote_fail
  /usr/bin/cmp -s "$baseline_dir/listeners.before" "$baseline_dir/listeners.after-verify" || remote_fail
  /usr/bin/cmp -s "$baseline_dir/responses.before" "$baseline_dir/responses.after-verify" || remote_fail
  verify_listeners="$(preview_listener_set)" || remote_fail
  [[ "$verify_listeners" == "127.0.0.1:8081" ]] || remote_fail
}

verify_phase() {
  local verify_ok=0
  cleanup_failed_verify() {
    local status=$?
    [[ "$verify_ok" == 1 ]] || stop_preview_services
    exit "$status"
  }
  trap cleanup_failed_verify EXIT HUP INT TERM
  validate_read_only_preflight
  extract_release
  [[ -f "$base_compose" && -f "$preview_base_compose" && \
    -f "$preview_compose" ]] || remote_fail
  validate_release_contract
  capture_baseline
  resolve_postgres_endpoint || remote_fail
  prerequisite_result="$("$release_path/deploy/cloud/bootstrap-demo-preview-prerequisites.sh" \
    "$postgres_container" 2>/dev/null)" || remote_fail
  [[ "$prerequisite_result" == 'DEMO_PREVIEW_PREREQUISITES_READY files=12' ]] || remote_fail
  validate_secret_prerequisites
  /usr/bin/docker build --pull=false --build-arg "RELEASE_SHA=$release_sha" \
    -t "$image_ref" -f "$release_path/deploy/cloud/Dockerfile" "$release_path" >/dev/null
  PLATFORM_IMAGE="$image_ref" compose_preview --profile demo-preview-tools config --format json > "$baseline_dir/compose-config.json"
  /bin/chmod 600 "$baseline_dir/compose-config.json"
  /usr/bin/python3 - "$baseline_dir/compose-config.json" "$image_ref" \
    "$postgres_address" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
services = document.get("services", {})
expected_image = sys.argv[2]
expected_postgres_address = sys.argv[3]
required_networks = {"platform-internal", "platform-edge"}
egress_services = (
    "platform-api-demo-preview",
    "platform-demo-preview-runner",
)
for name in egress_services:
    service = services.get(name)
    if not isinstance(service, dict) or service.get("image") != expected_image:
        raise SystemExit(1)
    networks = service.get("networks", {})
    if not required_networks.issubset(networks):
        raise SystemExit(1)
    edge_priority = networks["platform-edge"].get("gw_priority", 0)
    internal_priority = networks["platform-internal"].get("gw_priority", 0)
    if edge_priority != 1 or edge_priority <= internal_priority:
        raise SystemExit(1)
    if service.get("extra_hosts") != [
        f"platform-postgres={expected_postgres_address}"
    ]:
        raise SystemExit(1)
for name in egress_services:
    if services[name].get("ports"):
        raise SystemExit(1)

loopback = services.get("platform-loopback-demo-preview")
if not isinstance(loopback, dict) or loopback.get("image") != expected_image:
    raise SystemExit(1)
if set(loopback.get("networks", {})) != required_networks:
    raise SystemExit(1)
ports = loopback.get("ports", [])
if not isinstance(ports, list) or len(ports) != 1:
    raise SystemExit(1)
port = ports[0]
if not isinstance(port, dict):
    raise SystemExit(1)
if port.get("host_ip") != "127.0.0.1":
    raise SystemExit(1)
if str(port.get("published")) != "8081":
    raise SystemExit(1)
if int(port.get("target", 0)) != 8080:
    raise SystemExit(1)
PY
  image_id="$(/usr/bin/docker image inspect --format '{{.Id}}' "$image_ref")"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || remote_fail
  /usr/bin/printf '%s\n' "$image_id" > "$baseline_dir/image-id"
  /bin/chown root:root "$baseline_dir/image-id"
  /bin/chmod 600 "$baseline_dir/image-id"
  secret_result="$(PLATFORM_IMAGE="$image_ref" \
    "$release_path/deploy/cloud/bootstrap-demo-preview-secrets.sh" 2>/dev/null)" || remote_fail
  [[ "$secret_result" == "$expected_secret_result" ]] || remote_fail
  run_preview_migration
  run_preview_bootstrap
  compose_preview up -d --no-deps platform-api-demo-preview >/dev/null
  compose_preview up -d --no-deps platform-loopback-demo-preview >/dev/null
  wait_preview_health
  verify_invariants
  /usr/bin/printf 'release_sha=%s\narchive_sha256=%s\n' \
    "$release_sha" "$archive_sha256" > "$verified_state.part"
  /bin/chown root:root "$verified_state.part"
  /bin/chmod 600 "$verified_state.part"
  /bin/mv -f -- "$verified_state.part" "$verified_state"
  verify_ok=1
  trap - EXIT HUP INT TERM
  /usr/bin/printf '%s\n' 'DEMO_PREVIEW_VERIFY_OK'
}

cleanup_transaction_links() {
  local link
  for link in "${current_next:-}" "${current_restore:-}"; do
    [[ -n "$link" ]] || continue
    case "$link" in
      "$platform_root"/.current-next-"$transaction_id"|"$platform_root"/.current-restore-"$transaction_id") ;;
      *) return 1 ;;
    esac
    if [[ -L "$link" ]]; then
      /bin/rm -f -- "$link" || return 1
    elif [[ -e "$link" ]]; then
      return 1
    fi
  done
}

restore_current_atomically() {
  local previous_current active_current
  [[ -f "$baseline_dir/current.before" && ! -L "$baseline_dir/current.before" ]] || return 1
  IFS= read -r previous_current < "$baseline_dir/current.before" || return 1
  [[ "$previous_current" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ ]] || return 1
  [[ -d "$previous_current" && ! -L "$previous_current" ]] || return 1
  active_current="$(resolve_current_release)" || return 1
  if [[ "$active_current" == "$previous_current" ]]; then
    return 0
  fi
  [[ "$active_current" == "$release_path" ]] || return 1
  [[ ! -e "$current_restore" && ! -L "$current_restore" ]] || return 1
  /bin/ln -s "$previous_current" "$current_restore" || return 1
  /bin/mv -Tf -- "$current_restore" "$platform_root/current" || return 1
  [[ "$(resolve_current_release)" == "$previous_current" ]]
}

preserve_rollback_retry() {
  local retry_state="$state_dir/rollback-retry"
  local retry_part="$state_dir/.rollback-retry-$transaction_id"
  if [[ -L "$retry_state" || -e "$retry_part" || -L "$retry_part" ]]; then
    return 1
  fi
  /usr/bin/printf 'release_sha=%s\ncommand=%s\n' \
    "$release_sha" "$release_path/deploy/cloud/rollback-demo-preview.sh" > "$retry_part" || return 1
  /bin/chown root:root "$retry_part" || return 1
  /bin/chmod 600 "$retry_part" || return 1
  /bin/mv -f -- "$retry_part" "$retry_state" || return 1
  /usr/bin/printf '%s\n' \
    "DEMO_PREVIEW_ROLLBACK_RETRY_REQUIRED $retry_state" >&2
}

rollback_after_activation() {
  local status=$? rollback_result agent_target include_count active_current rollback_listeners
  trap - EXIT
  trap '' HUP INT TERM
  if ! cleanup_transaction_links; then
    preserve_rollback_retry || exit 1
    exit 1
  fi
  if [[ "${activation_completed:-0}" == 1 ]]; then
    exit "$status"
  fi
  if [[ "${current_switch_attempted:-0}" != 1 ]]; then
    if ! stop_preview_services_strict; then
      preserve_rollback_retry || exit 1
      exit 1
    fi
    exit "$status"
  fi
  active_current="$(resolve_current_release)" || {
    preserve_rollback_retry || exit 1
    exit 1
  }
  if [[ "$active_current" == "$current_before" ]]; then
    if ! stop_preview_services_strict; then
      preserve_rollback_retry || exit 1
      exit 1
    fi
    exit "$status"
  fi
  if [[ "$active_current" != "$release_path" ]]; then
    preserve_rollback_retry || exit 1
    exit 1
  fi

  if ! rollback_result="$("$release_path/deploy/cloud/rollback-demo-preview.sh" 2>&1)"; then
    preserve_rollback_retry || exit 1
    exit 1
  fi
  case "$rollback_result" in
    AGENT_DEMO_PREVIEW_ROLLBACK_OK|"AGENT_DEMO_PREVIEW_ROLLBACK_OK state=already-absent") ;;
    *)
      preserve_rollback_retry || exit 1
      exit 1
      ;;
  esac

  agent_target="$(/usr/bin/readlink -f /etc/nginx/sites-enabled/agent-domain.conf)" || {
    preserve_rollback_retry || exit 1
    exit 1
  }
  [[ "$agent_target" == /etc/nginx/* && -f "$agent_target" && ! -L "$agent_target" ]] || {
    preserve_rollback_retry || exit 1
    exit 1
  }
  include_count="$(/usr/bin/awk \
    '/include \/etc\/nginx\/snippets\/orbbec-agent-demo-preview\.conf;/{count++} END {print count+0}' \
    "$agent_target")" || {
    preserve_rollback_retry || exit 1
    exit 1
  }
  if [[ "$include_count" != 0 || \
        -e /etc/nginx/snippets/orbbec-agent-demo-preview.conf || \
        -L /etc/nginx/snippets/orbbec-agent-demo-preview.conf ]]; then
    preserve_rollback_retry || exit 1
    exit 1
  fi
  rollback_listeners="$(preview_listener_set)" || {
    preserve_rollback_retry || exit 1
    exit 1
  }
  if [[ -n "$rollback_listeners" ]]; then
    preserve_rollback_retry || exit 1
    exit 1
  fi
  if ! restore_current_atomically; then
    preserve_rollback_retry || exit 1
    exit 1
  fi
  if [[ -f "$state_dir/rollback-retry" && ! -L "$state_dir/rollback-retry" ]]; then
    /bin/rm -f -- "$state_dir/rollback-retry"
  fi
  exit "$status"
}

activate_phase() {
  [[ -f "$verified_state" && ! -L "$verified_state" ]] || remote_fail
  /usr/bin/grep -Fxq "release_sha=$release_sha" "$verified_state" || remote_fail
  /usr/bin/grep -Fxq "archive_sha256=$archive_sha256" "$verified_state" || remote_fail
  [[ "$(response_code https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1)" == 401 ]] || remote_fail
  [[ "$(response_code https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1)" == 200 ]] || remote_fail
  current_before="$(resolve_current_release)" || remote_fail
  [[ "$(< "$baseline_dir/current.before")" == "$current_before" ]] || remote_fail
  transaction_nonce="$(/usr/bin/od -An -N16 -tx1 /dev/urandom | \
    /usr/bin/tr -d ' \n')" || remote_fail
  [[ "$transaction_nonce" =~ ^[0-9a-f]{32}$ ]] || remote_fail
  transaction_id="${release_sha}-${BASHPID}-${transaction_nonce}"
  [[ "$transaction_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9a-f]{32}$ ]] || remote_fail
  current_next="$platform_root/.current-next-$transaction_id"
  current_restore="$platform_root/.current-restore-$transaction_id"
  [[ ! -e "$current_next" && ! -L "$current_next" ]] || remote_fail
  [[ ! -e "$current_restore" && ! -L "$current_restore" ]] || remote_fail
  activation_completed=0
  current_switch_attempted=0
  current_switched=0
  trap rollback_after_activation EXIT
  trap 'exit 1' HUP INT TERM
  /bin/ln -s "$release_path" "$current_next"
  current_switch_attempted=1
  /bin/mv -Tf -- "$current_next" "$platform_root/current"
  current_switched=1
  [[ "$(resolve_current_release)" == "$release_path" ]] || remote_fail
  EXPECTED_LIVE_SHA256=382d733e1a581569f4ceedd03ce24ab9113f61a595015bc0449e1319026c1e97 \
    "$release_path/deploy/cloud/install-demo-preview.sh" \
    "$release_path/deploy/cloud/demo-preview.nginx.conf" >/dev/null
  "$release_path/deploy/cloud/accept-demo-preview.sh" >/dev/null
  cleanup_transaction_links || remote_fail
  activation_completed=1
  trap - EXIT HUP INT TERM
  /usr/bin/printf '%s\n' 'DEMO_PREVIEW_ACTIVATE_OK'
}

case "$phase" in
  verify) verify_phase ;;
  activate) activate_phase ;;
esac
