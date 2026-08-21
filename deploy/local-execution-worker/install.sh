#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "EXECUTION_WORKER_INSTALL_FAILED" >&2
  exit 1
}

[[ $# -eq 1 && "$1" == /* && "$(/usr/bin/id -un)" == "agentops" ]] || fail
owner_dsn_file="$1"
runtime_root=/Users/agentops/AgentRuntime
platform_root="$runtime_root/platform"
private_root="$runtime_root/private"
log_root="$runtime_root/log"
metabot_contract="$runtime_root/metabot/runtime-contract.json"
metabot_secret="$private_root/metabot-api-token"
private_key="$private_root/execution-worker-ed25519.key"
public_document="$runtime_root/execution-worker-public.json"
runtime_dsn="$private_root/execution-worker-postgres-dsn"
target="$HOME/Library/LaunchAgents/com.orbbec.agent-execution-worker.plist"
script_dir="$(cd "$(dirname "$0")" && pwd)"

[[ "$platform_root" == "$(cd "$script_dir/../.." && pwd)" ]] || fail
[[ -x "$platform_root/backend/.venv/bin/python" ]] || fail
[[ -f "$owner_dsn_file" && ! -L "$owner_dsn_file" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$owner_dsn_file")" == "600 agentops" ]] || fail
for directory in "$runtime_root" "$private_root"; do
  [[ -d "$directory" && ! -L "$directory" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$directory")" == "700 agentops" ]] || fail
done
for secret in "$metabot_secret"; do
  [[ -f "$secret" && ! -L "$secret" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$secret")" == "600 agentops" ]] || fail
done
[[ -f "$metabot_contract" && ! -L "$metabot_contract" ]] || fail
/bin/mkdir -p "$log_root" "$HOME/Library/LaunchAgents"
/bin/chmod 700 "$log_root" "$HOME/Library/LaunchAgents"

"$platform_root/backend/.venv/bin/python" "$script_dir/generate-worker-key.py" \
  "$private_key" "$public_document"
"$script_dir/bootstrap-worker-database.sh" "$owner_dsn_file" "$runtime_dsn"

temporary="$(/usr/bin/mktemp "$HOME/Library/LaunchAgents/.execution-worker.XXXXXX")"
cleanup() { /bin/rm -f -- "$temporary"; }
trap cleanup EXIT
/bin/cp "$script_dir/com.orbbec.agent-execution-worker.plist.template" "$temporary"
/bin/chmod 600 "$temporary"
/usr/bin/plutil -lint "$temporary" >/dev/null
/bin/mv -f "$temporary" "$target"
trap - EXIT
domain="gui/$(/usr/bin/id -u)"
/bin/launchctl bootout "$domain" "$target" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "$domain" "$target"
/bin/launchctl enable "$domain/com.orbbec.agent-execution-worker"
echo "EXECUTION_WORKER_INSTALLED"
