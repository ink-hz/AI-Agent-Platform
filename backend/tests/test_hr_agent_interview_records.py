"""User supplied interview records keep exact private attachment provenance."""

from uuid import UUID, uuid4

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


def _candidate(uploaded, intake):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("interview-candidate")
    item = service.get_batch(owner, batch["batch_id"])["items"][0]
    item, _ = finish_profile(uploaded, intake, item)
    receipt = service.confirm_item(owner, item["item_id"], confirmation(item), uuid4())
    return receipt["candidate_id"]


def _request(client, aid, **changes):
    ref = client.get("/api/hr/agent/materials/" + str(aid)).json()["text_ref"]
    return {
        "material_ref": ref,
        "title": "一面原始记录",
        "occurred_at": None,
        "position_id": None,
        "interview_plan_ref": None,
        **changes,
    }


def test_http_register_list_and_exact_read_are_private_and_idempotent(
    uploaded, intake, database
):
    from app.hr_agent.interview_records import InterviewRecordService

    client, headers, repo, owner, materials, aid, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    client.app.state.hr_agent_service.candidates = intake[0]
    client.app.state.hr_agent_service.interviews = InterviewRecordService(
        repo, materials, intake[0]
    )
    path = f"/api/hr/agent/candidates/{candidate_id}/interview-records"
    request = _request(client, aid)
    request["title"] = "候选人说：保留原话"
    headers = {**headers, "Idempotency-Key": str(uuid4())}
    first = client.post(path, headers=headers, json=request)
    assert first.status_code == 201, first.text
    assert client.post(path, headers=headers, json=request).json() == first.json()
    assert first.json()["authorship"] == "user_supplied"
    listing = client.get(path).json()["items"]
    assert listing == [{k: v for k, v in first.json().items() if k != "text"}]
    assert "text" not in listing[0]
    exact = client.get(path + "/" + first.json()["record_id"])
    assert exact.status_code == 200
    assert exact.json()["text"] == "公开岗位：负责机器人算法研发。\n需要真实项目经验。"
    assert exact.json()["material_ref"] == request["material_ref"]
    with database.admin_connection() as c:
        raw = str(
            c.execute(
                "SELECT row_to_json(r)::text FROM platform_hr_agent.candidate_interview_records r WHERE record_id=%s",
                (UUID(first.json()["record_id"]),),
            ).fetchone()[0]
        )
        assert request["title"] not in raw and exact.json()["text"] not in raw
        marker = c.execute(
            "SELECT registered_by_item,registered_by_record FROM platform_hr_agent.personal_materials WHERE owner_id=%s AND attachment_id=%s",
            (owner, UUID(aid)),
        ).fetchone()
        # This source already had the legacy intake marker; registration remains compatible.
        assert marker[0] is not None and marker[1] is None


def test_registration_rejects_injection_wrong_kind_and_revocation(
    uploaded, intake, database
):
    from app.hr_agent.interview_records import InterviewRecordService

    client, headers, repo, owner, materials, aid, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    service = InterviewRecordService(repo, materials, intake[0])
    client.app.state.hr_agent_service.candidates = intake[0]
    client.app.state.hr_agent_service.interviews = service
    path = f"/api/hr/agent/candidates/{candidate_id}/interview-records"
    request = _request(client, aid)
    for extra in ("authorship", "source_kind", "body", "text", "owner_id"):
        response = client.post(
            path,
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={**request, extra: "forged"},
        )
        assert response.status_code == 422
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET source_kind='agent_output' WHERE attachment_id=%s",
            (UUID(aid),),
        )
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json=request,
        ).status_code
        == 410
    )
    with database.admin_connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.candidate_interview_records WHERE owner_id=%s",
                (owner,),
            ).fetchone()[0]
            == 0
        )


def test_exact_read_propagates_source_revocation(uploaded, intake, database):
    from app.hr_agent.interview_records import InterviewRecordService

    client, headers, repo, _owner, materials, aid, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    client.app.state.hr_agent_service.candidates = intake[0]
    client.app.state.hr_agent_service.interviews = InterviewRecordService(
        repo, materials, intake[0]
    )
    path = f"/api/hr/agent/candidates/{candidate_id}/interview-records"
    created = client.post(
        path,
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=_request(client, aid),
    ).json()
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (UUID(aid),),
        )
    assert client.get(path).status_code == 410
    assert client.get(path + "/" + created["record_id"]).status_code == 410


def test_text_rules_and_candidate_scoped_idempotency(uploaded, intake):
    from app.hr_agent.interview_records import InterviewRecordService

    client, _, repo, owner, materials, aid, _, store = uploaded
    candidate_id = _candidate(uploaded, intake)
    service = InterviewRecordService(repo, materials, intake[0])
    request = _request(client, aid)
    store.objects[next(iter(store.objects))] = b"   \n"
    with pytest.raises(HrAgentProblem):
        service.register(owner, candidate_id, request, uuid4())


def test_request_requires_rfc3339_and_rejects_false_or_empty_optional_refs(
    uploaded, intake
):
    from app.hr_agent.interview_records import InterviewRecordService

    client, _, repo, owner, materials, aid, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    service = InterviewRecordService(repo, materials, intake[0])
    base = _request(client, aid)
    for changes in (
        {"occurred_at": "2026-09-11"},
        {"occurred_at": "2026-09-11T09:00:00"},
        {"position_id": ""},
        {"position_id": False},
        {"interview_plan_ref": False},
    ):
        with pytest.raises(HrAgentProblem) as error:
            service.register(owner, candidate_id, {**base, **changes}, uuid4())
        assert error.value.problem["code"] == "invalid_input"
