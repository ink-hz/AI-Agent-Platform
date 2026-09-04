from __future__ import annotations

from uuid import UUID

from .candidate_models import (
    AttachCandidateDraftExecution,
    CandidateDraft,
    CandidateDraftProcessingAttempt,
    ClaimNextCandidateDraft,
    CompleteCandidateDraft,
    FailCandidateDraft,
)


class CandidateParserQueue:
    """Durable worker boundary; all recovery state lives in PostgreSQL."""

    def __init__(self, repository) -> None:
        required = (
            "claim_next_draft",
            "attach_draft_execution",
            "recover_draft_attempt",
            "complete_claimed_draft",
            "fail_claimed_draft",
        )
        if any(not callable(getattr(repository, name, None)) for name in required):
            raise ValueError("candidate parser queue repository required")
        self._repository = repository

    def claim_next(
        self, command: ClaimNextCandidateDraft
    ) -> CandidateDraftProcessingAttempt:
        if not isinstance(command, ClaimNextCandidateDraft):
            raise ValueError("candidate parser claim required")
        return self._repository.claim_next_draft(command)

    def attach_execution(
        self, command: AttachCandidateDraftExecution
    ) -> CandidateDraftProcessingAttempt:
        if not isinstance(command, AttachCandidateDraftExecution):
            raise ValueError("candidate parser execution identity required")
        return self._repository.attach_draft_execution(command)

    def recover_attempt(
        self, attempt_id: UUID, worker_id: str
    ) -> CandidateDraftProcessingAttempt:
        if not isinstance(attempt_id, UUID) or not isinstance(worker_id, str):
            raise ValueError("candidate parser recovery identity required")
        return self._repository.recover_draft_attempt(attempt_id, worker_id)

    def complete(
        self, attempt_id: UUID, worker_id: str, command: CompleteCandidateDraft
    ) -> CandidateDraft:
        if (
            not isinstance(attempt_id, UUID)
            or not isinstance(worker_id, str)
            or not isinstance(command, CompleteCandidateDraft)
        ):
            raise ValueError("candidate parser completion required")
        return self._repository.complete_claimed_draft(attempt_id, worker_id, command)

    def fail(
        self, attempt_id: UUID, worker_id: str, command: FailCandidateDraft
    ) -> CandidateDraft:
        if (
            not isinstance(attempt_id, UUID)
            or not isinstance(worker_id, str)
            or not isinstance(command, FailCandidateDraft)
        ):
            raise ValueError("candidate parser failure required")
        return self._repository.fail_claimed_draft(attempt_id, worker_id, command)
