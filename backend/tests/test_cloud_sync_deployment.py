"""Host rollout tools never execute a real Docker command in these tests."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest
import yaml
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg import sql

from app.cloud_sync.models import SourceProfile
from app.cloud_sync.sources import _tables
from test_observability_migration_runner import observability_database

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'deploy/cloud/provision-agent-sync.py'


def provisioner():
    assert SCRIPT.exists(), 'cloud sync host provisioner is missing'
    spec = importlib.util.spec_from_file_location('cloud_sync_provisioner', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_worker_is_explicit_isolated_hardened_and_has_no_api_secrets():
    path = ROOT / 'deploy/cloud/compose.agent-sync.yaml'
    assert path.exists(), 'isolated cloud sync compose file is missing'
    value = yaml.safe_load(path.read_text())
    assert value['name'] == 'orbbec-agent-cloud-sync'
    assert set(value['services']) == {'cloud-agent-sync'}
    service = value['services']['cloud-agent-sync']
    assert service['profiles'] == ['cloud-sync']
    assert service['image'].startswith('${PLATFORM_CLOUD_SYNC_IMAGE:')
    assert service['user'] == '10001:10001'
    assert service['read_only'] is True
    assert service['cap_drop'] == ['ALL']
    assert service['security_opt'] == ['no-new-privileges:true']
    assert 'ports' not in service and 'expose' not in service
    assert service['command'] == ['python', '-m', 'app.cloud_sync.cli', 'run', '--config', '/run/cloud-sync/config.json']
    assert len(service['volumes']) == 1
    mount = service['volumes'][0]
    assert mount['target'] == '/run/cloud-sync' and mount['read_only'] is True
    assert mount['bind']['create_host_path'] is False
    assert 'private/cloud-agent-sync' in mount['source']
    assert not any(s in path.read_text() for s in ('docker.sock', 'platform-api-secrets', 'signing-private'))
    assert all('size=' in v and 'uid=10001' in v and 'noexec' in v for v in service['tmpfs'])
    assert {n['name'] for n in value['networks'].values()} == {
        'deploy_default', 'orbbec-voc_voc_private', 'orbbec-agent-platform-internal'}
    assert all(n['external'] is True for n in value['networks'].values())
    assert set(service['networks']) == set(value['networks'])
    assert service['healthcheck']['test'] == ['CMD', 'python', '-m', 'app.cloud_sync.cli', 'health', '--config', '/run/cloud-sync/config.json']


def test_provisioning_is_private_idempotent_and_sql_is_only_stdin(tmp_path, capsys):
    tool = provisioner()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b'', stderr=b'')

    private = tmp_path / 'private'
    tool.provision(private, runner=fake_run, uid=os.getuid(), gid=os.getgid())
    first = {p.name: p.read_bytes() for p in private.iterdir()}
    tool.provision(private, runner=fake_run, uid=os.getuid(), gid=os.getgid())
    assert first == {p.name: p.read_bytes() for p in private.iterdir()}
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in private.iterdir())
    config = json.loads(first['config.json'])
    assert config['interval_seconds'] == 10 and config['include_verified_subject'] is True
    assert len(config['sources']) == 4
    assert config['identity_key_file'] == '/run/cloud-sync/identity-key'
    assert config['replica_encryption_key_file'] == '/run/cloud-sync/replica-encryption-key'
    assert all(len(json.loads(first[f'config-{kind}.json'])['sources']) == 1 for kind in ('fae','daq','admin','voc'))
    assert 'identity-key' not in first and 'replica-encryption-key' not in first
    dsns = [content.decode().strip() for name, content in first.items() if name.endswith('-database-url')]
    assert len(dsns) == 5
    assert any('@orbbec-voc-postgres-1:5432/orbbec_voc' in d for d in dsns)
    assert any('@ai-fae-postgres:5432/ai_fae' in d for d in dsns)
    for argv, kwargs in calls:
        assert argv[:3] == ['docker', 'exec', '-i']
        assert 'psql' in argv and '-c' not in argv
        assert '-X' in argv and 'ON_ERROR_STOP=1' in argv
        assert kwargs['capture_output'] is True and kwargs['check'] is False
        assert isinstance(kwargs['input'], bytes)
        assert all(d not in ' '.join(argv) for d in dsns)
        assert all(d.split(':', 2)[2].split('@')[0] not in ' '.join(argv) for d in dsns)
        assert b'set_agent_publisher' not in kwargs['input']
        assert b'insert into platform_replica.agent_sources' not in kwargs['input'].lower()
    assert capsys.readouterr() == ('', '')


def test_source_role_grants_match_adapter_allowlist_and_never_grant_business_writes():
    tool = provisioner()
    for kind, profile in tool.SOURCES.items():
        expected = _tables(SourceProfile(profile['agent_id'], kind, '/secret', profile['database'], profile['schema']))
        assert tool.TABLES[kind] == expected
        script = tool.source_role_sql(kind, 'test_password_never_in_argv')
        assert 'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS' in script
        assert 'default_transaction_read_only' in script
        grants = [line for line in script.splitlines() if line.startswith('GRANT SELECT')]
        assert len(grants) == len(expected)
        for (schema, table), columns in expected.items():
            assert f'GRANT SELECT ({", ".join(columns)}) ON {schema}.{table} TO {profile["role"]};' in grants
        assert not any(word in script for word in ('GRANT ALL', 'GRANT INSERT', 'GRANT UPDATE', 'GRANT DELETE'))


@pytest.mark.parametrize('unsafe', ['../secrets', '/tmp/a/../b', '/tmp/x\nsecret'])
def test_unsafe_secret_directory_is_rejected_without_running_docker(unsafe):
    tool = provisioner()
    with pytest.raises(tool.ProvisionError, match='cloud_sync_input_invalid'):
        tool.provision(Path(unsafe), runner=lambda *a, **k: pytest.fail('Docker must not run'))


def test_symlink_and_insecure_existing_credentials_fail_closed(tmp_path):
    tool = provisioner()
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    target = tmp_path / 'target'
    target.write_text('do not read')
    (private / 'fae-database-url').symlink_to(target)
    with pytest.raises(tool.ProvisionError, match='cloud_sync_private_file_invalid'):
        tool.provision(private, uid=os.getuid(), gid=os.getgid(), runner=lambda *a, **k: pytest.fail('Docker must not run'))
    assert target.read_text() == 'do not read'


def test_psql_failures_are_payload_free(tmp_path, capsys):
    tool = provisioner()
    def fail(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout=b'private payload', stderr=b'password: private payload')
    with pytest.raises(tool.ProvisionError, match='^cloud_sync_database_operation_failed$'):
        tool.provision(tmp_path / 'private', runner=fail, uid=os.getuid(), gid=os.getgid())
    assert capsys.readouterr() == ('', '')


def test_cli_refuses_non_root_before_mutation(monkeypatch, capsys):
    tool = provisioner()
    monkeypatch.setattr(tool.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(tool, 'provision', lambda *a, **k: pytest.fail('must not provision'))
    assert tool.main(['provision']) == 1
    assert capsys.readouterr() == ('', 'cloud_sync_root_required\n')


@pytest.mark.parametrize('kind', ('fae', 'daq', 'admin', 'voc'))
def test_role_sql_does_not_accept_untrusted_password_sql(kind):
    tool = provisioner()
    with pytest.raises(tool.ProvisionError, match='cloud_sync_input_invalid'):
        tool.source_role_sql(kind, "x'; SELECT dangerous(); --")


@pytest.mark.parametrize('publisher,mode,writer', [('cloud','cloud_direct','platform_cloud_sync_worker'), ('office','office',None)])
def test_takeover_and_rollback_only_use_controlled_superuser_function(publisher, mode, writer):
    tool = provisioner()
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b'', stderr=b'')
    tool.set_publisher('fae', publisher, 'known-instance-id', runner=fake_run)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[-4:] == ['-U', 'platform_owner', '-d', 'agent_platform']
    query = kwargs['input'].decode()
    assert query == f"SELECT platform_replica.set_agent_publisher('ai-fae-agent', 'fae', '{mode}', '{publisher}', {repr(writer) if writer else 'NULL'}, 'known-instance-id');\n"
    assert 'INSERT' not in query and 'UPDATE' not in query
    with pytest.raises(tool.ProvisionError, match='cloud_sync_input_invalid'):
        tool.set_publisher('fae', publisher, "x'); DROP ROLE important; --", runner=fake_run)
    assert len(calls) == 1


@pytest.mark.postgres
@pytest.mark.parametrize('kind', ('fae', 'daq', 'admin', 'voc'))
def test_generated_source_role_sql_reconciles_real_column_privileges(observability_database, monkeypatch, kind):
    tool = provisioner()
    database = f'cloud_role_test_{kind}'
    with psycopg.connect(observability_database, autocommit=True) as owner:
        owner.execute(sql.SQL('create database {}').format(sql.Identifier(database)))
    monkeypatch.setitem(tool.SOURCES[kind], 'database', database)
    dsn = make_conninfo(observability_database, dbname=database)
    role = tool.SOURCES[kind]['role']
    (schema, table), columns = next(iter(tool.TABLES[kind].items()))
    source_table = sql.Identifier(schema, table)
    with psycopg.connect(dsn, autocommit=True) as owner:
        for namespace in {s for s, _ in tool.TABLES[kind]}:
            owner.execute(sql.SQL('create schema if not exists {}').format(sql.Identifier(namespace)))
        for (namespace, relation), fields in tool.TABLES[kind].items():
            owner.execute(sql.SQL('create table {} ({})').format(sql.Identifier(namespace, relation),
                sql.SQL(', ').join(sql.SQL('{} text').format(sql.Identifier(field)) for field in fields)))
        owner.execute(sql.SQL('insert into {} ({}) values (\'synthetic record\')').format(source_table, sql.Identifier(columns[0])))
        owner.execute(sql.SQL('alter table {} add column raw_private_trace text').format(source_table))
        owner.execute('create table public.unrelated_business (private_text text)')
        owner.execute('create schema extra_business')
        owner.execute('create table extra_business.unrelated (private_text text)')
        old_role = f'old_reader_{kind}'
        owner.execute(sql.SQL('create role {} nologin').format(sql.Identifier(old_role)))
        for _ in range(2):
            owner.execute(tool.source_role_sql(kind, 'synthetic_private_password'))
            # Simulate a stale privilege from an older deployment before reconciliation.
            if _ == 0:
                owner.execute(sql.SQL('grant select (raw_private_trace) on {} to {}').format(source_table, sql.Identifier(role)))
                owner.execute(sql.SQL('grant select on public.unrelated_business to {}').format(sql.Identifier(role)))
                owner.execute(sql.SQL('grant usage on schema extra_business to {}').format(sql.Identifier(role)))
                owner.execute(sql.SQL('grant select on extra_business.unrelated to {}').format(sql.Identifier(role)))
                owner.execute(sql.SQL('grant {} to {}').format(sql.Identifier(old_role), sql.Identifier(role)))
        row = owner.execute('select rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls from pg_roles where rolname=%s', (role,)).fetchone()
        assert row == (False, False, False, False, False, False)
        assert owner.execute("select pg_has_role(%s,%s,'MEMBER')", (role,old_role)).fetchone() == (False,)
    worker_dsn = make_conninfo(dsn, user=role, password='synthetic_private_password')
    with psycopg.connect(worker_dsn, autocommit=True) as worker:
        assert worker.execute('show default_transaction_read_only').fetchone() == ('on',)
        assert worker.execute(sql.SQL('select {} from {}').format(sql.Identifier(columns[0]), source_table)).fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute(sql.SQL('select raw_private_trace from {}').format(source_table))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute('select * from public.unrelated_business')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute('select * from extra_business.unrelated')
        worker.execute('set default_transaction_read_only=off')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute(sql.SQL("update {} set {}='forbidden'").format(source_table, sql.Identifier(columns[0])))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute(sql.SQL('delete from {}').format(source_table))


def test_voc_only_repair_reuses_existing_credentials_and_never_touches_other_sources(tmp_path):
    tool = provisioner()
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    credential = private / 'voc-database-url'
    credential.write_text('postgresql://platform_sync_voc_read:existing_password@orbbec-voc-postgres-1:5432/orbbec_voc\n')
    credential.chmod(0o600)
    before = credential.read_bytes()
    calls = []
    def run(argv, **kwargs):
        calls.append((argv,kwargs))
        return subprocess.CompletedProcess(argv,0,stdout=b'',stderr=b'')
    tool.repair_voc_observation(private,runner=run,uid=os.getuid())
    assert credential.read_bytes() == before
    assert list(private.iterdir()) == [credential]
    assert len(calls) == 1
    assert calls[0][0][-4:] == ['-U','postgres','-d','orbbec_voc']
    assert calls[0][1]['input'].decode() == tool.source_role_sql('voc','existing_password')
    credential.unlink()
    with pytest.raises(tool.ProvisionError,match='cloud_sync_private_file_invalid'):
        tool.repair_voc_observation(private,runner=lambda *a,**k:pytest.fail('must not provision'),uid=os.getuid())
    assert not list(private.iterdir())
