from pathlib import Path
from typing import Protocol

import yaml
from pydantic import ValidationError

from .models import AgentEntry, Registry


class RegistryError(Exception):
    """Raised on any registry load or validation failure."""


class Repository(Protocol):
    def list_agents(self) -> list[AgentEntry]: ...

    def get_agent(self, agent_id: str) -> AgentEntry | None: ...


class YamlRepository:
    def __init__(self, path: str) -> None:
        self._registry = self._load(Path(path))

    @staticmethod
    def _load(path: Path) -> Registry:
        if not path.exists():
            raise RegistryError(f"registry file not found: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RegistryError(f"invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RegistryError(
                f"registry must be a mapping, got {type(data).__name__}"
            )
        try:
            registry = Registry.model_validate(data)
        except ValidationError as exc:
            raise RegistryError(f"registry validation failed: {exc}") from exc

        ids = [agent.id for agent in registry.agents]
        duplicates = {agent_id for agent_id in ids if ids.count(agent_id) > 1}
        if duplicates:
            raise RegistryError(f"duplicate agent ids: {sorted(duplicates)}")
        return registry

    def list_agents(self) -> list[AgentEntry]:
        return list(self._registry.agents)

    def get_agent(self, agent_id: str) -> AgentEntry | None:
        for agent in self._registry.agents:
            if agent.id == agent_id:
                return agent
        return None
