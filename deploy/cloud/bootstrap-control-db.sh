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
credential_helper="$release_path/deploy/cloud/control-db-credential-state.sh"
credential_state_file="$private_path/.control-database-credentials-v2.state"
credential_work_path="$private_path/.control-database-credentials-v2"
[[ -x "$credential_helper" && ! -L "$credential_helper" ]] || fail

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
production_rotation_roles=(
  platform_control_migrator
  platform_control_app
  platform_directory_worker
  platform_stream_ingest
  platform_audit_append
  platform_control_maintenance
)
[[ "${#production_rotation_roles[@]}" -eq 6 ]] || fail
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
brain_roles=(
  platform_brain_worker
  platform_brain_worker_preview
)
brain_password_names=(
  brain-worker-password
  preview-brain-worker-password
)
brain_dsn_names=(
  brain-worker-database-url
  preview-brain-worker-database-url
)
brain_database_names=(
  agent_platform_control
  agent_platform_control_preview
)

for credential_name in "${password_names[@]}" "${dsn_names[@]}"; do
  credential_file="$private_path/$credential_name"
  if [[ -e "$credential_file" ]]; then
    [[ -f "$credential_file" && ! -L "$credential_file" ]] || fail
    [[ "$(/usr/bin/stat -c '%a %U' "$credential_file")" == "600 root" ]] \
      || fail
  fi
done
if [[ -e "$credential_state_file" ]]; then
  [[ -f "$credential_state_file" && ! -L "$credential_state_file" ]] || fail
  [[ "$(/usr/bin/stat -c '%a %U' "$credential_state_file")" == "600 root" ]] \
    || fail
fi

credential_layout="$("$credential_helper" classify "$private_path")" || fail
control_catalog_signature="$(/usr/bin/docker exec "$postgres_container" \
  psql -X -A -t -U platform_owner -d postgres -c \
  "select concat(
    (select count(*) from pg_database where datname in ('agent_platform_control', 'agent_platform_control_preview')), ':',
    (select count(*) from pg_roles where rolname = any(array['platform_control_migrator', 'platform_control_app', 'platform_directory_worker', 'platform_stream_ingest', 'platform_audit_append', 'platform_control_maintenance'])), ':',
    (select count(*) from pg_roles where rolname = any(array['platform_control_migrator_preview', 'platform_control_app_preview', 'platform_directory_worker_preview', 'platform_stream_ingest_preview', 'platform_audit_append_preview', 'platform_control_maintenance_preview'])), ':',
    (select count(*) from pg_roles where rolname = any(array['platform_control_owner', 'platform_control_owner_preview'])), ':',
    (select count(*) from pg_database database_entry join pg_roles owner_role on owner_role.oid = database_entry.datdba where (database_entry.datname = 'agent_platform_control' and owner_role.rolname = 'platform_control_migrator') or (database_entry.datname = 'agent_platform_control_preview' and owner_role.rolname = 'platform_control_migrator')), ':',
    (select count(*) from pg_database database_entry join pg_roles owner_role on owner_role.oid = database_entry.datdba where (database_entry.datname = 'agent_platform_control' and owner_role.rolname = 'platform_control_owner') or (database_entry.datname = 'agent_platform_control_preview' and owner_role.rolname = 'platform_control_owner_preview'))
  )")" || fail
case "$credential_layout:$control_catalog_signature" in
  fresh:0:0:0:0:0:0|fresh:1:0:6:1:0:1|legacy-shared:2:6:0:0:2:0|isolated-unmarked:2:6:6:2:0:2)
    credential_origin="$credential_layout"
    "$credential_helper" prepare "$private_path" "$credential_origin" \
      >/dev/null || fail
    /bin/chown root:root "$credential_state_file" "$credential_work_path"
    /bin/chmod 600 "$credential_state_file"
    /bin/chmod 700 "$credential_work_path"
    for credential_name in "${password_names[@]}" "${dsn_names[@]}"; do
      /bin/chown root:root "$credential_work_path/$credential_name"
      /bin/chmod 600 "$credential_work_path/$credential_name"
    done
    credential_source_path="$credential_work_path"
    rotate_credentials=1
    ;;
  rotating:fresh:*|rotating:legacy-shared:*|rotating:isolated-unmarked:*)
    credential_origin="${credential_layout#rotating:}"
    "$credential_helper" prepare "$private_path" "$credential_origin" \
      >/dev/null || fail
    /bin/chown root:root "$credential_state_file" "$credential_work_path"
    /bin/chmod 600 "$credential_state_file"
    /bin/chmod 700 "$credential_work_path"
    for credential_name in "${password_names[@]}" "${dsn_names[@]}"; do
      /bin/chown root:root "$credential_work_path/$credential_name"
      /bin/chmod 600 "$credential_work_path/$credential_name"
    done
    credential_source_path="$credential_work_path"
    rotate_credentials=1
    ;;
  complete:2:6:6:2:0:2)
    "$credential_helper" prepare "$private_path" complete >/dev/null || fail
    credential_origin=complete
    credential_source_path="$private_path"
    rotate_credentials=0
    ;;
  *)
    fail
    ;;
esac

declare -a passwords=()
for index in "${!roles[@]}"; do
  password_file="$credential_source_path/${password_names[$index]}"
  [[ -f "$password_file" && ! -L "$password_file" ]] || fail
  password_value="$(/usr/bin/tr -d '\n' < "$password_file")"
  [[ "$password_value" =~ ^[0-9a-f]{64}$ ]] || fail
  if [[ "${#passwords[@]}" -gt 0 ]]; then
    for existing_password in "${passwords[@]}"; do
      [[ "$password_value" != "$existing_password" ]] || fail
    done
  fi
  passwords+=("$password_value")
done

write_root_secret() {
  local target_path="$1"
  local secret_value="$2"
  local temporary_path="${target_path}.tmp.$$"
  [[ ! -e "$target_path" && ! -e "$temporary_path" ]] || fail
  /usr/bin/printf '%s\n' "$secret_value" > "$temporary_path"
  /bin/chown root:root "$temporary_path"
  /bin/chmod 600 "$temporary_path"
  /bin/mv "$temporary_path" "$target_path"
}

declare -a brain_passwords=()
for index in "${!brain_roles[@]}"; do
  password_file="$private_path/${brain_password_names[$index]}"
  dsn_file="$private_path/${brain_dsn_names[$index]}"
  database_name="${brain_database_names[$index]}"

  if [[ -e "$password_file" ]]; then
    [[ -f "$password_file" && ! -L "$password_file" ]] || fail
    [[ "$(/usr/bin/stat -c '%a %U' "$password_file")" == "600 root" ]] \
      || fail
    brain_password="$(/usr/bin/tr -d '\n' < "$password_file")"
    [[ "$brain_password" =~ ^[0-9a-f]{64}$ ]] || fail
  else
    brain_password="$(/usr/bin/openssl rand -hex 32)"
    [[ "$brain_password" =~ ^[0-9a-f]{64}$ ]] || fail
    write_root_secret "$password_file" "$brain_password"
  fi

  for existing_password in "${passwords[@]}" "${brain_passwords[@]}"; do
    [[ "$brain_password" != "$existing_password" ]] || fail
  done
  brain_passwords+=("$brain_password")

  brain_dsn="postgresql://${brain_roles[$index]}:${brain_password}@postgres:5432/${database_name}"
  if [[ -e "$dsn_file" ]]; then
    [[ -f "$dsn_file" && ! -L "$dsn_file" ]] || fail
    [[ "$(/usr/bin/stat -c '%a %U' "$dsn_file")" == "600 root" ]] || fail
    [[ "$(/usr/bin/tr -d '\n' < "$dsn_file")" == "$brain_dsn" ]] || fail
  else
    write_root_secret "$dsn_file" "$brain_dsn"
  fi
done

/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null <<SQL
\set production_brain_password '${brain_passwords[0]}'
\set preview_brain_password '${brain_passwords[1]}'

select format(
  'create role %I login password %L nosuperuser nocreatedb nocreaterole noreplication nobypassrls inherit',
  role_name, role_password
)
from (values
  ('platform_brain_worker', :'production_brain_password'),
  ('platform_brain_worker_preview', :'preview_brain_password')
) configured(role_name, role_password)
where not exists (select 1 from pg_roles where rolname = role_name) \gexec

select format(
  'alter role %1$I login password %2$L nosuperuser nocreatedb nocreaterole noreplication nobypassrls inherit',
  role_name, role_password
)
from (values
  ('platform_brain_worker', :'production_brain_password'),
  ('platform_brain_worker_preview', :'preview_brain_password')
) configured(role_name, role_password) \gexec
SQL

if [[ "$rotate_credentials" -eq 1 ]]; then
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
  ('platform_control_maintenance', :'production_maintenance_password', 'inherit')
) configured(role_name, role_password, inheritance) \gexec

select format(
  'alter role %I password %L login nosuperuser nocreatedb nocreaterole noreplication nobypassrls %s',
  role_name, role_password, inheritance
)
from (values
  ('platform_control_migrator_preview', :'preview_migrator_password', 'noinherit'),
  ('platform_control_app_preview', :'preview_app_password', 'inherit'),
  ('platform_directory_worker_preview', :'preview_directory_password', 'inherit'),
  ('platform_stream_ingest_preview', :'preview_stream_password', 'inherit'),
  ('platform_audit_append_preview', :'preview_audit_password', 'inherit'),
  ('platform_control_maintenance_preview', :'preview_maintenance_password', 'inherit')
) configured(role_name, role_password, inheritance) \gexec
SQL
fi

/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null <<SQL
select 'create role platform_control_owner nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit'
where not exists (select 1 from pg_roles where rolname = 'platform_control_owner') \gexec
select 'create role platform_control_owner_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit'
where not exists (select 1 from pg_roles where rolname = 'platform_control_owner_preview') \gexec
alter role platform_control_owner nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit;
alter role platform_control_owner_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit;
select format(
  'alter role %I login nosuperuser nocreatedb nocreaterole noreplication nobypassrls %s',
  role_name, inheritance
)
from (values
  ('platform_control_migrator', 'noinherit'),
  ('platform_control_app', 'inherit'),
  ('platform_directory_worker', 'inherit'),
  ('platform_stream_ingest', 'inherit'),
  ('platform_audit_append', 'inherit'),
  ('platform_control_maintenance', 'inherit'),
  ('platform_brain_worker', 'inherit'),
  ('platform_control_migrator_preview', 'noinherit'),
  ('platform_control_app_preview', 'inherit'),
  ('platform_directory_worker_preview', 'inherit'),
  ('platform_stream_ingest_preview', 'inherit'),
  ('platform_audit_append_preview', 'inherit'),
  ('platform_control_maintenance_preview', 'inherit'),
  ('platform_brain_worker_preview', 'inherit')
) configured(role_name, inheritance)
where exists (select 1 from pg_roles where rolname = role_name) \gexec
SQL

control_role_count="$(/usr/bin/docker exec "$postgres_container" \
  psql -X -A -t -U platform_owner -d postgres -c \
  "select count(*) from pg_roles where rolname = any(array['platform_control_migrator', 'platform_control_app', 'platform_directory_worker', 'platform_stream_ingest', 'platform_audit_append', 'platform_control_maintenance', 'platform_brain_worker', 'platform_control_migrator_preview', 'platform_control_app_preview', 'platform_directory_worker_preview', 'platform_stream_ingest_preview', 'platform_audit_append_preview', 'platform_control_maintenance_preview', 'platform_brain_worker_preview'])")"
[[ "$control_role_count" == "14" ]] || fail

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
  platform_brain_worker,
  platform_control_migrator_preview, platform_control_app_preview,
  platform_directory_worker_preview, platform_stream_ingest_preview,
  platform_audit_append_preview, platform_control_maintenance_preview,
  platform_brain_worker_preview;
revoke connect on database agent_platform_control_preview from public,
  platform_control_migrator, platform_control_app,
  platform_directory_worker, platform_stream_ingest,
  platform_audit_append, platform_control_maintenance,
  platform_brain_worker,
  platform_control_migrator_preview, platform_control_app_preview,
  platform_directory_worker_preview, platform_stream_ingest_preview,
  platform_audit_append_preview, platform_control_maintenance_preview,
  platform_brain_worker_preview;
grant connect on database agent_platform_control to
  platform_control_migrator, platform_control_app,
  platform_directory_worker, platform_stream_ingest,
  platform_audit_append, platform_control_maintenance,
  platform_brain_worker;
grant connect on database agent_platform_control_preview to
  platform_control_migrator_preview, platform_control_app_preview,
  platform_directory_worker_preview, platform_stream_ingest_preview,
  platform_audit_append_preview, platform_control_maintenance_preview,
  platform_brain_worker_preview;
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
  'platform_brain_worker',
  'platform_control_migrator_preview', 'platform_control_app_preview',
  'platform_directory_worker_preview', 'platform_stream_ingest_preview',
  'platform_audit_append_preview', 'platform_control_maintenance_preview',
  'platform_brain_worker_preview'
]) \gexec
grant platform_control_owner to platform_control_migrator;
grant platform_control_owner_preview to platform_control_migrator_preview;
SQL

migrator_secrets=(
  "$credential_source_path/control-migrator-database-url"
  "$credential_source_path/preview-control-migrator-database-url"
)
owner_role_names=(
  platform_control_owner
  platform_control_owner_preview
)
for index in "${!migrator_secrets[@]}"; do
  migrator_secret_relative="${migrator_secrets[$index]#"$private_path/"}"
  [[ "$migrator_secret_relative" != "${migrator_secrets[$index]}" ]] || fail
  /usr/bin/docker run --rm --user 0:0 \
    --network orbbec-agent-platform-internal \
    -v "$private_path:/run/control-secrets:ro" \
    -v "$release_path/backend/control_migrations:/app/backend/control_migrations:ro" \
    -e "PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/run/control-secrets/$migrator_secret_relative" \
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

if [[ "$rotate_credentials" -eq 1 ]]; then
  "$credential_helper" publish "$private_path" || fail
  for credential_name in "${password_names[@]}" "${dsn_names[@]}"; do
    /bin/chown root:root "$private_path/$credential_name"
    /bin/chmod 600 "$private_path/$credential_name"
  done
  "$credential_helper" complete "$private_path" || fail
  /bin/chown root:root "$credential_state_file"
  /bin/chmod 600 "$credential_state_file"
fi
[[ "$("$credential_helper" classify "$private_path")" == "complete" ]] || fail
/usr/bin/printf '%s\n' 'CONTROL_DATABASE_CREDENTIALS_READY version=2'

unset passwords password_value existing_password brain_passwords brain_password brain_dsn
