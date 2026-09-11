"""Opt-in C3 public archive research: real provider, disposable DB, bounded resume."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest
from tests.test_hr_agent_b_public_model import _provider_evidence, _stage_evidence
from tests.test_hr_agent_materials import database, secured, uploaded

_FIXTURES = (database, secured, uploaded)


def public_archive(bundle, company):
    """Use complete original bodies, never the normalized excerpts or aggregates."""
    bundle = Path(bundle)
    index = {
        item["sha256"]: item
        for item in json.loads((bundle / "raw-evidence-index.json").read_text())
    }
    normalized = [
        json.loads(line)
        for line in (bundle / "normalized-jobs.jsonl").read_text().splitlines()
        if line
    ]
    selected = [row for row in normalized if row["company_key"] == company]
    assert selected and len({row["job_id"] for row in selected}) == len(selected)
    jobs = []
    for row in selected:
        digest = row["evidence_sha256"]
        locator = Path(index[digest]["locator"])
        assert not locator.is_absolute() and ".." not in locator.parts
        raw = (bundle / locator).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        found = [
            item
            for item in json.loads(raw)["jobs"]
            if str(item["id"]) == row["public_job_key"]
        ]
        assert len(found) == 1
        original = found[0]
        assert isinstance(original["duty"], str) and isinstance(
            original["requirements"], str
        )
        jobs.append(
            {
                **{
                    key: row[key]
                    for key in (
                        "job_id",
                        "title",
                        "source_url",
                        "observed_at",
                        "public_job_key",
                        "evidence_sha256",
                    )
                },
                "duty": original["duty"],
                "requirements": original["requirements"],
            }
        )
    text = f"# {company} 公开招聘归档全文\n\n本材料范围仅为该归档中 {len(jobs)} 个岗位身份，不代表企业当前全部岗位，也不能证明组织实际运行情况。以下逐条保留原始职责与要求，不使用规范层摘要。\n"
    for job in jobs:
        text += f"\n## {job['title']}\njob_id: {job['job_id']}\nsource_url: {job['source_url']}\nobserved_at: {job['observed_at']}\n\n### 职责原文\n{job['duty']}\n\n### 要求原文\n{job['requirements']}\n"
    return text, {
        "company_key": company,
        "job_count": len(jobs),
        "bundle_manifest_sha256": hashlib.sha256(
            (bundle / "manifest.json").read_bytes()
        ).hexdigest(),
        "jobs": jobs,
        "scope": "all normalized identities for this company in this static archive; original full bodies; not current company completeness",
    }


@pytest.mark.skipif(
    not os.getenv("HR_C_RESEARCH_BUNDLE"),
    reason="explicit public archive path required",
)
def test_research_archive_uses_all_original_bodies():
    text, provenance = public_archive(os.environ["HR_C_RESEARCH_BUNDLE"], "revopoint")
    assert provenance["job_count"] == 19
    assert (
        sum(len(job["duty"]) + len(job["requirements"]) for job in provenance["jobs"])
        == 13397
    )
    for job in provenance["jobs"]:
        assert job["duty"] in text and job["requirements"] in text


@pytest.mark.skipif(
    not (os.getenv("HR_C_REAL_PROFILE_FILE") and os.getenv("HR_C_RESEARCH_BUNDLE")),
    reason="explicit public real-provider profile and archive required",
)
def test_public_full_research_budget_resume(uploaded, database, tmp_path):
    from app.attachments.derivatives import DerivativeBuilder
    from app.attachments.scanner import TrustedInternalScanner
    from app.attachments.validation import AttachmentValidator
    from app.attachments.worker import AttachmentProcessor
    from app.attachments.worker_runtime import AttachmentProcessingRepository
    from app.hr_agent.knowledge import KnowledgeReleases
    from app.hr_agent.model import ConfiguredHttpModelPort
    from app.hr_agent.resources import ResourceReader
    from app.hr_agent.runtime import run_work
    from hr_agent_support import make_hr_settings
    from tools.hr_agent.build_knowledge_release import build

    client, headers, repo, _owner, materials, _, _, store = uploaded
    text, provenance = public_archive(os.environ["HR_C_RESEARCH_BUNDLE"], "revopoint")
    data = text.encode()
    upload = client.post(
        "/api/v1/attachments/uploads",
        headers=headers,
        json={
            "conversation_id": None,
            "original_name": "revopoint-public-archive.txt",
            "declared_mime": "text/plain",
            "declared_size": len(data),
        },
    )
    assert upload.status_code == 201, upload.text
    upload = upload.json()
    path = "/api/v1/attachments/uploads/" + upload["upload_id"]
    assert (
        client.put(
            path + "/content",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=data,
        ).status_code
        == 200
    )
    assert client.post(path + "/complete", headers=headers).status_code == 200
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
        worker_id="c3-public",
    )
    for _ in range(4):
        if not asyncio.run(processor.process_next()):
            break
    material_response = client.get("/api/hr/agent/materials/" + upload["attachment_id"])
    assert material_response.status_code == 200, material_response.text
    material = material_response.json()
    assert material["parse_state"] == "ready"
    build(Path(__file__).parents[1] / "hr_agent_knowledge", tmp_path / "release")
    knowledge = KnowledgeReleases(tmp_path / "release")
    budget = {
        "id": "c3-public",
        "service_limits": {
            "model_calls": 24,
            "total_tokens": 1200000,
            "active_seconds": 1800,
        },
        "limits": {"model_calls": 4, "total_tokens": 600000, "active_seconds": 900},
        "reserve": {"model_calls": 1, "total_tokens": 16000, "active_seconds": 30},
        "max_output_tokens": 8192,
        "input_target_tokens": 50000,
        "input_trigger_tokens": 70000,
        "work_retention_seconds": 3600,
    }
    budget_file = tmp_path / "c3-budget.json"
    budget_file.write_text(json.dumps(budget))
    budget_file.chmod(0o600)
    source_profile = json.loads(Path(os.environ["HR_C_REAL_PROFILE_FILE"]).read_text())
    run_profile = dict(
        source_profile,
        timeout_seconds=int(os.getenv("HR_C_REAL_TIMEOUT_SECONDS", "120")),
    )
    run_profile_file = tmp_path / "private-run-profile.json"
    run_profile_file.write_text(json.dumps(run_profile))
    run_profile_file.chmod(0o600)
    repo.settings = make_hr_settings(
        tmp_path / "c3-settings",
        PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(run_profile_file),
        PLATFORM_HR_AGENT_MODEL="claude-opus-5",
        PLATFORM_HR_AGENT_BUDGET_PROFILE_FILE=str(budget_file),
        PLATFORM_HR_AGENT_KNOWLEDGE_DIR=str(tmp_path / "release"),
    )
    profile = repo.settings.provider_profile
    resources = ResourceReader(repo, knowledge, material_service=materials)
    repo.scope_validator = resources.validate_scope
    repo.release_provider = knowledge.metadata
    repo.release_validator = knowledge.validate_record
    prompt = "请研究所选 Revopoint 公开归档的全部19个岗位全文（静态包范围，不是现行官网）。一次回答一个问题：它在从研发到客户交付的过程中，招聘哪些能解决具体问题的人；哪些自建/外采或分工关系有直接证据，哪些不能判断？请先阅读全部职责与要求，自主选择有帮助的方法。使用岗位job_id及少量原文支撑承重判断，保留反例和不能推出的关系，不要用计数或字段复述代替判断，不把共同词汇拼成已运行团队。完成时保存research成果，说明未完成的部分。系统可能因预算暂停；暂停时诚实保留未读范围，追加后继续同一个问题，不把读过一页称为全文读完。这次先交付800–1200字的核心研究要点，聚焦最能改变找人做法的两到三个判断；不重复材料全文。需要更长展开可以另存后续成果。每次先保存再简短回复；预算收尾只保存简短进度与未完成问题，不在收尾展开整份报告。"
    response = client.post(
        "/api/hr/agent/works",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "thread_id": None,
            "text": prompt,
            "objects": [],
            "references": [material["text_ref"]],
            "budget_profile": "c3-public",
        },
    )
    assert response.status_code == 201, response.text
    work = response.json()
    out = Path(os.environ["HR_C_REAL_OUTPUT_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    stages = []
    transport_evidence = []
    repository_root = Path(__file__).parents[2]
    runner_files = (
        "backend/tests/test_hr_agent_c_public_research.py",
        "backend/tests/test_hr_agent_b_public_model.py",
        "backend/app/hr_agent/model.py",
        "backend/app/hr_agent/repository.py",
        "backend/app/hr_agent/config.py",
        "backend/app/hr_agent/context.py",
    )
    runner_fingerprints = {
        name: hashlib.sha256((repository_root / name).read_bytes()).hexdigest()
        for name in runner_files
    }

    class RecordedProvider:
        def __init__(self):
            self.provider = ConfiguredHttpModelPort.from_mapping(profile)

        def stream(self, request):
            record = {
                "attempt_id": str(request.attempt_id),
                "stops": [],
                "text_characters": 0,
                "tool_argument_characters": 0,
                "error_code": None,
            }
            transport_evidence.append(record)
            started = monotonic()
            try:
                for event in self.provider.stream(request):
                    if event.type == "stop":
                        record["stops"].append(event.payload)
                    elif event.type == "text_delta":
                        record["text_characters"] += len(event.payload.get("text", ""))
                    elif event.type == "tool_delta":
                        record["tool_argument_characters"] += len(
                            event.payload.get("arguments_delta", "")
                        )
                    yield event
            except Exception as error:
                record["error_code"] = getattr(error, "code", "local_capture_failure")
                raise
            finally:
                record["duration_seconds"] = monotonic() - started

    def snapshot(done):
        stages.append(_stage_evidence(client, repo, done))
        evidence = {
            "provider": _provider_evidence(profile),
            "source_provider_nonsecret": _provider_evidence(source_profile),
            "test_profile_override": {
                "timeout_seconds": run_profile["timeout_seconds"]
            },
            "budget_configuration": budget,
            "input": prompt,
            "public_provenance": provenance,
            "source_text_sha256": hashlib.sha256(data).hexdigest(),
            "source_text_characters": len(text),
            "release": knowledge.metadata(),
            "stages": stages,
            "transport_observations": transport_evidence,
            "acceptance": "Public archived jobs only; semantic quality requires independent professional review; no production publication.",
            "runner_files_sha256_before_first_send": runner_fingerprints,
        }
        (out / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n"
        )
        for stage_index, stage in enumerate(stages, 1):
            for index, result in enumerate(stage["results"], 1):
                (out / f"stage-{stage_index}-result-{index}.md").write_text(
                    "# " + result["title"] + "\n\n" + result["body"] + "\n"
                )

    done = run_work(
        repo,
        RecordedProvider(),
        resources,
        repo.claim("c3-public-initial", 1200),
    )
    snapshot(done)
    assert done["state"] == "waiting_budget", done["state"]
    old_usage = done["budget"]["charged_tokens"]
    response = client.post(
        f"/api/hr/agent/works/{work['work_id']}/budget-extensions",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "expected_budget_revision": done["budget"]["revision"],
            "addition": {
                "model_calls": 20,
                "total_tokens": 600000,
                "active_seconds": 900,
            },
            "reason": "公开材料研究验证：显式追加预算继续未完成范围",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["budget"]["charged_tokens"] == old_usage
    done = run_work(
        repo,
        RecordedProvider(),
        resources,
        repo.claim("c3-public-resume", 1800),
    )
    snapshot(done)
    # Optional explicit review is a new user input, never an implicit provider retry.
    # Preserve the initial/resume stages, including any failure, for separate acceptance.
    review_file = os.getenv("HR_C_REVIEW_FILE")
    if review_file:
        review = Path(review_file).read_text()
        reviewed_usage = done["budget"]["charged_tokens"]
        response = client.post(
            f"/api/hr/agent/works/{work['work_id']}/inputs",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "expected_input_revision": done["input_revision"],
                "text": review,
                "objects": [],
                "references": [material["text_ref"], *done["result_refs"]],
                "question_id": None,
            },
        )
        assert response.status_code == 202, response.text
        done = run_work(
            repo, RecordedProvider(), resources, repo.claim("c3-public-review", 1800)
        )
        snapshot(done)
        review_record = {
            "text": review,
            "sha256": hashlib.sha256(review.encode()).hexdigest(),
            "previous_usage": reviewed_usage,
            "current_usage": done["budget"]["charged_tokens"],
            "input_revision": done["input_revision"],
        }
        (out / "explicit-review.json").write_text(
            json.dumps(review_record, ensure_ascii=False, indent=2) + "\n"
        )
        assert done["budget"]["charged_tokens"] >= reviewed_usage
    assert done["state"] in ("completed", "waiting_user"), done["state"]
    assert done["budget"]["charged_tokens"] >= old_usage
    assert any(result["kind"] == "research" for result in stages[-1]["results"]), (
        "No saved research result: inspect evidence; no quality claim."
    )
