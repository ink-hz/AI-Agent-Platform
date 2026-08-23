#!/bin/bash
set -eEuo pipefail
umask 077

fail() { echo EXECUTION_WORKER_AGENTOPS_PROVISION_FAILED >&2; exit 1; }
[[ $# -ge 1 && "$(/usr/bin/id -un)" == "agentops" ]] || fail
[[ "${HOME:-}" == /Users/agentops && "${USER:-}" == agentops && "${LOGNAME:-}" == agentops ]] || fail
cd /Users/agentops || fail

action="$1"
shift
runtime=/Users/agentops/AgentRuntime
platform="$runtime/platform"
private="$runtime/private"
log="$runtime/log"
deploy_tools="$runtime/deploy-tools"
contract_source="$runtime/metabot/runtime-contract.json"
token_source="/Users/agentops/Library/Application Support/MetaBotReliability/api-secret"
owner_dsn="$private/postgres-owner-dsn"
token_target="$private/metabot-api-token"
snapshot="$deploy_tools/reliability/sanitized-pm2.sh"
before="$private/worker-provision-nonbrain-before.json"
brain_before="$private/worker-provision-brain-before.txt"
receipt="$private/worker-provision-receipt"
worker_supervisor="$platform/deploy/local-execution-worker/worker-pm2.sh"
pm2_dump=/Users/agentops/.pm2/dump.pm2
key_manifest="$private/execution-worker-key-binding.plist"

safe_remove_tree() {
  [[ $# -eq 1 ]] || return 1
  /usr/bin/python3 - "$runtime" "$1" <<'PY'
import os, pathlib, shutil, stat, sys
root, target = map(pathlib.Path, sys.argv[1:])
root_meta = root.lstat()
if root.is_symlink() or not stat.S_ISDIR(root_meta.st_mode) or root_meta.st_uid != os.getuid():
    raise SystemExit(1)
try:
    relative = target.relative_to(root)
except ValueError:
    raise SystemExit(1)
if not relative.parts or relative.parts[0] not in {
    "private", "platform", "deploy-tools"
} and not relative.parts[0].startswith(".platform"):
    raise SystemExit(1)
if target.exists() or target.is_symlink():
    meta = target.lstat()
    if target.is_symlink() or not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid():
        raise SystemExit(1)
    shutil.rmtree(target)
PY
}

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
         name,pid,pm_id,status:.pm2_env.status,
         restart_time:.pm2_env.restart_time,created_at:.pm2_env.created_at,
         pm_exec_path:.pm2_env.pm_exec_path,pm_cwd:.pm2_env.pm_cwd,
         args:(.pm2_env.args // [])
       }
       end
  '
}

nonbrain_snapshot() {
  "$snapshot" jlist | /usr/bin/jq -ceS '
    map(select(.name != "metabot-agent-brain" and .name != "orbbec-agent-execution-worker"))
    | if any(.[];
        (.name | type) != "string" or (.pid | type) != "number"
        or (.pm_id | type) != "number"
        or (.pm2_env.status != "online" and .pm2_env.status != "stopped")
        or (.pm2_env.restart_time | type) != "number"
        or (.pm2_env.created_at | type) != "number"
        or (.pm2_env.pm_exec_path | type) != "string"
        or (.pm2_env.pm_cwd | type) != "string")
      then error("non-Brain PM2 process is not exactly inspectable")
      else map({
        name,pid,pm_id,status:.pm2_env.status,
        restart_time:.pm2_env.restart_time,created_at:.pm2_env.created_at,
        pm_exec_path:.pm2_env.pm_exec_path,pm_cwd:.pm2_env.pm_cwd,
        args:(.pm2_env.args // [])
      }) | sort_by(.name,.pm_id)
      end
  '
}

rollback_worker() {
  [[ -d "$receipt" && ! -L "$receipt" ]] || return 0
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$receipt")" == "700 agentops" ]] || return 1
  [[ -f "$receipt/prepared" && ! -L "$receipt/prepared" && "$(<"$receipt/prepared")" == v1 ]] || return 1
  [[ -f "$receipt/state" && ! -L "$receipt/state" ]] || return 1
  prior_state="$(<"$receipt/state")" || return 1
  [[ "$prior_state" == absent || "$prior_state" == online || "$prior_state" == stopped ]] || return 1
  "$worker_supervisor" restore "$prior_state" >/dev/null || return 1
  if [[ -e "$receipt/previous.manifest" || -L "$receipt/previous.manifest" ]]; then
    [[ -f "$receipt/previous.manifest" && ! -L "$receipt/previous.manifest" ]] || return 1
    manifest_part="$key_manifest.rollback.$$"
    [[ ! -e "$manifest_part" && ! -L "$manifest_part" ]] || return 1
    /bin/cp "$receipt/previous.manifest" "$manifest_part" || return 1
    /bin/chmod 600 "$manifest_part" || return 1
    /bin/mv -f "$manifest_part" "$key_manifest" || return 1
    /usr/bin/cmp -s "$receipt/previous.manifest" "$key_manifest" || return 1
  else
    /bin/rm -f -- "$key_manifest" || return 1
    [[ ! -e "$key_manifest" && ! -L "$key_manifest" ]] || return 1
  fi
  if [[ -e "$receipt/previous.dump" || -L "$receipt/previous.dump" ]]; then
    [[ -f "$receipt/previous.dump" && ! -L "$receipt/previous.dump" ]] || return 1
    [[ -f "$receipt/previous.dump.mode" && ! -L "$receipt/previous.dump.mode" ]] || return 1
    prior_dump_mode="$(<"$receipt/previous.dump.mode")" || return 1
    [[ "$prior_dump_mode" =~ ^6[04][04]$ ]] || return 1
    dump_part="$pm2_dump.rollback.$$"
    [[ ! -e "$dump_part" && ! -L "$dump_part" ]] || return 1
    /bin/cp "$receipt/previous.dump" "$dump_part" || return 1
    /bin/chmod "$prior_dump_mode" "$dump_part" || return 1
    /bin/mv -f "$dump_part" "$pm2_dump" || return 1
    /usr/bin/cmp -s "$receipt/previous.dump" "$pm2_dump" || return 1
  else
    [[ ! -e "$receipt/previous.dump.mode" && ! -L "$receipt/previous.dump.mode" ]] || return 1
    /bin/rm -f -- "$pm2_dump" || return 1
    [[ ! -e "$pm2_dump" && ! -L "$pm2_dump" ]] || return 1
  fi
  [[ "$("$worker_supervisor" state)" == "$prior_state" ]] || return 1
  safe_remove_tree "$receipt" || return 1
}

cleanup_ephemeral() {
  /bin/rm -f -- "$owner_dsn" "$before" "$private/worker-provision-nonbrain-after.json" \
    "$brain_before" "$private/worker-provision-brain-after.txt"
}

case "$action" in
  stage)
    [[ $# -eq 2 && "$1" =~ ^[0-9a-f]{40}$ && "$2" =~ ^[0-9a-f]{64}$ ]] || fail
    release_sha="$1"
    archive_sha="$2"
    /bin/mkdir -p "$runtime" "$private" "$log" "$deploy_tools"
    /bin/chmod 700 "$runtime" "$private" "$log" "$deploy_tools"
    stage="$runtime/.platform.first-bootstrap.$release_sha"
    archive="$runtime/.platform-release.$release_sha.tar"
    previous="$runtime/.platform.previous-release"
    venv_stage="$stage/backend/.venv"
    stage_complete=0
    old_moved=0
    new_published=0
    stage_exit() {
      stage_status="$?"; trap - ERR EXIT
      if [[ "$stage_complete" != 1 ]]; then
        if [[ "$new_published" == 1 && -d "$platform" && ! -L "$platform" ]]; then
          safe_remove_tree "$platform" >/dev/null 2>&1 || stage_status=1
        fi
        if [[ "$old_moved" == 1 && -d "$previous" && ! -L "$previous" \
          && ! -e "$platform" && ! -L "$platform" ]]; then
          /bin/mv "$previous" "$platform" >/dev/null 2>&1 || stage_status=1
        fi
        safe_remove_tree "$stage" >/dev/null 2>&1 || stage_status=1
        /bin/rm -f -- "$archive" || stage_status=1
      fi
      exit "$stage_status"
    }
    trap stage_exit ERR EXIT
    safe_remove_tree "$stage" || fail
    /bin/rm -f -- "$archive"
    /usr/bin/python3 -c '
import hashlib,os,pathlib,stat,sys
target=pathlib.Path(sys.argv[1]); expected=sys.argv[2]; total=0
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
digest=hashlib.sha256()
try:
  while True:
    chunk=sys.stdin.buffer.read(65536)
    if not chunk: break
    total += len(chunk)
    if total > 67108864: raise OSError("archive too large")
    digest.update(chunk); view=memoryview(chunk)
    while view: view=view[os.write(fd,view):]
  os.fsync(fd)
except BaseException:
  os.close(fd); target.unlink(missing_ok=True); raise
else: os.close(fd)
if digest.hexdigest()!=expected: target.unlink(missing_ok=True); raise SystemExit(1)
' "$archive" "$archive_sha" || fail
    /bin/mkdir -m 700 "$stage"
    /usr/bin/tar -xf "$archive" -C "$stage" || fail
    /usr/bin/python3 - "$stage" "$release_sha" "$archive_sha" <<'PY' || fail
import json,os,pathlib,stat,sys
stage=pathlib.Path(sys.argv[1]); release,archive=sys.argv[2:]
for path in stage.rglob("*"):
    meta=path.lstat()
    if path.is_symlink() or not (stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode)):
        raise SystemExit(1)
marker=stage/".platform-release.json"
marker.write_text(json.dumps({"schema_version":1,"release_sha":release,"archive_sha256":archive},sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
os.chmod(marker,0o600)
PY
    if [[ -d "$previous" && ! -L "$previous" ]]; then
      if [[ -d "$platform" && ! -L "$platform" ]] && \
         /usr/bin/cmp -s "$stage/.platform-release.json" "$platform/.platform-release.json"; then
        old_moved=1
        new_published=1
      elif [[ ! -e "$platform" && ! -L "$platform" ]]; then
        /bin/mv "$previous" "$platform" || fail
      else
        [[ -d "$platform" && ! -L "$platform" ]] || fail
        safe_remove_tree "$platform" || fail
        /bin/mv "$previous" "$platform" || fail
      fi
    elif [[ -e "$previous" || -L "$previous" ]]; then
      fail
    fi
    if [[ -d "$platform" && ! -L "$platform" ]] && \
       /usr/bin/cmp -s "$stage/.platform-release.json" "$platform/.platform-release.json"; then
      platform_delta="$(/usr/bin/rsync -rlpni --checksum --delete --omit-dir-times \
        --exclude .platform-release.json --exclude .venv --exclude __pycache__ \
        --exclude '*.pyc' --exclude .pytest_cache \
        "$stage/" "$platform/")" || fail
      [[ -z "$platform_delta" ]] || fail
      safe_remove_tree "$stage" || fail
      venv="$platform/backend/.venv"
      venv_marker="$venv/.orbbec-release"
      [[ -d "$venv" && ! -L "$venv" && -x "$venv/bin/python" \
        && -f "$venv_marker" && "$(<"$venv_marker")" == "$release_sha" ]] || fail
      "$venv/bin/python" -m pip check >/dev/null || fail
    else
      [[ ! -e "$platform" || ( -d "$platform" && ! -L "$platform" ) ]] || fail
      /opt/homebrew/bin/python3.11 -m venv "$venv_stage" || fail
      "$venv_stage/bin/python" -m pip install --disable-pip-version-check \
        -r "$stage/backend/requirements.txt" >/dev/null || fail
      "$venv_stage/bin/python" -m pip check >/dev/null || fail
      printf '%s\n' "$release_sha" > "$venv_stage/.orbbec-release"
      /bin/chmod 600 "$venv_stage/.orbbec-release"
      [[ -x "$venv_stage/bin/python" && "$(<"$venv_stage/.orbbec-release")" == "$release_sha" ]] || fail
      if [[ -d "$platform" && ! -L "$platform" ]]; then
        [[ ! -e "$previous" && ! -L "$previous" ]] || fail
        /bin/mv "$platform" "$previous" || fail
        old_moved=1
      fi
      /bin/mv "$stage" "$platform" || fail
      new_published=1
      venv="$platform/backend/.venv"
      venv_marker="$venv/.orbbec-release"
      [[ -x "$venv/bin/python" && "$(<"$venv_marker")" == "$release_sha" ]] || fail
      "$venv/bin/python" -m pip check >/dev/null || fail
    fi
    /bin/rm -f -- "$archive"
    if [[ "$old_moved" == 1 ]]; then
      # The new tree is fully validated. From this point the old tree may become
      # partially deleted and must never again be treated as a rollback source.
      old_moved=0
      new_published=0
      stage_complete=1
      safe_remove_tree "$previous" || fail
    fi
    stage_complete=1
    trap - ERR EXIT
    echo EXECUTION_WORKER_AGENTOPS_STAGED
    ;;
  prepare)
    [[ $# -eq 0 && -d "$platform/backend/.venv" && ! -L "$platform" ]] || fail
    [[ -x "$snapshot" && ! -L "$snapshot" ]] || fail
    [[ "$(/usr/bin/stat -f '%Su' "$snapshot")" == agentops ]] || fail
    [[ -x "$worker_supervisor" && ! -L "$worker_supervisor" ]] || fail
    [[ "$(/usr/bin/stat -f '%Lp %Su' "$worker_supervisor")" == "755 agentops" ]] || fail
    [[ ! -e "$receipt" && ! -L "$receipt" ]] || fail
    [[ ! -e "$owner_dsn" && ! -L "$owner_dsn" ]] || fail
    nonbrain_snapshot > "$before"; /bin/chmod 600 "$before"
    brain_snapshot > "$brain_before"; /bin/chmod 600 "$brain_before"
    "$platform/backend/.venv/bin/python" - "$token_source" "$token_target" "$contract_source" <<'PY' || fail
import os,pathlib,stat,sys
source,target,contract=map(pathlib.Path,sys.argv[1:])
for path,maximum in ((source,16384),(contract,65536)):
    meta=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(meta.st_mode) or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_uid!=os.getuid() or not 0<meta.st_size<=maximum: raise SystemExit(1)
fd=os.open(source,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
try: raw=os.read(fd,16385)
finally: os.close(fd)
if not raw or len(raw)>16384: raise SystemExit(1)
if target.exists():
    meta=target.lstat()
    if target.is_symlink() or not stat.S_ISREG(meta.st_mode) or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_uid!=os.getuid() or target.read_bytes()!=raw: raise SystemExit(1)
else:
    out=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
    try:
        view=memoryview(raw)
        while view: view=view[os.write(out,view):]
        os.fsync(out)
    except BaseException:
        os.close(out); target.unlink(missing_ok=True); raise
    else: os.close(out)
PY
    "$platform/backend/.venv/bin/python" -c '
import os,pathlib,stat,sys
target=pathlib.Path(sys.argv[1]); raw=sys.stdin.buffer.read(16385)
if not raw or len(raw)>16384: raise SystemExit(1)
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
try:
  view=memoryview(raw)
  while view: view=view[os.write(fd,view):]
  os.fsync(fd)
except BaseException:
  os.close(fd); target.unlink(missing_ok=True); raise
else: os.close(fd)
' "$owner_dsn" || fail
    echo EXECUTION_WORKER_AGENTOPS_PREPARED
    ;;
  install)
    [[ $# -eq 0 && -f "$owner_dsn" && ! -L "$owner_dsn" ]] || fail
    [[ ! -e "$receipt" && ! -L "$receipt" ]] || fail
    receipt_part="$private/.worker-provision-receipt.part"
    safe_remove_tree "$receipt_part" || fail
    receipt_published=0
    receipt_exit() {
      status="$?"; trap - ERR EXIT
      if [[ "$receipt_published" != 1 ]]; then
        safe_remove_tree "$receipt_part" >/dev/null 2>&1 || status=1
      fi
      exit "$status"
    }
    trap receipt_exit ERR EXIT
    /bin/mkdir -m 700 "$receipt_part"
    prior_state="$("$worker_supervisor" state)" || fail
    [[ "$prior_state" == absent || "$prior_state" == online || "$prior_state" == stopped ]] || fail
    printf '%s\n' "$prior_state" > "$receipt_part/state"
    /bin/chmod 600 "$receipt_part/state"
    if [[ -e "$key_manifest" || -L "$key_manifest" ]]; then
      [[ -f "$key_manifest" && ! -L "$key_manifest" \
        && "$(/usr/bin/stat -f '%Lp %Su' "$key_manifest")" == "600 agentops" ]] || fail
      /bin/cp "$key_manifest" "$receipt_part/previous.manifest"
      /bin/chmod 600 "$receipt_part/previous.manifest"
      /usr/bin/cmp -s "$key_manifest" "$receipt_part/previous.manifest" || fail
    fi
    if [[ -e "$pm2_dump" || -L "$pm2_dump" ]]; then
      [[ -f "$pm2_dump" && ! -L "$pm2_dump" ]] || fail
      prior_dump_mode="$(/usr/bin/stat -f '%Lp' "$pm2_dump")" || fail
      [[ "$prior_dump_mode" =~ ^6[04][04]$ ]] || fail
      /bin/cp "$pm2_dump" "$receipt_part/previous.dump"
      /bin/chmod 600 "$receipt_part/previous.dump"
      /usr/bin/cmp -s "$pm2_dump" "$receipt_part/previous.dump" || fail
      printf '%s\n' "$prior_dump_mode" > "$receipt_part/previous.dump.mode"
      /bin/chmod 600 "$receipt_part/previous.dump.mode"
    fi
    printf 'v1\n' > "$receipt_part/prepared"
    /bin/chmod 600 "$receipt_part/prepared"
    /bin/mv "$receipt_part" "$receipt"
    receipt_published=1
    trap - ERR EXIT
    install_ok=0
    install_exit() {
      status="$?"; trap - ERR EXIT
      if [[ "$install_ok" != 1 ]]; then rollback_worker >/dev/null 2>&1 || status=1; fi
      exit "$status"
    }
    trap install_exit ERR EXIT
    "$platform/deploy/local-execution-worker/install.sh" "$owner_dsn" || fail
    after="$private/worker-provision-nonbrain-after.json"
    nonbrain_snapshot > "$after"; /bin/chmod 600 "$after"
    /usr/bin/cmp -s "$before" "$after" || fail
    brain_after="$private/worker-provision-brain-after.txt"
    brain_snapshot > "$brain_after"; /bin/chmod 600 "$brain_after"
    /usr/bin/cmp -s "$brain_before" "$brain_after" || fail
    brain_pid="$(/usr/bin/jq -er '.pid' "$brain_before")" || fail
    brain_listener="$(/usr/sbin/lsof -nP -iTCP:9110 -sTCP:LISTEN -Fpn | /usr/bin/awk '/^p/{pid=substr($0,2)} /^n/{print pid "," substr($0,2)}')" || fail
    worker_listener="$(/usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN -Fpn | /usr/bin/awk '/^p/{pid=substr($0,2)} /^n/{print pid "," substr($0,2)}')" || fail
    [[ "$brain_listener" == "$brain_pid,127.0.0.1:9110" && "$worker_listener" =~ ^[1-9][0-9]*,127\.0\.0\.1:9120$ ]] || fail
    worker_listener_pid="${worker_listener%%,*}"
    worker_identity="$("$worker_supervisor" inspect)" || fail
    worker_pm2_pid="$(/usr/bin/jq -er '.pid' <<<"$worker_identity")" || fail
    [[ "$worker_pm2_pid" == "$worker_listener_pid" ]] || fail
    install_ok=1
    echo EXECUTION_WORKER_AGENTOPS_READY
    ;;
  commit)
    [[ $# -eq 0 && -d "$receipt" && ! -L "$receipt" \
      && "$(/usr/bin/stat -f '%Lp %Su' "$receipt")" == "700 agentops" \
      && -f "$receipt/prepared" && ! -L "$receipt/prepared" \
      && "$(<"$receipt/prepared")" == v1 ]] || fail
    "$worker_supervisor" save || fail
    /bin/chmod 600 "$pm2_dump" || fail
    [[ "$("$worker_supervisor" state)" == online \
      && -f "$pm2_dump" && ! -L "$pm2_dump" \
      && "$(/usr/bin/stat -f '%Lp %Su' "$pm2_dump")" == "600 agentops" ]] || fail
    cleanup_ephemeral || fail
    if [[ ! -e "$receipt/committed" && ! -L "$receipt/committed" ]]; then
      /bin/rm -f -- "$receipt/committed.part"
      printf 'v1\n' > "$receipt/committed.part"
      /bin/chmod 600 "$receipt/committed.part"
      /bin/mv "$receipt/committed.part" "$receipt/committed"
    fi
    [[ -f "$receipt/committed" && ! -L "$receipt/committed" \
      && "$(<"$receipt/committed")" == v1 ]] || fail
    echo EXECUTION_WORKER_AGENTOPS_COMMITTED
    ;;
  commit-status)
    [[ $# -eq 0 && -d "$receipt" && ! -L "$receipt" \
      && "$(/usr/bin/stat -f '%Lp %Su' "$receipt")" == "700 agentops" \
      && -f "$receipt/prepared" && ! -L "$receipt/prepared" \
      && "$(<"$receipt/prepared")" == v1 \
      && -f "$receipt/committed" && ! -L "$receipt/committed" \
      && "$(<"$receipt/committed")" == v1 ]] || fail
    echo EXECUTION_WORKER_AGENTOPS_COMMITTED
    ;;
  finalize)
    [[ $# -eq 0 ]] || fail
    if [[ ! -e "$receipt" && ! -L "$receipt" ]]; then
      echo EXECUTION_WORKER_AGENTOPS_FINALIZED
      exit 0
    fi
    [[ -d "$receipt" && ! -L "$receipt" \
      && "$(/usr/bin/stat -f '%Lp %Su' "$receipt")" == "700 agentops" \
      && -f "$receipt/prepared" && ! -L "$receipt/prepared" \
      && "$(<"$receipt/prepared")" == v1 \
      && -f "$receipt/committed" && ! -L "$receipt/committed" \
      && "$(<"$receipt/committed")" == v1 ]] || fail
    prior_state="$(<"$receipt/state")" || fail
    [[ "$prior_state" == absent || "$prior_state" == online || "$prior_state" == stopped ]] || fail
    if [[ -e "$receipt/previous.dump" || -L "$receipt/previous.dump" ]]; then
      [[ -f "$receipt/previous.dump" && ! -L "$receipt/previous.dump" ]] || fail
      [[ -f "$receipt/previous.dump.mode" && ! -L "$receipt/previous.dump.mode" \
        && "$(<"$receipt/previous.dump.mode")" =~ ^6[04][04]$ ]] || fail
    else
      [[ ! -e "$receipt/previous.dump.mode" && ! -L "$receipt/previous.dump.mode" ]] || fail
    fi
    safe_remove_tree "$receipt" || fail
    echo EXECUTION_WORKER_AGENTOPS_FINALIZED
    ;;
  rollback)
    [[ $# -eq 0 ]] || fail
    rollback_worker || fail
    cleanup_ephemeral || fail
    echo EXECUTION_WORKER_AGENTOPS_ROLLED_BACK
    ;;
  cleanup)
    [[ $# -eq 0 ]] || fail
    rollback_worker || fail
    cleanup_ephemeral || fail
    ;;
  *) fail ;;
esac
