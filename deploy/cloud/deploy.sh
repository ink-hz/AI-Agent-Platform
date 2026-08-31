#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "CLOUD_PLATFORM_DEPLOY_FAILED" >&2
  exit 1
}

if [[ $# -ne 1 || "$1" != /* || ! -f "$1" || -L "$1" ]]; then
  fail
fi
config_path="$1"
if [[ "$(/usr/bin/stat -f '%Lp' "$config_path" 2>/dev/null || true)" != "600" ||
      "$(/usr/bin/stat -f '%u' "$config_path" 2>/dev/null || true)" != "$(/usr/bin/id -u)" ]]; then
  fail
fi

set -a
# shellcheck disable=SC1090
source "$config_path"
set +a
[[ -z "${PLATFORM_OFFICE_RECIPIENT_BEARER+x}" ]] || fail
[[ -z "${PLATFORM_OFFICE_RECIPIENT_BEARER_FILE+x}" ]] || fail
[[ -z "${PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED+x}" ]] || fail
for required_name in CLOUD_ADMIN_HOST CLOUD_ADMIN_KEY CLOUD_SIGNING_PUBLIC_KEY CLOUD_BACKUP_PUBLIC_KEY CLOUD_CONTENT_ENCRYPTION_KEYRING CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING; do
  [[ -n "${!required_name:-}" ]] || fail
done
if [[ "$CLOUD_ADMIN_KEY" != /* || "$CLOUD_SIGNING_PUBLIC_KEY" != /* || "$CLOUD_BACKUP_PUBLIC_KEY" != /* ||
      "$CLOUD_CONTENT_ENCRYPTION_KEYRING" != /* || "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" != /* ||
      ! -f "$CLOUD_ADMIN_KEY" || -L "$CLOUD_ADMIN_KEY" ||
      ! -f "$CLOUD_SIGNING_PUBLIC_KEY" || -L "$CLOUD_SIGNING_PUBLIC_KEY" ||
      ! -f "$CLOUD_BACKUP_PUBLIC_KEY" || -L "$CLOUD_BACKUP_PUBLIC_KEY" ||
      ! -f "$CLOUD_CONTENT_ENCRYPTION_KEYRING" || -L "$CLOUD_CONTENT_ENCRYPTION_KEYRING" ||
      ! -f "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" || -L "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_ADMIN_KEY")" != "600" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_SIGNING_PUBLIC_KEY")" != "600" ||
      "$(/usr/bin/stat -f '%z' "$CLOUD_SIGNING_PUBLIC_KEY")" != "32" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_CONTENT_ENCRYPTION_KEYRING")" != "600" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING")" != "600" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_BACKUP_PUBLIC_KEY")" != "600" ||
      "$(/usr/bin/stat -f '%z' "$CLOUD_BACKUP_PUBLIC_KEY")" != "32" ]]; then
  fail
fi

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$repository_root"
backend_python="$repository_root/backend/.venv/bin/python"
if [[ ! -x "$backend_python" ]]; then
  common_git="$(git rev-parse --path-format=absolute --git-common-dir)" || fail
  backend_python="$(/usr/bin/dirname "$common_git")/backend/.venv/bin/python"
fi
[[ -x "$backend_python" ]] || fail
[[ -z "$(git status --porcelain)" ]] || fail
release_sha="$(git rev-parse HEAD)"
remote_master_sha="$(git rev-parse refs/remotes/origin/master 2>/dev/null || true)"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
[[ "$release_sha" == "$remote_master_sha" ]] || fail

artifact_root="$(mktemp -d)"
deploy_input_acquired=0
agent_brain_action_lock_acquired=0
agent_brain_action_lock_token=""
remote_operation_uncertain=0
cutover_started=0
cutover_confirmed=0
run_remote_operation() {
  remote_operation_uncertain=1
  if "$@"; then
    remote_operation_uncertain=0
    return 0
  fi
  return 1
}
release_agent_brain_action_lock() {
  /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" /bin/bash -s -- \
    "$agent_brain_action_lock_token" <<'REMOTE'
set -euo pipefail
token="$1"; lock=/opt/orbbec-agent-platform/private/agent-brain-action.lock
[[ "$token" =~ ^[0-9a-f-]{36}$ && -d "$lock" && ! -L "$lock" ]] || exit 1
[[ "$(cat "$lock/owner")" == "$token" ]] || exit 1
tombstone="$lock.releasing.$token"
[[ ! -e "$tombstone" && ! -L "$tombstone" ]] || exit 1
mv "$lock" "$tombstone"
rm -f -- "$tombstone/owner"
rmdir "$tombstone"
REMOTE
}
cleanup() {
  exit_status=$?
  trap - EXIT
  release_safe=0
  if [[ ( "$remote_operation_uncertain" == "0" && "$cutover_started" == "0" ) ||
        "$cutover_confirmed" == "1" ]]; then
    release_safe=1
    if [[ "$deploy_input_acquired" == "1" ]]; then
      if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
        /usr/bin/python3 - release "$release_sha" "$deployment_id" \
        < "$repository_root/deploy/cloud/deploy-input-lock.py" >/dev/null; then
        exit_status=1
        release_safe=0
      fi
    fi
    if [[ "$release_safe" == "1" && "${agent_brain_action_lock_acquired:-0}" == "1" ]]; then
      if ! release_agent_brain_action_lock; then
        exit_status=1
      fi
    fi
  fi
  /bin/rm -rf -- "$artifact_root"
  exit "$exit_status"
}
trap cleanup EXIT
source_root="$artifact_root/source"
/bin/mkdir -p "$source_root"
git archive "$release_sha" | /usr/bin/tar -x -C "$source_root"
"$backend_python" - "$source_root" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
lines = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    relative = path.relative_to(root).as_posix()
    if relative == "MANIFEST.sha256":
        continue
    lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
(root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
artifact_path="$artifact_root/platform-$release_sha.tar.gz"
/usr/bin/tar -czf "$artifact_path" -C "$source_root" .
artifact_digest="$(/usr/bin/shasum -a 256 "$artifact_path" | /usr/bin/awk '{print $1}')"

ssh_options=(
  -i "$CLOUD_ADMIN_KEY"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)
deployment_id="$("$backend_python" -c 'import secrets; print(secrets.token_hex(16))')"
[[ "$deployment_id" =~ ^[0-9a-f]{32}$ ]] || fail
agent_brain_action_lock_token="$("$backend_python" -c 'import uuid; print(uuid.uuid4())')"
[[ "$agent_brain_action_lock_token" =~ ^[0-9a-f-]{36}$ ]] || fail
acquire_agent_brain_action_lock() {
  /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" /bin/bash -s -- \
    "$agent_brain_action_lock_token" <<'REMOTE'
set -euo pipefail
umask 077
token="$1"; lock=/opt/orbbec-agent-platform/private/agent-brain-action.lock
[[ "$token" =~ ^[0-9a-f-]{36}$ && ! -e "$lock" && ! -L "$lock" ]] || exit 1
complete=0
cleanup_lock() {
  status="$?"
  trap - EXIT
  if [[ "$complete" == "0" ]]; then rm -f -- "$lock/owner"; rmdir "$lock" 2>/dev/null || true; fi
  exit "$status"
}
trap cleanup_lock EXIT
mkdir -m 700 "$lock"
printf '%s\n' "$token" > "$lock/owner"
chmod 600 "$lock/owner"
complete=1
trap - EXIT
REMOTE
}
if ! run_remote_operation acquire_agent_brain_action_lock; then
  fail
fi
agent_brain_action_lock_acquired=1
if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  /usr/bin/python3 - acquire "$release_sha" "$deployment_id" \
  < "$repository_root/deploy/cloud/deploy-input-lock.py" >/dev/null; then
  fail
fi
deploy_input_acquired=1

if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/bin; /bin/cat > /opt/orbbec-agent-platform/bin/deploy-input-lock.py.part; chmod 700 /opt/orbbec-agent-platform/bin/deploy-input-lock.py.part; mv -f /opt/orbbec-agent-platform/bin/deploy-input-lock.py.part /opt/orbbec-agent-platform/bin/deploy-input-lock.py' \
  < "$repository_root/deploy/cloud/deploy-input-lock.py"; then
  fail
fi
if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/bin; /bin/cat > /opt/orbbec-agent-platform/bin/remote-stage.sh.part; chmod 700 /opt/orbbec-agent-platform/bin/remote-stage.sh.part; mv -f /opt/orbbec-agent-platform/bin/remote-stage.sh.part /opt/orbbec-agent-platform/bin/remote-stage.sh' \
  < "$repository_root/deploy/cloud/remote-stage.sh"; then
  fail
fi
if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/bin; /bin/cat > /opt/orbbec-agent-platform/bin/install-execution-worker-keyring.py.part; chmod 700 /opt/orbbec-agent-platform/bin/install-execution-worker-keyring.py.part; mv -f /opt/orbbec-agent-platform/bin/install-execution-worker-keyring.py.part /opt/orbbec-agent-platform/bin/install-execution-worker-keyring.py' \
  < "$repository_root/deploy/cloud/install-execution-worker-keyring.py"; then
  fail
fi
if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/private; /bin/cat > /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub.part; chmod 600 /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub.part; mv -f /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub.part /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub' \
  < "$CLOUD_BACKUP_PUBLIC_KEY"; then
  fail
fi
if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/private; /bin/cat > /opt/orbbec-agent-platform/private/replica-signing-public-key.part; chmod 600 /opt/orbbec-agent-platform/private/replica-signing-public-key.part; mv -f /opt/orbbec-agent-platform/private/replica-signing-public-key.part /opt/orbbec-agent-platform/private/replica-signing-public-key' \
  < "$CLOUD_SIGNING_PUBLIC_KEY"; then
  fail
fi
if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/private; /bin/cat > /opt/orbbec-agent-platform/private/content-encryption-keyring.part; chmod 600 /opt/orbbec-agent-platform/private/content-encryption-keyring.part; mv -f /opt/orbbec-agent-platform/private/content-encryption-keyring.part /opt/orbbec-agent-platform/private/content-encryption-keyring' \
  < "$CLOUD_CONTENT_ENCRYPTION_KEYRING"; then
  fail
fi
cutover_started=1
if ! run_remote_operation /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  "/opt/orbbec-agent-platform/bin/install-execution-worker-keyring.py" stage "$release_sha" "$deployment_id" \
  < "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING"; then
  fail
fi
remote_operation_uncertain=1
if ! cutover_output="$(/usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  "/opt/orbbec-agent-platform/bin/install-execution-worker-keyring.py" cutover "$release_sha" "$artifact_digest" "$deployment_id" \
  < "$artifact_path")"; then
  fail
fi
remote_operation_uncertain=0
[[ "$cutover_output" == "CLOUD_PLATFORM_DEPLOY_OK release=$release_sha mode=dingtalk" ]] || fail
/usr/bin/printf '%s\n' "$cutover_output"
cutover_confirmed=1
