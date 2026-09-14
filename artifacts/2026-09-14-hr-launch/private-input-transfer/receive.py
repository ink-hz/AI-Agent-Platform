"""Receive exactly seven private files; never extract arbitrary archive paths."""
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile

TARGET = Path('/opt/orbbec-agent-platform/private/hr-incremental-ad5-inputs')
NAMES = {'hr-budget-profile.json', 'hr-content-keyring.json',
         'hr-diagnostic-profile.json', 'hr-provider-credential',
         'hr-provider-profile.json', 'hr-release-policy.json', 'knowledge.tar.gz'}
if os.getuid() != 0:
    raise SystemExit('root_required')
os.umask(0o077)
parent = TARGET.parent.lstat()
if TARGET.parent.is_symlink() or parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) != 0o700:
    raise SystemExit('private_parent_invalid')
payload = sys.stdin.buffer.read(1_048_577)
if len(payload) > 1_048_576:
    raise SystemExit('input_too_large')
with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
    members = archive.getmembers()
    if len(members) != len(NAMES) or {m.name for m in members} != NAMES:
        raise SystemExit('input_members_invalid')
    if any(not m.isfile() or m.size > 524288 for m in members):
        raise SystemExit('input_type_invalid')
    TARGET.mkdir(mode=0o700, exist_ok=False)
    for member in members:
        body = archive.extractfile(member).read()
        if len(body) != member.size:
            raise SystemExit('input_truncated')
        fd = os.open(TARGET/member.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    descriptor = os.open(TARGET, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
print(json.dumps({'status': 'private_inputs_copied', 'path': str(TARGET),
                  'file_count': len(NAMES), 'services_changed': False,
                  'database_changed': False, 'installed': False}))
