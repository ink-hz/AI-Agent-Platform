#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "REPLICA_IMPORT_FAILED" >&2
  exit 1
}

if [[ -n "${SSH_ORIGINAL_COMMAND:-}" ]]; then
  fail
fi

compose_file="${CLOUD_PLATFORM_COMPOSE_FILE:-/opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml}"
environment_file="${CLOUD_PLATFORM_ENV_FILE:-/opt/orbbec-agent-platform/private/platform.env}"
if [[ ! -f "$compose_file" || ! -f "$environment_file" || -L "$environment_file" ]]; then
  fail
fi

if ! result="$(
  /usr/bin/docker compose \
    --env-file "$environment_file" \
    -f "$compose_file" \
    run --rm --no-deps -T \
    -v orbbec-agent-platform-import-secrets:/run/import-secrets:ro \
    -e PLATFORM_REPLICA_DATABASE_URL_FILE=/run/import-secrets/replica-database-url \
    -e PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/import-secrets/replica-encryption-key \
    -e PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE=/run/import-secrets/replica-signing-public-key \
    platform-api \
    python -m app.cloud_replica.cli import
)"; then
  fail
fi

if ! parsed="$(/usr/bin/python3 -c '
import json, re, sys
value = json.load(sys.stdin)
status = value.get("status")
sequence = value.get("sequence")
digest = value.get("digest")
if status not in {"imported", "replayed"} or type(sequence) is not int or sequence < 1:
    raise SystemExit(1)
if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
    raise SystemExit(1)
print(status, sequence, digest)
' <<< "$result")"; then
  fail
fi
read -r status sequence digest <<< "$parsed"
replayed=0
if [[ "$status" == "replayed" ]]; then
  replayed=1
fi
echo "REPLICA_IMPORT_OK sequence=$sequence digest=$digest replay=$replayed"
