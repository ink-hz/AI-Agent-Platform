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
worker_public_keyring="$platform_root/private/execution-worker-public-keyring.json"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
for path in "$environment_path" "$compose_path" "$cutover_state" "$agent_config" "$worker_public_keyring"; do
  [[ -f "$path" && ! -L "$path" ]] || fail
done
[[ "$(/usr/bin/stat -c '%a %U' "$cutover_state")" == "600 root" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$worker_public_keyring")" == "600 root" ]] || fail
set -a
# shellcheck disable=SC1090
source "$cutover_state"
set +a

compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
for service in platform-postgres platform-api platform-loopback platform-directory platform-dingtalk-stream; do
  container_id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || fail
  [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$container_id")" == "healthy" ]] || fail
done

postgres_id="$("${compose[@]}" ps -q platform-postgres)"
readiness="$(/usr/bin/docker exec "$postgres_id" psql -X -A -t \
  -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c \
  "select concat(
    (select count(*) from platform_control.internal_users where role='platform_owner' and status='active'), ':',
    (select count(*) from platform_control.directory_state where singleton and active_generation_id is not null and last_complete_at > clock_timestamp() - interval '8 hours'), ':',
    (select count(*) from platform_control.worker_heartbeats where worker_name='dingtalk-directory-event' and status='healthy' and last_seen_at > clock_timestamp() - interval '2 minutes')
  )")" || fail
[[ "$readiness" == "1:1:1" ]] || fail

worker_identity="$(/usr/bin/python3 - "$worker_public_keyring" <<'PY'
import base64
import hashlib
import json
import pathlib
import re
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if set(value) != {"worker_id", "key_id", "public_key_base64url", "allowed_agent_ids"}:
    raise SystemExit(1)
if (
    value["worker_id"] != "agentops-mac-primary"
    or not isinstance(value["key_id"], str)
    or re.fullmatch(r"worker-v[1-9][0-9]*", value["key_id"]) is None
):
    raise SystemExit(1)
expected_agents = ['hr-bot', 'fae-bot', 'marketing-prospecting-bot', 'marketing-inbound-bot', 'marketing-voice-bot', 'marketing-intelligence-bot', 'marketing-gtm-bot', 'agent-brain-bot']
if value["allowed_agent_ids"] != expected_agents:
    raise SystemExit(1)
public_key = base64.urlsafe_b64decode(value["public_key_base64url"] + "=")
if len(public_key) != 32:
    raise SystemExit(1)
if base64.urlsafe_b64encode(public_key).decode().rstrip("=") != value["public_key_base64url"]:
    raise SystemExit(1)
print(
    value["worker_id"],
    value["key_id"],
    hashlib.sha256(public_key).hexdigest(),
    json.dumps(expected_agents,separators=(",",":")),
)
PY
)" || fail
read -r expected_worker_id expected_key_id public_key_sha256 expected_agents_json <<<"$worker_identity"
[[ "$expected_worker_id" == "agentops-mac-primary" && "$expected_key_id" =~ ^worker-v[1-9][0-9]*$ && "$public_key_sha256" =~ ^[0-9a-f]{64}$ && -n "$expected_agents_json" ]] || fail
relay_identity="$(/usr/bin/docker exec "$postgres_id" psql -X -A -t \
  -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 \
  -v expected_key_id="$expected_key_id" -c \
  "select concat(worker.status, ':', worker_key.status, ':',
    worker.last_seen_at > clock_timestamp() - interval '60 seconds', ':',
    encode(sha256(worker_key.public_key), 'hex'), ':',
    array_to_json(worker.allowed_agent_ids)::text)
   from platform_control.execution_workers worker
   join platform_control.execution_worker_keys worker_key using(worker_id)
   where worker.worker_id='agentops-mac-primary'
     and worker_key.key_id=:'expected_key_id'
     and worker.status='active'
     and worker_key.status='active'")" || fail
[[ "$relay_identity" == "active:active:t:$public_key_sha256:$expected_agents_json" ]] || fail

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
  /usr/bin/python3 -c 'import json,sys; raise SystemExit(json.load(sys.stdin)!={"status":"ok"})' || fail

/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fxq '127.0.0.1:8080' || fail
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):(8080|5432)$' || fail
# The seven MetaBot listeners 9101-9108 must not exist on any IPv4/IPv6 address.
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '.*:910[1-8]$' || fail
[[ "$FAE_ID" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$FAE_STARTED_AT" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
[[ "$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)" == "healthy" ]] || fail
/usr/bin/openssl x509 -in /etc/letsencrypt/live/agent.orbbec.com.cn/cert.pem \
  -noout -checkend 604800 >/dev/null 2>&1 || fail

trap - EXIT
cleanup
echo "DINGTALK_PRODUCTION_ACCEPTANCE_OK release=$release_sha"
