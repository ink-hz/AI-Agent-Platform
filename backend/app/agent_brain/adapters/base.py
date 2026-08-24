from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
import re
from uuid import UUID

from app.agent_brain.loop_models import NormalizedTaskResult


_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class AdapterTask:
    task_id: UUID
    loop_id: UUID
    agent_id: str
    context: dict[str, object] = field(repr=False)
    effective_deadline_at: datetime


@dataclass(frozen=True, slots=True)
class AdapterDelivery:
    delivery_id: UUID
    attempt: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    accepted: bool
    result: NormalizedTaskResult


@dataclass(frozen=True, slots=True)
class CancelReceipt:
    accepted: bool


class AgentAdapter(ABC):
    @abstractmethod
    def dispatch(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> DispatchReceipt: ...

    @abstractmethod
    def request_cancel(self, task: AdapterTask) -> CancelReceipt: ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AgentAdapter] = {}

    def register(self, kind: str, adapter: AgentAdapter) -> None:
        if type(kind) is not str or _KIND.fullmatch(kind) is None:
            raise ValueError("Adapter kind invalid")
        if not isinstance(adapter, AgentAdapter):
            raise ValueError("Agent Adapter invalid")
        if kind in self._adapters:
            raise ValueError("Adapter already registered")
        self._adapters[kind] = adapter

    def require(self, kind: str) -> AgentAdapter:
        try:
            return self._adapters[kind]
        except (KeyError, TypeError):
            raise LookupError("Adapter not registered") from None

    def is_registered(self, kind: str) -> bool:
        return isinstance(kind, str) and kind in self._adapters

