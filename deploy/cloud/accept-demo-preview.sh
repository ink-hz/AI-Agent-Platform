#!/bin/bash
set -euo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' 'FAIL acceptance'
  /usr/bin/printf '%s\n' 'DEMO_PREVIEW_ACCEPTANCE_FAIL'
  exit 1
}

[[ $# -eq 0 ]] || fail
[[ "${EUID:-$(/usr/bin/id -u)}" -eq 0 ]] || fail

platform_root=/opt/orbbec-agent-platform
private_path=/opt/orbbec-agent-platform/private/demo-preview
state_dir=/var/lib/orbbec-agent-demo-preview
baseline_dir="$state_dir/release-baseline"
platform_environment="$platform_root/private/platform.env"
base_compose="$platform_root/current/deploy/cloud/compose.yaml"
preview_compose="$platform_root/current/deploy/cloud/compose.demo-preview.yaml"
prefix=/_preview/dingtalk-r1/
public_base=https://agent.orbbec.com.cn/_preview/dingtalk-r1/
temporary_root="$(/usr/bin/mktemp -d /tmp/orbbec-demo-accept.XXXXXX)"
curl_common=(/usr/bin/curl --noproxy '*' -sS --max-time 10 --resolve \
  agent.orbbec.com.cn:443:127.0.0.1)

cleanup() {
  /bin/rm -rf -- "$temporary_root"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

for required in \
  containers.before listeners.before responses.before bootstrap-result \
  release-sha image-ref image-id; do
  [[ -f "$baseline_dir/$required" && ! -L "$baseline_dir/$required" ]] || fail
  [[ "$(/usr/bin/stat -c '%u:%a:%F' "$baseline_dir/$required")" == \
    "0:600:regular file" ]] || fail
done
[[ -f "$platform_environment" && ! -L "$platform_environment" ]] || fail
[[ -f "$base_compose" && ! -L "$base_compose" ]] || fail
[[ -f "$preview_compose" && ! -L "$preview_compose" ]] || fail

protected_container_invariants() {
  local name
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    case "$name" in
      *platform-api-demo-preview*|*platform-loopback-demo-preview*) continue ;;
    esac
    /usr/bin/docker inspect --format \
      '{{.Name}}|{{.Id}}|{{.Config.Image}}|{{.State.StartedAt}}|{{.RestartCount}}' \
      "$name"
  done < <(/usr/bin/docker ps --format '{{.Names}}' | /usr/bin/sort)
}

public_listener_invariants() {
  /usr/bin/ss -H -lnt | /usr/bin/awk \
    '$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}' | /usr/bin/sort -u
}

response_code() {
  local url="$1" resolve_value="${2:-}" command
  command=(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 8)
  [[ -z "$resolve_value" ]] || command+=(--resolve "$resolve_value")
  "${command[@]}" "$url" 2>/dev/null
}

capture_responses() {
  /usr/bin/printf 'agent_root=%s\n' \
    "$(response_code https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1)"
  /usr/bin/printf 'agent_admin=%s\n' \
    "$(response_code https://agent.orbbec.com.cn/admin/ agent.orbbec.com.cn:443:127.0.0.1)"
  /usr/bin/printf 'fae_domain=%s\n' \
    "$(response_code https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1)"
  /usr/bin/printf 'fae_ip=%s\n' "$(response_code http://47.106.112.69/)"
}

compose=(/usr/bin/docker compose --env-file "$platform_environment" \
  -f "$base_compose" -f "$preview_compose")
image_ref="$(< "$baseline_dir/image-ref")"
image_id="$(< "$baseline_dir/image-id")"
[[ "$image_ref" =~ ^orbbec-agent-platform-demo-preview:[0-9a-f]{40}$ ]] || fail
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || fail

PLATFORM_IMAGE="$image_ref" "${compose[@]}" config --format json \
  > "$temporary_root/compose.json" 2>/dev/null || fail
for service in platform-api-demo-preview platform-loopback-demo-preview; do
  container_id="$(PLATFORM_IMAGE="$image_ref" "${compose[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || fail
  [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$container_id")" == \
    healthy ]] || fail
  [[ "$(/usr/bin/docker inspect --format '{{.Config.Image}}' "$container_id")" == \
    "$image_ref" ]] || fail
  [[ "$(/usr/bin/docker inspect --format '{{.Image}}' "$container_id")" == \
    "$image_id" ]] || fail
done
/usr/bin/printf '%s\n' 'PASS preview_containers'

/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | \
  /usr/bin/grep -Fxq '127.0.0.1:8081' || fail
 # Public wildcard listeners 0.0.0.0:8081 and [::]:8081 are forbidden.
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | \
  /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):8081$' || fail
/usr/bin/printf '%s\n' 'PASS loopback_only'

protected_container_invariants > "$temporary_root/containers.after"
public_listener_invariants > "$temporary_root/listeners.after"
capture_responses > "$temporary_root/responses.after"
/usr/bin/cmp -s "$baseline_dir/containers.before" "$temporary_root/containers.after" || fail
/usr/bin/cmp -s "$baseline_dir/listeners.before" "$temporary_root/listeners.after" || fail
/usr/bin/cmp -s "$baseline_dir/responses.before" "$temporary_root/responses.after" || fail
/usr/bin/printf '%s\n' 'PASS existing_invariants'

root_code="$("${curl_common[@]}" -D "$temporary_root/root.headers" \
  -o /dev/null -w '%{http_code}' https://agent.orbbec.com.cn/ 2>/dev/null)" || fail
[[ "$root_code" == 401 ]] || fail
/usr/bin/grep -Eiq '^www-authenticate:[[:space:]]*Basic([[:space:]]|$)' \
  "$temporary_root/root.headers" || fail
[[ "$(response_code https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1)" == \
  200 ]] || fail
[[ "$(response_code http://47.106.112.69/)" == 200 ]] || fail
/usr/bin/printf '%s\n' 'PASS root_fae_admin'

health_code="$("${curl_common[@]}" -D "$temporary_root/health.headers" \
  -o "$temporary_root/health.body" -w '%{http_code}' \
  "${public_base}api/health" 2>/dev/null)" || fail
[[ "$health_code" == 200 ]] || fail
/usr/bin/grep -Eiq '^content-type:[[:space:]]*application/json([[:space:]]*;|[[:space:]]*$)' \
  "$temporary_root/health.headers" || fail
/usr/bin/python3 - "$temporary_root/health.body" <<'PY' || fail
import json
import pathlib
import sys

if json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")) != {"status": "ok"}:
    raise SystemExit(1)
PY
/usr/bin/printf '%s\n' 'PASS public_health'

entry_code="$("${curl_common[@]}" -D "$temporary_root/entry.headers" \
  -o /dev/null -w '%{http_code}' "$public_base" 2>/dev/null)" || fail
[[ "$entry_code" == 302 ]] || fail
/usr/bin/tr -d '\r' < "$temporary_root/entry.headers" | \
  /usr/bin/grep -Fxqi "location: ${prefix}login" || fail

login_code="$("${curl_common[@]}" -c "$temporary_root/cookies" \
  -D "$temporary_root/login.headers" -o "$temporary_root/login.body" \
  -w '%{http_code}' "${public_base}login" 2>/dev/null)" || fail
[[ "$login_code" == 200 ]] || fail
/usr/bin/python3 - "$temporary_root/login.headers" <<'PY' || fail
import pathlib
import sys

required = ("Secure", "HttpOnly", "SameSite=Lax", "Path=/_preview/dingtalk-r1/")
found = False
for raw in pathlib.Path(sys.argv[1]).read_text(encoding="iso-8859-1").splitlines():
    name, separator, value = raw.partition(":")
    if not separator or name.strip().lower() != "set-cookie":
        continue
    parts = [part.strip() for part in value.split(";")]
    if not parts or not parts[0].startswith("platform_preview_login_challenge="):
        continue
    lowered = {part.lower() for part in parts[1:]}
    if not {item.lower() for item in required}.issubset(lowered):
        raise SystemExit(1)
    found = True
if not found:
    raise SystemExit(1)
PY
/usr/bin/python3 - "$temporary_root/login.body" "$temporary_root/asset.path" <<'PY' || fail
import pathlib
import re
import sys

value = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
matches = re.findall(r'''(?:src|href)=["']\./(assets/[A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9_-]{8,64}\.(?:js|css|woff2?|png|jpe?g|webp))["']''', value)
if not matches:
    raise SystemExit(1)
pathlib.Path(sys.argv[2]).write_text(matches[0], encoding="ascii")
PY
asset_path="$(< "$temporary_root/asset.path")"
asset_code="$("${curl_common[@]}" -o /dev/null -w '%{http_code}' \
  "${public_base}${asset_path}" 2>/dev/null)" || fail
[[ "$asset_code" == 200 ]] || fail
/usr/bin/printf '%s\n' 'PASS login_assets_cookie'

start_code="$("${curl_common[@]}" -b "$temporary_root/cookies" \
  -H 'Origin: https://agent.orbbec.com.cn' -H 'Content-Type: application/json' \
  -X POST -D "$temporary_root/start.headers" -o "$temporary_root/start.body" \
  -w '%{http_code}' --data '{"return_path":"/_preview/dingtalk-r1/"}' \
  "${public_base}api/v1/auth/dingtalk/start" 2>/dev/null)" || fail
[[ "$start_code" == 200 ]] || fail
/usr/bin/python3 - "$temporary_root/start.body" "$temporary_root/oauth.state" <<'PY' || fail
import json
import pathlib
import sys
import urllib.parse

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(value, dict) or len(value) != 1:
    raise SystemExit(1)
target = next(iter(value.values()))
if not isinstance(target, str) or not target.startswith("https://"):
    raise SystemExit(1)
query = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query, strict_parsing=True)
state = query.get("state", [])
if len(state) != 1 or not state[0] or len(state[0]) > 1024:
    raise SystemExit(1)
pathlib.Path(sys.argv[2]).write_text(state[0], encoding="ascii")
PY
/usr/bin/printf '%s\n' 'PASS login_start'

account_code="$("${curl_common[@]}" -o "$temporary_root/account.body" \
  -w '%{http_code}' "${public_base}api/v1/account" 2>/dev/null)" || fail
[[ "$account_code" == 401 ]] || fail
/usr/bin/printf '%s\n' 'PASS provider_zero_call'

callback_url="${public_base}api/v1/auth/dingtalk/callback?state=invalid_state&code=invalid_code"
invalid_code="$("${curl_common[@]}" -o "$temporary_root/invalid.body" \
  -w '%{http_code}' "$callback_url" 2>/dev/null)" || fail
[[ "$invalid_code" == 401 ]] || fail
/usr/bin/printf '%s\n' 'PASS invalid_state'

replay_url="${public_base}api/v1/auth/dingtalk/callback"
provider_failure_code="$("${curl_common[@]}" --get \
  --data-urlencode "state@$temporary_root/oauth.state" \
  --data-urlencode 'code=invalid_code' -o "$temporary_root/provider-failure.body" \
  -w '%{http_code}' "$replay_url" 2>/dev/null)" || fail
replay_code="$("${curl_common[@]}" --get \
  --data-urlencode "state@$temporary_root/oauth.state" \
  --data-urlencode 'code=invalid_code' -o "$temporary_root/replay.body" \
  -w '%{http_code}' "$replay_url" 2>/dev/null)" || fail
[[ "$provider_failure_code" == 503 && "$replay_code" == 401 ]] || fail
/usr/bin/printf '%s\n' 'PASS replayed_state'

[[ -f "$private_path/demo-userids" && ! -L "$private_path/demo-userids" ]] || fail
[[ "$(/usr/bin/stat -c '%u:%a:%F' "$private_path/demo-userids")" == \
  "0:600:regular file" ]] || fail
/usr/bin/python3 - "$private_path/demo-userids" "$baseline_dir/bootstrap-result" <<'PY' || fail
import pathlib
import re
import sys

userids = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if not 1 <= len(userids) <= 3 or len(userids) != len(set(userids)):
    raise SystemExit(1)
match = re.fullmatch(
    r"DEMO_DIRECTORY_READY generation=[0-9a-f-]{36} members=([1-3])\n?",
    pathlib.Path(sys.argv[2]).read_text(encoding="ascii"),
)
if match is None or int(match.group(1)) != len(userids):
    raise SystemExit(1)
PY
/usr/bin/printf '%s\n' 'PASS unapproved_denial'

/usr/bin/printf '%s\n' 'DEMO_PREVIEW_ACCEPTANCE_PASS'
trap - HUP INT TERM
trap - EXIT
cleanup
