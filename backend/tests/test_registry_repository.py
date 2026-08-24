import textwrap

import pytest

from app.registry.repository import RegistryError, YamlRepository


GOOD = textwrap.dedent(
    """
    version: 1
    agents:
      - id: fae
        name: "AI FAE Agent"
        entry_url: "http://fae/app/"
        health: {url: "http://fae/health", type: "fae"}
        flywheel_agent_id: "ai-fae-agent"
      - id: admin
        name: "AI ADMIN Agent"
        entry_url: "http://admin/app/"
        health: {url: "http://admin/health", type: "admin"}
        flywheel_agent_id: "ai-admin-agent"
    """
)

REGISTRY_WITH_REPLAY = textwrap.dedent(
    """
    version: 1
    agents:
      - id: fae
        name: "AI FAE Agent"
        entry_url: "http://prod.example/app/"
        api_base: "http://prod.example"
        health: {url: "http://prod.example/health", type: "fae"}
        flywheel_agent_id: "ai-fae-agent"
        replay_targets:
          - environment: dev
            api_base: "http://127.0.0.1:18000"
            health_url: "http://127.0.0.1:18000/health"
            credential_ref: "file:/private/fae-dev-api"
    """
)


def _write(tmp_path, content):
    path = tmp_path / "registry.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_loads_and_lists_agents(tmp_path):
    repo = YamlRepository(_write(tmp_path, GOOD))
    agents = repo.list_agents()
    assert [agent.id for agent in agents] == ["fae", "admin"]


def test_get_agent_by_id(tmp_path):
    repo = YamlRepository(_write(tmp_path, GOOD))
    assert repo.get_agent("admin").name == "AI ADMIN Agent"
    assert repo.get_agent("missing") is None


def test_admin_registry_join_is_fixed_to_the_canonical_catalog_id(tmp_path):
    invalid = GOOD.replace('flywheel_agent_id: "ai-admin-agent"', 'flywheel_agent_id: "admin"')

    with pytest.raises(RegistryError, match="canonical Catalog identity"):
        YamlRepository(_write(tmp_path, invalid))


def test_registry_mapping_does_not_require_a_replay_target(tmp_path):
    repo = YamlRepository(_write(tmp_path, GOOD))

    assert repo.get_agent_by_flywheel_id("ai-admin-agent").id == "admin"


def test_missing_file_fails_fast(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        YamlRepository(str(tmp_path / "nope.yaml"))


def test_invalid_yaml_fails_fast(tmp_path):
    with pytest.raises(RegistryError, match="invalid YAML"):
        YamlRepository(_write(tmp_path, "version: 1\nagents: [oops: ]["))


def test_missing_required_field_fails_fast(tmp_path):
    bad = textwrap.dedent(
        """
        version: 1
        agents:
          - id: fae
            name: "no entry_url / health"
        """
    )
    with pytest.raises(RegistryError, match="validation failed"):
        YamlRepository(_write(tmp_path, bad))


def test_duplicate_ids_fail_fast(tmp_path):
    duplicate = textwrap.dedent(
        """
        version: 1
        agents:
          - id: fae
            name: a
            entry_url: "http://a/app/"
            health: {url: "http://a/health"}
          - id: fae
            name: b
            entry_url: "http://b/app/"
            health: {url: "http://b/health"}
        """
    )
    with pytest.raises(RegistryError, match="duplicate"):
        YamlRepository(_write(tmp_path, duplicate))


def test_registry_resolves_unique_flywheel_id_and_dev_target(tmp_path):
    repo = YamlRepository(_write(tmp_path, REGISTRY_WITH_REPLAY))

    agent = repo.get_agent_by_flywheel_id("ai-fae-agent")

    assert agent is not None
    assert agent.id == "fae"
    assert agent.replay_targets[0].environment == "dev"
    assert agent.replay_targets[0].credential_ref == "file:/private/fae-dev-api"


@pytest.mark.parametrize(
    "agents",
    [
        [
            {
                "id": "fae",
                "flywheel_agent_id": "same-agent",
                "api_base": "http://prod-a.example",
                "replay_targets": [
                    {
                        "environment": "dev",
                        "api_base": "http://dev-a.example",
                        "health_url": "http://dev-a.example/health",
                        "credential_ref": "env:FAE_KEY",
                    }
                ],
            },
            {
                "id": "admin",
                "flywheel_agent_id": "same-agent",
                "api_base": "http://prod-b.example",
                "replay_targets": [
                    {
                        "environment": "dev",
                        "api_base": "http://dev-b.example",
                        "health_url": "http://dev-b.example/health",
                        "credential_ref": "env:ADMIN_KEY",
                    }
                ],
            },
        ],
        [
            {
                "id": "fae",
                "flywheel_agent_id": "ai-fae-agent",
                "api_base": "http://same.example",
                "replay_targets": [
                    {
                        "environment": "dev",
                        "api_base": "http://same.example:80/dev",
                        "health_url": "http://same.example/health",
                        "credential_ref": "env:FAE_KEY",
                    }
                ],
            }
        ],
    ],
)
def test_invalid_replay_registry_fails_fast(tmp_path, agents):
    rows = []
    for agent in agents:
        rows.append(
            {
                "name": agent["id"],
                "entry_url": f"http://{agent['id']}.example/app/",
                "health": {"url": f"http://{agent['id']}.example/health"},
                **agent,
            }
        )
    import yaml

    payload = yaml.safe_dump({"version": 1, "agents": rows}, sort_keys=False)

    with pytest.raises(RegistryError):
        YamlRepository(_write(tmp_path, payload))
