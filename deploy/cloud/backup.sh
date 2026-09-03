#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "CLOUD_BACKUP_FAILED" >&2
  exit 1
}

root_path="/opt/orbbec-agent-platform"
current_path="$root_path/current"
private_path="$root_path/private"
environment_path="$private_path/platform.env"
compose_path="$current_path/deploy/cloud/compose.yaml"
recovery_public="$private_path/backup-recovery-x25519.pub"
backup_path=/data/orbbec-agent-platform/backups
attachment_path=/data/orbbec-agent-platform/attachments
[[ -f "$compose_path" && -f "$environment_path" && -f "$recovery_public" && ! -L "$recovery_public" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U %s' "$recovery_public")" == "600 root 32" ]] || fail

compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
/usr/bin/docker volume create orbbec-agent-platform-backup-secrets >/dev/null
/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-backup-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 \
  sh -ceu 'cp /source/backup-recovery-x25519.pub /target/recovery-public-key; chown 10001:10001 /target/recovery-public-key; chmod 600 /target/recovery-public-key'
/usr/bin/install -d -o 10001 -g 10001 -m 700 "$backup_path"
[[ -d "$attachment_path" && ! -L "$attachment_path" ]] || fail

timestamp="$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)"
backup_name="replica-$timestamp.orb"
if ! result="$(
  "${compose[@]}" exec -T platform-postgres \
    pg_dump -U platform_owner -d agent_platform --format=custom --no-password \
  | "${compose[@]}" run --rm --no-deps -T \
      -v orbbec-agent-platform-backup-secrets:/run/backup-secrets:ro \
      -v "$backup_path:/backups" \
      -e PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE=/run/backup-secrets/recovery-public-key \
      -e "PLATFORM_REPLICA_BACKUP_PATH=/backups/$backup_name" \
      platform-api python -m app.cloud_replica.cli backup
)"; then
  fail
fi
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status")=="backed_up" and value.get("encrypted_size",-1)>=0 and value.get("plaintext_size",-1)>=0' <<< "$result" || fail

control_backup_name="control-$timestamp.orb"
if ! control_result="$(
  "${compose[@]}" exec -T platform-postgres \
    pg_dump -U platform_owner -d agent_platform_control --format=custom --no-password \
  | "${compose[@]}" run --rm --no-deps -T \
      -v orbbec-agent-platform-backup-secrets:/run/backup-secrets:ro \
      -v "$backup_path:/backups" \
      -e PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE=/run/backup-secrets/recovery-public-key \
      -e "PLATFORM_REPLICA_BACKUP_PATH=/backups/$control_backup_name" \
      platform-api python -m app.cloud_replica.cli backup
)"; then
  fail
fi
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status")=="backed_up" and value.get("encrypted_size",-1)>0 and value.get("plaintext_size",-1)>0' <<< "$control_result" || fail

object_backup_name="attachments-$timestamp.orb"
if ! object_result="$(
  /usr/bin/docker run --rm --network none -v "$attachment_path:/source:ro" alpine:3.22 \
    tar -C /source -cf - . \
  | "${compose[@]}" run --rm --no-deps -T \
      -v orbbec-agent-platform-backup-secrets:/run/backup-secrets:ro \
      -v "$backup_path:/backups" \
      -e PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE=/run/backup-secrets/recovery-public-key \
      -e "PLATFORM_REPLICA_BACKUP_PATH=/backups/$object_backup_name" \
      platform-api python -m app.cloud_replica.cli backup
)"; then
  fail
fi
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status")=="backed_up" and value.get("encrypted_size",-1)>0 and value.get("plaintext_size",-1)>0' <<< "$object_result" || fail

if ! retention_result="$(
  "${compose[@]}" run --rm --no-deps -T \
    -v orbbec-agent-platform-import-secrets:/run/import-secrets:ro \
    -e PLATFORM_REPLICA_DATABASE_URL_FILE=/run/import-secrets/replica-database-url \
    -e PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/import-secrets/replica-encryption-key \
    platform-api python -m app.cloud_replica.cli retention
)"; then
  fail
fi
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status")=="completed" and value.get("dry_run") is False' <<< "$retention_result" || fail

marker_path="$private_path/last-backup-success"
/usr/bin/printf '%s %s %s\n' "$backup_name" "$control_backup_name" "$object_backup_name" > "$marker_path.part"
/bin/chown root:root "$marker_path.part"
/bin/chmod 600 "$marker_path.part"
/bin/mv -f "$marker_path.part" "$marker_path"
echo "CLOUD_BACKUP_OK"
