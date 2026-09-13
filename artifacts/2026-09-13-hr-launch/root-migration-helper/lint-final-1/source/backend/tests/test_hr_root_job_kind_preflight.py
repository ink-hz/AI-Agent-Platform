"""Real v5 signed completion proves queued transport envelopes are valid job kinds."""
import importlib.util
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_hr_cutover_count_review import complete_v5
from test_hr_cutover_dispatch_review import (
    attempt_repository,
    bindings,
    conversation_database,
    database,
    direct_database,
    repository,
    signed_api,
    signed_http_app,
    transport_worker,
)
from tests.test_hr_agent_migration_deployment import (
    ROOT,
    SUPERVISOR,
    _write_fake_docker,
)

_FIXTURES = (attempt_repository, bindings, conversation_database, database, direct_database,
             repository, signed_api, signed_http_app, transport_worker)


def supervisor(tmp_path, monkeypatch, db):
    fake = tmp_path / 'docker'
    _write_fake_docker(fake)
    monkeypatch.setenv('HR_MIGRATION_DOCKER', str(fake))
    monkeypatch.setenv('TEST_ADMIN_DSN', db.admin_dsn)
    monkeypatch.setenv('PYTHONPATH', str(ROOT / 'backend'))
    spec = importlib.util.spec_from_file_location('root_host_supervisor', os.environ.get('HR_TEST_SUPERVISOR_SOURCE', str(SUPERVISOR)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Supervisor(SimpleNamespace(release=str(ROOT), private=str(tmp_path), migration_set='root', environment='production', postgres='test-postgres', command_timeout=10))


def test_modern_preflight_accepts_three_signed_completed_v5_still_queued(database, repository, conversation_database, attempt_repository, bindings, signed_api, transport_worker, tmp_path, monkeypatch):
    for _ in range(3):
        complete_v5(database, repository, conversation_database[1], attempt_repository, bindings, signed_api, transport_worker)
    helper = supervisor(tmp_path, monkeypatch, database)
    helper.job_kind_preflight()
    assert helper.receipt['job_kind_preflight']['state'] == 'classified'
    with database.admin_connection() as c:
        assert c.execute("select count(*) from platform_control.execution_jobs where job_kind='worker_direct_v5' and status='queued'").fetchone()[0] == 3
        assert c.execute("select count(*) from platform_control.turn_attempts where status='completed'").fetchone()[0] == 3


@pytest.mark.parametrize('fault', ['orphan', 'constraint_drift'])
def test_modern_preflight_rejects_unknown_schema_or_v5_identity(database, tmp_path, monkeypatch, fault):
    with database.admin_connection() as c:
        if fault == 'orphan':
            c.execute("insert into platform_control.execution_jobs(job_id,run_id,agent_id,job_kind,payload_ciphertext,encryption_key_version,status) values(%s,%s,'hr-bot','worker_direct_v5',%s,1,'queued')", (uuid4(), uuid4(), b'fixture'))
        else:
            c.execute('alter table platform_control.execution_jobs drop constraint execution_jobs_job_kind_v42')
    helper = supervisor(tmp_path, monkeypatch, database)
    with pytest.raises(Exception, match='job_kind_preflight_failed'):
        helper.job_kind_preflight()
    assert 'fixture' not in json.dumps(helper.receipt)


@pytest.mark.parametrize('fault', ['transport_run', 'executor_kind', 'conversation_lineage'])
def test_modern_preflight_rejects_real_linkage_corruption(database, repository, conversation_database, attempt_repository, bindings, signed_api, transport_worker, tmp_path, monkeypatch, fault):
    _, lease, _ = complete_v5(database, repository, conversation_database[1], attempt_repository, bindings, signed_api, transport_worker)
    other = None
    if fault == 'conversation_lineage':
        other, _, _ = complete_v5(database, repository, conversation_database[1], attempt_repository, bindings, signed_api, transport_worker)
    with database.admin_connection() as c:
        if fault == 'transport_run':
            c.execute('update platform_control.turn_attempts set transport_run_id=%s where attempt_id=%s', (uuid4(), lease.attempt_id))
        elif fault == 'executor_kind':
            c.execute("update platform_control.turn_attempts set executor_kind='legacy_api_v1' where attempt_id=%s", (lease.attempt_id,))
        else:
            c.execute('update platform_control.turn_attempts set turn_id=%s,attempt_no=2 where attempt_id=%s', (other.turn.turn_id, lease.attempt_id))
    helper = supervisor(tmp_path, monkeypatch, database)
    with pytest.raises(Exception, match='job_kind_preflight_failed'):
        helper.job_kind_preflight()
