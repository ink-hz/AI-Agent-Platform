from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from app.agent_catalog.models import CANONICAL_AGENT_IDS

from .models import AgentEntry, Registry


class RegistryError(Exception):
    """Raised on any registry load or validation failure."""


class Repository(Protocol):
    def list_agents(self) -> list[AgentEntry]: ...

    def get_agent(self, agent_id: str) -> AgentEntry | None: ...

    def get_agent_by_flywheel_id(
        self, flywheel_agent_id: str
    ) -> AgentEntry | None: ...


def _origin(value: str) -> tuple[str, str, int] | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return (
        parsed.scheme,
        parsed.hostname.casefold(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


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

        flywheel_ids = [
            agent.flywheel_agent_id
            for agent in registry.agents
            if agent.flywheel_agent_id
        ]
        duplicate_flywheel_ids = {
            item for item in flywheel_ids if flywheel_ids.count(item) > 1
        }
        if duplicate_flywheel_ids:
            raise RegistryError(
                "duplicate flywheel agent ids: "
                f"{sorted(duplicate_flywheel_ids)}"
            )

        canonical_ids = set(CANONICAL_AGENT_IDS)
        fixed_joins = {"fae": "ai-fae-agent", "admin": "ai-admin-agent"}
        for agent in registry.agents:
            if agent.id in fixed_joins and agent.flywheel_agent_id != fixed_joins[agent.id]:
                raise RegistryError(
                    f"agent {agent.id!r} must use its canonical Catalog identity"
                )
            if agent.flywheel_agent_id and agent.flywheel_agent_id not in canonical_ids:
                raise RegistryError(
                    f"agent {agent.id!r} has an unknown canonical Catalog identity"
                )

        for agent in registry.agents:
            if not agent.replay_targets:
                continue
            if len(agent.replay_targets) != 1:
                raise RegistryError(
                    f"agent {agent.id!r} must define exactly one dev replay target"
                )
            target = agent.replay_targets[0]
            production_url = agent.api_base or agent.health.url
            production_origin = _origin(production_url)
            target_origin = _origin(target.api_base)
            health_origin = _origin(target.health_url)
            if target_origin is None or health_origin is None:
                raise RegistryError(
                    f"agent {agent.id!r} has an invalid replay target URL"
                )
            if target_origin != health_origin:
                raise RegistryError(
                    f"agent {agent.id!r} replay API and health origins differ"
                )
            if production_origin is not None and target_origin == production_origin:
                raise RegistryError(
                    f"agent {agent.id!r} dev and production origins must differ"
                )
        return registry

    def list_agents(self) -> list[AgentEntry]:
        return list(self._registry.agents)

    def get_agent(self, agent_id: str) -> AgentEntry | None:
        for agent in self._registry.agents:
            if agent.id == agent_id:
                return agent
        return None

    def get_agent_by_flywheel_id(
        self, flywheel_agent_id: str
    ) -> AgentEntry | None:
        for agent in self._registry.agents:
            if agent.flywheel_agent_id == flywheel_agent_id:
                return agent
        return None
