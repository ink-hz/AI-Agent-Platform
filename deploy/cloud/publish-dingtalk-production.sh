#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "DINGTALK_PRODUCTION_PUBLISH_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" ]] || fail
owner_bootstrap=0
if [[ $# -eq 2 && "$2" == "--allow-unbound-owner" ]]; then
  owner_bootstrap=1
elif [[ $# -ne 1 ]]; then
  fail
fi
release_path="$1"
[[ "$release_path" == /opt/orbbec-agent-platform/releases/* ]] || fail
transaction="$release_path/deploy/cloud/dingtalk_nginx_transaction.py"
[[ -f "$transaction" && ! -L "$transaction" ]] || fail

platform_root=/opt/orbbec-agent-platform
environment_path="$platform_root/private/platform.env"
compose_path="$release_path/deploy/cloud/compose.yaml"
agent_available=/etc/nginx/sites-available/agent-domain.conf
agent_enabled=/etc/nginx/sites-enabled/agent-domain.conf
state_path="$platform_root/private/dingtalk-production-cutover"
[[ -f "$environment_path" && -f "$compose_path" && -f "$agent_available" ]] || fail
[[ ! -e "$state_path" ]] || fail
[[ -f "$release_path/PREVIOUS_RELEASE" && -f "$release_path/PREVIOUS_PLATFORM_ENV" ]] || fail
previous_release="$(/usr/bin/tr -d '\n' < "$release_path/PREVIOUS_RELEASE")"
[[ "$previous_release" == /opt/orbbec-agent-platform/releases/* ]] || fail
[[ -f "$previous_release/deploy/cloud/compose.yaml" ]] || fail

fae_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)"
fae_started_at="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)"
fae_health="$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)"
[[ "$fae_health" == "healthy" ]] || fail

compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
for service in platform-api platform-loopback platform-directory platform-dingtalk-stream; do
  container_id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || fail
  [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$container_id")" == "healthy" ]] || fail
done
directory_id="$("${compose[@]}" ps -q platform-directory)"
[[ -n "$directory_id" ]] || fail
[[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$directory_id")" == "healthy" ]] || fail
gender_probe_json="$(/usr/bin/docker exec "$directory_id" \
  python -m app.control_plane.gender_probe)" || fail
/usr/bin/python3 -c \
  'import json,sys; sys.exit(0 if json.loads(sys.stdin.read()).get("ready") is True else 1)' \
  <<<"$gender_probe_json" || fail
unset gender_probe_json

api_id="$("${compose[@]}" ps -q platform-api)"
/usr/bin/docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$api_id" |
  /usr/bin/grep -Fxq 'PLATFORM_IDENTITY_MODE=production' || fail
postgres_id="$("${compose[@]}" ps -q platform-postgres)"
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
if [[ "$owner_bootstrap" == "1" ]]; then
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

timestamp="$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)"
backup_path="/root/nginx-backups/agent-platform-dingtalk-$timestamp"
/usr/bin/install -d -o root -g root -m 700 "$backup_path"
/bin/cp -a "$agent_available" "$backup_path/agent-domain.conf"
/usr/bin/printf 'BACKUP_PATH=%q\nRELEASE_PATH=%q\nPREVIOUS_RELEASE=%q\nPREVIOUS_ENVIRONMENT=%q\nFAE_ID=%q\nFAE_STARTED_AT=%q\nOWNER_BOOTSTRAP=%q\n' \
  "$backup_path" "$release_path" "$previous_release" \
  "$release_path/PREVIOUS_PLATFORM_ENV" "$fae_id" "$fae_started_at" \
  "$owner_bootstrap" > "$state_path.part"
/bin/chown root:root "$state_path.part"
/bin/chmod 600 "$state_path.part"

rendered="$backup_path/agent-domain.dingtalk.conf"
/usr/bin/python3 "$transaction" "$agent_available" "$rendered" || fail
/bin/chown root:root "$rendered"
/bin/chmod 644 "$rendered"

rollback_required=1
rollback_on_failure() {
  if [[ "$rollback_required" == "1" ]]; then
    /bin/cp -a "$backup_path/agent-domain.conf" "$agent_available" 2>/dev/null || true
    /usr/sbin/nginx -t >/dev/null 2>&1 && /bin/systemctl reload nginx >/dev/null 2>&1 || true
    /bin/rm -f -- "$state_path.part"
  fi
}
trap rollback_on_failure EXIT
/usr/bin/install -o root -g root -m 644 "$rendered" "$agent_available.part"
/bin/mv -f "$agent_available.part" "$agent_available"
/bin/ln -sfn "$agent_available" "$agent_enabled"
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl reload nginx

response_headers="$backup_path/root-response.headers"
for _attempt in $(/usr/bin/seq 1 20); do
  code="$(/usr/bin/curl --noproxy '*' -sS -D "$response_headers" -o /dev/null -w '%{http_code}' --max-time 4 \
    --resolve agent.orbbec.com.cn:443:127.0.0.1 https://agent.orbbec.com.cn/ || true)"
  [[ "$code" == "302" ]] && break
  /bin/sleep 1
done
[[ "$code" == "302" ]] || fail
/usr/bin/tr -d '\r' < "$response_headers" |
  /usr/bin/grep -Fxiq 'location: /login' || fail
[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 4 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/login)" == "200" ]] || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 4 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/api/health |
  /usr/bin/python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)=={"status":"ok"} else 1)' || fail
[[ "$fae_id" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$fae_started_at" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
/bin/mv -f "$state_path.part" "$state_path"
rollback_required=0
trap - EXIT
if [[ "$owner_bootstrap" == "1" ]]; then
  echo "DINGTALK_PRODUCTION_OWNER_LOGIN_REQUIRED"
else
  echo "DINGTALK_PRODUCTION_PUBLISH_OK"
fi
