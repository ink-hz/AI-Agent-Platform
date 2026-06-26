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
      - id: admin
        name: "AI ADMIN Agent"
        entry_url: "http://admin/app/"
        health: {url: "http://admin/health", type: "admin"}
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
