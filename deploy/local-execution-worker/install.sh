#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "EXECUTION_WORKER_INSTALL_FAILED" >&2
  exit 1
}

[[ $# -eq 1 && "$1" == /* && "$(/usr/bin/id -un)" == "agentops" ]] || fail
owner_dsn_file="$1"
runtime_root=/Users/agentops/AgentRuntime
platform_root="$runtime_root/platform"
private_root="$runtime_root/private"
log_root="$runtime_root/log"
metabot_contract="$runtime_root/metabot/runtime-contract.json"
metabot_secret="$private_root/metabot-api-token"
private_key="$private_root/execution-worker-ed25519.key"
public_document="$runtime_root/execution-worker-public.json"
runtime_dsn="$private_root/execution-worker-postgres-dsn"
target="$HOME/Library/LaunchAgents/com.orbbec.agent-execution-worker.plist"
script_dir="$(cd "$(dirname "$0")" && pwd)"
label=com.orbbec.agent-execution-worker
domain="gui/$(/usr/bin/id -u)"

[[ "$platform_root" == "$(cd "$script_dir/../.." && pwd)" ]] || fail
[[ -x "$platform_root/backend/.venv/bin/python" ]] || fail
[[ -f "$owner_dsn_file" && ! -L "$owner_dsn_file" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$owner_dsn_file")" == "600 agentops" ]] || fail
for directory in "$runtime_root" "$private_root"; do
  [[ -d "$directory" && ! -L "$directory" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$directory")" == "700 agentops" ]] || fail
done
for secret in "$metabot_secret"; do
  [[ -f "$secret" && ! -L "$secret" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$secret")" == "600 agentops" ]] || fail
done
[[ -f "$metabot_contract" && ! -L "$metabot_contract" ]] || fail
/bin/mkdir -p "$log_root" "$HOME/Library/LaunchAgents"
/bin/chmod 700 "$log_root" "$HOME/Library/LaunchAgents"
rotation_lock="$private_root/execution-worker-key-rotation.lock"
if [[ -z "${PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD:-}" ]]; then
  "$platform_root/backend/.venv/bin/python" - "$rotation_lock" "$0" "$@" <<'PY' || fail
import fcntl
import os
import stat
import sys

lock_path, script, *arguments = sys.argv[1:]
descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
        raise SystemExit(1)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(descriptor, True)
    environment = dict(os.environ)
    environment["PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD"] = str(descriptor)
    os.execve("/bin/bash", ["/bin/bash", script, *arguments], environment)
finally:
    os.close(descriptor)
PY
  exit 0
fi
[[ "$PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD" =~ ^[0-9]+$ ]] || fail
"$platform_root/backend/.venv/bin/python" - "$rotation_lock" "$PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD" <<'PY' || fail
import fcntl
import os
import stat
import sys

path, raw_descriptor = sys.argv[1:]
descriptor = int(raw_descriptor)
metadata = os.fstat(descriptor)
named = os.stat(path, follow_symlinks=False)
if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid() or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino):
    raise SystemExit(1)
fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
PY

"$platform_root/backend/.venv/bin/python" "$script_dir/generate-worker-key.py" \
  "$private_key" "$public_document"
"$script_dir/bootstrap-worker-database.sh" "$owner_dsn_file" "$runtime_dsn"

previous_exists=0
previous_loaded=0
if [[ -e "$target" || -L "$target" ]]; then
  [[ -f "$target" && ! -L "$target" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$target")" == "600 agentops" ]] || fail
  previous_exists=1
fi
if /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
  previous_loaded=1
fi
[[ "$previous_loaded" == "0" || "$previous_exists" == "1" ]] || fail

temporary=""
previous_plist=""
cleanup_prepared() {
  prepared_cleanup_failed=0
  for prepared_path in "$temporary" "$previous_plist"; do
    if [[ -n "$prepared_path" && -e "$prepared_path" ]]; then
      if ! /bin/rm -f -- "$prepared_path"; then
        prepared_cleanup_failed=1
      fi
    fi
  done
  if [[ "$prepared_cleanup_failed" == "1" ]]; then
    echo "EXECUTION_WORKER_INSTALL_PREPARATION_CLEANUP_FAILED" >&2
  fi
}
trap cleanup_prepared EXIT

temporary="$(/usr/bin/mktemp "$HOME/Library/LaunchAgents/.execution-worker.XXXXXX")"
/bin/cp "$script_dir/com.orbbec.agent-execution-worker.plist.template" "$temporary"
/bin/chmod 600 "$temporary"
/usr/bin/plutil -lint "$temporary" >/dev/null
previous_plist="$(/usr/bin/mktemp "$HOME/Library/LaunchAgents/.execution-worker.previous.XXXXXX")"
if [[ "$previous_exists" == "1" ]]; then
  /bin/cp "$target" "$previous_plist"
fi

rollback_install() {
  rollback_failed=0
  if /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
    if ! /bin/launchctl bootout "$domain/$label" >/dev/null 2>&1; then
      rollback_failed=1
    fi
  fi
  if [[ "$previous_exists" == "1" ]]; then
    rollback_temporary="$(/usr/bin/mktemp "$HOME/Library/LaunchAgents/.execution-worker.rollback.XXXXXX")" || rollback_failed=1
    if [[ "$rollback_failed" == "0" ]]; then
      if ! /bin/cp "$previous_plist" "$rollback_temporary"; then
        rollback_failed=1
      elif ! /bin/chmod 600 "$rollback_temporary"; then
        rollback_failed=1
      elif ! /bin/mv -f "$rollback_temporary" "$target"; then
        rollback_failed=1
      fi
    fi
    if [[ -n "${rollback_temporary:-}" && -e "$rollback_temporary" ]]; then
      if ! /bin/rm -f -- "$rollback_temporary"; then
        rollback_failed=1
      fi
    fi
  elif [[ -e "$target" || -L "$target" ]]; then
    if ! /bin/rm -f -- "$target"; then
      rollback_failed=1
    fi
  fi
  if [[ "$previous_loaded" == "1" && "$rollback_failed" == "0" ]]; then
    if ! /bin/launchctl bootstrap "$domain" "$target"; then
      rollback_failed=1
    elif ! /bin/launchctl enable "$domain/$label"; then
      rollback_failed=1
    fi
  elif [[ "$previous_loaded" == "0" ]] && /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
    rollback_failed=1
  fi
  [[ "$rollback_failed" == "0" ]]
}

install_exit() {
  selected_status=$?
  trap - EXIT
  if [[ "$selected_status" != "0" ]]; then
    if ! rollback_install; then
      echo "EXECUTION_WORKER_INSTALL_ROLLBACK_FAILED" >&2
      /bin/rm -f -- "$temporary" "$previous_plist"
      exit 1
    fi
    if ! /bin/rm -f -- "$temporary" "$previous_plist"; then
      echo "EXECUTION_WORKER_INSTALL_ROLLBACK_FAILED" >&2
      exit 1
    fi
    echo "EXECUTION_WORKER_INSTALL_FAILED" >&2
    exit 1
  fi
  if ! /bin/rm -f -- "$temporary" "$previous_plist"; then
    echo "EXECUTION_WORKER_INSTALL_FAILED" >&2
    exit 1
  fi
}
trap install_exit EXIT

/bin/mv -f "$temporary" "$target"
if [[ "$previous_loaded" == "1" ]]; then
  /bin/launchctl bootout "$domain/$label" >/dev/null 2>&1
fi
/bin/launchctl bootstrap "$domain" "$target"
/bin/launchctl enable "$domain/$label"
echo "EXECUTION_WORKER_INSTALLED"
