from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.hr.candidate_models import (
    AppendHumanFeedback,
    AttachCandidateDraftExecution,
    Candidate,
    CandidateAnalysisVersion,
    CandidateDocument,
    CandidateDraft,
    CandidateDraftProcessingAttempt,
    CandidateEnvelopeFragment,
    ClaimNextCandidateDraft,
    ComparePositionCandidates,
    CompleteCandidateDraft,
    ConfirmCandidateDraft,
    CreateCandidateAnalysis,
    CreateCandidateDraftBatch,
    FailCandidateDraft,
    PositionCandidate,
    RetryCandidateDraft,
)

NOW = datetime.now(UTC)


def test_analysis_requires_exact_context_and_documents() -> None:
    position_candidate_id = uuid4()
    context_version_id = uuid4()
    document_id = uuid4()
    request_id = uuid4()

    value = CreateCandidateAnalysis(
        owner_id=uuid4(),
        position_candidate_id=position_candidate_id,
        context_version_id=context_version_id,
        document_ids=(document_id,),
        analysis_kind="match",
        client_request_id=request_id,
        result={"summary": "有嵌入式经验"},
        evidence=({"claim": "嵌入式", "document_id": str(document_id)},),
        unknowns=("量产规模",),
        conflicts=(),
        verification_questions=("请说明量产规模",),
        agent_version="hr-r12",
        model_version="model-v1",
    )

    assert value.document_ids == (document_id,)
    assert value.context_version_id == context_version_id
    assert value.unknowns == ("量产规模",)
    with pytest.raises(ValueError, match="documents required"):
        replace(value, document_ids=())


def test_candidate_records_are_frozen_and_preserve_human_ai_separation() -> None:
    owner_id, position_id, candidate_id, context_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    draft = CandidateDraft(
        draft_id=uuid4(), owner_id=owner_id, position_id=position_id,
        attachment_id=uuid4(), batch_request_id=uuid4(), client_request_id=uuid4(),
        state="ready", extracted_facts={"stable_name": "候选人甲"},
        identity_candidates=(), error_code=None, row_version=2,
        created_at=NOW, updated_at=NOW,
    )
    candidate = Candidate(
        candidate_id=candidate_id, owner_id=owner_id, stable_name="候选人甲",
        facts={"skills": ["Python"]}, created_at=NOW, updated_at=NOW,
    )
    document = CandidateDocument(
        document_id=uuid4(), owner_id=owner_id, candidate_id=candidate_id,
        attachment_id=draft.attachment_id, source_draft_id=draft.draft_id,
        document_kind="resume", version_number=1, content_sha256="a" * 64,
        status="active", created_at=NOW,
    )
    relation = PositionCandidate(
        position_candidate_id=uuid4(), owner_id=owner_id,
        position_id=position_id, candidate_id=candidate_id,
        context_version_id=context_id, source_draft_id=draft.draft_id,
        status="active", row_version=1, created_at=NOW, updated_at=NOW,
    )
    analysis = CandidateAnalysisVersion(
        analysis_version_id=uuid4(), owner_id=owner_id,
        position_candidate_id=relation.position_candidate_id,
        position_id=position_id, candidate_id=candidate_id,
        context_version_id=context_id, version_number=1, analysis_kind="match",
        document_ids=(document.document_id,), feedback_ids=(),
        result={"summary": "适配"}, evidence=(), unknowns=("领导力",),
        conflicts=(), verification_questions=("举例说明领导经历",),
        agent_version="hr-r12", model_version="model-v1", created_at=NOW,
    )
    feedback = AppendHumanFeedback(
        owner_id=owner_id, position_candidate_id=relation.position_candidate_id,
        analysis_version_id=analysis.analysis_version_id,
        feedback_kind="correction", conclusion_key="leadership",
        correction="候选人实际带过 6 人团队", reason="HR 电话确认",
        client_request_id=uuid4(),
    )

    assert analysis.unknowns == ("领导力",)
    assert feedback.correction == "候选人实际带过 6 人团队"
    with pytest.raises(FrozenInstanceError):
        candidate.stable_name = "changed"  # type: ignore[misc]


def test_draft_state_error_and_confirmation_commands_are_bounded() -> None:
    common = dict(
        draft_id=uuid4(), owner_id=uuid4(), position_id=uuid4(),
        attachment_id=uuid4(), batch_request_id=uuid4(), client_request_id=uuid4(),
        extracted_facts={}, identity_candidates=(), row_version=1,
        created_at=NOW, updated_at=NOW,
    )
    failed = CandidateDraft(state="failed", error_code="parse_failed", **common)
    assert failed.error_code == "parse_failed"
    with pytest.raises(ValueError, match="draft error state invalid"):
        CandidateDraft(state="ready", error_code="parse_failed", **common)

    command = ConfirmCandidateDraft(
        owner_id=common["owner_id"], draft_id=common["draft_id"],
        client_request_id=uuid4(), expected_row_version=3,
        candidate_id=uuid4(), stable_name="候选人甲", confirmed_facts={},
        merge_candidate_id=None,
    )
    assert command.expected_row_version == 3
    with pytest.raises(ValueError, match="merge target invalid"):
        replace(command, merge_candidate_id=command.candidate_id)


def test_batch_retry_compare_and_envelope_require_precise_nonempty_scope() -> None:
    owner_id, position_id = uuid4(), uuid4()
    attachments = (uuid4(), uuid4(), uuid4())
    batch = CreateCandidateDraftBatch(
        owner_id=owner_id, position_id=position_id,
        attachment_ids=attachments, client_request_id=uuid4(),
    )
    retry = RetryCandidateDraft(
        owner_id=owner_id, draft_id=uuid4(), client_request_id=uuid4(),
        expected_row_version=2,
    )
    comparison = ComparePositionCandidates(
        owner_id=owner_id, position_id=position_id,
        position_candidate_ids=(uuid4(), uuid4()),
        context_version_id=uuid4(), client_request_id=uuid4(),
        agent_version="hr-r12", model_version="model-v1",
    )
    fragment = CandidateEnvelopeFragment(
        candidate_id=uuid4(), position_candidate_id=uuid4(),
        context_version_id=comparison.context_version_id,
        document_ids=(uuid4(),), document_attachment_ids=(uuid4(),),
        human_feedback_ids=(uuid4(),), prompt_context="候选人事实",
    )

    assert batch.attachment_ids == attachments
    assert retry.expected_row_version == 2
    assert len(comparison.position_candidate_ids) == 2
    assert fragment.prompt_context == "候选人事实"
    with pytest.raises(ValueError, match="attachments required"):
        replace(batch, attachment_ids=())
    with pytest.raises(ValueError, match="comparison candidates invalid"):
        replace(comparison, position_candidate_ids=(uuid4(),))
    with pytest.raises(ValueError, match="document scope invalid"):
        replace(fragment, document_ids=())
    with pytest.raises(ValueError, match="feedback scope invalid"):
        replace(fragment, human_feedback_ids=tuple(uuid4() for _ in range(101)))
    with pytest.raises(ValueError, match="candidate prompt context invalid"):
        replace(fragment, prompt_context="候" * 65537)


def test_protected_or_unrelated_personal_fields_are_rejected_from_facts() -> None:
    invalid_fact_sets = (
        {"gender": "女"},
        {"race": "x"},
        {"marital_status": "x"},
        {"religion": "x"},
        {"political_affiliation": "x"},
        {"offer_status": "pending"},
    )
    for facts in invalid_fact_sets:
        with pytest.raises(ValueError, match="candidate facts contain forbidden fields"):
            Candidate(
                candidate_id=uuid4(), owner_id=uuid4(), stable_name="候选人",
                facts=facts, created_at=NOW, updated_at=NOW,
            )


def test_uuid_fields_do_not_accept_strings() -> None:
    with pytest.raises(ValueError, match="identifiers invalid"):
        RetryCandidateDraft(
            owner_id=UUID(int=1), draft_id="not-a-uuid",  # type: ignore[arg-type]
            client_request_id=UUID(int=2), expected_row_version=1,
        )


def test_complete_and_fail_commands_validate_before_repository_writes() -> None:
    owner_id, draft_id, request_id = uuid4(), uuid4(), uuid4()
    complete = CompleteCandidateDraft(
        owner_id, draft_id, request_id, 2,
        {"skills": ["Python"], "projects": [{"name": "平台"}]}, (),
    )
    fail = FailCandidateDraft(owner_id, draft_id, uuid4(), 2, "parse_failed")

    assert complete.expected_row_version == fail.expected_row_version == 2
    with pytest.raises(ValueError, match="candidate facts contain forbidden fields"):
        replace(complete, extracted_facts={"profile": {"gender": "女"}})
    with pytest.raises(ValueError, match="candidate facts invalid"):
        replace(complete, extracted_facts={"unreviewed_blob": "raw"})
    with pytest.raises(ValueError, match="draft error code invalid"):
        replace(fail, error_code=" ")


@pytest.mark.parametrize(
    "payload",
    (
        {"summary": "x", "details": {"pipeline_stage": "screen"}},
        {"summary": "x", "sources": [{"immutable_locator": "secret"}]},
        {"summary": "x", "nested": {"object_ref": "secret"}},
    ),
)
def test_analysis_rejects_nested_ats_locator_and_protected_fields(payload) -> None:
    with pytest.raises(ValueError, match="analysis .* forbidden fields"):
        CreateCandidateAnalysis(
            uuid4(), uuid4(), uuid4(), (uuid4(),), "match", uuid4(),
            payload, ({"claim": "x"},), (), (), (), "hr-r12", "model-v1",
        )


def test_evidence_rejects_nested_locator_fields() -> None:
    with pytest.raises(ValueError, match="analysis evidence contains forbidden fields"):
        CreateCandidateAnalysis(
            uuid4(), uuid4(), uuid4(), (uuid4(),), "match", uuid4(),
            {"summary": "x"},
            ({"claim": "x", "source": {"storage_path": "/private"}},),
            (), (), (), "hr-r12", "model-v1",
        )


def test_non_correction_feedback_cannot_carry_correction_text() -> None:
    with pytest.raises(ValueError, match="feedback correction invalid"):
        AppendHumanFeedback(
            uuid4(), uuid4(), uuid4(), "accepted", "summary", "changed",
            "reviewed", uuid4(),
        )


def test_processing_attempt_keeps_durable_execution_identity() -> None:
    attempt = CandidateDraftProcessingAttempt(
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4(),
        "candidate-parser-1", None, None, None, "processing", 2, 3,
        NOW, NOW, None, None, None,
    )

    assert attempt.execution_job_id is None
    assert attempt.state == "processing"
    with pytest.raises(ValueError, match="processing attempt state invalid"):
        replace(attempt, state="unknown")


def test_parser_queue_commands_separate_claim_from_execution_binding() -> None:
    claim = ClaimNextCandidateDraft(uuid4(), "candidate-parser-1", 300)
    attach = AttachCandidateDraftExecution(
        claim.attempt_id, claim.worker_id, uuid4(), uuid4(), uuid4()
    )

    assert claim.lease_seconds == 300
    assert attach.execution_job_id is not None
    with pytest.raises(ValueError, match="processing lease invalid"):
        replace(claim, lease_seconds=29)
