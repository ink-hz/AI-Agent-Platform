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
agentops_group=staff
repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
dispatcher_relative=deploy/local-execution-worker/agentops-control.sh
sudoers_relative=deploy/local-execution-worker/agentops-control.sudoers
dispatcher_source="$repository_root/$dispatcher_relative"
sudoers_source="$repository_root/$sudoers_relative"
dispatcher_target=/Library/PrivilegedHelperTools/orbbec-agentops-control
sudoers_target=/etc/sudoers.d/orbbec-agentops-control
legacy_sudoers_target=/etc/sudoers.d/agentops-management
sudoers_root=/etc/sudoers
staging_root=/Users/neo/.orbbec-agent-platform/agentops-control
pending_private="$staging_root/cloud-admin-ed25519.pending"
pending_public="$staging_root/cloud-admin-ed25519.pending.pub"
pending_fingerprint="$staging_root/cloud-admin-ed25519.fingerprint"
pending_config="$staging_root/acceptance-config.pending.json"
pending_known_hosts="$staging_root/cloud-known-hosts.pending"
agentops_private=/Users/agentops/AgentRuntime/private
cloud_key_target="$agentops_private/cloud-admin-ed25519"
relay_config_target="$agentops_private/acceptance-config.json"
cloud_known_hosts_target="$agentops_private/cloud-known-hosts"
visudo_bin=/usr/sbin/visudo
sudo_bin=/usr/bin/sudo
git_bin=/usr/bin/git
install_bin=/usr/bin/install
ssh_keygen_bin=/usr/bin/ssh-keygen

[[ $# -eq 0 && "$(/usr/bin/id -u)" == "$required_uid" ]] || fail
for directory in "$(/usr/bin/dirname "$dispatcher_target")" "$(/usr/bin/dirname "$sudoers_target")"; do
  [[ -d "$directory" && ! -L "$directory" ]] || fail
done
for source_path in "$dispatcher_source" "$sudoers_source"; do
  [[ -f "$source_path" && ! -L "$source_path" ]] || fail
  [[ "$(/usr/bin/stat -f '%Su' "$source_path")" == "$source_user" ]] || fail
done
[[ -d "$staging_root" && ! -L "$staging_root" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$staging_root")" == "700 $source_user" ]] || fail
[[ -d "$agentops_private" && ! -L "$agentops_private" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$agentops_private")" == "700 $agentops_user $agentops_group" ]] || fail
[[ -f "$pending_fingerprint" && ! -L "$pending_fingerprint" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$pending_fingerprint")" == "600 $source_user" ]] || fail
expected_fingerprint="$(<"$pending_fingerprint")"
[[ "$expected_fingerprint" =~ ^SHA256:[A-Za-z0-9+/]+$ ]] || fail

pending_count=0
for pending_path in "$pending_private" "$pending_public" "$pending_config" "$pending_known_hosts"; do
  if [[ -e "$pending_path" || -L "$pending_path" ]]; then
    pending_count=$((pending_count + 1))
  fi
done
[[ "$pending_count" == 0 || "$pending_count" == 4 ]] || fail
staged_key=0
if [[ "$pending_count" == 4 ]]; then
  staged_key=1
  for pending_path in "$pending_private" "$pending_public" "$pending_config" "$pending_known_hosts"; do
    [[ -f "$pending_path" && ! -L "$pending_path" ]] || fail
    [[ "$(/usr/bin/stat -f '%Lp %Su' "$pending_path")" == "600 $source_user" ]] || fail
  done
fi

validate_config() {
  config_path="$1"
  /usr/bin/python3 - "$config_path" "$cloud_key_target" <<'PY'
import json,pathlib,sys
path=pathlib.Path(sys.argv[1]); key=sys.argv[2]
value=json.loads(path.read_bytes())
expected={"schema_version":1,"cloud_admin_host":"root@47.106.112.69","cloud_admin_key":key}
if value!=expected: raise SystemExit(1)
PY
}

validate_cloud_known_hosts() {
  known_hosts_path="$1"
  /usr/bin/python3 - "$known_hosts_path" <<'PY'
import pathlib,re,sys
lines=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if len(lines)!=1: raise SystemExit(1)
parts=lines[0].split()
if len(parts)!=3 or parts[:2]!=["47.106.112.69","ssh-ed25519"]: raise SystemExit(1)
if re.fullmatch(r"[A-Za-z0-9+/=]+",parts[2]) is None: raise SystemExit(1)
PY
}

validate_key_pair() {
  private_path="$1"
  public_path="$2"
  derived="$($ssh_keygen_bin -y -f "$private_path" | /usr/bin/awk 'NF>=2 {print $1 " " $2; exit}')" || return 1
  selected="$(/usr/bin/awk 'NF>=2 {print $1 " " $2; exit}' "$public_path")" || return 1
  [[ "$derived" == "$selected" ]] || return 1
  actual_fingerprint="$($ssh_keygen_bin -lf "$public_path" | /usr/bin/awk '{print $2}')" || return 1
  [[ "$actual_fingerprint" == "$expected_fingerprint" ]]
}

if [[ "$staged_key" == 1 ]]; then
  validate_key_pair "$pending_private" "$pending_public" || fail
  validate_config "$pending_config" || fail
  validate_cloud_known_hosts "$pending_known_hosts" || fail
else
  [[ -f "$cloud_key_target" && ! -L "$cloud_key_target" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$cloud_key_target")" == "600 $agentops_user $agentops_group" ]] || fail
  existing_public="$staging_root/.installed-key.pub"
  [[ ! -e "$existing_public" && ! -L "$existing_public" ]] || fail
  "$ssh_keygen_bin" -y -f "$cloud_key_target" > "$existing_public"
  /bin/chmod 600 "$existing_public"
  validate_key_pair "$cloud_key_target" "$existing_public" || { /bin/rm -f -- "$existing_public"; fail; }
  /bin/rm -f -- "$existing_public"
  [[ -f "$relay_config_target" && ! -L "$relay_config_target" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$relay_config_target")" == "600 $agentops_user $agentops_group" ]] || fail
  validate_config "$relay_config_target" || fail
  [[ -f "$cloud_known_hosts_target" && ! -L "$cloud_known_hosts_target" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$cloud_known_hosts_target")" == "600 $agentops_user $agentops_group" ]] || fail
  validate_cloud_known_hosts "$cloud_known_hosts_target" || fail
fi

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
cloud_key_backup="$transaction/cloud-key.backup"
relay_config_backup="$transaction/relay-config.backup"
cloud_known_hosts_backup="$transaction/cloud-known-hosts.backup"
legacy_sudoers_backup="$transaction/legacy-sudoers.backup"
dispatcher_candidate="$dispatcher_target.candidate.$$"
sudoers_candidate="$sudoers_target.candidate.$$"
cloud_key_candidate="$cloud_key_target.candidate.$$"
relay_config_candidate="$relay_config_target.candidate.$$"
cloud_known_hosts_candidate="$cloud_known_hosts_target.candidate.$$"
dispatcher_existed=0
sudoers_existed=0
cloud_key_existed=0
relay_config_existed=0
cloud_known_hosts_existed=0
legacy_sudoers_existed=0
success=0

validate_existing() {
  target="$1"
  expected_mode="$2"
  [[ -f "$target" && ! -L "$target" ]] || return 1
  [[ "$(/usr/bin/stat -f '%Su %Sg %Lp' "$target")" == "$target_owner $target_group $expected_mode" ]]
}

validate_legacy_sudoers() {
  [[ -f "$legacy_sudoers_target" && ! -L "$legacy_sudoers_target" ]] || return 1
  [[ "$(/usr/bin/stat -f '%Su %Sg %Lp %z' "$legacy_sudoers_target")" == "$target_owner $target_group 440 33" ]] || return 1
  [[ "$(<"$legacy_sudoers_target")" == "neo ALL=(agentops) NOPASSWD: ALL" ]]
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
    if [[ "$cloud_key_existed" == 1 ]]; then
      "$install_bin" -o "$agentops_user" -g "$agentops_group" -m 0600 "$cloud_key_backup" "$cloud_key_target" || status=1
    else
      /bin/rm -f -- "$cloud_key_target" || status=1
    fi
    if [[ "$relay_config_existed" == 1 ]]; then
      "$install_bin" -o "$agentops_user" -g "$agentops_group" -m 0600 "$relay_config_backup" "$relay_config_target" || status=1
    else
      /bin/rm -f -- "$relay_config_target" || status=1
    fi
    if [[ "$cloud_known_hosts_existed" == 1 ]]; then
      "$install_bin" -o "$agentops_user" -g "$agentops_group" -m 0600 "$cloud_known_hosts_backup" "$cloud_known_hosts_target" || status=1
    else
      /bin/rm -f -- "$cloud_known_hosts_target" || status=1
    fi
    if [[ "$legacy_sudoers_existed" == 1 ]]; then
      "$install_bin" -o "$target_owner" -g "$target_group" -m 0440 "$legacy_sudoers_backup" "$legacy_sudoers_target" || status=1
    fi
  fi
  /bin/rm -f -- "$dispatcher_candidate" "$sudoers_candidate" \
    "$cloud_key_candidate" "$relay_config_candidate" "$cloud_known_hosts_candidate" \
    "$dispatcher_backup" "$sudoers_backup" "$cloud_key_backup" \
    "$relay_config_backup" "$cloud_known_hosts_backup" "$legacy_sudoers_backup" || status=1
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
if [[ -e "$cloud_key_target" || -L "$cloud_key_target" ]]; then
  [[ -f "$cloud_key_target" && ! -L "$cloud_key_target" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$cloud_key_target")" == "600 $agentops_user $agentops_group" ]] || fail
  /bin/cp -p "$cloud_key_target" "$cloud_key_backup"
  cloud_key_existed=1
fi
if [[ -e "$relay_config_target" || -L "$relay_config_target" ]]; then
  [[ -f "$relay_config_target" && ! -L "$relay_config_target" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$relay_config_target")" == "600 $agentops_user $agentops_group" ]] || fail
  /bin/cp -p "$relay_config_target" "$relay_config_backup"
  relay_config_existed=1
fi
if [[ -e "$cloud_known_hosts_target" || -L "$cloud_known_hosts_target" ]]; then
  [[ -f "$cloud_known_hosts_target" && ! -L "$cloud_known_hosts_target" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su %Sg' "$cloud_known_hosts_target")" == "600 $agentops_user $agentops_group" ]] || fail
  validate_cloud_known_hosts "$cloud_known_hosts_target" || fail
  /bin/cp -p "$cloud_known_hosts_target" "$cloud_known_hosts_backup"
  cloud_known_hosts_existed=1
fi
if [[ -e "$legacy_sudoers_target" || -L "$legacy_sudoers_target" ]]; then
  validate_legacy_sudoers || fail
  /bin/cp -p "$legacy_sudoers_target" "$legacy_sudoers_backup"
  legacy_sudoers_existed=1
fi
[[ ! -e "$dispatcher_candidate" && ! -L "$dispatcher_candidate" ]] || fail
[[ ! -e "$sudoers_candidate" && ! -L "$sudoers_candidate" ]] || fail
"$install_bin" -o "$target_owner" -g "$target_group" -m 0755 "$dispatcher_source" "$dispatcher_candidate"
"$install_bin" -o "$target_owner" -g "$target_group" -m 0440 "$sudoers_source" "$sudoers_candidate"
if [[ "$staged_key" == 1 ]]; then
  [[ ! -e "$cloud_key_candidate" && ! -L "$cloud_key_candidate" ]] || fail
  [[ ! -e "$relay_config_candidate" && ! -L "$relay_config_candidate" ]] || fail
  [[ ! -e "$cloud_known_hosts_candidate" && ! -L "$cloud_known_hosts_candidate" ]] || fail
  "$install_bin" -o "$agentops_user" -g "$agentops_group" -m 0600 "$pending_private" "$cloud_key_candidate"
  "$install_bin" -o "$agentops_user" -g "$agentops_group" -m 0600 "$pending_config" "$relay_config_candidate"
  "$install_bin" -o "$agentops_user" -g "$agentops_group" -m 0600 "$pending_known_hosts" "$cloud_known_hosts_candidate"
fi
"$visudo_bin" -cf "$sudoers_candidate" >/dev/null
/bin/mv -f "$dispatcher_candidate" "$dispatcher_target"
/bin/mv -f "$sudoers_candidate" "$sudoers_target"
if [[ "$staged_key" == 1 ]]; then
  /bin/mv -f "$cloud_key_candidate" "$cloud_key_target"
  /bin/mv -f "$relay_config_candidate" "$relay_config_target"
  /bin/mv -f "$cloud_known_hosts_candidate" "$cloud_known_hosts_target"
fi
if [[ "$legacy_sudoers_existed" == 1 ]]; then
  validate_legacy_sudoers || fail
  /bin/rm -f -- "$legacy_sudoers_target"
else
  [[ ! -e "$legacy_sudoers_target" && ! -L "$legacy_sudoers_target" ]] || fail
fi
"$visudo_bin" -cf "$sudoers_root" >/dev/null
status_output="$($sudo_bin -n -H -u "$agentops_user" "$dispatcher_target" status)" || fail
[[ "$status_output" == "AGENTOPS_CONTROL_OK commands=7" ]] || fail
if [[ "$staged_key" == 1 ]]; then
  /bin/rm -f -- "$pending_private" "$pending_public" "$pending_config" "$pending_known_hosts"
fi
success=1
/usr/bin/printf '%s\n' AGENTOPS_CONTROL_INSTALL_OK
