from pathlib import Path
import json,stat
rows={}
for name in ('attachment-s3-access-key','attachment-s3-secret-key'):
 p=Path('/var/lib/docker/volumes/orbbec-agent-platform-api-secrets/_data')/name;s=p.lstat();rows[name]={'uid':s.st_uid,'mode':oct(stat.S_IMODE(s.st_mode)),'regular':stat.S_ISREG(s.st_mode),'symlink':p.is_symlink()}
r=Path('/opt/orbbec-agent-platform/private/hr-conditional-probe-ca7f1f1d1c5c4df7b431f2745d1c6a80')
for name in ('evidence','evidence/binding.json','probe.py'):
 p=r/name;s=p.lstat();rows[name]={'uid':s.st_uid,'mode':oct(stat.S_IMODE(s.st_mode)),'symlink':p.is_symlink()}
print(json.dumps(rows))
