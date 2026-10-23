#!/usr/bin/env python3
"""Prepare private partner keyrings. Does not enable login or replace existing keys."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import stat
import sys


def prepare(directory: Path) -> None:
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError
    metadata = directory.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise ValueError
    definitions = (
        ('partner-encryption-keyring', 'partner-provider-encryption', False),
        ('partner-hmac-keyring', 'partner-provider-lookup-hmac', True),
    )
    all_keys = []
    for filename, purpose, transition in definitions:
        path = directory / filename
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            document = {'purpose': purpose, 'active_version': 1,
                        'keys': {'1': base64.b64encode(secrets.token_bytes(32)).decode('ascii')}}
            if transition:
                document['transition_versions'] = [1]
            with os.fdopen(descriptor, 'w') as output:
                json.dump(document, output)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise ValueError
            document = json.loads(source.read(65537))
        expected = {'purpose', 'active_version', 'keys'} | ({'transition_versions'} if transition else set())
        if set(document) != expected or document['purpose'] != purpose:
            raise ValueError
        active = document['active_version']
        if type(active) is not int or active <= 0 or str(active) not in document['keys']:
            raise ValueError
        keys = []
        for version, encoded in document['keys'].items():
            if not version.isascii() or not version.isdigit() or str(int(version)) != version or int(version) <= 0:
                raise ValueError
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError
            keys.append(key)
        if transition:
            versions = document['transition_versions']
            if not isinstance(versions, list) or not 1 <= len(versions) <= 3 or any(type(v) is not int or str(v) not in document['keys'] for v in versions):
                raise ValueError
            if versions != list(range(versions[0], versions[0] + len(versions))) or active not in versions:
                raise ValueError
        if any(key in all_keys for key in keys):
            raise ValueError
        all_keys.extend(keys)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError
        prepare(Path(sys.argv[1]))
    except Exception:
        print('FAE_PARTNER_SECRETS_FAILED', file=sys.stderr)
        raise SystemExit(1) from None
    print('FAE_PARTNER_SECRETS_PREPARED')
