#!/bin/bash
set -euo pipefail
umask 077

fail() { echo 'HR_OFFICIAL_IMPORT_FAILED' >&2; exit 1; }
[[ -z "${SSH_ORIGINAL_COMMAND:-}" ]] || fail

compose_file="${CLOUD_PLATFORM_COMPOSE_FILE:-/opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml}"
environment_file="${CLOUD_PLATFORM_ENV_FILE:-/opt/orbbec-agent-platform/private/platform.env}"
[[ -f "$compose_file" && -f "$environment_file" && ! -L "$environment_file" ]] || fail
compose=(/usr/bin/docker compose --env-file "$environment_file" -f "$compose_file")
api_container="$("${compose[@]}" ps -q platform-api)" || fail
[[ -n "$api_container" ]] || fail
image_id="$(/usr/bin/docker inspect --format '{{.Image}}' "$api_container")" || fail
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || fail
image_tags="$(/usr/bin/docker image inspect --format '{{range .RepoTags}}{{println .}}{{end}}' "$image_id")" || fail
trusted_image=0
while IFS= read -r image_tag; do
  if [[ "$image_tag" == orbbec-agent-platform:* ]]; then
    trusted_image=1
    break
  fi
done <<< "$image_tags"
[[ "$trusted_image" == 1 ]] || fail

result="$(
  /usr/bin/docker run --rm -i --pull=never \
    --user 10001:10001 --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --network orbbec-agent-platform-internal \
    --tmpfs /tmp:rw,noexec,nosuid,size=8m,uid=10001,gid=10001,mode=0700 \
    -v orbbec-agent-platform-api-secrets:/run/secrets:ro \
    -e PLATFORM_CONTROL_DATABASE_URL_FILE=/run/secrets/control-database-url \
    "$image_id" python -m app.hr.hourly_sync
)" || fail

ack="$(/usr/bin/python3 -c '
import json,re,sys
value=json.load(sys.stdin)
if set(value)!={"status","owners","jobs","versions","version","source_synced_at","sha256"}: raise SystemExit(1)
if value["status"] not in {"imported","replayed"}: raise SystemExit(1)
if any(type(value[key]) is not int or value[key]<0 for key in ("owners","jobs","versions")): raise SystemExit(1)
if value["owners"]<1 or value["jobs"]<1: raise SystemExit(1)
if not isinstance(value["version"],str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}",value["version"]): raise SystemExit(1)
if not isinstance(value["sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",value["sha256"]): raise SystemExit(1)
print("HR_OFFICIAL_IMPORT_OK sha256={sha256} version={version} status={status} owners={owners} jobs={jobs}".format(**value))
' <<< "$result")" || fail
echo "$ack"
