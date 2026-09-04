from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.hr.candidate_models import CandidateDraftProcessingAttempt
from app.hr.candidate_parser_runtime import (
    CandidateParserExecutionResult,
    CandidateParserProtocolError,
    CandidateParserRuntime,
    CandidateParserSubmission,
    CandidateParserSubmissionCoordinator,
    candidate_parser_submission_loop,
    decode_candidate_parser_response,
)
from app.hr.candidate_repository import CandidateNotFound, CandidateUnavailable


def _attempt(**changes) -> CandidateDraftProcessingAttempt:
    now = datetime.now(timezone.utc)
    value = CandidateDraftProcessingAttempt(
        attempt_id=UUID(int=1),
        owner_id=UUID(int=2),
        draft_id=UUID(int=3),
        position_id=UUID(int=4),
        attachment_id=UUID(int=5),
        draft_client_request_id=UUID(int=6),
        worker_id="candidate-parser.primary",
        execution_job_id=None,
        conversation_id=None,
        turn_id=None,
        state="processing",
        starting_row_version=1,
        claimed_row_version=2,
        claimed_at=now,
        lease_expires_at=now + timedelta(minutes=10),
        execution_attached_at=None,
        finished_at=None,
        terminal_request_id=None,
    )
    return replace(value, **changes)


def test_submission_coordinator_starts_exact_unbound_hr_conversation() -> None:
    attempt = _attempt()
    selected = CandidateParserSubmission.from_attempt(attempt)

    class Repository:
        def next_submission(self):
            return selected

    class Commands:
        def __init__(self):
            self.calls = []

        def start(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return object()

    commands = Commands()
    coordinator = CandidateParserSubmissionCoordinator(Repository(), commands)

    assert coordinator.submit_one() is True
    assert commands.calls[0][0][:2] == (
        attempt.owner_id,
        attempt.attempt_id,
    )
    prompt = commands.calls[0][0][2]
    assert "extracted_facts" in prompt
    assert "identity_candidate_ids" in prompt
    assert "必须始终为空数组" in prompt
    assert str(attempt.attachment_id) not in prompt
    assert commands.calls[0][1] == {
        "mode": "direct_agent",
        "direct_agent_id": "hr-bot",
    }

    retried = CandidateParserSubmission.from_attempt(
        replace(attempt, attempt_id=UUID(int=7))
    )
    assert retried.client_request_id != selected.client_request_id
    assert retried.draft_client_request_id == selected.draft_client_request_id


def test_submission_response_loss_replays_same_request_without_side_effects() -> None:
    selected = CandidateParserSubmission.from_attempt(_attempt())

    class Repository:
        def next_submission(self):
            return selected

    class Commands:
        def __init__(self):
            self.calls = []
            self.persisted = set()

        def start(self, owner, request_id, prompt, **kwargs):
            self.calls.append((owner, request_id, prompt, kwargs))
            duplicate = (owner, request_id) in self.persisted
            self.persisted.add((owner, request_id))
            if not duplicate:
                raise TimeoutError("response lost after commit")
            return object()

    commands = Commands()
    coordinator = CandidateParserSubmissionCoordinator(Repository(), commands)

    with pytest.raises(TimeoutError):
        coordinator.submit_one()
    assert coordinator.submit_one() is True
    assert commands.calls[0] == commands.calls[1]
    assert len(commands.persisted) == 1


def test_submission_collision_is_failed_and_does_not_call_conversation_service() -> None:
    selected = replace(
        CandidateParserSubmission.from_attempt(_attempt()),
        request_collision=True,
    )

    class Repository:
        def __init__(self):
            self.failed = []

        def next_submission(self):
            return selected

        def fail_submission_collision(self, submission):
            self.failed.append(submission)

    class Commands:
        def start(self, *_args, **_kwargs):
            raise AssertionError("collision must never create or replay a conversation")

    repository = Repository()
    coordinator = CandidateParserSubmissionCoordinator(repository, Commands())

    assert coordinator.submit_one() is True
    assert repository.failed == [selected]


def test_submission_loop_retries_transient_failures() -> None:
    selected = CandidateParserSubmission.from_attempt(_attempt())

    class Repository:
        def next_submission(self):
            return selected

    class Commands:
        def __init__(self):
            self.calls = 0
            self.completed = asyncio.Event()

        def start(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise CandidateUnavailable("temporary app failure")
            self.completed.set()

    async def scenario():
        commands = Commands()
        coordinator = CandidateParserSubmissionCoordinator(Repository(), commands)
        task = asyncio.create_task(candidate_parser_submission_loop(
            coordinator, idle_seconds=0.001
        ))
        await asyncio.wait_for(commands.completed.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert commands.calls >= 2

    asyncio.run(scenario())


def test_decoder_accepts_only_the_exact_candidate_parser_contract() -> None:
    parsed = decode_candidate_parser_response(
        '{"extracted_facts":{"stable_name":"Lin","skills":["Python"]},'
        '"identity_candidate_ids":[]}'
    )

    assert parsed.extracted_facts == {
        "stable_name": "Lin",
        "skills": ["Python"],
    }
    assert parsed.identity_candidate_ids == ()
    assert decode_candidate_parser_response(
        '\n {"extracted_facts":{},"identity_candidate_ids":[]} \n'
    ).extracted_facts == {}

    for invalid in (
        "```json\n{}\n```",
        '{"extracted_facts":{},"identity_candidate_ids":[],"extra":true}',
        '{"extracted_facts":{},"identity_candidate_ids":"no"}',
        (
            '{"extracted_facts":{},"identity_candidate_ids":'
            '["00000000-0000-0000-0000-000000000009"]}'
        ),
        (
            '{"extracted_facts":{"offer_status":"pending"},'
            '"identity_candidate_ids":[]}'
        ),
    ):
        with pytest.raises(CandidateParserProtocolError):
            decode_candidate_parser_response(invalid)


class _Queue:
    def __init__(self, *, recovered=None, claimed=None, discovered=None):
        self.recovered = recovered
        self.claimed = claimed
        self.discovered = discovered
        self.calls = []
        self.completed = []
        self.failed = []

    def recover_next(self, worker_id):
        self.calls.append(("recover", worker_id))
        if self.recovered is None:
            raise CandidateNotFound()
        return self.recovered

    def claim_next(self, command):
        self.calls.append(("claim", command))
        if self.claimed is None:
            raise CandidateNotFound()
        return self.claimed

    def discover_execution(self, attempt_id, worker_id):
        self.calls.append(("discover", attempt_id, worker_id))
        if self.discovered is None:
            raise CandidateNotFound()
        return self.discovered

    def attach_execution(self, command):
        self.calls.append(("attach", command))
        return object()

    def complete(self, attempt_id, worker_id, command):
        self.completed.append((attempt_id, worker_id, command))
        return object()

    def fail(self, attempt_id, worker_id, command):
        self.failed.append((attempt_id, worker_id, command))
        return object()


class _Reader:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def read(self, attempt_id, worker_id):
        self.calls.append((attempt_id, worker_id))
        return self.result


def _execution_identity(attempt):
    from app.hr.candidate_models import AttachCandidateDraftExecution

    return AttachCandidateDraftExecution(
        attempt.attempt_id,
        attempt.worker_id,
        UUID(int=20),
        UUID(int=21),
        UUID(int=22),
    )


def test_runtime_recovers_before_claim_and_waits_for_nonterminal_execution() -> None:
    attempt = _attempt()
    queue = _Queue(recovered=attempt)
    runtime = CandidateParserRuntime(queue, _Reader(None), worker_id=attempt.worker_id)

    assert runtime.tick() is False
    assert [call[0] for call in queue.calls] == ["recover", "discover"]


def test_runtime_completes_valid_terminal_result_with_stable_request_id() -> None:
    attempt = _attempt()
    queue = _Queue(recovered=attempt, discovered=_execution_identity(attempt))
    reader = _Reader(CandidateParserExecutionResult(
        execution_status="completed",
        turn_status="completed",
        assistant_content=(
            '{"extracted_facts":{"stable_name":"Lin"},'
            '"identity_candidate_ids":[]}'
        ),
    ))
    runtime = CandidateParserRuntime(queue, reader, worker_id=attempt.worker_id)

    assert runtime.tick() is True
    assert len(queue.completed) == 1
    command = queue.completed[0][2]
    assert command.owner_id == attempt.owner_id
    assert command.draft_id == attempt.draft_id
    assert command.expected_row_version == attempt.claimed_row_version
    assert command.extracted_facts == {"stable_name": "Lin"}
    assert command.client_request_id == runtime.terminal_request_id(attempt.attempt_id)
    assert queue.failed == []


def test_runtime_uses_persisted_execution_pin_before_discovery() -> None:
    attempt = _attempt(
        execution_job_id=UUID(int=20),
        conversation_id=UUID(int=21),
        turn_id=UUID(int=22),
        assistant_message_id=UUID(int=23),
        execution_attached_at=datetime.now(timezone.utc),
    )
    queue = _Queue(recovered=attempt)

    def reject_discovery(*_args):
        raise AssertionError("a persisted execution pin must win over later matches")

    queue.discover_execution = reject_discovery
    runtime = CandidateParserRuntime(
        queue,
        _Reader(CandidateParserExecutionResult(
            "completed",
            "completed",
            '{"extracted_facts":{"stable_name":"Pinned"},'
            '"identity_candidate_ids":[]}',
        )),
        worker_id=attempt.worker_id,
    )

    assert runtime.tick() is True
    assert queue.completed[0][2].extracted_facts == {"stable_name": "Pinned"}


@pytest.mark.parametrize(
    ("result", "error_code"),
    [
        (
            CandidateParserExecutionResult(
                execution_status="completed",
                turn_status="completed",
                assistant_content="not-json",
            ),
            "parser_response_invalid",
        ),
        (
            CandidateParserExecutionResult(
                execution_status="failed",
                turn_status="failed",
                assistant_content=None,
            ),
            "execution_failed",
        ),
    ],
)
def test_runtime_terminal_failure_never_fabricates_candidate(result, error_code) -> None:
    attempt = _attempt()
    queue = _Queue(recovered=attempt, discovered=_execution_identity(attempt))
    runtime = CandidateParserRuntime(queue, _Reader(result), worker_id=attempt.worker_id)

    assert runtime.tick() is True
    assert queue.completed == []
    assert queue.failed[0][2].error_code == error_code


def test_runtime_propagates_indeterminate_terminal_response_loss() -> None:
    attempt = _attempt()
    queue = _Queue(recovered=attempt, discovered=_execution_identity(attempt))

    def lose_response(*_args):
        raise CandidateUnavailable("response lost after commit")

    queue.complete = lose_response
    runtime = CandidateParserRuntime(
        queue,
        _Reader(CandidateParserExecutionResult(
            "completed",
            "completed",
            '{"extracted_facts":{"stable_name":"Lin"},'
            '"identity_candidate_ids":[]}',
        )),
        worker_id=attempt.worker_id,
    )

    with pytest.raises(CandidateUnavailable):
        runtime.tick()
