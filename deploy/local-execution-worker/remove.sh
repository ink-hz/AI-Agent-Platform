#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "EXECUTION_WORKER_REMOVAL_FAILED" >&2
  exit 1
}

[[ $# -eq 3 && "$1" == /* && "$2" == /* && "$3" == "--confirm-remove-agent-execution-worker" ]] || fail
[[ "$(/usr/bin/id -un)" == "agentops" ]] || fail

owner_dsn_file="$1"
backup_file="$2"
runtime_root=/Users/agentops/AgentRuntime
private_root=/Users/agentops/AgentRuntime/private
log_root=/Users/agentops/AgentRuntime/log
plist=/Users/agentops/Library/LaunchAgents/com.orbbec.agent-execution-worker.plist
private_key=/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key
public_document=/Users/agentops/AgentRuntime/execution-worker-public.json
runtime_dsn=/Users/agentops/AgentRuntime/private/execution-worker-postgres-dsn
stdout_log=/Users/agentops/AgentRuntime/log/execution-worker.out.log
stderr_log=/Users/agentops/AgentRuntime/log/execution-worker.err.log
acceptance_root=/Users/agentops/AgentRuntime/private/execution-relay-acceptance
next_private_key=/Users/agentops/AgentRuntime/private/execution-worker-ed25519.next.key
next_public_document=/Users/agentops/AgentRuntime/execution-worker-public.next.json
next_plist=/Users/agentops/Library/LaunchAgents/com.orbbec.agent-execution-worker.next.plist
previous_private_key=/Users/agentops/AgentRuntime/private/execution-worker-ed25519.previous.key
previous_public_document=/Users/agentops/AgentRuntime/execution-worker-public.previous.json
previous_plist=/Users/agentops/Library/LaunchAgents/com.orbbec.agent-execution-worker.previous.plist
rotation_state=/Users/agentops/AgentRuntime/private/execution-worker-key-rotation-state.json
rotation_lock=/Users/agentops/AgentRuntime/private/execution-worker-key-rotation.lock
label=com.orbbec.agent-execution-worker
domain="gui/$(/usr/bin/id -u)"
psql_bin="${PLATFORM_LOCAL_POSTGRES17_PSQL:-/opt/homebrew/opt/postgresql@17/bin/psql}"
pg_restore_bin="${PLATFORM_LOCAL_POSTGRES17_PG_RESTORE:-/opt/homebrew/opt/postgresql@17/bin/pg_restore}"
python_bin="${PLATFORM_LOCAL_PYTHON3:-/usr/bin/python3}"

[[ "$backup_file" == "$private_root/agent_execution_worker.dump" ]] || fail
[[ -x "$python_bin" ]] || fail

if [[ -z "${PLATFORM_EXECUTION_WORKER_REMOVAL_LOCK_FD:-}" ]]; then
  "$python_bin" - "$rotation_lock" "$0" "$@" <<'PY' || fail
import fcntl
import os
import stat
import sys

sys.excepthook = lambda *_arguments: None
lock_path, script, *arguments = sys.argv[1:]
if not os.path.isabs(script):
    raise SystemExit(1)
parent = os.path.dirname(lock_path)
name = os.path.basename(lock_path)
parent_fd = os.open(
    parent,
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
)
try:
    parent_metadata = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or parent_metadata.st_uid != os.getuid()
    ):
        raise SystemExit(1)
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=parent_fd,
    )
finally:
    os.close(parent_fd)
try:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
    ):
        raise SystemExit(1)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.lseek(descriptor, 0, os.SEEK_SET)
    value = os.read(descriptor, 64)
    if metadata.st_size == 0:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, b"rotation-lock\n")
        os.fsync(descriptor)
    elif value != b"rotation-lock\n" or os.read(descriptor, 1):
        raise SystemExit(1)
    named = os.stat(lock_path, follow_symlinks=False)
    current = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (current.st_dev, current.st_ino):
        raise SystemExit(1)
    os.set_inheritable(descriptor, True)
    environment = dict(os.environ)
    environment["PLATFORM_EXECUTION_WORKER_REMOVAL_LOCK_FD"] = str(descriptor)
    os.execve("/bin/bash", ["/bin/bash", script, *arguments], environment)
finally:
    os.close(descriptor)
PY
  exit 0
fi

[[ "$PLATFORM_EXECUTION_WORKER_REMOVAL_LOCK_FD" =~ ^[0-9]+$ ]] || fail
"$python_bin" - "$rotation_lock" "$PLATFORM_EXECUTION_WORKER_REMOVAL_LOCK_FD" 2>/dev/null <<'PY' || fail
import fcntl
import os
import stat
import sys

path = sys.argv[1]
descriptor = int(sys.argv[2])
metadata = os.fstat(descriptor)
named = os.stat(path, follow_symlinks=False)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_uid != os.getuid()
    or (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino)
):
    raise SystemExit(1)
fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
os.lseek(descriptor, 0, os.SEEK_SET)
if os.read(descriptor, 64) != b"rotation-lock\n" or os.read(descriptor, 1):
    raise SystemExit(1)
PY

[[ -x "$psql_bin" && "$($psql_bin --version)" == psql\ \(PostgreSQL\)\ 17.* ]] || fail
[[ -x "$pg_restore_bin" && "$($pg_restore_bin --version)" == pg_restore\ \(PostgreSQL\)\ 17.* ]] || fail

secure_input() {
  "$python_bin" - "$1" "$2" <<'PY'
import os
import pathlib
import stat
import sys
from urllib.parse import unquote, urlsplit

path = pathlib.Path(sys.argv[1])
kind = sys.argv[2]
if not path.is_absolute() or not path.name or path.name in {".", ".."}:
    raise SystemExit(1)
parent_fd = os.open(
    path.parent,
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
)
try:
    parent = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o700
        or parent.st_uid != os.getuid()
    ):
        raise SystemExit(1)
    descriptor = os.open(
        path.name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_size < 1
            or (kind == "dsn" and metadata.st_size > 16_384)
        ):
            raise SystemExit(1)
        raw = os.read(descriptor, 16_385) if kind == "dsn" else b""
        if kind == "dsn" and (len(raw) > 16_384 or os.read(descriptor, 1)):
            raise SystemExit(1)
    finally:
        os.close(descriptor)
finally:
    os.close(parent_fd)
if kind == "dump":
    raise SystemExit(0)
value = raw.decode("utf-8").strip()
if not value or any(character in value for character in "\x00\n\r\t"):
    raise SystemExit(1)
parsed = urlsplit(value)
if (
    parsed.scheme not in {"postgres", "postgresql"}
    or parsed.hostname not in {"127.0.0.1", "localhost"}
    or parsed.username is None
    or parsed.password is None
    or parsed.port is None
    or parsed.path != "/postgres"
    or parsed.query
    or parsed.fragment
):
    raise SystemExit(1)
fields = (parsed.hostname, str(parsed.port), unquote(parsed.username), unquote(parsed.password))
if any(not field or any(character in field for character in "\x00\n\r\t") for field in fields):
    raise SystemExit(1)
print("\t".join(fields))
PY
}

IFS=$'\t' read -r owner_host owner_port owner_user owner_password < <(
  secure_input "$owner_dsn_file" dsn 2>/dev/null
) || fail
secure_input "$backup_file" dump >/dev/null 2>&1 || fail
backup_toc="$("$pg_restore_bin" --list "$backup_file" 2>/dev/null)" || fail
BACKUP_TOC="$backup_toc" "$python_bin" - <<'PY' >/dev/null 2>&1 || fail
import os
import re

toc = os.environ["BACKUP_TOC"].splitlines()
if ";     dbname: agent_execution_worker" not in toc:
    raise SystemExit(1)
pattern = re.compile(
    r"^[0-9]+; [0-9]+ [0-9]+ (TABLE DATA|TABLE|SCHEMA) (\S+) (\S+) (\S+)$"
)
identity = {
    tuple(match.groups())
    for line in toc
    if (match := pattern.fullmatch(line)) is not None
}
owner = "agent_execution_worker_owner"
expected = {
    ("SCHEMA", "-", "execution_worker", owner),
    *(('TABLE', 'execution_worker', table, owner) for table in (
        "schema_migrations", "local_runs", "event_outbox"
    )),
    *(('TABLE DATA', 'execution_worker', table, owner) for table in (
        "schema_migrations", "local_runs", "event_outbox"
    )),
}
if identity != expected:
    raise SystemExit(1)
PY

"$python_bin" - "$runtime_root" "$private_root" "$log_root" "$plist" \
  "$private_key" "$public_document" "$runtime_dsn" "$stdout_log" \
  "$stderr_log" "$acceptance_root" "$next_private_key" \
  "$next_public_document" "$next_plist" "$previous_private_key" \
  "$previous_public_document" "$previous_plist" "$rotation_state" \
  "$rotation_lock" <<'PY'
import os
import pathlib
import stat
import sys

(
    runtime, private, log, plist, private_key, public, dsn, stdout, stderr,
    acceptance, next_private, next_public, next_plist, previous_private,
    previous_public, previous_plist, rotation_state, rotation_lock,
) = map(pathlib.Path, sys.argv[1:])
for directory in (runtime, private, log, plist.parent):
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or directory.is_symlink()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise SystemExit(1)
for path in (plist, private_key, public, dsn):
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
    ):
        raise SystemExit(1)
for path in (
    stdout, stderr, next_private, next_public, next_plist, previous_private,
    previous_public, previous_plist, rotation_state, rotation_lock,
):
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise SystemExit(1)
if acceptance.exists() or acceptance.is_symlink():
    metadata = acceptance.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or acceptance.is_symlink()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise SystemExit(1)
    allowed = {"control.json", "state.json", "completion-paused", "dispatching-paused"}
    entries = list(acceptance.iterdir())
    if any(path.name not in allowed for path in entries):
        raise SystemExit(1)
    for path in entries:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise SystemExit(1)
PY

owner_psql() {
  PGPASSWORD="$owner_password" "$psql_bin" -X -A -t -q -v ON_ERROR_STOP=1 \
    -h "$owner_host" -p "$owner_port" -U "$owner_user" "$@"
}

# This read-only inventory rejects every cross-database dependency before any
# LaunchAgent, file, database, or role mutation.
preflight=$(owner_psql -d postgres <<'SQL'
do $preflight$
declare
  selected record;
  actual text[];
  target_database oid;
  owner_role oid;
  migrator_role oid;
begin
  if not exists (select 1 from pg_roles where rolname=current_user and rolsuper) then
    raise exception 'owner dsn role must be superuser';
  end if;
  select oid into target_database from pg_database where datname='agent_execution_worker';
  select oid into owner_role from pg_roles where rolname='agent_execution_worker_owner';
  select oid into migrator_role from pg_roles where rolname='agent_execution_worker_migrator';
  if target_database is null or owner_role is null or migrator_role is null
     or not exists (select 1 from pg_roles where rolname='agent_execution_worker_runtime') then
    raise exception 'execution worker removal inventory missing';
  end if;
  for selected in
    select rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
           rolreplication,rolbypassrls,rolinherit,rolconnlimit,
           rolvaliduntil,rolconfig
    from pg_roles
    where rolname=any(array[
      'agent_execution_worker_owner','agent_execution_worker_migrator','agent_execution_worker_runtime'
    ])
  loop
    if selected.rolcanlogin <> (selected.rolname='agent_execution_worker_runtime')
       or selected.rolsuper or selected.rolcreatedb or selected.rolcreaterole
       or selected.rolreplication or selected.rolbypassrls or selected.rolinherit
       or selected.rolconnlimit <> -1 or selected.rolvaliduntil is not null
       or selected.rolconfig is not null then
      raise exception 'execution worker role attribute mismatch';
    end if;
  end loop;
  if exists (
    select 1 from pg_database database
    where database.oid=target_database
      and (
        database.datdba <> owner_role
        or database.datistemplate
        or not database.datallowconn
        or pg_encoding_to_char(database.encoding) <> 'UTF8'
      )
  ) then
    raise exception 'execution worker target database identity or acl mismatch';
  end if;
  select array_agg(
           concat(coalesce(grantee.rolname,'PUBLIC'),':',acl.privilege_type,':',acl.is_grantable)
           order by coalesce(grantee.rolname,'PUBLIC'),acl.privilege_type
         )
    into actual
    from pg_database database,
    lateral aclexplode(coalesce(database.datacl,acldefault('d',database.datdba))) acl
    left join pg_roles grantee on grantee.oid=acl.grantee
   where database.oid=target_database;
  if actual is distinct from array[
    'agent_execution_worker_migrator:CONNECT:f',
    'agent_execution_worker_owner:CONNECT:f',
    'agent_execution_worker_owner:CREATE:f',
    'agent_execution_worker_owner:TEMPORARY:f',
    'agent_execution_worker_runtime:CONNECT:f'
  ] then
    raise exception 'execution worker target database identity or acl mismatch';
  end if;
  if not exists (
    select 1 from pg_auth_members membership
    where membership.roleid=owner_role and membership.member=migrator_role
      and not membership.admin_option and not membership.inherit_option
      and membership.set_option
      and membership.grantor=(select oid from pg_roles where rolname=current_user)
  ) or exists (
    select 1 from pg_auth_members membership
    where (
      membership.roleid=any(array(
        select oid from pg_roles where rolname=any(array[
          'agent_execution_worker_owner','agent_execution_worker_migrator','agent_execution_worker_runtime'
        ])))
      or membership.member=any(array(
        select oid from pg_roles where rolname=any(array[
          'agent_execution_worker_owner','agent_execution_worker_migrator','agent_execution_worker_runtime'
        ])))
    ) and not (
      membership.roleid=owner_role and membership.member=migrator_role
      and not membership.admin_option and not membership.inherit_option
      and membership.set_option
      and membership.grantor=(select oid from pg_roles where rolname=current_user)
    )
  ) then
    raise exception 'execution worker role membership mismatch';
  end if;
  if exists (
    select 1
    from pg_shdepend dependency
    join pg_roles role
      on dependency.refclassid='pg_authid'::regclass and dependency.refobjid=role.oid
    where role.rolname=any(array[
      'agent_execution_worker_owner','agent_execution_worker_migrator','agent_execution_worker_runtime'
    ])
      and dependency.dbid <> target_database
      and not (
        dependency.dbid=0
        and dependency.classid='pg_database'::regclass
        and dependency.objid=target_database
      )
      and not (
        dependency.dbid=0
        and dependency.classid='pg_auth_members'::regclass
        and dependency.objid in (
          select oid from pg_auth_members
          where roleid=owner_role and member=migrator_role
        )
      )
  ) then
    raise exception 'execution worker cross-database dependency';
  end if;
end
$preflight$;
select 'EXECUTION_WORKER_REMOVAL_PREFLIGHT_OK';
SQL
) || fail
[[ "$preflight" == "EXECUTION_WORKER_REMOVAL_PREFLIGHT_OK" ]] || fail

if /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
  /bin/launchctl bootout "$domain/$label" >/dev/null 2>&1 || fail
fi

owner_psql -d postgres >/dev/null <<'SQL'
drop database agent_execution_worker with (force);
drop role agent_execution_worker_runtime;
drop role agent_execution_worker_migrator;
drop role agent_execution_worker_owner;
SQL

/bin/rm -f -- "$plist"
/bin/rm -f -- "$private_key"
/bin/rm -f -- "$public_document"
/bin/rm -f -- "$runtime_dsn"
/bin/rm -f -- "$stdout_log"
/bin/rm -f -- "$stderr_log"
/bin/rm -f -- "$next_private_key"
/bin/rm -f -- "$next_public_document"
/bin/rm -f -- "$next_plist"
/bin/rm -f -- "$previous_private_key"
/bin/rm -f -- "$previous_public_document"
/bin/rm -f -- "$previous_plist"
/bin/rm -f -- "$rotation_state"
for residual in control.json state.json completion-paused dispatching-paused; do
  if [[ -e "$acceptance_root/$residual" ]]; then
    /bin/rm -f -- "$acceptance_root/$residual"
  fi
done
if [[ -d "$acceptance_root" ]]; then
  /bin/rmdir -- "$acceptance_root" || fail
fi

echo "EXECUTION_WORKER_REMOVED"
