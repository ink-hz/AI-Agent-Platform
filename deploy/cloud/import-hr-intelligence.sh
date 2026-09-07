#!/bin/bash
set -euo pipefail
umask 077

fail() {
  /usr/bin/printf 'HR_INTELLIGENCE_IMPORT_FAILED\n' >&2
  exit 1
}

[[ "$#" -eq 2 && "$1" == /* && "$2" == /* ]] || fail
config_path="$1"
bundle_path="${2%/}"
[[ -f "$config_path" && ! -L "$config_path" && -d "$bundle_path" && ! -L "$bundle_path" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp' "$config_path" 2>/dev/null || true)" == "600" ]] || fail

set -a
# shellcheck disable=SC1090
source "$config_path"
set +a
for required_name in CLOUD_ADMIN_HOST CLOUD_ADMIN_KEY PLATFORM_HR_PANORAMA_OWNER_ID; do
  [[ -n "${!required_name:-}" ]] || fail
done
[[ "$CLOUD_ADMIN_KEY" == /* && -f "$CLOUD_ADMIN_KEY" && ! -L "$CLOUD_ADMIN_KEY" ]] || fail
[[ "$PLATFORM_HR_PANORAMA_OWNER_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || fail

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
backend_python="$repository_root/backend/.venv/bin/python"
if [[ ! -x "$backend_python" ]]; then
  common_git="$(git -C "$repository_root" rev-parse --path-format=absolute --git-common-dir)" || fail
  backend_python="$(/usr/bin/dirname "$common_git")/backend/.venv/bin/python"
fi
[[ -x "$backend_python" ]] || fail
bundle_id="$(/usr/bin/basename "$bundle_path")"
[[ "$bundle_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || fail
PYTHONPATH="$repository_root/backend" "$backend_python" -c \
  'import pathlib,sys; from app.hr.intelligence_bundle import verify_import_bundle; verify_import_bundle(pathlib.Path(sys.argv[1]))' \
  "$bundle_path"

deployment_id="$("$backend_python" -c 'import secrets; print(secrets.token_hex(16))')"
[[ "$deployment_id" =~ ^[0-9a-f]{32}$ ]] || fail
remote_staging="/data/staging/orbbec-agent-platform/hr-intelligence-$deployment_id"
remote_bundle="/data/orbbec-agent-platform/hr-intelligence/bundles/$bundle_id"
remote_release=/opt/orbbec-agent-platform/current
remote_environment=/opt/orbbec-agent-platform/private/platform.env
ssh_options=(
  -i "$CLOUD_ADMIN_KEY"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)

cleanup() {
  status=$?
  trap - EXIT
  /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" /bin/bash -s -- "$remote_staging" <<'REMOTE' || status=1
set -euo pipefail
target="$1"
[[ "$target" =~ ^/data/staging/orbbec-agent-platform/hr-intelligence-[0-9a-f]{32}$ ]] || exit 1
if [[ -d "$target" && ! -L "$target" ]]; then
  find "$target" -depth -mindepth 1 -delete
  rmdir "$target"
fi
REMOTE
  exit "$status"
}
trap cleanup EXIT

/usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" /bin/bash -s -- "$remote_staging" <<'REMOTE'
set -euo pipefail
staging="$1"
[[ "$staging" =~ ^/data/staging/orbbec-agent-platform/hr-intelligence-[0-9a-f]{32}$ ]] || exit 1
df -B1 / /data
root_available="$(df -B1 --output=avail / | tail -1 | tr -d ' ')"
data_available="$(df -B1 --output=avail /data | tail -1 | tr -d ' ')"
[[ "$root_available" -ge 26843545600 && "$data_available" -ge 21474836480 ]] || exit 1
[[ ! -e "$staging" && ! -L "$staging" ]] || exit 1
install -d -m 0750 "$staging"
REMOTE

/usr/bin/tar -C "$bundle_path" -cf - . | /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  /usr/bin/tar -C "$remote_staging" -xf -

/usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" /bin/bash -s -- \
  "$remote_staging" "$remote_bundle" "$bundle_id" "$remote_release" \
  "$remote_environment" "$PLATFORM_HR_PANORAMA_OWNER_ID" <<'REMOTE'
set -euo pipefail
staging="$1"; final="$2"; bundle_id="$3"; release="$4"; environment="$5"; owner_id="$6"
[[ "$staging" =~ ^/data/staging/orbbec-agent-platform/hr-intelligence-[0-9a-f]{32}$ ]] || exit 1
[[ "$final" == "/data/orbbec-agent-platform/hr-intelligence/bundles/$bundle_id" ]] || exit 1
[[ -d "$staging" && ! -L "$staging" && -d "$release" && ! -L "$release" ]] || exit 1
[[ -f "$environment" && ! -L "$environment" ]] || exit 1
(cd "$staging" && /usr/bin/sha256sum -c checksums.sha256)
/usr/bin/chown -R 10001:10001 -- "$staging"
installed=0
rollback_file() {
  status=$?
  trap - EXIT
  if [[ "$installed" == "1" && -d "$final" && ! -L "$final" ]]; then
    find "$final" -depth -mindepth 1 -delete
    rmdir "$final"
  fi
  exit "$status"
}
trap rollback_file EXIT
if [[ -e "$final" || -L "$final" ]]; then
  [[ -d "$final" && ! -L "$final" ]] || exit 1
  (cd "$final" && /usr/bin/sha256sum -c checksums.sha256)
  find "$staging" -depth -mindepth 1 -delete
  rmdir "$staging"
else
  install -d -m 0750 "$(dirname "$final")"
  mv "$staging" "$final"
  installed=1
fi
export PLATFORM_HR_INTELLIGENCE_BUNDLE_PATH="$final"
export PLATFORM_HR_INTELLIGENCE_BUNDLE_ID="$bundle_id"
export PLATFORM_HR_PANORAMA_OWNER_ID="$owner_id"
/usr/bin/docker compose --env-file "$environment" \
  -f "$release/deploy/cloud/compose.yaml" \
  -f "$release/deploy/cloud/compose.hr-intelligence-import.yaml" \
  --profile hr-intelligence-import run --rm --no-deps \
  platform-hr-intelligence-import \
  python -m app.hr.intelligence_import --expected-bundle-id "$bundle_id" </dev/null
installed=0
trap - EXIT
df -B1 / /data
REMOTE

trap - EXIT
cleanup
