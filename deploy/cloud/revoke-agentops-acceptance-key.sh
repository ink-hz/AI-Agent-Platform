#!/bin/bash
set -eEuo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' AGENTOPS_ACCEPTANCE_KEY_REVOKE_FAILED >&2
  exit 1
}

required_user=neo
state_root=/Users/neo/.orbbec-agent-platform/agentops-control
fingerprint_file="$state_root/cloud-admin-ed25519.fingerprint"
cloud_admin_host=root@47.106.112.69
cloud_admin_key=/Users/neo/.ssh/orbbec_aliyun_ed25519
ssh_bin=/usr/bin/ssh

[[ $# -eq 0 && "$(/usr/bin/id -un)" == "$required_user" ]] || fail
for private_file in "$fingerprint_file" "$cloud_admin_key"; do
  [[ -f "$private_file" && ! -L "$private_file" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$private_file")" == "600 $required_user" ]] || fail
done
fingerprint="$(<"$fingerprint_file")"
[[ "$fingerprint" =~ ^SHA256:[A-Za-z0-9+/]+$ ]] || fail
"$ssh_bin" -i "$cloud_admin_key" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o ConnectTimeout=8 -o StrictHostKeyChecking=yes \
  "$cloud_admin_host" /bin/bash -s -- "$fingerprint" <<'REMOTE'
set -euo pipefail
umask 077
fingerprint="$1"; path=/root/.ssh/authorized_keys
begin='# BEGIN ORBBEC AGENTOPS ACCEPTANCE KEY'; end='# END ORBBEC AGENTOPS ACCEPTANCE KEY'
/usr/bin/python3 - "$path" "$begin" "$end" "$fingerprint" <<'PY'
import os,pathlib,stat,sys,tempfile
path=pathlib.Path(sys.argv[1]); begin,end,fingerprint=sys.argv[2:]
meta=path.lstat()
if path.is_symlink() or not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0: raise SystemExit(1)
raw=path.read_text(encoding="utf-8")
if raw.count(begin)!=1 or raw.count(end)!=1: raise SystemExit(1)
start=raw.index(begin); finish=raw.index(end,start)+len(end)
block=raw[start:finish]
if f'orbbec-agentops-acceptance:{fingerprint}' not in block: raise SystemExit(1)
if finish<len(raw) and raw[finish:finish+1]=="\n": finish+=1
payload=(raw[:start]+raw[finish:]).encode()
fd,temp=tempfile.mkstemp(prefix=".authorized_keys.",dir=path.parent)
try:
    os.fchmod(fd,stat.S_IMODE(meta.st_mode))
    with os.fdopen(fd,"wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.chown(temp,0,0); os.replace(temp,path); os.chmod(path,stat.S_IMODE(meta.st_mode))
finally:
    try: os.unlink(temp)
    except FileNotFoundError: pass
PY
REMOTE
/usr/bin/printf '%s\n' AGENTOPS_ACCEPTANCE_KEY_REVOKED_OK
