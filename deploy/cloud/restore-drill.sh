#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "CLOUD_RESTORE_DRILL_FAILED" >&2
  exit 1
}

[[ $# -eq 2 && "$1" == /* && "$2" == /* ]] || fail
backup_path="$1"
recovery_private="$2"
[[ -f "$backup_path" && ! -L "$backup_path" && -f "$recovery_private" && ! -L "$recovery_private" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %z' "$recovery_private")" == "600 32" ]] || fail

script_dir="$(cd "$(dirname "$0")" && pwd)"
repository_root="$(cd "$script_dir/../.." && pwd)"
python_path="$repository_root/backend/.venv/bin/python"
[[ -x "$python_path" ]] || fail
docker_path="${DOCKER_BIN:-$(command -v docker || true)}"
[[ "$docker_path" == /* && -x "$docker_path" ]] || fail
drill_container="orbbec-platform-restore-$BASHPID"
cleanup() { "$docker_path" rm -f "$drill_container" >/dev/null 2>&1 || true; }
trap cleanup EXIT

"$docker_path" run -d --name "$drill_container" \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  postgres:17.6-bookworm >/dev/null
for _attempt in $(/usr/bin/seq 1 30); do
  "$docker_path" exec "$drill_container" pg_isready -U postgres >/dev/null 2>&1 && break
  /bin/sleep 1
done
"$docker_path" exec "$drill_container" pg_isready -U postgres >/dev/null 2>&1 || fail

if ! PLATFORM_REPLICA_BACKUP_PRIVATE_KEY_FILE="$recovery_private" \
     PLATFORM_REPLICA_BACKUP_PATH="$backup_path" \
     "$python_path" -m app.cloud_replica.cli restore-stream \
  | "$docker_path" exec -i "$drill_container" \
      pg_restore -U postgres -d postgres --no-owner --no-privileges --exit-on-error; then
  fail
fi
verification="$("$docker_path" exec "$drill_container" psql -At -U postgres -d postgres -v ON_ERROR_STOP=1 -c \
  "select (to_regclass('platform_replica.sessions') is not null)::int, count(*), count(*) filter (where payload_sha256 !~ '^[0-9a-f]{64}$') from platform_replica.sessions;")" || fail
IFS='|' read -r schema_ok session_count invalid_hash_count <<< "$verification"
[[ "$schema_ok" == "1" && "$session_count" =~ ^[0-9]+$ && "$invalid_hash_count" == "0" ]] || fail
echo "CLOUD_RESTORE_DRILL_OK sessions=$session_count"
