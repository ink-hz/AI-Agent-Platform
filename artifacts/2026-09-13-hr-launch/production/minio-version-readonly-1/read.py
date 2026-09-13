from pathlib import Path
import hashlib,json,subprocess,time
out=Path(__file__).resolve().parent
argv=['/usr/bin/ssh','-i','/Users/neo/.ssh/orbbec_aliyun_ed25519','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8','root@47.106.112.69','/usr/bin/docker','exec','de10f2bd60988f07a77c685b55919cfb98ebed8e6792d41aa7c5e2a9a0b34238','minio','--version']
started=time.time()
result=subprocess.run(argv,capture_output=True,text=True,timeout=20)
(out/'stdout.log').write_text(result.stdout)
(out/'stderr.log').write_text(result.stderr)
(out/'receipt.json').write_text(json.dumps({'started_at':started,'finished_at':time.time(),'exit_code':result.returncode,'command':argv,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'stdout_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),'boundary':'Read-only minio --version in exact existing production container; no S3 calls/data changes/service restart'},indent=2)+'\n')
print(result.stdout)
raise SystemExit(result.returncode)
