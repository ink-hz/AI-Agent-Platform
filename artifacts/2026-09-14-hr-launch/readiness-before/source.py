import json,subprocess,os,stat
from pathlib import Path
ids=subprocess.check_output(['/usr/bin/docker','ps','-aq'],text=True).split()
rows=json.loads(subprocess.check_output(['/usr/bin/docker','inspect',*ids],text=True))
containers={r['Name']:{'id':r['Id'],'image':r['Image'],'running':r['State']['Running'],'started_at':r['State']['StartedAt'],'restart_policy':r['HostConfig']['RestartPolicy']['Name']} for r in rows}
p=Path('/opt/orbbec-agent-platform/private/hr-p0-acceptance.json')
config={'exists':p.exists(),'symlink':p.is_symlink()}
if p.exists():
 s=p.lstat();config.update(uid=s.st_uid,mode=oct(stat.S_IMODE(s.st_mode)),regular=stat.S_ISREG(s.st_mode))
new=json.loads(subprocess.check_output(['/usr/bin/docker','image','inspect','sha256:cbe282151ff678b9e0573102267b71ba1c32888603b4d818fa07be22ccc50442'],text=True))[0]
print(json.dumps({'current':os.readlink('/opt/orbbec-agent-platform/current'),'containers':containers,'new_image':{'id':new['Id'],'os':new['Os'],'architecture':new['Architecture']},'expected_owner_config':config,'boundary':'read-only container/image/symlink and expected config metadata;no secret/body reads or service/database changes'}))
