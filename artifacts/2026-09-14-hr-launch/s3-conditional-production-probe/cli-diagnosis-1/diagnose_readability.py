"""Read-only metadata/open diagnostic. No boto, S3, writes or secret output."""
import json
import os
from pathlib import Path

result = {'uid': os.getuid(), 'euid': os.geteuid(), 'secret_readability': {}}
try:
    status = Path('/proc/self/status').read_text()
    result['linux_capabilities'] = {
        line.split(':', 1)[0]: line.split(':', 1)[1].strip()
        for line in status.splitlines()
        if line.startswith(('CapEff:', 'CapPrm:', 'CapBnd:'))
    }
except OSError:
    result['linux_capabilities'] = 'unavailable'
for name in ('attachment-s3-access-key', 'attachment-s3-secret-key'):
    path = Path('/run/secrets') / name
    row = {}
    try:
        info = path.lstat()
        row.update(owner_uid=info.st_uid, mode=oct(info.st_mode & 0o777))
        with path.open('rb') as source:
            source.read(1)  # Discard; never emit content, length or digest.
        row['open_read_ok'] = True
    except OSError as error:
        row.update(open_read_ok=False, errno=error.errno, exception=type(error).__name__)
    result['secret_readability'][name] = row
print(json.dumps(result, sort_keys=True))
