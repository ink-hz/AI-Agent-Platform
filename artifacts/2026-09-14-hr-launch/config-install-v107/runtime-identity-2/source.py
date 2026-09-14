from pathlib import Path
import hashlib,json,stat
p=Path('/opt/orbbec-agent-platform/private/hr-agent/runtime.env')
s=p.lstat();assert not p.is_symlink() and stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_uid==0
b=p.read_bytes();assert hashlib.sha256(b).hexdigest()=='68024083ee6b26e6033c8bab0074132df26bc2fc7012e5480efca0f639cfd2d8'
new=b'\n'.join(line for line in b.splitlines() if not line.startswith(b'PLATFORM_IMAGE='))+b'\nPLATFORM_IMAGE=sha256:0327cc3a6259994b2546aaf3d3e3ca3fa13911e7d53afe7a78d4a9342a293213\n'
print(json.dumps({'old_sha256':hashlib.sha256(b).hexdigest(),'new_runtime_sha256':hashlib.sha256(new).hexdigest(),'wrote_files':False},sort_keys=True))
