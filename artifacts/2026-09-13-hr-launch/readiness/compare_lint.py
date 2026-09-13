import collections
import json
import subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[3]
files = ['backend/app/hr_agent/readiness.py','backend/app/hr_agent/worker_health.py','backend/app/hr_agent/worker.py','backend/app/main.py','backend/app/control_plane/routes_auth.py','backend/app/control_plane/authorization.py','backend/app/control_plane/middleware.py','backend/tests/test_hr_agent_readiness.py','backend/tests/test_hr_agent_worker_health.py']
results = {}
for label in ('baseline', 'current'):
    entries = []
    for name in files:
        if label == 'baseline':
            source = subprocess.run(['git','show','482a43f:'+name],cwd=root,capture_output=True)
            if source.returncode: continue
            data = source.stdout
        else: data = (root/name).read_bytes()
        output = subprocess.run([str(root/'backend/.venv/bin/ruff'),'check','--output-format','json','--stdin-filename',name,'-'],input=data,cwd=root,capture_output=True)
        for diagnostic in json.loads(output.stdout):
            entries.append({'file':name, 'code':diagnostic['code'], 'message':diagnostic['message']})
    results[label] = entries
counts = lambda entries: collections.Counter(json.dumps(e,sort_keys=True) for e in entries)
new = counts(results['current']) - counts(results['baseline'])
results['new_diagnostics'] = list(new.elements())
print(json.dumps(results,indent=2))
raise SystemExit(bool(new))
