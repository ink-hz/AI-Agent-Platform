from app.registry.models import AgentEntry, Registry


def _entry_kwargs(**over):
    base = dict(
        id="fae",
        name="AI FAE Agent",
        domain="技术支持",
        description="desc",
        icon="🛠️",
        owner="someone",
        entry_url="http://host/app/",
        health={"url": "http://host/health", "type": "fae"},
    )
    base.update(over)
    return base


def test_agent_entry_defaults():
    entry = AgentEntry(**_entry_kwargs())
    assert entry.env == "prod"
    assert entry.status == "active"
    assert entry.version == ""
    assert entry.tags == []
    assert entry.health.type == "fae"


def test_public_dict_hides_health_url_and_api_base():
    entry = AgentEntry(**_entry_kwargs(api_base="http://host"))
    public = entry.public_dict()
    assert public["id"] == "fae"
    assert public["entry_url"] == "http://host/app/"
    assert "health" not in public
    assert "api_base" not in public


def test_registry_parses_agent_list():
    registry = Registry.model_validate({"version": 1, "agents": [_entry_kwargs()]})
    assert len(registry.agents) == 1
    assert registry.agents[0].id == "fae"
