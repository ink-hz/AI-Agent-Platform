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

case "$action" in
  prepare)
    [[ ! -e "$owner_dsn" && ! -L "$owner_dsn" ]] || fail
    /bin/mkdir -p "$runtime" "$private" "$log"
    /bin/chmod 700 "$runtime" "$private" "$log"
    [[ -x "$snapshot" ]] || fail
    /bin/zsh "$snapshot" snapshot-except metabot-agent-brain > "$before"
    /bin/chmod 600 "$before"
    /bin/zsh "$snapshot" state-one metabot-agent-brain > "$brain_before"
    /bin/chmod 600 "$brain_before"
    if [[ ! -d "$platform" ]]; then
      stage="$runtime/.platform.first-bootstrap"
      [[ ! -e "$stage" && ! -L "$stage" ]] || fail
      /bin/mkdir -m 700 "$stage"
      /usr/bin/rsync -a --delete \
        --exclude .git --exclude .worktrees --exclude .venv --exclude node_modules \
        "$helper_source/" "$stage/" || fail
      /bin/mv "$stage" "$platform"
    fi
    [[ -d "$platform/backend" && ! -L "$platform" ]] || fail
    for relative in \
      backend/requirements.txt \
      backend/app/execution_relay/worker_schema.sql \
      deploy/local-execution-worker/install.sh \
      deploy/local-execution-worker/provision-agentops.sh; do
      /usr/bin/cmp -s "$helper_source/$relative" "$platform/$relative" || fail
    done
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
        os.write(out, raw); os.fsync(out)
    finally:
        os.close(out)
PY
    /bin/cat > "$owner_dsn"
    /bin/chmod 600 "$owner_dsn"
    [[ -s "$owner_dsn" && "$(( $(/usr/bin/stat -f %z "$owner_dsn") ))" -le 16384 ]] || fail
    ;;
  install)
    [[ -f "$owner_dsn" && ! -L "$owner_dsn" ]] || fail
    "$platform/deploy/local-execution-worker/install.sh" "$owner_dsn" || fail
    after="$private/worker-provision-nonbrain-after.json"
    /bin/zsh "$snapshot" snapshot-except metabot-agent-brain > "$after"
    /bin/chmod 600 "$after"
    /usr/bin/cmp -s "$before" "$after" || fail
    brain_after="$private/worker-provision-brain-after.txt"
    /bin/zsh "$snapshot" state-one metabot-agent-brain > "$brain_after"
    /bin/chmod 600 "$brain_after"
    /usr/bin/cmp -s "$brain_before" "$brain_after" || fail
    brain_lines="$(/usr/sbin/lsof -nP -iTCP:9110 -sTCP:LISTEN | /usr/bin/awk 'NR>1 {print $9}')"
    worker_lines="$(/usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN | /usr/bin/awk 'NR>1 {print $9}')"
    [[ "$brain_lines" == "127.0.0.1:9110" && "$worker_lines" == "127.0.0.1:9120" ]] || fail
    uid="$(/usr/bin/id -u)"
    /bin/launchctl print "gui/$uid/com.orbbec.agent-execution-worker" >/dev/null || fail
    /bin/rm -f -- "$owner_dsn" "$before" "$after" "$brain_before" "$brain_after"
    echo EXECUTION_WORKER_AGENTOPS_READY
    ;;
  cleanup)
    /bin/rm -f -- "$owner_dsn"
    ;;
  *) fail ;;
esac
