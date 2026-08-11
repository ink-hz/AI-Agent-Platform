#!/bin/bash
set -euo pipefail
umask 077

if [[ $# -ne 1 || "$1" != /* || "$1" == "/" ]]; then
  echo "KEY_BOOTSTRAP_FAILED" >&2
  exit 1
fi
private_root="$1"
/bin/mkdir -p "$private_root"
/bin/chmod 700 "$private_root"
script_dir="$(cd "$(dirname "$0")" && pwd)"
repository_root="$(cd "$script_dir/../.." && pwd)"
crypto_python="${CLOUD_BOOTSTRAP_PYTHON:-$repository_root/backend/.venv/bin/python}"
if [[ "$crypto_python" != /* || ! -x "$crypto_python" ]]; then
  echo "KEY_BOOTSTRAP_FAILED" >&2
  exit 1
fi

identity_key="$private_root/identity-hmac.key"
signing_private="$private_root/replica-signing-private.key"
signing_public="$private_root/replica-signing-public.key"
ssh_private="$private_root/replica-transport-ed25519"
backup_private="$private_root/backup-recovery-x25519.key"
backup_public="$private_root/backup-recovery-x25519.pub"

if [[ ! -e "$identity_key" ]]; then
  /usr/bin/openssl rand 32 > "$identity_key"
fi
if [[ ! -e "$signing_private" ]]; then
  "$crypto_python" - "$signing_private" "$signing_public" <<'PY'
import os
import pathlib
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

private_path, public_path = map(pathlib.Path, sys.argv[1:])
key = Ed25519PrivateKey.generate()
private_path.write_bytes(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
public_path.write_bytes(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
os.chmod(private_path, 0o600)
os.chmod(public_path, 0o600)
PY
fi
if [[ ! -e "$signing_public" ]]; then
  "$crypto_python" - "$signing_private" "$signing_public" <<'PY'
import os
import pathlib
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

private_path, public_path = map(pathlib.Path, sys.argv[1:])
key = Ed25519PrivateKey.from_private_bytes(private_path.read_bytes())
public_path.write_bytes(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
os.chmod(public_path, 0o600)
PY
fi
if [[ ! -e "$ssh_private" ]]; then
  /usr/bin/ssh-keygen -q -t ed25519 -N "" -C "orbbec-platform-replica" -f "$ssh_private"
fi
if [[ ! -e "$backup_private" ]]; then
  "$crypto_python" - "$backup_private" "$backup_public" <<'PY'
import os
import pathlib
import sys
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

private_path, public_path = map(pathlib.Path, sys.argv[1:])
key = X25519PrivateKey.generate()
private_path.write_bytes(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
public_path.write_bytes(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
os.chmod(private_path, 0o600)
os.chmod(public_path, 0o600)
PY
fi
if [[ ! -e "$backup_public" ]]; then
  "$crypto_python" - "$backup_private" "$backup_public" <<'PY'
import os
import pathlib
import sys
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

private_path, public_path = map(pathlib.Path, sys.argv[1:])
key = X25519PrivateKey.from_private_bytes(private_path.read_bytes())
public_path.write_bytes(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
os.chmod(public_path, 0o600)
PY
fi

for raw_key in "$identity_key" "$signing_private" "$signing_public" "$backup_private" "$backup_public"; do
  [[ -f "$raw_key" && ! -L "$raw_key" && "$(/usr/bin/stat -f '%z' "$raw_key")" == "32" ]] || {
    echo "KEY_BOOTSTRAP_FAILED" >&2
    exit 1
  }
  /bin/chmod 600 "$raw_key"
done
for private_key in "$ssh_private"; do
  [[ -f "$private_key" && ! -L "$private_key" ]] || {
    echo "KEY_BOOTSTRAP_FAILED" >&2
    exit 1
  }
  /bin/chmod 600 "$private_key"
done

# The remote AES storage key is generated independently on the cloud host.
ssh_public="$(<"$ssh_private.pub")"
echo "AUTHORIZED_KEY=restrict,command=\"/opt/orbbec-agent-platform/current/deploy/cloud/forced-import.sh\",no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding $ssh_public"
echo "SIGNING_PUBLIC_BASE64=$(base64 < "$signing_public" | tr -d '\n')"
echo "SSH_FINGERPRINT=$(/usr/bin/ssh-keygen -lf "$ssh_private.pub" | /usr/bin/awk '{print $2}')"
echo "BACKUP_FINGERPRINT=$(/usr/bin/shasum -a 256 "$backup_public" | /usr/bin/awk '{print $1}')"
