from __future__ import annotations

from pathlib import Path

from .models import AgentCatalogCard, load_agent_catalog


class AgentCatalogRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        self._cards = load_agent_catalog(path)
        self._by_id = {card.agent_id: card for card in self._cards}

    def list(self) -> tuple[AgentCatalogCard, ...]:
        return self._cards

    def get(self, agent_id: str) -> AgentCatalogCard | None:
        return self._by_id.get(agent_id) if isinstance(agent_id, str) else None

    def require(self, agent_id: str) -> AgentCatalogCard:
        card = self.get(agent_id)
        if card is None:
            raise KeyError(agent_id)
        return card
