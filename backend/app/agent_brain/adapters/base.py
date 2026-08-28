from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.agent_brain.loop_models import NormalizedTaskResult
from app.execution_relay.models import RequesterSubject

_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class AgentEventProtocolError(RuntimeError):
    """One upstream Task returned a non-conforming event page."""


@dataclass(frozen=True, slots=True)
class AdapterTask:
    task_id: UUID
    loop_id: UUID
    agent_id: str
    context: dict[str, object] = field(repr=False)
    effective_deadline_at: datetime
    requester_subject: RequesterSubject | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AdapterDelivery:
    delivery_id: UUID
    attempt: int
    idempotency_key: str
    delivery_kind: Literal["initial", "followup", "stop"] = "initial"
    source_message_seq: int | None = None


@dataclass(frozen=True, slots=True)
class AdapterMessage:
    seq: int
    text: str = field(repr=False)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    supports_persistent_session: bool
    supports_followup_message: bool
    supports_progress_events: bool
    supports_thinking_summary: bool
    supports_cancel: bool
    supports_attachments: bool
    typical_latency_seconds: int


@dataclass(frozen=True, slots=True)
class ChildSessionReceipt:
    accepted: bool
    child_session_id: str
    external_run_id: UUID | None


@dataclass(frozen=True, slots=True)
class MessageDeliveryReceipt:
    accepted: bool
    external_run_id: UUID | None


@dataclass(frozen=True, slots=True)
class StopDeliveryReceipt:
    accepted: bool
    supported: bool


@dataclass(frozen=True, slots=True)
class AdapterEvent:
    seq: int
    kind: Literal[
        "thinking_summary",
        "work_update",
        "message",
        "artifact",
        "question",
        "result",
        "error",
    ]
    source: Literal["provider", "agent", "adapter"]
    source_ref: str
    created_at: datetime
    payload: dict[str, object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    accepted: bool
    result: NormalizedTaskResult | None
    external_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CancelReceipt:
    accepted: bool


class AgentAdapter(ABC):
    supports_cancellation: bool = False
    capabilities: AdapterCapabilities

    @abstractmethod
    def start_session(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> ChildSessionReceipt: ...

    @abstractmethod
    def send_message(
        self,
        child_session_id: str,
        message: AdapterMessage,
        delivery: AdapterDelivery,
    ) -> MessageDeliveryReceipt: ...

    @abstractmethod
    def read_events(
        self, child_session_id: str, *, after: int
    ) -> tuple[AdapterEvent, ...]: ...

    @abstractmethod
    def request_stop(
        self,
        child_session_id: str,
        reason: str,
        delivery: AdapterDelivery,
    ) -> StopDeliveryReceipt: ...

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
