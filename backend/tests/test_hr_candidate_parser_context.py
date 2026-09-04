from __future__ import annotations

from uuid import UUID

import pytest

from app.hr.candidate_parser_runtime import CandidateParserInputProvider


def test_candidate_parser_provider_returns_only_repository_verified_attachment() -> None:
    calls = []

    class Repository:
        def candidate_parser_input_for_turn(self, owner_id, conversation_id, turn_id):
            calls.append((owner_id, conversation_id, turn_id))
            return UUID(int=4)

    provider = CandidateParserInputProvider(Repository())

    assert provider.for_turn(UUID(int=1), UUID(int=2), UUID(int=3)) == UUID(int=4)
    assert calls == [(UUID(int=1), UUID(int=2), UUID(int=3))]


def test_candidate_parser_provider_rejects_invalid_repository_projection() -> None:
    class Repository:
        def candidate_parser_input_for_turn(self, *_args):
            return "wrong attachment"

    with pytest.raises(ValueError):
        CandidateParserInputProvider(Repository()).for_turn(
            UUID(int=1), UUID(int=2), UUID(int=3)
        )
