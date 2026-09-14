LEDGER='/opt/orbbec-agent-platform/private/hr-conditional-probe-4cc5854e73974db494203f70a702ee9a/evidence/private-ledger.json'
CLIENT='4d7f555b48b16fccbfb6ab91c0a74bc647d95c9529d2d5caec26bf10ed9f7315'
from pathlib import Path
import hashlib,json,subprocess
p=Path(LEDGER);raw=p.read_bytes();d=json.loads(raw);info=p.stat()
def h(v):return hashlib.sha256(v.encode()).hexdigest()
rows=[]
for event in d['events']:
 event=dict(event)
 if 'version' in event:event['version_sha256']=h(event.pop('version'))
 rows.append(event)
ids=subprocess.check_output(['/usr/bin/docker','--host','unix:///var/run/docker.sock','ps','-aq','--no-trunc'],timeout=10).decode().split()
print(json.dumps({'private_ledger_sha256':hashlib.sha256(raw).hexdigest(),'key_sha256':h(d['key']),'owned_version_sha256':[h(v) for v in d['owned_versions']],'status':d['status'],'events':rows,'private_ledger_owner':info.st_uid,'private_ledger_mode':oct(info.st_mode&0o777),'client_absent':CLIENT not in ids,'boundary':'readonly ledger redaction plus working-daemon container absence inspection'}))
