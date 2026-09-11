"""C1 user HTTP boundary, marked personal sources, and new candidate authority."""

from uuid import uuid4

import pytest
from app.hr_agent.types import HrAgentProblem
from tests.test_hr_agent_candidate_intake import (
    batch_request,
    confirmation,
    finish_profile,
    intake,
)
from tests.test_hr_agent_materials import database, secured, uploaded

_FIXTURES = (database, secured, uploaded, intake)


def test_batch_http_confirmed_candidate_and_scope(uploaded, intake, database):
    client, headers, _repo, owner, _, aid, _, _ = uploaded
    candidate_service, resources = intake
    client.app.state.hr_agent_service.candidates = candidate_service
    response = client.post(
        "/api/hr/agent/candidate-batches",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=batch_request([aid]),
    )
    assert response.status_code == 201, response.text
    batch = response.json()
    assert (
        client.get("/api/hr/agent/candidate-batches").json()["items"][0]["batch_id"]
        == batch["batch_id"]
    )
    candidate_service.advance_one("coordinator")
    item = client.get("/api/hr/agent/candidate-batches/" + batch["batch_id"]).json()[
        "items"
    ][0]
    item, _ = finish_profile(uploaded, intake, item)
    path = "/api/hr/agent/candidate-items/" + item["item_id"]
    assert client.get(path).json()["state"] == "awaiting_review"
    confirm_headers = {**headers, "Idempotency-Key": str(uuid4())}
    response = client.post(
        path + "/confirm",
        headers=confirm_headers,
        json=confirmation(item),
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            path + "/confirm", headers=confirm_headers, json=confirmation(item)
        ).json()
        == response.json()
    )
    candidate_id = response.json()["candidate_id"]
    assert (
        client.get("/api/hr/agent/candidates/" + candidate_id).json()["display_name"]
        == confirmation(item)["display_name"]
    )
    assert (
        client.get("/api/hr/agent/candidates").json()["items"][0]["candidate_id"]
        == candidate_id
    )
    obj = {"kind": "candidate", "id": candidate_id}
    resources.validate_scope(owner, [obj], [])
    with pytest.raises(HrAgentProblem):
        resources.validate_scope(uuid4(), [obj], [])
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (aid,),
        )
    with pytest.raises(HrAgentProblem):
        resources.validate_scope(owner, [obj], [])
    assert client.get("/api/hr/agent/candidates/" + candidate_id).status_code == 410


def test_batch_http_rejects_identity_injection_missing_csrf_and_foreign_item(
    uploaded, intake
):
    client, headers, _, _, _, aid, _, _ = uploaded
    client.app.state.hr_agent_service.candidates = intake[0]
    path = "/api/hr/agent/candidate-batches"
    request = batch_request([aid])
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={**request, "owner_id": str(uuid4())},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers={"Idempotency-Key": str(uuid4())}, json=request
        ).status_code
        == 403
    )
    assert (
        client.get("/api/hr/agent/candidate-items/" + str(uuid4())).status_code == 404
    )


def test_unconfirmed_resume_marker_rejects_generic_standard_sources(uploaded, intake):
    from app.hr_agent.proposals import validate_sources

    client, _, repo, owner, _, aid, _, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    intake[0].create_batch(owner, batch_request([aid]), uuid4())
    with repo.transaction() as c:
        with pytest.raises(HrAgentProblem) as error:
            validate_sources(repo, c, owner, [ref])
        assert error.value.problem["code"] == "personal_source_not_allowed"


def test_unconfigured_candidate_http_is_unavailable(uploaded):
    client, headers, _, _, _, aid, _, _ = uploaded
    response = client.post(
        "/api/hr/agent/candidate-batches",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=batch_request([aid]),
    )
    assert response.status_code == 503


def test_marked_resume_cannot_bypass_processing_gate_through_normal_work(
    uploaded, intake
):
    from app.hr_agent.runtime import run_work
    from tests.test_hr_agent_runtime import ScriptModel, answer

    client, _, repo, owner, _, aid, _, _ = uploaded
    intake[0].create_batch(owner, batch_request([aid]), uuid4())
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    repo.personal_processing_authorizer = None
    work = repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "普通入口读取这份材料",
            "objects": [],
            "references": [ref],
            "budget_profile": "test",
        },
        uuid4(),
    )
    model = ScriptModel([answer()])
    done = run_work(repo, model, intake[1], repo.claim("c1-gate", 60))
    assert done["work_id"] == work["work_id"] and done["state"] == "blocked"
    assert model.requests == []


def test_other_authenticated_owner_cannot_read_real_existing_intake_item(
    uploaded, intake
):
    client, headers, _, owner, _, aid, _, _ = uploaded
    service = intake[0]
    client.app.state.hr_agent_service.candidates = service
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    item = batch["item_ids"][0]
    assert client.get("/api/hr/agent/candidate-items/" + item).status_code == 200
    middleware_auth = next(
        m.kwargs["auth"] for m in client.app.user_middleware if "auth" in m.kwargs
    )
    middleware_auth.owner_id = uuid4()
    assert client.get("/api/hr/agent/candidate-items/" + item).status_code == 404
    assert (
        client.get("/api/hr/agent/candidate-batches/" + batch["batch_id"]).status_code
        == 404
    )
    assert (
        client.post(
            "/api/hr/agent/candidate-items/" + item + "/retry",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"expected_row_version": 1, "stage": "profile"},
        ).status_code
        == 404
    )
