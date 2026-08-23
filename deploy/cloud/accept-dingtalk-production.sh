#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "DINGTALK_PRODUCTION_ACCEPTANCE_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && $# -eq 0 ]] || fail
platform_root=/opt/orbbec-agent-platform
release_path="$(/usr/bin/readlink -f "$platform_root/current")"
release_sha="$(/usr/bin/basename "$release_path")"
environment_path="$platform_root/private/platform.env"
compose_path="$release_path/deploy/cloud/compose.yaml"
cutover_state="$platform_root/private/dingtalk-production-cutover"
agent_config=/etc/nginx/sites-available/agent-domain.conf
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
for path in "$environment_path" "$compose_path" "$cutover_state" "$agent_config"; do
  [[ -f "$path" && ! -L "$path" ]] || fail
done
[[ "$(/usr/bin/stat -c '%a %U' "$cutover_state")" == "600 root" ]] || fail
set -a
# shellcheck disable=SC1090
source "$cutover_state"
set +a

compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
directory_id="$("${compose[@]}" ps -q platform-directory)"
[[ -n "$directory_id" ]] || fail
[[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$directory_id")" == "healthy" ]] || fail
gender_probe_json="$(/usr/bin/docker exec "$directory_id" \
  python -m app.control_plane.gender_probe)" || fail
/usr/bin/python3 -c \
  'import json,sys; assert json.loads(sys.stdin.read()).get("ready") is True' \
  <<<"$gender_probe_json" || fail
unset gender_probe_json

for service in platform-postgres platform-api platform-loopback platform-directory platform-dingtalk-stream; do
  container_id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || fail
  [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$container_id")" == "healthy" ]] || fail
done

postgres_id="$("${compose[@]}" ps -q platform-postgres)"
[[ "$OWNER_BOOTSTRAP" == "0" || "$OWNER_BOOTSTRAP" == "1" ]] || fail
directory_gate_sql="$(/bin/cat <<'SQL'
WITH active_generation AS (
  SELECT state.active_generation_id, state.last_complete_at,
         generation.status, generation.source_schema_version
  FROM platform_control.directory_state AS state
  LEFT JOIN platform_control.directory_generations AS generation
    ON generation.generation_id=state.active_generation_id
  WHERE state.singleton
), gender_coverage AS (
  SELECT
    count(*) filter (where member.status='active') AS active_gender_count,
    count(*) filter (where member.status='active' and member.gender in ('male','female')) AS valid_gender_count,
    count(*) filter (where member.status='active' and (member.gender is null or member.gender not in ('male','female'))) AS null_invalid_gender_count
  FROM active_generation
  LEFT JOIN platform_control.directory_members AS member
    ON member.generation_id=active_generation.active_generation_id
)
SELECT concat(
  (select count(*) from platform_control.internal_users where role='platform_owner' and status='active'), ':',
  (select count(*) from active_generation where active_generation_id is not null and status='complete' and source_schema_version=2 and last_complete_at > clock_timestamp() - interval '8 hours'), ':',
  (select count(*) from platform_control.worker_heartbeats where worker_name='dingtalk-directory-event' and status='healthy' and last_seen_at > clock_timestamp() - interval '2 minutes'), ':',
  active_gender_count, ':', valid_gender_count, ':', null_invalid_gender_count
) FROM gender_coverage
SQL
)"
directory_gates="$(/usr/bin/docker exec "$postgres_id" psql -X -A -t \
  -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c \
  "$directory_gate_sql")" || fail
IFS=: read -r owner_count fresh_generation_count heartbeat_count \
  active_gender_count valid_gender_count null_invalid_gender_count <<<"$directory_gates"
expected_owner_count="1"
if [[ "$OWNER_BOOTSTRAP" == "1" ]]; then
  expected_owner_count="0"
fi
[[ "$owner_count" =~ ^[0-9]+$ \
  && "$fresh_generation_count" =~ ^[0-9]+$ \
  && "$heartbeat_count" =~ ^[0-9]+$ \
  && "$active_gender_count" =~ ^[0-9]+$ \
  && "$valid_gender_count" =~ ^[0-9]+$ \
  && "$null_invalid_gender_count" =~ ^[0-9]+$ \
  && "$owner_count" == "$expected_owner_count" \
  && "$fresh_generation_count" == "1" \
  && "$heartbeat_count" == "1" \
  && "$active_gender_count" -gt 0 \
  && "$active_gender_count" -eq "$valid_gender_count" \
  && "$null_invalid_gender_count" -eq 0 ]] || fail

/usr/sbin/nginx -t >/dev/null 2>&1 || fail
! /usr/bin/grep -Fq 'auth_basic "Orbbec Agent Platform";' "$agent_config" || fail
! /usr/bin/grep -Fq 'orbbec-agent-demo-preview.conf' "$agent_config" || fail
/usr/bin/grep -Fq 'proxy_read_timeout 360s;' "$agent_config" || fail
/usr/bin/grep -Fq 'proxy_set_header X-Forwarded-For $remote_addr;' "$agent_config" || fail

headers="$(/usr/bin/mktemp)"
body="$(/usr/bin/mktemp)"
cleanup() { /bin/rm -f -- "$headers" "$body"; }
trap cleanup EXIT
/usr/bin/curl --noproxy '*' -sS -D "$headers" -o "$body" --max-time 8 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/ || fail
/usr/bin/grep -Eq '^HTTP/[0-9.]+ 302 ' "$headers" || fail
/usr/bin/tr -d '\r' < "$headers" | /usr/bin/grep -Fxiq 'location: /login' || fail
! /usr/bin/grep -Eqi '^WWW-Authenticate:' "$headers" || fail
/usr/bin/curl --noproxy '*' -sS -o "$body" --max-time 8 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/login || fail
/usr/bin/grep -Fq 'platform-identity-mode' "$body" || fail
[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 8 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/api/v1/account)" == "401" ]] || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 8 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/api/health |
  /usr/bin/python3 -c 'import json,sys; assert json.load(sys.stdin)=={"status":"ok"}' || fail

/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fxq '127.0.0.1:8080' || fail
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):(8080|5432)$' || fail
[[ "$FAE_ID" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$FAE_STARTED_AT" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
[[ "$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)" == "healthy" ]] || fail
/usr/bin/openssl x509 -in /etc/letsencrypt/live/agent.orbbec.com.cn/cert.pem \
  -noout -checkend 604800 >/dev/null 2>&1 || fail

trap - EXIT
cleanup
echo "DINGTALK_PRODUCTION_ACCEPTANCE_OK release=$release_sha directory_gates=$directory_gates"
