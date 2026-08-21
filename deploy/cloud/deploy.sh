#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "CLOUD_PLATFORM_DEPLOY_FAILED" >&2
  exit 1
}

if [[ $# -ne 1 || "$1" != /* || ! -f "$1" || -L "$1" ]]; then
  fail
fi
config_path="$1"
if [[ "$(/usr/bin/stat -f '%Lp' "$config_path" 2>/dev/null || true)" != "600" ||
      "$(/usr/bin/stat -f '%u' "$config_path" 2>/dev/null || true)" != "$(/usr/bin/id -u)" ]]; then
  fail
fi

set -a
# shellcheck disable=SC1090
source "$config_path"
set +a
for required_name in CLOUD_ADMIN_HOST CLOUD_ADMIN_KEY CLOUD_SIGNING_PUBLIC_KEY CLOUD_BACKUP_PUBLIC_KEY CLOUD_CONTENT_ENCRYPTION_KEYRING CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING; do
  [[ -n "${!required_name:-}" ]] || fail
done
if [[ "$CLOUD_ADMIN_KEY" != /* || "$CLOUD_SIGNING_PUBLIC_KEY" != /* || "$CLOUD_BACKUP_PUBLIC_KEY" != /* ||
      "$CLOUD_CONTENT_ENCRYPTION_KEYRING" != /* || "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" != /* ||
      ! -f "$CLOUD_ADMIN_KEY" || -L "$CLOUD_ADMIN_KEY" ||
      ! -f "$CLOUD_SIGNING_PUBLIC_KEY" || -L "$CLOUD_SIGNING_PUBLIC_KEY" ||
      ! -f "$CLOUD_BACKUP_PUBLIC_KEY" || -L "$CLOUD_BACKUP_PUBLIC_KEY" ||
      ! -f "$CLOUD_CONTENT_ENCRYPTION_KEYRING" || -L "$CLOUD_CONTENT_ENCRYPTION_KEYRING" ||
      ! -f "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" || -L "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_ADMIN_KEY")" != "600" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_SIGNING_PUBLIC_KEY")" != "600" ||
      "$(/usr/bin/stat -f '%z' "$CLOUD_SIGNING_PUBLIC_KEY")" != "32" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_CONTENT_ENCRYPTION_KEYRING")" != "600" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING")" != "600" ||
      "$(/usr/bin/stat -f '%Lp' "$CLOUD_BACKUP_PUBLIC_KEY")" != "600" ||
      "$(/usr/bin/stat -f '%z' "$CLOUD_BACKUP_PUBLIC_KEY")" != "32" ]]; then
  fail
fi

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$repository_root"
[[ -z "$(git status --porcelain)" ]] || fail
release_sha="$(git rev-parse HEAD)"
remote_master_sha="$(git rev-parse refs/remotes/origin/master 2>/dev/null || true)"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
[[ "$release_sha" == "$remote_master_sha" ]] || fail

artifact_root="$(mktemp -d)"
cleanup() { /bin/rm -rf -- "$artifact_root"; }
trap cleanup EXIT
source_root="$artifact_root/source"
/bin/mkdir -p "$source_root"
git archive "$release_sha" | /usr/bin/tar -x -C "$source_root"
"$repository_root/backend/.venv/bin/python" - "$source_root" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
lines = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    relative = path.relative_to(root).as_posix()
    if relative == "MANIFEST.sha256":
        continue
    lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
(root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
artifact_path="$artifact_root/platform-$release_sha.tar.gz"
/usr/bin/tar -czf "$artifact_path" -C "$source_root" .
artifact_digest="$(/usr/bin/shasum -a 256 "$artifact_path" | /usr/bin/awk '{print $1}')"

ssh_options=(
  -i "$CLOUD_ADMIN_KEY"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=yes
)

if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/bin; /bin/cat > /opt/orbbec-agent-platform/bin/remote-stage.sh.part; chmod 700 /opt/orbbec-agent-platform/bin/remote-stage.sh.part; mv -f /opt/orbbec-agent-platform/bin/remote-stage.sh.part /opt/orbbec-agent-platform/bin/remote-stage.sh' \
  < "$repository_root/deploy/cloud/remote-stage.sh"; then
  fail
fi
if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/private; /bin/cat > /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub.part; chmod 600 /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub.part; mv -f /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub.part /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub' \
  < "$CLOUD_BACKUP_PUBLIC_KEY"; then
  fail
fi
if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/private; /bin/cat > /opt/orbbec-agent-platform/private/replica-signing-public-key.part; chmod 600 /opt/orbbec-agent-platform/private/replica-signing-public-key.part; mv -f /opt/orbbec-agent-platform/private/replica-signing-public-key.part /opt/orbbec-agent-platform/private/replica-signing-public-key' \
  < "$CLOUD_SIGNING_PUBLIC_KEY"; then
  fail
fi
if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/private; /bin/cat > /opt/orbbec-agent-platform/private/content-encryption-keyring.part; chmod 600 /opt/orbbec-agent-platform/private/content-encryption-keyring.part; mv -f /opt/orbbec-agent-platform/private/content-encryption-keyring.part /opt/orbbec-agent-platform/private/content-encryption-keyring' \
  < "$CLOUD_CONTENT_ENCRYPTION_KEYRING"; then
  fail
fi
if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  'umask 077; install -d -m 700 /opt/orbbec-agent-platform/private; /bin/cat > /opt/orbbec-agent-platform/private/execution-worker-public-keyring.json.part; chmod 600 /opt/orbbec-agent-platform/private/execution-worker-public-keyring.json.part; mv -f /opt/orbbec-agent-platform/private/execution-worker-public-keyring.json.part /opt/orbbec-agent-platform/private/execution-worker-public-keyring.json' \
  < "$CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING"; then
  fail
fi
if ! /usr/bin/ssh "${ssh_options[@]}" "$CLOUD_ADMIN_HOST" \
  "/opt/orbbec-agent-platform/bin/remote-stage.sh" "$release_sha" "$artifact_digest" \
  < "$artifact_path"; then
  fail
fi
