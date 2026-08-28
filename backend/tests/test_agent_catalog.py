from pathlib import Path

import pytest
import yaml
from app.agent_catalog import (
    CANONICAL_AGENT_IDS,
    AgentCatalogRepository,
    load_agent_catalog,
)

EXPECTED_IDS = {
    "hr-bot",
    "voc",
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


def test_catalog_contains_exactly_the_nine_product_agents() -> None:
    cards = load_agent_catalog()

    assert set(CANONICAL_AGENT_IDS) == EXPECTED_IDS
    assert tuple(card.agent_id for card in cards) == CANONICAL_AGENT_IDS
    assert not {
        "fae-bot",
        "feishu-default",
        "test-bot",
        "codex-assistant",
        "agent-brain-bot",
    } & {card.agent_id for card in cards}


def test_catalog_expresses_direct_delegated_and_external_modes_explicitly() -> None:
    repository = AgentCatalogRepository()

    for agent_id in EXPECTED_IDS - {"ai-admin-agent", "ai-fae-agent", "voc"}:
        card = repository.require(agent_id)
        assert card.interaction_modes == ("direct_chat", "brain_delegation")
        assert card.adapter_kind == "metabot_local"
        assert card.workspace_url is None
        assert card.dispatchable is True

    admin = repository.require("ai-admin-agent")
    fae = repository.require("ai-fae-agent")
    voc = repository.require("voc")
    assert admin.interaction_modes == ("external_workspace",)
    assert admin.workspace_url == "/office/?view=services"
    assert admin.adapter_kind is None
    assert admin.dispatchable is False
    assert fae.workspace_url == "https://fae.orbbec.com.cn/"
    assert fae.adapter_kind is None
    assert fae.dispatchable is False
    assert voc.interaction_modes == ("external_workspace", "brain_delegation")
    assert voc.workspace_url == "/agents/voc/workspace"
    assert voc.adapter_kind == "voc_action"
    assert voc.dispatchable is True


def test_catalog_exposes_public_persona_subtitles() -> None:
    repository = AgentCatalogRepository()

    assert repository.require("hr-bot").persona_subtitle == (
        "Hannah · 技术人才搜寻与招聘协作"
    )
    assert all(
        repository.require(agent_id).persona_subtitle
        for agent_id in EXPECTED_IDS
        if agent_id.startswith("marketing-")
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda agents: agents.append(dict(agents[0])),
        lambda agents: agents.pop(),
        lambda agents: agents[0].update(agent_id="fae-bot"),
        lambda agents: agents[0].update(interaction_modes=["external_workspace"]),
        lambda agents: agents[0].update(workspace_url="https://evil.example/"),
        lambda agents: agents[-1].update(adapter_kind="fae_http"),
        lambda agents: agents[0].update(pool_concurrency=2),
        lambda agents: agents[0].update(execution_pool=None),
        lambda agents: agents[1].update(execution_pool=None),
    ],
    ids=(
        "duplicate",
        "missing",
        "legacy-id",
        "external-without-workspace",
        "arbitrary-workspace",
        "external-with-adapter",
        "pool-concurrency-conflict",
        "delegated-without-pool",
        "delegated-without-pool",
    ),
)
def test_invalid_catalog_fails_closed(tmp_path: Path, mutate) -> None:
    payload = _payload()
    mutate(payload["agents"])
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="Agent Catalog invalid"):
        load_agent_catalog(path)


def test_delegated_agents_declare_the_executor_they_contend_for() -> None:
    cards = {card.agent_id: card for card in load_agent_catalog()}

    # All six MetaBot Agents run on one host that executes strictly one task at a
    # time, so the Brain must not treat them as six parallel slots.
    pooled = [card for card in cards.values() if card.execution_pool is not None]
    assert {card.execution_pool for card in pooled} == {
        "metabot_local",
        "voc_extension",
    }
    assert {card.pool_concurrency for card in pooled} == {1}
    assert len(pooled) == 7

    for agent_id in ("ai-admin-agent", "ai-fae-agent"):
        assert cards[agent_id].execution_pool is None
        assert cards[agent_id].pool_concurrency is None
    assert cards["voc"].execution_pool == "voc_extension"
    assert cards["voc"].pool_concurrency == 1
