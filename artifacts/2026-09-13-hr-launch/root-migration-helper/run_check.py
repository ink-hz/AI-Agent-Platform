import datetime,hashlib,json,subprocess,sys
from pathlib import Path
root=Path.cwd();out=root/'artifacts/2026-09-13-hr-launch/root-migration-helper'/sys.argv[1];out.mkdir()
paths=['deploy/cloud/hr_agent_migrate.py','deploy/cloud/preflight-execution-job-kind.sh','backend/tests/test_hr_root_migration_deployment.py','backend/tests/test_hr_agent_migration_deployment.py','backend/tests/test_hr_root_job_kind_preflight.py']
hashes={}
for n in paths:
 p=root/n;d=out/'source'/n;d.parent.mkdir(parents=True,exist_ok=True);d.write_bytes(p.read_bytes());hashes[n]=hashlib.sha256(p.read_bytes()).hexdigest()
args=sys.argv[2:];meta={'command':args,'cwd':str(root),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_sha256':hashes,'started':datetime.datetime.now(datetime.timezone.utc).isoformat()}
with (out/'output.log').open('w') as log:r=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT)
meta.update(exit_code=r.returncode,ended=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_unchanged=all(hashlib.sha256((root/n).read_bytes()).hexdigest()==h for n,h in hashes.items()));(out/'command.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps({'exit_code':r.returncode,'log':str(out/'output.log')}))
