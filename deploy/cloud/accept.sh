#!/bin/bash
set -eEuo pipefail
umask 077

fail() {
  echo "AGENT_BRAIN_ACCEPTANCE_FAILED" >&2
  exit 1
}

[[ $# -eq 2 && "$1" == /* ]] || fail
[[ "$(/usr/bin/id -un)" == "neo" ]] || fail
config_path="$1"
action="$2"
[[ "$action" == "preflight" || "$action" == "reference" || "$action" == "routes" || "$action" == "release" || "$action" == "accept" || "$action" == "rollback" || "$action" == "restore" ]] || fail
[[ -f "$config_path" && ! -L "$config_path" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %u' "$config_path")" == "600 $(/usr/bin/id -u)" ]] || fail

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
python="$repository_root/backend/.venv/bin/python"
[[ -x "$python" ]] || fail

config_value() {
  "$python" - "$config_path" "$1" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
field = sys.argv[2]
value = json.loads(path.read_bytes())
common_keys = {
    "schema_version",
    "member_cookie_file", "owner_cookie_file", "hr_prompt_file",
    "interruption_prompt_file", "relay_acceptance_config", "evidence_file",
}
if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
    raise SystemExit(1)
schema_version = value["schema_version"]
expected_keys = common_keys | ({"viewer_cookie_file"} if schema_version == 3 else set())
if schema_version not in {2, 3} or set(value) != expected_keys:
    raise SystemExit(1)
if field == "schema_version":
    print(schema_version)
    raise SystemExit(0)
selected = value.get(field)
if not isinstance(selected, str) or not selected or "\n" in selected or "\r" in selected or "\0" in selected:
    raise SystemExit(1)
if not pathlib.Path(selected).is_absolute():
    raise SystemExit(1)
print(selected)
PY
}

cloud_admin_host=root@47.106.112.69
cloud_admin_key=/Users/neo/.ssh/orbbec_aliyun_ed25519
config_schema_version="$(config_value schema_version)" || fail
member_cookie_file="$(config_value member_cookie_file)" || fail
owner_cookie_file="$(config_value owner_cookie_file)" || fail
viewer_cookie_file=""
if [[ "$config_schema_version" == "3" ]]; then
  viewer_cookie_file="$(config_value viewer_cookie_file)" || fail
fi
hr_prompt_file="$(config_value hr_prompt_file)" || fail
interruption_prompt_file="$(config_value interruption_prompt_file)" || fail
relay_acceptance_config="$(config_value relay_acceptance_config)" || fail
evidence_file="$(config_value evidence_file)" || fail

require_action_identity_schema() {
  case "$action" in
    release|accept|restore)
      [[ "$config_schema_version" == "3" && -n "$viewer_cookie_file" ]] || fail
      ;;
  esac
}

require_private_file() {
  local path="$1" maximum="$2"
  [[ -f "$path" && ! -L "$path" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %u' "$path")" == "600 $(/usr/bin/id -u)" ]] || fail
  size="$(/usr/bin/stat -f '%z' "$path")"
  [[ "$size" =~ ^[0-9]+$ && "$size" -gt 0 && "$size" -le "$maximum" ]] || fail
}

require_private_file "$cloud_admin_key" 16384
[[ "$cloud_admin_key" == /Users/neo/.ssh/orbbec_aliyun_ed25519 ]] || fail
ssh_options=(
  -i "$cloud_admin_key"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)

remote() {
  /usr/bin/ssh "${ssh_options[@]}" "$cloud_admin_host" "$@"
}

agentops_control=/Library/PrivilegedHelperTools/orbbec-agentops-control
run_agentops_control() {
  [[ $# -eq 1 ]] || fail
  case "$1" in
    relay-canary|worker-stop|worker-restore|metabot-release-sha|agent-team-release-sha) ;;
    *) fail ;;
  esac
  [[ -x "$agentops_control" && ! -L "$agentops_control" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$agentops_control")" == "755 root wheel" ]] || fail
  /usr/bin/sudo -n -H -u agentops "$agentops_control" "$1"
}

action_lock_token=""
action_lock_acquired=0
local_action_lock="$(/usr/bin/dirname "$config_path")/.agent-brain-action.lock"

release_action_lock() {
  [[ "$action_lock_acquired" == "1" ]] || return 0
  remote /bin/bash -s -- "$action_lock_token" <<'REMOTE' || return 1
set -euo pipefail
token="$1"; lock=/opt/orbbec-agent-platform/private/agent-brain-action.lock
[[ "$token" =~ ^[0-9a-f-]{36}$ && -d "$lock" && ! -L "$lock" ]] || exit 1
[[ "$(cat "$lock/owner")" == "$token" ]] || exit 1
tombstone="$lock.releasing.$token"
[[ ! -e "$tombstone" && ! -L "$tombstone" ]] || exit 1
mv "$lock" "$tombstone"
rm -f -- "$tombstone/owner"
rmdir "$tombstone"
REMOTE
  [[ -d "$local_action_lock" && ! -L "$local_action_lock" ]] || return 1
  [[ "$(<"$local_action_lock/owner")" == "$action_lock_token" ]] || return 1
  /bin/rm -f -- "$local_action_lock/owner"
  /bin/rmdir "$local_action_lock"
  action_lock_acquired=0
}

action_lock_exit() {
  status="$?"
  trap - ERR EXIT
  release_action_lock || status=1
  exit "$status"
}

acquire_action_lock() {
  local parent
  parent="$(/usr/bin/dirname "$config_path")"
  [[ -d "$parent" && ! -L "$parent" && "$(/usr/bin/stat -f '%Lp %u' "$parent")" == "700 $(/usr/bin/id -u)" ]] || fail
  action_lock_token="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  [[ "$action_lock_token" =~ ^[0-9a-f-]{36}$ ]] || fail
  /bin/mkdir -m 700 "$local_action_lock" || fail
  if ! /usr/bin/printf '%s\n' "$action_lock_token" > "$local_action_lock/owner" ||
     ! /bin/chmod 600 "$local_action_lock/owner"; then
    /bin/rm -f -- "$local_action_lock/owner"
    /bin/rmdir "$local_action_lock"
    fail
  fi
  if ! remote /bin/bash -s -- "$action_lock_token" <<'REMOTE'
set -euo pipefail
umask 077
token="$1"; lock=/opt/orbbec-agent-platform/private/agent-brain-action.lock
[[ "$token" =~ ^[0-9a-f-]{36}$ && ! -e "$lock" && ! -L "$lock" ]] || exit 1
complete=0
cleanup_lock() {
  status="$?"
  trap - EXIT
  if [[ "$complete" == "0" ]]; then rm -f -- "$lock/owner"; rmdir "$lock" 2>/dev/null || true; fi
  exit "$status"
}
trap cleanup_lock EXIT
mkdir -m 700 "$lock"
printf '%s\n' "$token" > "$lock/owner"
chmod 600 "$lock/owner"
complete=1
trap - EXIT
REMOTE
  then
    /bin/rm -f -- "$local_action_lock/owner"
    /bin/rmdir "$local_action_lock"
    fail
  fi
  action_lock_acquired=1
  trap action_lock_exit EXIT
}

workspace_non_regression_snapshot() {
(
  set -euo pipefail
  local snapshot_dir index url upstream_owner owner_count
  snapshot_dir="$(/usr/bin/mktemp -d)"
  trap '/bin/rm -rf -- "$snapshot_dir"' EXIT
  local -a urls=(
    'https://agent.orbbec.com.cn/'
    'https://agent.orbbec.com.cn/office/'
    'https://agent.orbbec.com.cn/office/?view=services'
    'https://fae.orbbec.com.cn/'
    'https://agent.orbbec.com.cn/voc/'
  )

  remote /usr/bin/python3 - <<'PY' > "$snapshot_dir/upstream-owners" || return 1
import hashlib
import re
import subprocess


def block(value: str, selector: str, *, last: bool = False) -> str:
    start = value.rindex(selector) if last else value.index(selector)
    depth = 0
    opened = False
    for index in range(start, len(value)):
        if value[index] == "{":
            depth += 1
            opened = True
        elif value[index] == "}":
            depth -= 1
            if opened and depth == 0:
                return value[start : index + 1]
    raise ValueError("nginx block incomplete")


def proxy(value: str, selector: str, *, last: bool = False) -> str:
    selected = block(value, selector, last=last)
    matches = re.findall(r"(?m)^\s*proxy_pass\s+([^;]+);", selected)
    if len(matches) != 1:
        raise ValueError("workspace route owner is ambiguous")
    return matches[0]


rendered = subprocess.run(
    ["/usr/sbin/nginx", "-T"],
    check=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
).stdout
servers = []
offset = 0
while True:
    match = re.search(r"(?m)^\s*server\s*\{", rendered[offset:])
    if match is None:
        break
    start = offset + match.start()
    selected = block(rendered[start:], "server {")
    offset = start + len(selected)
    servers.append(selected)


def active_https_server(hostname: str) -> str:
    named = [
        value
        for value in servers
        if re.search(
            rf"(?m)^\s*server_name\s+[^;]*\b{re.escape(hostname)}\b[^;]*;",
            value,
        )
        and re.search(r"(?m)^\s*listen\s+(?:[^; ]+:)?443\b[^;]*;", value)
    ]
    if len(named) != 1:
        raise ValueError(f"active HTTPS server for {hostname} is ambiguous")
    return named[0]


agent_config = active_https_server("agent.orbbec.com.cn")
public_fae = active_https_server("fae.orbbec.com.cn")
public_fae_owner = "nginx-server-sha256:" + hashlib.sha256(
    public_fae.encode()
).hexdigest()

print(proxy(agent_config, "location / {", last=True))
print(proxy(agent_config, "location ^~ /office/ {"))
print(proxy(agent_config, "location ^~ /office/ {"))
print(public_fae_owner)
print(proxy(agent_config, "location ^~ /voc/ {"))
PY
  owner_count="$(/usr/bin/wc -l < "$snapshot_dir/upstream-owners" | /usr/bin/tr -d ' ')"
  [[ "$owner_count" == "5" ]] || return 1

  for index in "${!urls[@]}"; do
    url="${urls[$index]}"
    upstream_owner="$(/usr/bin/sed -n "$((index + 1))p" "$snapshot_dir/upstream-owners")"
    [[ -n "$upstream_owner" ]] || return 1
    /usr/bin/curl --noproxy '*' --silent --show-error --max-time 15 \
      -D "$snapshot_dir/$index.headers" -o "$snapshot_dir/$index.body" \
      "$url" || return 1
    "$python" - "$url" "$upstream_owner" "$snapshot_dir/$index.headers" \
      "$snapshot_dir/$index.body" <<'PY' || return 1
import hashlib
import json
import pathlib
import sys

url, upstream_owner, header_path, body_path = sys.argv[1:]
raw_headers = pathlib.Path(header_path).read_text(encoding="iso-8859-1")
blocks = [block for block in raw_headers.replace("\r\n", "\n").split("\n\n") if block]
block = next((item for item in reversed(blocks) if item.startswith("HTTP/")), "")
lines = block.splitlines()
if not lines or len(lines[0].split()) < 2:
    raise SystemExit(1)
headers = {}
for line in lines[1:]:
    if ":" in line:
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
security_names = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
)
snapshot = {
    "url": url,
    "status": int(lines[0].split()[1]),
    "location": headers.get("location", ""),
    "content_marker": hashlib.sha256(pathlib.Path(body_path).read_bytes()).hexdigest(),
    "upstream_owner": upstream_owner,
    "security_headers": {name: headers.get(name, "") for name in security_names},
}
print(json.dumps(snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
PY
  done
)
}

route_non_regression_snapshot() {
(
  set -euo pipefail
  local snapshot_dir index url
  snapshot_dir="$(/usr/bin/mktemp -d)"
  trap '/bin/rm -rf -- "$snapshot_dir"' EXIT
  local -a urls=(
    'https://agent.orbbec.com.cn/'
    'https://agent.orbbec.com.cn/office/'
    'https://agent.orbbec.com.cn/office/?view=services'
    'https://fae.orbbec.com.cn/'
  )

  for index in "${!urls[@]}"; do
    url="${urls[$index]}"
    /usr/bin/curl --noproxy '*' --silent --show-error --max-time 15 \
      -D "$snapshot_dir/$index.headers" -o "$snapshot_dir/$index.body" \
      "$url" || return 1
    "$python" - "$url" "$snapshot_dir/$index.headers" \
      "$snapshot_dir/$index.body" <<'PY' || return 1
import hashlib
import json
import pathlib
import sys

url, header_path, body_path = sys.argv[1:]
raw_headers = pathlib.Path(header_path).read_text(encoding="iso-8859-1")
blocks = [block for block in raw_headers.replace("\r\n", "\n").split("\n\n") if block]
block = next((item for item in reversed(blocks) if item.startswith("HTTP/")), "")
lines = block.splitlines()
if not lines or len(lines[0].split()) < 2:
    raise SystemExit(1)
headers = {}
for line in lines[1:]:
    if ":" in line:
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
security_names = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
)
snapshot = {
    "url": url,
    "status": int(lines[0].split()[1]),
    "location": headers.get("location", ""),
    "content_marker": hashlib.sha256(pathlib.Path(body_path).read_bytes()).hexdigest(),
    "security_headers": {name: headers.get(name, "") for name in security_names},
}
print(json.dumps(snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
PY
  done
)
}

verify_canonical_workspace_routes() {
  remote /usr/bin/python3 - <<'PY' || return 1
import re
import subprocess


def block(value: str, selector: str) -> str:
    start = value.index(selector)
    depth = 0
    opened = False
    for index in range(start, len(value)):
        if value[index] == "{":
            depth += 1
            opened = True
        elif value[index] == "}":
            depth -= 1
            if opened and depth == 0:
                return value[start : index + 1]
    raise ValueError("nginx block incomplete")


rendered = subprocess.run(
    ["/usr/sbin/nginx", "-T"],
    check=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
).stdout
servers = []
offset = 0
while True:
    match = re.search(r"(?m)^\s*server\s*\{", rendered[offset:])
    if match is None:
        break
    start = offset + match.start()
    selected = block(rendered[start:], "server {")
    offset = start + len(selected)
    servers.append(selected)
agent_servers = [
    value
    for value in servers
    if re.search(r"(?m)^\s*server_name\s+[^;]*\bagent\.orbbec\.com\.cn\b[^;]*;", value)
    and re.search(r"(?m)^\s*listen\s+(?:[^; ]+:)?443\b[^;]*;", value)
]
if len(agent_servers) != 1:
    raise SystemExit(1)
agent = agent_servers[0]
expected = {
    "location ^~ /fae/manage/ {": "http://127.0.0.1:8080",
    "location ^~ /fae/ {": "http://127.0.0.1:8000",
    "location ^~ /voc/ {": "http://172.29.0.3:18130",
    "location ^~ /office/ {": "http://127.0.0.1:8011",
    "location ^~ /admin/ {": "http://127.0.0.1:8080",
}
for selector, owner in expected.items():
    selected = block(agent, selector)
    proxies = re.findall(r"(?m)^\s*proxy_pass\s+([^;]+);", selected)
    if proxies != [owner]:
        raise SystemExit(1)
catch_all = block(agent, "location / {")
catch_all_proxies = re.findall(
    r"(?m)^\s*proxy_pass\s+([^;]+);", catch_all
)
if catch_all_proxies != ["http://127.0.0.1:8080"]:
    raise SystemExit(1)
location_selectors = re.findall(
    r"(?m)^\s*location\s+([^\{]+?)\s*\{", agent
)
for namespace in ("/hr", "/marketing"):
    if any(namespace in selector for selector in location_selectors):
        raise SystemExit(1)
PY
  [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error -o /dev/null -w '%{http_code}' --max-time 15 https://agent.orbbec.com.cn/fae)" == "308" ]] || return 1
  [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error -o /dev/null -w '%{http_code}' --max-time 15 https://agent.orbbec.com.cn/fae/health)" == "404" ]] || return 1
  [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error -o /dev/null -w '%{http_code}' --max-time 15 https://agent.orbbec.com.cn/fae/manage)" == "308" ]] || return 1
  [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error -o /dev/null -w '%{http_code}' --max-time 15 https://agent.orbbec.com.cn/voc)" == "308" ]] || return 1
  [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error -o /dev/null -w '%{http_code}' --max-time 15 https://agent.orbbec.com.cn/voc/health)" == "404" ]] || return 1
}

remote_fae_snapshot() {
  remote /bin/bash -s <<'REMOTE'
set -euo pipefail
container=ai-fae-backend
printf '%s\n' \
  "id=$(docker inspect --format '{{.Id}}' "$container")" \
  "image=$(docker inspect --format '{{.Image}}' "$container")" \
  "started=$(docker inspect --format '{{.State.StartedAt}}' "$container")" \
  "restart=$(docker inspect --format '{{.RestartCount}}' "$container")" \
  "config=$(docker inspect --format '{{json .Config}}' "$container" | sha256sum | awk '{print $1}')" \
  "fae_mounts=$(docker inspect --format '{{json .Mounts}}' "$container" | sha256sum | awk '{print $1}')" \
  "health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")" \
  "fae_domain_hash=$(curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | sha256sum | awk '{print $1}')" \
  "fae_legacy_ip_hash=$(curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | sha256sum | awk '{print $1}')"
REMOTE
}

verify_standalone_voc_release() {
  local fae_voc_before status_code voc_asset voc_runtime
  fae_voc_before="$(remote_fae_snapshot)" || fail

  status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
    -o "$temporary/voc-root.html" -w '%{http_code}' --max-time 15 "$base/voc/")" || fail
  [[ "$status_code" == "200" ]] || fail
  status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
    -o /dev/null -w '%{http_code}' --max-time 15 "$base/voc/health")" || fail
  [[ "$status_code" == "404" ]] || fail
  status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
    -o /dev/null -w '%{http_code}' --max-time 15 "$base/voc/session")" || fail
  [[ "$status_code" == "401" ]] || fail
  status_code="$("${curl_owner[@]}" -o /dev/null -w '%{http_code}' \
    "$base/voc/api/v1/admin/vocs")" || fail
  [[ "$status_code" == "200" ]] || fail
  status_code="$("${curl_viewer[@]}" -o /dev/null -w '%{http_code}' \
    "$base/voc/api/v1/admin/vocs")" || fail
  [[ "$status_code" == "200" ]] || fail
  status_code="$("${curl_member[@]}" -o /dev/null -w '%{http_code}' \
    "$base/voc/api/v1/admin/vocs")" || fail
  [[ "$status_code" == "403" ]] || fail
  status_code="$("${curl_member[@]}" -o /dev/null -w '%{http_code}' \
    "$base/office/?view=services")" || fail
  [[ "$status_code" == "200" ]] || fail

  "$python" - "$temporary/voc-root.html" > "$temporary/voc-asset-path" <<'PY' || fail
import pathlib,re,sys
body=pathlib.Path(sys.argv[1]).read_bytes()
matches=re.findall(rb'(?:src|href)=["\'](/voc/assets/[^"\'<> ]+-[A-Za-z0-9_-]{8,}\.[^"\'<> ]+)["\']',body)
if not matches: raise SystemExit(1)
print(matches[0].decode('ascii'))
PY
  voc_asset="$(<"$temporary/voc-asset-path")"
  [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error -o /dev/null \
    -w '%{http_code}' --max-time 15 "$base$voc_asset")" == "200" ]] || fail

  voc_runtime="$(remote /bin/bash -s <<'REMOTE'
set -euo pipefail
root=/opt/orbbec-voc-agent
release_sha="$(tr -d '\n' < "$root/current-release")"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]]
release="$root/releases/$release_sha"
compose="$release/deploy/linux/compose.yaml"
[[ -f "$compose" && ! -L "$compose" ]]
cd "$(dirname "$compose")"
docker compose -f "$compose" config --quiet </dev/null
docker compose -f "$compose" ps -a --format json </dev/null | python3 -c '
import json,sys
expected={"postgres","workspace","bot-ingest","bot-interact"}
ready=set()
for line in sys.stdin:
    if not line.strip(): continue
    item=json.loads(line); service=item.get("Service",""); state=item.get("State","")
    health=item.get("Health") or ("active" if state == "running" else state)
    if service in expected and health in {"healthy","active"}: ready.add(service)
    if state == "running" and ("clamd" in service.casefold() or "attachment" in service.casefold()): raise SystemExit(1)
if ready != expected: raise SystemExit(1)
'
[[ "$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 8 http://172.29.0.3:18130/health)" == "200" ]]
postgres="$(docker compose -f "$compose" ps -q postgres </dev/null)"
[[ -n "$postgres" ]]
migrations="$(docker exec "$postgres" psql -X -A -t -U postgres -d orbbec_voc -v ON_ERROR_STOP=1 -c 'SELECT name FROM voc_meta.schema_migrations ORDER BY name')"
[[ "$(grep -Fxc '018_shared_web_bot_identity.sql' <<< "$migrations")" == "1" ]]
[[ "$(grep -Fxc '019_bot_interaction_internal_identity.sql' <<< "$migrations")" == "1" ]]
[[ "$(tail -n 1 <<< "$migrations")" == "019_bot_interaction_internal_identity.sql" ]]
printf 'VOC_RUNTIME_OK release_sha=%s service_count=4 latest_migration_count=1\n' "$release_sha"
REMOTE
  )" || fail
  [[ "$voc_runtime" =~ ^VOC_RUNTIME_OK\ release_sha=[0-9a-f]{40}\ service_count=4\ latest_migration_count=1$ ]] || fail
  [[ "$(remote_fae_snapshot)" == "$fae_voc_before" ]] || fail
  echo "STANDALONE_VOC_ACCEPTANCE_OK"
}

local_runtime_preflight() {
  /usr/bin/nc -z -w 2 127.0.0.1 9110 || fail
  /usr/bin/nc -z -w 2 127.0.0.1 9120 || fail
  ! /usr/sbin/lsof -nP -iTCP:9110 -sTCP:LISTEN | /usr/bin/awk 'NR>1 {print $9}' | /usr/bin/grep -Ev '^127\.0\.0\.1:9110$' | /usr/bin/grep -q . || fail
  ! /usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN | /usr/bin/awk 'NR>1 {print $9}' | /usr/bin/grep -Ev '^127\.0\.0\.1:9120$' | /usr/bin/grep -q . || fail
}

run_relay_canary() {
  [[ "$relay_acceptance_config" == /Users/agentops/AgentRuntime/private/acceptance-config.json ]] || fail
  relay_result="$(run_agentops_control relay-canary)" || fail
  [[ "$relay_result" == "AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 accepted_job_kinds=direct_agent,metabot_local public_ports_added=0 duplicate_dispatches=0" ]] || fail
  LOCAL_WORKER_ACCEPTS=metabot_local
  [[ "$LOCAL_WORKER_ACCEPTS" == "metabot_local" ]] || fail
}

prepare_v2_reference_evidence() {
  local local_release remote_release contract_sha reference_payload
  local_release="$(/usr/bin/git -C "$repository_root" rev-parse HEAD)" || fail
  [[ "$local_release" =~ ^[0-9a-f]{40}$ ]] || fail
  remote_release="$(remote '/usr/bin/basename "$(/usr/bin/readlink -f /opt/orbbec-agent-platform/current)"')" || fail
  [[ "$remote_release" == "$local_release" ]] || fail

  acceptance_tests=(
    "tests/test_agent_brain_v2_acceptance.py"
    "tests/test_agent_brain_v2_budget.py::test_forced_pending_waits_then_submits"
    "tests/test_agent_brain_live_repository.py::test_protocol_failure_is_task_local_and_does_not_fabricate_event"
    "tests/test_agent_brain_voc_action.py::test_voc_action_survives_restarts_and_submits_exactly_once"
  )
  while IFS= read -r test_ref; do
    [[ "$test_ref" == tests/test_agent_brain_*::* ]] || fail
    acceptance_tests+=("$test_ref")
  done < <(
    cd "$repository_root/backend" &&
      "$python" -m app.agent_brain.acceptance_contract pytest-args
  )
  [[ "${#acceptance_tests[@]}" == "24" ]] || fail
  (
    cd "$repository_root/backend"
    PYTHONDONTWRITEBYTECODE=1 "$python" -m pytest -q "${acceptance_tests[@]}"
  ) || fail

  contract_sha="$(/usr/bin/shasum -a 256 "$repository_root/backend/app/agent_brain/acceptance_contract.py" | /usr/bin/awk '{print $1}')" || fail
  [[ "$contract_sha" =~ ^[0-9a-f]{64}$ ]] || fail
  reference_payload="$(
    "$python" - "$local_release" "$contract_sha" <<'PY'
import json,re,sys
release_sha,contract_sha=sys.argv[1:]
if not re.fullmatch(r'[0-9a-f]{40}',release_sha): raise SystemExit(1)
if not re.fullmatch(r'[0-9a-f]{64}',contract_sha): raise SystemExit(1)
print(json.dumps({
    'schema_version':1,
    'status':'passed',
    'release_sha':release_sha,
    'scenario_count':20,
    'core_gate_count':3,
    'pending_action_forced_recovery':'passed',
    'task_protocol_isolation':'passed',
    'voc_action_exactly_once':'passed',
    'contract_sha256':contract_sha,
},separators=(',',':'),sort_keys=True))
PY
  )" || fail
  remote '/bin/bash -s' <<REMOTE || fail
set -euo pipefail
umask 077
target=/opt/orbbec-agent-platform/private/agent-brain-v2/reference-recovery.passed
mkdir -p -m 700 "\$(dirname "\$target")"
[[ ! -L "\$target" ]] || exit 1
printf '%s\n' '$reference_payload' > "\$target.part"
chown root:root "\$target.part"
chmod 600 "\$target.part"
mv -f "\$target.part" "\$target"
REMOTE
  echo "AGENT_BRAIN_V2_REFERENCE_OK"
}

remote_partner_gate() {
  remote /bin/bash -s <<'REMOTE'
set -euo pipefail
root=/opt/orbbec-agent-platform
release="$(readlink -f "$root/current")"
environment="$root/private/platform.env"
compose="$release/deploy/cloud/compose.yaml"
api="$(docker compose --env-file "$environment" -f "$compose" ps -q platform-api)"
[[ -n "$api" ]] || exit 1
docker exec "$api" python -m app.control_plane.partner_release gate
REMOTE
}

v2_cutover_gates() {
  local fae_gate_before fae_gate_after partner_gate remote_gates
  fae_gate_before="$(remote_fae_snapshot)" || fail
  remote_gates="$(remote /bin/bash -s <<'REMOTE'
set -euo pipefail
fail() { echo AGENT_BRAIN_V2_GATES_FAILED >&2; exit 1; }
root=/opt/orbbec-agent-platform
private="$root/private"
release="$(readlink -f "$root/current")"
environment="$private/platform.env"
compose="$release/deploy/cloud/compose.yaml"
evidence_dir="$private/agent-brain-v2"
[[ "$release" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ ]] || fail
[[ -f "$environment" && ! -L "$environment" && -f "$compose" && ! -L "$compose" ]] || fail
mkdir -p -m 700 "$evidence_dir"
reference_evidence="$evidence_dir/reference-recovery.passed"
[[ -f "$reference_evidence" && ! -L "$reference_evidence" ]] || fail
[[ "$(stat -c '%a %U' "$reference_evidence")" == "600 root" ]] || fail
python3 - "$reference_evidence" "$release" <<'PY'
import hashlib,json,pathlib,re,sys
evidence=pathlib.Path(sys.argv[1]); release=pathlib.Path(sys.argv[2])
value=json.loads(evidence.read_bytes())
contract=release/'backend/app/agent_brain/acceptance_contract.py'
expected={
    'schema_version':1,
    'status':'passed',
    'release_sha':release.name,
    'scenario_count':20,
    'core_gate_count':3,
    'pending_action_forced_recovery':'passed',
    'task_protocol_isolation':'passed',
    'voc_action_exactly_once':'passed',
    'contract_sha256':hashlib.sha256(contract.read_bytes()).hexdigest(),
}
if value != expected or not re.fullmatch(r'[0-9a-f]{40}',value['release_sha']):
    raise SystemExit(1)
PY
REFERENCE_RECOVERY=passed
PENDING_ACTION_FORCED_RECOVERY=passed
TASK_PROTOCOL_ISOLATION=passed
VOC_ACTION_EXACTLY_ONCE=passed
compose_command=(docker compose --env-file "$environment" -f "$compose")
brain="$("${compose_command[@]}" ps -q platform-brain)"
postgres="$("${compose_command[@]}" ps -q platform-postgres)"
[[ -n "$brain" && -n "$postgres" ]] || fail
probe_name="provider-evidence.$$.json"
docker exec "$brain" python -m app.agent_brain.provider_probe \
  --manifest /app/brain-model.release.json \
  --system-prompt /app/backend/app/agent_brain/prompts/brain_v1.md \
  --evidence-out "/tmp/$probe_name" || fail
docker exec "$brain" cat -- "/tmp/$probe_name" > "$evidence_dir/provider-evidence.json.part"
docker exec "$brain" rm -f -- "/tmp/$probe_name"
chown root:root "$evidence_dir/provider-evidence.json.part"
chmod 600 "$evidence_dir/provider-evidence.json.part"
python3 - "$release" "$evidence_dir/provider-evidence.json.part" <<'PY'
import hashlib,json,pathlib,re,sys
release,evidence=map(pathlib.Path,sys.argv[1:])
manifest=release/'deploy/cloud/brain-model.release.json'
prompt=release/'backend/app/agent_brain/prompts/brain_v1.md'
value=json.loads(evidence.read_bytes())
required={
    'streaming','forced_tool_choice','summarized_thinking',
    'mid_conversation_system','one_hour_cache','one_million_context',
}
if value.get('manifest_sha256') != hashlib.sha256(manifest.read_bytes()).hexdigest(): raise SystemExit(1)
if value.get('system_prompt_sha256') != hashlib.sha256(prompt.read_bytes()).hexdigest(): raise SystemExit(1)
if set(value.get('supported',{})) != required or not all(value['supported'].values()): raise SystemExit(1)
if value.get('stable_cache_ttl') != '1h' or value.get('rolling_cache_ttl') != '5m': raise SystemExit(1)
PY
mv -f "$evidence_dir/provider-evidence.json.part" "$evidence_dir/provider-evidence.json"
sha256sum "$evidence_dir/provider-evidence.json" > "$evidence_dir/provider-evidence.sha256.part"
chmod 600 "$evidence_dir/provider-evidence.sha256.part"
chown root:root "$evidence_dir/provider-evidence.sha256.part"
mv -f "$evidence_dir/provider-evidence.sha256.part" "$evidence_dir/provider-evidence.sha256"
PROVIDER_PROBE=passed
MIGRATION_COUNT="$(docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select count(*) from platform_control.schema_migrations where version in (49,50,51);")"
WAIT_CURSOR_COLUMNS="$(docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select count(*) from information_schema.columns where table_schema='platform_brain' and table_name='brain_wait_subscriptions' and column_name='cursors';")"
BRAIN_CURSOR_WATERLINE_COLUMNS="$(docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select count(*) from information_schema.columns where table_schema='platform_brain' and table_name='brain_task_event_cursors' and column_name='delivered_seq';")"
ACCESS_HISTORY_SCHEMA="$(docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select concat((select count(*) from platform_control.schema_migrations where version=67),'|',(to_regclass('platform_control.user_access_events') is not null)::int,'|',(to_regprocedure('platform_control.append_page_view_v65(uuid,uuid,uuid,text,text,text)') is not null)::int,'|',(to_regprocedure('platform_control.read_user_access_events_v67(uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer)') is not null)::int,'|',(to_regprocedure('platform_control.read_access_subjects_v67(uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer)') is not null)::int);")"
[[ "$MIGRATION_COUNT" == "3" ]] || fail
[[ "$WAIT_CURSOR_COLUMNS" == "0" ]] || fail
[[ "$BRAIN_CURSOR_WATERLINE_COLUMNS" == "1" ]] || fail
[[ "$ACCESS_HISTORY_SCHEMA" == "1|1|1|1|1" ]] || fail
MIGRATIONS_049_050_051=applied
BRAIN_CURSOR_WATERLINE=passed
ACCESS_HISTORY_MIGRATION=applied
V1_NONTERMINAL_MISSIONS="$(docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select count(*) from platform_control.missions where status in ('planning','delegated','synthesizing');")"
V2_MISSION_RUN_WRITES="$(docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select count(*) from platform_control.mission_runs run join platform_control.missions mission on mission.mission_id=run.mission_id join platform_brain.brain_loops loop on loop.turn_id=mission.turn_id;")"
[[ "$V1_NONTERMINAL_MISSIONS" == "0" && "$V2_MISSION_RUN_WRITES" == "0" ]] || fail
printf '%s\n' \
  "PROVIDER_PROBE=$PROVIDER_PROBE" \
  "REFERENCE_RECOVERY=$REFERENCE_RECOVERY" \
  "MIGRATIONS_049_050_051=$MIGRATIONS_049_050_051" \
  "WAIT_CURSOR_COLUMNS=$WAIT_CURSOR_COLUMNS" \
  "BRAIN_CURSOR_WATERLINE=$BRAIN_CURSOR_WATERLINE" \
  "ACCESS_HISTORY_MIGRATION=$ACCESS_HISTORY_MIGRATION" \
  "PENDING_ACTION_FORCED_RECOVERY=$PENDING_ACTION_FORCED_RECOVERY" \
  "TASK_PROTOCOL_ISOLATION=$TASK_PROTOCOL_ISOLATION" \
  "VOC_ACTION_EXACTLY_ONCE=$VOC_ACTION_EXACTLY_ONCE" \
  "V1_NONTERMINAL_MISSIONS=$V1_NONTERMINAL_MISSIONS" \
  "V2_MISSION_RUN_WRITES=$V2_MISSION_RUN_WRITES"
REMOTE
)" || fail
  [[ "$remote_gates" == $'PROVIDER_PROBE=passed\nREFERENCE_RECOVERY=passed\nMIGRATIONS_049_050_051=applied\nWAIT_CURSOR_COLUMNS=0\nBRAIN_CURSOR_WATERLINE=passed\nACCESS_HISTORY_MIGRATION=applied\nPENDING_ACTION_FORCED_RECOVERY=passed\nTASK_PROTOCOL_ISOLATION=passed\nVOC_ACTION_EXACTLY_ONCE=passed\nV1_NONTERMINAL_MISSIONS=0\nV2_MISSION_RUN_WRITES=0' ]] || fail
  partner_gate="$(remote_partner_gate)" || fail
  [[ "$partner_gate" == $'PARTNER_PROVIDER_CONFIG_VALID=true\nPARTNER_LOGIN_EXPECTED=false\nPARTNER_PROVIDER_KIND=none\nPARTNER_RELEASE_REASON=partner_identity_disabled' ]] || fail
  fae_gate_after="$(remote_fae_snapshot)" || fail
  [[ "$fae_gate_after" == "$fae_gate_before" ]] || fail
  FAE_MANAGED_FILES_UNCHANGED=true
  [[ "$FAE_MANAGED_FILES_UNCHANGED" == "true" ]] || fail
}

remote_feature() {
  local selected="$1"
  [[ "$selected" == "0" || "$selected" == "1" ]] || fail
  if ! remote /bin/bash -s -- "$selected" <<'REMOTE'
set -eEuo pipefail
umask 077
fail() { echo AGENT_BRAIN_REMOTE_TOGGLE_FAILED >&2; exit 1; }
[[ "$#" -eq 1 && ( "$1" == "0" || "$1" == "1" ) ]] || fail
selected="$1"
root=/opt/orbbec-agent-platform
private="$root/private"
environment="$private/platform.env"
release="$(/usr/bin/readlink -f "$root/current")"
compose="$release/deploy/cloud/compose.yaml"
[[ "$release" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ ]] || fail
for path in "$environment" "$compose"; do [[ -f "$path" && ! -L "$path" ]] || fail; done
[[ "$(/usr/bin/stat -c '%a %U' "$environment")" == "600 root" ]] || fail
compose_command=(/usr/bin/docker compose --env-file "$environment" -f "$compose")
feature_environment_before="$private/platform.env.agent-brain.before.$$"
temporary="$environment.agent-brain.part"
/usr/bin/install -o root -g root -m 600 "$environment" "$feature_environment_before"
feature_mutated=0
restore_feature() {
  status="$?"
  trap - ERR EXIT
  if [[ "$status" -ne 0 && "$feature_mutated" == "1" ]]; then
    if /usr/bin/install -o root -g root -m 600 "$feature_environment_before" "$environment.part.restore" &&
       /bin/mv -f "$environment.part.restore" "$environment" &&
       "${compose_command[@]}" up -d --force-recreate platform-api platform-loopback >/dev/null 2>&1; then
      restored=0
      for _attempt in $(/usr/bin/seq 1 12); do
        if /usr/bin/curl -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null; then restored=1; break; fi
        /bin/sleep 5
      done
      [[ "$restored" == "1" ]] || echo AGENT_BRAIN_REMOTE_RESTORE_FAILED >&2
    else
      echo AGENT_BRAIN_REMOTE_RESTORE_FAILED >&2
    fi
  fi
  /bin/rm -f -- "$feature_environment_before" "$environment.part.restore" "$temporary"
  exit "$status"
}
trap restore_feature ERR EXIT
fae_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)"
fae_image="$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend)"
fae_started="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)"
fae_restart="$(/usr/bin/docker inspect --format '{{.RestartCount}}' ai-fae-backend)"
fae_config="$(/usr/bin/docker inspect --format '{{json .Config}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_mounts="$(/usr/bin/docker inspect --format '{{json .Mounts}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_health="$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)"
fae_domain_hash="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_legacy_ip_hash="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
/usr/bin/python3 - "$environment" "$temporary" "$selected" <<'PY'
import os
import pathlib
import sys

source, target = map(pathlib.Path, sys.argv[1:3])
selected = sys.argv[3]
lines = source.read_text(encoding="utf-8").splitlines()
kept = [
    line for line in lines
    if not line.startswith("PLATFORM_AGENT_BRAIN_")
]
raw = (
    "\n".join(
        kept
        + [
            f"PLATFORM_AGENT_BRAIN_ENABLED={selected}",
            f"PLATFORM_AGENT_BRAIN_V2_ENABLED={selected}",
        ]
    )
    + "\n"
).encode()
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    os.write(descriptor, raw)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
/bin/chown root:root "$temporary"
/bin/chmod 600 "$temporary"
/bin/mv -f "$temporary" "$environment"
feature_mutated=1
"${compose_command[@]}" up -d --force-recreate platform-api platform-loopback >/dev/null
for _attempt in $(/usr/bin/seq 1 12); do
  /usr/bin/curl -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null && break
  /bin/sleep 5
done
/usr/bin/curl -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null || fail
api_id="$("${compose_command[@]}" ps -q platform-api)"
[[ -n "$api_id" ]] || fail
/usr/bin/docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$api_id" | /usr/bin/grep -Fxq "PLATFORM_AGENT_BRAIN_ENABLED=$selected" || fail
/usr/bin/docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$api_id" | /usr/bin/grep -Fxq "PLATFORM_AGENT_BRAIN_V2_ENABLED=$selected" || fail
[[ "$fae_id" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$fae_image" == "$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend)" ]] || fail
[[ "$fae_started" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
[[ "$fae_restart" == "$(/usr/bin/docker inspect --format '{{.RestartCount}}' ai-fae-backend)" ]] || fail
[[ "$fae_config" == "$(/usr/bin/docker inspect --format '{{json .Config}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_mounts" == "$(/usr/bin/docker inspect --format '{{json .Mounts}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_health" == "$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)" ]] || fail
[[ "$fae_domain_hash" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_legacy_ip_hash" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
trap - ERR EXIT
/bin/rm -f -- "$feature_environment_before" "$temporary"
REMOTE
  then
    return 1
  fi
}

publish_formal_nginx() {
  local committed_template transaction_id="$1"
  [[ "$transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail
  committed_template="$(git -C "$repository_root" show HEAD:deploy/cloud/agent-domain.nginx.conf 2>/dev/null)" || fail
  [[ "$committed_template" == "$(<"$repository_root/deploy/cloud/agent-domain.nginx.conf")" ]] || fail
  remote 'umask 077; /usr/bin/install -d -o root -g root -m 700 /opt/orbbec-agent-platform/private/agent-brain-release; /bin/cat > /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part; /bin/chown root:root /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part; /bin/chmod 600 /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part; /bin/mv -f /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf' \
    < "$repository_root/deploy/cloud/agent-domain.nginx.conf" || fail
  remote /bin/bash -s -- "$transaction_id" <<'REMOTE' || fail
set -eEuo pipefail
umask 077
fail() { echo AGENT_BRAIN_NGINX_FAILED >&2; exit 1; }
transaction_id="$1"
[[ "$transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail
source=/opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf
target=/etc/nginx/sites-available/agent-domain.conf
enabled=/etc/nginx/sites-enabled/agent-domain.conf
state=/opt/orbbec-agent-platform/private/agent-brain-release
root=/opt/orbbec-agent-platform
transaction_lock="$state/agent-domain.transaction.lock"
[[ ! -L "$transaction_lock" ]] || fail
exec 9>"$transaction_lock"
/bin/chmod 600 "$transaction_lock"
/usr/bin/flock -x 9
release="$(/usr/bin/readlink -f "$root/current")"
release_template="$release/deploy/cloud/agent-domain.nginx.conf"
manifest="$release/MANIFEST.sha256"
[[ "$release" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ ]] || fail
[[ -f "$source" && ! -L "$source" && -f "$target" && ! -L "$target" ]] || fail
if [[ -L "$enabled" ]]; then
  enabled_before_kind="symlink"
elif [[ -f "$enabled" && ! -L "$enabled" ]]; then
  enabled_before_kind="regular"
else
  fail
fi
[[ -f "$release_template" && ! -L "$release_template" && -f "$manifest" && ! -L "$manifest" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$source")" == "600 root" ]] || fail
[[ "$(/usr/bin/stat -c '%U' "$target")" == "root" ]] || fail
[[ "$(/usr/bin/stat -c '%U' "$enabled")" == "root" ]] || fail
/usr/bin/cmp -s "$source" "$release_template" || fail
template_digest="$(/usr/bin/sha256sum "$release_template" | /usr/bin/awk '{print $1}')"
/usr/bin/grep -Fxq "$template_digest  deploy/cloud/agent-domain.nginx.conf" "$manifest" || fail
transaction_before="$state/agent-domain.transaction.before.conf"
enabled_transaction_before="$state/agent-domain.transaction.before.enabled"
enabled_transaction_before_config="$state/agent-domain.transaction.before.enabled.conf"
enabled_transaction_before_kind="$state/agent-domain.transaction.before.enabled.kind"
transaction_marker="$state/agent-domain.transaction.id"
[[ ! -e "$transaction_before" && ! -e "$enabled_transaction_before" && ! -e "$enabled_transaction_before_config" && ! -e "$enabled_transaction_before_kind" && ! -e "$transaction_marker" ]] || fail
published=0
restore_enabled_nginx() {
  case "$enabled_before_kind" in
    symlink)
      [[ "$enabled_before" =~ ^(/etc/nginx/sites-available/|\.\./sites-available/)[A-Za-z0-9._-]+$ ]] || return 1
      /bin/rm -f -- "$enabled"
      /bin/ln -s "$enabled_before" "$enabled"
      ;;
    regular)
      /usr/bin/install -o root -g root -m 644 "$enabled_transaction_before_config" "$enabled.part.restore"
      /bin/rm -f -- "$enabled"
      /bin/mv -f "$enabled.part.restore" "$enabled"
      ;;
    *) return 1 ;;
  esac
}
restore_nginx() {
  status="$?"
  trap - ERR EXIT
  restore_status=0
  if [[ "$status" -ne 0 && "$published" == "1" ]]; then
    if ! /usr/bin/install -o root -g root -m 644 "$transaction_before" "$target.part.restore"; then restore_status=1; fi
    if [[ "$restore_status" == "0" ]] && ! /bin/mv -f "$target.part.restore" "$target"; then restore_status=1; fi
    if [[ "$restore_status" == "0" ]] && ! restore_enabled_nginx; then restore_status=1; fi
    if [[ "$restore_status" == "0" ]] && ! /usr/sbin/nginx -t >/dev/null 2>&1; then restore_status=1; fi
    if [[ "$restore_status" == "0" ]] && ! /bin/systemctl reload nginx >/dev/null 2>&1; then restore_status=1; fi
  elif [[ "$status" -ne 0 ]]; then
    /bin/rm -f -- "$transaction_before" "$enabled_transaction_before" "$enabled_transaction_before_config" "$enabled_transaction_before_kind" "$transaction_marker"
  fi
  if [[ "$restore_status" -ne 0 ]]; then exit "$restore_status"; fi
  exit "$status"
}
trap restore_nginx ERR EXIT
/usr/bin/install -o root -g root -m 600 "$target" "$transaction_before"
case "$enabled_before_kind" in
  symlink)
    enabled_before="$(/usr/bin/readlink "$enabled")"
    [[ "$enabled_before" =~ ^(/etc/nginx/sites-available/|\.\./sites-available/)[A-Za-z0-9._-]+$ ]] || fail
    /usr/bin/printf '%s\n' "$enabled_before" > "$enabled_transaction_before.part"
    /bin/chown root:root "$enabled_transaction_before.part"
    /bin/chmod 600 "$enabled_transaction_before.part"
    /bin/mv -f "$enabled_transaction_before.part" "$enabled_transaction_before"
    ;;
  regular)
    enabled_before=""
    /usr/bin/install -o root -g root -m 600 "$enabled" "$enabled_transaction_before_config"
    ;;
  *) fail ;;
esac
/usr/bin/printf '%s\n' "$enabled_before_kind" > "$enabled_transaction_before_kind.part"
/bin/chown root:root "$enabled_transaction_before_kind.part"
/bin/chmod 600 "$enabled_transaction_before_kind.part"
/bin/mv -f "$enabled_transaction_before_kind.part" "$enabled_transaction_before_kind"
/usr/bin/printf '%s\n' "$transaction_id" > "$transaction_marker.part"
/bin/chown root:root "$transaction_marker.part"
/bin/chmod 600 "$transaction_marker.part"
/bin/mv -f "$transaction_marker.part" "$transaction_marker"
fae_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)"
fae_image="$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend)"
fae_started="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)"
fae_restart="$(/usr/bin/docker inspect --format '{{.RestartCount}}' ai-fae-backend)"
fae_config="$(/usr/bin/docker inspect --format '{{json .Config}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_mounts="$(/usr/bin/docker inspect --format '{{json .Mounts}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_health="$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)"
fae_domain_hash="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_legacy_ip_hash="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
[[ -f "$state/agent-domain.before.conf" ]] || /usr/bin/install -o root -g root -m 600 "$target" "$state/agent-domain.before.conf"
rendered="$state/agent-domain.rendered.conf"
/usr/bin/python3 - "$source" "$rendered" <<'PY'
import os
import pathlib
import sys

source, target = map(pathlib.Path, sys.argv[1:])
value = source.read_text(encoding="utf-8")
replacements = {
    "__AGENT_DOMAIN__": "agent.orbbec.com.cn",
    "__CERT_PATH__": "/etc/letsencrypt/live/agent.orbbec.com.cn/fullchain.pem",
    "__KEY_PATH__": "/etc/letsencrypt/live/agent.orbbec.com.cn/privkey.pem",
}
for marker, replacement in replacements.items():
    if value.count(marker) == 0:
        raise SystemExit(1)
    value = value.replace(marker, replacement)
if "__" in value or "auth_basic" in value or "limit_except GET HEAD OPTIONS" in value:
    raise SystemExit(1)
raw = value.encode()
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    os.write(descriptor, raw)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
/bin/chown root:root "$rendered"
/bin/chmod 600 "$rendered"
/usr/bin/install -o root -g root -m 644 "$rendered" "$target.part"
published=1
/bin/mv -f "$target.part" "$target"
/bin/rm -f -- "$enabled"
/bin/ln -s "$target" "$enabled"
/usr/sbin/nginx -t >/dev/null 2>&1
/bin/systemctl reload nginx
[[ "$fae_id" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$fae_image" == "$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend)" ]] || fail
[[ "$fae_started" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
[[ "$fae_restart" == "$(/usr/bin/docker inspect --format '{{.RestartCount}}' ai-fae-backend)" ]] || fail
[[ "$fae_config" == "$(/usr/bin/docker inspect --format '{{json .Config}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_mounts" == "$(/usr/bin/docker inspect --format '{{json .Mounts}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_health" == "$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)" ]] || fail
[[ "$fae_domain_hash" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_legacy_ip_hash" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
trap - ERR EXIT
REMOTE
}

rollback_formal_nginx_transaction() {
  local transaction_id="$1"
  [[ "$transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail
  remote /bin/bash -s -- "$transaction_id" <<'REMOTE'
set -euo pipefail
umask 077
transaction_id="$1"
[[ "$transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || exit 1
state=/opt/orbbec-agent-platform/private/agent-brain-release
transaction_lock="$state/agent-domain.transaction.lock"
[[ ! -L "$transaction_lock" ]] || exit 1
exec 9>"$transaction_lock"
/bin/chmod 600 "$transaction_lock"
/usr/bin/flock -x 9
transaction_before="$state/agent-domain.transaction.before.conf"
enabled_transaction_before="$state/agent-domain.transaction.before.enabled"
enabled_transaction_before_config="$state/agent-domain.transaction.before.enabled.conf"
enabled_transaction_before_kind="$state/agent-domain.transaction.before.enabled.kind"
transaction_marker="$state/agent-domain.transaction.id"
target=/etc/nginx/sites-available/agent-domain.conf
enabled=/etc/nginx/sites-enabled/agent-domain.conf
if [[ ! -e "$transaction_before" && ! -e "$enabled_transaction_before" && ! -e "$enabled_transaction_before_config" && ! -e "$enabled_transaction_before_kind" && ! -e "$transaction_marker" ]]; then exit 0; fi
[[ -f "$transaction_before" && ! -L "$transaction_before" ]] || exit 1
[[ -f "$enabled_transaction_before_kind" && ! -L "$enabled_transaction_before_kind" ]] || exit 1
[[ -f "$transaction_marker" && ! -L "$transaction_marker" ]] || exit 1
[[ "$(/usr/bin/stat -c '%a %U' "$transaction_before")" == "600 root" ]] || exit 1
[[ "$(/usr/bin/stat -c '%a %U' "$enabled_transaction_before_kind")" == "600 root" ]] || exit 1
[[ "$(/usr/bin/stat -c '%a %U' "$transaction_marker")" == "600 root" ]] || exit 1
[[ "$(/bin/cat "$transaction_marker")" == "$transaction_id" ]] || exit 1
enabled_before_kind="$(/bin/cat "$enabled_transaction_before_kind")"
/usr/bin/install -o root -g root -m 644 "$transaction_before" "$target.part.restore"
/bin/mv -f "$target.part.restore" "$target"
case "$enabled_before_kind" in
  symlink)
    [[ -f "$enabled_transaction_before" && ! -L "$enabled_transaction_before" ]] || exit 1
    [[ "$(/usr/bin/stat -c '%a %U' "$enabled_transaction_before")" == "600 root" ]] || exit 1
    enabled_before="$(/bin/cat "$enabled_transaction_before")"
    [[ "$enabled_before" =~ ^(/etc/nginx/sites-available/|\.\./sites-available/)[A-Za-z0-9._-]+$ ]] || exit 1
    /bin/rm -f -- "$enabled"
    /bin/ln -s "$enabled_before" "$enabled"
    ;;
  regular)
    [[ -f "$enabled_transaction_before_config" && ! -L "$enabled_transaction_before_config" ]] || exit 1
    [[ "$(/usr/bin/stat -c '%a %U' "$enabled_transaction_before_config")" == "600 root" ]] || exit 1
    /usr/bin/install -o root -g root -m 644 "$enabled_transaction_before_config" "$enabled.part.restore"
    /bin/rm -f -- "$enabled"
    /bin/mv -f "$enabled.part.restore" "$enabled"
    ;;
  *) exit 1 ;;
esac
/usr/sbin/nginx -t >/dev/null 2>&1
/bin/systemctl reload nginx >/dev/null 2>&1
/bin/rm -f -- "$transaction_before" "$enabled_transaction_before" "$enabled_transaction_before_config" "$enabled_transaction_before_kind" "$transaction_marker"
REMOTE
}

commit_formal_nginx_transaction() {
  local transaction_id="$1"
  [[ "$transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail
  remote /bin/bash -s -- "$transaction_id" <<'REMOTE'
set -euo pipefail
umask 077
transaction_id="$1"
[[ "$transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || exit 1
state=/opt/orbbec-agent-platform/private/agent-brain-release
transaction_lock="$state/agent-domain.transaction.lock"
[[ ! -L "$transaction_lock" ]] || exit 1
exec 9>"$transaction_lock"
/bin/chmod 600 "$transaction_lock"
/usr/bin/flock -x 9
transaction_before="$state/agent-domain.transaction.before.conf"
enabled_transaction_before="$state/agent-domain.transaction.before.enabled"
enabled_transaction_before_config="$state/agent-domain.transaction.before.enabled.conf"
enabled_transaction_before_kind="$state/agent-domain.transaction.before.enabled.kind"
transaction_marker="$state/agent-domain.transaction.id"
[[ -f "$transaction_before" && ! -L "$transaction_before" ]] || exit 1
[[ -f "$enabled_transaction_before_kind" && ! -L "$enabled_transaction_before_kind" ]] || exit 1
[[ -f "$transaction_marker" && ! -L "$transaction_marker" ]] || exit 1
[[ "$(/usr/bin/stat -c '%a %U' "$transaction_before")" == "600 root" ]] || exit 1
[[ "$(/usr/bin/stat -c '%a %U' "$enabled_transaction_before_kind")" == "600 root" ]] || exit 1
[[ "$(/usr/bin/stat -c '%a %U' "$transaction_marker")" == "600 root" ]] || exit 1
[[ "$(/bin/cat "$transaction_marker")" == "$transaction_id" ]] || exit 1
/bin/rm -f -- "$transaction_before" "$enabled_transaction_before" "$enabled_transaction_before_config" "$enabled_transaction_before_kind" "$transaction_marker"
REMOTE
}

cookie_config() {
  local source="$1" target="$2" browser_target="$3"
  require_private_file "$source" 8192
  "$python" - "$source" "$target" "$browser_target" <<'PY'
from http.cookies import SimpleCookie
import json
import os
import pathlib
import sys

source, target, browser_target = map(pathlib.Path, sys.argv[1:])
raw_cookie = source.read_text(encoding="utf-8").strip()
if "\n" in raw_cookie or "\r" in raw_cookie or '"' in raw_cookie or "\\" in raw_cookie:
    raise SystemExit(1)
jar = SimpleCookie()
try:
    jar.load(raw_cookie)
except Exception:
    raise SystemExit(1)
required = {"__Host-platform_session", "__Host-platform_csrf"}
if set(jar) != required or any(not jar[name].value for name in required):
    raise SystemExit(1)
session = jar["__Host-platform_session"].value
csrf = jar["__Host-platform_csrf"].value
cookie = f"__Host-platform_session={session}; __Host-platform_csrf={csrf}"
raw = (
    f'header = "Cookie: {cookie}"\n'
    'header = "Origin: https://agent.orbbec.com.cn"\n'
    f'header = "X-CSRF-Token: {csrf}"\n'
).encode()
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    os.write(descriptor, raw)
finally:
    os.close(descriptor)
browser_raw = json.dumps(
    {"__Host-platform_session": session, "__Host-platform_csrf": csrf},
    separators=(",", ":"),
).encode()
descriptor = os.open(browser_target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    os.write(descriptor, browser_raw)
finally:
    os.close(descriptor)
PY
}

verify_fae_account_role() {
  local expected_role="$1" account_file="$2" browser_cookie_file="$3" status_code
  shift 3
  status_code="$("$@" -o "$account_file" -w '%{http_code}' "$base/api/v1/account")" || fail
  [[ "$status_code" == "200" ]] || fail
  "$python" - "$account_file" "$browser_cookie_file" "$expected_role" <<'PY' || fail
import json
import sys
import uuid

account = json.load(open(sys.argv[1], encoding="utf-8"))
cookies = json.load(open(sys.argv[2], encoding="utf-8"))
expected_role = sys.argv[3]
if account.get("role") != expected_role:
    raise SystemExit(1)
try:
    uuid.UUID(account.get("internal_user_id", ""))
except (AttributeError, TypeError, ValueError):
    raise SystemExit(1)
csrf = cookies.get("__Host-platform_csrf")
if not isinstance(csrf, str) or not csrf or account.get("csrf_token") != csrf:
    raise SystemExit(1)
PY
}

terminate_acceptance_process() {
  local selected_pid="${1:-}" _attempt
  [[ "$selected_pid" =~ ^[0-9]+$ ]] || return 0
  if /bin/kill -0 "$selected_pid" >/dev/null 2>&1; then
    /bin/kill -TERM "$selected_pid" >/dev/null 2>&1 || true
    for _attempt in $(/usr/bin/seq 1 10); do
      /bin/kill -0 "$selected_pid" >/dev/null 2>&1 || break
      /bin/sleep 0.1
    done
    if /bin/kill -0 "$selected_pid" >/dev/null 2>&1; then
      /bin/kill -KILL "$selected_pid" >/dev/null 2>&1 || true
    fi
  fi
  wait "$selected_pid" >/dev/null 2>&1 || true
}

cleanup_fae_report_processes() {
  terminate_acceptance_process "${probe_watchdog_pid:-}"
  probe_watchdog_pid=""
  terminate_acceptance_process "${node_pid:-}"
  node_pid=""
  terminate_acceptance_process "${chrome_pid:-}"
  chrome_pid=""
}

verify_fae_rendered_state() {
  local browser_cookie_file="$1" workspace="$2" requested_url="$3" probe_mode="$4" artifact_name="$5"
  local status=0 cleanup_allowed=0 chrome_port page_socket
  local chrome=/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
  local node=/opt/homebrew/bin/node
  local probe="$repository_root/deploy/cloud/fae-reports-placeholder-probe.js"
  local probe_deadline_ms=12000
  local command_timeout_ms=2000
  local watchdog_seconds=15
  local profile="$workspace/$artifact_name-chrome-profile"
  local active_port="$profile/DevToolsActivePort"
  local target_json="$workspace/$artifact_name-chrome-target.json"

  run_fae_reports_probe() {
    local _attempt watched_pid
    [[ "$workspace" == /* && -d "$workspace" && ! -L "$workspace" ]] || return 1
    if [[ "$probe_mode" == "report" ]]; then
      [[ "$requested_url" == "https://agent.orbbec.com.cn/fae/manage/reports" && "$artifact_name" == "fae-reports" ]] || return 1
    elif [[ "$probe_mode" == "compat-report" ]]; then
      [[ "$requested_url" == "https://agent.orbbec.com.cn/admin/fae/reports" && "$artifact_name" == "fae-compat-reports" ]] || return 1
    else
      [[ "$probe_mode" == "viewer-denied" && "$requested_url" == "https://agent.orbbec.com.cn/fae/manage/" && "$artifact_name" == "fae-viewer" ]] || return 1
    fi
    [[ "$browser_cookie_file" == "$workspace/"* && -f "$browser_cookie_file" && ! -L "$browser_cookie_file" ]] || return 1
    [[ -x "$chrome" && -x "$node" && -f "$probe" && ! -L "$probe" ]] || return 1
    [[ ! -e "$profile" && ! -L "$profile" && ! -e "$target_json" && ! -L "$target_json" ]] || return 1
    cleanup_allowed=1
    /bin/mkdir -m 700 "$profile" || return 1
    "$chrome" --headless=new --disable-gpu --no-first-run --no-default-browser-check \
      --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 \
      --user-data-dir="$profile" about:blank > /dev/null 2>&1 &
    chrome_pid="$!"
    [[ "$chrome_pid" =~ ^[0-9]+$ ]] || return 1
    for _attempt in $(/usr/bin/seq 1 50); do
      [[ -s "$active_port" ]] && break
      /bin/kill -0 "$chrome_pid" >/dev/null 2>&1 || return 1
      /bin/sleep 0.1
    done
    [[ -s "$active_port" ]] || return 1
    chrome_port="$(/usr/bin/sed -n '1p' "$active_port")" || return 1
    [[ "$chrome_port" =~ ^[0-9]+$ ]] || return 1
    /usr/bin/curl --silent --show-error --fail --request PUT --max-time 2 \
      "http://127.0.0.1:$chrome_port/json/new?about%3Ablank" > "$target_json" || return 1
    page_socket="$($python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["webSocketDebuggerUrl"])' "$target_json")" || return 1
    [[ "$page_socket" == ws://127.0.0.1:* || "$page_socket" == ws://localhost:* ]] || return 1
    "$node" "$probe" "$page_socket" "$browser_cookie_file" "$requested_url" \
      "$probe_mode" "$probe_deadline_ms" "$command_timeout_ms" &
    node_pid="$!"
    [[ "$node_pid" =~ ^[0-9]+$ ]] || return 1
    watched_pid="$node_pid"
    "$python" - "$watched_pid" "$watchdog_seconds" <<'PY' &
import os
import signal
import sys
import time

pid = int(sys.argv[1])
time.sleep(int(sys.argv[2]))
try:
    os.kill(pid, signal.SIGTERM)
except ProcessLookupError:
    raise SystemExit(0)
time.sleep(1)
try:
    os.kill(pid, signal.SIGKILL)
except ProcessLookupError:
    pass
PY
    probe_watchdog_pid="$!"
    if wait "$node_pid"; then
      node_pid=""
    else
      status="$?"
      node_pid=""
      return "$status"
    fi
  }

  if run_fae_reports_probe; then
    status=0
  else
    status="$?"
  fi
  cleanup_fae_report_processes
  if [[ "$cleanup_allowed" == "1" ]]; then
    /bin/rm -rf -- "$profile" || status=1
    /bin/rm -f -- "$target_json" "$browser_cookie_file" || status=1
  fi
  return "$status"
}

verify_fae_reports_ready() {
  verify_fae_rendered_state "$1" "$2" \
    https://agent.orbbec.com.cn/fae/manage/reports report fae-reports
}

verify_fae_reports_compatibility() {
  verify_fae_rendered_state "$1" "$2" \
    https://agent.orbbec.com.cn/admin/fae/reports compat-report fae-compat-reports
}

verify_fae_viewer_denied() {
  verify_fae_rendered_state "$1" "$2" \
    https://agent.orbbec.com.cn/fae/manage/ viewer-denied fae-viewer
}

verify_access_history_authorization_contract() {
  local status_code
  status_code="$("${curl_owner[@]}" -o "$temporary/access-history.json" -w '%{http_code}' \
    "$base/api/v1/manage/access-events?limit=1")" || fail
  [[ "$status_code" == "200" ]] || fail
  status_code="$("${curl_owner[@]}" -o "$temporary/access-subjects.json" -w '%{http_code}' \
    "$base/api/v1/manage/access-subjects?limit=1")" || fail
  [[ "$status_code" == "200" ]] || fail
  status_code="$("${curl_member[@]}" -o /dev/null -w '%{http_code}' \
    "$base/api/v1/manage/access-events?limit=1")" || fail
  [[ "$status_code" == "403" ]] || fail
  status_code="$("${curl_member[@]}" -o /dev/null -w '%{http_code}' \
    "$base/api/v1/manage/access-subjects?limit=1")" || fail
  [[ "$status_code" == "403" ]] || fail
  status_code="$("${curl_viewer[@]}" -o /dev/null -w '%{http_code}' \
    "$base/api/v1/manage/access-events?limit=1")" || fail
  [[ "$status_code" == "403" ]] || fail
  status_code="$("${curl_viewer[@]}" -o /dev/null -w '%{http_code}' \
    "$base/api/v1/manage/access-subjects?limit=1")" || fail
  [[ "$status_code" == "403" ]] || fail
}

verify_access_history_browser_contract() {
  local browser_cookie_file="$1" workspace="$2" status=0 chrome_port page_socket watched_pid result
  local chrome=/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
  local node=/opt/homebrew/bin/node
  local probe="$repository_root/deploy/cloud/access-history-probe.mjs"
  local profile="$workspace/access-history-chrome-profile"
  local target_json="$workspace/access-history-chrome-target.json"
  local output="$workspace/access-history-probe.out"
  local active_port="$profile/DevToolsActivePort"
  [[ "$workspace" == /* && -d "$workspace" && ! -L "$workspace" ]] || return 1
  [[ "$browser_cookie_file" == "$workspace/"* && -f "$browser_cookie_file" && ! -L "$browser_cookie_file" ]] || return 1
  [[ -x "$chrome" && -x "$node" && -f "$probe" && ! -L "$probe" ]] || return 1
  [[ ! -e "$profile" && ! -e "$target_json" && ! -e "$output" ]] || return 1
  /bin/mkdir -m 700 "$profile" || return 1
  "$chrome" --headless=new --disable-gpu --no-first-run --no-default-browser-check \
    --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 \
    --user-data-dir="$profile" about:blank >/dev/null 2>&1 &
  chrome_pid="$!"
  for _attempt in $(/usr/bin/seq 1 50); do
    [[ -s "$active_port" ]] && break
    /bin/kill -0 "$chrome_pid" >/dev/null 2>&1 || { status=1; break; }
    /bin/sleep 0.1
  done
  if [[ "$status" == "0" && -s "$active_port" ]]; then
    chrome_port="$(/usr/bin/sed -n '1p' "$active_port")" || status=1
  else
    status=1
  fi
  if [[ "$status" == "0" && "$chrome_port" =~ ^[0-9]+$ ]]; then
    /usr/bin/curl --silent --show-error --fail --request PUT --max-time 2 \
      "http://127.0.0.1:$chrome_port/json/new?about%3Ablank" > "$target_json" || status=1
  else
    status=1
  fi
  if [[ "$status" == "0" ]]; then
    page_socket="$($python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["webSocketDebuggerUrl"])' "$target_json")" || status=1
    [[ "$page_socket" == ws://127.0.0.1:* || "$page_socket" == ws://localhost:* ]] || status=1
  fi
  if [[ "$status" == "0" ]]; then
    "$node" "$probe" "$page_socket" "$browser_cookie_file" 90000 >"$output" &
    node_pid="$!"; watched_pid="$node_pid"
    "$python" - "$watched_pid" <<'PY' &
import os,signal,sys,time
pid=int(sys.argv[1]); time.sleep(95)
try: os.kill(pid,signal.SIGTERM)
except ProcessLookupError: raise SystemExit(0)
time.sleep(1)
try: os.kill(pid,signal.SIGKILL)
except ProcessLookupError: pass
PY
    probe_watchdog_pid="$!"
    if wait "$node_pid"; then node_pid=""; else status="$?"; node_pid=""; fi
  fi
  cleanup_fae_report_processes
  result="$(/bin/cat "$output" 2>/dev/null || true)"
  /bin/rm -rf -- "$profile"
  /bin/rm -f -- "$target_json" "$output"
  [[ "$status" == "0" && "$result" == "ACCESS_HISTORY_BROWSER_OK pages=7 external_fae_events=0" ]]
}

verify_fae_workbench_cloud_contract() {
  local path status_code
  local -a fae_owner_paths=(
    '/fae/manage/'
    '/fae/manage/sessions'
    '/fae/manage/issues'
  )
  local -a fae_owner_apis=(
    '/api/fae/overview'
    '/api/fae/sessions?limit=1'
    '/api/fae/issues'
    '/api/fae/reports/latest'
  )
  local -a fae_member_denied_paths=(
    '/api/fae/overview'
    '/api/fae/sessions?limit=1'
    '/api/fae/issues'
  )
  local -a fae_viewer_denied_apis=(
    '/api/fae/overview'
    '/api/fae/sessions?limit=1'
    '/api/fae/issues'
  )

  verify_fae_account_role platform_owner "$temporary/fae-owner-account.json" \
    "$temporary/owner.browser.json" "${curl_owner[@]}"
  verify_fae_account_role member "$temporary/fae-member-account.json" \
    "$temporary/member.browser.json" "${curl_member[@]}"
  verify_fae_account_role management_viewer "$temporary/fae-viewer-account.json" \
    "$temporary/viewer.browser.json" "${curl_viewer[@]}"
  "$python" - "$temporary/fae-owner-account.json" "$temporary/fae-member-account.json" \
    "$temporary/fae-viewer-account.json" <<'PY' || fail
import json
import sys

identities = [
    json.load(open(path, encoding="utf-8")).get("internal_user_id")
    for path in sys.argv[1:]
]
if len(set(identities)) != 3:
    raise SystemExit(1)
PY

  status_code="$("${curl_member[@]}" -o "$temporary/fae-direct.html" -w '%{http_code}' "$base/fae/")" || fail
  [[ "$status_code" == "200" ]] || fail
  status_code="$("${curl_member[@]}" -o "$temporary/fae-direct-deep-link.html" -w '%{http_code}' \
    "$base/fae/conversations/fae:owned-1")" || fail
  [[ "$status_code" == "200" ]] || fail

  for path in "${fae_owner_paths[@]}"; do
    status_code="$("${curl_owner[@]}" -o /dev/null -w '%{http_code}' "$base$path")" || fail
    [[ "$status_code" == "200" ]] || fail
  done
  for path in "${fae_owner_apis[@]}"; do
    status_code="$("${curl_owner[@]}" -o "$temporary/fae-owner-api.json" -w '%{http_code}' "$base$path")" || fail
    [[ "$status_code" == "200" ]] || fail
    if [[ "$path" == "/api/fae/issues" ]]; then
      "$python" - "$temporary/fae-owner-api.json" <<'PY' || fail
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
items = value.get("items") if isinstance(value, dict) else None
total = value.get("total") if isinstance(value, dict) else None
limit = value.get("limit") if isinstance(value, dict) else None
offset = value.get("offset") if isinstance(value, dict) else None
has_more = value.get("has_more") if isinstance(value, dict) else None
if (
    not isinstance(items, list)
    or not isinstance(total, int) or isinstance(total, bool) or total < 0
    or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200
    or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
    or not isinstance(has_more, bool)
    or len(items) > limit
    or total < offset + len(items)
    or has_more != (offset + len(items) < total)
):
    raise SystemExit(1)
PY
    elif [[ "$path" == "/api/fae/reports/latest" ]]; then
      "$python" - "$temporary/fae-owner-api.json" <<'PY' || fail
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
metrics = value.get("metrics") if isinstance(value, dict) else None
source = value.get("source") if isinstance(value, dict) else None
dimensions = {
    item.get("dimension") for item in metrics or () if isinstance(item, dict)
}
if (
    value.get("schema_name") != "fae.analysis-report"
    or value.get("status") != "ready"
    or value.get("source", {}).get("agent_id") != "ai-fae-agent"
    or not isinstance(source, dict)
    or not all(isinstance(source.get(field), int) for field in (
        "session_count", "turn_count", "reviewed_session_count"
    ))
    or dimensions != {
        "usage", "business_value", "answer_effectiveness", "insights_improvement"
    }
    or "canonical_key" in json.dumps(value, ensure_ascii=False)
):
    raise SystemExit(1)
PY
    else
      "$python" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$temporary/fae-owner-api.json" || fail
    fi
    if [[ "$path" == "/api/fae/overview" ]]; then
      /bin/cp "$temporary/fae-owner-api.json" "$temporary/fae-canonical-overview.json" || fail
    fi
  done

  status_code="$("${curl_owner[@]}" -o "$temporary/fae-compat-api.json" -w '%{http_code}' \
    "$base/api/admin/fae/overview")" || fail
  [[ "$status_code" == "200" ]] || fail
  "$python" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
    "$temporary/fae-compat-api.json" || fail
  /usr/bin/cmp -s "$temporary/fae-canonical-overview.json" \
    "$temporary/fae-compat-api.json" || fail

  status_code="$("${curl_owner[@]}" -o "$temporary/fae-reports.html" -w '%{http_code}' "$base/fae/manage/reports")" || fail
  [[ "$status_code" == "200" ]] || fail
  "$python" - "$temporary/fae-reports.html" <<'PY' || fail
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
lowered = text.casefold()
if not text.strip() or "<html" not in lowered:
    raise SystemExit(1)
if any(term in lowered for term in ("sample report", "demo report", "fixture report")):
    raise SystemExit(1)
PY
  verify_fae_reports_ready "$temporary/owner.browser.json" "$temporary"
  verify_fae_reports_compatibility "$temporary/owner.browser.json" "$temporary"

  for path in "${fae_member_denied_paths[@]}"; do
    status_code="$("${curl_member[@]}" -o /dev/null -w '%{http_code}' "$base$path")" || fail
    [[ "$status_code" == "403" ]] || fail
  done
  status_code="$("${curl_viewer[@]}" -o "$temporary/fae-viewer-shell.html" -w '%{http_code}' "$base/fae/manage/")" || fail
  [[ "$status_code" == "200" ]] || fail
  verify_fae_viewer_denied "$temporary/viewer.browser.json" "$temporary"
  for path in "${fae_viewer_denied_apis[@]}"; do
    status_code="$("${curl_viewer[@]}" -o /dev/null -w '%{http_code}' "$base$path")" || fail
    [[ "$status_code" == "403" ]] || fail
  done

  status_code="$("${curl_owner[@]}" -o "$temporary/fae-mutation-denied.json" -w '%{http_code}' \
    -X POST -H 'Content-Type: application/json' --data-binary '{}' \
    "$base/api/fae/issues")" || fail
  [[ "$status_code" == "403" ]] || fail
  "$python" - "$temporary/fae-mutation-denied.json" <<'PY' || fail
import json
import sys

if json.load(open(sys.argv[1], encoding="utf-8")) != {"detail": "cloud_review_read_only"}:
    raise SystemExit(1)
PY
}

verify_platform_workspace_history() {
  local agent_id conversation_id encoded_id path status_code index
  local -a direct_agent_ids=(
    'hr-bot'
    'marketing-prospecting-bot'
    'marketing-inbound-bot'
    'marketing-voice-bot'
    'marketing-intelligence-bot'
    'marketing-gtm-bot'
  )
  local -a workspace_paths=(
    '/hr'
    '/marketing/prospecting'
    '/marketing/inbound'
    '/marketing/voice'
    '/marketing/intelligence'
    '/marketing/gtm'
  )

  for index in "${!direct_agent_ids[@]}"; do
    agent_id="${direct_agent_ids[$index]}"
    path="${workspace_paths[$index]}"
    status_code="$("${curl_member[@]}" -o "$temporary/history-$index.json" \
      -w '%{http_code}' "$base/api/v1/conversations?limit=100&direct_agent_id=$agent_id")" || fail
    [[ "$status_code" == "200" ]] || fail
    conversation_id="$("$python" - "$temporary/history-$index.json" "$agent_id" <<'PY'
import json
import sys
import uuid

value = json.load(open(sys.argv[1], encoding="utf-8"))
items = value.get("items") if isinstance(value, dict) else None
if not isinstance(items, list):
    raise SystemExit(1)
for item in items:
    if not isinstance(item, dict) or item.get("direct_agent_id") != sys.argv[2]:
        raise SystemExit(1)
if not items:
    raise SystemExit(1)
print(uuid.UUID(items[0]["conversation_id"]))
PY
    )" || fail
    status_code="$("${curl_member[@]}" -o /dev/null -w '%{http_code}' \
      "$base/api/v1/conversations/$conversation_id")" || fail
    [[ "$status_code" == "200" ]] || fail
    encoded_id="$("$python" -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$conversation_id")" || fail
    status_code="$("${curl_member[@]}" -o /dev/null -w '%{http_code}' \
      "$base$path/conversations/$encoded_id")" || fail
    [[ "$status_code" == "200" ]] || fail
  done

  status_code="$("${curl_owner[@]}" -o "$temporary/owner-history.json" -w '%{http_code}' \
    "$base/api/v1/conversations?limit=1")" || fail
  [[ "$status_code" == "200" ]] || fail
  conversation_id="$("$python" - "$temporary/owner-history.json" <<'PY'
import json
import sys
import uuid

value = json.load(open(sys.argv[1], encoding="utf-8"))
items = value.get("items") if isinstance(value, dict) else None
if not isinstance(items, list):
    raise SystemExit(1)
if not items:
    raise SystemExit(1)
print(uuid.UUID(items[0]["conversation_id"]))
PY
  )" || fail
  status_code="$("${curl_member[@]}" -o "$temporary/cross-owner-history.json" \
    -w '%{http_code}' "$base/api/v1/conversations/$conversation_id")" || fail
  [[ "$status_code" == "404" ]] || fail
  "$python" - "$temporary/cross-owner-history.json" <<'PY' || fail
import json
import sys

if json.load(open(sys.argv[1], encoding="utf-8")) != {"detail": "Conversation not found"}:
    raise SystemExit(1)
PY
}

verify_fae_internal_history() {
  local member_launch_file="$1" role launch_file status_code selected_session_id
  local member_session_id owner_session_id
  [[ -f "$member_launch_file" && ! -L "$member_launch_file" ]] || fail
  "$python" - "$temporary/fae-member-account.json" \
    "$temporary/fae-owner-account.json" <<'PY' || fail
import json
import sys
import uuid

member_internal_user_id = uuid.UUID(
    json.load(open(sys.argv[1], encoding="utf-8"))["internal_user_id"]
)
owner_internal_user_id = uuid.UUID(
    json.load(open(sys.argv[2], encoding="utf-8"))["internal_user_id"]
)
if member_internal_user_id == owner_internal_user_id:
    raise SystemExit(1)
PY

  status_code="$("${curl_owner[@]}" -o "$temporary/fae-owner-launch.json" \
    -w '%{http_code}' -X POST "$base/api/v1/agents/ai-fae-agent/launch")" || fail
  [[ "$status_code" == "200" ]] || fail

  for role in member owner; do
    if [[ "$role" == "member" ]]; then
      launch_file="$member_launch_file"
    else
      launch_file="$temporary/fae-owner-launch.json"
    fi
    "$python" - "$launch_file" > "$temporary/fae-$role-exchange.json" <<'PY' || fail
import json
import re
import sys
import urllib.parse

value = json.load(open(sys.argv[1], encoding="utf-8"))
url = value.get("launch_url")
if not isinstance(url, str):
    raise SystemExit(1)
parsed = urllib.parse.urlsplit(url)
if (parsed.scheme, parsed.netloc, parsed.path) != (
    "https", "agent.orbbec.com.cn", "/fae/"
):
    raise SystemExit(1)
if parsed.query or parsed.fragment.count("=") != 1:
    raise SystemExit(1)
key, code = parsed.fragment.split("=", 1)
if key != "platform_launch" or urllib.parse.unquote(code) != code:
    raise SystemExit(1)
if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", code) is None:
    raise SystemExit(1)
print(json.dumps({"code": code}, separators=(",", ":")))
PY
    status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
      --max-time 15 --cookie-jar "$temporary/fae-$role.jar" \
      -o "$temporary/fae-$role-session.json" -w '%{http_code}' -X POST \
      -H 'Origin: https://agent.orbbec.com.cn' -H 'Content-Type: application/json' \
      --data-binary "@$temporary/fae-$role-exchange.json" \
      "$base/fae/api/enterprise/session")" || fail
    [[ "$status_code" == "201" ]] || fail
    "$python" - "$temporary/fae-$role-session.json" \
      "$temporary/fae-$role.jar" "$temporary/fae-$role-account.json" <<'PY' || fail
import json
import pathlib
import sys

session = json.load(open(sys.argv[1], encoding="utf-8"))
platform_account = json.load(open(sys.argv[3], encoding="utf-8"))
if set(session) != {
    "authenticated", "authentication_mode", "display_name",
    "partner_display_name", "csrf_token",
}:
    raise SystemExit(1)
if session["authenticated"] is not True:
    raise SystemExit(1)
if session["authentication_mode"] != "platform_enterprise":
    raise SystemExit(1)
if session["display_name"] != platform_account.get("display_name"):
    raise SystemExit(1)
if not isinstance(session["csrf_token"], str) or not session["csrf_token"]:
    raise SystemExit(1)
cookies = []
for raw_line in pathlib.Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
    line = raw_line.removeprefix("#HttpOnly_")
    if not line or line.startswith("#"):
        continue
    fields = line.split("\t")
    if len(fields) == 7 and fields[5] == "__Host-fae_enterprise_session":
        cookies.append(fields)
if len(cookies) != 1:
    raise SystemExit(1)
cookie = cookies[0]
if cookie[2] != "/" or cookie[3] != "TRUE" or not cookie[6]:
    raise SystemExit(1)
PY
    status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
      --max-time 15 --cookie "$temporary/fae-$role.jar" \
      -o "$temporary/fae-$role-history.json" -w '%{http_code}' \
      "$base/fae/api/authenticated/conversations?limit=30")" || fail
    [[ "$status_code" == "200" ]] || fail
  done

  "$python" - "$temporary/fae-member-history.json" \
    "$temporary/fae-owner-history.json" \
    > "$temporary/fae-history-subjects.txt" <<'PY' || fail
import json
import sys
import uuid

def session_ids(path):
    value = json.load(open(path, encoding="utf-8"))
    if set(value) != {"items", "next_cursor"} or not isinstance(value["items"], list):
        raise SystemExit(1)
    ids = []
    for item in value["items"]:
        if set(item) != {
            "session_id", "title", "channel", "created_at", "last_active_at",
        }:
            raise SystemExit(1)
        ids.append(str(uuid.UUID(item["session_id"])))
    return ids

member_session_ids = session_ids(sys.argv[1])
owner_session_ids = session_ids(sys.argv[2])
if not member_session_ids or not owner_session_ids:
    raise SystemExit(1)
member_session_id = member_session_ids[0]
owner_session_id = owner_session_ids[0]
if not set(member_session_ids).isdisjoint(owner_session_ids):
    raise SystemExit(1)
print(member_session_id, owner_session_id)
PY
  read -r member_session_id owner_session_id \
    < "$temporary/fae-history-subjects.txt" || fail

  for role in member owner; do
    if [[ "$role" == "member" ]]; then
      selected_session_id="$member_session_id"
    else
      selected_session_id="$owner_session_id"
    fi
    status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
      --max-time 15 --cookie "$temporary/fae-$role.jar" \
      -o "$temporary/fae-$role-history-detail.json" -w '%{http_code}' \
      "$base/fae/api/authenticated/conversations/$selected_session_id")" || fail
    [[ "$status_code" == "200" ]] || fail
    "$python" - "$temporary/fae-$role-history-detail.json" \
      "$selected_session_id" <<'PY' || fail
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
if set(value) != {
    "session_id", "channel", "messages", "current_schema", "attachments",
}:
    raise SystemExit(1)
if value["session_id"] != sys.argv[2]:
    raise SystemExit(1)
if not isinstance(value["messages"], list) or not isinstance(value["attachments"], list):
    raise SystemExit(1)
PY
  done

  status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
    --max-time 15 --cookie "$temporary/fae-owner.jar" \
    -o "$temporary/fae-owner-cross-history.json" -w '%{http_code}' \
    "$base/fae/api/authenticated/conversations/$member_session_id")" || fail
  [[ "$status_code" == "404" ]] || fail
  status_code="$(/usr/bin/curl --noproxy '*' --silent --show-error \
    --max-time 15 --cookie "$temporary/fae-member.jar" \
    -o "$temporary/fae-member-cross-history.json" -w '%{http_code}' \
    "$base/fae/api/authenticated/conversations/$owner_session_id")" || fail
  [[ "$status_code" == "404" ]] || fail
  /usr/bin/cmp -s "$temporary/fae-owner-cross-history.json" \
    "$temporary/fae-member-cross-history.json" || fail
  "$python" - "$temporary/fae-owner-cross-history.json" <<'PY' || fail
import json
import sys

if json.load(open(sys.argv[1], encoding="utf-8")) != {"detail": "conversation not found"}:
    raise SystemExit(1)
PY
}

verify_markdown_rendering() {
  local conversation_id="$1" browser_cookie_file="$2" workspace="$3"
  local chrome=/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
  local profile="$workspace/chrome-profile" active_port="$profile/DevToolsActivePort"
  [[ -x "$chrome" ]] || fail
  /bin/mkdir -m 700 "$profile"
  "$chrome" --headless=new --disable-gpu --no-first-run --no-default-browser-check \
    --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 \
    --user-data-dir="$profile" about:blank > /dev/null 2>&1 &
  chrome_pid="$!"
  for _attempt in $(/usr/bin/seq 1 6); do
    [[ -s "$active_port" ]] && break
    /bin/sleep 5
  done
  [[ -s "$active_port" ]] || fail
  chrome_port="$(/usr/bin/sed -n '1p' "$active_port")"
  [[ "$chrome_port" =~ ^[0-9]+$ ]] || fail
  /usr/bin/curl --silent --show-error --fail --request PUT --max-time 5 \
    "http://127.0.0.1:$chrome_port/json/new?about%3Ablank" > "$workspace/chrome-target.json" || fail
  page_socket="$($python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["webSocketDebuggerUrl"])' "$workspace/chrome-target.json")" || fail
  [[ "$page_socket" == ws://127.0.0.1:* || "$page_socket" == ws://localhost:* ]] || fail
  /usr/bin/node - "$page_socket" "$browser_cookie_file" "https://agent.orbbec.com.cn/conversations/$conversation_id" <<'NODE' || fail
const fs = require("fs");
const [socketUrl, cookiePath, missionUrl] = process.argv.slice(2);
const cookies = JSON.parse(fs.readFileSync(cookiePath, "utf8"));
if (Object.keys(cookies).sort().join(",") !== "__Host-platform_csrf,__Host-platform_session") process.exit(1);

const socket = new WebSocket(socketUrl);
const pending = new Map();
let commandId = 0;
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error("cdp command failed"));
  else resolve(message.result);
};
const opened = new Promise((resolve, reject) => {
  socket.onopen = resolve;
  socket.onerror = () => reject(new Error("cdp connection failed"));
});
const command = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++commandId;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});
const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

(async () => {
  await opened;
  await command("Network.enable");
  for (const name of ["__Host-platform_session", "__Host-platform_csrf"]) {
    const cookie = await command("Network.setCookie", {
      name, value: cookies[name], url: "https://agent.orbbec.com.cn",
      secure: true, httpOnly: name === "__Host-platform_session", sameSite: "Lax",
    });
    if (!cookie.success) throw new Error("cookie rejected");
  }
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Page.navigate", { url: "https://agent.orbbec.com.cn/" });
  let rootRendered = false;
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await pause(5000);
    const root = await command("Runtime.evaluate", {
      expression: `(() => {
        const heading = document.getElementById('brain-heading');
        return Boolean(heading && heading.textContent.trim() === 'Agent 大脑');
      })()`,
      returnByValue: true,
    });
    if (root.result && root.result.value === true) { rootRendered = true; break; }
  }
  if (!rootRendered) throw new Error("brain root did not render");
  await command("Page.navigate", { url: missionUrl });
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await pause(5000);
    const evaluation = await command("Runtime.evaluate", {
      expression: `(() => {
        const blocks = Array.from(document.querySelectorAll('.message-markdown'));
        const visibleText = document.body.innerText;
        const forbiddenText = ['诊断详情', 'hr-bot', 'accepted'];
        const hasForbiddenMissionLink = Array.from(document.querySelectorAll('a'))
          .some((link) => link.getAttribute('href')?.includes('/missions/'));
        return !forbiddenText.some((value) => visibleText.includes(value)) &&
          !hasForbiddenMissionLink &&
          blocks.some((block) => block.textContent.trim().length > 0 &&
            block.querySelector('p,h1,h2,h3,h4,h5,h6,ul,ol,pre,blockquote,table'));
      })()`,
      returnByValue: true,
    });
    if (evaluation.result && evaluation.result.value === true) {
      process.stdout.write("BRAIN_ROOT_RENDER_OK MARKDOWN_RENDER_OK\n");
      socket.close();
      return;
    }
  }
  throw new Error("markdown did not render");
})().catch(() => process.exit(1));
NODE
  /bin/kill "$chrome_pid" >/dev/null 2>&1 || true
  wait "$chrome_pid" >/dev/null 2>&1 || true
  chrome_pid=""
}

accept_real() {
  require_private_file "$member_cookie_file" 8192
  require_private_file "$owner_cookie_file" 8192
  require_private_file "$viewer_cookie_file" 8192
  require_private_file "$hr_prompt_file" 32768
  require_private_file "$interruption_prompt_file" 32768
  evidence_parent="$(/usr/bin/dirname "$evidence_file")"
  [[ -d "$evidence_parent" && ! -L "$evidence_parent" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %u' "$evidence_parent")" == "700 $(/usr/bin/id -u)" ]] || fail
  [[ ! -L "$evidence_file" ]] || fail
  if [[ -e "$evidence_file" ]]; then require_private_file "$evidence_file" 65536; fi
  temporary="$(/usr/bin/mktemp -d)"
  chrome_pid=""
  node_pid=""
  probe_watchdog_pid=""
  worker_stopped=0
  restore_worker() {
    [[ "$worker_stopped" == "1" ]] || return 0
    run_agentops_control worker-restore >/dev/null || return 1
    for _attempt in $(/usr/bin/seq 1 12); do
      if /usr/bin/nc -z -w 2 127.0.0.1 9120 >/dev/null 2>&1; then worker_stopped=0; return 0; fi
      /bin/sleep 5
    done
    return 1
  }
  cleanup_accept_resources() {
    cleanup_status=0
    restore_worker || cleanup_status=1
    cleanup_fae_report_processes
    /bin/rm -rf -- "$temporary"
    return "$cleanup_status"
  }
  accept_failure_rollback() {
    status="$?"
    trap - ERR EXIT
    cleanup_accept_resources || status=1
    if [[ "$status" -ne 0 ]]; then
      remote_feature 0 || status=1
    fi
    release_action_lock || status=1
    exit "$status"
  }
  trap accept_failure_rollback ERR EXIT
  cookie_config "$member_cookie_file" "$temporary/member.curl" "$temporary/member.browser.json"
  cookie_config "$owner_cookie_file" "$temporary/owner.curl" "$temporary/owner.browser.json"
  cookie_config "$viewer_cookie_file" "$temporary/viewer.curl" "$temporary/viewer.browser.json"
  base=https://agent.orbbec.com.cn
  curl_member=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/member.curl" --max-time 15)
  curl_owner=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" --max-time 15)
  curl_viewer=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/viewer.curl" --max-time 15)
  office_url="https://agent.orbbec.com.cn/office/?view=services"
  [[ "$("${curl_member[@]}" -o "$temporary/office-services.html" -w '%{http_code}' "$office_url")" == "200" ]] || fail
  /usr/bin/grep -Fq '<html' "$temporary/office-services.html" || fail

  restored_conversation_id=""
  third_turn_id=""
  third_mission_id=""
  restore_conversation() {
    require_private_file "$evidence_file" 65536
    read -r restored_conversation_id restored_after < <("$python" - "$evidence_file" <<'PY'
import pathlib,re,sys
values={}
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if "=" in line:
        key,value=line.split("=",1); values[key]=value
if not re.fullmatch(r"[0-9a-f-]{36}",values.get("conversation_id","")): raise SystemExit(1)
if not values.get("last_event_seq","").isdigit() or int(values["last_event_seq"]) < 1: raise SystemExit(1)
print(values["conversation_id"],values["last_event_seq"])
PY
    ) || fail
    "$python" - "$temporary/restore-follow-up.json" <<'PY'
import json,pathlib,sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"text":"继续这个对话：请把前两轮结果收敛为一份可直接执行的行动清单。"},ensure_ascii=False,separators=(",",":")),encoding="utf-8")
PY
    /bin/chmod 600 "$temporary/restore-follow-up.json"
    third_key="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
    [[ "$("${curl_member[@]}" -o "$temporary/restore-response.json" -w '%{http_code}' -X POST \
      -H 'Content-Type: application/json' -H "Idempotency-Key: $third_key" \
      --data-binary "@$temporary/restore-follow-up.json" "$base/api/v1/conversations/$restored_conversation_id/messages")" == "201" ]] || fail
    third_turn_id="$("$python" - "$temporary/restore-response.json" "$restored_conversation_id" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1],encoding="utf-8"))
if value["conversation"].get("conversation_id") != sys.argv[2]: raise SystemExit(1)
if "mission_id" in value["turn"] or "mission_id" in value["message"]: raise SystemExit(1)
print(uuid.UUID(value["turn"]["turn_id"]))
PY
    )" || fail
    third_mission_id="$(remote /bin/bash -s -- "$third_turn_id" <<'REMOTE'
set -euo pipefail
turn="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v turn="$turn" -c "select mission_id from platform_control.missions where turn_id=:'turn'::uuid;"
REMOTE
    )" || fail
    [[ "$third_mission_id" =~ ^[0-9a-f-]{36}$ ]] || fail
    [[ "$("${curl_member[@]}" -o "$temporary/restore-replay.json" -w '%{http_code}' -X POST \
      -H 'Content-Type: application/json' -H "Idempotency-Key: $third_key" \
      --data-binary "@$temporary/restore-follow-up.json" "$base/api/v1/conversations/$restored_conversation_id/messages")" == "200" ]] || fail
    [[ "$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["turn"]["turn_id"])' "$temporary/restore-replay.json")" == "$third_turn_id" ]] || fail
    "${curl_member[@]}" --max-time 900 -H 'Accept: text/event-stream' \
      "$base/api/v1/conversations/$restored_conversation_id/events?after=$restored_after" > "$temporary/restore-events.sse" || fail
    "$python" - "$temporary/restore-events.sse" "$restored_conversation_id" "$((restored_after + 1))" <<'PY' || fail
import json,sys
events=[]
for frame in open(sys.argv[1],encoding="utf-8").read().replace("\r\n","\n").split("\n\n"):
    if not frame or frame.startswith(":"): continue
    lines=frame.splitlines(); ids=[x[4:] for x in lines if x.startswith("id: ")]; data=[x[6:] for x in lines if x.startswith("data: ")]
    if len(ids)!=1 or len(data)!=1: raise SystemExit(1)
    value=json.loads(data[0])
    if value.get("seq") != int(ids[0]) or value.get("conversation_id") != sys.argv[2]: raise SystemExit(1)
    events.append((value["seq"],value["event_type"]))
start=int(sys.argv[3])
if not events or [x[0] for x in events] != list(range(start,events[-1][0]+1)): raise SystemExit(1)
if "turn.completed" not in [kind for _,kind in events]: raise SystemExit(1)
PY
    [[ "$("${curl_member[@]}" -o "$temporary/restore-messages.json" -w '%{http_code}' "$base/api/v1/conversations/$restored_conversation_id/messages")" == "200" ]] || fail
    "$python" - "$temporary/restore-messages.json" "$restored_conversation_id" "$third_turn_id" <<'PY' || fail
import json,sys
items=json.load(open(sys.argv[1],encoding="utf-8")).get("items")
if not isinstance(items,list) or len(items)!=6: raise SystemExit(1)
if [item.get("role") for item in items] != ["user","assistant"]*3: raise SystemExit(1)
if any(item.get("conversation_id") != sys.argv[2] for item in items): raise SystemExit(1)
if items[-2].get("turn_id") != sys.argv[3]: raise SystemExit(1)
if any("mission_id" in item for item in items): raise SystemExit(1)
PY
    restore_shape="$(remote /bin/bash -s -- "$restored_conversation_id" <<'REMOTE'
set -euo pipefail
conversation="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v conversation="$conversation" -c "select concat('turn_count=',count(*),',message_count=',(select count(*) from platform_control.conversation_messages where conversation_id=:'conversation'::uuid),',mission_count=',(select count(*) from platform_control.missions mission where mission.conversation_id=:'conversation'::uuid)) from platform_control.conversation_turns where conversation_id=:'conversation'::uuid;"
REMOTE
    )" || fail
    [[ "$restore_shape" == "turn_count=3,message_count=6,mission_count=3" ]] || fail
  }
  if [[ "$action" == "restore" ]]; then restore_conversation; fi

  member_account="$temporary/member-account.json"
  owner_account="$temporary/owner-account.json"
  [[ "$("${curl_member[@]}" -o "$member_account" -w '%{http_code}' "$base/api/v1/account")" == "200" ]] || fail
  [[ "$("${curl_owner[@]}" -o "$owner_account" -w '%{http_code}' "$base/api/v1/account")" == "200" ]] || fail
  member_role="$("$python" - "$member_account" <<'PY'
import json,sys
value=json.load(open(sys.argv[1],encoding="utf-8"))
if value.get("role") != "member" or not isinstance(value.get("csrf_token"),str) or not value["csrf_token"]: raise SystemExit(1)
print(value["role"])
PY
  )" || fail
  owner_role="$("$python" - "$owner_account" <<'PY'
import json,sys
value=json.load(open(sys.argv[1],encoding="utf-8"))
if value.get("role") not in {"platform_owner","platform_admin"} or not isinstance(value.get("csrf_token"),str) or not value["csrf_token"]: raise SystemExit(1)
print(value["role"])
PY
  )" || fail
  [[ "$member_role" == "member" && ( "$owner_role" == "platform_owner" || "$owner_role" == "platform_admin" ) ]] || fail
  verify_fae_workbench_cloud_contract
  verify_platform_workspace_history
  [[ "$("${curl_member[@]}" -o "$temporary/root.html" -w '%{http_code}' "$base/")" == "200" ]] || fail
  [[ "$("${curl_member[@]}" -o /dev/null -w '%{http_code}' "$base/admin")" == "403" ]] || fail
  [[ "$("${curl_owner[@]}" -o /dev/null -w '%{http_code}' "$base/admin")" == "200" ]] || fail
  for owner_path in /admin/sessions /admin/review /admin/activity; do
    [[ "$("${curl_owner[@]}" -o /dev/null -w '%{http_code}' "$base$owner_path")" == "200" ]] || fail
  done
  for owner_api in '/api/sessions?limit=1' '/api/review/overview?agent_id=hr-bot' '/api/operations/brief' '/api/operations/conversation-metrics'; do
    [[ "$("${curl_owner[@]}" -o "$temporary/owner-api.json" -w '%{http_code}' "$base$owner_api")" == "200" ]] || fail
    "$python" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$temporary/owner-api.json" || fail
  done
  [[ "$("${curl_member[@]}" -o "$temporary/catalog.json" -w '%{http_code}' "$base/api/v1/catalog/agents")" == "200" ]] || fail
  "$python" - "$temporary/catalog.json" <<'PY' || fail
import json,sys
agents={item.get("agent_id") for item in json.load(open(sys.argv[1],encoding="utf-8")).get("agents",[])}
if "hr-bot" not in agents or "voc" not in agents or "marketing-gtm-bot" in agents: raise SystemExit(1)
PY

  make_body() {
    "$python" - "$1" "$2" <<'PY'
import json,pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not text or len(text.encode()) > 32768: raise SystemExit(1)
pathlib.Path(sys.argv[2]).write_text(json.dumps({"text":text},ensure_ascii=False,separators=(",",":")),encoding="utf-8")
PY
    /bin/chmod 600 "$2"
  }
  make_body "$hr_prompt_file" "$temporary/hr.json"
  first_key="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  [[ "$("${curl_member[@]}" -o "$temporary/conversation.json" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $first_key" \
    --data-binary "@$temporary/hr.json" "$base/api/v1/conversations")" == "201" ]] || fail
  IFS=, read -r conversation_id first_turn_id < <("$python" - "$temporary/conversation.json" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1],encoding="utf-8"))
conversation=uuid.UUID(value["conversation"]["conversation_id"])
turn=uuid.UUID(value["turn"]["turn_id"])
if "mission_id" in value["turn"] or "mission_id" in value["message"]: raise SystemExit(1)
print(f"{conversation},{turn}")
PY
  ) || fail
  first_mission_id="$(remote /bin/bash -s -- "$first_turn_id" <<'REMOTE'
set -euo pipefail
turn="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v turn="$turn" -c "select mission_id from platform_control.missions where turn_id=:'turn'::uuid;"
REMOTE
  )" || fail
  [[ "$first_mission_id" =~ ^[0-9a-f-]{36}$ ]] || fail
  [[ "$("${curl_member[@]}" -o "$temporary/conversation-replay.json" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $first_key" \
    --data-binary "@$temporary/hr.json" "$base/api/v1/conversations")" == "200" ]] || fail
  [[ "$("$python" -c 'import json,sys; v=json.load(open(sys.argv[1])); print(v["turn"]["turn_id"])' "$temporary/conversation-replay.json")" == "$first_turn_id" ]] || fail
  [[ "$("${curl_member[@]}" -o /dev/null -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')" \
    --data-binary "@$temporary/hr.json" "$base/api/v1/agents/marketing-gtm-bot/conversations")" == "403" ]] || fail
  "${curl_member[@]}" --max-time 900 -H 'Accept: text/event-stream' \
    "$base/api/v1/conversations/$conversation_id/events?after=0" > "$temporary/first-events.sse" || fail
  IFS='|' read -r first_last_seq first_event_summary < <("$python" - "$temporary/first-events.sse" "$conversation_id" 1 <<'PY'
import json,sys
events=[]
for frame in open(sys.argv[1],encoding="utf-8").read().replace("\r\n","\n").split("\n\n"):
    if not frame or frame.startswith(":"): continue
    lines=frame.splitlines(); ids=[x[4:] for x in lines if x.startswith("id: ")]; data=[x[6:] for x in lines if x.startswith("data: ")]
    if len(ids)!=1 or len(data)!=1: raise SystemExit(1)
    value=json.loads(data[0])
    if value.get("seq") != int(ids[0]) or value.get("conversation_id") != sys.argv[2]: raise SystemExit(1)
    events.append((value["seq"],value["event_type"]))
start=int(sys.argv[3])
if not events or [x[0] for x in events] != list(range(start,events[-1][0]+1)): raise SystemExit(1)
types=[x[1] for x in events]
for required in ("conversation.started","turn.accepted","task.dispatched","agent.accepted","agent.result","turn.completed"):
    if required not in types: raise SystemExit(1)
print(f"{events[-1][0]}|"+",".join(f"{seq}:{kind}" for seq,kind in events))
PY
  )" || fail
  "$python" - "$temporary/follow-up.json" <<'PY'
import json,pathlib,sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"text":"继续：请基于上一轮结果给出三条可执行的 GitHub 搜索式，并说明筛选信号。"},ensure_ascii=False,separators=(",",":")),encoding="utf-8")
PY
  /bin/chmod 600 "$temporary/follow-up.json"
  second_key="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  [[ "$("${curl_member[@]}" -o "$temporary/follow-up-response.json" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $second_key" \
    --data-binary "@$temporary/follow-up.json" "$base/api/v1/conversations/$conversation_id/messages")" == "201" ]] || fail
  second_turn_id="$("$python" - "$temporary/follow-up-response.json" "$conversation_id" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1],encoding="utf-8"))
if value["conversation"].get("conversation_id") != sys.argv[2]: raise SystemExit(1)
if "mission_id" in value["turn"] or "mission_id" in value["message"]: raise SystemExit(1)
print(uuid.UUID(value["turn"]["turn_id"]))
PY
  )" || fail
  second_mission_id="$(remote /bin/bash -s -- "$second_turn_id" <<'REMOTE'
set -euo pipefail
turn="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v turn="$turn" -c "select mission_id from platform_control.missions where turn_id=:'turn'::uuid;"
REMOTE
  )" || fail
  [[ "$second_mission_id" =~ ^[0-9a-f-]{36}$ ]] || fail
  [[ "$second_turn_id" != "$first_turn_id" && "$second_mission_id" != "$first_mission_id" ]] || fail
  [[ "$("${curl_member[@]}" -o "$temporary/follow-up-replay.json" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $second_key" \
    --data-binary "@$temporary/follow-up.json" "$base/api/v1/conversations/$conversation_id/messages")" == "200" ]] || fail
  [[ "$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["turn"]["turn_id"])' "$temporary/follow-up-replay.json")" == "$second_turn_id" ]] || fail
  "${curl_member[@]}" --max-time 900 -H 'Accept: text/event-stream' \
    "$base/api/v1/conversations/$conversation_id/events?after=$first_last_seq" > "$temporary/second-events.sse" || fail
  IFS='|' read -r second_last_seq second_event_summary < <("$python" - "$temporary/second-events.sse" "$conversation_id" "$((first_last_seq + 1))" <<'PY'
import json,sys
events=[]
for frame in open(sys.argv[1],encoding="utf-8").read().replace("\r\n","\n").split("\n\n"):
    if not frame or frame.startswith(":"): continue
    lines=frame.splitlines(); ids=[x[4:] for x in lines if x.startswith("id: ")]; data=[x[6:] for x in lines if x.startswith("data: ")]
    if len(ids)!=1 or len(data)!=1: raise SystemExit(1)
    value=json.loads(data[0])
    if value.get("seq") != int(ids[0]) or value.get("conversation_id") != sys.argv[2]: raise SystemExit(1)
    events.append((value["seq"],value["event_type"]))
start=int(sys.argv[3])
if not events or [x[0] for x in events] != list(range(start,events[-1][0]+1)): raise SystemExit(1)
types=[x[1] for x in events]
for required in ("turn.accepted","task.dispatched","agent.accepted","agent.result","turn.completed"):
    if required not in types: raise SystemExit(1)
print(f"{events[-1][0]}|"+",".join(f"{seq}:{kind}" for seq,kind in events))
PY
  )" || fail
  [[ "$("${curl_member[@]}" --max-time 30 -o "$temporary/resume.sse" -w '%{http_code}' -H 'Accept: text/event-stream' \
    "$base/api/v1/conversations/$conversation_id/events?after=$second_last_seq")" == "200" ]] || fail
  [[ ! -s "$temporary/resume.sse" ]] || fail
  [[ "$("${curl_member[@]}" -o "$temporary/messages.json" -w '%{http_code}' "$base/api/v1/conversations/$conversation_id/messages")" == "200" ]] || fail
  "$python" - "$temporary/messages.json" "$conversation_id" "$first_turn_id" "$second_turn_id" <<'PY' || fail
import json,sys
items=json.load(open(sys.argv[1],encoding="utf-8")).get("items")
if not isinstance(items,list) or len(items)!=4: raise SystemExit(1)
if [item.get("role") for item in items] != ["user","assistant","user","assistant"]: raise SystemExit(1)
if any(item.get("conversation_id") != sys.argv[2] for item in items): raise SystemExit(1)
if [items[0].get("turn_id"),items[2].get("turn_id")] != [sys.argv[3],sys.argv[4]]: raise SystemExit(1)
if any("mission_id" in item for item in items): raise SystemExit(1)
PY
  [[ "$("${curl_member[@]}" -o "$temporary/history.json" -w '%{http_code}' "$base/api/v1/conversations?limit=100")" == "200" ]] || fail
  "$python" - "$temporary/history.json" "$conversation_id" <<'PY' || fail
import json,sys
items=json.load(open(sys.argv[1],encoding="utf-8")).get("items",[])
if sum(item.get("conversation_id")==sys.argv[2] for item in items) != 1: raise SystemExit(1)
PY
  conversation_db_summary="$(remote /bin/bash -s -- "$conversation_id" <<'REMOTE'
set -euo pipefail
conversation="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"
postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -F ',' -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v conversation="$conversation" -c "select concat('turn_count=',count(*),',message_count=',(select count(*) from platform_control.conversation_messages where conversation_id=:'conversation'::uuid),',mission_count=',(select count(*) from platform_control.missions mission where mission.conversation_id=:'conversation'::uuid)) from platform_control.conversation_turns where conversation_id=:'conversation'::uuid; select concat(run.agent_id,':',run.status,':',run.run_id) from platform_control.mission_runs run join platform_control.missions mission on mission.mission_id=run.mission_id where mission.conversation_id=:'conversation'::uuid and run.phase in ('professional','direct') order by run.created_at;"
REMOTE
  )" || fail
  /usr/bin/grep -Fxq 'turn_count=2,message_count=4,mission_count=2' <<<"$conversation_db_summary" || fail
  [[ "$(/usr/bin/grep -Ec 'hr-bot:(completed|succeeded):[0-9a-f-]{36}' <<<"$conversation_db_summary")" == "2" ]] || fail
  resume_duplicate_turns=0
  verify_markdown_rendering "$conversation_id" "$temporary/member.browser.json" "$temporary"
  mission_id="$first_mission_id"
  event_summary="$first_event_summary,$second_event_summary"
  db_summary="$conversation_db_summary"

  make_body "$interruption_prompt_file" "$temporary/interruption.json"
  interrupted_key="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  [[ "$("${curl_member[@]}" -o "$temporary/interrupted-mission.json" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $interrupted_key" \
    --data-binary "@$temporary/interruption.json" "$base/api/v1/brain/missions")" == "201" ]] || fail
  interrupted_mission_id="$("$python" -c 'import json,sys,uuid; value=json.load(open(sys.argv[1])); print(uuid.UUID(value["mission_id"]))' "$temporary/interrupted-mission.json")" || fail
  child_run_id=""
  child_run_state=""
  for _attempt in $(/usr/bin/seq 1 60); do
    child_state="$(remote /bin/bash -s -- "$interrupted_mission_id" <<'REMOTE'
set -euo pipefail
mission="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -F ',' -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v mission="$mission" -c "select run_id,status from platform_control.mission_runs where mission_id=:'mission'::uuid and phase in ('professional','direct') and agent_id='hr-bot' order by created_at limit 1;"
REMOTE
    )" || fail
    IFS=, read -r child_run_id child_run_state <<<"$child_state"
    [[ "$child_run_id" =~ ^[0-9a-f-]{36}$ && "$child_run_state" == "running" ]] && break
    /bin/sleep 5
  done
  [[ "$child_run_id" =~ ^[0-9a-f-]{36}$ && "$child_run_state" == "running" ]] || fail
  run_agentops_control worker-stop >/dev/null || fail
  worker_stopped=1
  for _attempt in $(/usr/bin/seq 1 12); do
    ! /usr/bin/nc -z -w 2 127.0.0.1 9120 >/dev/null 2>&1 && break
    /bin/sleep 5
  done
  ! /usr/bin/nc -z -w 2 127.0.0.1 9120 >/dev/null 2>&1 || fail
  interrupted_state=""
  for _attempt in $(/usr/bin/seq 1 72); do
    interrupted_state="$(remote /bin/bash -s -- "$interrupted_mission_id" <<'REMOTE'
set -euo pipefail
mission="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -F ',' -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v mission="$mission" -c "select mission.status,(select count(*) from platform_control.mission_events event where event.mission_id=mission.mission_id and event.event_type='mission.interrupted'),(select count(*) from platform_control.mission_runs run where run.mission_id=mission.mission_id and run.phase in ('professional','direct') and run.agent_id='hr-bot') from platform_control.missions mission where mission.mission_id=:'mission'::uuid;"
REMOTE
    )" || fail
    [[ "$interrupted_state" =~ ^(interrupted|partially_completed),1,1$ ]] && break
    /bin/sleep 5
  done
  [[ "$interrupted_state" =~ ^(interrupted|partially_completed),1,1$ ]] || fail
  restore_worker || fail
  "${curl_member[@]}" --max-time 900 -H 'Accept: text/event-stream' \
    "$base/api/v1/brain/missions/$interrupted_mission_id/events?after=0" > "$temporary/interrupted.sse" || fail
  /usr/bin/grep -Fq 'mission.interrupted' "$temporary/interrupted.sse" || fail
  duplicate_count="$(remote /bin/bash -s -- "$interrupted_mission_id" <<'REMOTE'
set -euo pipefail
mission="$1"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v mission="$mission" -c "select count(*) from platform_control.mission_runs where mission_id=:'mission'::uuid and phase in ('professional','direct') and agent_id='hr-bot';"
REMOTE
  )" || fail
  [[ "$duplicate_count" == "1" ]] || fail

  remote_evidence="$(remote /bin/bash -s <<'REMOTE'
set -euo pipefail
root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"
api="$(docker compose --env-file "$env" -f "$compose" ps -q platform-api)"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
key="$(docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c "select key_id from platform_control.execution_worker_keys where worker_id='agentops-mac-primary' and status='active' order by created_at desc limit 1")"
[[ "$key" =~ ^worker-v[1-9][0-9]*$ ]] || exit 1
fae=ai-fae-backend
printf 'release_sha=%s\napi_container_id=%s\napi_started_at=%s\nworker_key_id=%s\nfae_container_id=%s\nfae_image_id=%s\nfae_started_at=%s\nfae_restart_count=%s\nfae_config_hash=%s\nfae_mounts_hash=%s\nfae_health=%s\nfae_domain_hash=%s\nfae_legacy_ip_hash=%s\nagent_nginx_hash=%s\n' \
  "$(basename "$release")" "$api" "$(docker inspect --format '{{.State.StartedAt}}' "$api")" "$key" \
  "$(docker inspect --format '{{.Id}}' "$fae")" "$(docker inspect --format '{{.Image}}' "$fae")" \
  "$(docker inspect --format '{{.State.StartedAt}}' "$fae")" "$(docker inspect --format '{{.RestartCount}}' "$fae")" \
  "$(docker inspect --format '{{json .Config}}' "$fae" | sha256sum | awk '{print $1}')" \
  "$(docker inspect --format '{{json .Mounts}}' "$fae" | sha256sum | awk '{print $1}')" \
  "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$fae")" \
  "$(curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | sha256sum | awk '{print $1}')" \
  "$(curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | sha256sum | awk '{print $1}')" \
  "$(sha256sum /etc/nginx/sites-available/agent-domain.conf | awk '{print $1}')"
REMOTE
  )" || fail
  metabot_release_sha="$(run_agentops_control metabot-release-sha)" || fail
  agent_team_release_sha="$(run_agentops_control agent-team-release-sha)" || fail
  [[ "$metabot_release_sha" =~ ^[0-9a-f]{40}$ && "$agent_team_release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
  local_listener_table="$(/usr/sbin/lsof -nP -iTCP -sTCP:LISTEN | /usr/bin/awk 'NR>1 && $9 ~ /^127\.0\.0\.1:(9101|9102|9103|9104|9105|9107|9108|9110|9120)$/ {print $9}' | /usr/bin/sort -u | /usr/bin/paste -sd, -)"
  [[ "$local_listener_table" == *"127.0.0.1:9110"* && "$local_listener_table" == *"127.0.0.1:9120"* ]] || fail
  cleanup_accept_resources || fail
  evidence_generation="$evidence_file.generation.$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  evidence_previous="$evidence_file.previous.$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  evidence_current_part="$evidence_file.part.$$"
  [[ ! -e "$evidence_generation" && ! -L "$evidence_generation" && ! -e "$evidence_previous" && ! -L "$evidence_previous" && ! -e "$evidence_current_part" && ! -L "$evidence_current_part" ]] || fail
  {
    /usr/bin/printf '%s\n' "$remote_evidence"
    /usr/bin/printf 'conversation_id=%s\nfirst_turn_id=%s\nsecond_turn_id=%s\nfirst_mission_id=%s\nsecond_mission_id=%s\nlast_event_seq=%s\nturn_count=2\nmessage_count=4\nresume_duplicate_turns=%s\nmission_id=%s\nchild_run_id=%s\nevents=%s\ninterrupted_mission_id=%s\ninterrupted_child_run_id=%s\nduplicate_child_runs=%s\n' \
      "$conversation_id" "$first_turn_id" "$second_turn_id" "$first_mission_id" "$second_mission_id" "$second_last_seq" "$resume_duplicate_turns" \
      "$mission_id" "$(/usr/bin/grep -Eo 'hr-bot:(completed|succeeded):[0-9a-f-]{36}' <<<"$db_summary" | /usr/bin/sed -n '1s/.*://p')" "$event_summary" \
      "$interrupted_mission_id" "$child_run_id" "$duplicate_count"
    if [[ -n "$restored_conversation_id" ]]; then
      /usr/bin/printf 'restored_conversation_id=%s\nthird_turn_id=%s\nthird_mission_id=%s\nrestore_turn_count=3\nrestore_message_count=6\n' \
        "$restored_conversation_id" "$third_turn_id" "$third_mission_id"
    fi
    /usr/bin/printf 'metabot_release_sha=%s\nagent_team_release_sha=%s\nlocal_listener_table=%s\nrollback=PLATFORM_AGENT_BRAIN_ENABLED=0,PLATFORM_AGENT_BRAIN_V2_ENABLED=0\n' \
      "$metabot_release_sha" "$agent_team_release_sha" "$local_listener_table"
    /usr/bin/printf 'OFFICE_ROUTE_UNCHANGED=true\n'
    /usr/bin/printf 'acceptance_status=complete\n'
  } > "$evidence_generation"
  /bin/chmod 600 "$evidence_generation"
  if [[ -e "$evidence_file" ]]; then
    /usr/bin/install -m 600 "$evidence_file" "$evidence_previous"
  fi
  /usr/bin/install -m 600 "$evidence_generation" "$evidence_current_part"
  /bin/mv -f "$evidence_current_part" "$evidence_file"
  trap - ERR EXIT
  trap action_lock_exit EXIT
  echo "AGENT_BRAIN_ACCEPTANCE_OK"
}

validate_v2_quality_review() {
  quality_review_file="$(/usr/bin/dirname "$config_path")/quality-review.json"
  require_private_file "$quality_review_file" 16384
  local release_sha
  release_sha="$(/usr/bin/git -C "$repository_root" rev-parse HEAD)" || fail
  "$python" - "$quality_review_file" "$release_sha" <<'PY'
import json,pathlib,re,sys
path=pathlib.Path(sys.argv[1]); release_sha=sys.argv[2]
value=json.loads(path.read_bytes())
if set(value) != {'schema_version','release_sha','reviewer_id','decision','scenarios'}: raise SystemExit(1)
if value['schema_version'] != 1 or value['release_sha'] != release_sha: raise SystemExit(1)
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,127}',value['reviewer_id']): raise SystemExit(1)
if value['decision'] != 'approved': raise SystemExit(1)
rows=value['scenarios']
if not isinstance(rows,list) or [row.get('scenario_id') for row in rows] != ['hr_quality','marketing_quality']: raise SystemExit(1)
for row in rows:
    if set(row) != {'scenario_id','outcome','material_defects'}: raise SystemExit(1)
    if row['outcome'] != 'approved' or row['material_defects'] != []: raise SystemExit(1)
print(value['reviewer_id'])
PY
}

accept_v2_real() {
  require_private_file "$member_cookie_file" 8192
  require_private_file "$owner_cookie_file" 8192
  require_private_file "$viewer_cookie_file" 8192
  require_private_file "$hr_prompt_file" 32768
  evidence_parent="$(/usr/bin/dirname "$evidence_file")"
  [[ -d "$evidence_parent" && ! -L "$evidence_parent" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %u' "$evidence_parent")" == "700 $(/usr/bin/id -u)" ]] || fail
  [[ ! -L "$evidence_file" ]] || fail
  reviewer_id="$(validate_v2_quality_review)" || fail
  temporary="$(/usr/bin/mktemp -d)"
  chrome_pid=""
  node_pid=""
  probe_watchdog_pid=""
  cleanup_v2_accept() {
    cleanup_fae_report_processes
    /bin/rm -rf -- "$temporary"
  }
  v2_accept_failure() {
    status="$?"
    trap - ERR EXIT
    cleanup_v2_accept
    remote_feature 0 || status=1
    release_action_lock || status=1
    exit "$status"
  }
  trap v2_accept_failure ERR EXIT
  cookie_config "$member_cookie_file" "$temporary/member.curl" "$temporary/member.browser.json"
  cookie_config "$owner_cookie_file" "$temporary/owner.curl" "$temporary/owner.browser.json"
  cookie_config "$owner_cookie_file" "$temporary/access-owner.curl" "$temporary/access-owner.browser.json"
  cookie_config "$viewer_cookie_file" "$temporary/viewer.curl" "$temporary/viewer.browser.json"
  base=https://agent.orbbec.com.cn
  curl_member=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/member.curl" --max-time 15)
  curl_owner=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" --max-time 15)
  curl_viewer=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/viewer.curl" --max-time 15)
  verify_access_history_authorization_contract
  verify_access_history_browser_contract "$temporary/access-owner.browser.json" "$temporary"
  verify_fae_workbench_cloud_contract
  verify_platform_workspace_history
  verify_standalone_voc_release
  fae_before="$(remote_fae_snapshot)" || fail
  partner_gate="$(remote_partner_gate)" || fail
  [[ "$partner_gate" == $'PARTNER_PROVIDER_CONFIG_VALID=true\nPARTNER_LOGIN_EXPECTED=false\nPARTNER_PROVIDER_KIND=none\nPARTNER_RELEASE_REASON=partner_identity_disabled' ]] || fail
  PARTNER_PROVIDER_CONFIG_VALID=true
  PARTNER_LOGIN_EXPECTED=false

  office_url="$base/office/?view=services"
  [[ "$("${curl_member[@]}" -o "$temporary/office-services.html" -w '%{http_code}' "$office_url")" == "200" ]] || fail
  /usr/bin/grep -Fq '<html' "$temporary/office-services.html" || fail
  OFFICE_ROUTE_UNCHANGED=true
  [[ "$("${curl_member[@]}" -o /dev/null -w '%{http_code}' "$base/admin")" == "403" ]] || fail
  [[ "$("${curl_owner[@]}" -o /dev/null -w '%{http_code}' "$base/admin")" == "200" ]] || fail
  PLATFORM_ADMIN_ROUTE_UNCHANGED=true

  # cookie_config writes the CSRF header into the private curl config, keeping
  # the token out of the process list and command arguments.
  [[ "$("${curl_member[@]}" -o "$temporary/fae-launch.json" -w '%{http_code}' -X POST "$base/api/v1/agents/ai-fae-agent/launch")" == "200" ]] || fail
  "$python" - "$temporary/fae-launch.json" <<'PY' || fail
import json,re,sys,urllib.parse
value=json.load(open(sys.argv[1],encoding='utf-8'))
url=value.get('launch_url')
if not isinstance(url,str): raise SystemExit(1)
parsed=urllib.parse.urlsplit(url)
if (parsed.scheme,parsed.netloc,parsed.path) != ('https','agent.orbbec.com.cn','/fae/'): raise SystemExit(1)
if parsed.query or parsed.fragment.count('=') != 1: raise SystemExit(1)
fragment_key,fragment_code=parsed.fragment.split('=',1)
if fragment_key != 'platform_launch': raise SystemExit(1)
if urllib.parse.unquote(fragment_code) != fragment_code: raise SystemExit(1)
if re.fullmatch(r'[A-Za-z0-9_-]{32,256}', fragment_code) is None: raise SystemExit(1)
PY
  verify_fae_internal_history "$temporary/fae-launch.json"
  ENTERPRISE_FAE_LAUNCH_UNCHANGED=true

  [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error -o "$temporary/fae-identity-capabilities.json" -w '%{http_code}' --max-time 15 https://fae.orbbec.com.cn/identity/capabilities)" == "200" ]] || fail
  "$python" - "$temporary/fae-identity-capabilities.json" <<'PY' || fail
import json,sys
if json.load(open(sys.argv[1],encoding='utf-8')) != {'partner_login_available':False}: raise SystemExit(1)
PY
  make_body "$hr_prompt_file" "$temporary/v2-request.json"
  request_key="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  status_code="$("${curl_member[@]}" -o "$temporary/v2-create.json" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $request_key" \
    --data-binary "@$temporary/v2-request.json" "$base/api/v1/conversations")" || fail
  [[ "$status_code" == "201" ]] || fail
  IFS=, read -r conversation_id turn_id < <("$python" - "$temporary/v2-create.json" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1],encoding='utf-8'))
conversation=uuid.UUID(value['conversation']['conversation_id'])
turn=value['turn']
if turn.get('mission_id') is not None: raise SystemExit(1)
print(f"{conversation},{uuid.UUID(turn['turn_id'])}")
PY
  ) || fail
  "${curl_member[@]}" --max-time 900 -H 'Accept: text/event-stream' \
    "$base/api/v1/conversations/$conversation_id/events?after=0" > "$temporary/v2-events.sse" || fail
  event_summary="$("$python" - "$temporary/v2-events.sse" "$conversation_id" "$turn_id" <<'PY'
import json,sys
path,conversation_id,turn_id=sys.argv[1:]
events=[]
for frame in open(path,encoding='utf-8').read().replace('\r\n','\n').split('\n\n'):
    if not frame or frame.startswith(':'): continue
    lines=frame.splitlines(); ids=[x[4:] for x in lines if x.startswith('id: ')]; data=[x[6:] for x in lines if x.startswith('data: ')]
    if len(ids)!=1 or len(data)!=1: raise SystemExit(1)
    value=json.loads(data[0])
    rendered=json.dumps(value,ensure_ascii=False,separators=(',',':')).lower()
    if any(term in rendered for term in ('thinking','provider_request_id','ciphertext','tool_use_id')): raise SystemExit(1)
    if value.get('seq') != int(ids[0]) or value.get('conversation_id') != conversation_id: raise SystemExit(1)
    if value.get('turn_id') not in (None,turn_id): raise SystemExit(1)
    events.append((value['seq'],value['event_type']))
if not events or [x[0] for x in events] != list(range(events[0][0],events[-1][0]+1)): raise SystemExit(1)
types=[kind for _,kind in events]
if 'turn.completed' not in types or 'brain.answer_submitted' not in types: raise SystemExit(1)
print(','.join(f'{seq}:{kind}' for seq,kind in events))
PY
  )" || fail
  [[ "$("${curl_member[@]}" -o "$temporary/v2-messages.json" -w '%{http_code}' "$base/api/v1/conversations/$conversation_id/messages")" == "200" ]] || fail
  "$python" - "$temporary/v2-messages.json" "$conversation_id" "$turn_id" <<'PY' || fail
import json,sys
items=json.load(open(sys.argv[1],encoding='utf-8')).get('items')
if not isinstance(items,list) or len(items)!=2: raise SystemExit(1)
if [item.get('role') for item in items] != ['user','assistant']: raise SystemExit(1)
if any(item.get('conversation_id') != sys.argv[2] or item.get('turn_id') != sys.argv[3] for item in items): raise SystemExit(1)
answer=items[1].get('content')
if not isinstance(answer,str) or not answer.strip(): raise SystemExit(1)
PY
  db_summary="$(remote /bin/bash -s -- "$conversation_id" "$turn_id" <<'REMOTE'
set -euo pipefail
conversation="$1"; turn="$2"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"
postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -F ',' -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v conversation="$conversation" -v turn="$turn" -c "select concat('loop_status=',loop.status,',task_count=',(select count(*) from platform_brain.agent_tasks where loop_id=loop.loop_id),',mission_count=',(select count(*) from platform_control.missions where turn_id=:'turn'::uuid),',mission_run_count=',(select count(*) from platform_control.mission_runs run join platform_control.missions mission on mission.mission_id=run.mission_id where mission.turn_id=:'turn'::uuid)) from platform_brain.brain_loops loop where loop.conversation_id=:'conversation'::uuid and loop.turn_id=:'turn'::uuid;"
REMOTE
  )" || fail
  [[ "$db_summary" =~ ^loop_status=completed,task_count=[0-8],mission_count=0,mission_run_count=0$ ]] || fail
  [[ "$(remote_fae_snapshot)" == "$fae_before" ]] || fail
  PUBLIC_FAE_CHAT_UNCHANGED=true
  remote_evidence="$(remote /bin/bash -s <<'REMOTE'
set -euo pipefail
root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; evidence="$root/private/agent-brain-v2/provider-evidence.json"
fae=ai-fae-backend
printf 'release_sha=%s\nmanifest_sha256=%s\nsystem_prompt_sha256=%s\nprovider_evidence_sha256=%s\nfae_container_id=%s\nfae_image_id=%s\nfae_started_at=%s\nfae_restart_count=%s\n' \
  "$(basename "$release")" \
  "$(sha256sum "$release/deploy/cloud/brain-model.release.json" | awk '{print $1}')" \
  "$(sha256sum "$release/backend/app/agent_brain/prompts/brain_v1.md" | awk '{print $1}')" \
  "$(sha256sum "$evidence" | awk '{print $1}')" \
  "$(docker inspect --format '{{.Id}}' "$fae")" "$(docker inspect --format '{{.Image}}' "$fae")" \
  "$(docker inspect --format '{{.State.StartedAt}}' "$fae")" "$(docker inspect --format '{{.RestartCount}}' "$fae")"
REMOTE
  )" || fail
  evidence_generation="$evidence_file.generation.$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  {
    /usr/bin/printf '%s\n' "$remote_evidence"
    /usr/bin/printf 'brain_v2=true\nscenario_count=20\ncore_gate_count=3\nMIGRATIONS_049_050_051=applied\nWAIT_CURSOR_COLUMNS=0\nBRAIN_CURSOR_WATERLINE=passed\nPENDING_ACTION_FORCED_RECOVERY=passed\nTASK_PROTOCOL_ISOLATION=passed\nVOC_ACTION_EXACTLY_ONCE=passed\nPARTNER_PROVIDER_CONFIG_VALID=true\nPARTNER_LOGIN_EXPECTED=false\nPUBLIC_FAE_CHAT_UNCHANGED=true\nENTERPRISE_FAE_LAUNCH_UNCHANGED=true\nOFFICE_ROUTE_UNCHANGED=true\nPLATFORM_ADMIN_ROUTE_UNCHANGED=true\nconversation_id=%s\nturn_id=%s\nevents=%s\n%s\nreviewer_id=%s\nquality_review=approved\nV2_MISSION_RUN_WRITES=0\nFAE_MANAGED_FILES_UNCHANGED=true\nacceptance_status=complete\n' \
      "$conversation_id" "$turn_id" "$event_summary" "$db_summary" "$reviewer_id"
  } > "$evidence_generation"
  /bin/chmod 600 "$evidence_generation"
  /usr/bin/install -m 600 "$evidence_generation" "$evidence_file.part.$$"
  /bin/mv -f "$evidence_file.part.$$" "$evidence_file"
  cleanup_v2_accept
  trap - ERR EXIT
  trap action_lock_exit EXIT
  echo "AGENT_BRAIN_V2_ACCEPTANCE_OK"
}

publish_routes_only() {
  local route_snapshot_before route_snapshot_after nginx_transaction_id
  nginx_transaction_id="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  [[ "$nginx_transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail
  nginx_transaction_published="0"
  route_failure_rollback() {
    status="$?"
    trap - ERR EXIT
    if [[ "$status" -ne 0 && "$nginx_transaction_published" == "1" ]]; then
      rollback_formal_nginx_transaction "$nginx_transaction_id" || status=1
      nginx_transaction_published="0"
    fi
    release_action_lock || status=1
    exit "$status"
  }
  trap route_failure_rollback ERR EXIT
  route_snapshot_before="$(route_non_regression_snapshot)" || fail
  nginx_transaction_published="1"
  publish_formal_nginx "$nginx_transaction_id"
  route_snapshot_after="$(route_non_regression_snapshot)" || fail
  [[ "$route_snapshot_after" == "$route_snapshot_before" ]] || fail
  verify_canonical_workspace_routes || fail
  commit_formal_nginx_transaction "$nginx_transaction_id"
  nginx_transaction_published="0"
  trap - ERR EXIT
  trap action_lock_exit EXIT
}

enable_with_rollback() {
  local workspace_snapshot_before workspace_snapshot_after nginx_transaction_id
  nginx_transaction_id="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  [[ "$nginx_transaction_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail
  nginx_transaction_published="0"
  enable_failure_rollback() {
    status="$?"
    trap - ERR EXIT
    if [[ "$status" -ne 0 && "$nginx_transaction_published" == "1" ]]; then
      rollback_formal_nginx_transaction "$nginx_transaction_id" || status=1
      nginx_transaction_published="0"
    fi
    if [[ "$status" -ne 0 ]]; then remote_feature 0 || status=1; fi
    release_action_lock || status=1
    exit "$status"
  }
  trap enable_failure_rollback ERR EXIT
  local_runtime_preflight
  remote_feature 0
  run_relay_canary
  prepare_v2_reference_evidence
  v2_cutover_gates
  workspace_snapshot_before="$(workspace_non_regression_snapshot)" || fail
  nginx_transaction_published="1"
  publish_formal_nginx "$nginx_transaction_id"
  workspace_snapshot_after="$(workspace_non_regression_snapshot)" || fail
  [[ "$workspace_snapshot_after" == "$workspace_snapshot_before" ]] || fail
  remote_feature 1
  commit_formal_nginx_transaction "$nginx_transaction_id"
  nginx_transaction_published="0"
  trap - ERR EXIT
  trap action_lock_exit EXIT
}

case "$action" in
  preflight)
    local_runtime_preflight
    remote '/usr/bin/test "$(/usr/bin/stat -c "%a %U" /opt/orbbec-agent-platform/private/platform.env)" = "600 root"; ! /usr/bin/ss -H -lnt | /usr/bin/awk '\''$4 !~ /^(127\.0\.0\.1|\[::1\]|127\.0\.0\.53%lo|127\.0\.0\.54):/ {print $4}'\'' | /usr/bin/grep -Eq '\''^(0\.0\.0\.0|\[::\]):(5432|8080|910[1-8]|9110|9120)$'\''' || fail
    echo "AGENT_BRAIN_PREFLIGHT_OK"
    ;;
  reference)
    acquire_action_lock
    prepare_v2_reference_evidence
    release_action_lock || fail
    trap - EXIT
    ;;
  routes)
    acquire_action_lock
    publish_routes_only
    release_action_lock || fail
    trap - EXIT
    echo "AGENT_WORKSPACE_ROUTES_OK"
    ;;
  release)
    require_action_identity_schema
    acquire_action_lock
    enable_with_rollback
    accept_v2_real
    release_action_lock || fail
    trap - EXIT
    ;;
  accept)
    require_action_identity_schema
    acquire_action_lock
    v2_cutover_gates
    accept_v2_real
    release_action_lock || fail
    trap - EXIT
    ;;
  rollback)
    acquire_action_lock
    require_private_file "$owner_cookie_file" 8192
    require_private_file "$evidence_file" 65536
    read -r preserved_conversation preserved_turn < <("$python" - "$evidence_file" <<'PY'
import pathlib,re,sys
values={}
for line in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    if '=' in line:
        key,value=line.split('=',1); values[key]=value
if values.get('brain_v2') != 'true' or values.get('acceptance_status') != 'complete': raise SystemExit(1)
for key in ('conversation_id','turn_id'):
    if not re.fullmatch(r'[0-9a-f-]{36}',values.get(key,'')): raise SystemExit(1)
if values.get('V2_MISSION_RUN_WRITES') != '0': raise SystemExit(1)
print(values['conversation_id'],values['turn_id'])
PY
    ) || fail
    fae_before="$(remote_fae_snapshot)" || fail
    remote_feature 0
    [[ "$(remote_fae_snapshot)" == "$fae_before" ]] || fail
    temporary="$(/usr/bin/mktemp -d)"
    cleanup_rollback() {
      status="$?"
      trap - EXIT
      /bin/rm -rf -- "$temporary"
      release_action_lock || status=1
      exit "$status"
    }
    trap cleanup_rollback EXIT
    cookie_config "$owner_cookie_file" "$temporary/owner.curl" "$temporary/owner.browser.json"
    rollback_headers="$temporary/root.headers"
    [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" -D "$rollback_headers" -o /dev/null -w '%{http_code}' --max-time 15 https://agent.orbbec.com.cn/)" == "200" ]] || fail
    for owner_path in /admin /admin/sessions /admin/review /admin/activity; do
      [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" -o /dev/null -w '%{http_code}' --max-time 15 "https://agent.orbbec.com.cn$owner_path")" == "200" ]] || fail
    done
    for owner_api in '/api/sessions?limit=1' '/api/review/overview?agent_id=hr-bot' '/api/operations/brief' '/api/operations/conversation-metrics'; do
      [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" -o "$temporary/owner-api.json" -w '%{http_code}' --max-time 15 "https://agent.orbbec.com.cn$owner_api")" == "200" ]] || fail
      "$python" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$temporary/owner-api.json" || fail
    done
    [[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 15 https://fae.orbbec.com.cn/)" == "200" ]] || fail
    [[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 15 http://47.106.112.69/)" == "200" ]] || fail
    preserved_shape="$(remote /bin/bash -s -- "$preserved_conversation" "$preserved_turn" <<'REMOTE'
set -euo pipefail
conversation="$1"; turn="$2"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v conversation="$conversation" -v turn="$turn" -c "select concat('conversation_count=',(select count(*) from platform_control.conversations where conversation_id=:'conversation'::uuid),',turn_count=',(select count(*) from platform_control.conversation_turns where turn_id=:'turn'::uuid and conversation_id=:'conversation'::uuid),',loop_count=',(select count(*) from platform_brain.brain_loops where turn_id=:'turn'::uuid and conversation_id=:'conversation'::uuid),',mission_count=',(select count(*) from platform_control.missions where turn_id=:'turn'::uuid));"
REMOTE
    )" || fail
    [[ "$preserved_shape" == "conversation_count=1,turn_count=1,loop_count=1,mission_count=0" ]] || fail
    V2_ROLLBACK_HISTORY_PRESERVED=true
    [[ "$V2_ROLLBACK_HISTORY_PRESERVED" == "true" ]] || fail
    trap - EXIT
    /bin/rm -rf -- "$temporary"
    release_action_lock || fail
    echo "AGENT_BRAIN_ROLLBACK_OK"
    echo "AGENT_BRAIN_V2_ROLLBACK_OK"
    ;;
  restore)
    require_action_identity_schema
    acquire_action_lock
    enable_with_rollback
    accept_v2_real
    release_action_lock || fail
    trap - EXIT
    ;;
esac
