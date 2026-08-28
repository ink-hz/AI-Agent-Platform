#!/bin/bash
set -eEuo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' AGENTOPS_CONTROL_INSTALL_FAILED >&2
  exit 1
}

required_uid=0
source_user=neo
target_owner=root
target_group=wheel
agentops_user=agentops
repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
dispatcher_relative=deploy/local-execution-worker/agentops-control.sh
sudoers_relative=deploy/local-execution-worker/agentops-control.sudoers
dispatcher_source="$repository_root/$dispatcher_relative"
sudoers_source="$repository_root/$sudoers_relative"
dispatcher_target=/Library/PrivilegedHelperTools/orbbec-agentops-control
sudoers_target=/etc/sudoers.d/orbbec-agentops-control
sudoers_root=/etc/sudoers
visudo_bin=/usr/sbin/visudo
sudo_bin=/usr/bin/sudo
git_bin=/usr/bin/git
install_bin=/usr/bin/install

[[ $# -eq 0 && "$(/usr/bin/id -u)" == "$required_uid" ]] || fail
for directory in "$(/usr/bin/dirname "$dispatcher_target")" "$(/usr/bin/dirname "$sudoers_target")"; do
  [[ -d "$directory" && ! -L "$directory" ]] || fail
done
for source_path in "$dispatcher_source" "$sudoers_source"; do
  [[ -f "$source_path" && ! -L "$source_path" ]] || fail
  [[ "$(/usr/bin/stat -f '%Su' "$source_path")" == "$source_user" ]] || fail
done

verify_tracked_source() {
  relative="$1"
  source_path="$repository_root/$relative"
  expected="$($git_bin -c safe.directory="$repository_root" -C "$repository_root" show "HEAD:$relative" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')" || return 1
  actual="$(/usr/bin/shasum -a 256 "$source_path" | /usr/bin/awk '{print $1}')" || return 1
  [[ "$expected" =~ ^[0-9a-f]{64}$ && "$actual" == "$expected" ]]
}
verify_tracked_source "$dispatcher_relative" || fail
verify_tracked_source "$sudoers_relative" || fail

transaction="$(/usr/bin/mktemp -d "${TMPDIR:-/private/var/tmp}/orbbec-agentops-control.XXXXXX")" || fail
/bin/chmod 700 "$transaction"
dispatcher_backup="$transaction/dispatcher.backup"
sudoers_backup="$transaction/sudoers.backup"
dispatcher_candidate="$dispatcher_target.candidate.$$"
sudoers_candidate="$sudoers_target.candidate.$$"
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
    else
      /bin/rm -f -- "$dispatcher_target" || status=1
    fi
    if [[ "$sudoers_existed" == 1 ]]; then
      "$install_bin" -o "$target_owner" -g "$target_group" -m 0440 "$sudoers_backup" "$sudoers_target" || status=1
    else
      /bin/rm -f -- "$sudoers_target" || status=1
    fi
  fi
  /bin/rm -f -- "$dispatcher_candidate" "$sudoers_candidate" \
    "$dispatcher_backup" "$sudoers_backup" || status=1
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
[[ ! -e "$dispatcher_candidate" && ! -L "$dispatcher_candidate" ]] || fail
[[ ! -e "$sudoers_candidate" && ! -L "$sudoers_candidate" ]] || fail
"$install_bin" -o "$target_owner" -g "$target_group" -m 0755 "$dispatcher_source" "$dispatcher_candidate"
"$install_bin" -o "$target_owner" -g "$target_group" -m 0440 "$sudoers_source" "$sudoers_candidate"
"$visudo_bin" -cf "$sudoers_candidate" >/dev/null
/bin/mv -f "$dispatcher_candidate" "$dispatcher_target"
/bin/mv -f "$sudoers_candidate" "$sudoers_target"
"$visudo_bin" -cf "$sudoers_root" >/dev/null
status_output="$($sudo_bin -n -H -u "$agentops_user" "$dispatcher_target" status)" || fail
[[ "$status_output" == "AGENTOPS_CONTROL_OK commands=6" ]] || fail
success=1
/usr/bin/printf '%s\n' AGENTOPS_CONTROL_INSTALL_OK
