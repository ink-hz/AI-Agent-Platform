import json

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


def test_public_dict_never_exposes_replay_credentials():
    entry = AgentEntry(
        **_entry_kwargs(
            flywheel_agent_id="ai-fae-agent",
            replay_targets=[
                {
                    "environment": "dev",
                    "api_base": "http://127.0.0.1:18000",
                    "health_url": "http://127.0.0.1:18000/health",
                    "credential_ref": "file:/private/replay-token",
                }
            ],
            review_evidence={
                "repository_path": "/work/AI-FAE-Agent",
                "release_manifest_dir": "/work/AI-FAE-Agent/dist/release/manifests",
            },
        )
    )

    public = entry.public_dict()

    assert "replay_targets" not in public
    assert "review_evidence" not in public
    assert "credential_ref" not in json.dumps(public)
