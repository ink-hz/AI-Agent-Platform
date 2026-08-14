#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "AGENT_DOMAIN_PUBLISH_FAILED" >&2
  exit 1
}

mode_600_file() {
  local path="$1" mode owner
  [[ "$path" == /* && -f "$path" && ! -L "$path" ]] || return 1
  mode="$(/usr/bin/stat -f '%Lp' "$path" 2>/dev/null || /usr/bin/stat -c '%a' "$path" 2>/dev/null || true)"
  owner="$(/usr/bin/stat -f '%u' "$path" 2>/dev/null || /usr/bin/stat -c '%u' "$path" 2>/dev/null || true)"
  [[ "$mode" == "600" && "$owner" == "$(/usr/bin/id -u)" ]]
}

[[ $# -eq 1 ]] || fail
config_path="$1"
mode_600_file "$config_path" || fail
set -a
# shellcheck disable=SC1090
source "$config_path"
set +a
for required_name in \
  CLOUD_ADMIN_HOST CLOUD_ADMIN_KEY AGENT_DOMAIN AGENT_PUBLIC_IP \
  AGENT_BASIC_AUTH_USER AGENT_BASIC_AUTH_PASSWORD_FILE; do
  [[ -n "${!required_name:-}" ]] || fail
done
mode_600_file "$CLOUD_ADMIN_KEY" || fail
mode_600_file "$AGENT_BASIC_AUTH_PASSWORD_FILE" || fail
[[ "$CLOUD_ADMIN_HOST" == "root@47.106.112.69" ]] || fail
[[ "$AGENT_DOMAIN" == "agent.orbbec.com.cn" ]] || fail
[[ "$AGENT_PUBLIC_IP" == "47.106.112.69" ]] || fail
[[ "$AGENT_BASIC_AUTH_USER" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] || fail
IFS= read -r agent_password < "$AGENT_BASIC_AUTH_PASSWORD_FILE" || fail
if IFS= read -r _unexpected_line < <(/usr/bin/tail -n +2 "$AGENT_BASIC_AUTH_PASSWORD_FILE"); then
  fail
fi
[[ "$agent_password" =~ ^[A-Za-z0-9_-]{32,128}$ ]] || fail

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
installer="$repository_root/deploy/cloud/install-agent-domain.sh"
template="$repository_root/deploy/cloud/agent-domain.basic-auth.nginx.conf"
[[ -f "$installer" && ! -L "$installer" && -f "$template" && ! -L "$template" ]] || fail

ssh_options=(
  -i "$CLOUD_ADMIN_KEY"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)
remote_bin=/opt/orbbec-agent-platform/bin
if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  "umask 077; /usr/bin/install -d -o root -g root -m 700 $remote_bin; /bin/cat > $remote_bin/install-agent-domain.sh.part; /bin/chown root:root $remote_bin/install-agent-domain.sh.part; /bin/chmod 700 $remote_bin/install-agent-domain.sh.part; /bin/mv -f $remote_bin/install-agent-domain.sh.part $remote_bin/install-agent-domain.sh" \
  < "$installer"; then
  fail
fi
if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  "umask 077; /bin/cat > $remote_bin/agent-domain.nginx.conf.part; /bin/chown root:root $remote_bin/agent-domain.nginx.conf.part; /bin/chmod 600 $remote_bin/agent-domain.nginx.conf.part; /bin/mv -f $remote_bin/agent-domain.nginx.conf.part $remote_bin/agent-domain.nginx.conf" \
  < "$template"; then
  fail
fi
remote_result="$(/usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  "$remote_bin/install-agent-domain.sh" "$AGENT_DOMAIN" "$AGENT_BASIC_AUTH_USER" "$remote_bin/agent-domain.nginx.conf" \
  < "$AGENT_BASIC_AUTH_PASSWORD_FILE")" || fail
[[ "$remote_result" == "AGENT_DOMAIN_INSTALL_OK domain=$AGENT_DOMAIN" ]] || fail

http_headers="$(/usr/bin/mktemp)"
curl_auth_config="$(/usr/bin/mktemp)"
cleanup() {
  /bin/rm -f -- "$http_headers" "$curl_auth_config"
}
trap cleanup EXIT
/bin/chmod 600 "$http_headers" "$curl_auth_config"
printf 'user = "%s:%s"\n' "$AGENT_BASIC_AUTH_USER" "$agent_password" > "$curl_auth_config"
unset agent_password

default_dns="$(/usr/bin/dig +time=3 +tries=1 +short "$AGENT_DOMAIN" A)" || fail
alidns="$(/usr/bin/dig @223.5.5.5 +time=3 +tries=1 +short "$AGENT_DOMAIN" A)" || fail
/usr/bin/grep -Fxq "$AGENT_PUBLIC_IP" <<< "$default_dns" || fail
/usr/bin/grep -Fxq "$AGENT_PUBLIC_IP" <<< "$alidns" || fail

/usr/bin/curl --noproxy '*' -sS -D "$http_headers" -o /dev/null --max-time 12 \
  --resolve "$AGENT_DOMAIN:80:$AGENT_PUBLIC_IP" \
  "http://$AGENT_DOMAIN/acceptance?source=platform" || fail
/usr/bin/grep -Eq '^HTTP/[0-9.]+ 308 ' "$http_headers" || fail
/usr/bin/grep -Fq "Location: https://$AGENT_DOMAIN/acceptance?source=platform" "$http_headers" || fail
[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 12 --resolve "$AGENT_DOMAIN:443:$AGENT_PUBLIC_IP" "https://$AGENT_DOMAIN/")" == "401" ]] || fail

/usr/bin/curl --noproxy '*' -fsS --max-time 12 --resolve "$AGENT_DOMAIN:443:$AGENT_PUBLIC_IP" --config "$curl_auth_config" "https://$AGENT_DOMAIN/" >/dev/null || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 12 --resolve "$AGENT_DOMAIN:443:$AGENT_PUBLIC_IP" --config "$curl_auth_config" "https://$AGENT_DOMAIN/api/health" | \
  /usr/bin/python3 -c 'import json,sys; assert json.load(sys.stdin)=={"status":"ok"}' || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 12 --resolve "$AGENT_DOMAIN:443:$AGENT_PUBLIC_IP" --config "$curl_auth_config" "https://$AGENT_DOMAIN/api/deployment" | \
  /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); assert v["mode"]=="cloud-replica" and v["read_only"] is True and v["auth"]=="basic-auth"' || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 12 --resolve "$AGENT_DOMAIN:443:$AGENT_PUBLIC_IP" --config "$curl_auth_config" "https://$AGENT_DOMAIN/api/agents" | \
  /usr/bin/python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)' || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 12 --resolve "$AGENT_DOMAIN:443:$AGENT_PUBLIC_IP" --config "$curl_auth_config" "https://$AGENT_DOMAIN/api/sessions" | \
  /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); assert isinstance(v.get("items"), list)' || fail

echo "AGENT_DOMAIN_PUBLISH_OK domain=$AGENT_DOMAIN"
