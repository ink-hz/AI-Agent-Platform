#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "PRODUCTION_OWNER_BINDING_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && $# -eq 4 ]] || fail
approver_a="$1"
approver_b="$2"
backup_reference="$3"
incident_reference="$4"
[[ "$approver_a" =~ ^[a-z_][a-z0-9_.-]{0,31}$ ]] || fail
[[ "$approver_b" =~ ^[a-z_][a-z0-9_.-]{0,31}$ && "$approver_b" != "$approver_a" ]] || fail
[[ "$backup_reference" =~ ^[A-Z][A-Z0-9_-]{2,63}$ ]] || fail
[[ "$incident_reference" =~ ^[A-Z][A-Z0-9_-]{2,63}$ ]] || fail

platform_root=/opt/orbbec-agent-platform
release_path="$(/usr/bin/readlink -f "$platform_root/current")"
private_path="$platform_root/private"
environment_path="$private_path/platform.env"
compose_path="$release_path/deploy/cloud/compose.yaml"
state_path="$private_path/owner-binding"
[[ "$release_path" == /opt/orbbec-agent-platform/releases/* ]] || fail
[[ -f "$environment_path" && -f "$compose_path" ]] || fail
/usr/bin/install -d -o root -g root -m 700 "$state_path"

required=(
  dingtalk-owner-userid
  dingtalk-corp-id
  control-migrator-database-url
  control-audit-database-url
  identity-encryption-keyring
  identity-hmac-keyring
  owner-receipt-keyring
)
for name in "${required[@]}"; do
  path="$private_path/$name"
  [[ -f "$path" && ! -L "$path" ]] || fail
  [[ "$(/usr/bin/stat -c '%a %U' "$path")" == "600 root" ]] || fail
done

provider_file="$state_path/owner-provider-id"
/usr/bin/python3 - "$private_path/dingtalk-corp-id" \
  "$private_path/dingtalk-owner-userid" "$provider_file.part" <<'PY'
import os
from pathlib import Path
import sys

corp = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
userid = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
if not corp or not userid or "\0" in corp or "\0" in userid:
    raise SystemExit(1)
value = f"{len(corp.encode('utf-8'))}:{corp}{userid}"
path = Path(sys.argv[3])
path.write_text(value + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY
/bin/chown root:root "$provider_file.part"
/bin/mv -f "$provider_file.part" "$provider_file"

compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
api_container="$("${compose[@]}" ps -q platform-api)"
[[ -n "$api_container" ]] || fail
image_name="$(/usr/bin/docker inspect --format '{{.Config.Image}}' "$api_container")"
[[ "$image_name" == orbbec-agent-platform:* ]] || fail
admin=(
  /usr/bin/docker run --rm --user 0:0 --read-only
  --security-opt no-new-privileges:true
  --network orbbec-agent-platform-internal
  --tmpfs /tmp:rw,noexec,nosuid,size=8m
  -v "$private_path:/run/owner-secrets:ro"
  -v "$state_path:/run/owner-state"
  "$image_name" python -m app.control_plane.admin_cli
  --database-url-file /run/owner-secrets/control-migrator-database-url
  --audit-database-url-file /run/owner-secrets/control-audit-database-url
  --encryption-keyring-file /run/owner-secrets/identity-encryption-keyring
  --hmac-keyring-file /run/owner-secrets/identity-hmac-keyring
  --owner-role platform_control_owner
)

generation_json="$("${admin[@]}" show-directory-generation)" || fail
generation_id="$(/usr/bin/python3 -c '
import json,sys
value=json.load(sys.stdin)
generation=value.get("generation")
if value.get("status")!="ok" or not isinstance(generation,dict) or generation.get("status")!="complete" or generation.get("is_active") is not True:
    raise SystemExit(1)
print(generation["generation_id"])
' <<<"$generation_json")" || fail
[[ "$generation_id" =~ ^[0-9a-f-]{36}$ ]] || fail

receipt_name="owner-bind-$(/usr/bin/date -u +%Y%m%dT%H%M%SZ).json"
receipt_container="/run/owner-state/$receipt_name"
common=(
  bind-owner
  --provider-id-file /run/owner-state/owner-provider-id
  --subject-kind employee
  --generation-id "$generation_id"
  --incident-reference "$incident_reference"
  --backup-reference "$backup_reference"
  --approver "$approver_a"
  --approver "$approver_b"
  --receipt-file "$receipt_container"
  --receipt-key-file /run/owner-secrets/owner-receipt-keyring
  --receipt-key-version 1
)
dry_run="$("${admin[@]}" "${common[@]}")" || fail
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status")=="dry_run" and value.get("receipt_created") is True' \
  <<<"$dry_run" || fail
result="$("${admin[@]}" "${common[@]}" --confirm "$receipt_container")" || fail
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status")=="ok" and value.get("operation")=="bind"' \
  <<<"$result" || fail

/bin/rm -f -- "$provider_file"
echo "PRODUCTION_OWNER_BINDING_OK owner_binding=1"
