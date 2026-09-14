PROGRAM='"""Read-only metadata/open diagnostic. No boto, S3, writes or secret output."""\nimport json\nimport os\nfrom pathlib import Path\n\nresult = {\'uid\': os.getuid(), \'euid\': os.geteuid(), \'secret_readability\': {}}\ntry:\n    status = Path(\'/proc/self/status\').read_text()\n    result[\'linux_capabilities\'] = {\n        line.split(\':\', 1)[0]: line.split(\':\', 1)[1].strip()\n        for line in status.splitlines()\n        if line.startswith((\'CapEff:\', \'CapPrm:\', \'CapBnd:\'))\n    }\nexcept OSError:\n    result[\'linux_capabilities\'] = \'unavailable\'\nfor name in (\'attachment-s3-access-key\', \'attachment-s3-secret-key\'):\n    path = Path(\'/run/secrets\') / name\n    row = {}\n    try:\n        info = path.lstat()\n        row.update(owner_uid=info.st_uid, mode=oct(info.st_mode & 0o777))\n        with path.open(\'rb\') as source:\n            source.read(1)  # Discard; never emit content, length or digest.\n        row[\'open_read_ok\'] = True\n    except OSError as error:\n        row.update(open_read_ok=False, errno=error.errno, exception=type(error).__name__)\n    result[\'secret_readability\'][name] = row\nprint(json.dumps(result, sort_keys=True))\n'
RUN='d551a4b48cd7450e9b486082ca74bd9a'
import json, subprocess
IMAGE='sha256:cbe282151ff678b9e0573102267b71ba1c32888603b4d818fa07be22ccc50442'
NAME='hr-conditional-readonly-'+RUN
D=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
def call(args):
 return subprocess.check_output(D+args,stderr=subprocess.DEVNULL,timeout=20,env={'PATH':'/usr/bin:/bin'})
def inspect(name):return json.loads(call(['inspect',name]))[0]
r={'boundary':'read-only secret open; no S3 or database call','run_id':RUN,'status':'failed'}
identity=None;attempted=False
try:
 if NAME in call(['ps','-a','--format','{{.Names}}']).decode().splitlines():raise RuntimeError('existing')
 attempted=True
 call(['create','--name',NAME,'--label','hr.probe.readonly='+RUN,'--network','none','--read-only','--restart','no','--cap-drop','ALL','--user','0:0','--security-opt','no-new-privileges:true','--mount','type=volume,src=orbbec-agent-platform-api-secrets,dst=/run/secrets,readonly','--entrypoint','python',IMAGE,'-c',PROGRAM])
 row=inspect(NAME)
 if row['Image']!=IMAGE or row['Config']['Labels'].get('hr.probe.readonly')!=RUN:raise RuntimeError('ownership')
 identity=row['Id'];r['client_id']=identity;r['image']=IMAGE
 r['diagnostic']=json.loads(call(['start','--attach',identity]));r['status']='completed'
finally:
 if attempted:
  row=inspect(identity or NAME)
  if row['Image']!=IMAGE or row['Config']['Labels'].get('hr.probe.readonly')!=RUN:raise RuntimeError('cleanup_ownership')
  call(['rm','--force',row['Id']]);r['owned_client_removed']=True
 print(json.dumps(r))
