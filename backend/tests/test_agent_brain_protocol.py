from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.agent_brain.protocol import (
    BrainDecision,
    BrainProtocolError,
    parse_brain_decision,
)


ALLOWED = ("hr-bot", "fae-bot")
DELEGATE = {
    "kind": "delegate",
    "answer": None,
    "agent_id": "hr-bot",
    "objective": "根据给定 JD 定义候选人能力组合与搜索方向",
    "rationale_summary": "该任务需要招聘与人才定位能力",
}
DIRECT = {
    "kind": "direct",
    "answer": "这是一个直接回答。",
    "agent_id": None,
    "objective": None,
    "rationale_summary": "不需要专业 Agent。",
}


def test_parser_accepts_only_one_exact_unfenced_final_object() -> None:
    decision = parse_brain_decision(
        json.dumps(DELEGATE, ensure_ascii=False), allowed_agent_ids=ALLOWED
    )

    assert isinstance(decision, BrainDecision)
    assert isinstance(decision, BaseModel)
    assert decision.model_dump() == DELEGATE


def test_parser_accepts_exact_direct_shape() -> None:
    assert parse_brain_decision(
        json.dumps(DIRECT, ensure_ascii=False), allowed_agent_ids=()
    ).model_dump() == DIRECT


@pytest.mark.parametrize(
    "rendered",
    (
        json.dumps({**DELEGATE, "debug": "hidden"}),
        json.dumps(DELEGATE) + "\n" + json.dumps(DIRECT),
        json.dumps(DELEGATE) + "\nexplanation",
        " " + json.dumps(DELEGATE),
        json.dumps(DELEGATE) + "\n",
        "```json\n" + json.dumps(DELEGATE) + "\n```",
        "```\n" + json.dumps(DELEGATE) + "\n```",
        "prefix\n" + json.dumps(DELEGATE),
        "```json\n" + json.dumps(DELEGATE) + "\n```\ntrailing",
        "```json\n" + json.dumps(DELEGATE) + "\n",
        json.dumps(DELEGATE)[:-1],
    ),
)
def test_parser_rejects_extra_multiple_trailing_prefixed_or_malformed_content(
    rendered: str,
) -> None:
    with pytest.raises(BrainProtocolError, match="^brain protocol invalid$"):
        parse_brain_decision(rendered, allowed_agent_ids=ALLOWED)


@pytest.mark.parametrize(
    "payload",
    (
        {**DELEGATE, "agent_id": "marketing-gtm-bot"},
        {**DELEGATE, "objective": None},
        {**DELEGATE, "objective": "  "},
        {**DIRECT, "answer": None},
        {**DIRECT, "answer": ""},
        {**DIRECT, "agent_id": "hr-bot"},
        {**DIRECT, "objective": "do work"},
        {**DELEGATE, "answer": "quiet fallback"},
    ),
)
def test_parser_rejects_unauthorized_and_bad_direct_or_delegate_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(BrainProtocolError, match="^brain protocol invalid$"):
        parse_brain_decision(json.dumps(payload), allowed_agent_ids=ALLOWED)


def test_parser_rejects_output_over_64_kib_without_repairing_it() -> None:
    oversized = json.dumps({**DIRECT, "answer": "a" * 65_536})

    with pytest.raises(BrainProtocolError, match="^brain protocol invalid$"):
        parse_brain_decision(oversized, allowed_agent_ids=ALLOWED)


def test_parser_collapses_adversarial_json_nesting_to_stable_error() -> None:
    deeply_nested = "[" * 2_000 + "]" * 2_000

    with pytest.raises(BrainProtocolError, match="^brain protocol invalid$"):
        parse_brain_decision(deeply_nested, allowed_agent_ids=ALLOWED)


def test_parser_rejects_duplicate_object_members() -> None:
    duplicate_kind = (
        '{"kind":"direct","kind":"delegate","answer":null,'
        '"agent_id":"hr-bot","objective":"work",'
        '"rationale_summary":"delegate"}'
    )

    with pytest.raises(BrainProtocolError, match="^brain protocol invalid$"):
        parse_brain_decision(duplicate_kind, allowed_agent_ids=ALLOWED)


def test_parser_collapses_unpaired_surrogate_to_stable_error() -> None:
    with pytest.raises(BrainProtocolError, match="^brain protocol invalid$"):
        parse_brain_decision("\ud800", allowed_agent_ids=ALLOWED)


@pytest.mark.parametrize(
    "rendered",
    (
        '{"kind":"direct","answer":"\\ud800","agent_id":null,'
        '"objective":null,"rationale_summary":"safe"}',
        '{"kind":"direct","answer":"safe","agent_id":null,'
        '"objective":null,"rationale_summary":"safe","\\ud800":"value"}',
    ),
)
def test_parser_rejects_unpaired_surrogates_after_json_decoding(
    rendered: str,
) -> None:
    with pytest.raises(BrainProtocolError, match="^brain protocol invalid$"):
        parse_brain_decision(rendered, allowed_agent_ids=ALLOWED)


@pytest.mark.parametrize("invalid", (None, b"{}", "", "[]", "null"))
def test_parser_collapses_type_and_schema_errors_to_stable_non_secret_error(
    invalid: object,
) -> None:
    with pytest.raises(BrainProtocolError) as raised:
        parse_brain_decision(
            invalid, allowed_agent_ids=ALLOWED  # type: ignore[arg-type]
        )

    assert type(raised.value) is BrainProtocolError
    assert str(raised.value) == "brain protocol invalid"
    assert repr(invalid) not in repr(raised.value)
