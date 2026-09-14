import json,subprocess,os,time
names = ['ai-admin-postgres-bridge', 'ai-fae-backend', 'ai-fae-postgres', 'amazing_wright', 'langfuse-clickhouse', 'langfuse-minio', 'langfuse-postgres', 'langfuse-redis', 'langfuse-web', 'langfuse-worker', 'orbbec-agent-platform-platform-api-1', 'orbbec-agent-platform-platform-attachment-storage-init-1', 'orbbec-agent-platform-platform-attachments-1', 'orbbec-agent-platform-platform-brain-1', 'orbbec-agent-platform-platform-dingtalk-stream-1', 'orbbec-agent-platform-platform-directory-1', 'orbbec-agent-platform-platform-hr-web-worker-1', 'orbbec-agent-platform-platform-loopback-1', 'orbbec-agent-platform-platform-minio-1', 'orbbec-agent-platform-platform-postgres-1', 'orbbec-voc-bot-ingest-1', 'orbbec-voc-bot-interact-1', 'orbbec-voc-postgres-1', 'orbbec-voc-workspace-1']
raw=subprocess.run(['/usr/bin/docker','inspect',*names],capture_output=True,timeout=15,check=True)
rows=json.loads(raw.stdout)
assert len(rows)==24
result={r['Name'].lstrip('/'):{'id':r['Id'],'image':r['Image'],'running':r['State']['Running'],'status':r['State']['Status'],'started_at':r['State']['StartedAt'],'restart_policy':r['HostConfig']['RestartPolicy']['Name']} for r in rows}
assert set(result)==set(names)
print(json.dumps({'observed_at':time.time(),'current':os.readlink('/opt/orbbec-agent-platform/current'),'containers':result},sort_keys=True))
