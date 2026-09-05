from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_agent_brain_conversation_repository import _codec

from app.agent_brain.conversation_repository import message_subject
from app.hr.candidate_repository import CandidateUnavailable
from app.hr.structured_output import encode_hr_envelope
from app.hr.task_result_projection import (
    ClaimedHrTaskResult,
    HrTaskResultReconciler,
    hr_task_result_projection_loop,
)
from app.hr.task_service import _PROMPTS

PDF_VERSION = UUID(int=9)


def _claim(
    task_kind: str = "jd",
    *,
    text: str = "完整真实结果",
    model_version: str = "hr-runtime-execution-v1",
    output_artifact_version_id: UUID | None = PDF_VERSION,
) -> ClaimedHrTaskResult:
    codec = _codec()
    conversation_id = UUID(int=10)
    message_id = UUID(int=12)
    sealed = codec.seal_json(
        message_subject(conversation_id, message_id), {"text": text}
    )
    candidate = task_kind.startswith("candidate_")
    return ClaimedHrTaskResult(
        task_record_id=UUID(int=1),
        task_request_id=UUID(int=2),
        projection_request_id=UUID(int=3),
        owner_id=UUID(int=4),
        position_id=UUID(int=5),
        task_kind=task_kind,
        official_version_id=UUID(int=6),
        context_version_id=UUID(int=7),
        material_attachment_ids=(UUID(int=8),),
        candidate_id=UUID(int=20) if candidate else None,
        position_candidate_id=UUID(int=21) if candidate else None,
        document_ids=(UUID(int=22),) if candidate else (),
        feedback_ids=(UUID(int=23),) if candidate else (),
        conversation_id=conversation_id,
        turn_id=UUID(int=11),
        output_artifact_version_id=output_artifact_version_id,
        assistant_message_id=message_id,
        agent_id="hr-bot",
        execution_model_version=model_version,
        content_ciphertext=sealed.ciphertext,
        encryption_key_version=sealed.key_version,
    )


class _Ledger:
    def __init__(self, claims=()) -> None:
        self.claims = list(claims)
        self.completed = []
        self.failed = []
        self.released = []

    def claim(self, worker_id, lease_seconds):
        return self.claims.pop(0) if self.claims else None

    def complete(self, claim, worker_id, resource_id):
        self.completed.append((claim, worker_id, resource_id))

    def fail(self, claim, worker_id, error_code):
        self.failed.append((claim, worker_id, error_code))

    def release(self, claim, worker_id, error_code):
        self.released.append((claim, worker_id, error_code))


class _Positions:
    def __init__(self) -> None:
        self.calls = []

    def create_draft(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(context_version_id=UUID(int=30))


class _Candidates:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.calls = []
        self.unavailable = unavailable

    def add_analysis(self, command):
        self.calls.append(command)
        if self.unavailable:
            raise CandidateUnavailable("temporary")
        return SimpleNamespace(analysis_version_id=UUID(int=31))


def _reconciler(ledger, positions=None, candidates=None):
    return HrTaskResultReconciler(
        ledger,
        positions or _Positions(),
        candidates or _Candidates(),
        _codec(),
        worker_id="hr-result-projector.test",
    )


def test_claim_requires_persisted_execution_model_version() -> None:
    with pytest.raises(ValueError):
        ClaimedHrTaskResult(
            **{
                name: getattr(_claim(), name)
                for name in _claim().__dataclass_fields__
                if name != "execution_model_version"
            },
            execution_model_version=" ",
        )


@pytest.mark.parametrize(
    ("task_kind", "module"),
    [
        ("jd", "jd"),
        ("jr", "jr"),
        ("talent_profile", "talent_profile"),
        ("sourcing_strategy", "sourcing_strategy"),
        ("position_interview_plan", "interview_standard"),
    ],
)
def test_position_results_create_exact_context_module(task_kind, module) -> None:
    claim = _claim(task_kind)
    ledger = _Ledger((claim,))
    positions = _Positions()

    assert _reconciler(ledger, positions=positions).reconcile_one() is True

    call = positions.calls[0]
    assert call == {
        "owner_id": claim.owner_id,
        "position_id": claim.position_id,
        "request_id": claim.projection_request_id,
        "base_context_version_id": claim.context_version_id,
        "official_version_id": claim.official_version_id,
        "modules": {module: {"text": "完整真实结果"}},
        "summary": "完整真实结果",
        "source_conversation_id": claim.conversation_id,
        "source_turn_id": claim.turn_id,
        "source_artifact_version_id": claim.output_artifact_version_id,
        "source_material_attachment_ids": claim.material_attachment_ids,
        "agent_id": "hr-bot",
        "model_version": "hr-runtime-execution-v1",
        "created_by": claim.owner_id,
    }
    assert ledger.completed == [(claim, "hr-result-projector.test", UUID(int=30))]


def test_candidate_match_projects_exact_structured_evidence() -> None:
    payload = {
        "summary": "总体匹配",
        "dimensions": {"technical": "strong"},
        "evidence": [{"resume_fact": "负责挤出系统"}],
        "gaps": ["未见海外交付"],
        "risks": ["团队规模不明确"],
        "unknowns": ["量产良率经验待验证"],
        "verification_questions": ["请说明量产良率。"],
    }
    text = "# 匹配分析\n\n总体匹配。\n\n" + encode_hr_envelope(
        "candidate_match", payload
    )
    claim = _claim("candidate_match", text=text, output_artifact_version_id=None)
    ledger = _Ledger((claim,))
    candidates = _Candidates()

    assert _reconciler(ledger, candidates=candidates).reconcile_one() is True

    command = candidates.calls[0]
    assert command.owner_id == claim.owner_id
    assert command.position_candidate_id == claim.position_candidate_id
    assert command.context_version_id == claim.context_version_id
    assert command.document_ids == claim.document_ids
    assert command.feedback_ids == claim.feedback_ids
    assert command.analysis_kind == "match"
    assert command.client_request_id == claim.projection_request_id
    assert command.result == payload
    assert command.evidence == ({"resume_fact": "负责挤出系统"},)
    assert command.unknowns == ("量产良率经验待验证",)
    assert command.conflicts == ()
    assert command.verification_questions == ("请说明量产良率。",)
    assert command.source_artifact_version_id is None
    assert command.agent_version == "hr-bot"
    assert command.model_version == "hr-runtime-execution-v1"


def test_candidate_interview_plan_projects_exact_questions_and_pdf() -> None:
    payload = {
        "title": "结构工程师面试题",
        "questions": [
            {
                "verification_goal": "验证量产经验",
                "candidate_reason": "简历提及量产",
                "question": "请说明量产挑战。",
                "follow_ups": ["良率如何？"],
                "strong_evidence": ["给出量化指标"],
                "risk_signals": ["无法说明本人贡献"],
            }
        ],
    }
    text = "# 专属面试题\n\n请按顺序提问。\n\n" + encode_hr_envelope(
        "candidate_interview_plan", payload
    )
    claim = _claim("candidate_interview_plan", text=text)
    ledger = _Ledger((claim,))
    candidates = _Candidates()

    assert _reconciler(ledger, candidates=candidates).reconcile_one() is True

    command = candidates.calls[0]
    assert command.analysis_kind == "candidate_interview_plan"
    assert command.result == payload
    assert command.evidence == ()
    assert command.unknowns == ()
    assert command.conflicts == ()
    assert command.verification_questions == ("请说明量产挑战。",)
    assert command.source_artifact_version_id == claim.output_artifact_version_id


@pytest.mark.parametrize(
    ("task_kind", "text", "artifact_id"),
    [
        ("candidate_match", "只有可读文本", None),
        (
            "candidate_interview_plan",
            "只有可读文本",
            UUID(int=9),
        ),
        (
            "candidate_interview_plan",
            "# 面试题\n\n"
            + encode_hr_envelope(
                "candidate_interview_plan",
                {"title": "面试题", "questions": []},
            ),
            None,
        ),
    ],
)
def test_candidate_results_require_exact_envelope_and_interview_pdf(
    task_kind, text, artifact_id
) -> None:
    claim = _claim(
        task_kind, text=text, output_artifact_version_id=artifact_id
    )
    ledger = _Ledger((claim,))
    candidates = _Candidates()

    assert _reconciler(ledger, candidates=candidates).reconcile_one() is True

    assert candidates.calls == []
    assert ledger.failed == [
        (claim, "hr-result-projector.test", "result_invalid")
    ]


def test_candidate_task_prompts_require_markdown_envelope_and_named_pdf() -> None:
    match_prompt = _PROMPTS["candidate_match"]
    interview_prompt = _PROMPTS["candidate_interview_plan"]

    assert "可读 Markdown" in match_prompt
    assert "candidate_match" in match_prompt
    assert "platform-hr-v1" in match_prompt
    assert "unpadded-base64url-canonical-json" in match_prompt
    for key in (
        "summary", "dimensions", "evidence", "gaps", "risks", "unknowns",
        "verification_questions",
    ):
        assert key in match_prompt
    assert "可读 Markdown" in interview_prompt
    assert "candidate_interview_plan" in interview_prompt
    assert "platform-hr-v1" in interview_prompt
    for key in (
        "verification_goal", "candidate_reason", "question", "follow_ups",
        "strong_evidence", "risk_signals",
    ):
        assert key in interview_prompt
    assert "write_output" in interview_prompt
    assert "<岗位>-<候选人>-面试题-v<版本>.pdf" in interview_prompt


def test_backlog_projection_uses_execution_snapshot_not_projector_runtime() -> None:
    claim = _claim("jd", model_version="hr-runtime-before-upgrade")
    positions = _Positions()

    assert _reconciler(_Ledger((claim,)), positions=positions).reconcile_one() is True

    assert positions.calls[0]["model_version"] == "hr-runtime-before-upgrade"


def test_bad_result_is_failed_and_next_result_is_not_blocked() -> None:
    bad = _claim("jd")
    bad = ClaimedHrTaskResult(
        **{
            name: getattr(bad, name)
            for name in bad.__dataclass_fields__
            if name not in {"content_ciphertext"}
        },
        content_ciphertext=b"not-sealed",
    )
    good = _claim("jr")
    ledger = _Ledger((bad, good))
    positions = _Positions()
    reconciler = _reconciler(ledger, positions=positions)

    assert reconciler.reconcile_one() is True
    assert reconciler.reconcile_one() is True

    assert ledger.failed == [(bad, "hr-result-projector.test", "result_invalid")]
    assert len(positions.calls) == 1
    assert positions.calls[0]["modules"] == {"jr": {"text": "完整真实结果"}}


def test_transient_service_failure_releases_claim_for_retry() -> None:
    claim = _claim(
        "candidate_match",
        text="# 匹配\n\n结果。\n\n" + encode_hr_envelope(
            "candidate_match",
            {
                "summary": "结果",
                "dimensions": {},
                "evidence": [],
                "gaps": [],
                "risks": [],
                "unknowns": [],
                "verification_questions": [],
            },
        ),
        output_artifact_version_id=None,
    )
    ledger = _Ledger((claim,))

    assert (
        _reconciler(ledger, candidates=_Candidates(unavailable=True)).reconcile_one()
        is True
    )

    assert ledger.released == [
        (claim, "hr-result-projector.test", "projection_unavailable")
    ]
    assert ledger.failed == []


def test_async_projection_loop_continues_until_cancelled() -> None:
    class Reconciler:
        def __init__(self):
            self.calls = 0
            self.ready = asyncio.Event()

        def reconcile_one(self):
            self.calls += 1
            if self.calls == 2:
                self.ready.set()
            return self.calls <= 2

    async def scenario():
        reconciler = Reconciler()
        task = asyncio.create_task(
            hr_task_result_projection_loop(reconciler, idle_seconds=0.001)
        )
        await asyncio.wait_for(reconciler.ready.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reconciler.calls >= 2

    asyncio.run(scenario())
