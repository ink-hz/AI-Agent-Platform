#!/bin/bash
set -eEuo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' AGENTOPS_ACCEPTANCE_KEY_STAGE_FAILED >&2
  exit 1
}

required_user=neo
state_root=/Users/neo/.orbbec-agent-platform/agentops-control
cloud_admin_host=root@47.106.112.69
cloud_admin_key=/Users/neo/.ssh/orbbec_aliyun_ed25519
ssh_bin=/usr/bin/ssh
ssh_keygen_bin=/usr/bin/ssh-keygen
pending_private="$state_root/cloud-admin-ed25519.pending"
pending_public="$state_root/cloud-admin-ed25519.pending.pub"
fingerprint_file="$state_root/cloud-admin-ed25519.fingerprint"
pending_config="$state_root/acceptance-config.pending.json"

[[ $# -eq 0 && "$(/usr/bin/id -un)" == "$required_user" ]] || fail
[[ -f "$cloud_admin_key" && ! -L "$cloud_admin_key" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$cloud_admin_key")" == "600 $required_user" ]] || fail
if [[ -e "$state_root" || -L "$state_root" ]]; then
  [[ -d "$state_root" && ! -L "$state_root" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$state_root")" == "700 $required_user" ]] || fail
else
  /bin/mkdir -m 700 "$state_root"
fi
for target in "$pending_private" "$pending_public" "$pending_config"; do
  [[ ! -e "$target" && ! -L "$target" ]] || fail
done

temporary="$(/usr/bin/mktemp -d "$state_root/.key-stage.XXXXXX")" || fail
/bin/chmod 700 "$temporary"
generated="$temporary/cloud-admin-ed25519"
success=0
published_local=0
remote_transaction_active=0
fingerprint_existed=0
if [[ -e "$fingerprint_file" || -L "$fingerprint_file" ]]; then
  [[ -f "$fingerprint_file" && ! -L "$fingerprint_file" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$fingerprint_file")" == "600 $required_user" ]] || fail
  /bin/cp -p "$fingerprint_file" "$temporary/previous-fingerprint"
  fingerprint_existed=1
fi
cleanup() {
  status="$?"
  trap - ERR EXIT
  if [[ "$status" != 0 && "$remote_transaction_active" == 1 ]]; then
    rollback_remote_transaction >/dev/null 2>&1 || status=1
  fi
  if [[ "$status" != 0 && "$published_local" == 1 ]]; then
    /bin/rm -f -- "$pending_private" "$pending_public" "$pending_config" "$fingerprint_file" || status=1
    if [[ "$fingerprint_existed" == 1 ]]; then
      /bin/mv "$temporary/previous-fingerprint" "$fingerprint_file" || status=1
    fi
  fi
  /bin/rm -f -- "$generated" "$generated.pub" \
    "$temporary/fingerprint" "$temporary/acceptance-config.json" \
    "$temporary/previous-fingerprint" || status=1
  /bin/rmdir "$temporary" >/dev/null 2>&1 || status=1
  exit "$status"
}
trap cleanup ERR EXIT

"$ssh_keygen_bin" -q -t ed25519 -N "" -C orbbec-agentops-acceptance -f "$generated"
/bin/chmod 600 "$generated" "$generated.pub"
public_line="$(<"$generated.pub")" || fail
[[ "$public_line" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+\ orbbec-agentops-acceptance$ ]] || fail
fingerprint="$("$ssh_keygen_bin" -lf "$generated.pub" | /usr/bin/awk '{print $2}')" || fail
[[ "$fingerprint" =~ ^SHA256:[A-Za-z0-9+/]+$ ]] || fail

ssh_options=(
  -i "$cloud_admin_key"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)
transaction_token="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
[[ "$transaction_token" =~ ^[0-9a-f-]{36}$ ]] || fail

prepare_remote_transaction() {
  "$ssh_bin" "${ssh_options[@]}" "$cloud_admin_host" /bin/bash -s -- \
    "$transaction_token" "$public_line" "$fingerprint" <<'REMOTE'
set -euo pipefail
umask 077
token="$1"; public_line="$2"; fingerprint="$3"
source_ip="${SSH_CONNECTION%% *}"
/usr/bin/python3 - "$source_ip" <<'PY'
import ipaddress,sys
ipaddress.ip_address(sys.argv[1])
PY
authorized_key_options="restrict,from=\"$source_ip\""
ssh_root=/root/.ssh
authorized_keys="$ssh_root/authorized_keys"
transaction=/opt/orbbec-agent-platform/private/agentops-acceptance-key.transaction
begin='# BEGIN ORBBEC AGENTOPS ACCEPTANCE KEY'
end='# END ORBBEC AGENTOPS ACCEPTANCE KEY'
[[ "$token" =~ ^[0-9a-f-]{36}$ && ! -e "$transaction" && ! -L "$transaction" ]] || exit 1
mkdir -m 700 "$transaction"
prepared=0
cleanup_prepare() {
  status="$?"; trap - EXIT
  if [[ "$prepared" != 1 ]]; then
    rm -f -- "$transaction/authorized_keys.backup" "$transaction/state" \
      "$transaction/token" "$transaction/prepared.sha256"
    rmdir "$transaction" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup_prepare EXIT
printf '%s\n' "$token" > "$transaction/token"; chmod 600 "$transaction/token"
mkdir -p "$ssh_root"; chmod 700 "$ssh_root"
if [[ -e "$authorized_keys" || -L "$authorized_keys" ]]; then
  [[ -f "$authorized_keys" && ! -L "$authorized_keys" ]] || exit 1
  cp -p "$authorized_keys" "$transaction/authorized_keys.backup"
  printf '%s\n' present > "$transaction/state"
else
  printf '%s\n' absent > "$transaction/state"
fi
chmod 600 "$transaction/state"
/usr/bin/python3 - "$authorized_keys" "$begin" "$end" "$authorized_key_options" "$public_line" "$fingerprint" <<'PY'
import os,pathlib,stat,sys,tempfile
path=pathlib.Path(sys.argv[1]); begin,end,options,public_line,fingerprint=sys.argv[2:]
if path.exists() or path.is_symlink():
    meta=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0:
        raise SystemExit(1)
    raw=path.read_text(encoding="utf-8")
    mode=stat.S_IMODE(meta.st_mode)
else:
    raw=""; mode=0o600
if raw.count(begin)>1 or raw.count(end)>1 or raw.count(begin)!=raw.count(end):
    raise SystemExit(1)
if begin in raw:
    start=raw.index(begin); finish=raw.index(end,start)+len(end)
    if finish < len(raw) and raw[finish:finish+1]=="\n": finish += 1
    raw=raw[:start]+raw[finish:]
parts=public_line.split()
if len(parts)<2 or parts[0]!="ssh-ed25519": raise SystemExit(1)
managed=f'{begin}\n{options} {parts[0]} {parts[1]} orbbec-agentops-acceptance:{fingerprint}\n{end}\n'
if raw and not raw.endswith("\n"): raw += "\n"
payload=(raw+managed).encode()
fd,temp=tempfile.mkstemp(prefix=".authorized_keys.",dir=path.parent)
try:
    os.fchmod(fd,mode)
    with os.fdopen(fd,"wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.chown(temp,0,0); os.replace(temp,path); os.chmod(path,mode)
    directory=os.open(path.parent,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    try: os.unlink(temp)
    except FileNotFoundError: pass
PY
sha256sum "$authorized_keys" | awk '{print $1}' > "$transaction/prepared.sha256"
chmod 600 "$transaction/prepared.sha256"
prepared=1
trap - EXIT
REMOTE
}

rollback_remote_transaction() {
  "$ssh_bin" "${ssh_options[@]}" "$cloud_admin_host" /bin/bash -s -- \
    "$transaction_token" <<'REMOTE'
set -euo pipefail
token="$1"; transaction=/opt/orbbec-agent-platform/private/agentops-acceptance-key.transaction
authorized_keys=/root/.ssh/authorized_keys
[[ -d "$transaction" && ! -L "$transaction" && "$(cat "$transaction/token")" == "$token" ]] || exit 1
[[ "$(sha256sum "$authorized_keys" | awk '{print $1}')" == "$(cat "$transaction/prepared.sha256")" ]] || exit 1
state="$(cat "$transaction/state")"
if [[ "$state" == present ]]; then
  cp -p "$transaction/authorized_keys.backup" "$authorized_keys"
elif [[ "$state" == absent ]]; then
  rm -f -- "$authorized_keys"
else
  exit 1
fi
rm -f -- "$transaction/authorized_keys.backup" "$transaction/state" \
  "$transaction/token" "$transaction/prepared.sha256"
rmdir "$transaction"
REMOTE
}

commit_remote_transaction() {
  "$ssh_bin" "${ssh_options[@]}" "$cloud_admin_host" /bin/bash -s -- \
    "$transaction_token" "$fingerprint" <<'REMOTE'
set -euo pipefail
token="$1"; fingerprint="$2"
transaction=/opt/orbbec-agent-platform/private/agentops-acceptance-key.transaction
authorized_keys=/root/.ssh/authorized_keys
[[ -d "$transaction" && ! -L "$transaction" && "$(cat "$transaction/token")" == "$token" ]] || exit 1
[[ "$(sha256sum "$authorized_keys" | awk '{print $1}')" == "$(cat "$transaction/prepared.sha256")" ]] || exit 1
[[ "$(grep -Fc "orbbec-agentops-acceptance:$fingerprint" "$authorized_keys")" == 1 ]] || exit 1
rm -f -- "$transaction/authorized_keys.backup" "$transaction/state" \
  "$transaction/token" "$transaction/prepared.sha256"
rmdir "$transaction"
REMOTE
}

prepare_remote_transaction
remote_transaction_active=1

dedicated_options=(
  -i "$generated"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)
"$ssh_bin" "${dedicated_options[@]}" "$cloud_admin_host" /usr/bin/true
/usr/bin/printf '%s\n' "$fingerprint" > "$temporary/fingerprint"
/bin/chmod 600 "$temporary/fingerprint"
/usr/bin/python3 - "$temporary/acceptance-config.json" <<'PY'
import json,os,pathlib,sys
path=pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "schema_version":1,
    "cloud_admin_host":"root@47.106.112.69",
    "cloud_admin_key":"/Users/agentops/AgentRuntime/private/cloud-admin-ed25519",
},sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
os.chmod(path,0o600)
PY
/bin/mv "$generated" "$pending_private"
/bin/mv "$generated.pub" "$pending_public"
/bin/mv "$temporary/fingerprint" "$fingerprint_file"
/bin/mv "$temporary/acceptance-config.json" "$pending_config"
/bin/chmod 600 "$pending_private" "$pending_public" "$fingerprint_file" "$pending_config"
published_local=1
commit_remote_transaction
remote_transaction_active=0
success=1
/usr/bin/printf '%s\n' AGENTOPS_ACCEPTANCE_KEY_STAGED_OK
