"""Owner health presentation and real-DB candidate availability diagnostics."""
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from app.hr.candidate_models import RetryCandidateDraft
from app.hr.candidate_repository import CandidateUnavailable
from app.hr_agent.service import HrAgentService
from test_dingtalk_auth_api import FakeAuth, _app
from test_hr_failed_draft_cutover import failed_draft

_FIXTURES = (failed_draft,)


@pytest.mark.parametrize("ready,has_repository,expected", [(False, False, False), (True, False, False), (True, True, True)])
def test_owner_health_exposes_api_assembly_readiness_without_changing_public_liveness(
    tmp_path, monkeypatch, ready, has_repository, expected,
):
    auth = FakeAuth()
    app = _app(tmp_path, monkeypatch, auth)
    audited = []
    app.state.system_health_audit = lambda context: audited.append(context.session_id)
    # Only HR assembly is substituted; actual owner route, audit gate and public route execute.
    app.state.hr_agent_service = HrAgentService(object() if has_repository else None, object(), ready=ready)
    with TestClient(app) as client:
        result = client.get("/api/v1/manage/system-health", cookies={auth.cookie_name: "valid-cookie"})
        assert result.status_code == 200
        assert result.json()["dependencies"]["services"]["hr_agent"] == {
            "api_ready": expected, "worker_checked": False,
        }
        assert client.get("/api/health").json() == {"status": "ok"}
    assert audited == [auth.context.session_id]


@pytest.mark.postgres
@pytest.mark.parametrize("failure_kind", ["cutover_paused", "database"])
def test_candidate_unavailable_keeps_safe_distinct_operational_reason(failed_draft, caplog, failure_kind):
    database, repository, ids = failed_draft
    caplog.set_level(logging.INFO, logger="app.hr.candidate_repository")
    with database.admin_connection() as connection:
        if failure_kind == "cutover_paused":
            connection.execute(
                "select * from platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)",
                (uuid4(),),
            )
        else:
            connection.execute("alter table platform_hr.candidate_drafts rename to synthetic_unavailable_drafts")
    with pytest.raises(CandidateUnavailable):
        repository.retry_draft(RetryCandidateDraft(ids["owner"], ids["draft"], uuid4(), 2))
    records = [r for r in caplog.records if r.name == "app.hr.candidate_repository"]
    assert len(records) == 1
    assert records[0].getMessage() == "candidate_repository_unavailable"
    assert records[0].failure_kind == failure_kind
    assert records[0].sqlstate == (None if failure_kind == "cutover_paused" else "42P01")
    assert records[0].exc_info is None
    assert "candidate_drafts" not in caplog.text
