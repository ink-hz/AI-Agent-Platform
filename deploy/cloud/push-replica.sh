#!/bin/bash
set -euo pipefail
umask 077

stable_failure() {
  echo "REPLICA_PUSH_FAILED" >&2
}

if [[ $# -ne 1 || "$1" != /* || ! -f "$1" || -L "$1" ]]; then
  stable_failure
  exit 1
fi
config_path="$1"
config_mode="$(/usr/bin/stat -f '%Lp' "$config_path" 2>/dev/null || true)"
config_owner="$(/usr/bin/stat -f '%u' "$config_path" 2>/dev/null || true)"
if [[ "$config_mode" != "600" || "$config_owner" != "$(/usr/bin/id -u)" ]]; then
  stable_failure
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$config_path"
set +a

required_names=(
  CLOUD_SYNC_SSH_HOST CLOUD_SYNC_SSH_KEY CLOUD_SYNC_QUEUE_DIR
  CLOUD_SYNC_BACKEND_DIR CLOUD_SYNC_PYTHON
)
for required_name in "${required_names[@]}"; do
  if [[ -z "${!required_name:-}" ]]; then
    stable_failure
    exit 1
  fi
done
if [[ "$CLOUD_SYNC_SSH_KEY" != /* || "$CLOUD_SYNC_QUEUE_DIR" != /* ||
      "$CLOUD_SYNC_BACKEND_DIR" != /* || "$CLOUD_SYNC_PYTHON" != /* ||
      ! -f "$CLOUD_SYNC_SSH_KEY" || -L "$CLOUD_SYNC_SSH_KEY" ]]; then
  stable_failure
  exit 1
fi
key_mode="$(/usr/bin/stat -f '%Lp' "$CLOUD_SYNC_SSH_KEY" 2>/dev/null || true)"
if [[ "$key_mode" != "600" ]]; then
  stable_failure
  exit 1
fi

export_failed=0
if ! (
  cd "$CLOUD_SYNC_BACKEND_DIR"
  "$CLOUD_SYNC_PYTHON" -m app.cloud_replica.cli export >/dev/null
); then
  export_failed=1
fi

batch_path=""
for candidate in "$CLOUD_SYNC_QUEUE_DIR"/batch-*.jsonl; do
  if [[ -f "$candidate" && ! -L "$candidate" ]]; then
    batch_path="$candidate"
    break
  fi
done
if [[ -z "$batch_path" ]]; then
  if [[ "$export_failed" -eq 1 ]]; then
    stable_failure
    exit 1
  fi
  exit 0
fi

if ! batch_identity="$("$CLOUD_SYNC_PYTHON" - "$batch_path" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
lines = path.read_bytes().splitlines()
if len(lines) < 2:
    raise SystemExit(1)
header = json.loads(lines[0])
trailer = json.loads(lines[-1])
sequence = header.get("sequence")
digest = trailer.get("digest")
if type(sequence) is not int or sequence < 1 or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
    raise SystemExit(1)
print(sequence, digest)
PY
)"; then
  stable_failure
  exit 1
fi
read -r expected_sequence expected_digest <<< "$batch_identity"

if ! acknowledgement="$(
  /usr/bin/ssh \
    -i "$CLOUD_SYNC_SSH_KEY" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o ConnectTimeout=8 \
    -o StrictHostKeyChecking=yes \
    "$CLOUD_SYNC_SSH_HOST" < "$batch_path"
)"; then
  stable_failure
  exit 1
fi

case "$acknowledgement" in
  "REPLICA_IMPORT_OK sequence=$expected_sequence digest=$expected_digest replay=0"|\
  "REPLICA_IMPORT_OK sequence=$expected_sequence digest=$expected_digest replay=1") ;;
  *)
    stable_failure
    exit 1
    ;;
esac

rm -f -- "$batch_path"
if [[ "$export_failed" -eq 1 ]]; then
  stable_failure
  exit 1
fi
echo "REPLICA_PUSH_OK sequence=$expected_sequence"
