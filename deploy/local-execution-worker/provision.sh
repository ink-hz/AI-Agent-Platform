#!/bin/bash
set -eEuo pipefail
umask 077

fail() { echo EXECUTION_WORKER_PROVISION_FAILED >&2; exit 1; }
[[ $# -eq 0 && "$(/usr/bin/id -un)" == "neo" ]] || fail
repository="$(cd "$(dirname "$0")/../.." && pwd)"
agentops_helper="$repository/deploy/local-execution-worker/provision-agentops.sh"
psql=/opt/homebrew/opt/postgresql@17/bin/psql
[[ -x "$agentops_helper" && -x "$psql" && "$($psql --version)" == psql\ \(PostgreSQL\)\ 17.* ]] || fail

temp_role="agent_execution_bootstrap_$(/usr/bin/openssl rand -hex 8)"
temp_password="$(/usr/bin/openssl rand -hex 32)"
[[ "$temp_role" =~ ^agent_execution_bootstrap_[0-9a-f]{16}$ && "$temp_password" =~ ^[0-9a-f]{64}$ ]] || fail
port="$($psql -X -A -t -v ON_ERROR_STOP=1 -d postgres -c 'show port')" || fail
hba="$($psql -X -A -t -v ON_ERROR_STOP=1 -d postgres -c 'show hba_file')" || fail
[[ "$port" =~ ^[0-9]{2,5}$ && "$hba" == /* && -f "$hba" && ! -L "$hba" ]] || fail
hba_dir="$(/usr/bin/dirname "$hba")"
[[ -d "$hba_dir" && ! -L "$hba_dir" ]] || fail
[[ "$($psql -X -A -t -v ON_ERROR_STOP=1 -d postgres -c \
  'select count(*) from pg_hba_file_rules where error is not null')" == 0 ]] || fail
backup="$(/usr/bin/mktemp "$hba_dir/.pg_hba.agent-worker.XXXXXX")" || fail
/bin/chmod 600 "$backup"
/bin/cp -p "$hba" "$backup"
/bin/chmod 600 "$backup"
managed_begin="# BEGIN ORBBEC AGENT EXECUTION WORKER"
managed_end="# END ORBBEC AGENT EXECUTION WORKER"
success=0
role_created=0
hba_changed=0

write_hba() {
  mode="$1"
  /usr/bin/python3 - "$hba" "$managed_begin" "$managed_end" "$mode" "$temp_role" <<'PY'
import os, pathlib, stat, sys
path = pathlib.Path(sys.argv[1]); begin, end, mode, role = sys.argv[2:]
meta = path.lstat()
if path.is_symlink() or not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid(): raise SystemExit(1)
original = path.read_bytes()
raw = original
if b"\0" in raw: raise SystemExit(1)
start = raw.find((begin + "\n").encode()); finish = raw.find((end + "\n").encode())
if (start == -1) != (finish == -1) or (start != -1 and (raw.find((begin+"\n").encode(), start+1) != -1 or raw.find((end+"\n").encode(), finish+1) != -1 or finish < start)): raise SystemExit(1)
if start != -1: raw = raw[:start] + raw[finish + len(end) + 1:]
if mode != "restore":
    lines = [begin]
    if mode == "temporary": lines.append(f"host postgres {role} 127.0.0.1/32 scram-sha-256")
    lines += ["host agent_execution_worker agent_execution_worker_runtime 127.0.0.1/32 scram-sha-256", end]
    raw = ("\n".join(lines) + "\n").encode() + raw
part = path.with_name("." + path.name + ".agent-worker.part")
try:
    fd = os.open(part, os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0), stat.S_IMODE(meta.st_mode))
    try:
        view = memoryview(raw)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    current = path.lstat()
    if (current.st_dev,current.st_ino) != (meta.st_dev,meta.st_ino) or path.read_bytes() != original:
        raise SystemExit(1)
    os.replace(part, path); os.chmod(path, stat.S_IMODE(meta.st_mode))
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(directory)
    finally: os.close(directory)
except BaseException:
    try: os.unlink(part)
    except FileNotFoundError: pass
    raise
PY
}

reload_hba() {
  [[ "$($psql -X -A -t -v ON_ERROR_STOP=1 -d postgres -c 'select pg_reload_conf()')" == t ]]
}

validate_hba() {
  expected_temporary="$1"
  result="$($psql -X -A -t -v ON_ERROR_STOP=1 -d postgres -c "select concat(
    count(*) filter (where error is not null), ':',
    count(*) filter (where database=array['agent_execution_worker'] and user_name=array['agent_execution_worker_runtime'] and address='127.0.0.1' and netmask='255.255.255.255' and auth_method='scram-sha-256'), ':',
    count(*) filter (where database=array['postgres'] and user_name=array['$temp_role'] and address='127.0.0.1' and netmask='255.255.255.255' and auth_method='scram-sha-256')
    ) from pg_hba_file_rules")" || return 1
  [[ "$result" == "0:1:$expected_temporary" ]]
}

restore_hba() {
  /usr/bin/python3 - "$hba" "$backup" <<'PY'
import os, pathlib, stat, sys
path, backup = map(pathlib.Path, sys.argv[1:])
path_meta = path.lstat(); backup_meta = backup.lstat()
if (path.is_symlink() or backup.is_symlink()
    or not stat.S_ISREG(path_meta.st_mode) or not stat.S_ISREG(backup_meta.st_mode)
    or path_meta.st_uid != os.getuid() or backup_meta.st_uid != os.getuid()):
    raise SystemExit(1)
raw = backup.read_bytes()
part = path.with_name("." + path.name + ".agent-worker.restore.part")
try:
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), stat.S_IMODE(path_meta.st_mode))
    try:
        view = memoryview(raw)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    current = path.lstat()
    if (current.st_dev, current.st_ino) != (path_meta.st_dev, path_meta.st_ino):
        raise SystemExit(1)
    os.replace(part, path)
    os.chmod(path, stat.S_IMODE(path_meta.st_mode))
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(directory)
    finally: os.close(directory)
except BaseException:
    try: os.unlink(part)
    except FileNotFoundError: pass
    raise
if path.read_bytes() != raw:
    raise SystemExit(1)
PY
}

cleanup() {
  status="$?"
  backup_removable=1
  trap - ERR EXIT
  /usr/bin/sudo -n -u agentops "$agentops_helper" cleanup >/dev/null 2>&1 || status=1
  if [[ "$hba_changed" == 1 ]]; then
    if [[ "$success" == 1 ]]; then
      if ! write_hba permanent >/dev/null 2>&1 ||
         ! reload_hba >/dev/null 2>&1 ||
         ! validate_hba 0 >/dev/null 2>&1; then
        status=1
        if ! restore_hba >/dev/null 2>&1 ||
           ! /usr/bin/cmp -s "$backup" "$hba" ||
           ! reload_hba >/dev/null 2>&1 ||
           [[ "$($psql -X -A -t -v ON_ERROR_STOP=1 -d postgres -c \
             'select count(*) from pg_hba_file_rules where error is not null')" != 0 ]]; then
          status=1
          backup_removable=0
        fi
      fi
    else
      if ! restore_hba >/dev/null 2>&1 ||
         ! /usr/bin/cmp -s "$backup" "$hba" ||
         ! reload_hba >/dev/null 2>&1 ||
         [[ "$($psql -X -A -t -v ON_ERROR_STOP=1 -d postgres -c \
           'select count(*) from pg_hba_file_rules where error is not null')" != 0 ]]; then
        status=1
        backup_removable=0
      fi
    fi
  fi
  if [[ "$role_created" == 1 ]]; then
    printf 'drop role if exists %s;\n' "$temp_role" | \
      $psql -X -v ON_ERROR_STOP=1 -d postgres >/dev/null 2>&1 || status=1
  fi
  if [[ "$backup_removable" == 1 ]]; then
    /bin/rm -f -- "$backup" || status=1
  else
    echo EXECUTION_WORKER_HBA_BACKUP_PRESERVED >&2
  fi
  temp_password=""
  exit "$status"
}
trap cleanup ERR EXIT

role_created=1
printf "set password_encryption='scram-sha-256'; create role %s login superuser password '%s';\n" \
  "$temp_role" "$temp_password" | \
  $psql -X -v ON_ERROR_STOP=1 -d postgres >/dev/null || fail
hba_changed=1
write_hba temporary || fail
reload_hba || fail
validate_hba 1 || fail
owner_dsn="postgresql://$temp_role:$temp_password@127.0.0.1:$port/postgres"
printf '%s\n' "$owner_dsn" | /usr/bin/sudo -n -u agentops "$agentops_helper" prepare || fail
owner_dsn=""
/usr/bin/sudo -n -u agentops "$agentops_helper" install || fail
success=1
echo EXECUTION_WORKER_PROVISION_OK
