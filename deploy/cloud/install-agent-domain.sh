#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "AGENT_DOMAIN_INSTALL_FAILED" >&2
  exit 1
}

[[ "$(/usr/bin/id -u)" == "0" && $# -eq 3 ]] || fail
agent_domain="$1"
agent_user="$2"
template_path="$3"
[[ "$agent_domain" == "agent.orbbec.com.cn" ]] || fail
[[ "$agent_user" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] || fail
[[ "$template_path" == /* && -f "$template_path" && ! -L "$template_path" ]] || fail
[[ "$(/usr/bin/stat -c '%a' "$template_path")" == "600" ]] || fail
[[ "$(/usr/bin/stat -c '%U' "$template_path")" == "root" ]] || fail
for placeholder in __AGENT_DOMAIN__ __HTPASSWD_PATH__ __CERT_PATH__ __KEY_PATH__; do
  /usr/bin/grep -Fq "$placeholder" "$template_path" || fail
done

IFS= read -r agent_password || fail
if IFS= read -r _unexpected_input; then
  fail
fi
[[ "$agent_password" =~ ^[A-Za-z0-9_-]{32,128}$ ]] || fail

nginx_available=/etc/nginx/sites-available
nginx_enabled=/etc/nginx/sites-enabled
fae_http="$nginx_available/fae-domain-http.conf"
agent_deny_available="$nginx_available/agent-domain-deny.conf"
agent_deny_enabled="$nginx_enabled/agent-domain-deny.conf"
agent_available="$nginx_available/agent-domain.conf"
agent_enabled="$nginx_enabled/agent-domain.conf"
htpasswd_path=/etc/nginx/.htpasswd-agent-platform
webroot=/var/www/letsencrypt
platform_root=/opt/orbbec-agent-platform
platform_environment="$platform_root/private/platform.env"
platform_compose="$platform_root/current/deploy/cloud/compose.yaml"
timestamp="$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)"
backup_path="/root/nginx-backups/agent-platform-$timestamp"
rollback_script="/root/rollback-agent-domain-$timestamp.sh"
[[ ! -e "$backup_path" && ! -e "$rollback_script" ]] || fail

for required in "$fae_http" "$agent_deny_available" "$platform_environment" "$platform_compose"; do
  [[ -f "$required" && ! -L "$required" ]] || fail
done
/usr/bin/getent group www-data >/dev/null || fail
[[ "$(/usr/bin/stat -c '%a %U' "$platform_environment")" == "600 root" ]] || fail
/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fxq '127.0.0.1:8080' || fail
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):8080$' || fail

fae_container_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend 2>/dev/null || true)"
fae_image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' ai-fae-backend 2>/dev/null || true)"
fae_started_at="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend 2>/dev/null || true)"
fae_health="$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend 2>/dev/null || true)"
[[ -n "$fae_container_id" && -n "$fae_image" && -n "$fae_started_at" && "$fae_health" == "healthy" ]] || fail
fae_ip_digest="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
fae_domain_digest="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"

/usr/bin/install -d -o root -g root -m 700 \
  "$backup_path/sites-available" "$backup_path/sites-enabled" "$backup_path/platform"
backup_if_present() {
  local source="$1" destination="$2"
  if [[ -e "$source" || -L "$source" ]]; then
    /bin/cp -a "$source" "$destination"
  fi
}
backup_if_present "$fae_http" "$backup_path/sites-available/fae-domain-http.conf"
backup_if_present "$agent_deny_available" "$backup_path/sites-available/agent-domain-deny.conf"
backup_if_present "$agent_deny_enabled" "$backup_path/sites-enabled/agent-domain-deny.conf"
backup_if_present "$agent_available" "$backup_path/sites-available/agent-domain.conf"
backup_if_present "$agent_enabled" "$backup_path/sites-enabled/agent-domain.conf"
backup_if_present "$htpasswd_path" "$backup_path/htpasswd-agent-platform"
/bin/cp -a "$platform_environment" "$backup_path/platform/platform.env"
/usr/bin/printf 'FAE_CONTAINER_ID=%q\nFAE_IMAGE=%q\nFAE_STARTED_AT=%q\nFAE_IP_DIGEST=%q\nFAE_DOMAIN_DIGEST=%q\n' \
  "$fae_container_id" "$fae_image" "$fae_started_at" "$fae_ip_digest" "$fae_domain_digest" \
  > "$backup_path/invariants.env"
/bin/chown -R root:root "$backup_path"
/bin/chmod 600 "$backup_path/invariants.env" "$backup_path/platform/platform.env"

rollback_template="$backup_path/rollback.template"
/bin/cat > "$rollback_template" <<'ROLLBACK'
#!/bin/bash
set -euo pipefail
umask 077
backup_path=__BACKUP_PATH__
nginx_available=/etc/nginx/sites-available
nginx_enabled=/etc/nginx/sites-enabled
platform_root=/opt/orbbec-agent-platform
platform_environment="$platform_root/private/platform.env"
platform_compose="$platform_root/current/deploy/cloud/compose.yaml"

restore_path() {
  local saved="$1" target="$2"
  /bin/rm -f -- "$target"
  if [[ -e "$saved" || -L "$saved" ]]; then
    /bin/cp -a "$saved" "$target"
  fi
}

restore_path "$backup_path/sites-available/fae-domain-http.conf" "$nginx_available/fae-domain-http.conf"
restore_path "$backup_path/sites-available/agent-domain-deny.conf" "$nginx_available/agent-domain-deny.conf"
restore_path "$backup_path/sites-enabled/agent-domain-deny.conf" "$nginx_enabled/agent-domain-deny.conf"
restore_path "$backup_path/sites-available/agent-domain.conf" "$nginx_available/agent-domain.conf"
restore_path "$backup_path/sites-enabled/agent-domain.conf" "$nginx_enabled/agent-domain.conf"
restore_path "$backup_path/htpasswd-agent-platform" /etc/nginx/.htpasswd-agent-platform
/bin/cp -a "$backup_path/platform/platform.env" "$platform_environment"
/usr/sbin/nginx -t >/dev/null 2>&1
/bin/systemctl reload nginx
unset PLATFORM_CLOUD_AUTH_MODE
compose=(/usr/bin/docker compose --env-file "$platform_environment" -f "$platform_compose")
"${compose[@]}" up -d --no-deps --force-recreate platform-api >/dev/null
for _attempt in $(/usr/bin/seq 1 40); do
  api_id="$("${compose[@]}" ps -q platform-api)"
  [[ -n "$api_id" && "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$api_id" 2>/dev/null || true)" == "healthy" ]] && break
  /bin/sleep 1
done
[[ -n "${api_id:-}" && "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$api_id")" == "healthy" ]]
"${compose[@]}" up -d --no-deps --force-recreate platform-loopback >/dev/null
for _attempt in $(/usr/bin/seq 1 40); do
  /usr/bin/curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1 && break
  /bin/sleep 1
done
/usr/bin/curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null
set -a
source "$backup_path/invariants.env"
set +a
[[ "$FAE_CONTAINER_ID" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]]
[[ "$FAE_IMAGE" == "$(/usr/bin/docker inspect --format '{{.Config.Image}}' ai-fae-backend)" ]]
[[ "$FAE_STARTED_AT" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]]
[[ "$FAE_IP_DIGEST" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]]
[[ "$FAE_DOMAIN_DIGEST" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]]
/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fxq '127.0.0.1:8080'
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):8080$'
echo "AGENT_DOMAIN_ROLLBACK_OK"
ROLLBACK
/usr/bin/sed "s|__BACKUP_PATH__|$backup_path|g" "$rollback_template" > "$rollback_script"
/bin/chown root:root "$rollback_script"
/bin/chmod 700 "$rollback_script"
/bin/rm -f -- "$rollback_template"

rollback_required=1
rollback_on_failure() {
  /bin/rm -f -- "$backup_path/curl-auth.conf" "$backup_path/curl-wrong.conf"
  if [[ "$rollback_required" == "1" ]]; then
    "$rollback_script" >/dev/null 2>&1 || true
  fi
}
trap rollback_on_failure EXIT

public_entry_active=0
if [[ -L "$agent_enabled" && ! -e "$agent_deny_enabled" ]] && \
   /usr/bin/grep -Fq 'auth_basic "Orbbec Agent Platform";' "$agent_available"; then
  public_entry_active=1
fi

cert_path="/etc/letsencrypt/live/$agent_domain/fullchain.pem"
key_path="/etc/letsencrypt/live/$agent_domain/privkey.pem"
rendered_full="$backup_path/agent-domain.rendered.conf"
/usr/bin/sed \
  -e "s|__AGENT_DOMAIN__|$agent_domain|g" \
  -e "s|__HTPASSWD_PATH__|$htpasswd_path|g" \
  -e "s|__CERT_PATH__|$cert_path|g" \
  -e "s|__KEY_PATH__|$key_path|g" \
  "$template_path" > "$rendered_full"

if [[ "$public_entry_active" == "0" ]]; then
  [[ -L "$agent_deny_enabled" ]] || fail
  /usr/bin/python3 - "$fae_http" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
pattern = re.compile(
    r"\n?server\s*\{\s*listen\s+80;\s*"
    r"server_name\s+agent\.orbbec\.com\.cn;\s*"
    r"access_log\s+off;\s*return\s+404;\s*\}\s*",
    re.DOTALL,
)
updated, count = pattern.subn("\n", value)
if count != 1:
    raise SystemExit(1)
temporary = path.with_suffix(path.suffix + ".part")
temporary.write_text(updated.rstrip() + "\n", encoding="utf-8")
temporary.chmod(0o644)
temporary.replace(path)
PY
  bootstrap_config="$backup_path/agent-domain.bootstrap.conf"
  /usr/bin/awk '/^# HTTPS_ENTRY_BEGIN$/{exit} {print}' "$rendered_full" > "$bootstrap_config"
  /usr/bin/install -o root -g root -m 644 "$bootstrap_config" "$agent_available"
  /bin/ln -sfn "$agent_available" "$agent_enabled"
  /usr/sbin/nginx -t >/dev/null 2>&1 || fail
  /bin/systemctl reload nginx
fi

/usr/bin/install -d -o root -g root -m 755 "$webroot"
certificate_ready=0
if [[ -f "$cert_path" && -f "$key_path" ]] && \
   /usr/bin/openssl x509 -in "$cert_path" -noout -checkend 604800 >/dev/null 2>&1 && \
   /usr/bin/openssl x509 -in "$cert_path" -noout -checkhost "$agent_domain" >/dev/null 2>&1; then
  certificate_ready=1
fi
if [[ "$certificate_ready" == "0" ]]; then
  /usr/bin/certbot certonly --webroot -w "$webroot" -d "$agent_domain" \
    --non-interactive --agree-tos --register-unsafely-without-email >/dev/null || fail
fi
[[ -f "$cert_path" && -f "$key_path" ]] || fail
/usr/bin/openssl x509 -in "$cert_path" -noout -checkend 604800 >/dev/null 2>&1 || fail
/usr/bin/openssl x509 -in "$cert_path" -noout -checkhost "$agent_domain" >/dev/null 2>&1 || fail

password_hash="$(printf '%s\n' "$agent_password" | /usr/bin/openssl passwd -6 -stdin)"
[[ "$password_hash" == \$6\$* ]] || fail
printf '%s:%s\n' "$agent_user" "$password_hash" > "$htpasswd_path.part"
unset password_hash
/bin/chown root:www-data "$htpasswd_path.part"
/bin/chmod 640 "$htpasswd_path.part"
/bin/mv -f "$htpasswd_path.part" "$htpasswd_path"
[[ "$(/usr/bin/stat -c '%a %U %G' "$htpasswd_path")" == "640 root www-data" ]] || fail

environment_part="$platform_environment.part"
/usr/bin/awk -F= '$1 != "PLATFORM_CLOUD_AUTH_MODE" {print}' "$platform_environment" > "$environment_part"
/usr/bin/printf 'PLATFORM_CLOUD_AUTH_MODE=basic-auth\n' >> "$environment_part"
/bin/chown root:root "$environment_part"
/bin/chmod 600 "$environment_part"
/bin/mv -f "$environment_part" "$platform_environment"

unset PLATFORM_CLOUD_AUTH_MODE
compose=(/usr/bin/docker compose --env-file "$platform_environment" -f "$platform_compose")
"${compose[@]}" up -d --no-deps --force-recreate platform-api >/dev/null
for _attempt in $(/usr/bin/seq 1 40); do
  api_id="$("${compose[@]}" ps -q platform-api)"
  [[ -n "$api_id" && "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$api_id" 2>/dev/null || true)" == "healthy" ]] && break
  /bin/sleep 1
done
[[ -n "${api_id:-}" && "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$api_id")" == "healthy" ]] || fail
"${compose[@]}" up -d --no-deps --force-recreate platform-loopback >/dev/null
for _attempt in $(/usr/bin/seq 1 40); do
  /usr/bin/curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1 && break
  /bin/sleep 1
done
/usr/bin/curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/api/deployment | \
  /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); assert v["mode"]=="cloud-replica" and v["read_only"] is True and v["auth"]=="basic-auth"' || fail

/usr/bin/install -o root -g root -m 644 "$rendered_full" "$agent_available"
/bin/ln -sfn "$agent_available" "$agent_enabled"
/bin/rm -f -- "$agent_deny_enabled"
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl reload nginx

curl_auth_config="$backup_path/curl-auth.conf"
curl_wrong_config="$backup_path/curl-wrong.conf"
printf 'user = "%s:%s"\n' "$agent_user" "$agent_password" > "$curl_auth_config"
printf 'user = "%s:%s"\n' "$agent_user" 'incorrect-credential-for-acceptance' > "$curl_wrong_config"
/bin/chmod 600 "$curl_auth_config" "$curl_wrong_config"
unset agent_password

[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 8 --resolve "$agent_domain:443:127.0.0.1" "https://$agent_domain/")" == "401" ]] || fail
[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 8 --resolve "$agent_domain:443:127.0.0.1" --config "$curl_wrong_config" "https://$agent_domain/")" == "401" ]] || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve "$agent_domain:443:127.0.0.1" --config "$curl_auth_config" "https://$agent_domain/" >/dev/null || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve "$agent_domain:443:127.0.0.1" --config "$curl_auth_config" "https://$agent_domain/api/health" | \
  /usr/bin/python3 -c 'import json,sys; assert json.load(sys.stdin)=={"status":"ok"}' || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve "$agent_domain:443:127.0.0.1" --config "$curl_auth_config" "https://$agent_domain/api/deployment" | \
  /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); assert v["mode"]=="cloud-replica" and v["read_only"] is True and v["auth"]=="basic-auth"' || fail
/bin/rm -f -- "$curl_auth_config" "$curl_wrong_config"

[[ "$fae_container_id" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$fae_image" == "$(/usr/bin/docker inspect --format '{{.Config.Image}}' ai-fae-backend)" ]] || fail
[[ "$fae_started_at" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
[[ "$fae_health" == "$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend)" ]] || fail
[[ "$fae_ip_digest" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 http://47.106.112.69/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$fae_domain_digest" == "$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fxq '127.0.0.1:8080' || fail
! /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq '^(0\.0\.0\.0|\[::\]):8080$' || fail
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl is-active --quiet certbot.timer || fail
/bin/systemctl is-enabled --quiet certbot.timer || fail
[[ -f "/etc/letsencrypt/renewal/$agent_domain.conf" ]] || fail
[[ -x "$rollback_script" && "$(/usr/bin/stat -c '%a %U' "$rollback_script")" == "700 root" ]] || fail

rollback_required=0
trap - EXIT
echo "AGENT_DOMAIN_INSTALL_OK domain=$agent_domain"
