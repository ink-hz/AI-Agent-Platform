from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    domain: str
    description: str
    glyph: str
    accent: str
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
    ) -> None:
        self._profiles = profiles
        self._aliases = aliases
        self._unresolved_aliases = unresolved_aliases

    @classmethod
    def default(cls) -> "AgentCatalog":
        path = Path(__file__).with_name("catalog.yaml")
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profiles = {
            bot_id: AgentProfile(id=bot_id, **fields)
            for bot_id, fields in (payload.get("profiles") or {}).items()
        }
        return cls(
            profiles,
            dict(payload.get("aliases") or {}),
            set(payload.get("unresolved_aliases") or []),
        )

    def profile(self, bot_id: str, fallback_name: str) -> AgentProfile:
        return self._profiles.get(bot_id) or AgentProfile(
            id=bot_id,
            name=fallback_name,
            domain="MetaBot 实例",
            description="由运行契约动态发现的 Agent Bot 实例。",
            glyph="AI",
            accent="default",
            live_since=None,
            live_since_basis="not_recorded",
            last_updated_at=None,
            last_updated_basis="not_recorded",
        )

    def canonical_id(self, bot_id: str) -> str | None:
        if bot_id in self._profiles:
            return bot_id
        if bot_id in self._unresolved_aliases:
            return None
        return self._aliases.get(bot_id, bot_id)

    def all_profiles(self) -> tuple[AgentProfile, ...]:
        return tuple(self._profiles.values())
