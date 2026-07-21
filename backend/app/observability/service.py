from __future__ import annotations

import asyncio

from .models import FlywheelFilters, SessionFilters
from .repository import ObservabilityRepository


class ObservabilityService:
    def __init__(self, repository: ObservabilityRepository) -> None:
        self._repository = repository

    async def list_agents(self):
        return await asyncio.to_thread(self._repository.list_agents)

    async def get_agent(self, agent_id: str):
        return await asyncio.to_thread(self._repository.get_agent, agent_id)

    async def list_sessions(self, filters: SessionFilters, limit: int, offset: int):
        return await asyncio.to_thread(self._repository.list_sessions, filters, limit, offset)

    async def get_session(self, session_key: str):
        return await asyncio.to_thread(self._repository.get_session, session_key)

    async def get_trace(self, turn_key: str):
        return await asyncio.to_thread(self._repository.get_trace, turn_key)

    async def flywheel_overview(self):
        return await asyncio.to_thread(self._repository.get_flywheel_overview)

    async def list_improvement_items(self, filters: FlywheelFilters, limit: int, offset: int):
        return await asyncio.to_thread(self._repository.list_improvement_items, filters, limit, offset)

    async def sync_status(self):
        return await asyncio.to_thread(self._repository.get_sync_status)

