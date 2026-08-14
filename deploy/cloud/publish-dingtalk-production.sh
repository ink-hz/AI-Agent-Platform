#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "DINGTALK_PRODUCTION_PUBLISH_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && $# -eq 1 ]] || fail
release_path="$1"
[[ "$release_path" == /opt/orbbec-agent-platform/releases/* ]] || fail
template="$release_path/deploy/cloud/agent-domain.nginx.conf"
[[ -f "$template" && ! -L "$template" ]] || fail
for placeholder in __AGENT_DOMAIN__ __CERT_PATH__ __KEY_PATH__; do
  /usr/bin/grep -Fq "$placeholder" "$template" || fail
done
if /usr/bin/grep -Fq "__HTPASSWD_PATH__" "$template"; then
  fail
fi

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
api_id="$("${compose[@]}" ps -q platform-api)"
/usr/bin/docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$api_id" |
  /usr/bin/grep -Fxq 'PLATFORM_IDENTITY_MODE=production' || fail
postgres_id="$("${compose[@]}" ps -q platform-postgres)"
readiness="$(/usr/bin/docker exec "$postgres_id" psql -X -A -t \
  -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c \
  "select concat(
    (select count(*) from platform_control.internal_users where role='platform_owner' and status='active'), ':',
    (select count(*) from platform_control.directory_state where singleton and active_generation_id is not null and last_complete_at > clock_timestamp() - interval '8 hours'), ':',
    (select count(*) from platform_control.worker_heartbeats where worker_name='dingtalk-directory-event' and status='healthy' and last_seen_at > clock_timestamp() - interval '2 minutes')
  )")" || fail
[[ "$readiness" == "1:1:1" ]] || fail

timestamp="$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)"
backup_path="/root/nginx-backups/agent-platform-dingtalk-$timestamp"
/usr/bin/install -d -o root -g root -m 700 "$backup_path"
/bin/cp -a "$agent_available" "$backup_path/agent-domain.conf"
/usr/bin/printf 'BACKUP_PATH=%q\nRELEASE_PATH=%q\nPREVIOUS_RELEASE=%q\nPREVIOUS_ENVIRONMENT=%q\nFAE_ID=%q\nFAE_STARTED_AT=%q\n' \
  "$backup_path" "$release_path" "$previous_release" \
  "$release_path/PREVIOUS_PLATFORM_ENV" "$fae_id" "$fae_started_at" > "$state_path.part"
/bin/chown root:root "$state_path.part"
/bin/chmod 600 "$state_path.part"

rendered="$backup_path/agent-domain.dingtalk.conf"
/usr/bin/sed \
  -e 's|__AGENT_DOMAIN__|agent.orbbec.com.cn|g' \
  -e 's|__CERT_PATH__|/etc/letsencrypt/live/agent.orbbec.com.cn/fullchain.pem|g' \
  -e 's|__KEY_PATH__|/etc/letsencrypt/live/agent.orbbec.com.cn/privkey.pem|g' \
  "$template" > "$rendered"
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

for _attempt in $(/usr/bin/seq 1 20); do
  code="$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 4 \
    --resolve agent.orbbec.com.cn:443:127.0.0.1 https://agent.orbbec.com.cn/ || true)"
  [[ "$code" == "200" ]] && break
  /bin/sleep 1
done
[[ "${code:-}" == "200" ]] || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 4 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/api/health |
  /usr/bin/python3 -c 'import json,sys; assert json.load(sys.stdin)=={"status":"ok"}' || fail
[[ "$fae_id" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$fae_started_at" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
/bin/mv -f "$state_path.part" "$state_path"
rollback_required=0
trap - EXIT
echo "DINGTALK_PRODUCTION_PUBLISH_OK"
