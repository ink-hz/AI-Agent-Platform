"""Pack and encrypt an existing immutable backup; no database/network access."""
from pathlib import Path
import hashlib
import json
import os
import sys
import tarfile
import tempfile

from app.cloud_replica.backup import encrypt_stream

os.umask(0o077)
source = Path('/source')
target = Path('/output')
expected = json.loads((source/'receipt.json').read_text())
if expected['deployment_id'] != 'dc4b996ae61b4390aab3dc1e70ba6228' or expected['status'] != 'completed':
    raise SystemExit('backup_identity_invalid')
names = ['production.dump', 'globals.sql', 'production-schema.sql', 'production-ledger.json']
with tempfile.TemporaryFile(dir=target) as packed:
    with tarfile.open(fileobj=packed, mode='w') as archive:
        for name in names:
            path = source/name
            digest = hashlib.sha256()
            with path.open('rb') as body:
                for chunk in iter(lambda: body.read(1048576), b''):
                    digest.update(chunk)
            if digest.hexdigest() != expected['files'][name]['sha256'] or path.stat().st_size != expected['files'][name]['bytes']:
                raise SystemExit('backup_file_identity_invalid')
            info = archive.gettarinfo(str(path), arcname=name)
            info.mode = 0o600
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            with path.open('rb') as body:
                archive.addfile(info, body)
    packed.seek(0)
    output = target/'control-backup.orb'
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        metadata = encrypt_stream(packed, stream, bytes.fromhex(sys.argv[1]))
        stream.flush()
        os.fsync(stream.fileno())
digest = hashlib.sha256()
with output.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1048576), b''):
        digest.update(chunk)
print(json.dumps({'status': 'encrypted', 'plaintext_bytes': metadata.plaintext_size,
                  'encrypted_file_bytes': output.stat().st_size,
                  'encrypted_file_sha256': digest.hexdigest(), 'source_backup_run': expected['deployment_id'],
                  'original_private_snapshot_retained': True}))
