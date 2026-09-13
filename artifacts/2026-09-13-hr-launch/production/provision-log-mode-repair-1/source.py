from pathlib import Path
import os,stat,hashlib,json
root=Path('/data/orbbec-agent-platform/release-metadata/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7/hr-provision-4cd463bbaf897639b891b04555bd0c78')
s=root.lstat()
if root.is_symlink() or not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=0o700:raise RuntimeError('metadata guard failed')
expected={'stdout.log':'345e531bdc1720966bdadcba4bc1ee19deca04410b86c531735ba2438362cec5','stderr.log':'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}
report={}
for name,digest in expected.items():
 p=root/name;f=p.lstat()
 if p.is_symlink() or not stat.S_ISREG(f.st_mode) or f.st_uid!=0 or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('log identity mismatch')
 if stat.S_IMODE(f.st_mode) not in {0o600,0o644}:raise RuntimeError('log mode unexpected')
 report[name]={'before':oct(stat.S_IMODE(f.st_mode)),'sha256':digest}
for name in expected:
 p=root/name;p.chmod(0o600);report[name]['after']=oct(stat.S_IMODE(p.lstat().st_mode))
 if report[name]['after']!='0o600':raise RuntimeError('repair failed')
print(json.dumps({'files':report,'content_changed':False,'scope':'two fixed provisioning metadata logs'},indent=2))
