import subprocess,json
names=['orbbec-agent-platform-hr-agent-secrets','orbbec-agent-platform-api-secrets']
a=json.loads(subprocess.check_output(['/usr/bin/docker','volume','inspect',*names]))
print(json.dumps({'docker_root':subprocess.check_output(['/usr/bin/docker','info','--format','{{.DockerRootDir}}'],text=True).strip(),'volumes':[{k:v.get(k) for k in ['Name','Driver','Mountpoint','Options','Scope']} for v in a]}))
