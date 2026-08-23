#!/bin/bash
set -euo pipefail
umask 077

fail() { echo EXECUTION_WORKER_PM2_FAILED >&2; exit 1; }
[[ $# -ge 1 && "$(/usr/bin/id -un)" == agentops && "${HOME:-}" == /Users/agentops ]] || fail
cd /Users/agentops || fail

name=orbbec-agent-execution-worker
config=/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/execution-worker.ecosystem.config.cjs
pm2=/Users/agentops/.npm-global/bin/pm2
safe_path=/Users/agentops/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
[[ -x "$pm2" && ! -L "$pm2" && -x /usr/bin/jq ]] || fail

fixed_config() {
  [[ -f "$config" && ! -L "$config" \
    && "$(/usr/bin/stat -f '%Lp %Su' "$config")" == "644 agentops" ]]
}

pm2_clean() {
  /usr/bin/env -i HOME=/Users/agentops USER=agentops LOGNAME=agentops \
    PATH="$safe_path" PM2_HOME=/Users/agentops/.pm2 \
    TMPDIR=/Users/agentops/AgentRuntime/tmp \
    NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
    "$pm2" "$@"
}

state() {
  pm2_clean jlist | /usr/bin/jq -er --arg name "$name" '
    [.[] | select(.name == $name)]
    | if length == 0 then "absent"
      elif length == 1 and (.[0].pm2_env.status == "online" or .[0].pm2_env.status == "stopped")
        then .[0].pm2_env.status
      else error("worker PM2 state is ambiguous") end'
}

delete_worker() {
  pm2_clean delete "$name" >/dev/null 2>&1 || [[ "$(state)" == absent ]]
}

case "$1" in
  state)
    [[ $# -eq 1 ]] || fail
    state
    ;;
  inspect)
    [[ $# -eq 1 ]] || fail
    pm2_clean jlist | /usr/bin/jq -ce --arg name "$name" '
      [.[] | select(.name == $name)]
      | if length == 1 and .[0].pm2_env.status == "online"
          and (.[0].pid | type) == "number" and .[0].pid > 0
          and .[0].pm2_env.pm_exec_path == "/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python"
          and .[0].pm2_env.pm_cwd == "/Users/agentops/AgentRuntime/platform/backend"
          and .[0].pm2_env.args == ["-m","app.execution_relay.worker"]
        then {name:.[0].name,pid:.[0].pid,status:.[0].pm2_env.status,
              pm_exec_path:.[0].pm2_env.pm_exec_path,pm_cwd:.[0].pm2_env.pm_cwd,
              args:.[0].pm2_env.args}
        else error("worker PM2 identity mismatch") end'
    ;;
  start)
    [[ $# -eq 1 ]] && fixed_config || fail
    delete_worker
    pm2_clean start "$config" --only "$name" --update-env >/dev/null
    [[ "$(state)" == online ]] || fail
    ;;
  stop)
    [[ $# -eq 1 && "$(state)" == online ]] || fail
    pm2_clean stop "$name" >/dev/null
    [[ "$(state)" == stopped ]] || fail
    ;;
  restore)
    [[ $# -eq 2 && ( "$2" == absent || "$2" == online || "$2" == stopped ) ]] || fail
    if [[ "$2" == absent ]]; then
      delete_worker
    else
      fixed_config || fail
      delete_worker
      pm2_clean start "$config" --only "$name" --update-env >/dev/null
      [[ "$2" == online ]] || pm2_clean stop "$name" >/dev/null
    fi
    [[ "$(state)" == "$2" ]] || fail
    ;;
  save)
    [[ $# -eq 1 ]] || fail
    current_state="$(state)" || fail
    [[ "$current_state" == absent || "$current_state" == online || "$current_state" == stopped ]] || fail
    pm2_clean save >/dev/null
    ;;
  *) fail ;;
esac
