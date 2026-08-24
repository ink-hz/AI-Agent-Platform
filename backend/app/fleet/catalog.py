from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import yaml

from app.agent_catalog import load_agent_catalog


AgentVisibility = Literal["business", "system"]


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    domain: str
    description: str
    glyph: str
    accent: str
    visibility: AgentVisibility = "system"
    live_since: str | None = None
    live_since_basis: str = "not_recorded"
    last_updated_at: str | None = None
    last_updated_basis: str = "not_recorded"


class AgentCatalog:
    def __init__(
        self,
        profiles: dict[str, AgentProfile],
        aliases: dict[str, str],
        unresolved_aliases: set[str],
        excluded_ids: set[str] | None = None,
    ) -> None:
        self._profiles = profiles
        self._aliases = aliases
        self._unresolved_aliases = unresolved_aliases
        self._excluded_ids = set(excluded_ids or ())
        missing = self._excluded_ids - profiles.keys()
        if missing:
            raise ValueError(
                f"excluded agent profile not declared: {sorted(missing)}"
            )

    @classmethod
    def default(cls) -> "AgentCatalog":
        path = Path(__file__).with_name("catalog.yaml")
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profiles = {
            bot_id: AgentProfile(id=bot_id, **fields)
            for bot_id, fields in (payload.get("profiles") or {}).items()
        }
        for card in load_agent_catalog():
            existing = profiles.get(card.agent_id)
            if existing is None:
                existing = AgentProfile(
                    id=card.agent_id,
                    name=card.display_name,
                    domain=card.domain_group,
                    description=card.mission,
                    glyph="AI",
                    accent="default",
                    visibility="business",
                )
            profiles[card.agent_id] = replace(
                existing,
                name=card.display_name,
                domain=card.domain_group,
                description=card.mission,
                visibility="business",
            )
        return cls(
            profiles,
            dict(payload.get("aliases") or {}),
            set(payload.get("unresolved_aliases") or []),
            set(payload.get("excluded_ids") or []),
        )

    def profile(self, bot_id: str, fallback_name: str) -> AgentProfile:
        return self._profiles.get(bot_id) or AgentProfile(
            id=bot_id,
            name=fallback_name,
            domain="MetaBot 实例",
            description="由运行契约动态发现的 Agent Bot 实例。",
            glyph="AI",
            accent="default",
            visibility="system",
            live_since=None,
            live_since_basis="not_recorded",
            last_updated_at=None,
            last_updated_basis="not_recorded",
        )

    def canonical_id(self, bot_id: str) -> str | None:
        if bot_id in self._unresolved_aliases:
            return None
        canonical = self._aliases.get(bot_id, bot_id)
        if canonical in self._excluded_ids:
            return None
        return canonical

    def is_excluded(self, agent_id: str) -> bool:
        return self._aliases.get(agent_id, agent_id) in self._excluded_ids

    def excluded_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._excluded_ids))

    def all_profiles(self) -> tuple[AgentProfile, ...]:
        return tuple(
            profile
            for profile in self._profiles.values()
            if profile.id not in self._excluded_ids
        )

    def ids_for_visibility(self, visibility: AgentVisibility) -> tuple[str, ...]:
        return tuple(
            profile.id
            for profile in self.all_profiles()
            if profile.visibility == visibility
        )
