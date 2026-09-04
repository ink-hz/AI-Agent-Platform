from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.hr.candidate_models import (
    AttachCandidateDraftExecution,
    CandidateDraftProcessingAttempt,
    ClaimNextCandidateDraft,
    CompleteCandidateDraft,
    FailCandidateDraft,
)
from app.hr.candidate_parser_queue import CandidateParserQueue

NOW = datetime.now(UTC)


def _attempt(command: ClaimNextCandidateDraft) -> CandidateDraftProcessingAttempt:
    return CandidateDraftProcessingAttempt(
        command.attempt_id, uuid4(), uuid4(), uuid4(), uuid4(), uuid4(),
        command.worker_id, None, None, None, "processing", 1, 2,
        NOW, NOW, None, None, None,
    )


class QueueRepository:
    def __init__(self) -> None:
        self.calls = []

    def claim_next_draft(self, command):
        self.calls.append(("claim", command))
        return _attempt(command)

    def attach_draft_execution(self, command):
        self.calls.append(("attach", command))
        current = _attempt(ClaimNextCandidateDraft(
            command.attempt_id, command.worker_id
        ))
        return CandidateDraftProcessingAttempt(
            current.attempt_id, current.owner_id, current.draft_id,
            current.position_id, current.attachment_id,
            current.draft_client_request_id, current.worker_id,
            command.execution_job_id, command.conversation_id, command.turn_id,
            current.state, current.starting_row_version,
            current.claimed_row_version, current.claimed_at,
            current.lease_expires_at, NOW, None, None,
        )

    def recover_draft_attempt(self, attempt_id, worker_id):
        self.calls.append(("recover", attempt_id, worker_id))
        return _attempt(ClaimNextCandidateDraft(attempt_id, worker_id))

    def complete_claimed_draft(self, attempt_id, command):
        self.calls.append(("complete", attempt_id, command))
        return object()

    def fail_claimed_draft(self, attempt_id, command):
        self.calls.append(("fail", attempt_id, command))
        return object()


def test_parser_queue_exposes_database_backed_two_phase_worker_contract() -> None:
    repository = QueueRepository()
    queue = CandidateParserQueue(repository)
    claim = ClaimNextCandidateDraft(uuid4(), "candidate-parser-1")
    attempt = queue.claim_next(claim)
    attach = AttachCandidateDraftExecution(
        attempt.attempt_id, attempt.worker_id, uuid4(), uuid4(), uuid4()
    )

    bound = queue.attach_execution(attach)
    recovered = CandidateParserQueue(repository).recover_attempt(
        attempt.attempt_id, attempt.worker_id
    )

    assert bound.execution_job_id == attach.execution_job_id
    assert recovered.attempt_id == attempt.attempt_id
    assert [call[0] for call in repository.calls] == ["claim", "attach", "recover"]


def test_parser_queue_complete_and_fail_are_attempt_scoped() -> None:
    repository = QueueRepository()
    queue = CandidateParserQueue(repository)
    attempt_id = uuid4()
    complete = CompleteCandidateDraft(
        uuid4(), uuid4(), uuid4(), 2, {"stable_name": "Candidate"}
    )
    failure = FailCandidateDraft(uuid4(), uuid4(), uuid4(), 2, "parse_failed")

    queue.complete(attempt_id, complete)
    queue.fail(attempt_id, failure)

    assert repository.calls == [
        ("complete", attempt_id, complete), ("fail", attempt_id, failure)
    ]
