(
set -euo pipefail
umask 077
# HR_LEGACY_STOP
export OPS_PM2=/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh
export ECOSYSTEM=/Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs
export hr_stop_evidence=/Users/agentops/AgentRuntime/release-evidence/APPROVED_CHANGE
test -d "$hr_stop_evidence"
# approved-hr-stop.json and the exact approved runbook.md must already exist.
python3 - <<'PY'
import hashlib, json, os, pathlib, socket
root = pathlib.Path(os.environ['hr_stop_evidence'])
approved = json.loads((root / 'approved-hr-stop.json').read_text())
assert not any((root / name).exists() for name in ('hr-before.txt', 'others-before.json', 'hr-before-identity.json', 'wrapper-before.sh', 'ecosystem-before.cjs', 'pm2-stop-receipt.json'))
assert approved['hostname'] == socket.gethostname()
assert approved['expectedState'] in {'online', 'stopped'}
for path, key in ((pathlib.Path(os.environ['OPS_PM2']), 'wrapperSha256'),
                  (pathlib.Path(os.environ['ECOSYSTEM']), 'ecosystemSha256'),
                  (root / 'runbook.md', 'runbookSha256')):
    assert path.is_file() and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == approved[key]
PY
sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr > "$hr_stop_evidence/hr-before.txt"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_stop_evidence/others-before.json"
python3 - <<'PY'
import json, os, pathlib
root = pathlib.Path(os.environ['hr_stop_evidence'])
identity = json.loads((root / 'approved-hr-stop.json').read_text())
identity['beforeState'] = (root / 'hr-before.txt').read_text().strip()
assert identity['beforeState'] == identity['expectedState']
identity['otherInstances'] = json.loads((root / 'others-before.json').read_text())
for variable, name in (('OPS_PM2', 'wrapper-before.sh'), ('ECOSYSTEM', 'ecosystem-before.cjs')):
    with (root / name).open('xb') as stream:
        stream.write(pathlib.Path(os.environ[variable]).read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
# Persist exit prerequisites before delete, without overwriting earlier evidence.
with (root / 'hr-before-identity.json').open('x') as stream:
    json.dump(identity, stream, indent=2)
    stream.flush()
    os.fsync(stream.fileno())
PY
sudo -n -H -u agentops /bin/bash "$OPS_PM2" delete-one metabot-hr
test "$(sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr)" = absent
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_stop_evidence/others-after.json"
cmp "$hr_stop_evidence/others-before.json" "$hr_stop_evidence/others-after.json"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" save
python3 - <<'PY'
import datetime, json, os, pathlib
root = pathlib.Path(os.environ['hr_stop_evidence'])
receipt = json.loads((root / 'hr-before-identity.json').read_text())
receipt.update(block='HR_LEGACY_STOP', afterState='absent', saved=True,
               checkedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
with (root / 'pm2-stop-receipt.json').open('x') as stream:
    json.dump(receipt, stream, indent=2)
PY
)
