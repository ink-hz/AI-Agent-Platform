from pathlib import Path

import pytest
import yaml

from app.agent_catalog import CANONICAL_AGENT_IDS, AgentCatalogRepository, load_agent_catalog


EXPECTED_IDS = {
    "hr-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "ai-admin-agent",
    "ai-fae-agent",
}


def _payload() -> dict:
    path = Path(__file__).parents[1] / "app" / "agent_catalog" / "catalog.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_catalog_contains_exactly_the_eight_product_agents() -> None:
    cards = load_agent_catalog()

    assert set(CANONICAL_AGENT_IDS) == EXPECTED_IDS
    assert tuple(card.agent_id for card in cards) == CANONICAL_AGENT_IDS
    assert not {
        "fae-bot", "feishu-default", "test-bot", "codex-assistant", "agent-brain-bot",
    } & {card.agent_id for card in cards}


def test_catalog_expresses_direct_delegated_and_external_modes_explicitly() -> None:
    repository = AgentCatalogRepository()

    for agent_id in EXPECTED_IDS - {"ai-admin-agent", "ai-fae-agent"}:
        card = repository.require(agent_id)
        assert card.interaction_modes == ("direct_chat", "brain_delegation")
        assert card.adapter_kind == "metabot_local"
        assert card.workspace_url is None
        assert card.dispatchable is True

    admin = repository.require("ai-admin-agent")
    fae = repository.require("ai-fae-agent")
    assert admin.interaction_modes == ("external_workspace",)
    assert admin.workspace_url == "/office/?view=services"
    assert admin.adapter_kind is None
    assert admin.dispatchable is False
    assert fae.workspace_url == "https://fae.orbbec.com.cn/"
    assert fae.adapter_kind is None
    assert fae.dispatchable is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda agents: agents.append(dict(agents[0])),
        lambda agents: agents.pop(),
        lambda agents: agents[0].update(agent_id="fae-bot"),
        lambda agents: agents[0].update(interaction_modes=["external_workspace"]),
        lambda agents: agents[0].update(workspace_url="https://evil.example/"),
        lambda agents: agents[-1].update(adapter_kind="fae_http"),
    ],
    ids=(
        "duplicate", "missing", "legacy-id", "external-without-workspace",
        "arbitrary-workspace", "external-with-adapter",
    ),
)
def test_invalid_catalog_fails_closed(tmp_path: Path, mutate) -> None:
    payload = _payload()
    mutate(payload["agents"])
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="Agent Catalog invalid"):
        load_agent_catalog(path)
