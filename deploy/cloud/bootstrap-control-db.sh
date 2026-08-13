#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "CONTROL_DATABASE_BOOTSTRAP_FAILED" >&2
  exit 1
}

[[ $# -eq 4 ]] || fail
release_path="$1"
private_path="$2"
image_name="$3"
postgres_container="$4"
[[ "$release_path" == /* && "$private_path" == /* ]] || fail
[[ -f "$release_path/backend/control_migrations/001_identity_security.sql" ]] || fail
[[ -f "$release_path/backend/control_migrations/002_isolate_environment_roles.sql" ]] || fail
[[ -n "$image_name" && -n "$postgres_container" ]] || fail

roles=(
  platform_control_migrator
  platform_control_app
  platform_directory_worker
  platform_stream_ingest
  platform_audit_append
  platform_control_maintenance
  platform_control_migrator_preview
  platform_control_app_preview
  platform_directory_worker_preview
  platform_stream_ingest_preview
  platform_audit_append_preview
  platform_control_maintenance_preview
)
password_names=(
  control-migrator-password
  control-app-password
  control-directory-worker-password
  control-stream-ingest-password
  control-audit-append-password
  control-maintenance-password
  preview-control-migrator-password
  preview-control-app-password
  preview-control-directory-worker-password
  preview-control-stream-ingest-password
  preview-control-audit-append-password
  preview-control-maintenance-password
)
dsn_names=(
  control-migrator-database-url
  control-database-url
  control-directory-worker-database-url
  control-stream-ingest-database-url
  control-audit-database-url
  control-maintenance-database-url
  preview-control-migrator-database-url
  preview-control-database-url
  preview-control-directory-worker-database-url
  preview-control-stream-ingest-database-url
  preview-control-audit-database-url
  preview-control-maintenance-database-url
)
database_names=(
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
)

declare -a passwords=()
for index in "${!roles[@]}"; do
  password_file="$private_path/${password_names[$index]}"
  if [[ ! -e "$password_file" ]]; then
    /usr/bin/openssl rand -hex 32 > "$password_file"
  fi
  [[ -f "$password_file" && ! -L "$password_file" ]] || fail
  password_value="$(/usr/bin/tr -d '\n' < "$password_file")"
  [[ "$password_value" =~ ^[0-9a-f]{64}$ ]] || fail
  for existing_password in "${passwords[@]}"; do
    [[ "$password_value" != "$existing_password" ]] || fail
  done
  /bin/chown root:root "$password_file"
  /bin/chmod 600 "$password_file"
  passwords+=("$password_value")
done

/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null <<SQL
\set production_migrator_password '${passwords[0]}'
\set production_app_password '${passwords[1]}'
\set production_directory_password '${passwords[2]}'
\set production_stream_password '${passwords[3]}'
\set production_audit_password '${passwords[4]}'
\set production_maintenance_password '${passwords[5]}'
\set preview_migrator_password '${passwords[6]}'
\set preview_app_password '${passwords[7]}'
\set preview_directory_password '${passwords[8]}'
\set preview_stream_password '${passwords[9]}'
\set preview_audit_password '${passwords[10]}'
\set preview_maintenance_password '${passwords[11]}'

select format(
  'create role %I login password %L nosuperuser nocreatedb nocreaterole noreplication nobypassrls %s',
  role_name, role_password, inheritance
)
from (values
  ('platform_control_migrator', :'production_migrator_password', 'noinherit'),
  ('platform_control_app', :'production_app_password', 'inherit'),
  ('platform_directory_worker', :'production_directory_password', 'inherit'),
  ('platform_stream_ingest', :'production_stream_password', 'inherit'),
  ('platform_audit_append', :'production_audit_password', 'inherit'),
  ('platform_control_maintenance', :'production_maintenance_password', 'inherit'),
  ('platform_control_migrator_preview', :'preview_migrator_password', 'noinherit'),
  ('platform_control_app_preview', :'preview_app_password', 'inherit'),
  ('platform_directory_worker_preview', :'preview_directory_password', 'inherit'),
  ('platform_stream_ingest_preview', :'preview_stream_password', 'inherit'),
  ('platform_audit_append_preview', :'preview_audit_password', 'inherit'),
  ('platform_control_maintenance_preview', :'preview_maintenance_password', 'inherit')
) configured(role_name, role_password, inheritance)
where not exists (select 1 from pg_roles where rolname = role_name) \gexec

select format(
  'alter role %I login password %L nosuperuser nocreatedb nocreaterole noreplication nobypassrls %s',
  role_name, role_password, inheritance
)
from (values
  ('platform_control_migrator', :'production_migrator_password', 'noinherit'),
  ('platform_control_app', :'production_app_password', 'inherit'),
  ('platform_directory_worker', :'production_directory_password', 'inherit'),
  ('platform_stream_ingest', :'production_stream_password', 'inherit'),
  ('platform_audit_append', :'production_audit_password', 'inherit'),
  ('platform_control_maintenance', :'production_maintenance_password', 'inherit'),
  ('platform_control_migrator_preview', :'preview_migrator_password', 'noinherit'),
  ('platform_control_app_preview', :'preview_app_password', 'inherit'),
  ('platform_directory_worker_preview', :'preview_directory_password', 'inherit'),
  ('platform_stream_ingest_preview', :'preview_stream_password', 'inherit'),
  ('platform_audit_append_preview', :'preview_audit_password', 'inherit'),
  ('platform_control_maintenance_preview', :'preview_maintenance_password', 'inherit')
) configured(role_name, role_password, inheritance) \gexec

select 'create role platform_control_owner nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit'
where not exists (select 1 from pg_roles where rolname = 'platform_control_owner') \gexec
select 'create role platform_control_owner_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit'
where not exists (select 1 from pg_roles where rolname = 'platform_control_owner_preview') \gexec
alter role platform_control_owner nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit;
alter role platform_control_owner_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit;
SQL

production_exists="$(/usr/bin/docker exec "$postgres_container" \
  psql -X -A -t -U platform_owner -d postgres \
  -c "select count(*) from pg_database where datname = 'agent_platform_control'")"
preview_exists="$(/usr/bin/docker exec "$postgres_container" \
  psql -X -A -t -U platform_owner -d postgres \
  -c "select count(*) from pg_database where datname = 'agent_platform_control_preview'")"
[[ "$production_exists" == "0" || "$production_exists" == "1" ]] || fail
[[ "$preview_exists" == "0" || "$preview_exists" == "1" ]] || fail
if [[ "$production_exists" == "0" ]]; then
  /usr/bin/docker exec "$postgres_container" createdb -U platform_owner \
    -O platform_control_owner -T template0 agent_platform_control
fi
if [[ "$preview_exists" == "0" ]]; then
  /usr/bin/docker exec "$postgres_container" createdb -U platform_owner \
    -O platform_control_owner_preview -T template0 agent_platform_control_preview
fi

/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null <<SQL
alter database agent_platform_control owner to platform_control_owner;
alter database agent_platform_control_preview owner to platform_control_owner_preview;
revoke connect on database agent_platform_control from public,
  platform_control_migrator, platform_control_app,
  platform_directory_worker, platform_stream_ingest,
  platform_audit_append, platform_control_maintenance,
  platform_control_migrator_preview, platform_control_app_preview,
  platform_directory_worker_preview, platform_stream_ingest_preview,
  platform_audit_append_preview, platform_control_maintenance_preview;
revoke connect on database agent_platform_control_preview from public,
  platform_control_migrator, platform_control_app,
  platform_directory_worker, platform_stream_ingest,
  platform_audit_append, platform_control_maintenance,
  platform_control_migrator_preview, platform_control_app_preview,
  platform_directory_worker_preview, platform_stream_ingest_preview,
  platform_audit_append_preview, platform_control_maintenance_preview;
grant connect on database agent_platform_control to
  platform_control_migrator, platform_control_app,
  platform_directory_worker, platform_stream_ingest,
  platform_audit_append, platform_control_maintenance;
grant connect on database agent_platform_control_preview to
  platform_control_migrator_preview, platform_control_app_preview,
  platform_directory_worker_preview, platform_stream_ingest_preview,
  platform_audit_append_preview, platform_control_maintenance_preview;
SQL

/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner \
  -d agent_platform_control >/dev/null <<SQL
reassign owned by platform_control_migrator, platform_control_migrator_preview
  to platform_control_owner;
revoke all on schema public from public;
revoke create on schema public from public;
SQL
/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner \
  -d agent_platform_control_preview >/dev/null <<SQL
reassign owned by platform_control_migrator, platform_control_migrator_preview
  to platform_control_owner_preview;
revoke all on schema public from public;
revoke create on schema public from public;
SQL

for index in "${!roles[@]}"; do
  dsn_file="$private_path/${dsn_names[$index]}"
  /usr/bin/printf 'postgresql://%s:%s@platform-postgres:5432/%s\n' \
    "${roles[$index]}" "${passwords[$index]}" "${database_names[$index]}" \
    > "$dsn_file"
  /bin/chown root:root "$dsn_file"
  /bin/chmod 600 "$dsn_file"
done

revoke_owner_memberships() {
  set +e
  /usr/bin/docker exec "$postgres_container" \
    psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres \
    -c "revoke platform_control_owner from platform_control_migrator" \
    >/dev/null 2>&1
  production_revoke_status=$?
  /usr/bin/docker exec "$postgres_container" \
    psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres \
    -c "revoke platform_control_owner_preview from platform_control_migrator_preview" \
    >/dev/null 2>&1
  preview_revoke_status=$?
  set -e
  [[ "$production_revoke_status" -eq 0 && "$preview_revoke_status" -eq 0 ]]
}
trap revoke_owner_memberships EXIT

/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null <<SQL
select format('revoke %I from %I', granted.rolname, member.rolname)
from pg_auth_members membership
join pg_roles member on member.oid = membership.member
join pg_roles granted on granted.oid = membership.roleid
where granted.rolname in (
  'platform_control_owner',
  'platform_control_owner_preview'
) \gexec
select format('revoke %I from %I', granted.rolname, member.rolname)
from pg_auth_members membership
join pg_roles member on member.oid = membership.member
join pg_roles granted on granted.oid = membership.roleid
where member.rolname = any(array[
  'platform_control_migrator', 'platform_control_app',
  'platform_directory_worker', 'platform_stream_ingest',
  'platform_audit_append', 'platform_control_maintenance',
  'platform_control_migrator_preview', 'platform_control_app_preview',
  'platform_directory_worker_preview', 'platform_stream_ingest_preview',
  'platform_audit_append_preview', 'platform_control_maintenance_preview'
]) \gexec
grant platform_control_owner to platform_control_migrator;
grant platform_control_owner_preview to platform_control_migrator_preview;
SQL

migrator_secrets=(
  control-migrator-database-url
  preview-control-migrator-database-url
)
owner_role_names=(
  platform_control_owner
  platform_control_owner_preview
)
for index in "${!migrator_secrets[@]}"; do
  /usr/bin/docker run --rm --user 0:0 \
    --network orbbec-agent-platform-internal \
    -v "$private_path:/run/control-secrets:ro" \
    -v "$release_path/backend/control_migrations:/app/backend/control_migrations:ro" \
    -e "PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/run/control-secrets/${migrator_secrets[$index]}" \
    -e "PLATFORM_CONTROL_OWNER_ROLE=${owner_role_names[$index]}" \
    -e PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations \
    "$image_name" python -m app.control_plane.migrate >/dev/null
done

revoke_owner_memberships || fail

for database_name in agent_platform_control agent_platform_control_preview; do
  /usr/bin/docker exec -i "$postgres_container" \
    psql -X -v ON_ERROR_STOP=1 -U platform_owner -d "$database_name" \
    >/dev/null <<SQL
revoke all on schema platform_control from public;
SQL
done

membership_count="$(/usr/bin/docker exec "$postgres_container" \
  psql -X -A -t -U platform_owner -d postgres -c \
  "select count(*) from pg_auth_members membership join pg_roles granted on granted.oid = membership.roleid where granted.rolname in ('platform_control_owner', 'platform_control_owner_preview')")"
[[ "$membership_count" == "0" ]] || fail
trap - EXIT

unset passwords password_value existing_password
