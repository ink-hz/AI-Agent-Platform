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
[[ -n "$image_name" && -n "$postgres_container" ]] || fail

roles=(
  platform_control_migrator
  platform_control_app
  platform_directory_worker
  platform_stream_ingest
  platform_audit_append
  platform_control_maintenance
)
password_names=(
  control-migrator-password
  control-app-password
  control-directory-worker-password
  control-stream-ingest-password
  control-audit-append-password
  control-maintenance-password
)
dsn_names=(
  control-migrator-database-url
  control-database-url
  control-directory-worker-database-url
  control-stream-ingest-database-url
  control-audit-database-url
  control-maintenance-database-url
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
  /bin/chown root:root "$password_file"
  /bin/chmod 600 "$password_file"
  passwords+=("$password_value")
done

/usr/bin/docker exec -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null <<SQL
\set migrator_password '${passwords[0]}'
\set app_password '${passwords[1]}'
\set directory_password '${passwords[2]}'
\set stream_password '${passwords[3]}'
\set audit_password '${passwords[4]}'
\set maintenance_password '${passwords[5]}'
select format('create role %I login password %L', 'platform_control_migrator', :'migrator_password')
where not exists (select 1 from pg_roles where rolname = 'platform_control_migrator') \gexec
select format('alter role %I password %L', 'platform_control_migrator', :'migrator_password') \gexec
select format('create role %I login password %L', 'platform_control_app', :'app_password')
where not exists (select 1 from pg_roles where rolname = 'platform_control_app') \gexec
select format('alter role %I password %L', 'platform_control_app', :'app_password') \gexec
select format('create role %I login password %L', 'platform_directory_worker', :'directory_password')
where not exists (select 1 from pg_roles where rolname = 'platform_directory_worker') \gexec
select format('alter role %I password %L', 'platform_directory_worker', :'directory_password') \gexec
select format('create role %I login password %L', 'platform_stream_ingest', :'stream_password')
where not exists (select 1 from pg_roles where rolname = 'platform_stream_ingest') \gexec
select format('alter role %I password %L', 'platform_stream_ingest', :'stream_password') \gexec
select format('create role %I login password %L', 'platform_audit_append', :'audit_password')
where not exists (select 1 from pg_roles where rolname = 'platform_audit_append') \gexec
select format('alter role %I password %L', 'platform_audit_append', :'audit_password') \gexec
select format('create role %I login password %L', 'platform_control_maintenance', :'maintenance_password')
where not exists (select 1 from pg_roles where rolname = 'platform_control_maintenance') \gexec
select format('alter role %I password %L', 'platform_control_maintenance', :'maintenance_password') \gexec
SQL

databases=(agent_platform_control agent_platform_control_preview)
for database_name in "${databases[@]}"; do
  database_exists="$(/usr/bin/docker exec "$postgres_container" \
    psql -X -A -t -U platform_owner -d postgres \
    -c "select count(*) from pg_database where datname = '$database_name'")"
  [[ "$database_exists" == "0" || "$database_exists" == "1" ]] || fail
  if [[ "$database_exists" == "0" ]]; then
    /usr/bin/docker exec "$postgres_container" \
      createdb -U platform_owner -O platform_control_migrator \
      -T template0 "$database_name"
  fi
  /usr/bin/docker exec -i "$postgres_container" \
    psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null <<SQL
revoke connect on database $database_name from public;
grant connect on database $database_name to
  platform_control_migrator,
  platform_control_app,
  platform_directory_worker,
  platform_stream_ingest,
  platform_audit_append,
  platform_control_maintenance;
SQL
  /usr/bin/docker exec -i "$postgres_container" \
    psql -X -v ON_ERROR_STOP=1 -U platform_owner -d "$database_name" >/dev/null <<SQL
revoke all on schema public from public;
revoke create on schema public from public;
SQL
done

for index in "${!roles[@]}"; do
  production_dsn="$private_path/${dsn_names[$index]}"
  preview_dsn="$private_path/preview-${dsn_names[$index]}"
  /usr/bin/printf 'postgresql://%s:%s@platform-postgres:5432/agent_platform_control\n' \
    "${roles[$index]}" "${passwords[$index]}" > "$production_dsn"
  /usr/bin/printf 'postgresql://%s:%s@platform-postgres:5432/agent_platform_control_preview\n' \
    "${roles[$index]}" "${passwords[$index]}" > "$preview_dsn"
  /bin/chown root:root "$production_dsn" "$preview_dsn"
  /bin/chmod 600 "$production_dsn" "$preview_dsn"
done

for migrator_secret in \
  control-migrator-database-url \
  preview-control-migrator-database-url; do
  /usr/bin/docker run --rm --user 0:0 \
    --network orbbec-agent-platform-internal \
    -v "$private_path:/run/control-secrets:ro" \
    -v "$release_path/backend/control_migrations:/app/backend/control_migrations:ro" \
    -e "PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/run/control-secrets/$migrator_secret" \
    -e PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations \
    "$image_name" python -m app.control_plane.migrate >/dev/null
done

# Defense in depth after the migration creates the application schema.
for database_name in "${databases[@]}"; do
  /usr/bin/docker exec -i "$postgres_container" \
    psql -X -v ON_ERROR_STOP=1 -U platform_owner -d "$database_name" >/dev/null <<SQL
revoke all on schema platform_control from public;
SQL
done

unset passwords password_value
