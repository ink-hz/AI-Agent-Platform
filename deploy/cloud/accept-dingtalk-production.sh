#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "DINGTALK_PRODUCTION_ACCEPTANCE_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && $# -eq 2 ]] || fail
expected_release_sha="$1"
controlled_cookie_path="$2"
[[ "$expected_release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
[[ "$controlled_cookie_path" == /* && -f "$controlled_cookie_path" \
  && ! -L "$controlled_cookie_path" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$controlled_cookie_path")" == "600 root" ]] || fail
[[ "$(/usr/bin/stat -c '%s' "$controlled_cookie_path")" =~ ^[0-9]+$ \
  && "$(/usr/bin/stat -c '%s' "$controlled_cookie_path")" -le 8192 ]] || fail
platform_root=/opt/orbbec-agent-platform
cutover_lock_token="${PLATFORM_DINGTALK_CUTOVER_LOCK_TOKEN:-}"
[[ "$cutover_lock_token" =~ ^[0-9a-f-]{36}$ \
  && -d "$platform_root/private/agent-brain-action.lock" \
  && ! -L "$platform_root/private/agent-brain-action.lock" \
  && "$(/bin/cat "$platform_root/private/agent-brain-action.lock/owner")" == "$cutover_lock_token" \
  && ! -e "$platform_root/private/deploy-input.lock" ]] || fail
release_path="$(/usr/bin/readlink -f "$platform_root/current")"
release_sha="$(/usr/bin/basename "$release_path")"
environment_path="$platform_root/private/platform.env"
compose_path="$release_path/deploy/cloud/compose.yaml"
cutover_state="$platform_root/private/dingtalk-production-cutover"
agent_config=/etc/nginx/sites-available/agent-domain.conf
worker_public_keyring="$platform_root/private/execution-worker-public-keyring.json"
[[ "$release_sha" == "$expected_release_sha" ]] || fail
for path in "$environment_path" "$compose_path" "$cutover_state" "$agent_config" "$worker_public_keyring"; do
  [[ -f "$path" && ! -L "$path" ]] || fail
done
[[ "$(/usr/bin/stat -c '%a %U' "$cutover_state")" == "600 root" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$worker_public_keyring")" == "600 root" ]] || fail
set -a
# shellcheck disable=SC1090
source "$cutover_state"
set +a
[[ "${RELEASE_PATH:-}" == "$release_path" ]] || fail

compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
directory_id="$("${compose[@]}" ps -q platform-directory)"
[[ -n "$directory_id" ]] || fail
[[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$directory_id")" == "healthy" ]] || fail
profile_probe_json="$(/usr/bin/docker exec "$directory_id" \
  python -m app.control_plane.employee_profile_probe)" || fail
profile_gates="$(/usr/bin/python3 -c '
import json,sys,uuid
p=json.loads(sys.stdin.read())
keys={"generation_id","active_employee_count","real_name_present_count","mobile_present_count","primary_department_present_count"}
if set(p)!=keys: raise SystemExit(1)
generation_id=str(uuid.UUID(p["generation_id"]))
counts=[p[name] for name in ("active_employee_count","real_name_present_count","mobile_present_count","primary_department_present_count")]
if any(type(value) is not int for value in counts): raise SystemExit(1)
active,real_name,mobile,primary_department=counts
if active<=0 or primary_department!=active or any(value<0 or value>active for value in (real_name,mobile)): raise SystemExit(1)
print(":".join((generation_id,*map(str,counts))))
' <<<"$profile_probe_json")" || fail
unset profile_probe_json
IFS=: read -r profile_generation_id active_employee_count real_name_present_count mobile_present_count \
  primary_department_present_count <<<"$profile_gates"

for service in platform-postgres platform-api platform-loopback platform-directory platform-dingtalk-stream; do
  container_id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || fail
  [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$container_id")" == "healthy" ]] || fail
done
for service in platform-api platform-loopback platform-directory platform-dingtalk-stream; do
  container_id="$("${compose[@]}" ps -q "$service")"
  /usr/bin/docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id" |
    /usr/bin/grep -Fxq "PLATFORM_RELEASE_SHA=$expected_release_sha" || fail
done
service_ids_before="$("${compose[@]}" ps -q platform-postgres platform-api \
  platform-loopback platform-directory platform-dingtalk-stream)"
[[ -n "$service_ids_before" ]] || fail

postgres_id="$("${compose[@]}" ps -q platform-postgres)"
directory_gate_sql="$(/bin/cat <<'SQL'
WITH active_generation AS (
  SELECT state.active_generation_id, state.last_complete_at,
         generation.status, generation.source_schema_version
  FROM platform_control.directory_state AS state
  LEFT JOIN platform_control.directory_generations AS generation
    ON generation.generation_id=state.active_generation_id
  WHERE state.singleton
)
SELECT concat(
  active_generation_id::text, ':',
  (select count(*) from platform_control.internal_users where role='platform_owner' and status='active'), ':',
  (select count(*) from active_generation where active_generation_id is not null and status='complete' and source_schema_version=3 and last_complete_at > clock_timestamp() - interval '8 hours'), ':',
  (select count(*) from platform_control.worker_heartbeats where worker_name='dingtalk-directory-event' and status='healthy' and last_seen_at > clock_timestamp() - interval '2 minutes')
) FROM active_generation
SQL
)"
directory_gates="$(/usr/bin/docker exec "$postgres_id" psql -X -A -t \
  -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c \
  "$directory_gate_sql")" || fail
IFS=: read -r sql_generation_id owner_count fresh_generation_count heartbeat_count <<<"$directory_gates"
[[ "$sql_generation_id" == "$profile_generation_id" \
  && "$owner_count" =~ ^[0-9]+$ \
  && "$fresh_generation_count" =~ ^[0-9]+$ \
  && "$heartbeat_count" =~ ^[0-9]+$ \
  && "$owner_count" == "1" \
  && "$fresh_generation_count" == "1" \
  && "$heartbeat_count" == "1" ]] || fail

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
account_cookie_config="$(/usr/bin/mktemp)"
account_headers="$(/usr/bin/mktemp)"
account_body="$(/usr/bin/mktemp)"
cleanup_account_probe() {
  /bin/rm -f -- "$account_cookie_config" "$account_headers" "$account_body"
}
trap cleanup_account_probe EXIT
/usr/bin/python3 - "$controlled_cookie_path" "$account_cookie_config" <<'PY' || fail
import json
import pathlib
import sys

cookie = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not cookie or len(cookie) > 4096 or any(ord(char) < 0x21 or ord(char) > 0x7e for char in cookie):
    raise SystemExit(1)
path = pathlib.Path(sys.argv[2])
path.write_text(
    'header = "Accept: application/json"\n'
    'header = "Accept-Encoding: identity"\n'
    f'header = "Cookie: __Host-platform_session={cookie}"\n',
    encoding="utf-8",
)
path.chmod(0o600)
PY
/usr/bin/curl --noproxy '*' -fsS --max-time 8 --config "$account_cookie_config" \
  -D "$account_headers" -o "$account_body" \
  --resolve agent.orbbec.com.cn:443:127.0.0.1 \
  https://agent.orbbec.com.cn/api/v1/account || fail
account_profile_gates="$(/usr/bin/python3 - "$account_headers" "$account_body" <<'PY'
import json
import pathlib
import re
import sys
import uuid

headers = pathlib.Path(sys.argv[1]).read_text(encoding="latin-1")
body = pathlib.Path(sys.argv[2]).read_bytes()
if len(body) > 65536 or not re.search(r"^HTTP/[0-9.]+ 200 ", headers, re.MULTILINE):
    raise SystemExit(1)
cache_values = re.findall(r"^Cache-Control:\s*(.+)$", headers, re.MULTILINE | re.IGNORECASE)
cache = {item.strip().lower() for value in cache_values for item in value.split(",")}
if not {"private", "no-store"}.issubset(cache):
    raise SystemExit(1)
payload = json.loads(body.decode("utf-8"))
expected = {
    "internal_user_id", "display_name", "role", "departments", "gender",
    "real_name", "mobile", "primary_department", "observation_agent_ids",
    "directory_freshness", "hard_stale_read_only", "csrf_token",
}
if type(payload) is not dict or set(payload) != expected:
    raise SystemExit(1)
try:
    uuid.UUID(payload["internal_user_id"])
except (AttributeError, TypeError, ValueError):
    raise SystemExit(1)
if (
    payload["role"] not in {"member", "management_viewer", "platform_admin", "platform_owner"}
    or type(payload["departments"]) is not list
    or any(type(item) is not str or not item.strip() for item in payload["departments"])
    or payload["gender"] not in {None, "male", "female"}
    or type(payload["observation_agent_ids"]) is not list
    or any(type(item) is not str or not item.strip() for item in payload["observation_agent_ids"])
    or payload["directory_freshness"] not in {"fresh", "warning", "hard_stale"}
    or type(payload["hard_stale_read_only"]) is not bool
    or type(payload["csrf_token"]) is not str
):
    raise SystemExit(1)
checks = {
    "display_name_present": type(payload["display_name"]) is str and bool(payload["display_name"].strip()),
    "real_name_present": type(payload["real_name"]) is str and bool(payload["real_name"].strip()),
    "mobile_present": type(payload["mobile"]) is str and re.fullmatch(r"1[3-9][0-9]{9}", payload["mobile"]) is not None,
    "primary_department_present": type(payload["primary_department"]) is str and bool(payload["primary_department"].strip()),
}
if not all(checks.values()):
    raise SystemExit(1)
print(",".join(f"{key}=true" for key in sorted(checks)))
PY
)" || fail
/bin/rm -f -- "$account_cookie_config" "$account_headers" "$account_body"
trap - EXIT
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
[[ "$(/usr/bin/readlink -f "$platform_root/current")" == "$release_path" \
  && "${RELEASE_PATH:-}" == "$release_path" \
  && ! -e "$platform_root/private/deploy-input.lock" \
  && "$(/bin/cat "$platform_root/private/agent-brain-action.lock/owner")" == "$cutover_lock_token" ]] || fail
service_ids_after="$("${compose[@]}" ps -q platform-postgres platform-api \
  platform-loopback platform-directory platform-dingtalk-stream)"
[[ "$service_ids_after" == "$service_ids_before" ]] || fail

trap - EXIT
cleanup
echo "DINGTALK_PRODUCTION_ACCEPTANCE_OK release=$release_sha directory_gates=$directory_gates employee_profile_counts=$profile_gates account_profile=$account_profile_gates"
