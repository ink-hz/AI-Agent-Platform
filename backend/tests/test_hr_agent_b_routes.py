from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from app.hr_agent.standards import StandardService
from tests import test_hr_agent_routes as route_fixtures
from tests.test_hr_agent_proposals import proposal_args
from tests.test_hr_agent_standards import confirmation

secured = route_fixtures.secured
database = route_fixtures.database
_FIXTURES = (secured, database)


def test_http_partial_confirmation_csrf_and_parallel_conflict(secured):
    client, headers, repo, _owner = secured
    client.app.state.hr_agent_service.standards = StandardService(repo)
    # Object ownership test is separately exercised by ResourceReader's actual
    # database adapter; here HrAccess still rejects unauthorized shape/identity.
    repo.scope_validator = lambda *args: None
    position = {"kind": "position", "id": str(uuid4())}
    repo.settings = replace(
        repo.settings, budget_profile={**repo.settings.budget_profile, "id": "test"}
    )
    # Use authenticated submit and exact work/model tool lifecycle.
    body = {
        "thread_id": None,
        "text": "公开岗位校准",
        "objects": [position],
        "references": [],
        "budget_profile": "test",
    }
    submitted = client.post("/api/hr/agent/works", json=body, headers=headers)
    assert submitted.status_code == 201, submitted.text
    fence = repo.claim("http-test", 60)
    from app.hr_agent.types import ModelContext, ModelReply, ToolCall, Usage

    context = ModelContext(
        "work", ({"role": "user", "content": "公开岗位"},), (), (), 100, 1
    )
    attempt = repo.prepare_model(fence, context)
    repo.mark_model_sending(fence, attempt.attempt_id)
    ops = repo.commit_model(
        fence,
        attempt.attempt_id,
        ModelReply(
            "",
            (ToolCall("proposal", "save_result", proposal_args(position)),),
            "stop",
            Usage(None, 100, 20, "reported"),
        ),
    )
    outcome = repo.execute_local_tool(fence, ops[0])
    assert outcome["status"] == "ok", outcome
    proposal = outcome["data"]
    url = f"/api/hr/agent/positions/{position['id']}/standards"
    payload = confirmation(proposal, [proposal["changes"][0]["change_id"]])
    assert client.post(url + "/confirm", json=payload).status_code == 403
    first = client.post(
        url + "/confirm",
        json=payload,
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 1
    assert client.get(url + "/current").json() == first.json()
    conflict = client.post(
        url + "/confirm",
        json=payload,
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert conflict.status_code == 409
    assert (
        conflict.json()["details"]["current_revision"]
        == first.json()["ref"]["revision"]
    )
    assert len(client.get(url + "/current").json()["items"]) == 1


def test_catalog_and_exact_body_use_published_bytes_and_current_auth(secured, tmp_path):
    from app.hr_agent.knowledge import KnowledgeReleases
    from tools.hr_agent.build_knowledge_release import build

    client, _, repo, _ = secured
    build(Path(__file__).parents[1] / "hr_agent_knowledge", tmp_path)
    client.app.state.hr_agent_service.knowledge = KnowledgeReleases(tmp_path)
    catalog = client.get("/api/hr/agent/knowledge")
    assert catalog.status_code == 200, catalog.text
    ref = catalog.json()["items"][0]["ref"]
    path = f"/api/hr/agent/knowledge/{ref['id']}/revisions/{ref['revision']}"
    response = client.get(path, params={"sha256": ref["sha256"]})
    assert response.status_code == 200, response.text
    import hashlib

    assert hashlib.sha256(response.json()["text"].encode()).hexdigest() == ref["sha256"]
    assert client.get(path, params={"sha256": "0" * 64}).status_code == 410
    config = client.get("/api/hr/agent/configuration").json()
    assert config["budget_profile"] == repo.settings.budget_profile["id"]
    assert "model" not in config
    client.cookies.clear()
    assert client.get("/api/hr/agent/knowledge").status_code == 401
