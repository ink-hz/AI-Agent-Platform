"""Shared non-blocking HTTP Task Contract v1 Adapter."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol
from urllib.parse import quote, urlparse
from uuid import UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .base import (
    AdapterCapabilities,
    AdapterDelivery,
    AdapterEvent,
    AdapterMessage,
    AdapterTask,
    AgentAdapter,
    AgentEventProtocolError,
    CancelReceipt,
    ChildSessionReceipt,
    DispatchReceipt,
    MessageDeliveryReceipt,
    StopDeliveryReceipt,
)

CONTRACT_VERSION = "orbbec-http-task/v1"
_TERMINAL_KINDS = frozenset({"result", "failed", "timeout", "cancelled"})
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EventKind = Literal[
    "thinking_summary",
    "message",
    "work_update",
    "artifact",
    "input_required",
    "action_required",
    "finding",
    "result",
    "failed",
    "timeout",
    "cancelled",
]


class _TokenIssuer(Protocol):
    def issue(self, **values: object) -> str: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return value


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values or tuple(sorted(set(values))) != values:
        raise ValueError("values must be non-empty, unique, and sorted")
    return values


class CreateTaskRequest(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"] = CONTRACT_VERSION
    platform_task_id: UUID
    conversation_ref: str = Field(min_length=1)
    turn_ref: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    context_excerpt: tuple[str, ...]
    constraints: tuple[str, ...]
    attachment_refs: tuple[UUID, ...]
    expected_output: str = Field(min_length=1)
    capability_version: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1)
    deadline_at: datetime
    authorized_scopes: tuple[str, ...]

    _deadline_utc = field_validator("deadline_at")(_utc)
    _scopes_sorted = field_validator("authorized_scopes")(_sorted_unique)


class _CreateTaskReceipt(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"]
    downstream_task_id: str = Field(min_length=1, max_length=256)
    status: Literal["queued"]
    next_event_seq: Literal[1]
    duplicate: bool


class _MessageRequest(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"] = CONTRACT_VERSION
    message_seq: int = Field(gt=0)
    content: str = Field(min_length=1)
    attachment_refs: tuple[UUID, ...]
    idempotency_key: str = Field(min_length=1)


class _MessageReceipt(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"]
    downstream_task_id: str = Field(min_length=1)
    message_seq: int = Field(gt=0)
    status: Literal["accepted"]
    duplicate: bool


class _CancelRequest(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"] = CONTRACT_VERSION
    idempotency_key: str = Field(min_length=1)


class _CancelReceipt(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"]
    downstream_task_id: str = Field(min_length=1)
    cancel_request_id: str = Field(min_length=1)
    status: Literal["cancel_requested", "cancelled", "completed", "failed", "timed_out"]
    duplicate: bool


class TaskEvent(_StrictModel):
    seq: int = Field(gt=0)
    kind: EventKind
    created_at: datetime
    payload: dict[str, object]

    _created_utc = field_validator("created_at")(_utc)


class TaskEventPage(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"]
    downstream_task_id: str = Field(min_length=1)
    events: tuple[TaskEvent, ...] = Field(max_length=100)
    next_after: int = Field(ge=0)
    terminal: bool

    def validate_after(self, after: int, *, downstream_task_id: str) -> None:
        if type(after) is not int or after < 0:
            raise AgentEventProtocolError("HTTP Task event cursor invalid")
        if self.downstream_task_id != downstream_task_id:
            raise AgentEventProtocolError("HTTP Task event page invalid")
        expected = after + 1
        for index, event in enumerate(self.events):
            if event.seq != expected + index:
                raise AgentEventProtocolError("HTTP Task event page invalid")
            if event.kind in _TERMINAL_KINDS and index != len(self.events) - 1:
                raise AgentEventProtocolError("HTTP Task event page invalid")
        expected_next = self.events[-1].seq if self.events else after
        if self.next_after != expected_next:
            raise AgentEventProtocolError("HTTP Task event page invalid")
        if self.events and self.events[-1].kind in _TERMINAL_KINDS:
            if not self.terminal:
                raise AgentEventProtocolError("HTTP Task event page invalid")
        elif self.events and self.terminal:
            raise AgentEventProtocolError("HTTP Task event page invalid")


class HttpTaskAdapter(AgentAdapter):
    """Call one independently deployed Agent through the frozen v1 contract."""

    supports_cancellation = True
    capabilities = AdapterCapabilities(
        supports_persistent_session=True,
        supports_followup_message=True,
        supports_progress_events=True,
        supports_thinking_summary=True,
        supports_cancel=True,
        supports_attachments=True,
        typical_latency_seconds=90,
    )

    def __init__(
        self,
        client: httpx.Client,
        *,
        base_url: str,
        token_issuer: _TokenIssuer,
        agent_id: str,
        audience: str,
        authorized_scopes: Sequence[str],
        timeout_seconds: float = 10.0,
    ) -> None:
        scopes = tuple(authorized_scopes)
        if (
            not isinstance(client, httpx.Client)
            or not hasattr(token_issuer, "issue")
            or _safe_internal_origin(base_url) is None
            or type(agent_id) is not str
            or _AGENT_ID.fullmatch(agent_id) is None
            or type(audience) is not str
            or _AGENT_ID.fullmatch(audience) is None
            or not scopes
            or tuple(sorted(set(scopes))) != scopes
            or any(type(scope) is not str or not scope for scope in scopes)
            or type(timeout_seconds) not in {int, float}
            or not 0.1 <= float(timeout_seconds) <= 30.0
        ):
            raise ValueError("HTTP Task Adapter configuration invalid")
        self._client = client
        self._base_url = _safe_internal_origin(base_url)
        self._token_issuer = token_issuer
        self._agent_id = agent_id
        self._audience = audience
        self._authorized_scopes = scopes
        self._timeout_seconds = float(timeout_seconds)

    def start_session(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> ChildSessionReceipt:
        self._validate_task(task)
        try:
            request = self._create_request(task, delivery)
            response = self._request(
                "POST",
                "/internal/platform/v1/tasks",
                task=task,
                request_id=delivery.delivery_id,
                json=request.model_dump(mode="json"),
            )
            if response is None:
                return ChildSessionReceipt(False, str(task.task_id), None)
            receipt = _CreateTaskReceipt.model_validate_json(response)
        except (ValidationError, ValueError):
            raise AgentEventProtocolError("HTTP Task create response invalid") from None
        return ChildSessionReceipt(
            True,
            receipt.downstream_task_id,
            None,
        )

    def send_message(
        self,
        child_session_id: str,
        message: AdapterMessage,
        delivery: AdapterDelivery,
        *,
        task: AdapterTask | None = None,
    ) -> MessageDeliveryReceipt:
        selected = self._required_task(task)
        request = _MessageRequest(
            message_seq=message.seq,
            content=message.text,
            attachment_refs=(),
            idempotency_key=delivery.idempotency_key,
        )
        response = self._request(
            "POST",
            self._task_path(child_session_id, "/messages"),
            task=selected,
            request_id=delivery.delivery_id,
            json=request.model_dump(mode="json"),
        )
        if response is None:
            return MessageDeliveryReceipt(False, None)
        try:
            receipt = _MessageReceipt.model_validate_json(response)
            if (
                receipt.downstream_task_id != child_session_id
                or receipt.message_seq != message.seq
            ):
                raise ValueError
        except (ValidationError, ValueError):
            raise AgentEventProtocolError(
                "HTTP Task message response invalid"
            ) from None
        return MessageDeliveryReceipt(True, None)

    def read_events(
        self,
        child_session_id: str,
        *,
        after: int,
        task: AdapterTask | None = None,
    ) -> tuple[AdapterEvent, ...]:
        selected = self._required_task(task)
        if type(after) is not int or after < 0:
            raise ValueError("HTTP Task event cursor invalid")
        response = self._request(
            "GET",
            self._task_path(child_session_id, "/events"),
            task=selected,
            request_id=uuid5(selected.task_id, f"http-events:{after}"),
            params={"after": str(after), "limit": "100", "wait_seconds": "0"},
        )
        if response is None:
            return ()
        try:
            page = TaskEventPage.model_validate_json(response)
            page.validate_after(after, downstream_task_id=child_session_id)
        except (ValidationError, AgentEventProtocolError):
            raise AgentEventProtocolError("HTTP Task event page invalid") from None
        return tuple(
            self._adapter_event(child_session_id, event) for event in page.events
        )

    def request_stop(
        self,
        child_session_id: str,
        reason: str,
        delivery: AdapterDelivery,
        *,
        task: AdapterTask | None = None,
    ) -> StopDeliveryReceipt:
        del reason
        selected = self._required_task(task)
        request = _CancelRequest(idempotency_key=delivery.idempotency_key)
        response = self._request(
            "POST",
            self._task_path(child_session_id, "/cancel"),
            task=selected,
            request_id=delivery.delivery_id,
            json=request.model_dump(mode="json"),
        )
        if response is None:
            return StopDeliveryReceipt(False, True)
        try:
            receipt = _CancelReceipt.model_validate_json(response)
            if receipt.downstream_task_id != child_session_id:
                raise ValueError
        except (ValidationError, ValueError):
            raise AgentEventProtocolError("HTTP Task cancel response invalid") from None
        return StopDeliveryReceipt(True, True)

    def dispatch(self, task: AdapterTask, delivery: AdapterDelivery) -> DispatchReceipt:
        receipt = self.start_session(task, delivery)
        return DispatchReceipt(receipt.accepted, None, receipt.external_run_id)

    def request_cancel(self, task: AdapterTask) -> CancelReceipt:
        del task
        return CancelReceipt(False)

    def _create_request(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> CreateTaskRequest:
        context = task.context
        try:
            return CreateTaskRequest(
                platform_task_id=task.task_id,
                conversation_ref=str(task.conversation_id),
                turn_ref=str(task.turn_id),
                objective=context["objective"],
                context_excerpt=tuple(context.get("context_excerpt", ())),
                constraints=tuple(context.get("constraints", ())),
                attachment_refs=tuple(
                    UUID(str(value)) for value in context.get("attachment_refs", ())
                ),
                expected_output=context["expected_output"],
                capability_version=task.capability_version,
                idempotency_key=delivery.idempotency_key,
                deadline_at=task.effective_deadline_at,
                authorized_scopes=self._authorized_scopes,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            raise ValueError("HTTP Task context invalid") from None

    def _request(
        self,
        method: str,
        path: str,
        *,
        task: AdapterTask,
        request_id: UUID,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> bytes | None:
        if task.requester_subject is None or task.capability_version is None:
            raise ValueError("HTTP Task identity unavailable")
        token = self._token_issuer.issue(
            audience=self._audience,
            internal_user_id=task.requester_subject.internal_user_id,
            agent_id=task.agent_id,
            agent_task_id=task.task_id,
            capability_version=task.capability_version,
            authorized_scopes=self._authorized_scopes,
            task_deadline_at=task.effective_deadline_at,
            action_execution_deadline_at=None,
            request_id=request_id,
        )
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Orbbec-Task-Contract": CONTRACT_VERSION,
                    "Accept": "application/json",
                },
                json=json,
                params=params,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TransportError:
            return None
        if response.status_code >= 500:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise AgentEventProtocolError("HTTP Task request rejected")
        if len(response.content) > 1024 * 1024:
            raise AgentEventProtocolError("HTTP Task response too large")
        return response.content

    def _validate_task(self, task: AdapterTask) -> None:
        if (
            not isinstance(task, AdapterTask)
            or task.agent_id != self._agent_id
            or task.conversation_id is None
            or task.turn_id is None
            or task.capability_version is None
            or task.requester_subject is None
        ):
            raise ValueError("HTTP Task dispatch invalid")

    def _required_task(self, task: AdapterTask | None) -> AdapterTask:
        if task is None:
            raise ValueError("HTTP Task identity unavailable")
        self._validate_task(task)
        return task

    @staticmethod
    def _task_path(child_session_id: str, suffix: str) -> str:
        if type(child_session_id) is not str or not child_session_id:
            raise ValueError("HTTP Task downstream ID invalid")
        return f"/internal/platform/v1/tasks/{quote(child_session_id, safe='')}{suffix}"

    @staticmethod
    def _adapter_event(child_session_id: str, event: TaskEvent) -> AdapterEvent:
        payload = dict(event.payload)
        kind: str = event.kind
        if kind == "finding":
            kind = "work_update"
            payload = {"kind": "finding", **payload}
        elif kind == "failed":
            kind = "error"
        return AdapterEvent(
            seq=event.seq,
            kind=kind,  # type: ignore[arg-type]
            source="provider" if event.kind == "thinking_summary" else "agent",
            source_ref=f"http-task:{child_session_id}:{event.seq}",
            created_at=event.created_at,
            payload=payload,
        )


def _safe_internal_origin(value: str) -> str | None:
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        if (
            parsed.scheme != "http"
            or host is None
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        if host.lower() != "localhost" and not ipaddress.ip_address(host).is_private:
            return None
    except ValueError:
        return None
    return value.rstrip("/")
