#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "DINGTALK_PRODUCTION_ROLLBACK_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && $# -eq 0 ]] || fail
platform_root=/opt/orbbec-agent-platform
state_path="$platform_root/private/dingtalk-production-cutover"
[[ -f "$state_path" && ! -L "$state_path" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$state_path")" == "600 root" ]] || fail
set -a
# shellcheck disable=SC1090
source "$state_path"
set +a
[[ "$BACKUP_PATH" == /root/nginx-backups/agent-platform-dingtalk-* ]] || fail
[[ "$RELEASE_PATH" == /opt/orbbec-agent-platform/releases/* ]] || fail
[[ "$PREVIOUS_RELEASE" == /opt/orbbec-agent-platform/releases/* ]] || fail
[[ "$PREVIOUS_ENVIRONMENT" == "$RELEASE_PATH/PREVIOUS_PLATFORM_ENV" ]] || fail

agent_available=/etc/nginx/sites-available/agent-domain.conf
agent_enabled=/etc/nginx/sites-enabled/agent-domain.conf
htpasswd=/etc/nginx/.htpasswd-agent-platform
basic_template="$RELEASE_PATH/deploy/cloud/agent-domain.basic-auth.nginx.conf"
[[ -f "$basic_template" && -f "$htpasswd" && -f "$BACKUP_PATH/agent-domain.conf" ]] || fail
[[ -f "$PREVIOUS_RELEASE/deploy/cloud/compose.yaml" && -f "$PREVIOUS_ENVIRONMENT" ]] || fail

current_fae_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)"
current_fae_started="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)"
[[ "$current_fae_id" == "$FAE_ID" && "$current_fae_started" == "$FAE_STARTED_AT" ]] || fail

environment_path="$platform_root/private/platform.env"
current_compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$RELEASE_PATH/deploy/cloud/compose.yaml")
current_services="$("${current_compose[@]}" config --services)"
services_to_stop=()
for service in platform-loopback platform-api platform-directory platform-dingtalk-stream platform-brain; do
  if /usr/bin/grep -Fxq "$service" <<<"$current_services"; then
    services_to_stop+=("$service")
  fi
done
[[ "${#services_to_stop[@]}" -gt 0 ]] || fail
"${current_compose[@]}" stop "${services_to_stop[@]}" >/dev/null
/usr/bin/python3 - "$PREVIOUS_ENVIRONMENT" "$environment_path.part" <<'PY'
import os,pathlib,sys
source,target=map(pathlib.Path,sys.argv[1:])
lines=source.read_text(encoding="utf-8").splitlines()
kept=[line for line in lines if not line.startswith((
    "PLATFORM_AGENT_BRAIN_ENABLED=","PLATFORM_AGENT_BRAIN_V2_ENABLED=",
))]
raw=("\n".join(kept+[
    "PLATFORM_AGENT_BRAIN_ENABLED=0",
    "PLATFORM_AGENT_BRAIN_V2_ENABLED=0",
])+"\n").encode()
descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
try:
    os.write(descriptor,raw); os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
/bin/chown root:root "$environment_path.part"
/bin/chmod 600 "$environment_path.part"
/bin/mv -f "$environment_path.part" "$environment_path"
/bin/ln -sfn "$PREVIOUS_RELEASE" "$platform_root/current"

previous_compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$PREVIOUS_RELEASE/deploy/cloud/compose.yaml")
previous_services="$("${previous_compose[@]}" config --services)"
services_to_start=()
for service in platform-api platform-directory platform-dingtalk-stream platform-brain platform-loopback; do
  if /usr/bin/grep -Fxq "$service" <<<"$previous_services"; then
    services_to_start+=("$service")
  fi
done
[[ "${#services_to_start[@]}" -gt 0 ]] || fail
"${previous_compose[@]}" up -d --force-recreate "${services_to_start[@]}" >/dev/null
for _attempt in $(/usr/bin/seq 1 40); do
  /usr/bin/curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1 && break
  /bin/sleep 1
done
/usr/bin/curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null || fail

/usr/bin/install -o root -g root -m 644 "$BACKUP_PATH/agent-domain.conf" "$agent_available.part"
/bin/mv -f "$agent_available.part" "$agent_available"
/bin/ln -sfn "$agent_available" "$agent_enabled"
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl reload nginx

[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 4 \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 https://agent.orbbec.com.cn/)" == "401" ]] || fail
[[ "$FAE_ID" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$FAE_STARTED_AT" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
/bin/rm -f -- "$state_path"
echo "DINGTALK_PRODUCTION_ROLLBACK_OK"
