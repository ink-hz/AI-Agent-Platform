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

if ! (cd "$backend" && "$python" - <<'PY'
from pathlib import Path

from app.execution_relay.metabot_client import MetaBotClient, MetaBotRuntimeMap

runtime_map = MetaBotRuntimeMap.from_contract(
    Path("/Users/agentops/AgentRuntime/metabot/runtime-contract.json")
)
client = MetaBotClient(
    runtime_map,
    Path("/Users/agentops/AgentRuntime/private/metabot-api-token"),
)
client.assert_result_contract_v2("hr-bot")
PY
); then
  fail
fi

echo "AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 accepted_job_kinds=direct_agent,metabot_local public_ports_added=0 duplicate_dispatches=0"
