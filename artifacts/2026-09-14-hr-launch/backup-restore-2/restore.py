"""Restore the verified off-host encrypted snapshot in an owned local PG17 instance."""
from pathlib import Path
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from uuid import uuid4

from app.cloud_replica.backup import decrypt_stream

os.umask(0o077)
PRIVATE = Path('/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-launch-2026-09-14-backup')
EVIDENCE = Path(__file__).resolve().parent
RAW = PRIVATE/'restore-2'
RAW.mkdir(mode=0o700, exist_ok=False)
BIN = Path('/opt/homebrew/bin')
RUN = uuid4().hex
ADMIN = 'platform_owner'
ROOT = Path(tempfile.mkdtemp(prefix='hrrestore-', dir='/private/tmp'))
(ROOT/'owned-run').write_text(RUN)
DATA = ROOT/'data'
receipt = {'run_id': RUN, 'started_at': time.time(), 'status': 'running',
           'production_database_changed': False, 'source_backup_run': 'dc4b996ae61b4390aab3dc1e70ba6228',
           'scope': 'actual encrypted off-host snapshot, complete globals/ownership/ACL and control DB restore; local Unix socket only',
           'steps': []}
started = False


def persist():
    (EVIDENCE/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')


def command(name, argv, timeout=180):
    step = {'name': name, 'argv': [str(a) for a in argv], 'started_at': time.time()}
    receipt['steps'].append(step)
    persist()
    with (RAW/(name+'.stdout')).open('xb') as out, (RAW/(name+'.stderr')).open('xb') as err:
        result = subprocess.run(argv, stdout=out, stderr=err, timeout=timeout,
                                env={**os.environ, 'PGHOST': str(ROOT), 'PGPORT': '5432', 'PGUSER': ADMIN})
    step.update(finished_at=time.time(), exit_code=result.returncode)
    persist()
    if result.returncode:
        raise RuntimeError('restore_step_failed:'+name)
    return (RAW/(name+'.stdout')).read_bytes()


persist()
try:
    with (PRIVATE/'control-backup.orb').open('rb') as source, (RAW/'backup.tar').open('xb') as target:
        metadata = decrypt_stream(source, target, (PRIVATE/'recovery-private.key').read_bytes())
        target.flush()
        os.fsync(target.fileno())
    receipt['decryption_authenticated'] = True
    receipt['plaintext_bytes'] = metadata.plaintext_size
    expected = json.loads((EVIDENCE.parent/'backup-refresh/stdout.json').read_text())['files']
    with tarfile.open(RAW/'backup.tar', 'r:') as archive:
        members = archive.getmembers()
        if len(members) != 4 or {m.name for m in members} != set(expected):
            raise RuntimeError('archive_members_mismatch')
        for member in members:
            if not member.isfile() or member.size != expected[member.name]['bytes']:
                raise RuntimeError('archive_member_invalid')
            digest = hashlib.sha256()
            with archive.extractfile(member) as source, (RAW/member.name).open('xb') as output:
                for chunk in iter(lambda: source.read(1048576), b''):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected[member.name]['sha256']:
                raise RuntimeError('decrypted_source_mismatch')
    receipt['all_four_source_files_sha256_match'] = True
    command('initdb', [BIN/'initdb', '-D', DATA, '--username='+ADMIN,
                      '--auth-local=trust', '--auth-host=reject', '--encoding=UTF8', '--no-locale'])
    options = shlex.join(['-c', "listen_addresses=", '-c', 'unix_socket_directories='+str(ROOT),
                          '-c', 'fsync=on', '-c', 'log_statement=none'])
    command('start', [BIN/'pg_ctl', '-D', DATA, '-l', RAW/'postgres.log', '-o', options, '-w', 'start'])
    started = True
    receipt['local_postgres_pid'] = int((DATA/'postmaster.pid').read_text().splitlines()[0])
    receipt['tcp_listen_addresses'] = ''
    psql = [BIN/'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-h', ROOT, '-U', ADMIN]
    # Match the source bootstrap role: PG role grantor identity has special bootstrap semantics.
    # Skip exactly its already-created CREATE, preserving every ALTER and GRANT unchanged.
    globals_source = (RAW/'globals.sql').read_text()
    bootstrap_create = 'CREATE ROLE platform_owner;\n'
    if globals_source.count(bootstrap_create) != 1:
        raise RuntimeError('unexpected_bootstrap_create_count')
    (RAW/'globals-restore.sql').write_text(globals_source.replace(bootstrap_create, '', 1))
    receipt['bootstrap_restore_adjustment'] = 'same platform_owner bootstrap identity; omit only its one redundant CREATE ROLE statement'
    command('globals', [*psql, '-d', 'postgres', '-f', RAW/'globals-restore.sql'])
    network = command('network', [*psql, '-d', 'postgres', '-c', 'show listen_addresses']).decode().strip()
    if network != '':
        raise RuntimeError('local_restore_tcp_listener_enabled')
    receipt['tcp_listen_verified'] = True
    command('database', [BIN/'createdb', '-h', ROOT, '-U', ADMIN, '-O', 'platform_control_owner', 'agent_platform_control'])
    command('restore', [BIN/'pg_restore', '--exit-on-error', '-h', ROOT, '-U', ADMIN,
                        '-d', 'agent_platform_control', RAW/'production.dump'], timeout=300)
    query = "select json_agg(json_build_object('version',version,'sha256',sha256) order by version) from platform_control.schema_migrations"
    ledger = json.loads(command('ledger', [*psql, '-d', 'agent_platform_control', '-c', query]))
    original = json.loads((RAW/'production-ledger.json').read_text())
    if ledger != original:
        raise RuntimeError('restored_ledger_mismatch')
    receipt['ledger_exact_match'] = True
    receipt['ledger_count'] = len(ledger)
    query = "select json_build_object('tables',(select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind='r' and n.nspname like 'platform_%'),'conversations',(select count(*) from platform_control.conversations),'erasure_jobs',(select count(*) from platform_attachments.erasure_jobs),'identity_boundary',(select current_database()))"
    receipt['restored_summary'] = json.loads(command('summary', [*psql, '-d', 'agent_platform_control', '-c', query]))
    receipt['status'] = 'restored'
except BaseException as error:
    receipt.update(status='failed', failure_type=type(error).__name__)
finally:
    try:
        # A failed pg_ctl client may still have launched this owned postmaster.
        if started or (DATA/'postmaster.pid').exists():
            command('stop', [BIN/'pg_ctl', '-D', DATA, '-m', 'fast', '-w', 'stop'], timeout=30)
        receipt['owned_postgres_stopped'] = not (DATA/'postmaster.pid').exists()
    except BaseException:
        receipt.update(status='failed', owned_postgres_stopped=False)
    if receipt.get('owned_postgres_stopped') and receipt['status'] == 'restored':
        if (ROOT/'owned-run').read_text() != RUN:
            raise RuntimeError('temporary_root_ownership_changed')
        shutil.rmtree(ROOT)
        receipt['owned_data_directory_removed'] = True
    receipt['finished_at'] = time.time()
    receipt['limitation'] = 'Point-in-time prepare snapshot; this is not a database rollback instruction after erasure fences, nor a restore of MinIO payloads or a zero-RPO claim.'
    persist()
    print(json.dumps({k:v for k,v in receipt.items() if k != 'steps'}))
raise SystemExit(0 if receipt['status'] == 'restored' and receipt.get('owned_postgres_stopped') else 1)
