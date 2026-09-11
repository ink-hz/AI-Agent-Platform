"""Extract the documented snippets; replace all operational commands with mocks."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

root = Path.cwd()
runbook = root / 'docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md'
blocks = re.findall(r'```bash\n(.*?)```', runbook.read_text(), re.S)
pm2 = next(block for block in blocks if 'delete-one metabot-hr' in block)
cloud = next(block for block in blocks if 'legacy_hr_ids=' in block)
assert '(\nset -euo pipefail\n' in pm2 and '(\nset -euo pipefail\n' in cloud
pm2 = pm2.replace('sudo -n -H -u agentops /bin/bash "$OPS_PM2"', 'mock_pm2')
cloud = cloud.replace('/usr/bin/docker inspect', 'mock_inspect')
assert 'sudo ' not in pm2 and '/usr/bin/docker' not in cloud
mock_functions = r'''
mock_pm2() {
  printf '%s\n' "$1" >> "$MOCK_ROOT/calls"
  case "$1" in
    state-one)
      if test "$MOCK_SCENARIO" = pm2_state_failure; then printf 'online\n'; else printf 'absent\n'; fi ;;
    snapshot-except)
      if test "$MOCK_SCENARIO" = pm2_cmp_failure && test -f "$MOCK_ROOT/deleted"; then printf '{"changed":true}\n'; else printf '{}\n'; fi ;;
    delete-one) : > "$MOCK_ROOT/deleted" ;;
    save) : > "$MOCK_ROOT/saved" ;;
    *) return 99 ;;
  esac
}
mock_compose() {
  case "$3" in
    ps) if test "$MOCK_SCENARIO" != cloud_missing; then printf 'fake-container\n'; fi ;;
    stop) : > "$MOCK_ROOT/stopped"; if test "$MOCK_SCENARIO" = cloud_stop_failure; then return 2; fi ;;
    *) return 99 ;;
  esac
}
mock_inspect() {
  : > "$MOCK_ROOT/inspected"
  if test "$MOCK_SCENARIO" = cloud_still_running; then printf 'true\n'; else printf 'false\n'; fi
}
hr_compose=(mock_compose)
'''
results = []
for scenario in ['pm2_state_failure', 'pm2_cmp_failure', 'pm2_success', 'cloud_missing', 'cloud_stop_failure', 'cloud_still_running', 'cloud_success']:
    with tempfile.TemporaryDirectory(prefix='hr-runbook-pure-mock-') as temp:
        folder = Path(temp)
        snippet = pm2 if scenario.startswith('pm2') else cloud
        snippet = snippet.replace('hr_stop_evidence=/Users/agentops/AgentRuntime/release-evidence/APPROVED_CHANGE', 'hr_stop_evidence=' + shlex.quote(temp))
        result = subprocess.run(['bash', '-c', mock_functions + snippet], env={**os.environ, 'MOCK_ROOT': temp, 'MOCK_SCENARIO': scenario}, capture_output=True, text=True, timeout=3)
        row = dict(scenario=scenario, exit_code=result.returncode,
                   save_called=(folder / 'saved').exists(),
                   stop_called=(folder / 'stopped').exists(),
                   inspect_called=(folder / 'inspected').exists())
        assert (result.returncode == 0) == scenario.endswith('_success'), row
        if scenario.startswith('pm2'):
            assert row['save_called'] == (scenario == 'pm2_success'), row
        if scenario == 'cloud_missing': assert not row['stop_called'], row
        if scenario == 'cloud_stop_failure': assert not row['inspect_called'], row
        results.append(row)
print(json.dumps({'runbook_sha256': hashlib.sha256(runbook.read_bytes()).hexdigest(), 'pure_mocks_only': True, 'results': results}, indent=2))
