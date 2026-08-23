#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "EXECUTION_WORKER_INSTALL_FAILED" >&2
  exit 1
}

[[ $# -eq 1 && "$1" == /* && "$(/usr/bin/id -un)" == "agentops" ]] || fail
[[ "${HOME:-}" == /Users/agentops ]] || fail
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
script_dir="$(cd "$(dirname "$0")" && pwd)"
worker_supervisor="$script_dir/worker-pm2.sh"

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
"$platform_root/backend/.venv/bin/python" - "$metabot_contract" <<'PY' || fail
import json
import sys
from pathlib import Path

expected_agents = ['hr-bot', 'fae-bot', 'marketing-prospecting-bot', 'marketing-inbound-bot', 'marketing-voice-bot', 'marketing-intelligence-bot', 'marketing-gtm-bot', 'agent-brain-bot']
expected_ports = {
    "hr-bot": 9101,
    "fae-bot": 9105,
    "marketing-prospecting-bot": 9102,
    "marketing-inbound-bot": 9103,
    "marketing-voice-bot": 9104,
    "marketing-intelligence-bot": 9108,
    "marketing-gtm-bot": 9107,
    "agent-brain-bot": 9110,
}
rejected_agents = {
    "test-bot",
    "feishu-default",
    "codex-assistant",
    "ai-admin-agent",
    "ai-fae-agent",
}
expected_brain = {
    "name": "agent-brain-bot",
    "platform": "web",
    "platformOnly": True,
    "engine": "claude",
    "model": "claude-opus-5",
    "backend": "pty",
    "toolPolicy": "none",
    "workdir": "/Users/agentops/Developer/work/Orbbec-Agent-Team/bots/agent-brain",
    "instance": {
        "pm2Name": "metabot-agent-brain",
        "apiPort": 9110,
        "stateDir": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/state",
        "configPath": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/bots.json",
        "logDir": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/logs",
    },
}
try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 2 or not isinstance(value.get("bots"), list):
        raise ValueError
    selected = {}
    for entry in value["bots"]:
        if not isinstance(entry, dict) or entry.get("name") not in expected_ports:
            continue
        name = entry["name"]
        instance = entry.get("instance")
        if name in selected or not isinstance(instance, dict):
            raise ValueError
        if name == "agent-brain-bot":
            for key, expected in expected_brain.items():
                if key == "instance":
                    continue
                if entry.get(key) != expected:
                    raise ValueError
            if any(
                instance.get(key) != expected
                for key, expected in expected_brain["instance"].items()
            ):
                raise ValueError
        selected[name] = instance.get("apiPort")
    if list(expected_ports) != expected_agents or selected != expected_ports:
        raise ValueError
    if rejected_agents & set(selected) or len(set(selected.values())) != len(selected):
        raise ValueError
except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
echo "EXECUTION_WORKER_RUNTIME_MAP_OK"
/bin/mkdir -p "$log_root"
/bin/chmod 700 "$log_root"
[[ -x "$worker_supervisor" && ! -L "$worker_supervisor" ]] || fail
rotation_lock="$private_root/execution-worker-key-rotation.lock"
if [[ -z "${PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD:-}" ]]; then
  "$platform_root/backend/.venv/bin/python" - "$rotation_lock" "$0" "$@" <<'PY' || fail
import fcntl
import os
import stat
import sys

lock_path, script, *arguments = sys.argv[1:]
descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
        raise SystemExit(1)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(descriptor, True)
    environment = dict(os.environ)
    environment["PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD"] = str(descriptor)
    os.execve("/bin/bash", ["/bin/bash", script, *arguments], environment)
finally:
    os.close(descriptor)
PY
  exit 0
fi
[[ "$PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD" =~ ^[0-9]+$ ]] || fail
"$platform_root/backend/.venv/bin/python" - "$rotation_lock" "$PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD" <<'PY' || fail
import fcntl
import os
import stat
import sys

path, raw_descriptor = sys.argv[1:]
descriptor = int(raw_descriptor)
metadata = os.fstat(descriptor)
named = os.stat(path, follow_symlinks=False)
if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid() or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino):
    raise SystemExit(1)
fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
PY

"$platform_root/backend/.venv/bin/python" "$script_dir/generate-worker-key.py" \
  "$private_key" "$public_document"
"$script_dir/bootstrap-worker-database.sh" "$owner_dsn_file" "$runtime_dsn"
"$worker_supervisor" start || fail
echo "EXECUTION_WORKER_INSTALLED"
