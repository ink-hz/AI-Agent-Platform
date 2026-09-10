"""B business journey uses real HTTP/auth/DB; only the model is scripted."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from tests import test_hr_agent_materials as materials_fixtures
from tests.test_hr_agent_runtime import ScriptModel, answer, tool

uploaded = materials_fixtures.uploaded
secured = materials_fixtures.secured
database = materials_fixtures.database
_FIXTURES = (uploaded, secured, database)


def test_public_calibration_partial_confirmation_and_cross_thread_continuation(
    uploaded, tmp_path, database
):
    from app.hr.models import CreateManualPosition
    from app.hr.repository import HrPositionRepository
    from app.hr_agent.knowledge import KnowledgeReleases
    from app.hr_agent.resources import ResourceReader
    from app.hr_agent.runtime import run_work
    from app.hr_agent.standards import StandardService
    from tools.hr_agent.build_knowledge_release import build

    client, headers, repo, owner, materials, aid, _, _ = uploaded
    repo.settings = replace(
        repo.settings,
        budget_profile={
            **repo.settings.budget_profile,
            "input_trigger_tokens": 70000,
            "input_target_tokens": 50000,
        },
        provider_profile={
            **repo.settings.provider_profile,
            "context_window_tokens": 131072,
        },
    )
    build(Path(__file__).parents[1] / "hr_agent_knowledge", tmp_path / "knowledge")
    knowledge = KnowledgeReleases(tmp_path / "knowledge")
    resources = ResourceReader(repo, knowledge, material_service=materials)
    repo.scope_validator = resources.validate_scope
    repo.release_provider = knowledge.metadata
    repo.release_validator = knowledge.validate_record
    # Add standards to the existing service through the bound handler's closure.
    # Better expose the original fixture instance for inspection/resume.
    service = client.app.state.hr_agent_service
    service.standards = StandardService(repo)
    service.knowledge = knowledge

    def post(path, body):
        return client.post(
            "/api/hr/agent" + path,
            json=body,
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )

    material = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    method = next(
        i["ref"] for i in knowledge.items if i["ref"]["id"] == "requirement-calibration"
    )
    submitted = post(
        "/works",
        {
            "thread_id": None,
            "text": "理解公开JD，澄清责任层级",
            "objects": [],
            "references": [material, method],
            "budget_profile": "test",
        },
    )
    assert submitted.status_code == 201, submitted.text
    work = submitted.json()
    wid = work["work_id"]
    model = ScriptModel(
        [
            tool("read_resource", {"ref": material}),
            tool("read_resource", {"ref": method}),
            tool(
                "ask_user",
                {
                    "question": "需要入职即可独立交付吗？",
                    "options": ["需要独立交付", "允许培养"],
                },
            ),
        ]
    )
    waiting = run_work(repo, model, resources, repo.claim("journey", 60))
    assert waiting["state"] == "waiting_user"
    position = HrPositionRepository(database.dsn).create_manual(
        CreateManualPosition(
            owner, uuid4(), uuid4(), "本地公开校准岗位", "研发", ("深圳",)
        )
    )
    obj = {"kind": "position", "id": str(position.position_id)}
    resumed = post(
        "/works/" + wid + "/inputs",
        {
            "expected_input_revision": 1,
            "text": "允许相邻项目经验，入职后三个月独立交付。选择此岗位。",
            "objects": [obj],
            "references": [material, method],
            "question_id": waiting["pending_question_id"],
        },
    )
    assert resumed.status_code == 202, resumed.text
    args = {
        "result_id": None,
        "expected_revision": None,
        "kind": "role_calibration",
        "title": "公开岗位校准",
        "body": "交付要求应与任务和培养期一致。",
        "objects": [obj],
        "source_refs": [material, method],
        "preceding_refs": [],
        "base_standard_ref": None,
        "changes": [],
        "basis": [{"kind": "user_temporary", "ref": material, "input_revision": 2}],
    }
    proposal = {
        **args,
        "kind": "standard_proposal",
        "title": "待确认岗位建议",
        "changes": [
            {"action": "add", "target_item_id": None, "text": "入职三个月后独立交付"},
            {
                "action": "add",
                "target_item_id": None,
                "text": "相邻项目经验可提供工作样本",
            },
        ],
    }
    model = ScriptModel(
        [
            tool("save_result", args),
            tool("save_result", proposal),
            answer("两份成果已保存，等待用户选择确认。"),
        ]
    )
    done = run_work(repo, model, resources, repo.claim("journey", 60))
    assert done["state"] == "completed"

    def read(ref):
        return client.get(
            f"/api/hr/agent/results/{ref['id']}/revisions/{ref['revision']}"
        ).json()

    results = [read(ref) for ref in done["result_refs"]]
    saved = next(r for r in results if r["kind"] == "standard_proposal")
    ref = saved["ref"]
    before = client.get(
        f"/api/hr/agent/results/{ref['id']}/revisions/{ref['revision']}/file"
    ).content
    assert (
        post(
            f"/results/{ref['id']}/links",
            {"expected_result_revision": ref["revision"], "objects": [obj]},
        ).status_code
        == 200
    )
    assert ref in [
        i["ref"]
        for i in client.get(
            "/api/hr/agent/results",
            params={"object_kind": "position", "object_id": obj["id"]},
        ).json()["items"]
    ]
    assert (
        before
        == client.get(
            f"/api/hr/agent/results/{ref['id']}/revisions/{ref['revision']}/file"
        ).content
    )
    payload = {
        "proposal_ref": ref,
        "selected_change_ids": [saved["changes"][0]["change_id"]],
        "expected_standard_revision": None,
    }
    confirmed = post(f"/positions/{obj['id']}/standards/confirm", payload)
    assert confirmed.status_code == 200, confirmed.text
    first = confirmed.json()
    assert [i["text"] for i in first["items"]] == ["入职三个月后独立交付"]
    conflict = post(
        f"/positions/{obj['id']}/standards/confirm",
        {**payload, "selected_change_ids": [saved["changes"][1]["change_id"]]},
    )
    assert (
        conflict.status_code == 409
        and conflict.json()["details"]["current_revision"] == first["ref"]["revision"]
    )
    new = post(
        "/works",
        {
            "thread_id": None,
            "text": "沿用已确认标准，解释哪些尚未确认。",
            "objects": [obj],
            "references": [first["ref"]],
            "budget_profile": "test",
        },
    )
    assert new.status_code == 201, new.text
    assert new.json()["thread_id"] != work["thread_id"]
    model = ScriptModel(
        [
            tool("read_resource", {"ref": first["ref"]}),
            answer("只确认三个月交付要求；第二条未确认。"),
        ]
    )
    final = run_work(repo, model, resources, repo.claim("journey-next", 60))
    assert final["state"] == "completed", final
    import json

    returned = [
        json.loads(m["content"])
        for request in model.requests
        for m in request.messages
        if m["role"] == "tool"
    ]
    assert any(
        (r.get("data") or {}).get("text") and "三个月" in r["data"]["text"]
        for r in returned
    )
    current_input = client.get(
        "/api/hr/agent/works/" + final["work_id"] + "/input"
    ).json()
    assert current_input["references"] == [first["ref"]]
