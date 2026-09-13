"""Real PG cloud recovery: no transfer to a different execution owner.

Repository scope is a synthetic public/no-reference fixture; no model is called.
"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from app.hr_agent.cutover import CutoverRejected
from app.hr_agent.repository import HrAgentRepository
from tests.hr_agent_support import hr_agent_database, make_hr_settings


@pytest.fixture
def cloud(tmp_path):
    with hr_agent_database() as database:
        settings = make_hr_settings(tmp_path)
        repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings,
                                 scope_validator=lambda *args: None)
        owner = uuid4()
        body = {"thread_id": None, "text": "公开岗位讨论", "objects": [],
                "references": [], "budget_profile": "test"}
        work = repo.submit(owner, body, uuid4())
        fence = repo.claim("owned-worker", 60)
        yield database, repo, owner, body, work, fence


def transition(database, phase, request_id=None):
    dsn = database.dsn.replace("user=platform_control_app", "user=platform_control_maintenance")
    with psycopg.connect(dsn) as c:
        c.execute("SET LOCAL lock_timeout='2s'")
        c.execute("SET LOCAL statement_timeout='3s'")
        return c.execute("select * from platform_control.transition_hr_execution_cutover_v102(%s,%s)",
                         (phase, request_id or uuid4())).fetchone()


def test_resume_cloud_preserves_active_work_and_reopens_only_cloud_admission(cloud):
    database, repo, owner, body, work, fence = cloud
    drained = transition(database, "draining_cloud")
    with pytest.raises(CutoverRejected):
        repo.submit(owner, body, uuid4())
    assert repo.renew(fence, 60)
    with database.admin_connection() as c:
        before = c.execute("select * from platform_hr_agent.works where work_id=%s", (work["work_id"],)).fetchone()
    resumed = transition(database, "cloud")
    assert resumed[1] == "cloud" and resumed[2] == drained[2] + 1
    with database.admin_connection() as c:
        assert c.execute("select * from platform_hr_agent.works where work_id=%s", (work["work_id"],)).fetchone() == before
    assert repo.renew(fence, 60)
    assert repo.submit(owner, body, uuid4())["work_id"] != work["work_id"]


def test_resume_cloud_concurrent_same_request_has_one_receipt(cloud):
    database, _, _, _, _, _ = cloud
    drained = transition(database, "draining_cloud")
    request = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: transition(database, "cloud", request), range(2)))
    assert receipts[0] == receipts[1]
    assert receipts[0][2] == drained[2] + 1
    with database.admin_connection() as c:
        assert c.execute("select count(*) from platform_control.hr_execution_cutover_operations where request_id=%s", (request,)).fetchone()[0] == 1
    with pytest.raises(psycopg.Error, match="replay mismatch"):
        transition(database, "draining_cloud", request)


def test_resume_cloud_rejects_residual_legacy_occupancy_without_receipt(cloud):
    database, _, _, _, _, _ = cloud
    drained = transition(database, "draining_cloud")
    request = uuid4()
    with database.admin_connection() as c:
        # Isolated residual fault fixture, never a fabricated successful drain.
        c.execute("insert into platform_control.execution_jobs(job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,status) values(%s,%s,'hr-bot',%s,1,'queued')", (uuid4(), uuid4(), b'synthetic'))
    with pytest.raises(psycopg.Error, match="drain is incomplete"):
        transition(database, "cloud", request)
    with database.admin_connection() as c:
        assert c.execute("select * from platform_control.hr_execution_cutover").fetchone() == drained
        assert c.execute("select count(*) from platform_control.hr_execution_cutover_operations where request_id=%s", (request,)).fetchone()[0] == 0


def test_resume_keeps_application_role_unprivileged(cloud):
    database, _, _, _, _, _ = cloud
    transition(database, "draining_cloud")
    with database.connection() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
