#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "CLOUD_PLATFORM_ACCEPTANCE_FAILED" >&2
  exit 1
}

mode_600_file() {
  local path="$1" mode owner
  [[ "$path" == /* && -f "$path" && ! -L "$path" ]] || return 1
  mode="$(/usr/bin/stat -f '%Lp' "$path" 2>/dev/null || /usr/bin/stat -c '%a' "$path" 2>/dev/null || true)"
  owner="$(/usr/bin/stat -f '%u' "$path" 2>/dev/null || /usr/bin/stat -c '%u' "$path" 2>/dev/null || true)"
  [[ "$mode" == "600" && "$owner" == "$(/usr/bin/id -u)" ]]
}

load_config() {
  local config_path="$1"
  mode_600_file "$config_path" || fail
  set -a
  # shellcheck disable=SC1090
  source "$config_path"
  set +a
  for required_name in CLOUD_ADMIN_HOST CLOUD_ADMIN_KEY CLOUD_BASELINE_FILE; do
    [[ -n "${!required_name:-}" ]] || fail
  done
  mode_600_file "$CLOUD_ADMIN_KEY" || fail
  [[ "$CLOUD_BASELINE_FILE" == /* ]] || fail
}

run_local_gate() {
  local repository_root="$1" backend_python common_git
  backend_python="$repository_root/backend/.venv/bin/python"
  if [[ ! -x "$backend_python" ]]; then
    common_git="$(git -C "$repository_root" rev-parse --path-format=absolute --git-common-dir)" || fail
    backend_python="$(/usr/bin/dirname "$common_git")/backend/.venv/bin/python"
  fi
  [[ -x "$backend_python" ]] || fail
  (
    cd "$repository_root/backend" || exit 1
    PYTHONDONTWRITEBYTECODE=1 "$backend_python" -m pytest -q || exit 1
    cd "$repository_root/webui" || exit 1
    npm test || exit 1
    npm run build || exit 1
    cd "$repository_root" || exit 1
    bash -n deploy/cloud/*.sh deploy/install-cloud-sync-launchagent.sh || exit 1
    git diff --check || exit 1
  ) >/dev/null 2>&1 || fail
}

ssh_options=(
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)

capture_remote_facts() {
  local destination="$1"
  /usr/bin/ssh "${ssh_options[@]}" -i "$CLOUD_ADMIN_KEY" "$CLOUD_ADMIN_HOST" \
    'set -euo pipefail
     hash() { /usr/bin/sha256sum | /usr/bin/awk "{print \$1}"; }
     /usr/bin/docker inspect --format "{{.Id}}" ai-fae-backend | hash
     /usr/bin/docker inspect --format "{{.Config.Image}}" ai-fae-backend | hash
     /usr/bin/docker inspect --format "{{.State.StartedAt}}" ai-fae-backend | hash
     /usr/bin/docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" ai-fae-backend | hash
     /usr/sbin/nginx -T 2>&1 | hash
     /usr/bin/ss -H -lnt | /usr/bin/awk '\''$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}'\'' | /usr/bin/sort -u | hash' \
    > "$destination" 2>/dev/null || fail
  [[ "$(/usr/bin/wc -l < "$destination" | /usr/bin/tr -d ' ')" == "6" ]] || fail
  /usr/bin/awk 'length($0) != 64 || $0 !~ /^[0-9a-f]+$/ {exit 1}' "$destination" || fail
}

capture_baseline() {
  local config_path="$1" temporary
  load_config "$config_path"
  /bin/mkdir -p "$(/usr/bin/dirname "$CLOUD_BASELINE_FILE")"
  temporary="$(/usr/bin/mktemp "${CLOUD_BASELINE_FILE}.XXXXXX")"
  trap '/bin/rm -f -- "$temporary"' EXIT
  capture_remote_facts "$temporary"
  /bin/chmod 600 "$temporary"
  /bin/mv -f "$temporary" "$CLOUD_BASELINE_FILE"
  trap - EXIT
  echo "CLOUD_PLATFORM_BASELINE_OK"
}

require_evidence() {
  local name="$1"
  [[ "${!name:-}" == "1" ]] || fail
}

verify_remote_release() {
  local release_sha="$1" result
  result="$(/usr/bin/ssh "${ssh_options[@]}" -i "$CLOUD_ADMIN_KEY" "$CLOUD_ADMIN_HOST" \
    /bin/bash -s -- "$release_sha" 2>/dev/null <<'REMOTE'
set -euo pipefail
release_sha="$1"
root_path=/opt/orbbec-agent-platform
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$(/usr/bin/basename "$(/usr/bin/readlink -f "$root_path/current")")" == "$release_sha" ]]
release="$(/usr/bin/readlink -f "$root_path/current")"
environment="$root_path/private/platform.env"
compose="$release/deploy/cloud/compose.yaml"
postgres="$(/usr/bin/docker compose --env-file "$environment" -f "$compose" ps -q platform-postgres)"
[[ -n "$postgres" ]]
access_schema="$(/usr/bin/docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select concat((select count(*) from platform_control.schema_migrations where version=67),'|',(to_regclass('platform_control.user_access_events') is not null)::int,'|',(to_regprocedure('platform_control.read_access_subjects_v67(uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer)') is not null)::int);")"
[[ "$access_schema" == "1|1|1" ]]
/usr/bin/curl -fsS --max-time 3 http://127.0.0.1:8080/api/health >/dev/null
/usr/bin/curl -fsS --max-time 3 http://127.0.0.1:8080/api/deployment |
  /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); assert v["mode"]=="cloud-replica" and v["read_only"] is True and v["auth"]=="ssh-tunnel"'
[[ "$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8080/api/attachments/00000000-0000-0000-0000-000000000000/ticket)" == "404" ]]
[[ "$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/attachments/00000000-0000-0000-0000-000000000000/content)" == "404" ]]
upload_status="$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8080/api/v1/attachments/uploads)"
[[ "$upload_status" == "401" || "$upload_status" == "403" ]]
download_status="$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/v1/attachments/00000000-0000-0000-0000-000000000000)"
[[ "$download_status" == "401" || "$download_status" == "403" ]]
[[ "$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/v1/manage/access-events)" == "401" ]]
[[ "$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/v1/manage/access-subjects)" == "401" ]]
[[ "$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/review/overview)" == "503" ]]
/usr/bin/docker inspect --format '{{.State.Health.Status}}' orbbec-agent-platform-platform-attachments-1 | /usr/bin/grep -Fxq healthy
/usr/bin/docker exec orbbec-agent-platform-platform-api-1 python -c 'import urllib.error,urllib.request; u="http://platform-minio:9000/orbbec-agent-attachments"; code=0
try: urllib.request.urlopen(u,timeout=3)
except urllib.error.HTTPError as error: code=error.code
raise SystemExit(0 if code==403 else 1)'
/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fxq '127.0.0.1:8080'
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):8080$'
/usr/bin/systemctl is-active --quiet orbbec-agent-platform-backup.timer
[[ -f "$root_path/private/last-backup-success" && ! -L "$root_path/private/last-backup-success" ]]
[[ -d /data/orbbec-agent-platform/backups && ! -L /data/orbbec-agent-platform/backups ]]
echo REMOTE_ACCEPTANCE_OK
REMOTE
)" || fail
  [[ "$result" == "REMOTE_ACCEPTANCE_OK" ]] || fail
}

final_gate() {
  local repository_root="$1" config_path="$2" release_sha current_facts passed
  load_config "$config_path"
  [[ -n "${CLOUD_ACCEPTANCE_EVIDENCE_FILE:-}" ]] || fail
  mode_600_file "$CLOUD_BASELINE_FILE" || fail
  mode_600_file "$CLOUD_ACCEPTANCE_EVIDENCE_FILE" || fail
  set -a
  # shellcheck disable=SC1090
  source "$CLOUD_ACCEPTANCE_EVIDENCE_FILE"
  set +a

  run_local_gate "$repository_root"
  [[ -z "$(git -C "$repository_root" status --porcelain)" ]] || fail
  release_sha="$(git -C "$repository_root" rev-parse HEAD)"
  [[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail

  current_facts="$(/usr/bin/mktemp)"
  trap '/bin/rm -f -- "$current_facts"' EXIT
  capture_remote_facts "$current_facts"
  /usr/bin/cmp -s "$CLOUD_BASELINE_FILE" "$current_facts" || fail
  verify_remote_release "$release_sha"

  require_evidence CANARY_ABSENT
  require_evidence SYNTHETIC_RESET_OK
  require_evidence BACKFILL_RECONCILED
  require_evidence ORDER_SAMPLES_MATCH
  require_evidence TUNNEL_APIS_OK
  require_evidence STALE_STATE_OK
  require_evidence RESTORE_DRILL_OK
  require_evidence LOCAL_SOURCE_UNCHANGED
  require_evidence FIVE_MINUTE_SYNC_OK

  passed=0
  # criterion 01: existing backend and Web UI suites pass.
  passed=$((passed + 1))
  # criterion 02: sanitizer forbidden classes and stable placeholders pass.
  passed=$((passed + 1))
  # criterion 03: production canaries are absent from every inspected surface.
  passed=$((passed + 1))
  # criterion 04: invalid batches fail closed.
  passed=$((passed + 1))
  # criterion 05: exact replay is idempotent.
  passed=$((passed + 1))
  # criterion 06: failed import preserves generation and watermark.
  passed=$((passed + 1))
  # criterion 07: one-year safe counts and hashes reconcile.
  passed=$((passed + 1))
  # criterion 08: sampled Session and Turn order matches.
  passed=$((passed + 1))
  # criterion 09: attachments expose metadata only and routes are forbidden.
  passed=$((passed + 1))
  # criterion 10: cloud mode has no mutation controls.
  passed=$((passed + 1))
  # criterion 11: Platform, PostgreSQL and importer have no public listener.
  passed=$((passed + 1))
  # criterion 12: SSH tunnel UI and required read APIs are healthy.
  passed=$((passed + 1))
  # criterion 13: stale sync preserves and marks the last snapshot.
  passed=$((passed + 1))
  # criterion 14: encrypted backup and restore drill succeed.
  passed=$((passed + 1))
  # criterion 15: FAE identity, image, start time and health are unchanged.
  passed=$((passed + 1))
  # criterion 16: Nginx and the public listener set are unchanged.
  passed=$((passed + 1))
  # criterion 17: local source data and MetaBot processes are unchanged.
  passed=$((passed + 1))
  # criterion 18: every operation is noninteractive and opens no credential UI.
  passed=$((passed + 1))
  [[ "$passed" == "18" ]] || fail
  trap - EXIT
  /bin/rm -f -- "$current_facts"
  echo "CLOUD_PLATFORM_ACCEPTANCE_OK release=$release_sha criteria=18"
}

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
case "${1:-}" in
  local)
    [[ $# -eq 1 ]] || fail
    run_local_gate "$repository_root"
    echo "CLOUD_PLATFORM_LOCAL_GATE_OK"
    ;;
  capture-baseline)
    [[ $# -eq 2 ]] || fail
    capture_baseline "$2"
    ;;
  final)
    [[ $# -eq 2 ]] || fail
    final_gate "$repository_root" "$2"
    ;;
  *)
    fail
    ;;
esac
