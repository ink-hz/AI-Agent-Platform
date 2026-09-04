from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.candidate_models import (
    AppendHumanFeedback,
    Candidate,
    CandidateAnalysisVersion,
    CandidateDocument,
    CandidateDraft,
    ComparePositionCandidates,
    ConfirmCandidateDraft,
    ConfirmedCandidate,
    CreateCandidateAnalysis,
    CreateCandidateDraftBatch,
    HumanFeedback,
    PositionCandidate,
    RetryCandidateDraft,
)
from app.hr.candidate_repository import CandidateConflict
from app.hr.candidate_service import (
    CandidateIdentityConflict,
    CandidateScopeViolation,
    CandidateService,
)

NOW = datetime.now(UTC)


def _draft(*, state="ready", identities=(), owner_id=None, position_id=None):
    return CandidateDraft(
        uuid4(), owner_id or uuid4(), position_id or uuid4(), uuid4(),
        uuid4(), uuid4(), state, {"stable_name": "候选人甲"}, identities,
        "parse_failed" if state == "failed" else None, 2, NOW, NOW,
    )


def _relation(owner_id, position_id, context_id, candidate_id=None):
    return PositionCandidate(
        uuid4(), owner_id, position_id, candidate_id or uuid4(), context_id,
        uuid4(), "active", 1, NOW, NOW,
    )


class MemoryRepository:
    def __init__(self, drafts=()):
        self.drafts = {draft.draft_id: draft for draft in drafts}
        self.create_calls = []
        self.confirm_calls = []
        self.retry_calls = []
        self.analysis_calls = []
        self.feedback_calls = []
        self.relations = {}
        self.feedback = {}
        self.batches = {}
        self.analysis_replays = {}

    def register_batch(self, command):
        key = (command.owner_id, command.client_request_id)
        payload = (command.position_id, command.attachment_ids)
        if key in self.batches and self.batches[key] != payload:
            raise CandidateConflict()
        self.batches[key] = payload

    def claim_draft(self, command):
        raise AssertionError("not used")

    def processing_attempt(self, owner_id, attempt_id):
        raise AssertionError("not used")

    def complete_claimed_draft(self, attempt_id, command):
        raise AssertionError("not used")

    def fail_claimed_draft(self, attempt_id, command):
        raise AssertionError("not used")

    def create_draft(self, draft_id, request_id, command, attachment_id):
        self.create_calls.append((draft_id, request_id, command, attachment_id))
        for current in self.drafts.values():
            if (
                current.owner_id == command.owner_id
                and current.batch_request_id == command.client_request_id
                and current.attachment_id == attachment_id
            ):
                return current
        draft = CandidateDraft(
            draft_id, command.owner_id, command.position_id, attachment_id,
            command.client_request_id, request_id, "pending", {}, (), None,
            1, NOW, NOW,
        )
        self.drafts[draft_id] = draft
        return draft

    def draft_for_owner(self, owner_id, draft_id):
        draft = self.drafts[draft_id]
        if draft.owner_id != owner_id:
            raise AssertionError("owner leak")
        return draft

    def retry_draft(self, command):
        self.retry_calls.append(command)
        current = self.draft_for_owner(command.owner_id, command.draft_id)
        updated = replace(
            current, state="pending", error_code=None,
            row_version=current.row_version + 1,
        )
        self.drafts[current.draft_id] = updated
        return updated

    def confirm_draft(self, command, *, document_id, position_candidate_id, context_version_id):
        self.confirm_calls.append(
            (command, document_id, position_candidate_id, context_version_id)
        )
        draft = self.drafts[command.draft_id]
        if draft.identity_candidates and command.merge_candidate_id is None:
            raise CandidateIdentityConflict()
        if (
            command.merge_candidate_id is not None
            and command.merge_candidate_id not in draft.identity_candidates
        ):
            raise CandidateIdentityConflict()
        candidate_id = command.merge_candidate_id or command.candidate_id
        candidate = Candidate(
            candidate_id, command.owner_id, command.stable_name,
            command.confirmed_facts, NOW, NOW,
        )
        document = CandidateDocument(
            document_id, command.owner_id, candidate_id,
            self.drafts[command.draft_id].attachment_id, command.draft_id,
            "resume", 1, "a" * 64, "active", NOW,
        )
        relation = PositionCandidate(
            position_candidate_id, command.owner_id,
            self.drafts[command.draft_id].position_id, candidate_id,
            context_version_id, command.draft_id, "active", 1, NOW, NOW,
        )
        return ConfirmedCandidate(candidate, document, relation)

    def position_candidate_for_owner(self, owner_id, position_candidate_id):
        relation = self.relations[position_candidate_id]
        if relation.owner_id != owner_id:
            raise AssertionError("owner leak")
        return relation

    def feedback_for_position_candidate(self, owner_id, position_candidate_id):
        return self.feedback.get(position_candidate_id, ())

    def add_analysis(self, command, *, analysis_version_id, feedback_ids):
        self.analysis_calls.append((command, analysis_version_id, feedback_ids))
        relation = self.position_candidate_for_owner(
            command.owner_id, command.position_candidate_id
        )
        value = CandidateAnalysisVersion(
            analysis_version_id, command.owner_id, command.position_candidate_id,
            relation.position_id, relation.candidate_id, command.context_version_id,
            len(self.analysis_calls), command.analysis_kind, command.document_ids,
            feedback_ids, command.result, command.evidence, command.unknowns,
            command.conflicts, command.verification_questions,
            command.agent_version, command.model_version, NOW,
        )
        self.analysis_replays[(command.owner_id, command.client_request_id)] = value
        return value

    def analysis_for_request(self, command):
        return self.analysis_replays.get((command.owner_id, command.client_request_id))

    def document_for_owner(self, owner_id, document_id):
        raise AssertionError("not used")

    def append_feedback(self, command, *, feedback_id):
        self.feedback_calls.append((command, feedback_id))
        value = HumanFeedback(
            feedback_id, command.owner_id, command.position_candidate_id,
            command.analysis_version_id, command.feedback_kind,
            command.conclusion_key, command.correction, command.reason, NOW,
        )
        self.feedback.setdefault(command.position_candidate_id, []).append(value)
        return value

    def latest_analysis(self, owner_id, position_candidate_id, context_version_id, *, kind):
        relation = self.position_candidate_for_owner(owner_id, position_candidate_id)
        return CandidateAnalysisVersion(
            uuid4(), owner_id, position_candidate_id, relation.position_id,
            relation.candidate_id, context_version_id, 1, kind,
            (uuid4(),), (), {"summary": str(relation.candidate_id)},
            ({"claim": "experience"},), ("规模",), (), ("说明规模",),
            "hr-r12", "model-v1", NOW,
        )


def test_batch_creation_derives_stable_per_attachment_identities() -> None:
    repository = MemoryRepository()
    service = CandidateService(repository)
    command = CreateCandidateDraftBatch(
        uuid4(), uuid4(), (uuid4(), uuid4(), uuid4()), uuid4()
    )

    first = service.create_drafts(command)
    second = service.create_drafts(command)

    assert first == second
    assert len({draft.draft_id for draft in first}) == 3
    assert [call[0:2] for call in repository.create_calls[:3]] == [
        call[0:2] for call in repository.create_calls[3:]
    ]


def test_batch_replay_rejects_a_changed_attachment_set() -> None:
    repository = MemoryRepository()
    service = CandidateService(repository)
    command = CreateCandidateDraftBatch(
        uuid4(), uuid4(), (uuid4(), uuid4()), uuid4()
    )
    service.create_drafts(command)

    with pytest.raises(CandidateConflict):
        service.create_drafts(replace(
            command, attachment_ids=(command.attachment_ids[0], uuid4())
        ))


def test_similar_identity_requires_explicit_merge_target() -> None:
    existing_candidate_id = uuid4()
    draft = _draft(identities=(existing_candidate_id,))
    repository = MemoryRepository((draft,))
    service = CandidateService(repository)
    command = ConfirmCandidateDraft(
        draft.owner_id, draft.draft_id, uuid4(), draft.row_version,
        uuid4(), "候选人甲", draft.extracted_facts, None,
    )

    with pytest.raises(CandidateIdentityConflict):
        service.confirm_draft(command, context_version_id=uuid4())
    assert len(repository.confirm_calls) == 1

    confirmed = service.confirm_draft(
        replace(command, client_request_id=uuid4(), merge_candidate_id=existing_candidate_id),
        context_version_id=uuid4(),
    )
    assert confirmed.candidate.candidate_id == existing_candidate_id


def test_identifiers_are_namespaced_by_owner_even_for_the_same_request() -> None:
    request_id, position_id, attachment_id = uuid4(), uuid4(), uuid4()
    repository = MemoryRepository()
    service = CandidateService(repository)

    left = service.create_drafts(CreateCandidateDraftBatch(
        uuid4(), position_id, (attachment_id,), request_id
    ))[0]
    right = service.create_drafts(CreateCandidateDraftBatch(
        uuid4(), position_id, (attachment_id,), request_id
    ))[0]

    assert left.draft_id != right.draft_id


def test_analysis_replay_returns_before_mutable_feedback_is_recomputed() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    relation = _relation(owner_id, position_id, context_id)
    repository = MemoryRepository()
    repository.relations[relation.position_candidate_id] = relation
    service = CandidateService(repository)
    command = CreateCandidateAnalysis(
        owner_id, relation.position_candidate_id, context_id, (uuid4(),),
        "match", uuid4(), {"summary": "stable"}, (), (), (), (),
        "hr-r12", "model-v1",
    )
    first = service.add_analysis(command)
    repository.feedback[relation.position_candidate_id] = (HumanFeedback(
        uuid4(), owner_id, relation.position_candidate_id, first.analysis_version_id,
        "accepted", "summary", None, "reviewed", NOW,
    ),)

    replay = service.add_analysis(command)

    assert replay == first
    assert len(repository.analysis_calls) == 1


def test_retry_reuses_the_failed_draft() -> None:
    failed = _draft(state="failed")
    repository = MemoryRepository((failed,))
    service = CandidateService(repository)
    command = RetryCandidateDraft(
        failed.owner_id, failed.draft_id, uuid4(), failed.row_version
    )

    retried = service.retry_draft(command)

    assert retried.draft_id == failed.draft_id
    assert retried.attachment_id == failed.attachment_id
    assert retried.state == "pending"


def test_new_analysis_automatically_pins_applicable_feedback() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    relation = _relation(owner_id, position_id, context_id)
    repository = MemoryRepository()
    repository.relations[relation.position_candidate_id] = relation
    feedback = HumanFeedback(
        uuid4(), owner_id, relation.position_candidate_id, uuid4(),
        "correction", "scope", "量产 100 万台", "HR 核实", NOW,
    )
    repository.feedback[relation.position_candidate_id] = (feedback,)
    service = CandidateService(repository)
    command = CreateCandidateAnalysis(
        owner_id, relation.position_candidate_id, context_id, (uuid4(),),
        "match", uuid4(), {"summary": "待复核"}, (), ("量产经验",), (),
        ("说明量产规模",), "hr-r12", "model-v1",
    )

    analysis = service.add_analysis(command)

    assert analysis.feedback_ids == (feedback.feedback_id,)
    assert repository.analysis_calls[-1][2] == (feedback.feedback_id,)


def test_comparison_requires_same_position_context_and_builds_evidence_view() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    left = _relation(owner_id, position_id, context_id)
    right = _relation(owner_id, position_id, context_id)
    repository = MemoryRepository()
    repository.relations = {
        left.position_candidate_id: left,
        right.position_candidate_id: right,
    }
    service = CandidateService(repository)
    command = ComparePositionCandidates(
        owner_id, position_id,
        (left.position_candidate_id, right.position_candidate_id),
        context_id, uuid4(), "hr-r12", "model-v1",
    )

    comparison = service.compare(command)

    assert comparison.analysis_kind == "comparison"
    assert comparison.context_version_id == context_id
    assert len(comparison.result["candidates"]) == 2
    assert comparison.result["ranking"] is None
    assert comparison.unknowns == ("规模",)

    repository.relations[right.position_candidate_id] = replace(
        right, context_version_id=uuid4()
    )
    with pytest.raises(CandidateScopeViolation):
        service.compare(replace(command, client_request_id=uuid4()))


def test_feedback_remains_a_separate_append_only_record() -> None:
    repository = MemoryRepository()
    service = CandidateService(repository)
    command = AppendHumanFeedback(
        uuid4(), uuid4(), uuid4(), "correction", "leadership",
        "带过 6 人团队", "HR 电话确认", uuid4(),
    )

    feedback = service.append_feedback(command)

    assert feedback.correction == "带过 6 人团队"
    assert repository.feedback_calls[-1][0] is command


def test_reanalysis_creates_a_new_version_and_preserves_the_old_ai_output() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    relation = _relation(owner_id, position_id, context_id)
    repository = MemoryRepository()
    repository.relations[relation.position_candidate_id] = relation
    service = CandidateService(repository)
    first_command = CreateCandidateAnalysis(
        owner_id, relation.position_candidate_id, context_id, (uuid4(),),
        "match", uuid4(), {"summary": "待验证"}, (), ("团队规模",), (),
        ("团队有多大",), "hr-r12", "model-v1",
    )
    first = service.add_analysis(first_command)
    correction = service.append_feedback(AppendHumanFeedback(
        owner_id, relation.position_candidate_id, first.analysis_version_id,
        "correction", "team_scale", "带过 6 人团队", "HR 电话确认", uuid4(),
    ))

    second = service.add_analysis(replace(
        first_command,
        client_request_id=uuid4(),
        result={"summary": "反馈已作为独立输入"},
        unknowns=(),
    ))

    assert second.analysis_version_id != first.analysis_version_id
    assert second.version_number > first.version_number
    assert first.result == {"summary": "待验证"}
    assert first.feedback_ids == ()
    assert second.feedback_ids == (correction.feedback_id,)
