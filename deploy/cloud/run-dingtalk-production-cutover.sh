#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "DINGTALK_PRODUCTION_CUTOVER_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && $# -eq 3 ]] || fail
release_path="$1"
expected_release_sha="$2"
controlled_cookie_path="$3"
platform_root=/opt/orbbec-agent-platform
private_root="$platform_root/private"
action_lock="$private_root/agent-brain-action.lock"
deploy_input_lock="$private_root/deploy-input.lock"
[[ "$release_path" == "$platform_root/releases/$expected_release_sha" \
  && "$expected_release_sha" =~ ^[0-9a-f]{40}$ \
  && ! -e "$deploy_input_lock" && ! -e "$action_lock" ]] || fail

lock_token="$(/usr/bin/python3 -c 'import uuid; print(uuid.uuid4())')"
published=0
cleanup_lock() {
  status=$?
  trap - EXIT
  if [[ "$status" -ne 0 && "$published" == "1" ]]; then
    "$release_path/deploy/cloud/rollback-dingtalk-production.sh" || status=1
  fi
  if [[ -d "$action_lock" && ! -L "$action_lock" \
    && "$(/bin/cat "$action_lock/owner" 2>/dev/null || true)" == "$lock_token" ]]; then
    /bin/rm -f -- "$action_lock/owner"
    /bin/rmdir "$action_lock"
  fi
  exit "$status"
}
trap cleanup_lock EXIT
/bin/mkdir -m 700 "$action_lock" || fail
/usr/bin/printf '%s\n' "$lock_token" > "$action_lock/owner"
/bin/chmod 600 "$action_lock/owner"
[[ ! -e "$deploy_input_lock" ]] || fail

export PLATFORM_DINGTALK_CUTOVER_LOCK_TOKEN="$lock_token"
"$release_path/deploy/cloud/publish-dingtalk-production.sh" "$release_path"
published=1
"$release_path/deploy/cloud/accept-dingtalk-production.sh" \
  "$expected_release_sha" "$controlled_cookie_path"
[[ "$(/bin/cat "$action_lock/owner")" == "$lock_token" \
  && ! -e "$deploy_input_lock" ]] || fail

echo "DINGTALK_PRODUCTION_CUTOVER_OK release=$expected_release_sha"
