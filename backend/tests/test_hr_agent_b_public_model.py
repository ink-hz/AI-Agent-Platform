"""Opt-in public-material model evidence, never part of routine offline tests."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from tests import test_hr_agent_materials as materials_fixtures

uploaded = materials_fixtures.uploaded
secured = materials_fixtures.secured
database = materials_fixtures.database
_FIXTURES = (uploaded, secured, database)


@pytest.mark.skipif(
    not os.getenv("HR_B_REAL_PROFILE_FILE"),
    reason="explicit public real-model profile required",
)
def test_public_jd_real_model_evidence(uploaded, database, tmp_path):
    from app.attachments.derivatives import DerivativeBuilder
    from app.attachments.scanner import TrustedInternalScanner
    from app.attachments.validation import AttachmentValidator
    from app.attachments.worker import AttachmentProcessor
    from app.attachments.worker_runtime import AttachmentProcessingRepository
    from app.hr_agent.knowledge import KnowledgeReleases
    from app.hr_agent.model import ConfiguredHttpModelPort
    from app.hr_agent.resources import ResourceReader
    from app.hr_agent.runtime import run_work
    from tools.hr_agent.build_knowledge_release import build

    client, headers, repo, owner, materials, _, _, store = uploaded
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/hr_agent_b4/public-jd-agibot.json"
        ).read_text()
    )
    job = fixture["job"]
    text = f"公开招聘归档（不是本公司已确认标准）\n观察时间：{job['observed_at']}\n来源：{job['source_url']}\n{fixture['provenance']['limitation']}\n\n{job['title']}\n\n职责\n{job['duty_excerpt']}\n\n要求\n{job['requirement_excerpt']}"
    data = text.encode()
    response = client.post(
        "/api/v1/attachments/uploads",
        headers=headers,
        json={
            "conversation_id": None,
            "original_name": "public-jd-agibot.txt",
            "declared_mime": "text/plain",
            "declared_size": len(data),
        },
    )
    assert response.status_code == 201, response.text
    upload = response.json()
    uid = upload["upload_id"]
    aid = upload["attachment_id"]
    assert (
        client.put(
            f"/api/v1/attachments/uploads/{uid}/content",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=data,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/attachments/uploads/{uid}/complete", headers=headers
        ).status_code
        == 200
    )
    processor = AttachmentProcessor(
        repository=AttachmentProcessingRepository(
            database.dsn.replace(
                "user=platform_control_app", "user=platform_brain_worker"
            ),
            content_codec=repo.codec,
        ),
        object_store=store,
        validator=AttachmentValidator(),
        scanner=TrustedInternalScanner(),
        derivatives=DerivativeBuilder(),
        worker_id="public-b4",
    )
    for _ in range(4):
        if not asyncio.run(processor.process_next()):
            break
    material = client.get("/api/hr/agent/materials/" + aid).json()
    assert material["parse_state"] == "ready", material
    build(Path(__file__).parents[1] / "hr_agent_knowledge", tmp_path / "release")
    knowledge = KnowledgeReleases(tmp_path / "release")
    profile = json.loads(Path(os.environ["HR_B_REAL_PROFILE_FILE"]).read_text())
    repo.settings = replace(
        repo.settings,
        provider_profile=profile,
        budget_profile={
            **repo.settings.budget_profile,
            "limits": {
                "model_calls": 20,
                "total_tokens": 600000,
                "active_seconds": 900,
            },
            "input_trigger_tokens": 70000,
            "input_target_tokens": 50000,
            "max_output_tokens": 4096,
        },
    )
    resources = ResourceReader(repo, knowledge, material_service=materials)
    repo.scope_validator = resources.validate_scope
    repo.release_provider = knowledge.metadata
    repo.release_validator = knowledge.validate_record
    prompt = "请通读上传的公开岗位材料，帮我理解这个岗位真正要交付什么、哪些条件有歧义，以及用人经理需要确认什么。请自主选择适用的方法，说明具体证据、反例和边界，保存一份岗位校准成果。当前没有选本公司岗位，材料是竞对静态公开归档，不要把它确认为我们的标准。可以先完成可答部分，把影响后续校准的少量问题列出来。"
    response = client.post(
        "/api/hr/agent/works",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "thread_id": None,
            "text": prompt,
            "objects": [],
            "references": [material["text_ref"]],
            "budget_profile": "test",
        },
    )
    assert response.status_code == 201, response.text
    work = response.json()
    fence = repo.claim("real-public-b4", 1200)
    done = run_work(
        repo, ConfiguredHttpModelPort.from_mapping(profile), resources, fence
    )
    stages = [done]
    confirmed_standard = None
    if os.getenv("HR_B_REAL_FULL_JOURNEY") == "1" and done["state"] == "completed":
        from app.hr.models import CreateManualPosition
        from app.hr.repository import HrPositionRepository
        from app.hr_agent.standards import StandardService

        position = HrPositionRepository(database.dsn).create_manual(
            CreateManualPosition(
                owner, uuid4(), uuid4(), "公开校准验证岗位", "研发", ("深圳",)
            )
        )
        obj = {"kind": "position", "id": str(position.position_id)}
        method = next(
            i["ref"]
            for i in knowledge.items
            if i["ref"]["id"] == "requirement-calibration"
        )
        service = client.app.state.hr_agent_service
        service.standards = StandardService(repo)
        clarification = "现在以已选的本公司测试岗位继续：目标是可运行在机器人上的动作生成模块，研发探索阶段，目前没有线上A/B系统。我们允许有动作生成项目、尚无真机经历的人入职培养；三个月后能独立完成约束验证。请读我选的要求校准方法，依据这次澄清修正前面的判断，保留临时基准标记，保存一份岗位校准成果和分条标准建议供我选择。当前本岗位尚无正式标准。其余未知写明边界，先形成可评审提案，不自动确认。"
        response = client.post(
            f"/api/hr/agent/works/{work['work_id']}/inputs",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "expected_input_revision": done["input_revision"],
                "text": clarification,
                "objects": [obj],
                "references": [material["text_ref"], method],
                "question_id": None,
            },
        )
        assert response.status_code == 202, response.text
        done = run_work(
            repo,
            ConfiguredHttpModelPort.from_mapping(profile),
            resources,
            repo.claim("real-public-b4-clarify", 1200),
        )
        stages.append(done)
        proposals = []
        for ref in done["result_refs"]:
            result = client.get(
                f"/api/hr/agent/results/{ref['id']}/revisions/{ref['revision']}"
            ).json()
            if result.get("kind") == "standard_proposal":
                proposals.append(result)
        if proposals:
            proposal = proposals[-1]
            response = client.post(
                f"/api/hr/agent/positions/{obj['id']}/standards/confirm",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={
                    "proposal_ref": proposal["ref"],
                    "selected_change_ids": [proposal["changes"][0]["change_id"]],
                    "expected_standard_revision": None,
                },
            )
            assert response.status_code == 200, response.text
            confirmed_standard = response.json()
            # This is an explicit local test-user HTTP action, never a model tool.
            response = client.post(
                "/api/hr/agent/works",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={
                    "thread_id": None,
                    "text": "请读取已选的正式标准，简要说明已确认什么；其他提案条目没有确认，不要把它们当正式标准。无需保存新成果。",
                    "objects": [obj],
                    "references": [confirmed_standard["ref"]],
                    "budget_profile": "test",
                },
            )
            assert response.status_code == 201, response.text
            continued = run_work(
                repo,
                ConfiguredHttpModelPort.from_mapping(profile),
                resources,
                repo.claim("real-public-b4-next-thread", 1200),
            )
            stages.append(continued)
    messages = client.get(f"/api/hr/agent/works/{work['work_id']}/messages").json()
    page = client.get(
        "/api/hr/agent/results", params={"thread_id": work["thread_id"]}
    ).json()
    results = [
        client.get(
            f"/api/hr/agent/results/{item['ref']['id']}/revisions/{item['ref']['revision']}"
        ).json()
        for item in page["items"]
    ]
    with repo.transaction() as c:
        c.execute(
            "SELECT ordinal,status,purpose,usage_quality,reserved_tokens,charged_tokens FROM platform_hr_agent.model_attempts WHERE work_id=%s ORDER BY ordinal",
            (work["work_id"],),
        )
        attempts = c.fetchall()
        c.execute(
            "SELECT * FROM platform_hr_agent.operations WHERE work_id=%s AND attempt_id IS NOT NULL ORDER BY created_at,slot",
            (work["work_id"],),
        )
        calls = [
            {
                "namespace": row["namespace"],
                "status": row["status"],
                "receipt": repo._unseal(
                    "operations", row["operation_id"], "sealed_receipt", row
                )
                if row["sealed_receipt"]
                else None,
            }
            for row in c.fetchall()
        ]
    out = Path(
        os.environ.get(
            "HR_B_REAL_OUTPUT_DIR",
            str(Path(__file__).parents[2] / ".superpowers/sdd/b4-real"),
        )
    )
    out.mkdir(parents=True, exist_ok=True)
    evidence = {
        "provider": {
            "model": profile["model"],
            "protocol": profile["protocol"],
            "profile_revision": profile["revision"],
        },
        "public_fixture": fixture,
        "input": prompt,
        "release": knowledge.metadata(),
        "work": done,
        "stages": stages,
        "confirmed_standard": confirmed_standard,
        "messages": messages,
        "results": results,
        "attempts": attempts,
        "tools": calls,
        "acceptance": "Real model output awaits user HR professional review; no real candidate/production data.",
    }
    (out / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    for index, result in enumerate(results):
        (out / f"result-{index + 1}.md").write_text(
            "# " + result["title"] + "\n\n" + result["body"] + "\n"
        )
    assert done["state"] in ("completed", "waiting_user"), done["state"]
    assert results, (
        "Model has not yet saved a result; inspect actual evidence instead of claiming business completion."
    )
    if os.getenv("HR_B_REAL_FULL_JOURNEY") == "1":
        assert confirmed_standard is not None, (
            "No proposal/confirmation completed: inspect saved evidence."
        )
        assert stages[-1]["state"] == "completed"
