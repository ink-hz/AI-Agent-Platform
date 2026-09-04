from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.candidate_context import CandidateEnvelopeProvider
from app.hr.candidate_models import (
    Candidate,
    CandidateDocument,
    HumanFeedback,
    PositionCandidate,
)
from app.hr.candidate_service import CandidateScopeViolation

NOW = datetime.now(UTC)


class ContextRepository:
    def __init__(self):
        self.owner_id = uuid4()
        self.position_id = uuid4()
        self.context_id = uuid4()
        self.candidate = Candidate(
            uuid4(), self.owner_id, "候选人甲",
            {"skills": ["Python"], "projects": ["视觉系统"]}, NOW, NOW,
        )
        self.relation = PositionCandidate(
            uuid4(), self.owner_id, self.position_id,
            self.candidate.candidate_id, self.context_id, uuid4(),
            "active", 1, NOW, NOW,
        )
        self.resume = CandidateDocument(
            uuid4(), self.owner_id, self.candidate.candidate_id, uuid4(),
            self.relation.source_draft_id, "resume", 1, "a" * 64,
            "active", NOW,
        )
        self.feedback = HumanFeedback(
            uuid4(), self.owner_id, self.relation.position_candidate_id,
            uuid4(), "correction", "project_scale", "量产 100 万台",
            "HR 电话确认", NOW,
        )
        self.documents = [self.resume]
        self.document_access = {self.resume.document_id: "ready"}

    def position_candidate_for_owner(self, owner_id, position_candidate_id):
        assert owner_id == self.owner_id
        if position_candidate_id != self.relation.position_candidate_id:
            raise CandidateScopeViolation()
        return self.relation

    def candidate_for_owner(self, owner_id, candidate_id):
        assert owner_id == self.owner_id
        if candidate_id != self.candidate.candidate_id:
            raise CandidateScopeViolation()
        return self.candidate

    def documents_for_candidate(self, owner_id, candidate_id):
        assert owner_id == self.owner_id
        assert candidate_id == self.candidate.candidate_id
        return tuple(self.documents)

    def attachment_state_for_document(self, owner_id, document_id):
        assert owner_id == self.owner_id
        return self.document_access[document_id]

    def feedback_for_position_candidate(self, owner_id, position_candidate_id):
        assert owner_id == self.owner_id
        assert position_candidate_id == self.relation.position_candidate_id
        return (self.feedback,)


def _provider(repository, *, confirmed=True):
    calls = []

    def context_is_confirmed(owner_id, position_id, context_version_id):
        calls.append((owner_id, position_id, context_version_id))
        return confirmed

    return CandidateEnvelopeProvider(repository, context_is_confirmed), calls


def test_fragment_contains_only_exact_documents_and_feedback() -> None:
    repository = ContextRepository()
    other_position_resume = uuid4()
    provider, calls = _provider(repository)

    fragment = provider.for_task(
        repository.owner_id, repository.position_id,
        repository.candidate.candidate_id,
        repository.relation.position_candidate_id,
    )

    assert fragment.document_ids == (repository.resume.document_id,)
    assert fragment.document_attachment_ids == (repository.resume.attachment_id,)
    assert fragment.human_feedback_ids == (repository.feedback.feedback_id,)
    assert other_position_resume not in fragment.document_attachment_ids
    assert calls == [
        (repository.owner_id, repository.position_id, repository.context_id)
    ]


def test_fragment_separates_confirmed_candidate_facts_from_human_feedback() -> None:
    repository = ContextRepository()
    provider, _ = _provider(repository)

    fragment = provider.for_task(
        repository.owner_id, repository.position_id,
        repository.candidate.candidate_id,
        repository.relation.position_candidate_id,
    )

    assert "CONFIRMED_CANDIDATE_FACTS" in fragment.prompt_context
    assert "HUMAN_FEEDBACK_DO_NOT_REWRITE_AS_AI_FACT" in fragment.prompt_context
    assert "量产 100 万台" in fragment.prompt_context
    assert str(repository.resume.document_id) in fragment.prompt_context
    assert "storage" not in fragment.prompt_context.lower()


@pytest.mark.parametrize("mismatch", ("position", "candidate", "archived"))
def test_provider_rejects_cross_scope_or_archived_relations(mismatch) -> None:
    repository = ContextRepository()
    provider, _ = _provider(repository)
    position_id = repository.position_id
    candidate_id = repository.candidate.candidate_id
    if mismatch == "position":
        position_id = uuid4()
    elif mismatch == "candidate":
        candidate_id = uuid4()
    else:
        repository.relation = replace(repository.relation, status="archived")

    with pytest.raises(CandidateScopeViolation):
        provider.for_task(
            repository.owner_id, position_id, candidate_id,
            repository.relation.position_candidate_id,
        )


@pytest.mark.parametrize("document_state", ("erased", "expired", "quarantined"))
def test_provider_rejects_erased_or_unavailable_active_documents(document_state) -> None:
    repository = ContextRepository()
    if document_state == "erased":
        repository.documents[0] = replace(repository.resume, status="erased")
    else:
        repository.document_access[repository.resume.document_id] = document_state
    provider, _ = _provider(repository)

    with pytest.raises(CandidateScopeViolation):
        provider.for_task(
            repository.owner_id, repository.position_id,
            repository.candidate.candidate_id,
            repository.relation.position_candidate_id,
        )


def test_provider_rejects_missing_confirmed_position_context_and_partial_ids() -> None:
    repository = ContextRepository()
    provider, _ = _provider(repository, confirmed=False)

    with pytest.raises(CandidateScopeViolation):
        provider.for_task(
            repository.owner_id, repository.position_id,
            repository.candidate.candidate_id,
            repository.relation.position_candidate_id,
        )
    confirmed, _ = _provider(repository)
    with pytest.raises(CandidateScopeViolation):
        confirmed.for_task(repository.owner_id, repository.position_id, None, None)
