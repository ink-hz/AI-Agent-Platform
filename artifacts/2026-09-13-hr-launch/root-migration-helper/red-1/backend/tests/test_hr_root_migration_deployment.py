"""Production-only host helper, real disposable PostgreSQL and Docker CLI boundary double."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from app.control_plane.migrate import migrate_control_database
from tests.hr_agent_support import hr_agent_database
from tests.test_hr_agent_migration_deployment import ROOT, SUPERVISOR, _memberships, _write_fake_docker


@pytest.fixture
def root_deployment(tmp_path):
    with hr_agent_database(migrate_hr=False) as db:
        migrate_control_database(db.migrator_dsn, ROOT / 'backend/control_migrations/hr_web', owner_role='platform_control_owner')
        with db.admin_connection() as connection:
            connection.execute('revoke platform_control_owner from platform_control_migrator')
        private = tmp_path / 'private'
        private.mkdir()
        secret = private / 'control-migrator-database-url'
        secret.write_text(db.migrator_dsn)
        secret.chmod(0o600)
        release = tmp_path / 'release'
        shutil.copytree(ROOT / 'backend/control_migrations', release / 'backend/control_migrations')
        fake = tmp_path / 'docker'
        _write_fake_docker(fake)
        source = fake.read_text().replace("migration_dir=pathlib.Path(os.environ['TEST_RELEASE'])/'backend/control_migrations/hr_agent'", "migration_dir=pathlib.Path(os.environ['TEST_RELEASE'])/state['environment']['PLATFORM_CONTROL_MIGRATION_DIR'].removeprefix('/app/')")
        # Log actual exec database/query (synthetic DB only), preserve real SQL execution.
        source = source.replace("query=args[args.index('-c')+1] if '-c' in args else sys.stdin.read()", "query=args[args.index('-c')+1] if '-c' in args else sys.stdin.read()\n    pathlib.Path(os.environ['TEST_SQL_LOG']).open('a').write(json.dumps({'database':database,'query':query})+'\\n')")
        fake.write_text(source)
        env = {**os.environ, 'HR_MIGRATION_DOCKER': str(fake), 'TEST_ADMIN_DSN': db.admin_dsn,
               'TEST_PRIVATE': str(private), 'TEST_RELEASE': str(release), 'TEST_DOCKER_STATE': str(tmp_path / 'state'),
               'TEST_DOCKER_CALLS': str(tmp_path / 'calls'), 'TEST_SQL_LOG': str(tmp_path / 'sql.log'),
               'PYTHONPATH': str(ROOT / 'backend')}
        args = [sys.executable, str(SUPERVISOR), str(release), str(private), 'sha256:' + 'a' * 64,
                'test-postgres', '--receipt-dir', str(tmp_path / 'receipts'), '--migration-set', 'root', '--environment', 'production']
        yield db, release, tmp_path, env, args


def invoke(case):
    return subprocess.run(case[-1], env=case[-2], capture_output=True, text=True, timeout=30, check=False)


def test_root_production_applies_only_root_and_reruns_with_exact_ledgers(root_deployment):
    db, release, tmp, _, _ = root_deployment
    (release / 'backend/control_migrations/106_test_root.sql').write_text('create table platform_control.root_scope_probe (id integer primary key);')
    # This HR migration must never execute in root mode.
    (release / 'backend/control_migrations/hr_agent/107_invalid.sql').write_text('invalid SQL must not run;')
    for _ in range(2):
        result = invoke(root_deployment)
        assert result.returncode == 0, result.stderr
    with db.admin_connection() as connection:
        assert connection.execute("select to_regclass('platform_control.root_scope_probe') is not null").fetchone()[0]
        assert connection.execute('select count(*) from platform_control.schema_migrations where version in (96,97,98,99,101,107)').fetchone()[0] == 0
    calls = (tmp / 'calls').read_text()
    assert calls.count('create ') == 2 and 'preview' not in calls
    statements = [json.loads(line) for line in (tmp / 'sql.log').read_text().splitlines()]
    assert {s['database'] for s in statements} <= {'postgres', 'agent_platform_control'}
    assert all('grant platform_control_owner_preview' not in s['query'] for s in statements)
    assert _memberships(db) == 0
    for receipt in (tmp / 'receipts').glob('*.json'):
        data = json.loads(receipt.read_text())
        assert data['migration_set'] == 'root' and data['requested_environment'] == 'production'
        assert data['cleanup_verified'] and data['status'] == 'completed'
        assert data['ledger_before'] and data['ledger_after']
        assert data['job_kind_preflight']['state'] == 'classified'
        assert len(data['job_kind_preflight']['script_sha256']) == 64


@pytest.mark.parametrize('fault', ['missing95', 'mismatch95', 'mismatch100', 'unknown_version', 'hr_already_applied', 'missing_root_file', 'job_kind_schema'])
def test_root_rejects_before_container_or_grant(root_deployment, fault):
    db, release, tmp, _, _ = root_deployment
    with db.admin_connection() as c:
        if fault == 'missing95': c.execute('delete from platform_control.schema_migrations where version=95')
        elif fault == 'mismatch95': c.execute("update platform_control.schema_migrations set sha256=repeat('a',64) where version=95")
        elif fault == 'mismatch100': c.execute("update platform_control.schema_migrations set sha256=repeat('a',64) where version=100")
        elif fault in ('unknown_version', 'hr_already_applied'):
            c.execute("insert into platform_control.schema_migrations(version,sha256,applied_at) values (%s,repeat('a',64),now())", (999 if fault == 'unknown_version' else 96,))
        elif fault == 'job_kind_schema': c.execute('alter table platform_control.execution_jobs rename column job_kind to invalid_job_kind')
    if fault == 'missing_root_file': next((release / 'backend/control_migrations').glob('100_*.sql')).unlink()
    result = invoke(root_deployment)
    assert result.returncode != 0
    assert not (tmp / 'calls').exists()
    assert _memberships(db) == 0
    receipt = json.loads(next((tmp / 'receipts').glob('*.json')).read_text())
    assert receipt['status'] == 'failed' and receipt['cleanup_verified']
    assert receipt['failure_code'] in {'root_ledger_invalid', 'root_baseline_invalid', 'job_kind_preflight_failed'}


def test_hr_explicit_production_needs_no_preview_and_checks_production_floor(root_deployment):
    db, _, tmp, _, args = root_deployment
    args[args.index('--migration-set') + 1] = 'hr'
    result = invoke(root_deployment)
    assert result.returncode == 0, result.stderr
    assert (tmp / 'calls').read_text().count('create ') == 1
    with db.admin_connection() as c:
        assert c.execute('select count(*) from platform_control.schema_migrations where version in (96,97,98,99,101)').fetchone()[0] == 5


def test_root_without_explicit_production_rejected_before_io(tmp_path):
    result = subprocess.run([sys.executable, str(SUPERVISOR), str(tmp_path), str(tmp_path), 'sha256:'+'a'*64, 'postgres', '--migration-set', 'root'], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert 'root requires --environment production' in result.stderr
