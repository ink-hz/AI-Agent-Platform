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
[[ "$action" == "preflight" || "$action" == "release" || "$action" == "accept" || "$action" == "rollback" || "$action" == "restore" ]] || fail
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
keys = {
    "schema_version",
    "member_cookie_file", "owner_cookie_file", "hr_prompt_file",
    "interruption_prompt_file", "relay_acceptance_config", "evidence_file",
}
if not isinstance(value, dict) or set(value) != keys or value["schema_version"] != 2:
    raise SystemExit(1)
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
member_cookie_file="$(config_value member_cookie_file)" || fail
owner_cookie_file="$(config_value owner_cookie_file)" || fail
hr_prompt_file="$(config_value hr_prompt_file)" || fail
interruption_prompt_file="$(config_value interruption_prompt_file)" || fail
relay_acceptance_config="$(config_value relay_acceptance_config)" || fail
evidence_file="$(config_value evidence_file)" || fail

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

run_agentops() {
  /usr/bin/sudo -n -u agentops /usr/bin/env -i \
    HOME=/Users/agentops USER=agentops LOGNAME=agentops \
    PATH=/Users/agentops/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
    /bin/sh -c 'cd /Users/agentops && exec "$@"' sh "$@"
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

local_runtime_preflight() {
  /usr/bin/nc -z -w 2 127.0.0.1 9110 || fail
  /usr/bin/nc -z -w 2 127.0.0.1 9120 || fail
  ! /usr/sbin/lsof -nP -iTCP:9110 -sTCP:LISTEN | /usr/bin/awk 'NR>1 {print $9}' | /usr/bin/grep -Ev '^127\.0\.0\.1:9110$' | /usr/bin/grep -q . || fail
  ! /usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN | /usr/bin/awk 'NR>1 {print $9}' | /usr/bin/grep -Ev '^127\.0\.0\.1:9120$' | /usr/bin/grep -q . || fail
}

run_relay_canary() {
  [[ -f "$relay_acceptance_config" && ! -L "$relay_acceptance_config" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$relay_acceptance_config")" == "600 agentops" ]] || fail
  relay_accept=/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/accept.sh
  [[ -x "$relay_accept" && ! -L "$relay_accept" ]] || fail
  relay_result="$(run_agentops "$relay_accept" "$relay_acceptance_config")" || fail
  [[ "$relay_result" == "AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 public_ports_added=0 duplicate_dispatches=0" ]] || fail
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
kept = [line for line in lines if not line.startswith("PLATFORM_AGENT_BRAIN_ENABLED=")]
raw = ("\n".join(kept + [f"PLATFORM_AGENT_BRAIN_ENABLED={selected}"]) + "\n").encode()
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
  local committed_template
  committed_template="$(git -C "$repository_root" show HEAD:deploy/cloud/agent-domain.nginx.conf 2>/dev/null)" || fail
  [[ "$committed_template" == "$(<"$repository_root/deploy/cloud/agent-domain.nginx.conf")" ]] || fail
  remote 'umask 077; /usr/bin/install -d -o root -g root -m 700 /opt/orbbec-agent-platform/private/agent-brain-release; /bin/cat > /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part; /bin/chown root:root /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part; /bin/chmod 600 /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part; /bin/mv -f /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf.part /opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf' \
    < "$repository_root/deploy/cloud/agent-domain.nginx.conf" || fail
  remote /bin/bash -s <<'REMOTE' || fail
set -eEuo pipefail
umask 077
fail() { echo AGENT_BRAIN_NGINX_FAILED >&2; exit 1; }
source=/opt/orbbec-agent-platform/private/agent-brain-release/agent-domain.nginx.conf
target=/etc/nginx/sites-available/agent-domain.conf
enabled=/etc/nginx/sites-enabled/agent-domain.conf
state=/opt/orbbec-agent-platform/private/agent-brain-release
root=/opt/orbbec-agent-platform
release="$(/usr/bin/readlink -f "$root/current")"
release_template="$release/deploy/cloud/agent-domain.nginx.conf"
manifest="$release/MANIFEST.sha256"
[[ "$release" =~ ^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$ ]] || fail
[[ -f "$source" && ! -L "$source" && -f "$target" && ! -L "$target" && -L "$enabled" ]] || fail
[[ -f "$release_template" && ! -L "$release_template" && -f "$manifest" && ! -L "$manifest" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$source")" == "600 root" ]] || fail
/usr/bin/cmp -s "$source" "$release_template" || fail
template_digest="$(/usr/bin/sha256sum "$release_template" | /usr/bin/awk '{print $1}')"
/usr/bin/grep -Fxq "$template_digest  deploy/cloud/agent-domain.nginx.conf" "$manifest" || fail
transaction_before="$state/agent-domain.transaction.before.conf"
/usr/bin/install -o root -g root -m 600 "$target" "$transaction_before"
enabled_before="$(/usr/bin/readlink "$enabled")"
[[ -n "$enabled_before" ]] || fail
published=0
restore_nginx() {
  status="$?"
  trap - ERR EXIT
  if [[ "$status" -ne 0 && "$published" == "1" ]]; then
    /usr/bin/install -o root -g root -m 644 "$transaction_before" "$target.part.restore"
    /bin/mv -f "$target.part.restore" "$target"
    /bin/ln -sfn "$enabled_before" "$enabled"
    /usr/sbin/nginx -t >/dev/null 2>&1 && /bin/systemctl reload nginx >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap restore_nginx ERR EXIT
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
/bin/ln -sfn "$target" "$enabled"
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
/bin/rm -f -- "$transaction_before"
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
        return blocks.some((block) => block.textContent.trim().length > 0 &&
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
  require_private_file "$hr_prompt_file" 32768
  require_private_file "$interruption_prompt_file" 32768
  evidence_parent="$(/usr/bin/dirname "$evidence_file")"
  [[ -d "$evidence_parent" && ! -L "$evidence_parent" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %u' "$evidence_parent")" == "700 $(/usr/bin/id -u)" ]] || fail
  [[ ! -L "$evidence_file" ]] || fail
  if [[ -e "$evidence_file" ]]; then require_private_file "$evidence_file" 65536; fi
  temporary="$(/usr/bin/mktemp -d)"
  chrome_pid=""
  worker_stopped=0
  worker_supervisor=/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/worker-pm2.sh
  restore_worker() {
    [[ "$worker_stopped" == "1" ]] || return 0
    run_agentops "$worker_supervisor" restore online >/dev/null || return 1
    for _attempt in $(/usr/bin/seq 1 12); do
      if /usr/bin/nc -z -w 2 127.0.0.1 9120 >/dev/null 2>&1; then worker_stopped=0; return 0; fi
      /bin/sleep 5
    done
    return 1
  }
  cleanup_accept_resources() {
    cleanup_status=0
    restore_worker || cleanup_status=1
    if [[ "$chrome_pid" =~ ^[0-9]+$ ]]; then
      /bin/kill "$chrome_pid" >/dev/null 2>&1 || true
      wait "$chrome_pid" >/dev/null 2>&1 || true
    fi
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
  base=https://agent.orbbec.com.cn
  curl_member=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/member.curl" --max-time 15)
  curl_owner=(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" --max-time 15)

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
    IFS=, read -r third_turn_id third_mission_id < <("$python" - "$temporary/restore-response.json" "$restored_conversation_id" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1],encoding="utf-8"))
if value["conversation"].get("conversation_id") != sys.argv[2]: raise SystemExit(1)
print(f'{uuid.UUID(value["turn"]["turn_id"])},{uuid.UUID(value["turn"]["mission_id"])}')
PY
    ) || fail
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
    "$python" - "$temporary/restore-messages.json" "$restored_conversation_id" "$third_turn_id" "$third_mission_id" <<'PY' || fail
import json,sys
items=json.load(open(sys.argv[1],encoding="utf-8")).get("items")
if not isinstance(items,list) or len(items)!=6: raise SystemExit(1)
if [item.get("role") for item in items] != ["user","assistant"]*3: raise SystemExit(1)
if any(item.get("conversation_id") != sys.argv[2] for item in items): raise SystemExit(1)
if items[-2].get("turn_id") != sys.argv[3] or items[-2].get("mission_id") != sys.argv[4]: raise SystemExit(1)
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
if "hr-bot" not in agents or "marketing-gtm-bot" in agents: raise SystemExit(1)
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
  IFS=, read -r conversation_id first_turn_id first_mission_id < <("$python" - "$temporary/conversation.json" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1],encoding="utf-8"))
conversation=uuid.UUID(value["conversation"]["conversation_id"])
turn=uuid.UUID(value["turn"]["turn_id"])
mission=uuid.UUID(value["turn"]["mission_id"])
if value["message"].get("mission_id") != str(mission): raise SystemExit(1)
print(f"{conversation},{turn},{mission}")
PY
  ) || fail
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
  IFS=, read -r second_turn_id second_mission_id < <("$python" - "$temporary/follow-up-response.json" "$conversation_id" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1],encoding="utf-8"))
if value["conversation"].get("conversation_id") != sys.argv[2]: raise SystemExit(1)
turn=uuid.UUID(value["turn"]["turn_id"]); mission=uuid.UUID(value["turn"]["mission_id"])
print(f"{turn},{mission}")
PY
  ) || fail
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
  "$python" - "$temporary/messages.json" "$conversation_id" "$first_turn_id" "$second_turn_id" "$first_mission_id" "$second_mission_id" <<'PY' || fail
import json,sys
items=json.load(open(sys.argv[1],encoding="utf-8")).get("items")
if not isinstance(items,list) or len(items)!=4: raise SystemExit(1)
if [item.get("role") for item in items] != ["user","assistant","user","assistant"]: raise SystemExit(1)
if any(item.get("conversation_id") != sys.argv[2] for item in items): raise SystemExit(1)
if [items[0].get("turn_id"),items[2].get("turn_id")] != [sys.argv[3],sys.argv[4]]: raise SystemExit(1)
if [items[0].get("mission_id"),items[2].get("mission_id")] != [sys.argv[5],sys.argv[6]]: raise SystemExit(1)
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
  run_agentops "$worker_supervisor" stop >/dev/null || fail
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
  metabot_release_sha="$(run_agentops /usr/bin/git -C /Users/agentops/AgentRuntime/metabot rev-parse HEAD)" || fail
  agent_team_release_sha="$(run_agentops /usr/bin/git -C /Users/agentops/Developer/work/Orbbec-Agent-Team rev-parse HEAD)" || fail
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
    /usr/bin/printf 'metabot_release_sha=%s\nagent_team_release_sha=%s\nlocal_listener_table=%s\nrollback=PLATFORM_AGENT_BRAIN_ENABLED=0\n' \
      "$metabot_release_sha" "$agent_team_release_sha" "$local_listener_table"
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

enable_with_rollback() {
  enable_failure_rollback() {
    status="$?"
    trap - ERR EXIT
    if [[ "$status" -ne 0 ]]; then remote_feature 0 || status=1; fi
    release_action_lock || status=1
    exit "$status"
  }
  trap enable_failure_rollback ERR EXIT
  local_runtime_preflight
  remote_feature 0
  run_relay_canary
  publish_formal_nginx
  remote_feature 1
  trap - ERR EXIT
  trap action_lock_exit EXIT
}

case "$action" in
  preflight)
    local_runtime_preflight
    remote '/usr/bin/test "$(/usr/bin/stat -c "%a %U" /opt/orbbec-agent-platform/private/platform.env)" = "600 root"; ! /usr/bin/ss -H -lnt | /usr/bin/awk '\''$4 !~ /^(127\.0\.0\.1|\[::1\]|127\.0\.0\.53%lo|127\.0\.0\.54):/ {print $4}'\'' | /usr/bin/grep -Eq '\''^(0\.0\.0\.0|\[::\]):(5432|8080|910[1-8]|9110|9120)$'\''' || fail
    echo "AGENT_BRAIN_PREFLIGHT_OK"
    ;;
  release)
    acquire_action_lock
    enable_with_rollback
    accept_real
    release_action_lock || fail
    trap - EXIT
    ;;
  accept)
    acquire_action_lock
    accept_real
    release_action_lock || fail
    trap - EXIT
    ;;
  rollback)
    acquire_action_lock
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
    [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" -D "$rollback_headers" -o /dev/null -w '%{http_code}' --max-time 15 https://agent.orbbec.com.cn/)" == "302" ]] || fail
    /usr/bin/tr -d '\r' < "$rollback_headers" | /usr/bin/grep -Fxiq 'location: /admin' || fail
    for owner_path in /admin /admin/sessions /admin/review /admin/activity; do
      [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" -o /dev/null -w '%{http_code}' --max-time 15 "https://agent.orbbec.com.cn$owner_path")" == "200" ]] || fail
    done
    for owner_api in '/api/sessions?limit=1' '/api/review/overview?agent_id=hr-bot' '/api/operations/brief' '/api/operations/conversation-metrics'; do
      [[ "$(/usr/bin/curl --noproxy '*' --silent --show-error --config "$temporary/owner.curl" -o "$temporary/owner-api.json" -w '%{http_code}' --max-time 15 "https://agent.orbbec.com.cn$owner_api")" == "200" ]] || fail
      "$python" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$temporary/owner-api.json" || fail
    done
    [[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 15 https://fae.orbbec.com.cn/)" == "200" ]] || fail
    [[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 15 http://47.106.112.69/)" == "200" ]] || fail
    for port in 9101 9102 9103 9104 9105 9107 9108 9110; do
      /usr/bin/nc -z -w 2 127.0.0.1 "$port" || fail
    done
    require_private_file "$evidence_file" 65536
    read -r preserved_conversation preserved_first preserved_second preserved_interrupted < <("$python" - "$evidence_file" <<'PY'
import pathlib,re,sys
values={}
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if "=" in line:
        key,value=line.split("=",1); values[key]=value
for key in ("conversation_id","first_mission_id","second_mission_id","interrupted_mission_id"):
    if not re.fullmatch(r"[0-9a-f-]{36}", values.get(key,"")): raise SystemExit(1)
print(values["conversation_id"], values["first_mission_id"], values["second_mission_id"], values["interrupted_mission_id"])
PY
    ) || fail
    preserved_shape="$(remote /bin/bash -s -- "$preserved_conversation" "$preserved_first" "$preserved_second" "$preserved_interrupted" <<'REMOTE'
set -euo pipefail
conversation="$1"; first="$2"; second="$3"; interrupted="$4"; root=/opt/orbbec-agent-platform; release="$(readlink -f "$root/current")"; env="$root/private/platform.env"; compose="$release/deploy/cloud/compose.yaml"; postgres="$(docker compose --env-file "$env" -f "$compose" ps -q platform-postgres)"
docker exec "$postgres" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v conversation="$conversation" -v first="$first" -v second="$second" -v interrupted="$interrupted" -c "select concat('turn_count=',(select count(*) from platform_control.conversation_turns where conversation_id=:'conversation'::uuid),',message_count=',(select count(*) from platform_control.conversation_messages where conversation_id=:'conversation'::uuid),',mission_count=',(select count(*) from platform_control.missions where mission_id in (:'first'::uuid,:'second'::uuid,:'interrupted'::uuid)));"
REMOTE
    )" || fail
    [[ "$preserved_shape" == "turn_count=2,message_count=4,mission_count=3" ]] || fail
    trap - EXIT
    /bin/rm -rf -- "$temporary"
    release_action_lock || fail
    echo "AGENT_BRAIN_ROLLBACK_OK"
    ;;
  restore)
    acquire_action_lock
    enable_with_rollback
    accept_real
    release_action_lock || fail
    trap - EXIT
    ;;
esac
