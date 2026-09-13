import datetime,hashlib,json,subprocess,sys
from pathlib import Path
root=Path.cwd();out=root/'artifacts/2026-09-13-hr-launch/frontend-existing/style-fix'/sys.argv[1];out.mkdir()
paths=['webui/src/styles.css','webui/src/styles.test.ts','webui/src/workspaces/hr/HrLoopWorkspace.css','webui/src/workspaces/hr/hrResearch.css','webui/src/workspaces/hr/hrCompanyIntelligence.css']
hashes={}
for n in paths:
 p=root/n;d=out/'source'/n;d.parent.mkdir(parents=True,exist_ok=True);d.write_bytes(p.read_bytes());hashes[n]=hashlib.sha256(p.read_bytes()).hexdigest()
args=sys.argv[2:];meta={'command':args,'cwd':str(root/'webui'),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_sha256':hashes,'started':datetime.datetime.now(datetime.timezone.utc).isoformat()}
with (out/'output.log').open('w') as log:r=subprocess.run(args,cwd=root/'webui',stdout=log,stderr=subprocess.STDOUT)
meta.update(exit_code=r.returncode,ended=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_unchanged=all(hashlib.sha256((root/n).read_bytes()).hexdigest()==h for n,h in hashes.items()));(out/'command.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps({'exit_code':r.returncode,'log':str(out/'output.log')}))
