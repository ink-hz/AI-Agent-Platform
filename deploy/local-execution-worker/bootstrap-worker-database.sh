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

[[ -x "$psql_bin" && "$($psql_bin --version)" == psql\ \(PostgreSQL\)\ 17.* ]] || fail
[[ -x "$python_bin" ]] || fail
[[ -f "$schema_file" && ! -L "$schema_file" ]] || fail
[[ -f "$owner_dsn_file" && ! -L "$owner_dsn_file" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$owner_dsn_file")" == "600 $(/usr/bin/id -un)" ]] || fail
[[ -d "$runtime_private_dir" && ! -L "$runtime_private_dir" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$runtime_private_dir")" == "700 agentops" ]] || fail

owner_field() {
  "$python_bin" - "$owner_dsn_file" "$1" <<'PY'
import pathlib
import sys
from urllib.parse import unquote, urlsplit

value = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
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
fields = {
    "host": parsed.hostname,
    "port": str(parsed.port),
    "user": unquote(parsed.username),
    "password": unquote(parsed.password),
}
selected = fields[sys.argv[2]]
if not selected or "\x00" in selected or "\n" in selected or "\r" in selected:
    raise SystemExit(1)
print(selected)
PY
}
owner_host="$(owner_field host)" || fail
owner_port="$(owner_field port)" || fail
owner_user="$(owner_field user)" || fail
owner_password="$(owner_field password)" || fail
owner_psql() {
  PGPASSWORD="$owner_password" "$psql_bin" -X -v ON_ERROR_STOP=1 \
    -h "$owner_host" -p "$owner_port" -U "$owner_user" "$@"
}
runtime_password=""
if [[ ! -e "$runtime_dsn_file" ]]; then
  runtime_password="$(/usr/bin/openssl rand -hex 32)"
else
  [[ -f "$runtime_dsn_file" && ! -L "$runtime_dsn_file" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$runtime_dsn_file")" == "600 agentops" ]] || fail
  runtime_password="$(/usr/bin/sed -n 's|^postgresql://agent_execution_worker_runtime:\([^@]*\)@127\.0\.0\.1:[0-9]*/agent_execution_worker$|\1|p' "$runtime_dsn_file")"
fi
[[ "$runtime_password" =~ ^[0-9a-f]{64}$ ]] || fail

owner_psql -d postgres >/dev/null <<SQL
select 'create role agent_execution_worker_owner nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls'
where not exists (select 1 from pg_roles where rolname='agent_execution_worker_owner') \gexec
select 'create role agent_execution_worker_migrator nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls'
where not exists (select 1 from pg_roles where rolname='agent_execution_worker_migrator') \gexec
select format(
  'create role agent_execution_worker_runtime login password %L nosuperuser nocreatedb nocreaterole noreplication nobypassrls',
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

cleanup_membership() {
  owner_psql -d postgres >/dev/null 2>&1 <<'SQL' || true
select format('revoke agent_execution_worker_migrator from %I', current_user) \gexec
SQL
}
trap cleanup_membership EXIT
owner_psql -d agent_execution_worker >/dev/null <<SQL
set role agent_execution_worker_migrator;
\i $schema_file
reset role;
revoke all on schema public from public;
revoke create on schema public from public;
revoke all on schema execution_worker from public;
grant usage on schema execution_worker to agent_execution_worker_runtime;
grant select,insert,update on execution_worker.local_runs to agent_execution_worker_runtime;
grant select,insert,update on execution_worker.event_outbox to agent_execution_worker_runtime;
grant select on execution_worker.schema_migrations to agent_execution_worker_runtime;
select version from execution_worker.schema_migrations where singleton;
select count(*) from execution_worker.event_outbox;
SQL
cleanup_membership
trap - EXIT

if [[ ! -e "$runtime_dsn_file" ]]; then
  temporary="$runtime_private_dir/.execution-worker-postgres-dsn.$$.part"
  /usr/bin/printf 'postgresql://agent_execution_worker_runtime:%s@%s:%s/agent_execution_worker\n' "$runtime_password" "$owner_host" "$owner_port" > "$temporary"
  /bin/chmod 600 "$temporary"
  /usr/sbin/chown agentops "$temporary"
  /bin/mv -f "$temporary" "$runtime_dsn_file"
fi
/bin/chmod 700 "$runtime_private_dir"
/bin/chmod 600 "$runtime_dsn_file"
/usr/sbin/chown agentops "$runtime_private_dir" "$runtime_dsn_file"
echo "EXECUTION_WORKER_DATABASE_READY version=1"
