#!/bin/bash
set -eEuo pipefail
umask 077

fail() { echo EXECUTION_WORKER_PROVISION_FAILED >&2; exit 1; }
[[ $# -eq 0 && "$(/usr/bin/id -un)" == "neo" ]] || fail

repository="$(cd "$(dirname "$0")/../.." && pwd)"
metabot_repository=/Users/neo/Developer/work/Orbbec-Agent-Team
git=/usr/bin/git
psql_bin=/opt/homebrew/opt/postgresql@17/bin/psql
psql_socket=/Users/neo/FlywheelData/socket
agentops_runtime=/Users/agentops/AgentRuntime
agentops_helper=/Users/agentops/AgentRuntime/deploy-tools/provision-agentops.sh
agentops_pm2_tool=/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh
agentops_path=/Users/agentops/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
[[ -x "$git" && -x "$psql_bin" && -S "$psql_socket/.s.PGSQL.5432" ]] || fail
[[ "$($psql_bin --version)" == psql\ \(PostgreSQL\)\ 17.* ]] || fail
[[ "$($git -C "$repository" rev-parse --show-toplevel)" == "$repository" ]] || fail
[[ "$($git -C "$metabot_repository" rev-parse --show-toplevel)" == "$metabot_repository" ]] || fail

run_agentops() {
  /usr/bin/sudo -n -u agentops /usr/bin/env -i \
    HOME=/Users/agentops USER=agentops LOGNAME=agentops PATH="$agentops_path" \
    "$@"
}

install_agentops_tool() {
  target="$1" expected="$2" source="$3"
  run_agentops /usr/bin/python3 -c '
import hashlib,os,pathlib,stat,sys
target=pathlib.Path(sys.argv[1]); expected=sys.argv[2]; runtime=pathlib.Path(sys.argv[4]); raw=sys.stdin.buffer.read(262145)
if not raw or len(raw)>262144 or hashlib.sha256(raw).hexdigest()!=expected: raise SystemExit(1)
deploy_tools=runtime/"deploy-tools"
allowed={deploy_tools/"provision-agentops.sh",deploy_tools/"reliability"/"sanitized-pm2.sh"}
if target not in allowed: raise SystemExit(1)
home=runtime.parent; home_meta=home.lstat()
if home.is_symlink() or not stat.S_ISDIR(home_meta.st_mode) or home_meta.st_uid!=os.getuid() or stat.S_IMODE(home_meta.st_mode)&0o022: raise SystemExit(1)
directories=[runtime,deploy_tools]
if target.parent!=deploy_tools: directories.append(target.parent)
for directory in directories:
 try: directory.mkdir(mode=0o700)
 except FileExistsError: pass
 meta=directory.lstat()
 if directory.is_symlink() or not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=os.getuid() or stat.S_IMODE(meta.st_mode)!=0o700: raise SystemExit(1)
if target.exists() or target.is_symlink():
 meta=target.lstat()
 if target.is_symlink() or not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid(): raise SystemExit(1)
part=target.with_name("."+target.name+".part")
try:
 fd=os.open(part,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o700)
 try:
  view=memoryview(raw)
  while view: view=view[os.write(fd,view):]
  os.fsync(fd)
 finally: os.close(fd)
 os.replace(part,target); os.chmod(target,0o700)
 parent_fd=os.open(target.parent,os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0))
 try: os.fsync(parent_fd)
 finally: os.close(parent_fd)
finally:
 try: os.unlink(part)
 except FileNotFoundError: pass
' "$target" "$expected" "$source" "$agentops_runtime" < "$source"
}

release_archive=""
status_file=""
helper_source=""
backup=""
hba=""
hba_dir=""
hba_expected=""
hba_lock=/Users/neo/FlywheelData/.agent-worker-hba.lock
lock_acquired=0
agentops_staged=0
role_created=0
hba_changed=0
worker_pending=0
worker_committed=0
success=0
cleanup_running=0
temp_password=""

cleanup() {
  status="$?"
  [[ "$cleanup_running" == 0 ]] || exit 1
  cleanup_running=1
  trap - ERR EXIT
  final_ok=0
  if [[ "$status" == 0 && "$success" == 1 && "$worker_pending" == 1 ]]; then
    if write_hba permanent >/dev/null 2>&1 &&
       reload_hba >/dev/null 2>&1 && validate_hba 0 >/dev/null 2>&1; then
      final_ok=1
    else
      status=1
    fi
  fi

  if [[ "$role_created" == 1 ]]; then
    if ! printf 'drop role if exists %s;\n' "$temp_role" | \
      "${psql_command[@]}" -X -v ON_ERROR_STOP=1 -d postgres >/dev/null 2>&1; then
      status=1
      final_ok=0
    fi
  fi

  if [[ "$final_ok" != 1 ]]; then
    if [[ "$agentops_staged" == 1 ]]; then
      run_agentops "$agentops_helper" rollback >/dev/null 2>&1 || status=1
    fi
    if [[ "$hba_changed" == 1 ]]; then
      if ! restore_hba >/dev/null 2>&1 || ! reload_hba >/dev/null 2>&1 ||
         [[ "$("${psql_command[@]}" -XAt -v ON_ERROR_STOP=1 -d postgres -c \
           'select count(*) from pg_hba_file_rules where error is not null')" != 0 ]]; then
        status=1
        echo EXECUTION_WORKER_HBA_BACKUP_PRESERVED >&2
      fi
    fi
  fi

  if [[ "$final_ok" == 1 && "$status" == 0 ]]; then
    if ! run_agentops "$agentops_helper" commit >/dev/null 2>&1; then
      status=1
      final_ok=0
      run_agentops "$agentops_helper" rollback >/dev/null 2>&1 || status=1
      if [[ "$hba_changed" == 1 ]]; then
        restore_hba >/dev/null 2>&1 || status=1
        reload_hba >/dev/null 2>&1 || status=1
      fi
    else
      worker_committed=1
    fi
  elif [[ "$agentops_staged" == 1 ]]; then
    run_agentops "$agentops_helper" cleanup >/dev/null 2>&1 || status=1
  fi

  if [[ "$status" == 0 && "$final_ok" == 1 && "$worker_committed" == 1 ]]; then
    if ! run_agentops "$agentops_helper" finalize >/dev/null 2>&1; then
      temp_password=""
      echo EXECUTION_WORKER_FINALIZE_RETRY_REQUIRED >&2
      exit 1
    fi
  fi

  backup_removable=1
  if [[ -n "$backup" && -e "$backup" ]]; then
    if [[ "$final_ok" == 1 ]] || /usr/bin/cmp -s "$backup" "$hba"; then
      /bin/rm -f -- "$backup" || status=1
    else
      backup_removable=0
      status=1
    fi
  fi
  if [[ "$backup_removable" == 1 ]]; then
    [[ -z "$hba_expected" || ! -e "$hba_expected" ]] || /bin/rm -f -- "$hba_expected" || status=1
  fi
  [[ -z "$status_file" || ! -e "$status_file" ]] || /bin/rm -f -- "$status_file" || status=1
  [[ -z "$helper_source" || ! -e "$helper_source" ]] || /bin/rm -f -- "$helper_source" || status=1
  [[ -z "$release_archive" || ! -e "$release_archive" ]] || /bin/rm -f -- "$release_archive" || status=1
  if [[ "$lock_acquired" == 1 ]]; then
    /bin/rmdir "$hba_lock" >/dev/null 2>&1 || status=1
  fi
  temp_password=""
  if [[ "$status" == 0 && "$final_ok" == 1 && "$backup_removable" == 1 ]]; then
    echo EXECUTION_WORKER_PROVISION_OK
    exit 0
  fi
  exit 1
}
trap cleanup ERR EXIT

release_sha="$($git -C "$repository" rev-parse HEAD)" || fail
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
status_file="$(/usr/bin/mktemp /Users/neo/FlywheelData/.agent-platform-status.XXXXXX)" || fail
$git -C "$repository" status --porcelain=v1 -z --untracked-files=all > "$status_file" || fail
/usr/bin/python3 - "$status_file" <<'PY' || fail
import pathlib,sys
allowed={
 ".superpowers/sdd/task-2-report.md", ".superpowers/sdd/task-3-report.md",
 ".superpowers/sdd/task-4-report.md", ".superpowers/sdd/task-6-report.md",
}
raw=pathlib.Path(sys.argv[1]).read_bytes()
entries=[item for item in raw.split(b"\0") if item]
for entry in entries:
    if len(entry)<4: raise SystemExit(1)
    path=entry[3:].decode("utf-8")
    if path not in allowed: raise SystemExit(1)
PY
/bin/rm -f -- "$status_file"
status_file=""
/bin/mkdir "$hba_lock" || fail
/bin/chmod 700 "$hba_lock"
lock_acquired=1

helper_source="$(/usr/bin/mktemp /Users/neo/FlywheelData/.agentops-helper.XXXXXX)" || fail
$git -C "$repository" show "$release_sha:deploy/local-execution-worker/provision-agentops.sh" > "$helper_source" || fail
helper_sha="$(/usr/bin/shasum -a 256 "$helper_source" | /usr/bin/awk '{print $1}')"
[[ "$helper_sha" =~ ^[0-9a-f]{64}$ ]] || fail
install_agentops_tool "$agentops_helper" "$helper_sha" "$helper_source" || fail
/bin/rm -f -- "$helper_source"
helper_source=""

metabot_release_sha="$($git -C "$metabot_repository" rev-parse HEAD)" || fail
[[ "$metabot_release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
helper_source="$(/usr/bin/mktemp /Users/neo/FlywheelData/.agentops-pm2-tool.XXXXXX)" || fail
$git -C "$metabot_repository" show \
  "$metabot_release_sha:scripts/reliability/sanitized-pm2.sh" > "$helper_source" || fail
helper_sha="$(/usr/bin/shasum -a 256 "$helper_source" | /usr/bin/awk '{print $1}')"
[[ "$helper_sha" =~ ^[0-9a-f]{64}$ ]] || fail
install_agentops_tool "$agentops_pm2_tool" "$helper_sha" "$helper_source" || fail
/bin/rm -f -- "$helper_source"
helper_source=""

release_archive="$(/usr/bin/mktemp /Users/neo/FlywheelData/.agent-platform-release.XXXXXX)" || fail
$git -C "$repository" archive --format=tar "$release_sha" > "$release_archive" || fail
archive_sha="$(/usr/bin/shasum -a 256 "$release_archive" | /usr/bin/awk '{print $1}')"
[[ "$archive_sha" =~ ^[0-9a-f]{64}$ ]] || fail
run_agentops "$agentops_helper" stage "$release_sha" "$archive_sha" < "$release_archive" || fail
agentops_staged=1

psql_command=(/usr/bin/env -u PGHOST -u PGPORT -u PGUSER -u PGDATABASE \
  -u PGSERVICE -u PGPASSWORD "$psql_bin" -h "$psql_socket" -p 5432 -U neo)
[[ "$("${psql_command[@]}" -XAt -v ON_ERROR_STOP=1 -d postgres -c \
  "select current_user || ':' || current_setting('port')")" == "neo:5432" ]] || fail
port="$("${psql_command[@]}" -XAt -v ON_ERROR_STOP=1 -d postgres -c 'show port')" || fail
hba="$("${psql_command[@]}" -XAt -v ON_ERROR_STOP=1 -d postgres -c 'show hba_file')" || fail
[[ "$port" =~ ^[0-9]{2,5}$ && "$hba" == /* && -f "$hba" && ! -L "$hba" ]] || fail
hba_dir="$(/usr/bin/dirname "$hba")"
[[ -d "$hba_dir" && ! -L "$hba_dir" ]] || fail
[[ "$("${psql_command[@]}" -XAt -v ON_ERROR_STOP=1 -d postgres -c \
  'select count(*) from pg_hba_file_rules where error is not null')" == 0 ]] || fail
backup="$(/usr/bin/mktemp "$hba_dir/.pg_hba.agent-worker.XXXXXX")" || fail
/bin/cp -p "$hba" "$backup"; /bin/chmod 600 "$backup"
hba_expected="$hba_dir/.pg_hba.agent-worker.expected"
[[ ! -e "$hba_expected" && ! -L "$hba_expected" ]] || fail
managed_begin="# BEGIN ORBBEC AGENT EXECUTION WORKER"
managed_end="# END ORBBEC AGENT EXECUTION WORKER"
temp_role="agent_execution_bootstrap_$(/usr/bin/openssl rand -hex 8)"
temp_password="$(/usr/bin/openssl rand -hex 32)"
[[ "$temp_role" =~ ^agent_execution_bootstrap_[0-9a-f]{16}$ && "$temp_password" =~ ^[0-9a-f]{64}$ ]] || fail

write_hba() {
  mode="$1"
  /usr/bin/python3 - "$hba" "$backup" "$hba_expected" "$managed_begin" "$managed_end" "$mode" "$temp_role" <<'PY'
import hashlib,os,pathlib,stat,sys
path,backup,state=map(pathlib.Path,sys.argv[1:4]); begin,end,mode,role=sys.argv[4:]
meta=path.lstat(); original=path.read_bytes(); backup_raw=backup.read_bytes()
if path.is_symlink() or not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid() or b"\0" in original: raise SystemExit(1)
if mode=="temporary" and original!=backup_raw: raise SystemExit(1)
if mode=="permanent":
    expected=state.read_text(encoding="ascii").strip()
    if hashlib.sha256(original).hexdigest()!=expected: raise SystemExit(1)
raw=original; start=raw.find((begin+"\n").encode()); finish=raw.find((end+"\n").encode())
if (start==-1)!=(finish==-1) or (start!=-1 and (raw.find((begin+"\n").encode(),start+1)!=-1 or raw.find((end+"\n").encode(),finish+1)!=-1 or finish<start)): raise SystemExit(1)
if start!=-1: raw=raw[:start]+raw[finish+len(end)+1:]
lines=[begin]
if mode=="temporary": lines.append(f"host postgres {role} 127.0.0.1/32 scram-sha-256")
lines += ["host agent_execution_worker agent_execution_worker_runtime 127.0.0.1/32 scram-sha-256",end]
raw=("\n".join(lines)+"\n").encode()+raw
digest=hashlib.sha256(raw).hexdigest()+"\n"
state_part=state.with_name("."+state.name+".part")
try: os.unlink(state_part)
except FileNotFoundError: pass
fd=os.open(state_part,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
try:
 view=memoryview(digest.encode("ascii"))
 while view: view=view[os.write(fd,view):]
 os.fsync(fd)
finally: os.close(fd)
part=path.with_name("."+path.name+".agent-worker.part")
path_published=False; state_published=False
try:
 fd=os.open(part,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),stat.S_IMODE(meta.st_mode))
 try:
  view=memoryview(raw)
  while view: view=view[os.write(fd,view):]
  os.fsync(fd)
 finally: os.close(fd)
 current=path.lstat()
 if (current.st_dev,current.st_ino)!=(meta.st_dev,meta.st_ino) or path.read_bytes()!=original: raise SystemExit(1)
 os.replace(part,path); path_published=True; os.chmod(path,stat.S_IMODE(meta.st_mode))
 directory=os.open(path.parent,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
 try: os.fsync(directory)
 finally: os.close(directory)
 os.replace(state_part,state); state_published=True
 directory=os.open(path.parent,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
 try: os.fsync(directory)
 finally: os.close(directory)
except BaseException:
 try: os.unlink(part)
 except FileNotFoundError: pass
 if path_published and not state_published:
  recovery=path.with_name("."+path.name+".agent-worker.recover.part")
  try:
   recovery_fd=os.open(recovery,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),stat.S_IMODE(meta.st_mode))
   try:
    view=memoryview(original)
    while view: view=view[os.write(recovery_fd,view):]
    os.fsync(recovery_fd)
   finally: os.close(recovery_fd)
   if path.read_bytes()!=raw: raise SystemExit(1)
   os.replace(recovery,path); os.chmod(path,stat.S_IMODE(meta.st_mode))
  except BaseException:
   try: os.unlink(recovery)
   except FileNotFoundError: pass
   try: os.replace(state_part,state)
   except FileNotFoundError: pass
   raise
 try: os.unlink(state_part)
 except FileNotFoundError: pass
 raise
PY
}

restore_hba() {
  /usr/bin/python3 - "$hba" "$backup" "$hba_expected" <<'PY'
import hashlib,os,pathlib,stat,sys
path,backup,state=map(pathlib.Path,sys.argv[1:]); meta=path.lstat(); backup_meta=backup.lstat()
if path.is_symlink() or backup.is_symlink() or not stat.S_ISREG(meta.st_mode) or not stat.S_ISREG(backup_meta.st_mode): raise SystemExit(1)
current=path.read_bytes(); raw=backup.read_bytes()
if current==raw: raise SystemExit(0)
if not state.is_file() or hashlib.sha256(current).hexdigest()!=state.read_text(encoding="ascii").strip(): raise SystemExit(1)
part=path.with_name("."+path.name+".agent-worker.restore.part")
try:
 fd=os.open(part,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),stat.S_IMODE(meta.st_mode))
 try:
  view=memoryview(raw)
  while view: view=view[os.write(fd,view):]
  os.fsync(fd)
 finally: os.close(fd)
 current_meta=path.lstat()
 if (current_meta.st_dev,current_meta.st_ino)!=(meta.st_dev,meta.st_ino) or path.read_bytes()!=current: raise SystemExit(1)
 os.replace(part,path); os.chmod(path,stat.S_IMODE(meta.st_mode))
except BaseException:
 try: os.unlink(part)
 except FileNotFoundError: pass
 raise
if path.read_bytes()!=raw: raise SystemExit(1)
PY
}

reload_hba() {
  [[ "$("${psql_command[@]}" -XAt -v ON_ERROR_STOP=1 -d postgres -c 'select pg_reload_conf()')" == t ]]
}

validate_hba() {
  expected_temporary="$1"
  result="$("${psql_command[@]}" -XAt -v ON_ERROR_STOP=1 -d postgres -c "select concat(
    count(*) filter (where error is not null), ':',
    count(*) filter (where database=array['agent_execution_worker'] and user_name=array['agent_execution_worker_runtime'] and address='127.0.0.1' and netmask='255.255.255.255' and auth_method='scram-sha-256'), ':',
    count(*) filter (where database=array['postgres'] and user_name=array['$temp_role'] and address='127.0.0.1' and netmask='255.255.255.255' and auth_method='scram-sha-256')
    ) from pg_hba_file_rules")" || return 1
  [[ "$result" == "0:1:$expected_temporary" ]]
}

role_created=1
printf "set password_encryption='scram-sha-256'; create role %s login superuser password '%s';\n" \
  "$temp_role" "$temp_password" | "${psql_command[@]}" -X -v ON_ERROR_STOP=1 -d postgres >/dev/null || fail
hba_changed=1
write_hba temporary || fail
reload_hba || fail
validate_hba 1 || fail
owner_dsn="postgresql://$temp_role:$temp_password@127.0.0.1:$port/postgres"
printf '%s\n' "$owner_dsn" | run_agentops "$agentops_helper" prepare || fail
owner_dsn=""
run_agentops "$agentops_helper" install || fail
worker_pending=1
success=1
