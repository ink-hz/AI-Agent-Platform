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
from tests.test_hr_agent_material_parsing import upload_document
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


def test_fresh_attachment_uses_record_marker_and_existing_privacy_gates(
    uploaded, intake, database
):
    from app.hr_agent.interview_records import InterviewRecordService
    from app.hr_agent.proposals import validate_sources
    from app.hr_agent.runtime import run_work
    from app.hr_agent.types import ResultQuery
    from tests.test_hr_agent_runtime import ScriptModel, answer, tool

    client, _, repo, owner, materials, _, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    aid = upload_document(
        uploaded,
        database,
        b"Candidate answered from first principles.",
        "text/plain",
        "interview.txt",
    )
    request = _request(client, aid)
    service = InterviewRecordService(repo, materials, intake[0])
    created = service.register(owner, candidate_id, request, uuid4())
    with repo.transaction() as c:
        c.execute(
            "SELECT registered_by_item,registered_by_record FROM platform_hr_agent.personal_materials WHERE owner_id=%s AND attachment_id=%s",
            (owner, UUID(aid)),
        )
        marker = c.fetchone()
        assert marker["registered_by_item"] is None
        assert str(marker["registered_by_record"]) == created["record_id"]
        with pytest.raises(HrAgentProblem) as error:
            validate_sources(repo, c, owner, [request["material_ref"]])
        assert error.value.problem["code"] == "personal_source_not_allowed"

    candidate_object = {"kind": "candidate", "id": candidate_id}
    repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "保存派生整理",
            "objects": [candidate_object],
            "references": [request["material_ref"]],
            "budget_profile": "test",
        },
        uuid4(),
    )
    derived_model = ScriptModel(
        [
            tool(
                "save_result",
                {
                    "kind": "interview_record",
                    "title": "派生整理",
                    "body": "仅供候选人工作使用。",
                    "result_id": None,
                    "expected_revision": None,
                    "objects": [candidate_object],
                    "source_refs": [request["material_ref"]],
                    "preceding_refs": [],
                    "base_standard_ref": None,
                    "changes": [],
                    "basis": [],
                },
            ),
            answer(),
        ]
    )
    assert (
        run_work(repo, derived_model, intake[1], repo.claim("record-derived", 60))[
            "state"
        ]
        == "completed"
    )
    derived = repo.list_results(owner, ResultQuery(object_ref=candidate_object))[
        "items"
    ][0]["ref"]
    with repo.transaction() as c, pytest.raises(HrAgentProblem) as error:
        validate_sources(repo, c, owner, [derived])
    assert error.value.problem["code"] == "personal_source_not_allowed"

    repo.personal_processing_authorizer = None
    work = repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "整理已登记面试记录",
            "objects": [],
            "references": [request["material_ref"]],
            "budget_profile": "test",
        },
        uuid4(),
    )
    model = ScriptModel([answer()])
    done = run_work(repo, model, intake[1], repo.claim("record-gate", 60))
    assert done["work_id"] == work["work_id"] and done["state"] == "blocked"
    assert model.requests == []


def test_exact_read_rechecks_candidate_after_attachment_io(
    uploaded, intake, database, monkeypatch
):
    from app.hr_agent.interview_records import InterviewRecordService

    client, _, repo, owner, materials, candidate_aid, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    interview_aid = upload_document(
        uploaded, database, b"Exact interview transcript.", "text/plain", "race.txt"
    )
    service = InterviewRecordService(repo, materials, intake[0])
    created = service.register(
        owner, candidate_id, _request(client, interview_aid), uuid4()
    )
    original = materials.read_text

    def revoke_candidate_after_read(selected_owner, ref):
        text = original(selected_owner, ref)
        with database.admin_connection() as c:
            c.execute(
                "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
                (UUID(candidate_aid),),
            )
        return text

    monkeypatch.setattr(materials, "read_text", revoke_candidate_after_read)
    with pytest.raises(HrAgentProblem) as error:
        service.read(owner, candidate_id, created["record_id"])
    assert error.value.problem["code"] == "reference_unavailable"


def test_http_owner_candidate_isolation_and_candidate_scoped_idempotency(
    uploaded, intake, database
):
    from app.hr_agent.interview_records import InterviewRecordService

    client, headers, repo, owner, materials, aid, _, _ = uploaded
    first_candidate = _candidate(uploaded, intake)
    second_candidate = _candidate(uploaded, intake)
    client.app.state.hr_agent_service.candidates = intake[0]
    client.app.state.hr_agent_service.interviews = InterviewRecordService(
        repo, materials, intake[0]
    )
    request = _request(client, aid)
    key = str(uuid4()).upper()
    first_path = f"/api/hr/agent/candidates/{first_candidate}/interview-records"
    created = client.post(
        first_path, headers={**headers, "Idempotency-Key": key}, json=request
    )
    assert created.status_code == 201
    replay = client.post(
        first_path,
        headers={**headers, "Idempotency-Key": key.lower()},
        json=request,
    )
    assert replay.status_code == 201 and replay.json() == created.json()
    assert (
        client.post(
            first_path,
            headers={**headers, "Idempotency-Key": key},
            json={**request, "title": "changed"},
        ).status_code
        == 409
    )
    second_path = f"/api/hr/agent/candidates/{second_candidate}/interview-records"
    assert (
        client.post(
            second_path, headers={**headers, "Idempotency-Key": key}, json=request
        ).status_code
        == 201
    )
    middleware_auth = next(
        m.kwargs["auth"] for m in client.app.user_middleware if "auth" in m.kwargs
    )
    middleware_auth.owner_id = uuid4()
    assert client.get(first_path).status_code == 404
    assert client.get(first_path + "/" + created.json()["record_id"]).status_code == 404
    with database.admin_connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.candidate_interview_records WHERE owner_id=%s",
                (owner,),
            ).fetchone()[0]
            == 2
        )


def test_whitespace_upload_and_marker_failure_leave_no_registration_rows(
    uploaded, intake, database
):
    from app.hr_agent.interview_records import InterviewRecordService

    client, _, repo, owner, materials, _, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    whitespace = upload_document(
        uploaded, database, " \n\t　".encode(), "text/plain", "blank.txt"
    )
    service = InterviewRecordService(repo, materials, intake[0])
    with pytest.raises(HrAgentProblem):
        service.register(owner, candidate_id, _request(client, whitespace), uuid4())
    fresh = upload_document(
        uploaded, database, b"atomic transcript", "text/plain", "atomic.txt"
    )
    with database.admin_connection() as c:
        c.execute(
            "CREATE FUNCTION platform_hr_agent.test_reject_marker() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'marker rejected'; END $$"
        )
        c.execute(
            "CREATE TRIGGER test_reject_marker BEFORE INSERT ON platform_hr_agent.personal_materials FOR EACH ROW EXECUTE FUNCTION platform_hr_agent.test_reject_marker()"
        )
    try:
        with pytest.raises(Exception, match="marker rejected"):
            service.register(owner, candidate_id, _request(client, fresh), uuid4())
    finally:
        with database.admin_connection() as c:
            c.execute(
                "DROP TRIGGER test_reject_marker ON platform_hr_agent.personal_materials"
            )
            c.execute("DROP FUNCTION platform_hr_agent.test_reject_marker()")
    with database.admin_connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.candidate_interview_records WHERE attachment_id IN (%s,%s)",
                (UUID(whitespace), UUID(fresh)),
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.personal_materials WHERE attachment_id IN (%s,%s)",
                (UUID(whitespace), UUID(fresh)),
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.operations WHERE namespace=%s",
                (f"interview_record:{candidate_id}",),
            ).fetchone()[0]
            == 0
        )


def test_optional_plan_rejects_non_plan_exact_result(uploaded, intake, database):
    from app.hr_agent.interview_records import InterviewRecordService
    from app.hr_agent.runtime import run_work
    from app.hr_agent.types import ResultQuery
    from tests.test_hr_agent_runtime import ScriptModel, answer, tool

    client, _, repo, owner, materials, _, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    aid = upload_document(
        uploaded, database, b"plan validation", "text/plain", "plan.txt"
    )
    ref = _request(client, aid)["material_ref"]
    obj = {"kind": "candidate", "id": candidate_id}
    repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "save",
            "objects": [obj],
            "references": [ref],
            "budget_profile": "test",
        },
        uuid4(),
    )
    model = ScriptModel(
        [
            tool(
                "save_result",
                {
                    "kind": "interview_record",
                    "title": "not a plan",
                    "body": "body",
                    "result_id": None,
                    "expected_revision": None,
                    "objects": [obj],
                    "source_refs": [ref],
                    "preceding_refs": [],
                    "base_standard_ref": None,
                    "changes": [],
                    "basis": [],
                },
            ),
            answer(),
        ]
    )
    assert (
        run_work(repo, model, intake[1], repo.claim("wrong-plan", 60))["state"]
        == "completed"
    )
    result = repo.list_results(owner, ResultQuery(object_ref=obj))["items"][0]["ref"]
    service = InterviewRecordService(repo, materials, intake[0])
    with pytest.raises(HrAgentProblem) as error:
        service.register(
            owner,
            candidate_id,
            _request(client, aid, interview_plan_ref=result),
            uuid4(),
        )
    assert error.value.problem["code"] == "invalid_input"


def test_optional_plan_requires_current_candidate_and_selected_position(
    uploaded, intake, database
):
    from app.hr.models import CreateManualPosition
    from app.hr.repository import HrPositionRepository
    from app.hr_agent.interview_records import InterviewRecordService
    from app.hr_agent.runtime import run_work
    from app.hr_agent.types import ResultQuery
    from tests.test_hr_agent_runtime import ScriptModel, answer, tool

    client, _, repo, owner, materials, _, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    other_candidate = _candidate(uploaded, intake)
    aid = upload_document(
        uploaded, database, b"exact plan", "text/plain", "exact-plan.txt"
    )
    ref = _request(client, aid)["material_ref"]
    position = HrPositionRepository(database.dsn).create_manual(
        CreateManualPosition(owner, uuid4(), uuid4(), "Test position", "R&D", ("Test",))
    )
    position_id = str(position.position_id)
    with database.admin_connection() as c:
        operation = c.execute(
            "SELECT created_by_operation FROM platform_hr_agent.candidates WHERE owner_id=%s AND candidate_id=%s",
            (owner, UUID(candidate_id)),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO platform_hr_agent.candidate_positions(owner_id,candidate_id,position_id,created_by_operation) VALUES(%s,%s,%s,%s)",
            (owner, UUID(candidate_id), position.position_id, operation),
        )

    def save_plan(objects, label):
        repo.submit(
            owner,
            {
                "thread_id": None,
                "text": label,
                "objects": objects,
                "references": [ref],
                "budget_profile": "test",
            },
            uuid4(),
        )
        model = ScriptModel(
            [
                tool(
                    "save_result",
                    {
                        "kind": "interview_plan",
                        "title": label,
                        "body": "questions",
                        "result_id": None,
                        "expected_revision": None,
                        "objects": objects,
                        "source_refs": [ref],
                        "preceding_refs": [],
                        "base_standard_ref": None,
                        "changes": [],
                        "basis": [],
                    },
                ),
                answer(),
            ]
        )
        assert (
            run_work(repo, model, intake[1], repo.claim(label, 60))["state"]
            == "completed"
        )
        items = repo.list_results(
            owner, ResultQuery(object_ref=objects[0], kind="interview_plan")
        )["items"]
        return next(item["ref"] for item in items if item["title"] == label)

    current = {"kind": "candidate", "id": candidate_id}
    other = {"kind": "candidate", "id": other_candidate}
    pobj = {"kind": "position", "id": position_id}
    wrong_candidate = save_plan([other], "wrong-candidate")
    missing_position = save_plan([current], "missing-position")
    valid = save_plan([current, pobj], "valid-plan")
    service = InterviewRecordService(repo, materials, intake[0])
    for plan in (wrong_candidate, missing_position):
        with pytest.raises(HrAgentProblem) as error:
            service.register(
                owner,
                candidate_id,
                _request(client, aid, position_id=position_id, interview_plan_ref=plan),
                uuid4(),
            )
        assert error.value.problem["code"] == "scope_denied"
    created = service.register(
        owner,
        candidate_id,
        _request(client, aid, position_id=position_id, interview_plan_ref=valid),
        uuid4(),
    )
    assert (
        created["interview_plan_ref"] == valid and created["position_id"] == position_id
    )


def test_register_rechecks_candidate_after_text_io_and_replay_observes_revocation(
    uploaded, intake, database, monkeypatch
):
    from app.hr_agent.interview_records import InterviewRecordService

    client, headers, repo, owner, materials, candidate_aid, _, _ = uploaded
    candidate_id = _candidate(uploaded, intake)
    interview_aid = upload_document(
        uploaded, database, b"POST race transcript", "text/plain", "post-race.txt"
    )
    service = InterviewRecordService(repo, materials, intake[0])
    client.app.state.hr_agent_service.candidates = intake[0]
    client.app.state.hr_agent_service.interviews = service
    request = _request(client, interview_aid)
    path = f"/api/hr/agent/candidates/{candidate_id}/interview-records"
    race_key = str(uuid4())
    original = materials.read_text

    def revoke_candidate_after_read(selected_owner, ref):
        text = original(selected_owner, ref)
        with database.admin_connection() as c:
            c.execute(
                "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
                (UUID(candidate_aid),),
            )
        return text

    monkeypatch.setattr(materials, "read_text", revoke_candidate_after_read)
    response = client.post(
        path,
        headers={**headers, "Idempotency-Key": race_key},
        json=request,
    )
    assert response.status_code == 410
    with database.admin_connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.candidate_interview_records WHERE attachment_id=%s",
                (UUID(interview_aid),),
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.personal_materials WHERE attachment_id=%s",
                (UUID(interview_aid),),
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) FROM platform_hr_agent.operations WHERE owner_id=%s AND namespace=%s AND request_key=%s",
                (owner, f"interview_record:{candidate_id}", race_key),
            ).fetchone()[0]
            == 0
        )
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()+interval '1 day' WHERE attachment_id=%s",
            (UUID(candidate_aid),),
        )

    monkeypatch.setattr(materials, "read_text", original)
    replay_key = str(uuid4())
    created = client.post(
        path,
        headers={**headers, "Idempotency-Key": replay_key},
        json=request,
    )
    assert created.status_code == 201
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (UUID(candidate_aid),),
        )
    denied = client.post(
        path,
        headers={**headers, "Idempotency-Key": replay_key},
        json=request,
    )
    assert denied.status_code == 410
