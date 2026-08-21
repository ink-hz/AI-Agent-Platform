#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "EXECUTION_WORKER_DATABASE_BOOTSTRAP_FAILED" >&2
  exit 1
}

[[ $# -eq 2 && "$1" == /* && "$2" == /* ]] || fail
owner_dsn_file="$1"
runtime_dsn_file="$2"
runtime_private_dir="$(/usr/bin/dirname "$runtime_dsn_file")"
script_dir="$(cd "$(dirname "$0")" && pwd)"
schema_file="$script_dir/../../backend/app/execution_relay/worker_schema.sql"
psql_bin="${PLATFORM_LOCAL_POSTGRES17_PSQL:-/opt/homebrew/opt/postgresql@17/bin/psql}"
python_bin="${PLATFORM_LOCAL_PYTHON3:-/usr/bin/python3}"

[[ "$(/usr/bin/id -un)" == "agentops" ]] || fail
[[ -x "$psql_bin" && "$($psql_bin --version)" == psql\ \(PostgreSQL\)\ 17.* ]] || fail
[[ -x "$python_bin" ]] || fail
[[ -f "$schema_file" && ! -L "$schema_file" ]] || fail

secure_dsn_fields() {
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
directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
parent_fd = os.open(path.parent, directory_flags)
try:
    parent = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_uid != os.getuid():
        raise SystemExit(1)
    descriptor = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid() or metadata.st_size > 16384:
            raise SystemExit(1)
        raw = os.read(descriptor, 16385)
        if len(raw) > 16384 or os.read(descriptor, 1):
            raise SystemExit(1)
    finally:
        os.close(descriptor)
finally:
    os.close(parent_fd)
value = raw.decode("utf-8").strip()
if not value or any(character in value for character in "\x00\n\r\t"):
    raise SystemExit(1)
parsed = urlsplit(value)
expected_path = "/postgres" if kind == "owner" else "/agent_execution_worker"
expected_user = None if kind == "owner" else "agent_execution_worker_runtime"
if (
    parsed.scheme not in {"postgres", "postgresql"}
    or parsed.hostname not in {"127.0.0.1", "localhost"}
    or parsed.username is None
    or parsed.password is None
    or parsed.port is None
    or parsed.path != expected_path
    or parsed.query
    or parsed.fragment
    or (expected_user is not None and unquote(parsed.username) != expected_user)
):
    raise SystemExit(1)
fields = (parsed.hostname, str(parsed.port), unquote(parsed.username), unquote(parsed.password))
if any(not field or any(character in field for character in "\x00\n\r\t") for field in fields):
    raise SystemExit(1)
if kind == "runtime" and not all(character in "0123456789abcdef" for character in fields[3]) or (kind == "runtime" and len(fields[3]) != 64):
    raise SystemExit(1)
print("\t".join(fields))
PY
}

IFS=$'\t' read -r owner_host owner_port owner_user owner_password < <(
  secure_dsn_fields "$owner_dsn_file" owner 2>/dev/null
) || fail
[[ -n "$owner_host" && -n "$owner_port" && -n "$owner_user" && -n "$owner_password" ]] || fail

runtime_host=127.0.0.1
runtime_password=""
if [[ -e "$runtime_dsn_file" || -L "$runtime_dsn_file" ]]; then
  IFS=$'\t' read -r _runtime_host runtime_port runtime_user runtime_password < <(
    secure_dsn_fields "$runtime_dsn_file" runtime 2>/dev/null
  ) || fail
  [[ "$runtime_port" == "$owner_port" && "$runtime_user" == "agent_execution_worker_runtime" ]] || fail
else
  [[ -d "$runtime_private_dir" && ! -L "$runtime_private_dir" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$runtime_private_dir")" == "700 agentops" ]] || fail
  runtime_password="$(/usr/bin/openssl rand -hex 32)"
fi
[[ "$runtime_password" =~ ^[0-9a-f]{64}$ ]] || fail

owner_psql() {
  PGPASSWORD="$owner_password" "$psql_bin" -X -v ON_ERROR_STOP=1 \
    -h "$owner_host" -p "$owner_port" -U "$owner_user" "$@"
}

cleanup_needed=0
cleanup_membership_once() {
  owner_psql -d postgres >/dev/null 2>&1 <<'SQL'
select format('revoke agent_execution_worker_migrator from %I', current_user) \gexec
do $membership_cleanup_verification$
declare
  membership_count integer;
begin
  select count(*) into membership_count
    from pg_auth_members membership
    join pg_roles granted_role on granted_role.oid=membership.roleid
    join pg_roles member_role on member_role.oid=membership.member
   where granted_role.rolname='agent_execution_worker_migrator'
     and member_role.rolname=current_user;
  if membership_count <> 0 then
    raise exception 'temporary execution worker membership remains';
  end if;
end
$membership_cleanup_verification$;
SQL
}
cleanup_membership() {
  for cleanup_attempt in 1 2; do
    if cleanup_membership_once; then
      return 0
    fi
  done
  return 1
}
database_exit() {
  selected_status=$?
  trap - EXIT
  if [[ "$cleanup_needed" == "1" ]]; then
    if ! cleanup_membership; then
      echo "EXECUTION_WORKER_DATABASE_MEMBERSHIP_ROLLBACK_FAILED" >&2
      exit 1
    fi
  fi
  exit "$selected_status"
}
trap database_exit EXIT

# Read-only collision audit. Invalid existing roles, memberships, database ownership,
# or ACLs fail before role grants or schema/outbox access.
owner_psql -d postgres >/dev/null <<'SQL'
do $preflight$
declare
  selected record;
begin
  for selected in
    select rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
           rolreplication,rolbypassrls,rolinherit
      from pg_roles
     where rolname in (
       'agent_execution_worker_owner',
       'agent_execution_worker_migrator',
       'agent_execution_worker_runtime'
     )
  loop
    if (selected.rolname = 'agent_execution_worker_runtime' and
        row(selected.rolcanlogin,selected.rolsuper,selected.rolcreatedb,
            selected.rolcreaterole,selected.rolreplication,
            selected.rolbypassrls,selected.rolinherit)
        is distinct from row(true,false,false,false,false,false,false))
       or (selected.rolname <> 'agent_execution_worker_runtime' and
        row(selected.rolcanlogin,selected.rolsuper,selected.rolcreatedb,
            selected.rolcreaterole,selected.rolreplication,
            selected.rolbypassrls,selected.rolinherit)
        is distinct from row(false,false,false,false,false,false,false)) then
      raise exception 'execution worker role attribute collision';
    end if;
  end loop;

  if exists (
    select 1
      from pg_auth_members membership
      join pg_roles granted_role on granted_role.oid=membership.roleid
      join pg_roles member_role on member_role.oid=membership.member
     where (granted_role.rolname like 'agent_execution_worker_%'
            or member_role.rolname like 'agent_execution_worker_%')
       and not (
         granted_role.rolname='agent_execution_worker_owner'
         and member_role.rolname='agent_execution_worker_migrator'
       )
  ) then
    raise exception 'execution worker role membership collision';
  end if;

  if exists (
    select 1 from pg_database database
     where database.datname='agent_execution_worker'
       and (
         database.datdba <> (select oid from pg_roles where rolname='agent_execution_worker_owner')
         or database.datistemplate
         or not database.datallowconn
         or pg_encoding_to_char(database.encoding) <> 'UTF8'
         or exists (
           select 1
             from aclexplode(coalesce(database.datacl,acldefault('d',database.datdba))) acl
             left join pg_roles grantee on grantee.oid=acl.grantee
            where not (
              grantee.rolname='agent_execution_worker_owner'
              or (
                grantee.rolname in (
                  'agent_execution_worker_migrator',
                  'agent_execution_worker_runtime'
                )
                and acl.privilege_type='CONNECT'
                and not acl.is_grantable
              )
            )
         )
       )
  ) then
    raise exception 'execution worker database or acl collision';
  end if;
end
$preflight$;
SQL

cleanup_needed=1
owner_psql -d postgres >/dev/null <<SQL
select 'create role agent_execution_worker_owner nologin noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls'
where not exists (select 1 from pg_roles where rolname='agent_execution_worker_owner') \gexec
select 'create role agent_execution_worker_migrator nologin noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls'
where not exists (select 1 from pg_roles where rolname='agent_execution_worker_migrator') \gexec
select format(
  'create role agent_execution_worker_runtime login noinherit password %L nosuperuser nocreatedb nocreaterole noreplication nobypassrls',
  '$runtime_password'
)
where not exists (select 1 from pg_roles where rolname='agent_execution_worker_runtime') \gexec
select format('alter role agent_execution_worker_runtime password %L', '$runtime_password') \gexec
grant agent_execution_worker_owner to agent_execution_worker_migrator;
select format('grant agent_execution_worker_migrator to %I', current_user) \gexec
select 'create database agent_execution_worker owner agent_execution_worker_owner template template0 encoding ''UTF8'''
where not exists (select 1 from pg_database where datname='agent_execution_worker') \gexec
revoke all on database agent_execution_worker from public;
grant connect on database agent_execution_worker to agent_execution_worker_migrator, agent_execution_worker_runtime;
SQL

owner_psql -d agent_execution_worker >/dev/null <<SQL
set role agent_execution_worker_owner;
\i $schema_file
revoke all on schema public from public;
revoke all on schema execution_worker from public;
grant usage on schema execution_worker to agent_execution_worker_runtime;
revoke all on all tables in schema execution_worker from public;
revoke all on all tables in schema execution_worker from agent_execution_worker_runtime;
grant select,insert,update on execution_worker.local_runs to agent_execution_worker_runtime;
grant select,insert,update on execution_worker.event_outbox to agent_execution_worker_runtime;
grant select on execution_worker.schema_migrations to agent_execution_worker_runtime;
reset role;
SQL

cleanup_membership
cleanup_needed=0

# Exact persistent-state audit after the temporary current_user grant is removed.
owner_psql -d agent_execution_worker >/dev/null <<'SQL'
do $verify$
declare
  actual text[];
begin
  if (select count(*) from pg_roles where rolname in (
        'agent_execution_worker_owner','agent_execution_worker_migrator','agent_execution_worker_runtime'
      )) <> 3 then
    raise exception 'execution worker roles missing';
  end if;
  if exists (
    select 1 from pg_auth_members membership
    join pg_roles granted_role on granted_role.oid=membership.roleid
    join pg_roles member_role on member_role.oid=membership.member
    where (granted_role.rolname like 'agent_execution_worker_%' or member_role.rolname like 'agent_execution_worker_%')
      and not (granted_role.rolname='agent_execution_worker_owner' and member_role.rolname='agent_execution_worker_migrator')
  ) or not pg_has_role('agent_execution_worker_migrator','agent_execution_worker_owner','MEMBER') then
    raise exception 'execution worker role membership mismatch';
  end if;
  if (select datdba from pg_database where datname=current_database()) <>
     (select oid from pg_roles where rolname='agent_execution_worker_owner')
     or exists (
       select 1 from pg_database database,
       lateral aclexplode(coalesce(database.datacl,acldefault('d',database.datdba))) acl
       where database.datname=current_database() and acl.grantee=0
     ) then
    raise exception 'execution worker database ownership mismatch';
  end if;
  select array_agg(
           concat(coalesce(grantee.rolname,'PUBLIC'),':',acl.privilege_type,':',acl.is_grantable)
           order by coalesce(grantee.rolname,'PUBLIC'),acl.privilege_type
         )
    into actual
    from pg_database database,
    lateral aclexplode(coalesce(database.datacl,acldefault('d',database.datdba))) acl
    left join pg_roles grantee on grantee.oid=acl.grantee
   where database.datname=current_database();
  if actual is distinct from array[
    'agent_execution_worker_migrator:CONNECT:f',
    'agent_execution_worker_owner:CONNECT:f',
    'agent_execution_worker_owner:CREATE:f',
    'agent_execution_worker_owner:TEMPORARY:f',
    'agent_execution_worker_runtime:CONNECT:f'
  ] then
    raise exception 'execution worker database grant mismatch';
  end if;
  if exists (
    select 1 from pg_namespace namespace,
    lateral aclexplode(coalesce(namespace.nspacl,acldefault('n',namespace.nspowner))) acl
     where namespace.nspname in ('public','execution_worker') and acl.grantee=0
  ) then
    raise exception 'execution worker PUBLIC schema privilege mismatch';
  end if;
  if (select nspowner from pg_namespace where nspname='execution_worker') <>
     (select oid from pg_roles where rolname='agent_execution_worker_owner') then
    raise exception 'execution worker schema ownership mismatch';
  end if;
  if exists (
    select 1 from pg_namespace namespace,
    lateral aclexplode(coalesce(namespace.nspacl,acldefault('n',namespace.nspowner))) acl
    left join pg_roles grantee on grantee.oid=acl.grantee
    where namespace.nspname='execution_worker'
      and not (
        grantee.rolname='agent_execution_worker_owner'
        or (
          grantee.rolname='agent_execution_worker_runtime'
          and acl.privilege_type='USAGE'
          and not acl.is_grantable
        )
      )
  ) then
    raise exception 'execution worker unexpected schema grant';
  end if;
  if exists (
    select 1 from pg_class relation
    join pg_namespace namespace on namespace.oid=relation.relnamespace
    where namespace.nspname='execution_worker' and relation.relkind in ('r','p')
      and relation.relowner <> (select oid from pg_roles where rolname='agent_execution_worker_owner')
  ) then
    raise exception 'execution worker table ownership mismatch';
  end if;
  if exists (
    select 1 from pg_class relation
    join pg_namespace namespace on namespace.oid=relation.relnamespace
    cross join lateral aclexplode(coalesce(relation.relacl,acldefault('r',relation.relowner))) acl
    left join pg_roles grantee on grantee.oid=acl.grantee
    where namespace.nspname='execution_worker' and relation.relkind in ('r','p')
      and coalesce(grantee.rolname,'PUBLIC') not in (
        'agent_execution_worker_owner','agent_execution_worker_runtime'
      )
  ) then
    raise exception 'execution worker unexpected table grant';
  end if;
  select array_agg(concat(table_name,':',privilege_type) order by table_name,privilege_type)
    into actual
    from information_schema.role_table_grants
   where grantee='agent_execution_worker_runtime' and table_schema='execution_worker';
  if actual is distinct from array[
    'event_outbox:INSERT','event_outbox:SELECT','event_outbox:UPDATE',
    'local_runs:INSERT','local_runs:SELECT','local_runs:UPDATE',
    'schema_migrations:SELECT'
  ] then
    raise exception 'execution worker runtime table grant mismatch';
  end if;
  if exists (
    select 1 from information_schema.role_usage_grants
     where grantee='agent_execution_worker_runtime'
       and object_schema not in ('execution_worker')
  ) then
    raise exception 'execution worker runtime usage grant mismatch';
  end if;
  if not has_schema_privilege('agent_execution_worker_runtime','execution_worker','USAGE')
     or has_schema_privilege('agent_execution_worker_runtime','execution_worker','CREATE') then
    raise exception 'execution worker runtime schema grant mismatch';
  end if;
  if (select version from execution_worker.schema_migrations where singleton) <> 1 then
    raise exception 'execution worker schema version mismatch';
  end if;
end
$verify$;
SQL

if [[ ! -e "$runtime_dsn_file" ]]; then
  if ! "$python_bin" - "$runtime_dsn_file" "$runtime_password" "$owner_port" 2>/dev/null <<'PY'
import os
import pathlib
import secrets
import stat
import sys

path = pathlib.Path(sys.argv[1])
value = f"postgresql://agent_execution_worker_runtime:{sys.argv[2]}@127.0.0.1:{sys.argv[3]}/agent_execution_worker\n".encode()
parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os,"O_DIRECTORY",0) | getattr(os,"O_NOFOLLOW",0))
temporary = f".{path.name}.{secrets.token_hex(16)}.part"
created = False
try:
    parent = os.fstat(parent_fd)
    if stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_uid != os.getuid():
        raise SystemExit(1)
    descriptor = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600,dir_fd=parent_fd)
    created = True
    try:
        os.fchmod(descriptor,0o600)
        offset = 0
        while offset < len(value):
            written = os.write(descriptor,value[offset:])
            if written <= 0:
                raise SystemExit(1)
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary,path.name,src_dir_fd=parent_fd,dst_dir_fd=parent_fd)
    created = False
    os.fsync(parent_fd)
finally:
    if created:
        try:
            os.unlink(temporary,dir_fd=parent_fd)
        except FileNotFoundError:
            pass
    os.close(parent_fd)
PY
  then
    fail
  fi
fi

echo "EXECUTION_WORKER_DATABASE_READY version=1"
