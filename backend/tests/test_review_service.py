from types import SimpleNamespace
from uuid import UUID

import pytest

from app.review.service import ReviewService, ReviewUnavailable


ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")
REPLAY_ID = UUID("00000000-0000-0000-0000-000000000002")


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
