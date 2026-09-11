"""An unsupported basis must not be reported as an unsupported research result."""

from uuid import uuid4

from app.hr_agent.runtime import run_work
from tests.test_hr_agent_candidate_intake import intake
from tests.test_hr_agent_materials import database, secured, uploaded
from tests.test_hr_agent_runtime import ScriptModel, answer, tool

_FIXTURES = (database, secured, uploaded, intake)


def test_research_basis_failure_identifies_field_and_recovery_saves_result(
    uploaded, intake
):
    client, _headers, repo, owner, _, aid, _, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    work = repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "研究公开归档",
            "objects": [],
            "references": [ref],
            "budget_profile": "test",
        },
        uuid4(),
    )
    result = {
        "kind": "research",
        "title": "公开归档研究",
        "body": "样例：这是静态归档，不代表当前官网。",
        "result_id": None,
        "expected_revision": None,
        "objects": [],
        "source_refs": [ref],
        "preceding_refs": [],
        "base_standard_ref": None,
        "changes": [],
        "basis": [{"kind": "official_original", "ref": ref, "input_revision": None}],
    }
    model = ScriptModel(
        [
            tool("save_result", result),
            tool("save_result", dict(result, basis=[])),
            answer(),
        ]
    )
    done = run_work(repo, model, intake[1], repo.claim("research-result", 60))
    assert done["state"] == "completed" and len(done["result_refs"]) == 1
    with repo.transaction() as c:
        c.execute(
            "SELECT * FROM platform_hr_agent.operations WHERE work_id=%s AND namespace='tool:save_result' ORDER BY created_at",
            (work["work_id"],),
        )
        rows = c.fetchall()
        first = repo._unseal(
            "operations", rows[0]["operation_id"], "sealed_receipt", rows[0]
        )
    assert first["error"]["code"] == "unsupported_kind"
    assert first["error"]["details"] == {"field": "basis.kind"}
    assert "source_refs" in first["error"]["message"]
