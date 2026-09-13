"""Capture exact owned sources and baseline patch before each local test."""
import datetime, hashlib, json, subprocess, sys
from pathlib import Path
root=Path(__file__).resolve().parents[3]
owned=['backend/app/hr_agent/readiness.py','backend/app/hr_agent/worker_health.py','backend/app/hr_agent/worker.py','backend/app/main.py','backend/app/control_plane/routes_auth.py','backend/app/control_plane/authorization.py','backend/app/control_plane/middleware.py','deploy/cloud/compose.yaml','backend/tests/test_hr_agent_readiness.py','backend/tests/test_hr_agent_worker_health.py']
out=Path(__file__).parent/sys.argv[1];out.mkdir(exist_ok=False)
meta={'cwd':str(root),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'command':sys.argv[2:],'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sources':{}}
for p in owned:
 if (root/p).is_file():
  data=(root/p).read_bytes(); dst=out/'sources'/p;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data);meta['sources'][p]=hashlib.sha256(data).hexdigest()
(out/'source.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD','--',*owned],cwd=root))
with (out/'output.log').open('x') as f:r=subprocess.run(sys.argv[2:],cwd=root,stdout=f,stderr=subprocess.STDOUT)
meta['exit_code']=r.returncode;meta['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();(out/'command.json').write_text(json.dumps(meta,indent=2)+'\n');print((out/'output.log').read_text());sys.exit(r.returncode)
