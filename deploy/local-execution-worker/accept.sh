#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "AGENT_EXECUTION_RELAY_ACCEPTANCE_FAILED" >&2
  exit 1
}

[[ $# -eq 1 && "$1" == /* ]] || fail
[[ "$(/usr/bin/id -un)" == "agentops" ]] || fail

python=/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python
backend=/Users/agentops/AgentRuntime/platform/backend
[[ -x "$python" && -d "$backend" ]] || fail

if ! (cd "$backend" && "$python" -m app.execution_relay.acceptance_orchestrator "$1"); then
  fail
fi

echo "AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 public_ports_added=0 duplicate_dispatches=0"
