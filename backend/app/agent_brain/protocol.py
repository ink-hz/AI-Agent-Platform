from __future__ import annotations

from collections.abc import Collection
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator


MAX_BRAIN_OUTPUT_BYTES = 64 * 1024


class BrainProtocolError(RuntimeError):
    """Stable protocol failure that does not include model output."""

    def __init__(self) -> None:
        super().__init__("brain protocol invalid")


class BrainDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["direct", "delegate"]
    answer: str | None
    agent_id: str | None
    objective: str | None
    rationale_summary: str

    @model_validator(mode="after")
    def _validate_shape(self) -> BrainDecision:
        if not self.rationale_summary.strip():
            raise ValueError
        if self.kind == "direct":
            if (
                self.answer is None
                or not self.answer.strip()
                or self.agent_id is not None
                or self.objective is not None
            ):
                raise ValueError
        elif (
            self.answer is not None
            or self.agent_id is None
            or not self.agent_id.strip()
            or self.objective is None
            or not self.objective.strip()
        ):
            raise ValueError
        return self


def _json_source(rendered: str) -> str:
    stripped = rendered.strip()
    if not stripped.startswith("```"):
        return stripped
    first_newline = stripped.find("\n")
    if first_newline < 0 or stripped[:first_newline] not in {"```", "```json"}:
        raise ValueError
    if not stripped.endswith("```"):
        raise ValueError
    return stripped[first_newline + 1 : -3].strip()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, member in pairs:
        if key in value:
            raise ValueError
        value[key] = member
    return value


def parse_brain_decision(
    rendered: str, *, allowed_agent_ids: Collection[str]
) -> BrainDecision:
    """Parse exactly one strict planner object without repairing model output."""

    try:
        if not isinstance(rendered, str):
            raise TypeError
        if len(rendered.encode("utf-8")) > MAX_BRAIN_OUTPUT_BYTES:
            raise ValueError
        if isinstance(allowed_agent_ids, (str, bytes)):
            raise TypeError
        allowed = frozenset(allowed_agent_ids)
        if any(not isinstance(agent_id, str) or not agent_id for agent_id in allowed):
            raise ValueError
        source = _json_source(rendered)
        decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
        value, end = decoder.raw_decode(source)
        if source[end:].strip():
            raise ValueError
        decision = BrainDecision.model_validate(value)
        if decision.kind == "delegate" and decision.agent_id not in allowed:
            raise ValueError
        return decision
    except (
        AttributeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValidationError,
        ValueError,
    ):
        raise BrainProtocolError() from None
