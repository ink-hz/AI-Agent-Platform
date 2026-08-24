#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "AI_ADMIN_OFFICE_PATH_MIGRATION_FAILED" >&2
  exit 1
}

[[ "$-" != *x* && "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && "$#" == "0" ]] || fail

ai_admin_release_sha="${AI_ADMIN_RELEASE_SHA:-}"
platform_release_sha="${PLATFORM_RELEASE_SHA:-}"
identity_smoke_mode="${OFFICE_MIGRATION_IDENTITY_SMOKE_MODE:-cookie}"
[[ "$ai_admin_release_sha" =~ ^[0-9a-f]{40}$ \
  && "$platform_release_sha" =~ ^[0-9a-f]{40}$ \
  && ( "$identity_smoke_mode" == "cookie" \
    || "$identity_smoke_mode" == "deferred_browser" ) ]] || fail

platform_root=/opt/orbbec-agent-platform
private_root="$platform_root/private"
ai_admin_root=/opt/ai-admin-agent
action_lock="$private_root/agent-brain-action.lock"
deploy_transaction_lock="$private_root/deploy-input.transaction.lock"
deploy_input_lock="$private_root/deploy-input.lock"
session_cookie_file="$private_root/office-migration-session-cookie"
platform_release="$platform_root/releases/$platform_release_sha"
transaction_override="${OFFICE_MIGRATION_TRANSACTION:-}"
transaction="$platform_release/deploy/cloud/office_path_nginx_transaction.py"
rollback_template_override="${OFFICE_MIGRATION_ROLLBACK_TEMPLATE:-}"
rollback_template="$platform_release/deploy/cloud/rollback-office-path-migration.sh"
if [[ -n "$transaction_override" ]]; then
  [[ "$transaction_override" == /root/office-migration-tools/*/office_path_nginx_transaction.py \
    && "$transaction_override" == "$(/usr/bin/readlink -f "$transaction_override")" \
    && -f "$transaction_override" && ! -L "$transaction_override" \
    && "$(/usr/bin/stat -c '%a %U' "$transaction_override")" == "600 root" ]] || fail
  transaction="$transaction_override"
fi
if [[ -n "$rollback_template_override" ]]; then
  [[ "$rollback_template_override" == /root/office-migration-tools/*/rollback-office-path-migration.sh \
    && "$rollback_template_override" == "$(/usr/bin/readlink -f "$rollback_template_override")" \
    && -f "$rollback_template_override" && ! -L "$rollback_template_override" \
    && "$(/usr/bin/stat -c '%a %U' "$rollback_template_override")" == "600 root" ]] || fail
  rollback_template="$rollback_template_override"
fi

[[ ! -e "$deploy_input_lock" && ! -e "$action_lock" \
  && -f "$deploy_transaction_lock" && ! -L "$deploy_transaction_lock" \
  && "$(/usr/bin/stat -c '%a %U' "$deploy_transaction_lock")" == "600 root" ]] || fail
lock_token="$(/usr/bin/python3 -c 'import uuid; print(uuid.uuid4())')"
[[ "$lock_token" =~ ^[0-9a-f-]{36}$ ]] || fail
/bin/mkdir -m 700 "$action_lock" || fail
/usr/bin/printf '%s\n' "$lock_token" > "$action_lock/owner"
/bin/chmod 600 "$action_lock/owner"

release_action_lock() {
  status=$?
  trap - EXIT
  cleanup_action_lock
  exit "$status"
}

cleanup_action_lock() {
  if [[ -d "$action_lock" && ! -L "$action_lock" \
    && "$(/bin/cat "$action_lock/owner" 2>/dev/null || true)" == "$lock_token" ]]; then
    /bin/rm -f -- "$action_lock/owner"
    /bin/rmdir "$action_lock"
  fi
}
trap release_action_lock EXIT

exec 9<>"$deploy_transaction_lock" || fail
/usr/bin/flock --exclusive --nonblock 9 || fail
[[ ! -e "$deploy_input_lock" ]] || fail

[[ "$(/usr/bin/readlink -f "$platform_root/current")" == "$platform_release" \
  && "$(/usr/bin/tr -d '\n' < "$ai_admin_root/RELEASE_COMMIT")" == "$ai_admin_release_sha" \
  && -f "$transaction" && ! -L "$transaction" \
  && -f "$rollback_template" && ! -L "$rollback_template" ]] || fail
if [[ "$identity_smoke_mode" == "cookie" ]]; then
  [[ -f "$session_cookie_file" && ! -L "$session_cookie_file" \
    && "$(/usr/bin/stat -c '%a %U' "$session_cookie_file")" == "600 root" ]] || fail
fi
/bin/systemctl is-active --quiet nginx || fail

/usr/bin/curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8011/health \
  | /usr/bin/python3 -c '
import json,sys
payload=json.load(sys.stdin)
runtime=payload.get("runtime")
raise SystemExit(0 if payload.get("status")=="ok" and isinstance(runtime,dict) and runtime.get("git_sha")==sys.argv[1] else 1)
' "$ai_admin_release_sha" || fail
[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 5 \
  http://127.0.0.1:8011/office/health)" == "404" ]] || fail
[[ "$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 5 \
  'http://127.0.0.1:8011/office/?view=services')" == "200" ]] || fail
/usr/bin/curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8080/api/health \
  | /usr/bin/python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin)=={"status":"ok"} else 1)' || fail

identity_smoke="$ai_admin_root/scripts/smoke_platform_identity.py"
[[ -f "$identity_smoke" && ! -L "$identity_smoke" ]] || fail
if [[ "$identity_smoke_mode" == "cookie" ]]; then
  "$ai_admin_root/.venv/bin/python" "$identity_smoke" \
    --ai-admin-base-url http://127.0.0.1:8011/office/ \
    --phase before_revoke --cookie-file "$session_cookie_file" --timeout-seconds 12 \
    >/dev/null || fail
fi

fingerprint_fae() {
  local fae_id fae_image_id fae_started_at fae_restart_count
  local fae_config_hash fae_mounts_hash fae_domain_http fae_ip_http
  fae_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" || return 1
  fae_image_id="$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend)" || return 1
  fae_started_at="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" || return 1
  fae_restart_count="$(/usr/bin/docker inspect --format '{{.RestartCount}}' ai-fae-backend)" || return 1
  fae_config_hash="$(/usr/bin/docker inspect --format '{{json .Config}}' ai-fae-backend \
    | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" || return 1
  fae_mounts_hash="$(/usr/bin/docker inspect --format '{{json .Mounts}}' ai-fae-backend \
    | /usr/bin/python3 -c 'import hashlib,json,sys; value=json.load(sys.stdin); value=sorted(value,key=lambda item:(item.get("Destination",""),item.get("Source",""),item.get("Type",""))); raw=json.dumps(value,sort_keys=True,separators=(",",":")).encode(); print(hashlib.sha256(raw).hexdigest())')" || return 1
  fae_domain_http="$(/usr/bin/curl --noproxy '*' --silent --show-error --fail \
    --max-time 10 https://fae.orbbec.com.cn/ -o /dev/null -w '%{http_code}')" || return 1
  fae_ip_http="$(/usr/bin/curl --noproxy '*' --silent --show-error --fail \
    --max-time 10 http://47.106.112.69/ -o /dev/null -w '%{http_code} %{redirect_url}')" || return 1
  [[ -n "$fae_id" && -n "$fae_image_id" && -n "$fae_started_at" \
    && "$fae_restart_count" =~ ^[0-9]+$ \
    && "$fae_config_hash" =~ ^[0-9a-f]{64}$ \
    && "$fae_mounts_hash" =~ ^[0-9a-f]{64}$ \
    && "$fae_domain_http" =~ ^2[0-9]{2}$ \
    && "$fae_ip_http" =~ ^2[0-9]{2}[[:space:]]$ ]] || return 1
  fae_ip_http="${fae_ip_http% }"
  /usr/bin/printf '%s\n' \
    "container_id=$fae_id" \
    "image_id=$fae_image_id" \
    "started_at=$fae_started_at" \
    "restart_count=$fae_restart_count" \
    "config_sha256=$fae_config_hash" \
    "mounts_sha256=$fae_mounts_hash" \
    "fae_domain_http=$fae_domain_http" \
    "fae_ip_http=$fae_ip_http"
}

wait_for_http_code() {
  local url="$1" allowed="$2" status attempt
  for ((attempt = 0; attempt < 20; attempt++)); do
    status="$(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' \
      --max-time 5 --resolve agent.orbbec.com.cn:443:127.0.0.1 "$url" || true)"
    if [[ " $allowed " == *" $status "* ]]; then
      return 0
    fi
    /bin/sleep 0.25
  done
  return 1
}

change_id="$(/usr/bin/python3 -c 'import uuid; print(uuid.uuid4().hex)')"
[[ "$change_id" =~ ^[0-9a-f]{32}$ ]] || fail
timestamp="$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)"
evidence_dir="/root/nginx-backups/ai-admin-office-$timestamp-$change_id"
migration_dir="$private_root/office-path-migrations/$change_id"
/usr/bin/install -d -o root -g root -m 700 "$evidence_dir"
/usr/bin/install -d -o root -g root -m 700 "$migration_dir"

baseline="$evidence_dir/fae-baseline"
after="$evidence_dir/fae-after"
nginx_dump="$evidence_dir/nginx-before.txt"
backup="$evidence_dir/agent-domain.conf"
candidate="$evidence_dir/agent-domain.office.conf"
report="$evidence_dir/report"
rollback_rendered="$evidence_dir/rollback-office-path-migration.sh"
rollback_installed="$migration_dir/rollback-office-path-migration.sh"
fingerprint_fae > "$baseline" || fail

/usr/sbin/nginx -T > "$nginx_dump" 2>&1 || fail
nginx_source="$(/usr/bin/python3 - "$nginx_dump" <<'PY'
from pathlib import Path
import os
import re
import stat
import sys

value = Path(sys.argv[1]).read_text(encoding="utf-8")
headers = list(re.finditer(r"(?m)^# configuration file ([^:\n]+):\s*$", value))
matches = []
for index, header in enumerate(headers):
    end = headers[index + 1].start() if index + 1 < len(headers) else len(value)
    body = value[header.end():end]
    if re.search(r"(?m)^\s*server_name\s+agent\.orbbec\.com\.cn;\s*$", body) and re.search(
        r"(?m)^\s*listen\s+443\s+ssl;\s*$", body
    ):
        matches.append(header.group(1))
if len(matches) != 1:
    raise SystemExit(1)
resolved = os.path.realpath(matches[0])
descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
try:
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise SystemExit(1)
finally:
    os.close(descriptor)
print(resolved)
PY
)" || fail
[[ "$nginx_source" == /etc/nginx/sites-available/agent-domain.conf \
  || "$nginx_source" == /etc/nginx/sites-enabled/agent-domain.conf ]] || fail

/usr/bin/install -o root -g root -m 600 "$nginx_source" "$backup"
/usr/bin/python3 "$transaction" "$nginx_source" "$candidate" || fail
[[ -s "$candidate" && "$candidate" != "$nginx_source" ]] || fail
/bin/chmod 600 "$backup" "$candidate" "$baseline" "$nginx_dump"
backup_sha256="$(/usr/bin/sha256sum "$backup" | /usr/bin/awk '{print $1}')"
candidate_sha256="$(/usr/bin/sha256sum "$candidate" | /usr/bin/awk '{print $1}')"
baseline_sha256="$(/usr/bin/sha256sum "$baseline" | /usr/bin/awk '{print $1}')"
[[ "$backup_sha256" =~ ^[0-9a-f]{64}$ && "$candidate_sha256" =~ ^[0-9a-f]{64}$ \
  && "$baseline_sha256" =~ ^[0-9a-f]{64}$ \
  && "$backup_sha256" != "$candidate_sha256" ]] || fail

/usr/bin/python3 - "$rollback_template" "$rollback_rendered" \
  "$change_id" "$backup" "$backup_sha256" "$candidate_sha256" "$nginx_source" \
  "$baseline" "$baseline_sha256" \
  "$ai_admin_release_sha" "$platform_release_sha" <<'PY' || fail
from pathlib import Path
import sys

source, target, change_id, backup, backup_hash, candidate_hash, nginx_source, baseline, baseline_hash, ai_sha, platform_sha = sys.argv[1:]
value = Path(source).read_text(encoding="utf-8")
replacements = {
    "__CHANGE_ID__": change_id,
    "__BACKUP_PATH__": backup,
    "__BACKUP_SHA256__": backup_hash,
    "__CANDIDATE_SHA256__": candidate_hash,
    "__NGINX_SOURCE__": nginx_source,
    "__BASELINE_PATH__": baseline,
    "__BASELINE_SHA256__": baseline_hash,
    "__AI_ADMIN_RELEASE_SHA__": ai_sha,
    "__PLATFORM_RELEASE_SHA__": platform_sha,
}
for marker, replacement in replacements.items():
    if value.count(marker) != 1 or not replacement or any(character.isspace() for character in replacement):
        raise SystemExit(1)
    value = value.replace(marker, replacement)
if "__" in value:
    raise SystemExit(1)
Path(target).write_text(value, encoding="utf-8")
PY
/bin/chmod 700 "$rollback_rendered"
/usr/bin/install -o root -g root -m 700 "$rollback_rendered" "$rollback_installed"

[[ "$(/usr/bin/sha256sum "$nginx_source" | /usr/bin/awk '{print $1}')" == "$backup_sha256" ]] || fail
rollback_required=1
nginx_source_part="$nginx_source.office-migration.part"
rollback_on_failure() {
  status=$?
  trap - EXIT
  if [[ "$rollback_required" == "1" ]]; then
    /usr/bin/install -o root -g root -m 644 "$backup" "$nginx_source_part" 2>/dev/null || true
    /bin/mv -f "$nginx_source_part" "$nginx_source" 2>/dev/null || true
    /usr/sbin/nginx -t >/dev/null 2>&1 \
      && /bin/systemctl reload nginx >/dev/null 2>&1 || true
  fi
  cleanup_action_lock
  exit "$status"
}
trap rollback_on_failure EXIT

/usr/bin/install -o root -g root -m 644 "$candidate" "$nginx_source_part"
/bin/mv -f "$nginx_source_part" "$nginx_source"
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl reload nginx

wait_for_http_code https://agent.orbbec.com.cn/ "302" || fail
wait_for_http_code https://agent.orbbec.com.cn/admin/ "302 401" || fail
wait_for_http_code https://agent.orbbec.com.cn/office/health "404" || fail
wait_for_http_code 'https://agent.orbbec.com.cn/office/?view=services' "200" || fail
if [[ "$identity_smoke_mode" == "cookie" ]]; then
  "$ai_admin_root/.venv/bin/python" "$identity_smoke" \
    --ai-admin-base-url https://agent.orbbec.com.cn/office/ \
    --phase before_revoke --cookie-file "$session_cookie_file" --timeout-seconds 12 \
    > "$evidence_dir/identity-smoke.json" || fail
else
  /usr/bin/printf '%s\n' '{"phase":"deferred_browser","status":"pending"}' \
    > "$evidence_dir/identity-smoke.json"
fi

fingerprint_fae > "$after" || fail
/bin/chmod 600 "$after" "$evidence_dir/identity-smoke.json"
/usr/bin/cmp -s "$baseline" "$after" || fail
/usr/bin/printf '%s\n' \
  "status=accepted" \
  "change_id=$change_id" \
  "ai_admin_release_sha=$ai_admin_release_sha" \
  "platform_release_sha=$platform_release_sha" \
  "nginx_source=$nginx_source" \
  "backup_sha256=$backup_sha256" \
  "candidate_sha256=$candidate_sha256" \
  "fae_baseline_sha256=$baseline_sha256" \
  "rollback_script=$rollback_installed" \
  "authenticated_identity_smoke=$identity_smoke_mode" \
  "platform_admin_route_restored=true" \
  "fae_managed_files_unchanged=true" \
  "legacy_admin_route_conflict_restored=false" > "$report"
/bin/chmod 600 "$backup" "$candidate" "$baseline" "$report"

rollback_required=0
trap release_action_lock EXIT
if [[ "$identity_smoke_mode" == "cookie" ]]; then
  echo "AI_ADMIN_OFFICE_PATH_MIGRATION_OK change_id=$change_id evidence=$evidence_dir rollback=$rollback_installed"
else
  echo "AI_ADMIN_OFFICE_PATH_MIGRATION_PENDING_IDENTITY change_id=$change_id evidence=$evidence_dir rollback=$rollback_installed"
fi
