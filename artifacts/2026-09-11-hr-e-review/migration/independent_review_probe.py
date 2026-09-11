"""Independent read-only implementation review; local synthetic PostgreSQL only."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path.cwd()
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
from test_hr_agent_migration_deployment import _write_fake_docker, _prepare_preview_and_revoke, _memberships
from hr_agent_support import hr_agent_database


def probe(case):
    with tempfile.TemporaryDirectory(prefix='hr-independent-migration-') as temp:
        folder = Path(temp)
        with hr_agent_database(migrate_hr=False) as database:
            preview = _prepare_preview_and_revoke(database) if case == 'signal_during_cleanup' else database.migrator_dsn
            private = folder / 'private'; private.mkdir()
            for name, dsn in [('control-migrator-database-url', database.migrator_dsn), ('preview-control-migrator-database-url', preview)]:
                path = private / name; path.write_text(dsn); path.chmod(0o600)
            fake = folder / 'docker'; _write_fake_docker(fake)
            marker = folder / 'revoke-pending'
            if case == 'signal_during_cleanup':
                text = fake.read_text()
                needle = "    values=conninfo_to_dict(os.environ['TEST_ADMIN_DSN']); values['dbname']=database"
                replacement = """    marker=pathlib.Path(os.environ['REVIEW_REVOKE_MARKER'])
    if 'revoke platform_control_owner from platform_control_migrator' in query and not marker.exists():
        marker.touch()
        time.sleep(0.7)
""" + needle
                assert needle in text
                fake.write_text(text.replace(needle, replacement, 1))
            receipts, calls = folder / 'receipts', folder / 'calls'
            environment = {**os.environ,
                'PATH': str(ROOT / 'backend/.venv/bin') + ':' + os.environ.get('PATH', ''),
                'HR_MIGRATION_DOCKER': str(fake), 'TEST_ADMIN_DSN': database.admin_dsn,
                'TEST_PRIVATE': str(private), 'TEST_RELEASE': str(ROOT),
                'TEST_DOCKER_STATE': str(folder / 'state'), 'TEST_DOCKER_CALLS': str(calls),
                'REVIEW_REVOKE_MARKER': str(marker), 'PYTHONPATH': str(ROOT / 'backend')}
            proc = subprocess.Popen([str(ROOT / 'deploy/cloud/migrate-hr-agent.sh'), str(ROOT), str(private),
                'sha256:' + 'a' * 64, 'review-local-postgres', '--receipt-dir', str(receipts),
                '--command-timeout', '3', '--migration-timeout', '20'], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if case == 'signal_during_cleanup':
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline and not marker.exists() and proc.poll() is None:
                    time.sleep(.01)
                assert marker.exists(), 'cleanup revoke window did not arrive'
                proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=15)
            receipt = json.loads(next(receipts.glob('*.json')).read_text())
            operations = calls.read_text().splitlines() if calls.exists() else []
            result = dict(case=case, returncode=proc.returncode, stdout=stdout.strip(), stderr=stderr.strip(),
                started_environments=[line.rsplit('-', 1)[-1] for line in operations if line.startswith('create ')],
                start_count=sum(line.startswith('start ') for line in operations),
                owner_memberships_remaining=_memberships(database),
                receipt_status=receipt['status'], cleanup_verified=receipt['cleanup_verified'],
                failure_code=receipt.get('failure_code'),
                secret_owner_uid=(private / 'control-migrator-database-url').stat().st_uid,
                supervisor_sha256=hashlib.sha256((ROOT / 'deploy/cloud/hr_agent_migrate.py').read_bytes()).hexdigest())
            return result

for case in ['signal_during_cleanup', 'dirty_at_rest']:
    print(json.dumps(probe(case), sort_keys=True), flush=True)
