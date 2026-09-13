"""Read-only container/mount/server configuration facts; never prints env values."""
import hashlib
import json
from pathlib import Path
import subprocess

DOCKER=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
def run(args):
 return subprocess.check_output(DOCKER+args,text=True,timeout=15)
ids=run(['ps','-aq']).split()
rows=json.loads(run(['inspect',*ids]))
targets=[r for r in rows if r['Name']=='/orbbec-agent-platform-platform-minio-1']
if len(targets)!=1:raise RuntimeError('exact MinIO identity is not unique')
t=targets[0]
def summary(r):
 return {'id':r['Id'],'name':r['Name'],'image':r['Image'],'running':r['State']['Running'],'pid':r['State']['Pid'],'started_at':r['State']['StartedAt'],'restart_count':r['RestartCount'],'restart_policy':r['HostConfig']['RestartPolicy'],'mounts':[{'type':m['Type'],'source':m['Source'],'destination':m['Destination'],'rw':m['RW']} for m in r['Mounts']],'environment_names':sorted(v.split('=',1)[0] for v in r['Config'].get('Env',[])),'cmd_sha256':hashlib.sha256(json.dumps(r['Config'].get('Cmd')).encode()).hexdigest(),'ports':r['NetworkSettings']['Ports']}
data=[m['Source'] for m in t['Mounts'] if m['Destination']=='/data']
if len(data)!=1:raise RuntimeError('MinIO data mount not unique')
shared=[summary(r) for r in rows if any(m['Source']==data[0] for m in r['Mounts'])]
processes=run(['top',t['Id'],'-eo','pid,comm'])
worker=[r for r in rows if r['Name']=='/orbbec-agent-platform-platform-attachments-1']
if len(worker)!=1 or worker[0]['Id']!='908c2f63e1de01f5226d6ddf8eb87dd4d1d3ff1edc45e534193f08203f81f819':raise RuntimeError('attachment client source changed')
probe='''import json,os
from app.attachments.worker_runtime import _build_s3_client
c=_build_s3_client();bucket=os.environ['PLATFORM_ATTACHMENT_S3_BUCKET'];out={}
for name, method in [('lifecycle','get_bucket_lifecycle_configuration'),('replication','get_bucket_replication'),('notification','get_bucket_notification_configuration')]:
 try:
  value=getattr(c,method)(Bucket=bucket)
  out[name]={'http_status':value.get('ResponseMetadata',{}).get('HTTPStatusCode'),'configured_fields':sorted(k for k in value if k!='ResponseMetadata'),'rule_count':len(value.get('Rules',value.get('ReplicationConfiguration',{}).get('Rules',[])))}
 except Exception as e:
  r=getattr(e,'response',{});out[name]={'error_code':r.get('Error',{}).get('Code'),'http_status':r.get('ResponseMetadata',{}).get('HTTPStatusCode')}
print(json.dumps(out))
'''
result=subprocess.run(DOCKER+['exec','-i',worker[0]['Id'],'python','-'],input=probe,text=True,capture_output=True,timeout=45)
if result.returncode:raise RuntimeError('readonly S3 configuration probe failed')
print(json.dumps({'writes':False,'target':summary(t),'containers_sharing_exact_data_mount':shared,'target_processes_pid_comm':processes,'bucket_configuration':json.loads(result.stdout)},sort_keys=True))
