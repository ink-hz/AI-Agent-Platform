#!/bin/bash
set -eEuo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' AGENTOPS_CONTROL_REMOVE_FAILED >&2
  exit 1
}

required_uid=0
target_owner=root
target_group=wheel
dispatcher_target=/Library/PrivilegedHelperTools/orbbec-agentops-control
sudoers_target=/etc/sudoers.d/orbbec-agentops-control
sudoers_root=/etc/sudoers
visudo_bin=/usr/sbin/visudo
install_bin=/usr/bin/install

[[ $# -eq 0 && "$(/usr/bin/id -u)" == "$required_uid" ]] || fail
transaction="$(/usr/bin/mktemp -d "${TMPDIR:-/private/var/tmp}/orbbec-agentops-control-remove.XXXXXX")" || fail
/bin/chmod 700 "$transaction"
dispatcher_backup="$transaction/dispatcher.backup"
sudoers_backup="$transaction/sudoers.backup"
dispatcher_existed=0
sudoers_existed=0
success=0

validate_existing() {
  target="$1"
  expected_mode="$2"
  [[ -f "$target" && ! -L "$target" ]] || return 1
  [[ "$(/usr/bin/stat -f '%Su %Sg %Lp' "$target")" == "$target_owner $target_group $expected_mode" ]]
}

cleanup() {
  status="$?"
  trap - ERR EXIT
  if [[ "$success" != 1 ]]; then
    if [[ "$dispatcher_existed" == 1 ]]; then
      "$install_bin" -o "$target_owner" -g "$target_group" -m 0755 "$dispatcher_backup" "$dispatcher_target" || status=1
    fi
    if [[ "$sudoers_existed" == 1 ]]; then
      "$install_bin" -o "$target_owner" -g "$target_group" -m 0440 "$sudoers_backup" "$sudoers_target" || status=1
    fi
  fi
  /bin/rm -f -- "$dispatcher_backup" "$sudoers_backup" || status=1
  /bin/rmdir "$transaction" >/dev/null 2>&1 || status=1
  exit "$status"
}
trap cleanup ERR EXIT

if [[ -e "$dispatcher_target" || -L "$dispatcher_target" ]]; then
  validate_existing "$dispatcher_target" 755 || fail
  /bin/cp -p "$dispatcher_target" "$dispatcher_backup"
  dispatcher_existed=1
fi
if [[ -e "$sudoers_target" || -L "$sudoers_target" ]]; then
  validate_existing "$sudoers_target" 440 || fail
  /bin/cp -p "$sudoers_target" "$sudoers_backup"
  sudoers_existed=1
fi
/bin/rm -f -- "$sudoers_target"
"$visudo_bin" -cf "$sudoers_root" >/dev/null
/bin/rm -f -- "$dispatcher_target"
success=1
/usr/bin/printf '%s\n' AGENTOPS_CONTROL_REMOVE_OK
