#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "HR_AGENT_MIGRATIONS_FAILED" >&2
  exit 1
}

[[ "$#" -eq 4 ]] || fail
release_path="$1"
private_path="$2"
image_name="$3"
postgres_container="$4"
docker_path="${HR_MIGRATION_DOCKER:-/usr/bin/docker}"

[[ "$release_path" == /* && -d "$release_path" && ! -L "$release_path" ]] || fail
[[ "$private_path" == /* && -d "$private_path" && ! -L "$private_path" ]] || fail
[[ -x "$docker_path" && "$image_name" != *[[:space:]]* && -n "$image_name" ]] || fail
[[ "$postgres_container" =~ ^[A-Za-z0-9_.-]+$ ]] || fail

root_migrations="$release_path/backend/control_migrations"
hr_migrations="$root_migrations/hr_agent"
root_100="$root_migrations/100_attachment_erasure_worker_access.sql"
root_102="$root_migrations/102_hr_execution_cutover.sql"
[[ -d "$hr_migrations" && ! -L "$hr_migrations" ]] || fail
[[ -f "$root_100" && ! -L "$root_100" && -f "$root_102" && ! -L "$root_102" ]] || fail

secrets=(control-migrator-database-url preview-control-migrator-database-url)
for secret_name in "${secrets[@]}"; do
  secret_path="$private_path/$secret_name"
  [[ -f "$secret_path" && ! -L "$secret_path" ]] || fail
  [[ "$(/usr/bin/stat -c '%a' "$secret_path" 2>/dev/null || /usr/bin/stat -f '%Lp' "$secret_path")" == "600" ]] || fail
done

sha_100="$(/usr/bin/shasum -a 256 "$root_100" | /usr/bin/awk '{print $1}')"
sha_102="$(/usr/bin/shasum -a 256 "$root_102" | /usr/bin/awk '{print $1}')"
[[ "$sha_100" =~ ^[0-9a-f]{64}$ && "$sha_102" =~ ^[0-9a-f]{64}$ ]] || fail

owner_psql() {
  local database="$1" statement="$2"
  "$docker_path" exec "$postgres_container" psql -X -A -t -v ON_ERROR_STOP=1 \
    -U platform_owner -d "$database" -c "$statement"
}

membership_count="$(owner_psql postgres "select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid where r.rolname in ('platform_control_owner','platform_control_owner_preview')")" || fail
[[ "$membership_count" == "0" ]] || fail

for database_name in agent_platform_control agent_platform_control_preview; do
  root_gate_count="$(owner_psql "$database_name" "select count(*) from platform_control.schema_migrations where (version=100 and sha256='$sha_100') or (version=102 and sha256='$sha_102')")" || fail
  [[ "$root_gate_count" == "2" ]] || fail
done

memberships_granted=0
revoke_owner_memberships() {
  local production_status preview_status count_status final_count
  set +e
  owner_psql postgres "revoke platform_control_owner from platform_control_migrator" >/dev/null 2>&1
  production_status=$?
  owner_psql postgres "revoke platform_control_owner_preview from platform_control_migrator_preview" >/dev/null 2>&1
  preview_status=$?
  final_count="$(owner_psql postgres "select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid where r.rolname in ('platform_control_owner','platform_control_owner_preview')" 2>/dev/null)"
  count_status=$?
  set -e
  [[ "$production_status" -eq 0 && "$preview_status" -eq 0 && "$count_status" -eq 0 && "$final_count" == "0" ]]
}

cleanup() {
  local status=$?
  trap - EXIT
  if [[ "$memberships_granted" -eq 1 ]] && ! revoke_owner_memberships; then
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT

memberships_granted=1
owner_psql postgres "grant platform_control_owner to platform_control_migrator; grant platform_control_owner_preview to platform_control_migrator_preview" >/dev/null || fail

owner_roles=(platform_control_owner platform_control_owner_preview)
for index in 0 1; do
  "$docker_path" run --rm --user 0:0 \
    --network orbbec-agent-platform-internal \
    -v "$private_path:/run/control-secrets:ro" \
    -v "$hr_migrations:/app/backend/control_migrations/hr_agent:ro" \
    -e "PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/run/control-secrets/${secrets[$index]}" \
    -e "PLATFORM_CONTROL_OWNER_ROLE=${owner_roles[$index]}" \
    -e PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations/hr_agent \
    "$image_name" python -m app.control_plane.migrate >/dev/null 2>&1 || fail
done

revoke_owner_memberships || fail
memberships_granted=0
trap - EXIT
echo "HR_AGENT_MIGRATIONS_OK versions=096-099,101"
