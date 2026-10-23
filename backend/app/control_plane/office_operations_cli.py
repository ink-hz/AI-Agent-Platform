"""One-time trusted history backfill; prints aggregate counts only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.local_secrets import read_secret_file
from .crypto import EncryptedDirectoryAttribute, IdentityKeyring, ProviderIdentityCodec
from .dsn import validate_control_dsn


def backfill(database_url, *, codec, corp_id, excluded_actor):
    validate_control_dsn(database_url,purpose='maintenance')
    with psycopg.connect(database_url,connect_timeout=3,options='-c statement_timeout=30000',row_factory=dict_row) as connection:
        rows=connection.execute('select * from platform_control.office_backfill_profiles_v124()').fetchall()
        departments={}
        for row in rows:
            value=None
            if row['primary_department_ciphertext'] is not None:
                value=codec.open_attribute(EncryptedDirectoryAttribute(
                    purpose='primary_department',ciphertext=row['primary_department_ciphertext'],
                    nonce=row['primary_department_nonce'],encryption_key_version=row['primary_department_encryption_key_version']),
                    corp_id,row['generation_id'],row['member_key'],'primary_department')
            choices=row['departments'] or []
            if not value and len(choices)==1:
                value=choices[0]
            value=(value or '未归属部门').strip()
            if not value or len(value)>160:
                raise ValueError('invalid department snapshot')
            actor=str(row['internal_user_id'])
            if actor in departments:
                raise ValueError('ambiguous historical identity')
            departments[actor]=value
        result=connection.execute('select platform_control.backfill_office_operations_v124(%s,%s) inserted',
            (Jsonb(departments),excluded_actor)).fetchone()
        return {'daily_rows_inserted':result['inserted'],'directory_subjects':len(departments)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-file',required=True)
    parser.add_argument('--encryption-keyring-file',required=True)
    parser.add_argument('--lookup-keyring-file',required=True)
    parser.add_argument('--excluded-account-file',required=True,help='Private file containing the verified internal UUID only')
    args=parser.parse_args(argv)
    path=Path(args.excluded_account_file)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('excluded account input must be a private regular file')
    excluded_actor=UUID(path.read_text().strip())
    codec=ProviderIdentityCodec(
        IdentityKeyring.from_file(args.encryption_keyring_file,expected_purpose='provider-encryption',expected_key_length=32),
        IdentityKeyring.from_file(args.lookup_keyring_file,expected_purpose='provider-lookup-hmac',expected_key_length=32))
    corp_id=os.environ['PLATFORM_DINGTALK_CORP_ID']
    result=backfill(read_secret_file(args.database_url_file),codec=codec,corp_id=corp_id,excluded_actor=excluded_actor)
    print(json.dumps({'status':'ok',**result}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
