from pathlib import Path
import subprocess,sys,json,hashlib,datetime
root=Path(__file__).resolve().parents[4]
out=Path(__file__).parent/sys.argv[1];out.mkdir(exist_ok=False)
meta={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'command':sys.argv[2:],'cwd':str(root),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sources':{}}
for f in ['backend/tests/test_hr_agent_history_replay.py','backend/tests/helpers/hr_history_replay.py']:
 p=root/f
 if p.is_file():
  raw=p.read_bytes();dest=out/'sources'/f;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);meta['sources'][f]=hashlib.sha256(raw).hexdigest()
with (out/'output.log').open('x') as stream:result=subprocess.run(sys.argv[2:],cwd=root,stdout=stream,stderr=subprocess.STDOUT)
meta.update(exit_code=result.returncode,finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat());(out/'command.json').write_text(json.dumps(meta,indent=2)+'\n');print((out/'output.log').read_text());sys.exit(result.returncode)
