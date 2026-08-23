#!/bin/bash
set -eEuo pipefail
umask 077

fail() { echo EXECUTION_WORKER_AGENTOPS_PROVISION_FAILED >&2; exit 1; }
[[ $# -eq 1 && "$(/usr/bin/id -un)" == "agentops" ]] || fail
action="$1"
runtime=/Users/agentops/AgentRuntime
platform="$runtime/platform"
private="$runtime/private"
log="$runtime/log"
contract_source="$runtime/metabot/runtime-contract.json"
token_source="/Users/agentops/Library/Application Support/MetaBotReliability/api-secret"
owner_dsn="$private/postgres-owner-dsn"
token_target="$private/metabot-api-token"
helper_source="$(cd "$(dirname "$0")/../.." && pwd)"
snapshot=/Users/agentops/Developer/work/Orbbec-Agent-Team/scripts/reliability/sanitized-pm2.sh
before="$private/worker-provision-nonbrain-before.json"
brain_before="$private/worker-provision-brain-before.txt"

brain_snapshot() {
  "$snapshot" jlist | /usr/bin/jq -ceS '
    [.[] | select(.name == "metabot-agent-brain")]
    | if length != 1 or .[0].pm2_env.status != "online"
         or (.[0].pid | type) != "number" or .[0].pid <= 0
         or (.[0].pm_id | type) != "number"
         or (.[0].pm2_env.restart_time | type) != "number"
         or (.[0].pm2_env.created_at | type) != "number"
         or (.[0].pm2_env.pm_exec_path | type) != "string"
         or (.[0].pm2_env.pm_cwd | type) != "string"
       then error("Agent Brain process is not exactly inspectable")
       else .[0] | {
         name,pid,pm_id,
         status:.pm2_env.status,
         restart_time:.pm2_env.restart_time,
         created_at:.pm2_env.created_at,
         pm_exec_path:.pm2_env.pm_exec_path,
         pm_cwd:.pm2_env.pm_cwd,
         args:(.pm2_env.args // [])
       }
       end
  '
}

nonbrain_snapshot() {
  "$snapshot" jlist | /usr/bin/jq -ceS '
    map(select(.name != "metabot-agent-brain"))
    | if any(.[];
        (.name | type) != "string"
        or (.pid | type) != "number"
        or (.pm_id | type) != "number"
        or (.pm2_env.status != "online" and .pm2_env.status != "stopped")
        or (.pm2_env.restart_time | type) != "number"
        or (.pm2_env.created_at | type) != "number"
        or (.pm2_env.pm_exec_path | type) != "string"
        or (.pm2_env.pm_cwd | type) != "string")
      then error("non-Brain PM2 process is not exactly inspectable")
      else map({
        name,pid,pm_id,
        status:.pm2_env.status,
        restart_time:.pm2_env.restart_time,
        created_at:.pm2_env.created_at,
        pm_exec_path:.pm2_env.pm_exec_path,
        pm_cwd:.pm2_env.pm_cwd,
        args:(.pm2_env.args // [])
      }) | sort_by(.name,.pm_id)
      end
  '
}

case "$action" in
  prepare)
    [[ ! -e "$owner_dsn" && ! -L "$owner_dsn" ]] || fail
    /bin/mkdir -p "$runtime" "$private" "$log"
    /bin/chmod 700 "$runtime" "$private" "$log"
    [[ -x "$snapshot" ]] || fail
    nonbrain_snapshot > "$before"
    /bin/chmod 600 "$before"
    brain_snapshot > "$brain_before"
    /bin/chmod 600 "$brain_before"
    if [[ ! -d "$platform" ]]; then
      stage="$runtime/.platform.first-bootstrap"
      [[ ! -e "$stage" && ! -L "$stage" ]] || fail
      /bin/mkdir -m 700 "$stage"
      /usr/bin/rsync -rlpt --delete \
        --exclude .git --exclude .worktrees --exclude .venv --exclude node_modules \
        --exclude __pycache__ --exclude '*.pyc' --exclude .pytest_cache \
        "$helper_source/" "$stage/" || fail
      /bin/mv "$stage" "$platform"
    fi
    [[ -d "$platform/backend" && ! -L "$platform" ]] || fail
    platform_delta="$(/usr/bin/rsync -rlpni --checksum --delete --omit-dir-times \
      --exclude .git --exclude .worktrees --exclude .venv --exclude node_modules \
      --exclude __pycache__ --exclude '*.pyc' --exclude .pytest_cache \
      "$helper_source/" "$platform/")" || fail
    [[ -z "$platform_delta" ]] || fail
    if [[ ! -x "$platform/backend/.venv/bin/python" ]]; then
      /opt/homebrew/bin/python3.11 -m venv "$platform/backend/.venv" || fail
      "$platform/backend/.venv/bin/python" -m pip install --disable-pip-version-check \
        -r "$platform/backend/requirements.txt" >/dev/null || fail
    fi
    "$platform/backend/.venv/bin/python" - "$token_source" "$token_target" "$contract_source" <<'PY' || fail
import os, pathlib, stat, sys
source, target, contract = map(pathlib.Path, sys.argv[1:])
for path, maximum in ((source, 16384), (contract, 65536)):
    meta = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(meta.st_mode) or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_uid != os.getuid() or meta.st_size <= 0 or meta.st_size > maximum:
        raise SystemExit(1)
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(source, flags)
try:
    raw = os.read(fd, 16385)
finally:
    os.close(fd)
if len(raw) > 16384 or not raw:
    raise SystemExit(1)
if target.exists():
    meta = target.lstat()
    if target.is_symlink() or not stat.S_ISREG(meta.st_mode) or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_uid != os.getuid() or target.read_bytes() != raw:
        raise SystemExit(1)
else:
    out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        view = memoryview(raw)
        while view:
            view = view[os.write(out, view):]
        os.fsync(out)
    except BaseException:
        os.close(out)
        target.unlink(missing_ok=True)
        raise
    else:
        os.close(out)
PY
    "$platform/backend/.venv/bin/python" -c '
import os, pathlib, stat, sys

target = pathlib.Path(sys.argv[1])
raw = sys.stdin.buffer.read(16385)
if not raw or len(raw) > 16384:
    raise SystemExit(1)
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(target, flags, 0o600)
try:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
        raise OSError("unsafe owner DSN target")
    written = 0
    while written < len(raw):
        written += os.write(descriptor, raw[written:])
    os.fsync(descriptor)
except BaseException:
    os.close(descriptor)
    target.unlink(missing_ok=True)
    raise
else:
    os.close(descriptor)
' "$owner_dsn" || fail
    ;;
  install)
    [[ -f "$owner_dsn" && ! -L "$owner_dsn" ]] || fail
    "$platform/deploy/local-execution-worker/install.sh" "$owner_dsn" || fail
    after="$private/worker-provision-nonbrain-after.json"
    nonbrain_snapshot > "$after"
    /bin/chmod 600 "$after"
    /usr/bin/cmp -s "$before" "$after" || fail
    brain_after="$private/worker-provision-brain-after.txt"
    brain_snapshot > "$brain_after"
    /bin/chmod 600 "$brain_after"
    /usr/bin/cmp -s "$brain_before" "$brain_after" || fail
    brain_pid="$(/usr/bin/jq -er '.pid' "$brain_before")" || fail
    brain_listener="$(/usr/sbin/lsof -nP -iTCP:9110 -sTCP:LISTEN -Fpn | /usr/bin/awk '/^p/{pid=substr($0,2)} /^n/{print pid "," substr($0,2)}')" || fail
    worker_listener="$(/usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN -Fpn | /usr/bin/awk '/^p/{pid=substr($0,2)} /^n/{print pid "," substr($0,2)}')" || fail
    [[ "$brain_listener" == "$brain_pid,127.0.0.1:9110" && "$worker_listener" =~ ^[1-9][0-9]*,127\.0\.0\.1:9120$ ]] || fail
    worker_listener_pid="${worker_listener%%,*}"
    uid="$(/usr/bin/id -u)"
    launchd_state="$(/bin/launchctl print "gui/$uid/com.orbbec.agent-execution-worker")" || fail
    launchd_pid="$(/usr/bin/awk '$1 == "pid" && $2 == "=" {gsub(/;/,"",$3); print $3}' <<<"$launchd_state")"
    [[ "$launchd_pid" == "$worker_listener_pid" ]] || fail
    /bin/rm -f -- "$owner_dsn" "$before" "$after" "$brain_before" "$brain_after"
    echo EXECUTION_WORKER_AGENTOPS_READY
    ;;
  cleanup)
    /bin/rm -f -- "$owner_dsn"
    ;;
  *) fail ;;
esac
