#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "AI_ADMIN_OFFICE_PATH_ROLLBACK_FAILED" >&2
  exit 1
}

[[ "$#" == "0" ]] || fail
[[ "$-" != *x* && "$(${ID_BIN:-/usr/bin/id} -u)" == "0" ]] || fail

change_id="__CHANGE_ID__"
backup="__BACKUP_PATH__"
backup_sha256="__BACKUP_SHA256__"
candidate_sha256="__CANDIDATE_SHA256__"
nginx_source="__NGINX_SOURCE__"
baseline="__BASELINE_PATH__"
baseline_sha256="__BASELINE_SHA256__"
ai_admin_release_sha="__AI_ADMIN_RELEASE_SHA__"
platform_release_sha="__PLATFORM_RELEASE_SHA__"
private_root=/opt/orbbec-agent-platform/private
migration_dir="$private_root/office-path-migrations/$change_id"
action_lock="$private_root/agent-brain-action.lock"
deploy_transaction_lock="$private_root/deploy-input.transaction.lock"
deploy_input_lock="$private_root/deploy-input.lock"
report="$migration_dir/rollback-report"

script_dir="$(/usr/bin/dirname "$(/usr/bin/readlink -f "$0")")"
[[ "$change_id" =~ ^[0-9a-f]{32}$ \
  && "$ai_admin_release_sha" =~ ^[0-9a-f]{40}$ \
  && "$platform_release_sha" =~ ^[0-9a-f]{40}$ \
  && "$migration_dir" == "$script_dir" \
  && "$backup" == /root/nginx-backups/ai-admin-office-*-$change_id/agent-domain.conf \
  && "$baseline" == /root/nginx-backups/ai-admin-office-*-$change_id/fae-baseline \
  && ( "$nginx_source" == /etc/nginx/sites-available/agent-domain.conf \
    || "$nginx_source" == /etc/nginx/sites-enabled/agent-domain.conf ) \
  && ! -e "$deploy_input_lock" && ! -e "$action_lock" \
  && -f "$deploy_transaction_lock" && ! -L "$deploy_transaction_lock" \
  && "$(/usr/bin/stat -c '%a %U' "$deploy_transaction_lock")" == "600 root" ]] || fail

/usr/bin/python3 - "$backup" "$baseline" "$nginx_source" <<'PY' || fail
import os
import stat
import sys

for index, raw in enumerate(sys.argv[1:]):
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(raw, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            raise SystemExit(1)
        if index < 2 and metadata.st_mode & 0o077:
            raise SystemExit(1)
    finally:
        os.close(descriptor)
PY

[[ "$(/usr/bin/sha256sum "$backup" | /usr/bin/awk '{print $1}')" == "$backup_sha256" \
  && "$(/usr/bin/sha256sum "$baseline" | /usr/bin/awk '{print $1}')" == "$baseline_sha256" \
  && "$(/usr/bin/sha256sum "$nginx_source" | /usr/bin/awk '{print $1}')" == "$candidate_sha256" ]] || fail

lock_token="$(/usr/bin/python3 -c 'import uuid; print(uuid.uuid4())')"
[[ "$lock_token" =~ ^[0-9a-f-]{36}$ ]] || fail
/bin/mkdir -m 700 "$action_lock" || fail
/usr/bin/printf '%s\n' "$lock_token" > "$action_lock/owner"
/bin/chmod 600 "$action_lock/owner"

release_action_lock() {
  status=$?
  trap - EXIT
  if [[ "${restore_current_required:-0}" == "1" ]]; then
    /usr/bin/install -o root -g root -m 644 "$current_before" "$nginx_source_part" 2>/dev/null || true
    /bin/mv -f "$nginx_source_part" "$nginx_source" 2>/dev/null || true
    /usr/sbin/nginx -t >/dev/null 2>&1 \
      && /bin/systemctl reload nginx >/dev/null 2>&1 || true
  fi
  if [[ -d "$action_lock" && ! -L "$action_lock" \
    && "$(/bin/cat "$action_lock/owner" 2>/dev/null || true)" == "$lock_token" ]]; then
    /bin/rm -f -- "$action_lock/owner"
    /bin/rmdir "$action_lock"
  fi
  exit "$status"
}
trap release_action_lock EXIT

exec 9<>"$deploy_transaction_lock" || fail
/usr/bin/flock --exclusive --nonblock 9 || fail
[[ ! -e "$deploy_input_lock" ]] || fail

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

fae_before="$migration_dir/.fae-before"
fae_after="$migration_dir/.fae-after"
current_before="$migration_dir/.agent-domain-before-rollback.conf"
fingerprint_fae > "$fae_before" || fail
/bin/chmod 600 "$fae_before"
/usr/bin/cmp -s "$baseline" "$fae_before" || fail

nginx_source_part="$nginx_source.office-rollback.part"
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/usr/bin/install -o root -g root -m 600 "$nginx_source" "$current_before"
restore_current_required=1
/usr/bin/install -o root -g root -m 644 "$backup" "$nginx_source_part"
/bin/mv -f "$nginx_source_part" "$nginx_source"
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl reload nginx

fingerprint_fae > "$fae_after" || fail
/bin/chmod 600 "$fae_after"
/usr/bin/cmp -s "$baseline" "$fae_after" || fail
/usr/bin/printf '%s\n' \
  "status=rolled_back" \
  "ai_admin_release_sha=$ai_admin_release_sha" \
  "platform_release_sha=$platform_release_sha" \
  "nginx_source=$nginx_source" \
  "backup_sha256=$backup_sha256" \
  "candidate_sha256=$candidate_sha256" \
  "legacy_admin_route_conflict_restored=true" > "$report.part"
/bin/chmod 600 "$report.part"
/bin/mv -f "$report.part" "$report"
restore_current_required=0
/bin/rm -f -- "$fae_before" "$fae_after" "$current_before"
echo "AI_ADMIN_OFFICE_PATH_ROLLBACK_OK"
