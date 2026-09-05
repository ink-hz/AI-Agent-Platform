#!/bin/bash
set -eEuo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' AGENTOPS_CONTROL_FAILED >&2
  exit 1
}

required_user=agentops
required_owner=root
required_mode=755
required_home=/Users/agentops
required_path=/Users/agentops/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
runtime_root=/Users/agentops/AgentRuntime
agent_team_root=/Users/agentops/Developer/work/Orbbec-Agent-Team
installed_path=/Library/PrivilegedHelperTools/orbbec-agentops-control
git_bin=/usr/bin/git
logger_bin=/usr/bin/logger

[[ $# -ge 1 && $# -le 2 ]] || fail
[[ "$0" == "$installed_path" && -f "$0" && ! -L "$0" ]] || fail
[[ "$(/usr/bin/stat -f '%Su %Lp' "$0")" == "$required_owner $required_mode" ]] || fail
[[ "$(/usr/bin/id -un)" == "$required_user" ]] || fail
[[ "${HOME:-}" == "$required_home" ]] || fail
[[ "${USER:-}" == "$required_user" && "${LOGNAME:-}" == "$required_user" ]] || fail

command="$1"
relay_accept="$runtime_root/platform/deploy/local-execution-worker/accept.sh"
relay_config="$runtime_root/private/acceptance-config.json"
worker_supervisor="$runtime_root/platform/deploy/local-execution-worker/worker-pm2.sh"
hr_p0_accept=$runtime_root/platform/deploy/cloud/accept-hr-p0.sh
hr_p0_config=$runtime_root/private/acceptance-config.json

audit() {
  phase="$1"
  exit_code="$2"
  "$logger_bin" -t orbbec-agentops-control \
    "actor=${SUDO_USER:-unknown} command=$command phase=$phase exit_code=$exit_code" \
    >/dev/null 2>&1 || true
}

run_fixed() {
  (
    cd "$required_home" || exit 1
    /usr/bin/env -i \
      HOME="$required_home" USER="$required_user" LOGNAME="$required_user" \
      PATH="$required_path" \
      "$@"
  )
}

audit start none
status=0
case "$command" in
  relay-canary)
    [[ $# -eq 1 ]] || fail
    run_fixed "$relay_accept" "$relay_config" || status=$?
    ;;
  worker-stop)
    [[ $# -eq 1 ]] || fail
    run_fixed "$worker_supervisor" stop || status=$?
    ;;
  worker-restore)
    [[ $# -eq 1 ]] || fail
    run_fixed "$worker_supervisor" restore online || status=$?
    ;;
  metabot-release-sha)
    [[ $# -eq 1 ]] || fail
    run_fixed "$git_bin" -C "$runtime_root/metabot" rev-parse HEAD || status=$?
    ;;
  agent-team-release-sha)
    [[ $# -eq 1 ]] || fail
    run_fixed "$git_bin" -C "$agent_team_root" rev-parse HEAD || status=$?
    ;;
  accept-hr-p0)
    [[ $# -eq 2 && "$2" == "$hr_p0_config" ]] || fail
    run_fixed "$hr_p0_accept" "$hr_p0_config" || status=$?
    ;;
  status)
    [[ $# -eq 1 ]] || fail
    /usr/bin/printf '%s\n' 'AGENTOPS_CONTROL_OK commands=7' || status=$?
    ;;
  *)
    status=1
    ;;
esac
audit finish "$status"
[[ "$status" == 0 ]] || fail
