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
platform_environment="$platform_root/private/platform.env"
state_dir=/var/lib/orbbec-agent-demo-preview
verified_state="$state_dir/verified-release"
baseline_dir="$state_dir/release-baseline"
image_ref="orbbec-agent-platform-demo-preview:$release_sha"
base_compose="$release_path/deploy/cloud/compose.yaml"
preview_compose="$release_path/deploy/cloud/compose.demo-preview.yaml"
compose=(/usr/bin/docker compose --env-file "$platform_environment" \
  -f "$base_compose" -f "$preview_compose")
postgres_container=""
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
  PLATFORM_IMAGE="$image_ref" "${compose[@]}" "$@"
}

stop_preview_services() {
  if [[ -f "$base_compose" && -f "$preview_compose" && -f "$platform_environment" ]]; then
    compose_preview stop platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1 || true
    compose_preview rm -f platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1 || true
  fi
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
  /usr/bin/python3 - "$private_path" <<'PY'
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
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
actual = {path.name for path in root.iterdir()}
if actual not in (operator, operator | generated):
    raise SystemExit(1)
for path in root.iterdir():
    item = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(item.st_mode):
        raise SystemExit(1)
    if item.st_uid != 0 or stat.S_IMODE(item.st_mode) != 0o600:
        raise SystemExit(1)
userids = (root / "demo-userids").read_text(encoding="utf-8").splitlines()
if not 1 <= len(userids) <= 3 or len(userids) != len(set(userids)):
    raise SystemExit(1)
if any(not value or value != value.strip() for value in userids):
    raise SystemExit(1)
PY
}

validate_read_only_preflight() {
  [[ -f "$incoming" && ! -L "$incoming" ]] || remote_fail
  [[ "$(/usr/bin/sha256sum "$incoming" | /usr/bin/awk '{print $1}')" == "$archive_sha256" ]] || remote_fail
  [[ -f "$platform_environment" && ! -L "$platform_environment" ]] || remote_fail
  [[ -L "$platform_root/current" ]] || remote_fail
  validate_operator_prerequisites
  [[ "$(( $(/usr/bin/df -Pk "$platform_root" | /usr/bin/awk 'NR==2 {print $4}') ))" -ge 2097152 ]] || remote_fail
  ! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(127\.0\.0\.1|0\.0\.0\.0|\[::\]|\[::1\]):8081$' || remote_fail
  [[ "$(response_code https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1)" == 401 ]] || remote_fail
  [[ "$(response_code https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1)" == 200 ]] || remote_fail
  [[ "$(response_code http://47.106.112.69/)" == 200 ]] || remote_fail
  local live_target
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
  /usr/bin/readlink -f "$platform_root/current" > "$baseline_dir/current.before"
  /usr/bin/printf '%s\n' "$release_sha" > "$baseline_dir/release-sha"
  /usr/bin/printf '%s\n' "$archive_sha256" > "$baseline_dir/archive-sha256"
  /usr/bin/printf '%s\n' "$image_ref" > "$baseline_dir/image-ref"
  /bin/chown -R root:root "$baseline_dir"
  /bin/chmod 600 "$baseline_dir"/*
}

run_preview_migration() {
  compose_preview run --rm --no-deps platform-demo-preview-runner /bin/sh -ec '
      install -d -m 0700 /tmp/migrate
      install -m 0600 /run/demo-preview-secrets/offline/preview-control-migrator-database-url /tmp/migrate/database-url
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
      for name in dingtalk-app-key dingtalk-corp-id dingtalk-app-secret preview-identity-encryption-keyring preview-identity-hmac-keyring; do
        install -m 0600 "/run/demo-preview-secrets/runtime/$name" "/tmp/bootstrap/$name"
      done
      for name in preview-control-directory-worker-database-url demo-userids; do
        install -m 0600 "/run/demo-preview-secrets/offline/$name" "/tmp/bootstrap/$name"
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
  protected_container_invariants > "$baseline_dir/containers.after-verify"
  public_listener_invariants > "$baseline_dir/listeners.after-verify"
  capture_responses > "$baseline_dir/responses.after-verify"
  /usr/bin/cmp -s "$baseline_dir/containers.before" "$baseline_dir/containers.after-verify" || remote_fail
  /usr/bin/cmp -s "$baseline_dir/listeners.before" "$baseline_dir/listeners.after-verify" || remote_fail
  /usr/bin/cmp -s "$baseline_dir/responses.before" "$baseline_dir/responses.after-verify" || remote_fail
  /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fxq '127.0.0.1:8081' || remote_fail
  # Reject the two public wildcard forms: 0.0.0.0:8081 and [::]:8081.
  ! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):8081$' || remote_fail
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
  [[ -f "$base_compose" && -f "$preview_compose" ]] || remote_fail
  validate_release_contract
  capture_baseline
  postgres_container="$(compose_preview ps -q platform-postgres)"
  [[ "$postgres_container" =~ ^[0-9a-f]{12,64}$ ]] || remote_fail
  prerequisite_result="$("$release_path/deploy/cloud/bootstrap-demo-preview-prerequisites.sh" \
    "$postgres_container" 2>/dev/null)" || remote_fail
  [[ "$prerequisite_result" == 'DEMO_PREVIEW_PREREQUISITES_READY files=12' ]] || remote_fail
  validate_secret_prerequisites
  /usr/bin/docker build --pull=false --build-arg "RELEASE_SHA=$release_sha" \
    -t "$image_ref" -f "$release_path/deploy/cloud/Dockerfile" "$release_path" >/dev/null
  PLATFORM_IMAGE="$image_ref" compose_preview config --format json > "$baseline_dir/compose-config.json"
  /bin/chmod 600 "$baseline_dir/compose-config.json"
  /usr/bin/python3 - "$baseline_dir/compose-config.json" "$image_ref" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
services = document.get("services", {})
expected_image = sys.argv[2]
required_networks = {"platform-internal", "platform-edge"}
for name in (
    "platform-api-demo-preview",
    "platform-demo-preview-runner",
    "platform-loopback-demo-preview",
):
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
for name in ("platform-api-demo-preview", "platform-demo-preview-runner"):
    if services[name].get("ports"):
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

rollback_after_activation() {
  local status=$?
  if [[ "${activation_completed:-0}" != 1 ]]; then
    "$release_path/deploy/cloud/rollback-demo-preview.sh" >/dev/null 2>&1 || true
    stop_preview_services
    if [[ -f "$baseline_dir/current.before" ]]; then
      previous_current="$(< "$baseline_dir/current.before")"
      if [[ "$previous_current" == "$platform_root"/releases/* && -d "$previous_current" ]]; then
        /bin/ln -s "$previous_current" "$platform_root/current.rollback"
        /bin/mv -Tf -- "$platform_root/current.rollback" "$platform_root/current"
      fi
    fi
  fi
  exit "$status"
}

activate_phase() {
  [[ -f "$verified_state" && ! -L "$verified_state" ]] || remote_fail
  /usr/bin/grep -Fxq "release_sha=$release_sha" "$verified_state" || remote_fail
  /usr/bin/grep -Fxq "archive_sha256=$archive_sha256" "$verified_state" || remote_fail
  [[ "$(response_code https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1)" == 401 ]] || remote_fail
  [[ "$(response_code https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1)" == 200 ]] || remote_fail
  activation_completed=0
  trap rollback_after_activation EXIT HUP INT TERM
  /bin/ln -s "$release_path" "$platform_root/current.part"
  /bin/mv -Tf -- "$platform_root/current.part" "$platform_root/current"
  EXPECTED_LIVE_SHA256=382d733e1a581569f4ceedd03ce24ab9113f61a595015bc0449e1319026c1e97 \
    "$release_path/deploy/cloud/install-demo-preview.sh" \
    "$release_path/deploy/cloud/demo-preview.nginx.conf" >/dev/null
  "$release_path/deploy/cloud/accept-demo-preview.sh" >/dev/null
  activation_completed=1
  trap - EXIT HUP INT TERM
  /usr/bin/printf '%s\n' 'DEMO_PREVIEW_ACTIVATE_OK'
}

case "$phase" in
  verify) verify_phase ;;
  activate) activate_phase ;;
esac
