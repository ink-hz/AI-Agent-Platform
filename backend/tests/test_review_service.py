from types import SimpleNamespace
from uuid import UUID

import pytest

from app.review.repository import InvalidReviewMutation, ReviewNotFound
from app.review.service import ReviewService, ReviewUnavailable


ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")
REPLAY_ID = UUID("00000000-0000-0000-0000-000000000002")
EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000003")


@pytest.mark.asyncio
async def test_semantic_review_passes_review_method_to_repository():
    class Repository:
        def __init__(self):
            self.review_method = None

        def review_replay(self, replay_id, *, method, **_kwargs):
            assert replay_id == REPLAY_ID
            self.review_method = method
            return {"issue_id": ISSUE_ID}

        def recalculate_and_record_transition(self, *_args, **_kwargs):
            return None

        def get_issue_detail(self, issue_id):
            assert issue_id == ISSUE_ID
            return {"issue": {"id": ISSUE_ID}}

    repository = Repository()
    service = ReviewService(repository, write_repository=repository)
    payload = SimpleNamespace(
        verdict="passed",
        method="codex",
        reviewer="codex",
        reason="independent semantic review passed",
    )

    detail = await service.semantic_review(REPLAY_ID, payload, actor="codex")

    assert repository.review_method == "codex"
    assert detail["issue"]["id"] == ISSUE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor", "method", "reviewer"),
    [
        ("fae:alice", "codex", "codex"),
        ("codex", "human_fae", "fae:alice"),
        ("fae:alice", "human_fae", "fae:bob"),
        ("corp:alice", "human_fae", "corp:alice"),
    ],
)
async def test_semantic_review_identity_cannot_be_spoofed(
    actor, method, reviewer
):
    class Repository:
        def review_replay(self, *_args, **_kwargs):
            raise AssertionError("spoofed review must not reach repository")

    service = ReviewService(Repository(), write_repository=Repository())
    payload = SimpleNamespace(
        verdict="passed",
        method=method,
        reviewer=reviewer,
        reason="claimed independent review",
    )

    with pytest.raises(InvalidReviewMutation, match="review identity"):
        await service.semantic_review(REPLAY_ID, payload, actor=actor)


@pytest.mark.asyncio
async def test_read_methods_survive_missing_writer():
    class ReadRepository:
        def overview(self, *, agent_id=None):
            return {"negative_turns": 7}

    service = ReviewService(ReadRepository(), write_repository=None)

    result = await service.overview()

    assert result == {"negative_turns": 7, "write_available": False}


@pytest.mark.asyncio
async def test_mutation_fails_explicitly_when_writer_is_missing():
    class ReadRepository:
        def overview(self):
            return {"negative_turns": 7}

    class Payload:
        reason = "create from negative feedback"

        @staticmethod
        def model_dump(**_kwargs):
            return {
                "agent_id": "ai-fae-agent",
                "title": "missing evidence",
            }

    service = ReviewService(ReadRepository(), write_repository=None)

    with pytest.raises(ReviewUnavailable, match="read-only"):
        await service.create_issue(Payload(), actor="codex")


@pytest.mark.asyncio
async def test_read_only_owner_lookups_return_only_owning_issue_id():
    class ReadRepository:
        def get_evidence(self, evidence_id):
            assert evidence_id == EVIDENCE_ID
            return {"id": EVIDENCE_ID, "issue_id": ISSUE_ID, "reference": "secret"}

        def get_replay(self, replay_id):
            assert replay_id == REPLAY_ID
            return {"id": REPLAY_ID, "issue_id": ISSUE_ID, "answer": "secret"}

    service = ReviewService(ReadRepository(), write_repository=None)

    assert await service.evidence_issue_id(EVIDENCE_ID) == ISSUE_ID
    assert await service.replay_issue_id(REPLAY_ID) == ISSUE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "entity_id", "message"),
    [
        ("evidence_issue_id", EVIDENCE_ID, "evidence not found"),
        ("replay_issue_id", REPLAY_ID, "replay not found"),
    ],
)
async def test_owner_lookups_preserve_not_found_semantics(
    operation, entity_id, message
):
    class ReadRepository:
        def get_evidence(self, _evidence_id):
            return None

        def get_replay(self, _replay_id):
            return None

    service = ReviewService(ReadRepository(), write_repository=None)

    with pytest.raises(ReviewNotFound, match=message):
        await getattr(service, operation)(entity_id)
