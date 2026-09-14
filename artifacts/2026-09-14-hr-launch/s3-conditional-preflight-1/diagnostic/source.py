import json,subprocess
from pathlib import Path
r=Path('/opt/orbbec-agent-platform/private/hr-conditional-probe-ca7f1f1d1c5c4df7b431f2745d1c6a80')
value={'client_stdout':(r/'client-stdout.json').read_text(),'client_stderr':(r/'client-stderr.log').read_text()[:1500]}
p=r/'evidence/private-ledger.json'
if p.exists():
 j=json.loads(p.read_text());value['ledger']={'status':j['status'],'owned_version_count':len(j['owned_versions']),'events':[{k:v for k,v in e.items() if k in ('stage','failure','http_status')} for e in j['events']]}
w=json.loads(subprocess.check_output(['/usr/bin/docker','inspect','908c2f63e1de01f5226d6ddf8eb87dd4d1d3ff1edc45e534193f08203f81f819']))[0]
value['worker_mounts']=[{k:m.get(k) for k in ('Type','Name','Source','Destination','RW')} for m in w['Mounts'] if m['Destination']=='/run/secrets']
print(json.dumps(value))
