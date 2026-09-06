#!/usr/bin/env bash
set -euo pipefail

fail() {
  /usr/bin/printf 'HR_PANORAMA_PRODUCER_FAILED\n' >&2
  exit 1
}

platform_root=/opt/orbbec-agent-platform
release_path="$platform_root/current"
environment_path="$platform_root/private/platform.env"
compose_path="$release_path/deploy/cloud/compose.yaml"
operator_compose_path="$release_path/deploy/cloud/compose.hr-intelligence.yaml"
data_root=/data/orbbec-agent-platform/hr-intelligence
lock_path=/data/orbbec-agent-platform/hr-intelligence/producer.lock

[[ -d "$release_path" && ! -L "$compose_path" && -f "$compose_path" ]] || fail
[[ -f "$operator_compose_path" && ! -L "$operator_compose_path" ]] || fail
[[ -f "$environment_path" && ! -L "$environment_path" ]] || fail
[[ "$#" -ge 1 ]] || fail
case "$1" in
  seed-sources|run|resume|status) ;;
  *) fail ;;
esac

/usr/bin/install -d -o 10001 -g 10001 -m 0750 "$data_root" "$data_root/evidence"
exec 9>"$lock_path"
/usr/bin/flock -n 9 || {
  /usr/bin/printf 'HR_PANORAMA_PRODUCER_BUSY\n' >&2
  exit 75
}

owner_id="$(/usr/bin/sed -n 's/^PLATFORM_HR_PANORAMA_OWNER_ID=//p' "$environment_path" | /usr/bin/tail -1)"
[[ "$owner_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || fail

compose=(
  /usr/bin/docker compose
  --env-file "$environment_path"
  -f "$compose_path"
  -f "$operator_compose_path"
  --profile hr-intelligence
)
exec "${compose[@]}" run --rm --no-deps platform-hr-intelligence \
  python -m app.hr.panorama_cli "$@"
