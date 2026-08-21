#!/bin/bash
set -euo pipefail
umask 077

root_path="/opt/orbbec-agent-platform"
private_path="$root_path/private"
releases_path="$root_path/releases"
staging_path="$root_path/staging"
environment_path="$private_path/platform.env"
rotation_lock="$private_path/execution-worker-key-rotation.lock"
rotation_state="$private_path/execution-worker-key-rotation-state.json"
deploy_state="$private_path/execution-worker-keyring-deploy-state.json"
deploy_state_part="$private_path/execution-worker-keyring-deploy-state.json.part"
worker_keyring="$private_path/execution-worker-public-keyring.json"
worker_keyring_part="$private_path/execution-worker-public-keyring.json.part"
worker_keyring_previous="$private_path/execution-worker-public-keyring.deploy.previous.json"

fail() {
  echo "CLOUD_PLATFORM_DEPLOY_FAILED" >&2
  exit 1
}

[[ $# -eq 2 ]] || fail
release_sha="$1"
expected_digest="$2"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ && "$expected_digest" =~ ^[0-9a-f]{64}$ ]] || fail
release_path="$releases_path/$release_sha"
stage_path="$staging_path/$release_sha"
archive_path="$stage_path/release.tar.gz"

/usr/bin/install -d -m 700 "$private_path" "$releases_path" "$stage_path"
staged_worker_keyring="$stage_path/execution-worker-public-keyring.json"
[[ "${PLATFORM_EXECUTION_WORKER_DEPLOY_LOCK_FD:-}" =~ ^[0-9]+$ ]] || fail
/usr/bin/python3 - "$rotation_lock" "$PLATFORM_EXECUTION_WORKER_DEPLOY_LOCK_FD" <<'PY' || fail
import fcntl
import os
import stat
import sys

path, raw_descriptor = sys.argv[1:]
descriptor = int(raw_descriptor)
metadata = os.fstat(descriptor)
named = os.stat(path, follow_symlinks=False)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_uid != os.getuid()
    or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino)
):
    raise SystemExit(1)
fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
PY
[[ ! -e "$rotation_state" && ! -L "$rotation_state" ]] || fail
[[ -f "$staged_worker_keyring" && ! -L "$staged_worker_keyring" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$staged_worker_keyring")" == "600 root" ]] || fail
available_bytes="$(/usr/bin/df -B1 --output=avail "$root_path" | /usr/bin/tail -1 | /usr/bin/tr -d ' ')"
[[ "$available_bytes" =~ ^[0-9]+$ && "$available_bytes" -ge 10737418240 ]] || fail

fae_container_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend 2>/dev/null || true)"
fae_image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' ai-fae-backend 2>/dev/null || true)"
fae_started_at="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend 2>/dev/null || true)"
fae_health_digest="$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend 2>/dev/null | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
nginx_digest="$(/usr/sbin/nginx -T 2>&1 | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
public_listener_digest="$(/usr/bin/ss -H -lnt | /usr/bin/awk '$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}' | /usr/bin/sort -u | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
[[ -n "$fae_container_id" && -n "$fae_image" && -n "$fae_started_at" ]] || fail

existing_api="$(/usr/bin/docker ps --filter label=com.docker.compose.project=orbbec-agent-platform --filter label=com.docker.compose.service=platform-api --format '{{.ID}}' | /usr/bin/head -1)"
control_secret_consumer_services=(
  platform-api
  platform-api-preview
  platform-directory
  platform-directory-preview
  platform-dingtalk-stream
  platform-dingtalk-stream-preview
)
previous_control_consumers=()
previous_release=""
if [[ -L "$root_path/current" ]]; then
  previous_release="$(/usr/bin/readlink -f "$root_path/current" 2>/dev/null || true)"
  [[ -n "$previous_release" ]] || fail
  [[ -f "$previous_release/deploy/cloud/compose.yaml" ]] || fail
fi
previous_environment="$stage_path/previous.env"
cloud_auth_mode="ssh-tunnel"
if [[ -f "$environment_path" ]]; then
  /bin/cp -p "$environment_path" "$previous_environment"
  configured_auth_mode="$(/usr/bin/sed -n 's/^PLATFORM_CLOUD_AUTH_MODE=//p' "$environment_path")"
  if [[ -n "$configured_auth_mode" ]]; then
    cloud_auth_mode="$configured_auth_mode"
  fi
fi
[[ "$cloud_auth_mode" == "ssh-tunnel" || "$cloud_auth_mode" == "basic-auth" || "$cloud_auth_mode" == "dingtalk" ]] || fail
if [[ -n "$existing_api" && ( -z "$previous_release" || ! -f "$previous_environment" ) ]]; then
  fail
fi
port_8080_listeners="$(/usr/bin/ss -H -lnt | /usr/bin/awk '$4 ~ /:8080$/ {print $4}')"
if [[ -n "$port_8080_listeners" ]]; then
  [[ -n "$existing_api" ]] || fail
  [[ "$port_8080_listeners" == "127.0.0.1:8080" ]] || fail
fi
forbidden_bind_ipv4="0.0.0.0:8080"
forbidden_bind_ipv6="[::]:8080"

/bin/dd of="$archive_path.part" status=none
actual_digest="$(/usr/bin/sha256sum "$archive_path.part" | /usr/bin/awk '{print $1}')"
[[ "$actual_digest" == "$expected_digest" ]] || fail
/bin/mv -f "$archive_path.part" "$archive_path"
if /usr/bin/tar -tzf "$archive_path" | /usr/bin/grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  fail
fi
[[ ! -e "$release_path" ]] || fail
/usr/bin/install -d -m 700 "$release_path"
rollback_required=1
api_stopped=0
fsync_private() {
  /usr/bin/python3 - "$private_path" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}
fsync_file() {
  /usr/bin/python3 - "$1" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}
write_deploy_state() {
  phase="$1"
  /usr/bin/printf '{"next_sha256":"%s","phase":"%s","previous_sha256":"%s","release_sha":"%s","schema_version":1}\n' \
    "$next_keyring_sha" "$phase" "$previous_keyring_sha" "$release_sha" > "$deploy_state_part"
  /bin/chmod 600 "$deploy_state_part"
  fsync_file "$deploy_state_part"
  /bin/mv -f "$deploy_state_part" "$deploy_state"
  fsync_private
}
cleanup_worker_keyring_deploy() {
  /bin/rm -f -- "$deploy_state_part" "$worker_keyring_previous" "$staged_worker_keyring"
  fsync_private
  /bin/rm -f -- "$deploy_state"
  fsync_private
}
restore_worker_keyring() {
  if [[ -e "$deploy_state" || -L "$deploy_state" ]]; then
    [[ -f "$deploy_state" && ! -L "$deploy_state" ]] || return 1
    [[ "$(/usr/bin/stat -c '%a %U' "$deploy_state")" == "600 root" ]] || return 1
    [[ -f "$worker_keyring_previous" && ! -L "$worker_keyring_previous" ]] || return 1
    [[ "$(/usr/bin/stat -c '%a %U' "$worker_keyring_previous")" == "600 root" ]] || return 1
    /usr/bin/python3 - "$deploy_state" "$worker_keyring_previous" "$staged_worker_keyring" "$release_sha" <<'PY' || return 1
import hashlib
import json
import pathlib
import re
import sys

state_path, previous_path, staged_path = map(pathlib.Path, sys.argv[1:4])
release_sha = sys.argv[4]
value = json.loads(state_path.read_bytes())
if (
    not isinstance(value, dict)
    or set(value) != {"schema_version", "phase", "release_sha", "previous_sha256", "next_sha256"}
    or value["schema_version"] != 1
    or value["phase"] not in {"keyring_switching", "keyring_switched", "completed"}
    or value["release_sha"] != release_sha
    or any(not isinstance(value[name], str) or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None for name in ("previous_sha256", "next_sha256"))
    or hashlib.sha256(previous_path.read_bytes()).hexdigest() != value["previous_sha256"]
    or hashlib.sha256(staged_path.read_bytes()).hexdigest() != value["next_sha256"]
):
    raise SystemExit(1)
PY
    /usr/bin/install -o root -g root -m 600 "$worker_keyring_previous" "$worker_keyring_part" || return 1
    fsync_file "$worker_keyring_part" || return 1
    /bin/mv -f "$worker_keyring_part" "$worker_keyring" || return 1
    fsync_private || return 1
    cleanup_worker_keyring_deploy || return 1
  elif [[ -e "$worker_keyring_previous" || -L "$worker_keyring_previous" ]]; then
    [[ -f "$worker_keyring_previous" && ! -L "$worker_keyring_previous" ]] || return 1
    /usr/bin/cmp -s "$worker_keyring_previous" "$worker_keyring" || return 1
    /bin/rm -f -- "$worker_keyring_previous" || return 1
    fsync_private || return 1
  fi
}
if [[ -e "$deploy_state" || -L "$deploy_state" || -e "$deploy_state_part" || -L "$deploy_state_part" || -e "$worker_keyring_previous" || -L "$worker_keyring_previous" ]]; then
  fail
fi
rollback() {
  if [[ "$rollback_required" -ne 1 ]]; then
    return
  fi
  if ! restore_worker_keyring; then
    echo "EXECUTION_WORKER_KEYRING_DEPLOY_ROLLBACK_FAILED" >&2
  fi
  if [[ "$api_stopped" -eq 1 ]]; then
    if [[ -f "$release_path/deploy/cloud/compose.yaml" && -f "$environment_path" ]]; then
      candidate_services="$(/usr/bin/docker compose --env-file "$environment_path" \
        -f "$release_path/deploy/cloud/compose.yaml" config --services 2>/dev/null || true)"
      candidate_to_stop=()
      for service_name in platform-loopback platform-api platform-directory platform-dingtalk-stream; do
        if /usr/bin/grep -Fxq "$service_name" <<<"$candidate_services"; then
          candidate_to_stop+=("$service_name")
        fi
      done
      if [[ "${#candidate_to_stop[@]}" -gt 0 ]]; then
        for service_name in "${candidate_to_stop[@]}"; do
          container_id="$(/usr/bin/docker compose --env-file "$environment_path" \
            -f "$release_path/deploy/cloud/compose.yaml" \
            ps -a -q "$service_name" 2>/dev/null || true)"
          if [[ -n "$container_id" ]]; then
            /usr/bin/docker rm -f "$container_id" >/dev/null 2>&1 || true
          fi
        done
      fi
    fi
    if [[ -n "$previous_release" && -f "$previous_environment" ]]; then
      /bin/cp -p "$previous_environment" "$environment_path"
      /bin/ln -sfn "$previous_release" "$root_path/current"
      if [[ "${#previous_control_consumers[@]}" -gt 0 ]]; then
        /usr/bin/docker compose --env-file "$environment_path" \
          -f "$previous_release/deploy/cloud/compose.yaml" \
          up -d --force-recreate "${previous_control_consumers[@]}" \
          >/dev/null 2>&1 || true
      fi
    else
      /usr/bin/docker rm -f orbbec-agent-platform-platform-loopback-1 >/dev/null 2>&1 || true
      /usr/bin/docker rm -f orbbec-agent-platform-platform-api-1 >/dev/null 2>&1 || true
      /usr/bin/systemctl disable --now orbbec-agent-platform-backup.timer >/dev/null 2>&1 || true
      if [[ -L "$root_path/current" ]]; then
        /usr/bin/unlink "$root_path/current" || true
      fi
    fi
  fi
  if [[ -d "$release_path" && "$release_path" != "$previous_release" ]]; then
    /bin/mv "$release_path" "$stage_path/failed-release-$BASHPID" >/dev/null 2>&1 || true
  fi
}
trap rollback EXIT
/usr/bin/tar -xzf "$archive_path" -C "$release_path"
if /usr/bin/find "$release_path" -type l -print -quit | /usr/bin/grep -q .; then
  fail
fi
(cd "$release_path" && /usr/bin/sha256sum --check MANIFEST.sha256 >/dev/null) || fail

signing_public="$private_path/replica-signing-public-key"
[[ -f "$signing_public" && ! -L "$signing_public" && "$(/usr/bin/stat -c '%a %U %s' "$signing_public")" == "600 root 32" ]] || fail

postgres_password="$private_path/postgres-owner-password"
read_password="$private_path/replica-read-password"
import_password="$private_path/replica-import-password"
encryption_key="$private_path/replica-encryption-key"
[[ -e "$postgres_password" ]] || /usr/bin/openssl rand -hex 32 > "$postgres_password"
[[ -e "$read_password" ]] || /usr/bin/openssl rand -hex 32 > "$read_password"
[[ -e "$import_password" ]] || /usr/bin/openssl rand -hex 32 > "$import_password"
[[ -e "$encryption_key" ]] || /usr/bin/openssl rand 32 > "$encryption_key"
for password_file in "$postgres_password" "$read_password" "$import_password"; do
  [[ "$(/usr/bin/tr -d '\n' < "$password_file")" =~ ^[0-9a-f]{64}$ ]] || fail
  /bin/chown root:root "$password_file"
  /bin/chmod 600 "$password_file"
done
[[ "$(/usr/bin/stat -c '%s' "$encryption_key")" == "32" ]] || fail
/bin/chown root:root "$encryption_key"
/bin/chmod 600 "$encryption_key"

owner_password_value="$(/usr/bin/tr -d '\n' < "$postgres_password")"
read_password_value="$(/usr/bin/tr -d '\n' < "$read_password")"
import_password_value="$(/usr/bin/tr -d '\n' < "$import_password")"
owner_dsn="$private_path/replica-owner-database-url"
read_dsn="$private_path/replica-database-url"
import_dsn="$private_path/replica-import-database-url"
/usr/bin/printf 'postgresql://platform_owner:%s@platform-postgres:5432/agent_platform\n' "$owner_password_value" > "$owner_dsn"
/usr/bin/printf 'postgresql://platform_replica_reader:%s@platform-postgres:5432/agent_platform\n' "$read_password_value" > "$read_dsn"
/usr/bin/printf 'postgresql://platform_replica_importer:%s@platform-postgres:5432/agent_platform\n' "$import_password_value" > "$import_dsn"
/bin/chown root:root "$owner_dsn" "$read_dsn" "$import_dsn"
/bin/chmod 600 "$owner_dsn" "$read_dsn" "$import_dsn"

image_name="orbbec-agent-platform:$release_sha"
/usr/bin/docker build --pull --build-arg "RELEASE_SHA=$release_sha" -t "$image_name" -f "$release_path/deploy/cloud/Dockerfile" "$release_path" >/dev/null

for volume_name in \
  orbbec-agent-platform-postgres-secrets \
  orbbec-agent-platform-api-secrets \
  orbbec-agent-platform-migrate-secrets \
  orbbec-agent-platform-import-secrets; do
  /usr/bin/docker volume create "$volume_name" >/dev/null
done
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-postgres-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/postgres-owner-password /target/postgres-owner-password; chown 999:999 /target/postgres-owner-password; chmod 400 /target/postgres-owner-password'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-api-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/replica-database-url /source/replica-encryption-key /source/replica-signing-public-key /target/; chown 10001:10001 /target/*; chmod 600 /target/replica-database-url; chmod 600 /target/replica-encryption-key; chmod 600 /target/replica-signing-public-key'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-migrate-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/replica-owner-database-url /target/replica-database-url; cp /source/replica-encryption-key /source/replica-signing-public-key /target/; chown 10001:10001 /target/*; chmod 600 /target/replica-database-url; chmod 600 /target/replica-encryption-key; chmod 600 /target/replica-signing-public-key'
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-import-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/replica-import-database-url /target/replica-database-url; cp /source/replica-encryption-key /source/replica-signing-public-key /target/; chown 10001:10001 /target/*; chmod 600 /target/replica-database-url; chmod 600 /target/replica-encryption-key; chmod 600 /target/replica-signing-public-key'

if [[ -n "$previous_release" && -f "$environment_path" ]]; then
  previous_compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$previous_release/deploy/cloud/compose.yaml")
  previous_services="$("${previous_compose[@]}" config --services)"
  previous_control_consumers=()
  for service_name in "${control_secret_consumer_services[@]}" platform-loopback platform-loopback-preview; do
    if /usr/bin/grep -Fxq "$service_name" <<<"$previous_services"; then
      previous_control_consumers+=("$service_name")
    fi
  done
  if [[ "${#previous_control_consumers[@]}" -gt 0 ]]; then
    "${previous_compose[@]}" stop "${previous_control_consumers[@]}" >/dev/null
    api_stopped=1
  fi
fi
/usr/bin/printf 'PLATFORM_IMAGE=%s\nPLATFORM_CLOUD_AUTH_MODE=dingtalk\n' \
  "$image_name" > "$environment_path"
/bin/chown root:root "$environment_path"
/bin/chmod 600 "$environment_path"
unset PLATFORM_CLOUD_AUTH_MODE
compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$release_path/deploy/cloud/compose.yaml")
"${compose[@]}" up -d --force-recreate platform-postgres >/dev/null
for _attempt in $(/usr/bin/seq 1 40); do
  postgres_id="$("${compose[@]}" ps -q platform-postgres)"
  [[ -n "$postgres_id" ]] || { /bin/sleep 1; continue; }
  [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$postgres_id")" == "healthy" ]] && break
  /bin/sleep 1
done
[[ "${postgres_id:-}" != "" && "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$postgres_id")" == "healthy" ]] || fail

/usr/bin/docker run --rm --network orbbec-agent-platform-internal \
  -v orbbec-agent-platform-migrate-secrets:/run/secrets:ro \
  -e PLATFORM_REPLICA_DATABASE_URL_FILE=/run/secrets/replica-database-url \
  -e PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/secrets/replica-encryption-key \
  "$image_name" python -m app.cloud_replica.cli migrate >/dev/null

postgres_container="$("${compose[@]}" ps -q platform-postgres)"
control_bootstrap_result="$("$release_path/deploy/cloud/bootstrap-control-db.sh" \
  "$release_path" "$private_path" "$image_name" "$postgres_container")" || fail
[[ "$control_bootstrap_result" == "CONTROL_DATABASE_CREDENTIALS_READY version=2" ]] || fail
/usr/bin/test ! -e "$worker_keyring_previous" || fail
/usr/bin/install -o root -g root -m 600 "$worker_keyring" "$worker_keyring_previous"
fsync_file "$worker_keyring_previous"
fsync_private
previous_keyring_sha="$(/usr/bin/sha256sum "$worker_keyring_previous" | /usr/bin/awk '{print $1}')"
next_keyring_sha="$(/usr/bin/sha256sum "$staged_worker_keyring" | /usr/bin/awk '{print $1}')"
write_deploy_state keyring_switching
/usr/bin/install -o root -g root -m 600 "$staged_worker_keyring" "$worker_keyring_part"
fsync_file "$worker_keyring_part"
/bin/mv -f "$worker_keyring_part" "$worker_keyring"
fsync_private
write_deploy_state keyring_switched
identity_bootstrap_result="$("$release_path/deploy/cloud/bootstrap-dingtalk-production-secrets.sh" \
  "$private_path")" || fail
[[ "$identity_bootstrap_result" == "DINGTALK_PRODUCTION_SECRETS_OK" ]] || fail
identity_policy_result="$(/usr/bin/docker run --rm --user 0:0 --read-only \
  --security-opt no-new-privileges:true \
  --network orbbec-agent-platform-internal \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  -v "$private_path:/run/control-secrets:ro" \
  "$image_name" python -m app.control_plane.maintenance_cli \
  --database-url-file /run/control-secrets/control-maintenance-database-url \
  sync-identity-policy \
  --keyring-file /run/control-secrets/identity-hmac-keyring)" || fail
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value=={"provider":"dingtalk","status":"ok","transition_versions":[1]}' \
  <<<"$identity_policy_result" || fail
/usr/bin/docker exec -i "$postgres_container" psql -v ON_ERROR_STOP=1 -U platform_owner -d agent_platform >/dev/null <<SQL
do \$\$
begin
  if not exists (select 1 from pg_roles where rolname='platform_replica_reader') then
    create role platform_replica_reader login password '$read_password_value';
  else
    alter role platform_replica_reader password '$read_password_value';
  end if;
  if not exists (select 1 from pg_roles where rolname='platform_replica_importer') then
    create role platform_replica_importer login password '$import_password_value';
  else
    alter role platform_replica_importer password '$import_password_value';
  end if;
end
\$\$;
grant platform_replica_read to platform_replica_reader;
grant platform_replica_import to platform_replica_importer;
SQL

available_release_services="$("${compose[@]}" config --services)"
active_control_secret_consumers=()
for service_name in "${control_secret_consumer_services[@]}"; do
  if /usr/bin/grep -Fxq "$service_name" <<<"$available_release_services"; then
    active_control_secret_consumers+=("$service_name")
  fi
done
if [[ "${#active_control_secret_consumers[@]}" -gt 0 ]]; then
  api_stopped=1
  "${compose[@]}" up -d --force-recreate "${active_control_secret_consumers[@]}" >/dev/null
fi
active_loopback_services=()
for service_name in platform-loopback platform-loopback-preview; do
  if /usr/bin/grep -Fxq "$service_name" <<<"$available_release_services"; then
    active_loopback_services+=("$service_name")
  fi
done
if [[ "${#active_loopback_services[@]}" -gt 0 ]]; then
  "${compose[@]}" up -d --force-recreate "${active_loopback_services[@]}" >/dev/null
fi
for _attempt in $(/usr/bin/seq 1 40); do
  if /usr/bin/curl --silent --show-error --fail --max-time 2 http://127.0.0.1:8080/api/health >/dev/null; then
    break
  fi
  /bin/sleep 1
done
/usr/bin/curl --silent --show-error --fail --max-time 2 http://127.0.0.1:8080/api/health >/dev/null || fail
api_container="$("${compose[@]}" ps -q platform-api)"
[[ -n "$api_container" ]] || fail
api_environment="$(/usr/bin/docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$api_container")"
for required_runtime_value in \
  PLATFORM_DEPLOYMENT_MODE=cloud-replica \
  PLATFORM_CLOUD_AUTH_MODE=dingtalk \
  PLATFORM_IDENTITY_MODE=production; do
  /usr/bin/grep -Fxq "$required_runtime_value" <<<"$api_environment" || fail
done
if [[ -n "$previous_release" ]]; then
  /usr/bin/printf '%s\n' "$previous_release" > "$release_path/PREVIOUS_RELEASE"
  /bin/chown root:root "$release_path/PREVIOUS_RELEASE"
  /bin/chmod 600 "$release_path/PREVIOUS_RELEASE"
fi
if [[ -f "$previous_environment" ]]; then
  /bin/cp -p "$previous_environment" "$release_path/PREVIOUS_PLATFORM_ENV"
  /bin/chown root:root "$release_path/PREVIOUS_PLATFORM_ENV"
  /bin/chmod 600 "$release_path/PREVIOUS_PLATFORM_ENV"
fi
/bin/ln -sfn "$release_path" "$root_path/current"
/usr/bin/install -o root -g root -m 644 \
  "$release_path/deploy/cloud/orbbec-agent-platform-backup.service" \
  /etc/systemd/system/orbbec-agent-platform-backup.service
/usr/bin/install -o root -g root -m 644 \
  "$release_path/deploy/cloud/orbbec-agent-platform-backup.timer" \
  /etc/systemd/system/orbbec-agent-platform-backup.timer
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl enable --now orbbec-agent-platform-backup.timer >/dev/null

[[ "$fae_container_id" == "$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)" ]] || fail
[[ "$fae_image" == "$(/usr/bin/docker inspect --format '{{.Config.Image}}' ai-fae-backend)" ]] || fail
[[ "$fae_started_at" == "$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)" ]] || fail
[[ "$fae_health_digest" == "$(/usr/bin/docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-fae-backend | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$nginx_digest" == "$(/usr/sbin/nginx -T 2>&1 | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$public_listener_digest" == "$(/usr/bin/ss -H -lnt | /usr/bin/awk '$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}' | /usr/bin/sort -u | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')" ]] || fail
/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Fq '127.0.0.1:8080' || fail
if /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/grep -Eq "^(${forbidden_bind_ipv4}|\\[::\\]:8080)$"; then
  fail
fi

write_deploy_state completed
cleanup_worker_keyring_deploy
rollback_required=0
trap - EXIT
echo "CLOUD_PLATFORM_DEPLOY_OK release=$release_sha mode=dingtalk"
