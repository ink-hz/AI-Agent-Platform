from pathlib import Path
import hashlib,json,stat
p=Path('/opt/orbbec-agent-platform/private/hr-agent/runtime.env')
s=p.lstat()
if p.is_symlink() or not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=0o600:raise SystemExit('input_metadata_invalid')
value=p.read_bytes()
count=len([line for line in value.splitlines() if line.startswith(b'PLATFORM_IMAGE=')])
if count<1:raise SystemExit('image_entry_invalid')
new=b'\n'.join(line for line in value.splitlines() if not line.startswith(b'PLATFORM_IMAGE='))+b'\nPLATFORM_IMAGE=sha256:cbe282151ff678b9e0573102267b71ba1c32888603b4d818fa07be22ccc50442\n'
print(json.dumps({'runtime_env_image_updated_sha256':hashlib.sha256(new).hexdigest(),'source_sha256':hashlib.sha256(value).hexdigest(),'file_changed':False,'prior_image_entry_count':count}))
