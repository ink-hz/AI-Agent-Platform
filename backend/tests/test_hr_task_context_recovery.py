from __future__ import annotations

import json
from dataclasses import replace
from uuid import uuid4

from app.agent_brain.conversation_context import ContextMessage, ConversationContext
from app.agent_brain.models import load_capability_cards
from app.agent_brain.orchestrator import build_direct_prompt
from app.hr.position_intelligence_models import HrPositionContextEnvelope
from app.hr.task_context import canonical_hash


def test_rebuilt_direct_relay_prompt_reuses_one_pinned_hr_envelope() -> None:
    envelope = HrPositionContextEnvelope(
        uuid4(), uuid4(), uuid4(), "talent_profile", (uuid4(),), None,
        None, (), (), "Pinned HR position facts", "a" * 64,
    )
    envelope = replace(envelope, canonical_sha256=canonical_hash(envelope))
    context = ConversationContext(
        summary="Prior conversation",
        messages=(ContextMessage("user", "生成人才画像"),),
        estimated_utf8_bytes=64,
        hr_position_context=envelope,
    )
    card = next(card for card in load_capability_cards() if card.agent_id == "hr-bot")

    first = build_direct_prompt(context, card)
    recovered = build_direct_prompt(context, card)
    first_payload = json.loads(first.split("\n", 1)[1])
    recovered_payload = json.loads(recovered.split("\n", 1)[1])

    assert first == recovered
    assert first_payload["hr_position_context"] == recovered_payload[
        "hr_position_context"
    ]
    assert first_payload["hr_position_context"]["canonical_sha256"] == (
        envelope.canonical_sha256
    )
    assert first.count('"hr_position_context"') == 1
