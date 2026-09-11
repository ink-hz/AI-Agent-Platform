"""Selecting an exact saved result carries usable provenance, never new object authority."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest
from app.hr_agent.resources import PublishedKnowledge, ResourceReader
from app.hr_agent.runtime import run_work
from app.hr_agent.types import HrAgentProblem
from psycopg.types.json import Jsonb
from tests.test_hr_agent_context import publication
from tests.test_hr_agent_materials import database, secured, uploaded
from tests.test_hr_agent_runtime import ScriptModel, answer, tool

_FIXTURES = (database, secured, uploaded)


def setup_result(uploaded, tmp_path, objects=None):
    _client, _headers, repo, owner, materials, aid, _, _ = uploaded
    repo.settings = replace(
        repo.settings,
        budget_profile={
            **repo.settings.budget_profile,
            "input_target_tokens": 20000,
            "input_trigger_tokens": 24000,
        },
    )
    publication(tmp_path)
    knowledge = PublishedKnowledge(tmp_path)
    resources = ResourceReader(repo, knowledge, material_service=materials)
    repo.scope_validator = resources.validate_scope
    repo.release_provider = knowledge.metadata
    ref = materials.resolve(owner, aid)["text_ref"]
    objects = objects or []
    first = repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "保存公开材料判断",
            "objects": objects,
            "references": [ref],
            "budget_profile": "test",
        },
        uuid4(),
    )
    args = {
        "kind": "research",
        "title": "已存判断",
        "body": "此判断基于已选材料；可以回查。",
        "objects": objects,
        "source_refs": [ref],
        "preceding_refs": [],
        "result_id": None,
        "expected_revision": None,
        "base_standard_ref": None,
        "changes": [],
        "basis": [],
    }
    done = run_work(
        repo,
        ScriptModel(
            [tool("read_resource", {"ref": ref}), tool("save_result", args), answer()]
        ),
        resources,
        repo.claim("saved", 60),
    )
    assert done["work_id"] == first["work_id"] and done["state"] == "completed"
    return resources, ref, done["result_refs"][0]


def test_exact_selected_result_allows_own_provenance_without_reselecting_material(
    uploaded, tmp_path
):
    client, headers, repo, _owner, _, _, _, _ = uploaded
    resources, material, result = setup_result(uploaded, tmp_path)
    response = client.post(
        "/api/hr/agent/works",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "thread_id": None,
            "text": "继续此准确成果，回查原文",
            "objects": [],
            "references": [result],
            "budget_profile": "test",
        },
    )
    assert response.status_code == 201, response.text
    model = ScriptModel(
        [
            tool("read_resource", {"ref": result}),
            tool("read_resource", {"ref": material}),
            answer("已回查准确来源"),
        ]
    )
    done = run_work(repo, model, resources, repo.claim("continue", 60))
    assert done["state"] == "completed", done["state"]
    tool_messages = [
        json.loads(message["content"])
        for message in model.requests[-1].messages
        if message["role"] == "tool"
    ]
    assert any(
        outcome.get("data", {}).get("ref") == material
        and outcome["data"]["text"] == uploaded[6].decode()
        for outcome in tool_messages
    )
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS n FROM platform_hr_agent.read_records WHERE work_id=%s AND ref=%s",
            (done["work_id"], Jsonb(material)),
        )
        assert c.fetchone()["n"] == 1


def test_unselected_material_and_revoked_selected_result_source_remain_denied(
    uploaded, tmp_path, database
):
    _, _, repo, owner, _, aid, _, _ = uploaded
    resources, material, result = setup_result(uploaded, tmp_path)
    work = repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "没有选原材料",
            "objects": [],
            "references": [],
            "budget_profile": "test",
        },
        uuid4(),
    )
    fence = repo.claim("unselected", 60)
    with pytest.raises(HrAgentProblem) as error:
        resources.read_resource(fence, {"ref": material})
    assert error.value.problem["code"] == "scope_denied"
    repo.cancel(owner, work["work_id"], "done", uuid4())
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (aid,),
        )
    with pytest.raises(HrAgentProblem):
        repo.submit(
            owner,
            {
                "thread_id": None,
                "text": "失效引用不能续作",
                "objects": [],
                "references": [result],
                "budget_profile": "test",
            },
            uuid4(),
        )


def test_result_provenance_does_not_authorize_another_work_object(
    uploaded, tmp_path, database
):
    from app.hr.models import CreateManualPosition
    from app.hr.repository import HrPositionRepository

    _, _, repo, owner, _, _, _, _ = uploaded
    positions = HrPositionRepository(database.dsn)
    a, b = [
        positions.create_manual(
            CreateManualPosition(owner, uuid4(), uuid4(), title, "研发", ("深圳",))
        )
        for title in ("虚构岗位A", "虚构岗位B")
    ]
    obj_a, obj_b = [{"kind": "position", "id": str(p.position_id)} for p in (a, b)]
    resources, material, result = setup_result(uploaded, tmp_path, [obj_a])
    repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "仅处理B",
            "objects": [obj_b],
            "references": [result],
            "budget_profile": "test",
        },
        uuid4(),
    )
    fence = repo.claim("wrong-object", 60)
    with pytest.raises(HrAgentProblem) as error:
        resources.read_resource(fence, {"ref": material})
    assert error.value.problem["code"] == "scope_denied"
