#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "DINGTALK_PRODUCTION_SECRETS_FAILED" >&2
  exit 1
}

[[ "$(${ID_BIN:-/usr/bin/id} -u)" == "0" && $# -eq 1 ]] || fail
private_path="$1"
[[ "$private_path" == /* && "$private_path" != / ]] || fail
[[ -d "$private_path" && ! -L "$private_path" ]] || fail

required_private=(
  dingtalk-app-key
  dingtalk-agent-id
  dingtalk-corp-id
  dingtalk-app-secret
  control-database-url
  control-audit-database-url
  control-directory-worker-database-url
  control-stream-ingest-database-url
  replica-database-url
  replica-encryption-key
  replica-signing-public-key
)
for name in "${required_private[@]}"; do
  path="$private_path/$name"
  [[ -f "$path" && ! -L "$path" ]] || fail
  [[ "$(/usr/bin/stat -c '%a %U' "$path")" == "600 root" ]] || fail
  [[ -s "$path" ]] || fail
done

write_keyring() {
  local destination="$1" purpose="$2" raw_file="$3" transition="$4" encoded
  encoded="$(/usr/bin/base64 -w 0 < "$raw_file")"
  if [[ "$transition" == "1" ]]; then
    /usr/bin/printf '{"purpose":"%s","active_version":1,"keys":{"1":"%s"},"transition_versions":[1]}\n' \
      "$purpose" "$encoded" > "$destination.part"
  else
    /usr/bin/printf '{"purpose":"%s","active_version":1,"keys":{"1":"%s"}}\n' \
      "$purpose" "$encoded" > "$destination.part"
  fi
  /bin/chown root:root "$destination.part"
  /bin/chmod 600 "$destination.part"
  /bin/mv -f "$destination.part" "$destination"
}

identity_encryption="$private_path/identity-encryption-keyring"
identity_hmac="$private_path/identity-hmac-keyring"
rate_hmac="$private_path/rate-limit-hmac-keyring"
owner_receipt="$private_path/owner-receipt-keyring"
temporary="$(/usr/bin/mktemp -d "$private_path/.identity-keys.XXXXXX")"
cleanup() {
  /bin/rm -f -- "$temporary/encryption.raw" "$temporary/identity-hmac.raw" \
    "$temporary/rate-hmac.raw" "$temporary/owner-receipt.raw"
  /bin/rmdir "$temporary" 2>/dev/null || true
}
trap cleanup EXIT

if [[ ! -e "$identity_encryption" ]]; then
  /usr/bin/openssl rand 32 > "$temporary/encryption.raw"
  write_keyring "$identity_encryption" provider-encryption "$temporary/encryption.raw" 0
fi
if [[ ! -e "$identity_hmac" ]]; then
  /usr/bin/openssl rand 32 > "$temporary/identity-hmac.raw"
  write_keyring "$identity_hmac" provider-lookup-hmac "$temporary/identity-hmac.raw" 1
fi
if [[ ! -e "$rate_hmac" ]]; then
  /usr/bin/openssl rand 32 > "$temporary/rate-hmac.raw"
  write_keyring "$rate_hmac" rate-limit-hmac "$temporary/rate-hmac.raw" 0
fi
if [[ ! -e "$owner_receipt" ]]; then
  /usr/bin/openssl rand 32 > "$temporary/owner-receipt.raw"
  write_keyring "$owner_receipt" offline-owner-receipt-hmac "$temporary/owner-receipt.raw" 0
fi
for path in "$identity_encryption" "$identity_hmac" "$rate_hmac" "$owner_receipt"; do
  [[ -f "$path" && ! -L "$path" ]] || fail
  [[ "$(/usr/bin/stat -c '%a %U' "$path")" == "600 root" ]] || fail
done

for volume in \
  orbbec-agent-platform-api-secrets \
  orbbec-agent-platform-directory-secrets \
  orbbec-agent-platform-stream-secrets; do
  /usr/bin/docker volume create "$volume" >/dev/null
done

/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-api-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 sh -ceu '
    cp /source/replica-database-url /source/replica-encryption-key /source/replica-signing-public-key /target/
    cp /source/control-database-url /source/control-audit-database-url /target/
    cp /source/dingtalk-app-key /source/dingtalk-agent-id /source/dingtalk-corp-id /source/dingtalk-app-secret /target/
    cp /source/identity-encryption-keyring /source/identity-hmac-keyring /source/rate-limit-hmac-keyring /target/
    chown 10001:10001 /target/*
    chmod 600 /target/*
  '

/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-directory-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 sh -ceu '
    rm -f /target/*
    cp /source/control-directory-worker-database-url /source/dingtalk-app-key /source/dingtalk-corp-id /source/dingtalk-app-secret /target/
    cp /source/identity-encryption-keyring /source/identity-hmac-keyring /target/
    chown 10001:10001 /target/*
    chmod 600 /target/*
  '

/usr/bin/docker run --rm --network none \
  -v orbbec-agent-platform-stream-secrets:/target \
  -v "$private_path:/source:ro" alpine:3.22 sh -ceu '
    rm -f /target/*
    cp /source/control-stream-ingest-database-url /source/dingtalk-app-key /source/dingtalk-corp-id /source/dingtalk-app-secret /target/
    cp /source/identity-encryption-keyring /target/
    chown 10001:10001 /target/*
    chmod 600 /target/*
  '

trap - EXIT
cleanup
echo "DINGTALK_PRODUCTION_SECRETS_OK"
