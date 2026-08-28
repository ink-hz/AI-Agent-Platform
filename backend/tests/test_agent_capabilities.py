from pathlib import Path

import pytest
import yaml
from app.agent_brain.authorization import AgentUseAuthorization
from app.agent_brain.models import (
    CALLABLE_AGENT_IDS,
    AgentCapabilityCard,
    load_capability_cards,
)
from pydantic import ValidationError

APP_DSN = "postgresql://platform_control_app:secret@127.0.0.1/agent_platform_control"


def _default_payload() -> dict[str, object]:
    path = Path(__file__).parents[1] / "app" / "agent_catalog" / "catalog.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_payload(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "capabilities.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_brain_projection_contains_exactly_the_seven_delegated_agents() -> None:
    cards = load_capability_cards()

    assert tuple(card.agent_id for card in cards) == CALLABLE_AGENT_IDS
    assert set(CALLABLE_AGENT_IDS) == {
        "hr-bot",
        "voc",
        "marketing-prospecting-bot",
        "marketing-inbound-bot",
        "marketing-voice-bot",
        "marketing-intelligence-bot",
        "marketing-gtm-bot",
    }
    assert not {
        "feishu-default",
        "test-bot",
        "codex-assistant",
        "ai-fae-agent",
        "ai-admin-agent",
        "fae-bot",
    } & {card.agent_id for card in cards}
    voc = next(card for card in cards if card.agent_id == "voc")
    assert voc.adapter_kind == "voc_action"
    assert voc.capability_version == 2


def test_capability_cards_are_immutable_and_contain_only_public_call_contract() -> None:
    card = load_capability_cards()[0]
    dumped = card.model_dump(mode="json")

    assert {
        "mission",
        "capabilities",
        "exclusions",
        "required_inputs",
        "example_tasks",
        "max_duration_seconds",
        "capability_version",
    } <= dumped.keys()
    assert card.exclusions
    assert 1 <= card.max_duration_seconds <= 300
    assert card.capability_version > 0
    assert isinstance(card.capabilities, tuple)

    forbidden_fields = {
        "prompt",
        "system_prompt",
        "model",
        "port",
        "credentials",
        "credential",
        "adapter_url",
        "health",
        "session",
    }
    assert forbidden_fields.isdisjoint(key.lower() for key in dumped)
    with pytest.raises(ValidationError):
        card.mission = "changed"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda agents: agents.append(dict(agents[0])),
        lambda agents: agents.append({**agents[0], "agent_id": "test-bot"}),
        lambda agents: agents[0].update(exclusions=[]),
        lambda agents: agents[0].pop("exclusions"),
        lambda agents: agents.pop(),
        lambda agents: agents[0].update(prompt="private"),
        lambda agents: agents[0].update(max_duration_seconds=0),
        lambda agents: agents[0].update(max_duration_seconds=301),
        lambda agents: agents[0].update(capability_version=0),
    ],
    ids=[
        "duplicate-id",
        "unknown-id",
        "empty-exclusions",
        "omitted-exclusions",
        "omitted-callable-id",
        "extra-yaml-field",
        "zero-duration",
        "excessive-duration",
        "non-positive-version",
    ],
)
def test_invalid_capability_configuration_fails_during_startup(
    tmp_path: Path, mutate
) -> None:
    payload = _default_payload()
    agents = payload["agents"]
    mutate(agents)
    path = _write_payload(tmp_path, payload)

    with pytest.raises(ValueError):
        AgentUseAuthorization(APP_DSN, capability_path=path)


def test_capability_model_rejects_undeclared_internal_fields() -> None:
    values = load_capability_cards()[0].model_dump()

    with pytest.raises(ValidationError):
        AgentCapabilityCard.model_validate({**values, "model": "private"})
