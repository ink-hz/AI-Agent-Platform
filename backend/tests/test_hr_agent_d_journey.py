"""D scenario journey: real local API/DB, wholly fictional candidate materials.

The normal run scripts only the model boundary. The opt-in run uses the fixed HR
provider for D work; candidate intake fixture initialization remains scripted.
"""

import hashlib
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from app.hr_agent.runtime import run_work
from tests.test_hr_agent_candidate_intake import batch_request, confirmation, intake
from tests.test_hr_agent_material_parsing import upload_document
from tests.test_hr_agent_materials import database, secured, uploaded
from tests.test_hr_agent_runtime import ScriptModel, answer, tool

_FIXTURES = (database, secured, uploaded, intake)
FIXTURE = Path(__file__).parent / "fixtures/hr_agent_d/scenario.md"


def sections():
    parts = FIXTURE.read_text().split("\n## ")
    return {
        part.split("\n", 1)[0]: part.split("\n", 1)[1].strip() for part in parts[1:]
    }


def upload_text(uploaded, database, text, name="synthetic.txt"):
    """Drain pending real attachment jobs; earlier text derivative retries may precede this upload."""
    import asyncio

    from app.attachments.derivatives import DerivativeBuilder
    from app.attachments.scanner import TrustedInternalScanner
    from app.attachments.validation import AttachmentValidator
    from app.attachments.worker import AttachmentProcessor
    from app.attachments.worker_runtime import AttachmentProcessingRepository

    _, _, repo, owner, materials, _, _, store = uploaded
    aid = upload_document(uploaded, database, text.encode(), "text/plain", name)
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
        worker_id="d-text-fixture",
    )
    for _ in range(32):
        view = materials.resolve(owner, aid)
        if view["state"] == "ready":
            assert view["text_ref"], view
            return aid, view["text_ref"]
        if not asyncio.run(processor.process_next()):
            break
    raise AssertionError(materials.resolve(owner, aid))


def saved(kind, body, objects, refs):
    return {
        "kind": kind,
        "title": "虚构场景 · " + kind,
        "body": body,
        "objects": objects,
        "source_refs": refs,
        "preceding_refs": [],
        "result_id": None,
        "expected_revision": None,
        "base_standard_ref": None,
        "changes": [],
        "basis": [],
    }


class BoundedRealPort:
    """One local validation's aggregate call budget, shared by all D stages."""

    def __init__(self, profile, *, extended=False):
        from app.hr_agent.model import ConfiguredHttpModelPort

        if (
            profile.get("model") != "claude-opus-5"
            or profile.get("protocol") != "anthropic_messages_sse"
        ):
            raise ValueError("fixed HR Opus5 profile required")
        self.port = ConfiguredHttpModelPort.from_mapping(profile)
        self.calls = 0
        self.max_output = 16384 if extended else 4096
        self.max_seconds = 300 if extended else 120
        self.observations = []

    def stream(self, request):
        from app.hr_agent.model import ModelTransportError

        if (
            self.calls >= 24
            or request.max_output_tokens > self.max_output
            or request.deadline_seconds > self.max_seconds
        ):
            raise ModelTransportError("configuration_unavailable")
        self.calls += 1
        observation = {
            "attempt_id": str(request.attempt_id),
            "stops": [],
            "usage": [],
            "stream_complete": False,
        }
        self.observations.append(observation)
        started = time.monotonic()
        try:
            for event in self.port.stream(request):
                if event.type == "stop":
                    observation["stops"].append(event.payload)
                elif event.type == "usage":
                    observation["usage"].append(event.payload)
                yield event
            observation["stream_complete"] = True
        except ModelTransportError as error:
            observation["error_code"] = error.code
            raise
        finally:
            observation["duration_seconds"] = time.monotonic() - started


def journey(
    uploaded,
    intake,
    database,
    tmp_path,
    *,
    real=False,
    without_plan=False,
    review_history=None,
):
    from app.hr.models import CreateManualPosition
    from app.hr.repository import HrPositionRepository
    from app.hr_agent.knowledge import KnowledgeReleases
    from app.hr_agent.resources import ResourceReader
    from tests.test_hr_agent_b_public_model import _provider_evidence, _stage_evidence
    from tools.hr_agent.build_knowledge_release import build

    client, headers, repo, owner, materials, _, _, _ = uploaded
    candidate_service, _ = intake
    client.app.state.hr_agent_service.candidates = candidate_service
    repo.settings = replace(
        repo.settings,
        budget_profile={
            **repo.settings.budget_profile,
            "input_target_tokens": 20000,
            "input_trigger_tokens": 24000,
        },
        provider_profile={
            **repo.settings.provider_profile,
            "context_window_tokens": 131072,
        },
    )
    content = sections()
    source_refs = {}
    for name in ["临时岗位要求", "虚构简历", "用户追加的虚构招聘过程记录"]:
        _, source_refs[name] = upload_text(uploaded, database, content[name])
    position = HrPositionRepository(database.dsn).create_manual(
        CreateManualPosition(
            owner, uuid4(), uuid4(), "虚构现场诊断岗位", "研发", ("示例城",)
        )
    )
    pobj = {"kind": "position", "id": str(position.position_id)}
    manifest = build(
        Path(__file__).parents[1] / "hr_agent_knowledge", tmp_path / "release"
    )
    knowledge = KnowledgeReleases(tmp_path / "release")
    resources = ResourceReader(repo, knowledge, material_service=materials)
    repo.scope_validator = resources.validate_scope
    repo.release_provider = knowledge.metadata
    repo.release_validator = knowledge.validate_record
    service = client.app.state.hr_agent_service
    service.knowledge = knowledge

    def post(path, body):
        response = client.post(
            "/api/hr/agent" + path,
            json=body,
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code in (200, 201, 202), response.text
        return response.json()

    # Fixture-only draft initialization: no real candidate and no model extraction claim.
    resume = source_refs["虚构简历"]
    batch = post(
        "/candidate-batches",
        {**batch_request([resume["id"].split(":")[0]]), "position_id": pobj["id"]},
    )
    candidate_service.advance_one("d-fixture")
    item = candidate_service.get_item(owner, batch["item_ids"][0])
    draft = ScriptModel(
        [
            tool("read_resource", {"ref": resume}),
            tool(
                "save_result", saved("research", content["虚构简历"], [pobj], [resume])
            ),
            answer("虚构材料已整理，等待用户核对。"),
        ]
    )
    assert (
        run_work(repo, draft, resources, repo.claim("d-fixture", 120))["state"]
        == "completed"
    )
    candidate_service.advance_one("d-fixture")
    item = candidate_service.get_item(owner, item["item_id"])
    candidate = post(
        "/candidate-items/" + item["item_id"] + "/confirm",
        confirmation(
            item,
            display_name="示例候选人青禾（虚构）",
            summary="用户核对的虚构传感器调试经历。",
        ),
    )
    cid = candidate["candidate_id"]
    cobj = {"kind": "candidate", "id": cid}
    objects = [pobj, cobj]
    evidence = {
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "materials": {
            k: {"text": content[k], "ref": v} for k, v in source_refs.items()
        },
        "knowledge_release": manifest,
        "candidate_id": cid,
        "objects": objects,
        "candidate_initialization": "scripted_model_real_user_HTTP_confirmation",
        "model_boundary": "real_configured_opus5" if real else "scripted",
        "scenario": "reviewed_record_retrospective"
        if review_history
        else "record_without_plan"
        if without_plan
        else "full_journey",
        "stages": [],
    }
    output = None
    port = None
    if real:
        output = Path(os.environ["HR_D_EVIDENCE_DIR"])
        output.mkdir(parents=True, exist_ok=False)
        profile_path = Path(os.environ["HR_D_REAL_PROFILE_FILE"])
        profile = json.loads(profile_path.read_text())
        extended = os.getenv("HR_D_EXTENDED_RESPONSE") == "1"
        max_seconds, max_output = (300, 16384) if extended else (120, 4096)
        # Explicit local experiment only. No private profile or deployment file is rewritten.
        profile = {
            **profile,
            "timeout_seconds": max_seconds,
        }
        repo.settings = replace(
            repo.settings,
            provider_profile=profile,
            budget_profile={
                **repo.settings.budget_profile,
                "max_output_tokens": max_output,
                "reserve": {
                    **repo.settings.budget_profile["reserve"],
                    "total_tokens": max(16000, max_output * 2),
                },
            },
        )
        port = BoundedRealPort(profile, extended=extended)
        evidence["provider"] = _provider_evidence(profile)
        evidence["provider"]["source_configuration"] = {
            "status": "read_from_explicit_local_profile",
            "profile_file_sha256": hashlib.sha256(
                profile_path.read_bytes()
            ).hexdigest(),
        }
        evidence["validation_limits"] = {
            "generation_calls": 24,
            "per_call_output": max_output,
            "per_call_seconds": max_seconds,
            "per_work": repo.settings.budget_profile["limits"],
            "reserve": repo.settings.budget_profile["reserve"],
            "configuration_scope": "explicit_local_validation_only",
        }
        evidence["runner_source"] = {
            "git_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "file_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "captured_before_first_D_model_request": True,
        }
        (output / "fixture.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2)
        )

    def stage(name, prompt, refs, scripted_results):
        work = post(
            "/works",
            {
                "thread_id": None,
                "text": prompt,
                "objects": objects,
                "references": refs,
                "budget_profile": "test",
            },
        )
        if real:
            model = port
        else:
            model = ScriptModel(
                [
                    *[tool("read_resource", {"ref": r}) for r in refs],
                    *[
                        tool("save_result", saved(kind, body, objects, refs))
                        for kind, body in scripted_results
                    ],
                    answer("虚构场景成果已保存。"),
                ]
            )
        done = run_work(repo, model, resources, repo.claim("d-" + name, 1200))
        assert done["work_id"] == work["work_id"]
        snapshot = _stage_evidence(client, repo, done)
        snapshot.update(name=name, prompt=prompt, selected_refs=refs)
        evidence["stages"].append(snapshot)
        assert done["state"] == "completed", done["state"]
        found = {r["kind"]: r for r in snapshot["results"]}
        for kind, _ in scripted_results:
            assert kind in found, (name, sorted(found))
        return found

    try:
        if without_plan or review_history:
            raw_text = content["用户提供的虚构面试原始记录"]
            _, raw_ref = upload_text(uploaded, database, raw_text)
            record = post(
                "/candidates/" + cid + "/interview-records",
                {
                    "material_ref": raw_ref,
                    "title": "虚构原始记录",
                    "occurred_at": None,
                    "position_id": pobj["id"],
                    "interview_plan_ref": None,
                },
            )
            record_path = (
                "/api/hr/agent/candidates/"
                + cid
                + "/interview-records/"
                + record["record_id"]
            )
            evidence["original_record"] = client.get(record_path).json()
            if review_history:
                source = Path(review_history)
                previous = json.loads((source / "evidence.json").read_text())
                assert previous["fixture_sha256"] == evidence["fixture_sha256"]
                refs = [
                    raw_ref,
                    source_refs["临时岗位要求"],
                    source_refs["用户追加的虚构招聘过程记录"],
                ]
                copies = []
                for index, previous_stage in enumerate(previous["stages"], 1):
                    for result in previous_stage["results"]:
                        if result["kind"] not in (
                            "candidate_assessment",
                            "interview_plan",
                        ):
                            continue
                        path = source / f"stage-{index}-{result['kind']}.md"
                        text = path.read_text()
                        assert text == result["body"]
                        _, ref = upload_text(
                            uploaded, database, text, "historical-ai-material.txt"
                        )
                        refs.append(ref)
                        copies.append(
                            {
                                "original_result_ref": result["ref"],
                                "copied_material_ref": ref,
                                "body_sha256": hashlib.sha256(
                                    text.encode()
                                ).hexdigest(),
                            }
                        )
                assert len(copies) == 2
                evidence["historical_AI_copies"] = copies
                found = stage(
                    "reviewed-record-retrospective",
                    "全部为虚构验证。用户提供实际面试原始记录、岗位临时要求和招聘过程说明，另提供两份历史 AI 评估/面试方案的原文副本作为参考。请重新整理实际记录并做本次招聘复盘，分别保存 interview_record 和 retrospective。历史方案是预期安排，不是已发生记录；这些是新工作的成果，不宣称改写原历史成果。",
                    refs,
                    [
                        ("interview_record", "用户自述与实际提问分开保留。"),
                        ("retrospective", "未提供的记录保持未知；所有基础要求仍保留。"),
                    ],
                )
                for result in found.values():
                    assert raw_ref in result["source_refs"]
                assert client.get(record_path).json()["text"] == raw_text
                evidence["engineering_assertions"] = (
                    "passed; semantic_quality_requires_separate_review"
                )
                return evidence
            found = stage(
                "record-without-plan",
                "这是虚构验证。用户仅提供这份实际面试记录，没有提供面试方案。请整理已有记录并保存 interview_record，分清记录陈述和未知。无需先生成方案或等待其他材料。",
                [raw_ref],
                [
                    (
                        "interview_record",
                        "记录陈述由同事提出供电假设；没有提供方案，不能评价方案执行。",
                    )
                ],
            )
            assert raw_ref in found["interview_record"]["source_refs"]
            assert client.get(record_path).json()["text"] == raw_text
            assert evidence["original_record"]["interview_plan_ref"] is None
            evidence["engineering_assertions"] = (
                "passed; semantic_quality_requires_separate_review"
            )
            return evidence
        first = stage(
            "sourcing-assessment",
            "所有材料均为虚构验证。请根据临时岗位要求，形成搜寻策略和这位候选人的岗位评估，分别保存 sourcing 与 candidate_assessment。材料未知处可以保留，不必等待补充，不作录用决定。按问题自主选用方法。",
            [source_refs["临时岗位要求"], resume],
            [
                ("sourcing", "相邻传感器经历可提供诊断工作样本；未联系任何人。"),
                ("candidate_assessment", "记录陈述亲自执行，独立假设设计尚待核验。"),
            ],
        )
        assessment = first["candidate_assessment"]
        # Same exact saved content through conversation, position and candidate links.
        for kind, identity in [("position", pobj["id"]), ("candidate", cid)]:
            response = client.get(
                "/api/hr/agent/results",
                params={"object_kind": kind, "object_id": identity},
            )
            assert response.status_code == 200, response.text
            assert assessment["ref"] in [
                item["ref"] for item in response.json()["items"]
            ]
        exact = client.get(
            f"/api/hr/agent/results/{assessment['ref']['id']}/revisions/{assessment['ref']['revision']}"
        )
        assert exact.json() == assessment
        planned = stage(
            "interview-plan",
            "继续已选候选人的准确评估。请准备针对本人经历的面试方案，解释需要验证什么，保存 interview_plan。这是未来方案，尚未发生面试。",
            [assessment["ref"], source_refs["临时岗位要求"], resume],
            [
                (
                    "interview_plan",
                    "询问供电假设由谁提出、如何设计对照；另询问示波器操作并提出工作样本任务。",
                )
            ],
        )
        plan = planned["interview_plan"]
        record_text = content["用户提供的虚构面试原始记录"]
        _record_aid, raw_ref = upload_text(
            uploaded, database, record_text, "synthetic-interview.txt"
        )
        record = post(
            "/candidates/" + cid + "/interview-records",
            {
                "material_ref": raw_ref,
                "title": "用户提供的虚构面试记录",
                "occurred_at": None,
                "position_id": pobj["id"],
                "interview_plan_ref": plan["ref"],
            },
        )
        original = client.get(
            "/api/hr/agent/candidates/"
            + cid
            + "/interview-records/"
            + record["record_id"]
        )
        assert original.status_code == 200, original.text
        assert original.json()["text"] == record_text
        assert original.json()["authorship"] == "user_supplied"
        evidence["original_record"] = original.json()
        last = stage(
            "record-retrospective",
            "用户已提供实际的虚构面试原始记录。请对照已选方案，整理实际记录并做本次招聘复盘，分别保存 interview_record 和 retrospective。只基于提供的过程材料，分清观察、推断与未知；长期标准变更仍待用户确认。",
            [
                raw_ref,
                plan["ref"],
                assessment["ref"],
                source_refs["临时岗位要求"],
                source_refs["用户追加的虚构招聘过程记录"],
            ],
            [
                (
                    "interview_record",
                    "用户记录：实际由同事提出供电假设。示波器操作未问，工作样本未展示。",
                ),
                (
                    "retrospective",
                    "独立设计尚未证实；保留允许培养的临时基准。一个案例不足以推断渠道转化或通用门槛。",
                ),
            ],
        )
        for result in last.values():
            assert raw_ref in result["source_refs"]
        # The raw user source is still the same text after model-derived results exist.
        assert (
            client.get(
                "/api/hr/agent/candidates/"
                + cid
                + "/interview-records/"
                + record["record_id"]
            ).json()["text"]
            == record_text
        )
        evidence["engineering_assertions"] = (
            "passed; semantic_quality_requires_separate_review"
        )
    finally:
        if output:
            evidence["generation_calls"] = port.calls
            evidence["normalized_stream_observations"] = port.observations
            (output / "evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
            )
            for i, st in enumerate(evidence["stages"], 1):
                for result in st["results"]:
                    (output / f"stage-{i}-{result['kind']}.md").write_text(
                        result["body"]
                    )
    return evidence


def test_fictional_candidate_interview_and_retrospective_http_journey(
    uploaded, intake, database, tmp_path
):
    evidence = journey(uploaded, intake, database, tmp_path)
    assert len(evidence["stages"]) == 3


def test_fictional_record_without_plan_can_be_saved(
    uploaded, intake, database, tmp_path
):
    evidence = journey(uploaded, intake, database, tmp_path, without_plan=True)
    assert len(evidence["stages"]) == 1
    assert evidence["original_record"]["interview_plan_ref"] is None


@pytest.mark.parametrize(
    "extended,seconds,output", [(False, 120, 4096), (True, 300, 16384)]
)
def test_d_validation_port_limits_are_explicit(monkeypatch, extended, seconds, output):
    from types import SimpleNamespace

    from app.hr_agent.model import (
        ConfiguredHttpModelPort,
        ModelEvent,
        ModelTransportError,
    )

    class FakePort:
        def stream(self, request):
            yield ModelEvent("stop", {"reason": "max_tokens"})

    monkeypatch.setattr(ConfiguredHttpModelPort, "from_mapping", lambda _: FakePort())
    profile = {"model": "claude-opus-5", "protocol": "anthropic_messages_sse"}
    with pytest.raises(ValueError):
        BoundedRealPort({**profile, "model": "other"})
    port = BoundedRealPort(profile, extended=extended)
    request = SimpleNamespace(
        attempt_id="synthetic", max_output_tokens=output, deadline_seconds=seconds
    )
    assert len(list(port.stream(request))) == 1
    assert port.observations[0]["stops"] == [{"reason": "max_tokens"}]
    for extra in ({"max_output_tokens": output + 1}, {"deadline_seconds": seconds + 1}):
        with pytest.raises(ModelTransportError):
            list(port.stream(SimpleNamespace(**{**vars(request), **extra})))
    port.calls = 24
    with pytest.raises(ModelTransportError):
        list(port.stream(request))


def test_reviewed_record_retrospective_uses_exact_history_copies(
    uploaded, intake, database, tmp_path
):
    history = Path(__file__).parents[2] / "artifacts/2026-09-11-hr-d-journey/run-3"
    evidence = journey(uploaded, intake, database, tmp_path, review_history=history)
    assert len(evidence["historical_AI_copies"]) == 2


@pytest.mark.skipif(
    not os.getenv("HR_D_REAL_PROFILE_FILE"),
    reason="explicit synthetic D real-model profile and evidence directory required",
)
def test_fictional_d_real_model_evidence(uploaded, intake, database, tmp_path):
    journey(
        uploaded,
        intake,
        database,
        tmp_path,
        real=True,
        without_plan=os.getenv("HR_D_WITHOUT_PLAN") == "1",
        review_history=os.getenv("HR_D_REVIEW_HISTORY"),
    )
