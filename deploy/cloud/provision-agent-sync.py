#!/usr/bin/env python3
"""Root-only host provisioning. No Docker socket or DDL is given to the worker.

Install the reviewed replica migration after backup before running this tool.
Provisioning deliberately does not assign publisher ownership or copy keys.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from urllib.parse import urlsplit


class ProvisionError(ValueError):
    """Stable diagnostics without SQL, credentials or business payloads."""


SOURCES = {
    'fae': dict(agent_id='ai-fae-agent', database='ai_fae', schema='public',
                role='platform_sync_fae_read', container='ai-fae-postgres', owner='ai_fae'),
    'daq': dict(agent_id='ai-daq-fae-agent', database='ai_fae', schema='daq_fae',
                role='platform_sync_daq_read', container='ai-fae-postgres', owner='ai_fae'),
    'admin': dict(agent_id='ai-admin-agent', database='ai_admin', schema='public',
                role='platform_sync_admin_read', container='ai-fae-postgres', owner='ai_fae'),
    'voc': dict(agent_id='voc', database='orbbec_voc', schema='voc_raw/voc_core',
                role='platform_sync_voc_read', container='orbbec-voc-postgres-1', owner='postgres'),
}
REPLICA = dict(database='agent_platform', container='orbbec-agent-platform-platform-postgres-1',
               host='platform-postgres', owner='platform_owner', role='platform_cloud_sync_worker')
RUNTIME = '/run/cloud-sync'
SESSION = ('id', 'external_session_id', 'channel', 'user_id', 'external_user_id',
           'conversation_title', 'created_at', 'last_active_at', 'metadata')
TURN = ('id', 'session_id', 'turn_index', 'question', 'answer', 'created_at',
        'question_at', 'answer_at', 'outcome', 'fallback_used', 'duration_ms', 'sources', 'done', 'metadata')
FEEDBACK = ('id', 'turn_id', 'rating', 'created_at')
REVIEW = ('id', 'turn_id', 'review_status', 'created_at', 'updated_at')
TABLES = {
    kind: {
        (schema, prefix + 'chat_sessions'): SESSION + identity,
        (schema, prefix + 'chat_turns'): TURN,
        (schema, prefix + 'turn_feedback'): FEEDBACK,
        (schema, prefix + 'turn_reviews'): review,
    }
    for kind, schema, prefix, identity, review in (
        ('fae', 'public', '', ('owner_subject_id', 'owner_subject_type'), REVIEW),
        ('daq', 'daq_fae', '', ('owner_subject_id', 'owner_subject_type'), REVIEW),
        ('admin', 'public', 'admin_', ('internal_user_id', 'verification_method', 'verified_at'),
         ('id', 'turn_id', 'verdict', 'created_at')),
    )
}
TABLES['daq'][('daq_fae', 'daq_installation_identity')] = ('singleton', 'agent_id', 'database_name')
TABLES['voc'] = {
    ('voc_raw', 'inbound_messages'): ('id', 'sender_internal_user_id', 'conversation_id',
        'conversation_type', 'sender_name', 'sender_dept', 'received_at', 'dingtalk_created_at', 'text_content'),
    ('voc_raw', 'interaction_jobs'): ('inbound_message_id', 'status'),
    ('voc_core', 'outbox_messages'): ('source_inbound_message_id', 'status', 'content', 'sent_at'),
    ('voc_meta', 'schema_migrations'): ('name',),
}


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value):
        raise ProvisionError('cloud_sync_input_invalid')
    return value


def _role_sql(role, password, *, inherit=False):
    _token(role)
    _token(password)
    return f"""BEGIN;
SET LOCAL log_statement = 'none';
SET LOCAL log_min_error_statement = 'panic';
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role} LOGIN;
  END IF;
END $$;
ALTER ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE {'INHERIT' if inherit else 'NOINHERIT'} NOREPLICATION NOBYPASSRLS PASSWORD '{password}';
DO $$ DECLARE membership record; BEGIN
  FOR membership IN SELECT parent.rolname FROM pg_auth_members m
    JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member
    WHERE child.rolname='{role}' LOOP
    EXECUTE format('REVOKE %I FROM %I', membership.rolname, '{role}');
  END LOOP;
END $$;
"""


def source_role_sql(kind, password):
    if kind not in SOURCES:
        raise ProvisionError('cloud_sync_input_invalid')
    profile = SOURCES[kind]
    role, database = profile['role'], profile['database']
    schemas = sorted({schema for schema, _ in TABLES[kind]})
    schema_list = ', '.join(schemas)
    script = _role_sql(role, password)
    script += f"REVOKE ALL ON DATABASE {database} FROM {role};\nGRANT CONNECT ON DATABASE {database} TO {role};\n"
    script += f"ALTER ROLE {role} IN DATABASE {database} SET default_transaction_read_only = on;\n"
    # This dedicated login must not retain an older deployment's wider grants.
    script += f"""DO $$ DECLARE business_schema record; BEGIN
  FOR business_schema IN SELECT nspname FROM pg_namespace
    WHERE nspname <> 'information_schema' AND nspname NOT LIKE 'pg_%' LOOP
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', business_schema.nspname, '{role}');
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I', business_schema.nspname, '{role}');
    EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM %I', business_schema.nspname, '{role}');
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM %I', business_schema.nspname, '{role}');
  END LOOP;
END $$;
"""
    # Table-level REVOKE does not remove old column grants.
    script += f"""DO $$ DECLARE old_grant record; BEGIN
  FOR old_grant IN SELECT table_schema, table_name, column_name, privilege_type
    FROM information_schema.column_privileges WHERE grantee='{role}' LOOP
    EXECUTE format('REVOKE %s (%I) ON %I.%I FROM %I', old_grant.privilege_type,
      old_grant.column_name, old_grant.table_schema, old_grant.table_name, '{role}');
  END LOOP;
END $$;
GRANT USAGE ON SCHEMA {schema_list} TO {role};
"""
    for (schema, table), columns in TABLES[kind].items():
        script += f'GRANT SELECT ({", ".join(columns)}) ON {schema}.{table} TO {role};\n'
    # Fail visibly if PUBLIC or ownership still gives the dedicated role writes.
    script += f"""DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
      AND c.relkind IN ('r','p','v','m','f')
      AND has_table_privilege('{role}', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')) THEN
    RAISE EXCEPTION 'cloud_sync_source_role_not_readonly';
  END IF;
END $$;
COMMIT;
"""
    return script


def _psql(profile, script, runner):
    argv = ['docker', 'exec', '-i', profile['container'], 'psql', '-X', '-q',
            '-v', 'ON_ERROR_STOP=1', '-U', profile['owner'], '-d', profile['database']]
    try:
        result = runner(argv, input=script.encode(), capture_output=True, check=False, timeout=60)
        if result.returncode:
            raise ProvisionError('cloud_sync_database_operation_failed')
    except (OSError, subprocess.SubprocessError):
        raise ProvisionError('cloud_sync_database_operation_failed') from None


def _read_private(path, uid):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != uid or metadata.st_size > 65536):
            raise ValueError
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            return stream.read(65537)
    except (OSError, ValueError):
        raise ProvisionError('cloud_sync_private_file_invalid') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_private(path, value, uid, gid):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = None
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise ProvisionError('cloud_sync_private_file_invalid') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _dsn(path, profile, uid, gid):
    host = profile.get('host', profile['container'])
    prefix = f"postgresql://{profile['role']}:"
    suffix = f"@{host}:5432/{profile['database']}"
    if path.exists() or path.is_symlink():
        try:
            value = _read_private(path, uid).decode().strip()
            if not value.startswith(prefix) or not value.endswith(suffix):
                raise ValueError
            password = value[len(prefix):-len(suffix)]
            _token(password)
            parsed = urlsplit(value)
            if parsed.username != profile['role'] or parsed.hostname != host:
                raise ValueError
            return password
        except (ValueError, UnicodeError):
            raise ProvisionError('cloud_sync_private_file_invalid') from None
    password = secrets.token_urlsafe(36)
    _write_private(path, f'{prefix}{password}{suffix}\n'.encode(), uid, gid)
    return password


def provision(private_dir, *, runner=subprocess.run, uid=10001, gid=10001):
    private_dir = Path(private_dir)
    if (not private_dir.is_absolute() or '..' in private_dir.parts
            or any(ord(c) < 32 for c in str(private_dir))):
        raise ProvisionError('cloud_sync_input_invalid')
    try:
        if not private_dir.exists() and not private_dir.is_symlink():
            private_dir.mkdir(mode=0o700)
            os.chown(private_dir, uid, gid)
        metadata = private_dir.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_uid != uid):
            raise ValueError
    except (OSError, ValueError):
        raise ProvisionError('cloud_sync_private_directory_invalid') from None
    credentials = {kind: _dsn(private_dir / f'{kind}-database-url', p, uid, gid) for kind, p in SOURCES.items()}
    credentials['replica'] = _dsn(private_dir / 'replica-database-url', REPLICA, uid, gid)
    config = dict(replica_database_url_file=f'{RUNTIME}/replica-database-url',
                  replica_encryption_key_file=f'{RUNTIME}/replica-encryption-key',
                  identity_key_file=f'{RUNTIME}/identity-key', interval_seconds=10, include_verified_subject=True,
                  sources=[dict(agent_id=p['agent_id'], kind=kind, dsn_file=f'{RUNTIME}/{kind}-database-url',
                                expected_database=p['database'], schema=p['schema']) for kind, p in SOURCES.items()])
    configs = {'config.json': config}
    configs.update({f'config-{p["kind"]}.json': dict(config, sources=[p]) for p in config['sources']})
    for name, value in configs.items():
        data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
        target = private_dir / name
        if target.exists() or target.is_symlink():
            if _read_private(target, uid) != data:
                raise ProvisionError('cloud_sync_private_file_invalid')
        else:
            _write_private(target, data, uid, gid)
    for kind, p in SOURCES.items():
        _psql(p, source_role_sql(kind, credentials[kind]), runner)
    # The migration owns and limits this group; the dedicated login owns no DB objects.
    script = _role_sql(REPLICA['role'], credentials['replica'], inherit=True)
    script += f"GRANT CONNECT ON DATABASE {REPLICA['database']} TO {REPLICA['role']};\n"
    script += f"GRANT platform_cloud_sync TO {REPLICA['role']};\nCOMMIT;\n"
    _psql(REPLICA, script, runner)


def set_publisher(kind, publisher, source_instance_id, *, runner=subprocess.run):
    if kind not in SOURCES or publisher not in ('cloud', 'office'):
        raise ProvisionError('cloud_sync_input_invalid')
    _token(source_instance_id)
    p = SOURCES[kind]
    mode = 'cloud_direct' if publisher == 'cloud' else 'office'
    source_kind = 'fae' if kind == 'daq' else kind
    writer = f"'{REPLICA['role']}'" if publisher == 'cloud' else 'NULL'
    script = (f"SELECT platform_replica.set_agent_publisher('{p['agent_id']}', '{source_kind}', "
              f"'{mode}', '{publisher}', {writer}, '{source_instance_id}');\n")
    _psql(REPLICA, script, runner)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    install = sub.add_parser('provision')
    install.add_argument('--private-dir', default='/opt/orbbec-agent-platform/private/cloud-agent-sync')
    ownership = sub.add_parser('set-publisher')
    ownership.add_argument('--source', choices=tuple(SOURCES), required=True)
    ownership.add_argument('--publisher', choices=('cloud', 'office'), required=True)
    ownership.add_argument('--source-instance-id', required=True)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ProvisionError('cloud_sync_root_required')
        if args.command == 'provision':
            provision(args.private_dir)
        else:
            set_publisher(args.source, args.publisher, args.source_instance_id)
        print('CLOUD_SYNC_PROVISION_OK' if args.command == 'provision' else 'CLOUD_SYNC_PUBLISHER_CHANGED')
        return 0
    except Exception as error:
        print(str(error) if isinstance(error, ProvisionError) else 'cloud_sync_provision_failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
