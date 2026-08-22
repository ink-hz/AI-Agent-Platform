from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import random
import re
import secrets
import signal
import stat
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from .acceptance_hooks import WorkerAcceptanceHooks
from .metabot_client import MetaBotClient, MetaBotRuntimeMap
from .models import RelayEvent, RelayJobPayload, RelayLease
from .repository import RelayStopRequest
from .worker_auth import WorkerRequestSigner
from .worker_store import WorkerRunRecovery, WorkerStore


_API_PREFIX = "/api/v1/execution-worker"
_CALLBACK_BODY_LIMIT = 1_048_576
_CALLBACK_HEADER_LIMIT = 16_384
_CALLBACK_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_FORCED_TERMINAL_STATES = frozenset({"cancelled", "interrupted"})
_MAX_PENDING_STOPS = 100
_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
_LOG = logging.getLogger("app.execution_relay.worker")


class CloudRelayError(RuntimeError):
    """Stable cloud-boundary failure without response or credential content."""

    def __init__(self) -> None:
        super().__init__("cloud relay request failed")


class WorkerRuntimeError(RuntimeError):
    """Stable runtime failure without job or event content."""

    def __init__(self) -> None:
        super().__init__("worker runtime failed")


class CallbackResult(Enum):
    ACCEPTED = 204
    INVALID = 400
    UNAUTHORIZED = 401
    CONFLICT = 409
    TOO_LARGE = 413


class _CoreChatBridge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    botName: str = Field(min_length=1, max_length=128)
    executionChatId: str = Field(min_length=1, max_length=256)


class _StrictCallbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    runId: UUID
    seq: int = Field(gt=0)
    type: Literal["state", "question", "file", "log", "complete", "error"]
    createdAt: AwareDatetime
    bridge: _CoreChatBridge
    payload: dict[str, object]


def _relay_event(value: _StrictCallbackEvent) -> RelayEvent:
    return RelayEvent(
        run_id=value.runId,
        seq=value.seq,
        event_type=f"agent.{value.type}",
        created_at=value.createdAt,
        payload=value.payload,
    )


class _StrictLeasePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    agent_id: str
    prompt: str
    max_turns: int = Field(ge=1, le=24)


class _StrictRelayLease(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    job_id: UUID
    payload: _StrictLeasePayload
    lease_expires_at: AwareDatetime
    cancel_requested: bool


class ExponentialBackoff:
    def __init__(
        self,
        *,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._jitter = jitter
        self._attempt = 0

    def next_delay(self) -> float:
        base = _BACKOFF_SECONDS[min(self._attempt, len(_BACKOFF_SECONDS) - 1)]
        self._attempt += 1
        factor = float(self._jitter(0.8, 1.2))
        if not 0.8 <= factor <= 1.2:
            factor = 1.0
        return base * factor

    def reset(self) -> None:
        self._attempt = 0


class SignedCloudClient:
    def __init__(
        self,
        base_url: str,
        signer: WorkerRequestSigner,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        try:
            parsed = urlsplit(base_url)
            loopback_test_url = (
                parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            )
            if (
                not isinstance(base_url, str)
                or (parsed.scheme != "https" and not loopback_test_url)
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
                or not callable(getattr(signer, "sign", None))
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise WorkerRuntimeError() from None
        self._base_url = base_url.rstrip("/")
        self._signer = signer
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _body(value: Mapping[str, object]) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    async def _post(
        self,
        path: str,
        value: Mapping[str, object],
        *,
        accepted_statuses: frozenset[int] = frozenset({200}),
    ) -> httpx.Response:
        body = self._body(value)
        try:
            headers = self._signer.sign("POST", path, body)
            response = await self._client.request(
                "POST",
                self._base_url + path,
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
            if response.status_code not in accepted_statuses:
                raise ValueError
            return response
        except Exception:
            raise CloudRelayError() from None

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, object]:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError
        return value

    async def lease(self) -> RelayLease | None:
        response = await self._post(
            f"{_API_PREFIX}/lease",
            {},
            accepted_statuses=frozenset({200, 204}),
        )
        if response.status_code == 204:
            if response.content:
                raise CloudRelayError()
            return None
        try:
            strict_lease = _StrictRelayLease.model_validate_json(
                response.content, strict=True
            )
            value = strict_lease.model_dump()
            value["lease_expires_at"] = strict_lease.lease_expires_at.astimezone(
                timezone.utc
            )
            return RelayLease.model_validate(value)
        except (TypeError, ValueError, ValidationError):
            raise CloudRelayError() from None

    async def heartbeat(self) -> tuple[RelayStopRequest, ...]:
        response = await self._post(f"{_API_PREFIX}/heartbeat", {})
        try:
            value = self._json_object(response)
            if set(value) == {"cancel_requested_run_ids"}:
                items = value["cancel_requested_run_ids"]
                if not isinstance(items, list) or any(
                    not isinstance(item, str) for item in items
                ):
                    raise ValueError
                return tuple(
                    RelayStopRequest(run_id=UUID(item), status="cancelled")
                    for item in items
                )
            if set(value) != {"stop_requests"} or not isinstance(
                value["stop_requests"], list
            ):
                raise ValueError
            requests: list[RelayStopRequest] = []
            for item in value["stop_requests"]:
                if not isinstance(item, dict) or set(item) != {"run_id", "status"}:
                    raise ValueError
                if item["status"] not in _FORCED_TERMINAL_STATES:
                    raise ValueError
                requests.append(RelayStopRequest(UUID(item["run_id"]), item["status"]))
            return tuple(requests)
        except Exception:
            raise CloudRelayError() from None

    async def mark_dispatched(self, run_id: UUID) -> None:
        response = await self._post(f"{_API_PREFIX}/runs/{run_id}/dispatched", {})
        self._require_accepted(response)

    async def upload_events(
        self, run_id: UUID, events: Sequence[RelayEvent]
    ) -> None:
        if not events or any(event.run_id != run_id for event in events):
            raise CloudRelayError()
        response = await self._post(
            f"{_API_PREFIX}/runs/{run_id}/events",
            {"events": [event.model_dump(mode="json") for event in events]},
        )
        try:
            value = self._json_object(response)
            if (
                set(value) != {"accepted", "inserted"}
                or isinstance(value["accepted"], bool)
                or not isinstance(value["accepted"], int)
                or value["accepted"] != len(events)
                or isinstance(value["inserted"], bool)
                or not isinstance(value["inserted"], int)
                or not 0 <= value["inserted"] <= len(events)
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise CloudRelayError() from None

    async def finish(self, run_id: UUID, status: str) -> None:
        if status not in _TERMINAL_STATES:
            raise CloudRelayError()
        response = await self._post(
            f"{_API_PREFIX}/runs/{run_id}/terminal", {"status": status}
        )
        self._require_accepted(response)

    def _require_accepted(self, response: httpx.Response) -> None:
        try:
            value = self._json_object(response)
            if value != {"status": "accepted"}:
                raise ValueError
        except (TypeError, ValueError):
            raise CloudRelayError() from None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass
class _RunContext:
    agent_id: str
    metabot_accepted: bool
    cloud_dispatched: bool
    terminal_status: str | None = None
    cancel_sent: bool = False
    transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WorkerRuntime:
    def __init__(
        self,
        *,
        worker_id: str,
        cloud: Any,
        store: WorkerStore,
        runtime_map: MetaBotRuntimeMap,
        metabot: MetaBotClient,
        callback_port: int,
        heartbeat_interval: float = 15.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        jitter: Callable[[float, float], float] = random.uniform,
        logger: logging.Logger = _LOG,
        acceptance_hooks: Any | None = None,
    ) -> None:
        if (
            not isinstance(worker_id, str)
            or not worker_id
            or isinstance(callback_port, bool)
            or not isinstance(callback_port, int)
            or not 0 <= callback_port <= 65535
            or heartbeat_interval <= 0
        ):
            raise WorkerRuntimeError()
        self.worker_id = worker_id
        self.cloud = cloud
        self.store = store
        self.runtime_map = runtime_map
        self.metabot = metabot
        self.callback_port = callback_port
        self.heartbeat_interval = float(heartbeat_interval)
        self.sleep = sleep
        self.token_factory = token_factory
        self.jitter = jitter
        self.logger = logger
        self.acceptance_hooks = acceptance_hooks
        self.shutdown_event = asyncio.Event()
        self.stop_event = asyncio.Event()
        self.callback_ready = asyncio.Event()
        self._runs: dict[UUID, _RunContext] = {}
        self._pending_stops: dict[UUID, str] = {}
        self._state_lock = asyncio.Lock()
        self._lease_lock = asyncio.Lock()
        self._upload_lock = asyncio.Lock()

    def stop(self) -> None:
        self.shutdown_event.set()
        self.stop_event.set()

    def begin_shutdown(self) -> None:
        self.shutdown_event.set()

    def recover_run(
        self,
        run_id: UUID,
        agent_id: str,
        *,
        terminal_status: str | None = None,
        cloud_dispatched: bool = True,
        metabot_accepted: bool = True,
    ) -> None:
        if (
            not isinstance(run_id, UUID)
            or not isinstance(agent_id, str)
            or (terminal_status is not None and terminal_status not in _TERMINAL_STATES)
        ):
            raise WorkerRuntimeError()
        self._runs.setdefault(
            run_id,
            _RunContext(
                agent_id=agent_id,
                metabot_accepted=metabot_accepted,
                cloud_dispatched=cloud_dispatched,
                terminal_status=terminal_status,
            ),
        )

    async def recover_local_state(self) -> None:
        try:
            rows = await self._store_call("recoverable_runs")
            for row in rows:
                if (
                    not isinstance(row, WorkerRunRecovery)
                    or not isinstance(row.run_id, UUID)
                    or not isinstance(row.agent_id, str)
                    or not isinstance(row.state, str)
                    or not isinstance(row.has_events, bool)
                ):
                    raise WorkerRuntimeError()
                run_id = row.run_id
                accepted = (
                    row.dispatched_at is not None
                    or row.has_events
                    or row.state
                    in {"dispatched", "running", "completed", "failed"}
                )
                if row.state == "dispatching":
                    await self._store_call("mark_terminal", run_id, "interrupted")
                    self.recover_run(
                        run_id,
                        row.agent_id,
                        terminal_status="interrupted",
                        cloud_dispatched=not accepted,
                        metabot_accepted=accepted,
                    )
                elif row.state in {"dispatched", "running"}:
                    self.recover_run(
                        run_id, row.agent_id, cloud_dispatched=False
                    )
                elif row.state in _TERMINAL_STATES:
                    self.recover_run(
                        run_id,
                        row.agent_id,
                        terminal_status=row.state,
                        cloud_dispatched=not accepted,
                        metabot_accepted=accepted,
                    )
                else:
                    raise WorkerRuntimeError()
        except Exception as error:
            self._safe_log("recovery_failed", error)
            raise WorkerRuntimeError() from None

    def _safe_log(
        self,
        state: str,
        error: BaseException,
        *,
        run_id: UUID | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.logger.warning(
            "worker_id=%s run_id=%s agent_id=%s state=%s error_class=%s",
            self.worker_id,
            str(run_id) if run_id is not None else "-",
            agent_id or "-",
            state,
            type(error).__name__,
        )

    async def _store_call(self, method: str, *args: object) -> Any:
        return await asyncio.to_thread(getattr(self.store, method), *args)

    async def lease_once(self) -> bool:
        async with self._lease_lock:
            async with self._state_lock:
                at_capacity = bool(self._runs)
            if at_capacity:
                return True
            try:
                lease = await self.cloud.lease()
            except Exception as error:
                self._safe_log("lease_failed", error)
                return False
            if lease is None:
                return True
            run_id = lease.payload.run_id
            agent_id = lease.payload.agent_id
            try:
                port = self.runtime_map.port_for(agent_id)
                token = self.token_factory()
                if (
                    not isinstance(token, str)
                    or _CALLBACK_TOKEN.fullmatch(token) is None
                ):
                    raise WorkerRuntimeError()
                await self._store_call("record_lease", lease, port, token)
                await self._store_call("mark_dispatching", run_id)
                context = _RunContext(agent_id, False, False)
                async with self._state_lock:
                    self._runs[run_id] = context
                async with context.transition_lock:
                    async with self._state_lock:
                        pending_status = self._pending_stops.pop(run_id, None)
                        cancellation_observed = lease.cancel_requested
                    if (
                        pending_status is not None
                        or cancellation_observed
                        or self.shutdown_event.is_set()
                    ):
                        status = pending_status or (
                            "cancelled" if cancellation_observed else "interrupted"
                        )
                        await self._store_call("mark_terminal", run_id, status)
                        context.terminal_status = status
                        return True
                    callback_url = (
                        f"http://127.0.0.1:{self.callback_port}/callbacks/"
                        f"{run_id}/{token}"
                    )
                    dispatch_task = asyncio.create_task(
                        self._dispatch_run(
                            lease.payload,
                            callback_url,
                            context,
                        )
                    )
                    cancelled = False
                    while not dispatch_task.done():
                        try:
                            await asyncio.shield(dispatch_task)
                        except asyncio.CancelledError:
                            cancelled = True
                            self.begin_shutdown()
                    result = dispatch_task.result()
                    if cancelled:
                        raise asyncio.CancelledError
                    return result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._safe_log(
                    "dispatch_interrupted",
                    error,
                    run_id=run_id,
                    agent_id=agent_id,
                )
                context = self._runs.get(run_id)
                if context is not None and context.terminal_status is None:
                    try:
                        await self._store_call("mark_terminal", run_id, "interrupted")
                        context.terminal_status = "interrupted"
                    except Exception as store_error:
                        self._safe_log(
                            "interrupt_commit_failed",
                            store_error,
                            run_id=run_id,
                            agent_id=agent_id,
                        )
                return False

    async def _dispatch_run(
        self,
        payload: RelayJobPayload,
        callback_url: str,
        context: _RunContext,
    ) -> bool:
        run_id = payload.run_id
        agent_id = payload.agent_id
        try:
            if self.acceptance_hooks is not None:
                self.acceptance_hooks.before_metabot_post(run_id)
            await asyncio.to_thread(self.metabot.start_run, payload, callback_url)
            if self.acceptance_hooks is not None:
                await self.acceptance_hooks.after_metabot_post(run_id)
            context.metabot_accepted = True
            await self._store_call("mark_dispatched", run_id)
        except Exception as error:
            self._safe_log(
                "dispatch_interrupted",
                error,
                run_id=run_id,
                agent_id=agent_id,
            )
            if context.metabot_accepted and not context.cancel_sent:
                try:
                    await asyncio.to_thread(
                        self.metabot.cancel_run, run_id, context.agent_id
                    )
                    context.cancel_sent = True
                except Exception as cancel_error:
                    self._safe_log(
                        "shutdown_cancel_failed",
                        cancel_error,
                        run_id=run_id,
                        agent_id=agent_id,
                    )
            await self._commit_interrupted(run_id, context)
            return False
        if self.shutdown_event.is_set():
            if context.terminal_status is not None:
                return True
            if not context.cancel_sent:
                try:
                    await asyncio.to_thread(
                        self.metabot.cancel_run, run_id, context.agent_id
                    )
                    context.cancel_sent = True
                except Exception as error:
                    self._safe_log(
                        "shutdown_cancel_failed",
                        error,
                        run_id=run_id,
                        agent_id=agent_id,
                    )
            await self._commit_interrupted(run_id, context)
            return True
        try:
            await self.cloud.mark_dispatched(run_id)
            context.cloud_dispatched = True
            return True
        except Exception as error:
            self._safe_log(
                "cloud_dispatch_ack_failed",
                error,
                run_id=run_id,
                agent_id=agent_id,
            )
            return False

    async def _commit_interrupted(
        self, run_id: UUID, context: _RunContext
    ) -> None:
        if context.terminal_status is not None:
            return
        try:
            await self._store_call("mark_terminal", run_id, "interrupted")
            context.terminal_status = "interrupted"
        except Exception as error:
            self._safe_log(
                "interrupt_commit_failed",
                error,
                run_id=run_id,
                agent_id=context.agent_id,
            )

    async def upload_once(self) -> bool:
        async with self._upload_lock:
            success = True
            async with self._state_lock:
                runs = tuple(self._runs.items())
            for run_id, context in runs:
                try:
                    if context.metabot_accepted and not context.cloud_dispatched:
                        await self.cloud.mark_dispatched(run_id)
                        context.cloud_dispatched = True
                    events = await self._store_call("contiguous_outbox", run_id, 100)
                    if events:
                        if (
                            self.acceptance_hooks is not None
                            and context.terminal_status is not None
                        ):
                            await self.acceptance_hooks.before_terminal_upload(run_id)
                        await self.cloud.upload_events(run_id, events)
                        await self._store_call("mark_delivered", run_id, events[-1].seq)
                    remaining = await self._store_call("contiguous_outbox", run_id, 1)
                    if context.terminal_status is not None and not remaining:
                        await self.cloud.finish(run_id, context.terminal_status)
                        async with self._state_lock:
                            if self._runs.get(run_id) is context:
                                del self._runs[run_id]
                except Exception as error:
                    success = False
                    self._safe_log(
                        "upload_failed",
                        error,
                        run_id=run_id,
                        agent_id=context.agent_id,
                    )
            return success

    async def heartbeat_once(self) -> bool:
        try:
            cancel_ids = await self.cloud.heartbeat()
        except Exception as error:
            self._safe_log("heartbeat_failed", error)
            return False
        for stop_request in cancel_ids:
            if isinstance(stop_request, UUID):
                stop_request = RelayStopRequest(
                    run_id=stop_request, status="cancelled"
                )
            if not isinstance(stop_request, RelayStopRequest):
                return False
            if stop_request.status not in _FORCED_TERMINAL_STATES:
                return False
            run_id = stop_request.run_id
            async with self._state_lock:
                context = self._runs.get(run_id)
                if context is None:
                    if (
                        run_id not in self._pending_stops
                        and len(self._pending_stops) >= _MAX_PENDING_STOPS
                    ):
                        return False
                    self._pending_stops[run_id] = stop_request.status
            if context is None:
                continue
            async with context.transition_lock:
                try:
                    if context.metabot_accepted and not context.cancel_sent:
                        await asyncio.to_thread(
                            self.metabot.cancel_run, run_id, context.agent_id
                        )
                        context.cancel_sent = True
                    await self._store_call(
                        "reconcile_forced_terminal", run_id, stop_request.status
                    )
                    context.terminal_status = stop_request.status
                except Exception as error:
                    self._safe_log(
                        "cancel_failed",
                        error,
                        run_id=run_id,
                        agent_id=context.agent_id,
                    )
                    return False
        return True

    async def accept_callback(
        self, run_id: UUID, token: str, body: bytes
    ) -> CallbackResult:
        if not isinstance(body, bytes) or len(body) > _CALLBACK_BODY_LIMIT:
            return CallbackResult.TOO_LARGE
        if (
            not isinstance(run_id, UUID)
            or not isinstance(token, str)
            or _CALLBACK_TOKEN.fullmatch(token) is None
        ):
            return CallbackResult.UNAUTHORIZED
        try:
            if not await self._store_call("callback_token_matches", run_id, token):
                return CallbackResult.UNAUTHORIZED
            strict_event = _StrictCallbackEvent.model_validate_json(
                body, strict=True
            )
            if strict_event.runId != run_id:
                return CallbackResult.INVALID
            event = _relay_event(strict_event)
            terminal = self._terminal_status(event)
            async with self._state_lock:
                context = self._runs.get(run_id)
            if context is None:
                if terminal is None:
                    await self._store_call("append_event", event)
                else:
                    await self._store_call(
                        "append_terminal_event", event, terminal
                    )
                async with self._state_lock:
                    context = self._runs.get(run_id)
                    if context is None:
                        context = _RunContext("", True, False)
                        self._runs[run_id] = context
                    else:
                        context.metabot_accepted = True
                if terminal is not None:
                    context.terminal_status = terminal
                return CallbackResult.ACCEPTED
            if terminal is None:
                await self._store_call("append_event", event)
            else:
                await self._store_call(
                    "append_terminal_event", event, terminal
                )
            async with self._state_lock:
                context.metabot_accepted = True
                if (
                    terminal is not None
                    and context.terminal_status not in _FORCED_TERMINAL_STATES
                ):
                    context.terminal_status = terminal
            return CallbackResult.ACCEPTED
        except (
            UnicodeError,
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
        ):
            return CallbackResult.INVALID
        except Exception:
            return CallbackResult.CONFLICT

    @staticmethod
    def _terminal_status(event: RelayEvent) -> str | None:
        return {
            "agent.complete": "completed",
            "agent.error": "failed",
        }.get(event.event_type)

    async def interrupt_active(self) -> None:
        async with self._state_lock:
            runs = tuple(self._runs.items())
        for run_id, context in runs:
            async with context.transition_lock:
                if context.terminal_status is not None:
                    continue
                if context.metabot_accepted and not context.cancel_sent:
                    try:
                        await asyncio.to_thread(
                            self.metabot.cancel_run, run_id, context.agent_id
                        )
                        context.cancel_sent = True
                    except Exception as error:
                        self._safe_log(
                            "shutdown_cancel_failed",
                            error,
                            run_id=run_id,
                            agent_id=context.agent_id,
                        )
                try:
                    await self._store_call("mark_terminal", run_id, "interrupted")
                    context.terminal_status = "interrupted"
                except Exception as error:
                    self._safe_log(
                        "shutdown_commit_failed",
                        error,
                        run_id=run_id,
                        agent_id=context.agent_id,
                    )

    async def pause(self, seconds: float) -> None:
        if self.shutdown_event.is_set() or self.stop_event.is_set():
            return
        if self.sleep is asyncio.sleep:
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass
        else:
            await self.sleep(seconds)


async def lease_loop(runtime: WorkerRuntime) -> None:
    backoff = ExponentialBackoff(jitter=runtime.jitter)
    while not runtime.shutdown_event.is_set():
        succeeded = await runtime.lease_once()
        if succeeded:
            backoff.reset()
            await runtime.pause(0.25)
        else:
            await runtime.pause(backoff.next_delay())


async def upload_loop(runtime: WorkerRuntime) -> None:
    backoff = ExponentialBackoff(jitter=runtime.jitter)
    while not runtime.shutdown_event.is_set():
        succeeded = await runtime.upload_once()
        if succeeded:
            backoff.reset()
            await runtime.pause(0.25)
        else:
            await runtime.pause(backoff.next_delay())


async def heartbeat_loop(runtime: WorkerRuntime) -> None:
    backoff = ExponentialBackoff(jitter=runtime.jitter)
    while not runtime.shutdown_event.is_set():
        succeeded = await runtime.heartbeat_once()
        if succeeded:
            backoff.reset()
            await runtime.pause(runtime.heartbeat_interval)
        else:
            await runtime.pause(backoff.next_delay())


async def _send_callback_response(
    writer: asyncio.StreamWriter, result: CallbackResult
) -> None:
    reason = {
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        409: "Conflict",
        413: "Content Too Large",
    }[result.value]
    writer.write(
        f"HTTP/1.1 {result.value} {reason}\r\n"
        "Content-Length: 0\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n".encode("ascii")
    )
    await writer.drain()


async def _handle_callback_connection(
    runtime: WorkerRuntime,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    result = CallbackResult.INVALID
    try:
        header_block = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10.0)
        if len(header_block) > _CALLBACK_HEADER_LIMIT:
            raise ValueError
        lines = header_block[:-4].split(b"\r\n")
        request_line = lines[0].decode("ascii").split(" ")
        if (
            len(request_line) != 3
            or request_line[0] != "POST"
            or request_line[2] != "HTTP/1.1"
        ):
            raise ValueError
        target = request_line[1]
        if "?" in target or "#" in target:
            raise ValueError
        parts = target.split("/")
        if len(parts) != 4 or parts[1] != "callbacks":
            raise ValueError
        run_id = UUID(parts[2])
        if str(run_id) != parts[2]:
            raise ValueError
        token = parts[3]
        headers: dict[str, str] = {}
        for raw_line in lines[1:]:
            name, separator, value = raw_line.partition(b":")
            if not separator:
                raise ValueError
            key = name.decode("ascii").strip().lower()
            if not key or key in headers:
                raise ValueError
            headers[key] = value.decode("ascii").strip()
        if "transfer-encoding" in headers:
            raise ValueError
        content_type = (
            headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if content_type != "application/json":
            raise ValueError
        raw_length = headers.get("content-length")
        if raw_length is None or not raw_length.isdigit():
            raise ValueError
        length = int(raw_length)
        if length > _CALLBACK_BODY_LIMIT:
            result = CallbackResult.TOO_LARGE
        else:
            body = await asyncio.wait_for(reader.readexactly(length), 10.0)
            result = await runtime.accept_callback(run_id, token, body)
    except (
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        asyncio.TimeoutError,
    ):
        result = CallbackResult.INVALID
    except (UnicodeError, ValueError):
        result = CallbackResult.INVALID
    try:
        await _send_callback_response(writer, result)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def callback_server(runtime: WorkerRuntime) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: _handle_callback_connection(
            runtime, reader, writer
        ),
        host="127.0.0.1",
        port=runtime.callback_port,
        limit=_CALLBACK_HEADER_LIMIT + 4,
    )
    socket = server.sockets[0]
    runtime.callback_port = int(socket.getsockname()[1])
    runtime.callback_ready.set()
    async with server:
        await runtime.stop_event.wait()


async def run_worker(runtime: WorkerRuntime) -> None:
    loop = asyncio.get_running_loop()
    shutdown_started = asyncio.Event()
    installed: list[signal.Signals] = []
    callback_task: asyncio.Task[None] | None = None
    ready_task: asyncio.Task[bool] | None = None
    worker_tasks: set[asyncio.Task[None]] = set()
    shutdown_tasks: set[asyncio.Task[Any]] = set()

    async def shutdown() -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        runtime.begin_shutdown()
        await runtime.interrupt_active()
        await runtime.upload_once()
        runtime.stop()

    def request_shutdown() -> None:
        task = asyncio.create_task(shutdown())
        shutdown_tasks.add(task)

    try:
        await runtime.recover_local_state()
        for name in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(name, request_shutdown)
                installed.append(name)
            except (NotImplementedError, RuntimeError):
                pass
        callback_task = asyncio.create_task(callback_server(runtime))
        ready_task = asyncio.create_task(runtime.callback_ready.wait())
        done, _pending = await asyncio.wait(
            {callback_task, ready_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if callback_task in done:
            await callback_task
        ready_task.cancel()
        await asyncio.gather(ready_task, return_exceptions=True)
        ready_task = None
        worker_tasks.update(
            {
                asyncio.create_task(lease_loop(runtime)),
                asyncio.create_task(upload_loop(runtime)),
                asyncio.create_task(heartbeat_loop(runtime)),
            }
        )
        await asyncio.shield(asyncio.gather(callback_task, *worker_tasks))
    finally:
        async def cleanup() -> None:
            runtime.begin_shutdown()
            if ready_task is not None and not ready_task.done():
                ready_task.cancel()
            for task in worker_tasks:
                if not task.done():
                    task.cancel()
            if ready_task is not None:
                await asyncio.gather(ready_task, return_exceptions=True)
            if worker_tasks:
                await asyncio.gather(*worker_tasks, return_exceptions=True)
            if shutdown_tasks:
                await asyncio.gather(
                    *tuple(shutdown_tasks), return_exceptions=True
                )
            await runtime.interrupt_active()
            await runtime.upload_once()
            runtime.stop()
            if callback_task is not None:
                await asyncio.gather(callback_task, return_exceptions=True)
            for name in installed:
                try:
                    loop.remove_signal_handler(name)
                except Exception as error:
                    runtime._safe_log("signal_cleanup_failed", error)
            close = getattr(runtime.cloud, "aclose", None)
            if callable(close):
                await close()
            close_hooks = getattr(runtime.acceptance_hooks, "close", None)
            if callable(close_hooks):
                close_hooks()

        cleanup_task = asyncio.create_task(cleanup())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        cleanup_task.result()


def _read_owner_only_bytes(path: Path) -> bytes:
    parent_descriptor: int | None = None
    file_descriptor: int | None = None
    value: bytes | None = None
    failed = False
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError
        common_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        parent_descriptor = os.open(
            candidate.parent,
            common_flags | no_follow | getattr(os, "O_DIRECTORY", 0),
        )
        parent = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.geteuid()
        ):
            raise ValueError
        file_descriptor = os.open(
            candidate.name,
            common_flags | no_follow,
            dir_fd=parent_descriptor,
        )
        current = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
            or current.st_uid != os.geteuid()
            or current.st_size > 16_384
        ):
            raise ValueError
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(file_descriptor, min(4096, 16_385 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 16_384:
                raise ValueError
        value = b"".join(chunks)
        if not value:
            raise ValueError
    except (OSError, TypeError, ValueError):
        failed = True
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except Exception:
                failed = True
        if parent_descriptor is not None:
            try:
                os.close(parent_descriptor)
            except Exception:
                failed = True
    if failed or value is None:
        raise WorkerRuntimeError() from None
    return value


def _owner_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        raw = _read_owner_only_bytes(path)
        if len(raw) == 32:
            return Ed25519PrivateKey.from_private_bytes(raw)
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError
        return key
    except Exception:
        raise WorkerRuntimeError() from None


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise WorkerRuntimeError()
    return value


def build_runtime_from_environment() -> WorkerRuntime:
    try:
        worker_id = _required_environment("PLATFORM_WORKER_ID")
        key_id = _required_environment("PLATFORM_WORKER_KEY_ID")
        private_key = _owner_private_key(
            Path(_required_environment("PLATFORM_WORKER_PRIVATE_KEY_FILE"))
        )
        store = WorkerStore.from_dsn_file(
            Path(_required_environment("PLATFORM_WORKER_DATABASE_URL_FILE"))
        )
        callback_port = int(_required_environment("PLATFORM_WORKER_CALLBACK_PORT"))
        if not 1 <= callback_port <= 65535:
            raise WorkerRuntimeError()
        runtime_map = MetaBotRuntimeMap.from_contract(
            Path(_required_environment("PLATFORM_METABOT_RUNTIME_CONTRACT"))
        )
        metabot = MetaBotClient(
            runtime_map,
            Path(_required_environment("PLATFORM_METABOT_API_SECRET_FILE")),
        )
        signer = WorkerRequestSigner(worker_id, key_id, private_key)
        cloud = SignedCloudClient(
            _required_environment("PLATFORM_WORKER_CLOUD_URL"), signer
        )
        return WorkerRuntime(
            worker_id=worker_id,
            cloud=cloud,
            store=store,
            runtime_map=runtime_map,
            metabot=metabot,
            callback_port=callback_port,
            acceptance_hooks=WorkerAcceptanceHooks.from_environment(),
        )
    except WorkerRuntimeError:
        raise
    except Exception:
        raise WorkerRuntimeError() from None


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker(build_runtime_from_environment()))
    except (KeyboardInterrupt, WorkerRuntimeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
