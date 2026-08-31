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
async def test_corporate_human_semantic_review_uses_authenticated_identity():
    class Repository:
        def review_replay(self, replay_id, **kwargs):
            assert replay_id == REPLAY_ID
            assert kwargs["reviewer"] == "corp:alice"
            assert kwargs["actor"] == "corp:alice"
            return {"issue_id": ISSUE_ID}

        def recalculate_and_record_transition(self, *_args, **_kwargs):
            return None

        def get_issue_detail(self, _issue_id):
            return {"issue": {"id": ISSUE_ID}}

    repository = Repository()
    payload = SimpleNamespace(
        verdict="passed", method="human_fae", reviewer="corp:alice", reason="review"
    )

    result = await ReviewService(repository, write_repository=repository).semantic_review(
        REPLAY_ID, payload, actor="corp:alice"
    )

    assert result["issue"]["id"] == ISSUE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor", "method", "reviewer"),
    [
        ("fae:alice", "codex", "codex"),
        ("codex", "human_fae", "fae:alice"),
        ("fae:alice", "human_fae", "fae:bob"),
        ("corp:alice", "human_fae", "corp:bob"),
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
        def get_evidence_owner(self, evidence_id):
            assert evidence_id == EVIDENCE_ID
            return {"issue_id": ISSUE_ID}

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
        def get_evidence_owner(self, _evidence_id):
            return None

        def get_replay(self, _replay_id):
            return None

    service = ReviewService(ReadRepository(), write_repository=None)

    with pytest.raises(ReviewNotFound, match=message):
        await getattr(service, operation)(entity_id)


@pytest.mark.asyncio
async def test_relocation_replay_preflights_use_read_repository():
    class ReadRepository:
        def move_link_has_replay(self, issue_id, link_id):
            assert (issue_id, link_id) == (ISSUE_ID, EVIDENCE_ID)
            return True

        def merge_relocation_has_replay(self, source_id, target_id):
            assert (source_id, target_id) == (ISSUE_ID, REPLAY_ID)
            return False

    repository = ReadRepository()
    service = ReviewService(repository, write_repository=repository)

    assert await service.move_link_has_replay(ISSUE_ID, EVIDENCE_ID) is True
    assert await service.merge_relocation_has_replay(ISSUE_ID, REPLAY_ID) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["move_link_has_replay", "merge_relocation_has_replay"])
async def test_relocation_preflights_preserve_cloud_read_only_denial(operation):
    class ReadRepository:
        def __getattr__(self, _name):
            raise AssertionError("read-only mutation must fail before preflight read")

    service = ReviewService(ReadRepository(), write_repository=None)
    arguments = (
        (ISSUE_ID, EVIDENCE_ID)
        if operation == "move_link_has_replay"
        else (ISSUE_ID, REPLAY_ID)
    )

    with pytest.raises(ReviewUnavailable, match="read-only"):
        await getattr(service, operation)(*arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provided",
    [["missing"], ["fb:other-turn"], ["fb:other-agent"]],
)
async def test_link_rejects_unowned_feedback_before_writer(provided):
    class ReadRepository:
        def feedback_keys_for_turn(self, agent_id, turn_key):
            assert (agent_id, turn_key) == ("ai-fae-agent", "fae:turn")
            return {"turn_key": turn_key, "feedback_keys": ["fb:real"]}

    class Writer:
        def link_turn(self, *_args, **_kwargs):
            raise AssertionError("invalid feedback must not reach writer")

    payload = SimpleNamespace(
        agent_id="ai-fae-agent",
        source_turn_key="fae:turn",
        source_feedback_keys=provided,
        link_role="primary",
        reason="link",
    )

    with pytest.raises(InvalidReviewMutation, match="feedback"):
        await ReviewService(ReadRepository(), write_repository=Writer()).link_turn(
            ISSUE_ID, payload, actor="corp:owner"
        )


@pytest.mark.asyncio
async def test_link_derives_exact_feedback_lineage_and_deduplicates_caller_keys():
    class Repository:
        written = None

        def feedback_keys_for_turn(self, _agent_id, turn_key):
            return {"turn_key": turn_key, "feedback_keys": ["fb:2", "fb:1"]}

        def link_turn(self, _issue_id, **kwargs):
            self.written = kwargs["source_feedback_keys"]

        def recalculate_and_record_transition(self, *_args, **_kwargs): return None
        def get_issue_detail(self, _issue_id): return {"issue": {"id": ISSUE_ID}}

    repository = Repository()
    payload = SimpleNamespace(
        agent_id="ai-fae-agent",
        source_turn_key="fae:turn",
        source_feedback_keys=["fb:2", "fb:2"],
        link_role="primary",
        reason="link",
    )

    await ReviewService(repository, write_repository=repository).link_turn(
        ISSUE_ID, payload, actor="corp:owner"
    )

    assert repository.written == ["fb:1", "fb:2"]


@pytest.mark.asyncio
async def test_empty_feedback_lineage_remains_valid_for_an_ordinary_turn():
    class Repository:
        written = None

        def feedback_keys_for_turn(self, _agent_id, turn_key):
            return {"turn_key": turn_key, "feedback_keys": []}

        def link_turn(self, _issue_id, **kwargs): self.written = kwargs["source_feedback_keys"]
        def recalculate_and_record_transition(self, *_args, **_kwargs): return None
        def get_issue_detail(self, _issue_id): return {"issue": {"id": ISSUE_ID}}

    repository = Repository()
    payload = SimpleNamespace(
        agent_id="ai-fae-agent", source_turn_key="fae:ordinary",
        source_feedback_keys=[], link_role="primary", reason="link",
    )

    await ReviewService(repository, write_repository=repository).link_turn(
        ISSUE_ID, payload, actor="corp:owner"
    )

    assert repository.written == []
