PROGRAM='"""Pack and encrypt an existing immutable backup; no database/network access."""\nfrom pathlib import Path\nimport hashlib\nimport json\nimport os\nimport sys\nimport tarfile\nimport tempfile\n\nfrom app.cloud_replica.backup import encrypt_stream\n\nos.umask(0o077)\nsource = Path(\'/source\')\ntarget = Path(\'/output\')\nexpected = json.loads((source/\'receipt.json\').read_text())\nif expected[\'deployment_id\'] != \'dc4b996ae61b4390aab3dc1e70ba6228\' or expected[\'status\'] != \'completed\':\n    raise SystemExit(\'backup_identity_invalid\')\nnames = [\'production.dump\', \'globals.sql\', \'production-schema.sql\', \'production-ledger.json\']\nwith tempfile.TemporaryFile(dir=target) as packed:\n    with tarfile.open(fileobj=packed, mode=\'w\') as archive:\n        for name in names:\n            path = source/name\n            digest = hashlib.sha256()\n            with path.open(\'rb\') as body:\n                for chunk in iter(lambda: body.read(1048576), b\'\'):\n                    digest.update(chunk)\n            if digest.hexdigest() != expected[\'files\'][name][\'sha256\'] or path.stat().st_size != expected[\'files\'][name][\'bytes\']:\n                raise SystemExit(\'backup_file_identity_invalid\')\n            info = archive.gettarinfo(str(path), arcname=name)\n            info.mode = 0o600\n            info.uid = info.gid = 0\n            info.uname = info.gname = \'\'\n            with path.open(\'rb\') as body:\n                archive.addfile(info, body)\n    packed.seek(0)\n    output = target/\'control-backup.orb\'\n    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)\n    with os.fdopen(fd, \'wb\') as stream:\n        metadata = encrypt_stream(packed, stream, bytes.fromhex(sys.argv[1]))\n        stream.flush()\n        os.fsync(stream.fileno())\ndigest = hashlib.sha256()\nwith output.open(\'rb\') as stream:\n    for chunk in iter(lambda: stream.read(1048576), b\'\'):\n        digest.update(chunk)\nprint(json.dumps({\'status\': \'encrypted\', \'plaintext_bytes\': metadata.plaintext_size,\n                  \'encrypted_file_bytes\': output.stat().st_size,\n                  \'encrypted_file_sha256\': digest.hexdigest(), \'source_backup_run\': expected[\'deployment_id\'],\n                  \'original_private_snapshot_retained\': True}))\n'
PUBLIC='e3a7fe5485ff216fc03679a1842ff29c05e065dc7f84a5dcbf780fb0ff137c4d'
import os,subprocess,json,signal,stat,re,time
from pathlib import Path
os.umask(0o077)
IMAGE='sha256:cbe282151ff678b9e0573102267b71ba1c32888603b4d818fa07be22ccc50442'
RUN='8c6f5887c8bb431dbb69c8256eb924273'
NAME='hr-backup-encrypt-'+RUN
ROOT=Path('/data/orbbec-agent-platform/private-backups/hr-cloud-dc4b996ae61b4390aab3dc1e70ba6228')
OUT=ROOT/('encrypted-'+RUN)
DOCKER=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
def command(args,timeout=20):
 r=subprocess.run(DOCKER+args,capture_output=True,timeout=timeout,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
 if r.returncode:raise RuntimeError('owned_container_command_failed')
 return r.stdout
container=None;receipt={'run_id':RUN,'started_at':time.time(),'production_services_changed':False,'database_changed':False,'status':'running'}
def interrupt(number,frame):raise RuntimeError('interrupted')
for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGQUIT):signal.signal(sig,interrupt)
try:
 if os.getuid()!=0 or ROOT.is_symlink() or stat.S_IMODE(ROOT.stat().st_mode)!=0o700:raise RuntimeError('private_backup_invalid')
 OUT.mkdir(mode=0o700,exist_ok=False)
 container=command(['create','--name',NAME,'--label','hr.backup.run='+RUN,'--network','none','--read-only','--restart','no','--cap-drop','ALL','--security-opt','no-new-privileges:true','--user','0:0','-e','TMPDIR=/output','--mount','type=bind,src='+str(ROOT)+',dst=/source,readonly','--mount','type=bind,src='+str(OUT)+',dst=/output','--entrypoint','python',IMAGE,'-c',PROGRAM,PUBLIC]).decode().strip()
 if re.fullmatch('[0-9a-f]{64}',container) is None:raise RuntimeError('container_identity_invalid')
 row=json.loads(command(['inspect',container]))[0]
 if row['Name']!='/'+NAME or row['Image']!=IMAGE or row['Config']['Labels'].get('hr.backup.run')!=RUN:raise RuntimeError('container_ownership_invalid')
 receipt['container_id']=container
 (OUT/'operation.json').write_text(json.dumps(receipt))
 result=command(['start','--attach',container],timeout=180)
 row=json.loads(command(['inspect',container]))[0]
 if row['State']['Running'] or row['State']['ExitCode']!=0:raise RuntimeError('encryption_incomplete')
 receipt.update(json.loads(result),private_path=str(OUT/'control-backup.orb'))
except BaseException as error:
 receipt.update(status='failed',failure_type=type(error).__name__)
finally:
 for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGQUIT):signal.signal(sig,signal.SIG_IGN)
 try:
  if container and re.fullmatch('[0-9a-f]{64}',container):
   row=json.loads(command(['inspect',container]))[0]
   if row['Name']!='/'+NAME or row['Image']!=IMAGE or row['Config']['Labels'].get('hr.backup.run')!=RUN:raise RuntimeError('cleanup_ownership_invalid')
   if row['State']['Running']:command(['stop','--time','2',container],timeout=10)
   row=json.loads(command(['inspect',container]))[0]
   if row['State']['Running']:raise RuntimeError('cleanup_unverified')
   command(['rm',container])
  receipt['owned_container_removed']=True
 except BaseException:
  receipt.update(status='failed',owned_container_removed=False)
 receipt['finished_at']=time.time()
 if OUT.exists():(OUT/'operation.json').write_text(json.dumps(receipt))
 print(json.dumps(receipt))
raise SystemExit(0 if receipt['status']=='encrypted' and receipt['owned_container_removed'] else 1)
