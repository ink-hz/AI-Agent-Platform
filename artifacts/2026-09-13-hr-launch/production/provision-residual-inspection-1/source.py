"""Read-only production snapshot after the fixed HR provisioning run."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

RELEASE='ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7'
DEPLOYMENT='4cd463bbaf897639b891b04555bd0c78'
PRIVATE=Path('/opt/orbbec-agent-platform/private')
META=Path('/data/orbbec-agent-platform/release-metadata')/RELEASE/('hr-provision-'+DEPLOYMENT)

def run(args):
 return subprocess.run(args,capture_output=True,check=True,text=True,timeout=20,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}).stdout

def digest(path):
 return hashlib.sha256(path.read_bytes()).hexdigest()

def metadata(path):
 if not path.exists() and not path.is_symlink(): return {'state':'absent'}
 s=path.lstat()
 return {'state':'present','uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode)),'bytes':s.st_size,'type':'file' if stat.S_ISREG(s.st_mode) else 'directory' if stat.S_ISDIR(s.st_mode) else 'other'}

def main():
 if os.getuid()!=0:raise RuntimeError('root required')
 docker=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
 ids=run(docker+['ps','-aq']).split()
 containers=json.loads(run(docker+['inspect',*ids]))
 fields=lambda c:{'id':c['Id'],'image':c['Image'],'running':c['State']['Running'],'started_at':c['State']['StartedAt'],'restart_count':c['RestartCount'],'restart_policy':c['HostConfig']['RestartPolicy']['Name'],'health':c['State'].get('Health',{}).get('Status'),'service':(c['Config'].get('Labels') or {}).get('com.docker.compose.service'),'project':(c['Config'].get('Labels') or {}).get('com.docker.compose.project')}
 peers={c['Name'].lstrip('/'):fields(c) for c in containers}
 paths=[PRIVATE/'agent-brain-action.lock',PRIVATE/'deploy-input.lock',PRIVATE/'hr-provision-inputs'/(RELEASE+'-'+DEPLOYMENT),Path('/data/orbbec-agent-platform/provision-staging')/DEPLOYMENT,Path('/data/orbbec-agent-platform/hr-knowledge')/('.current.'+DEPLOYMENT)]
 residues={str(p):metadata(p) for p in paths}
 if any(m['state']!='absent' for m in residues.values()):raise RuntimeError('provision residue present')
 hrprivate=PRIVATE/'hr-agent'
 expected={'hr-budget-profile.json','hr-content-keyring.json','hr-diagnostic-profile.json','hr-provider-credential','hr-provider-profile.json','hr-release-policy.json','runtime.env','api-runtime.env','worker-runtime.env'}
 if {p.name for p in hrprivate.iterdir()}!=expected:raise RuntimeError('private files mismatch')
 hostfiles={p.name:metadata(p) for p in hrprivate.iterdir()}
 if any(x['uid']!=0 or x['mode']!='0o600' or x['type']!='file' for x in hostfiles.values()):raise RuntimeError('host metadata mismatch')
 volumes=json.loads(run(docker+['volume','inspect','orbbec-agent-platform-hr-agent-secrets','orbbec-agent-platform-api-secrets']))
 mounts={v['Name']:Path(v['Mountpoint']) for v in volumes}
 hr=mounts['orbbec-agent-platform-hr-agent-secrets'];api=mounts['orbbec-agent-platform-api-secrets']
 expected_volume=expected-{'runtime.env'}|{'control-database-url','attachment-s3-access-key','attachment-s3-secret-key','content-encryption-keyring'}
 if {p.name for p in hr.iterdir()}!=expected_volume:raise RuntimeError('volume files mismatch')
 volfiles={p.name:metadata(p) for p in hr.iterdir()}
 if any(x['uid']!=10001 or x['mode']!='0o600' or x['type']!='file' for x in volfiles.values()):raise RuntimeError('volume metadata mismatch')
 equal={name:(hr/name).read_bytes()==(api/name).read_bytes() for name in ('control-database-url','attachment-s3-access-key','attachment-s3-secret-key','content-encryption-keyring')}
 equal.update({name:(hr/name).read_bytes()==(hrprivate/name).read_bytes() for name in expected-{'runtime.env'}})
 if not all(equal.values()):raise RuntimeError('secret copy mismatch')
 knowledge=Path('/data/orbbec-agent-platform/hr-knowledge')
 before=json.loads((META/'knowledge-before.json').read_text())
 old_preserved=True
 for name, expected_item in before['items'].items():
  p=knowledge/name;m=metadata(p)
  actual={'uid':m['uid'],'gid':m['gid'],'mode':int(m['mode'],8),'type':'regular' if m['type']=='file' else m['type']}
  if m['type']=='file':actual.update(size=m['bytes'],sha256=digest(p))
  if actual!=expected_item:old_preserved=False
 if not old_preserved:raise RuntimeError('old knowledge changed')
 result=json.loads((META/'result.json').read_text()); preflight=json.loads((META/'preflight.json').read_text())
 if (META/'exit_code').read_text().strip()!='0' or result['status']!='completed' or preflight['blockers']!=['database_not_checked']:raise RuntimeError('provision outcome mismatch')
 compose=Path('/opt/orbbec-agent-platform/releases/65e7fbd14a3cbe99883b0b31e31b5705d183d1f9/deploy/cloud/compose.yaml')
 print(json.dumps({'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'current_target':str(Path('/opt/orbbec-agent-platform/current').resolve()),'release':RELEASE,'deployment':DEPLOYMENT,'residues':residues,'private_directory':metadata(hrprivate),'private_files':hostfiles,'hr_volume_files':volfiles,'secret_copies_equal':equal,'old_knowledge_preserved':True,'knowledge_current':json.loads((knowledge/'current.json').read_text()),'preflight_sha256':digest(META/'preflight.json'),'preflight_blockers':preflight['blockers'],'platform_env_sha256':digest(PRIVATE/'platform.env'),'current_compose_sha256':digest(compose),'containers':peers,'inspection_is_readonly':True},indent=2))

if __name__=='__main__':main()
