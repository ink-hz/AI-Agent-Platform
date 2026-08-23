from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
import re
from threading import Lock
import time
from typing import Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from .models import RelayEvent
from .repository import (
    ExecutionRelayConflict,
    ExecutionRelayError,
    ExecutionRelayNotFound,
    ExecutionRelayRepository,
    ExecutionRelayWorkerUnavailable,
)
from .worker_auth import (
    WorkerAuthenticationError,
    WorkerIdentity,
    WorkerRequestVerifier,
)


_NO_STORE = {"Cache-Control": "no-store"}


class _EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _LeaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptance_run_id: UUID | None = None


class _StrictRelayEvent(RelayEvent):
    model_config = ConfigDict(extra="forbid")


class _EventsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[_StrictRelayEvent] = Field(min_length=1, max_length=100)


class _TerminalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed", "cancelled", "interrupted"]


class _StopAckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["cancelled", "interrupted"]


class ExecutionWorkerRequestLimiter:
    """One process-local rolling-window bucket shared by all relay routes."""

    def __init__(
        self,
        *,
        limit: int = 120,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("execution worker limiter invalid")
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, worker_id: str) -> int | None:
        with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            bucket = self._buckets.setdefault(worker_id, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return max(1, math.ceil(bucket[0] + self._window_seconds - now))
            bucket.append(now)
            return None


@dataclass(frozen=True)
class _AuthenticatedBody:
    identity: WorkerIdentity
    body: bytes


async def _read_raw_body(request: Request, max_body_bytes: int) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_body_bytes:
            return None
        body.extend(chunk)
    return bytes(body)


def _path_with_query(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    if not isinstance(raw_path, bytes):
        raw_path = request.url.path.encode("ascii")
    path = raw_path.decode("ascii")
    query = request.scope.get("query_string", b"")
    if query:
        path += "?" + query.decode("ascii")
    return path


def _error(status_code: int, detail: str, **headers: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=status_code,
        headers={**_NO_STORE, **headers},
    )


async def _authenticate(
    request: Request,
    verifier: WorkerRequestVerifier,
    limiter: ExecutionWorkerRequestLimiter,
    max_body_bytes: int,
) -> _AuthenticatedBody | JSONResponse:
    body = await _read_raw_body(request, max_body_bytes)
    if body is None:
        return _error(413, "request body too large")
    try:
        identity = await run_in_threadpool(
            verifier.verify,
            request.method,
            _path_with_query(request),
            body,
            request.headers,
        )
    except WorkerAuthenticationError:
        return _error(401, "worker authentication failed")
    retry_after = limiter.check(identity.worker_id)
    if retry_after is not None:
        return _error(
            429,
            "worker rate limit exceeded",
            **{"Retry-After": str(retry_after)},
        )
    if request.scope.get("query_string", b""):
        return _error(422, "request validation failed")
    return _AuthenticatedBody(identity, body)


def _validated(model: type[BaseModel], body: bytes):
    try:
        value = json.loads(body.decode("utf-8"))
        return model.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
        return None


def _repository_error(error: ExecutionRelayError) -> JSONResponse:
    if isinstance(error, ExecutionRelayWorkerUnavailable):
        return _error(401, "worker authentication failed")
    if isinstance(error, ExecutionRelayNotFound):
        return _error(404, "execution relay resource not found")
    if isinstance(error, ExecutionRelayConflict):
        return _error(409, "execution relay conflict")
    return _error(503, "execution relay unavailable")


def build_execution_relay_router(
    repository: ExecutionRelayRepository,
    verifier: WorkerRequestVerifier,
    *,
    lease_seconds: int,
    max_body_bytes: int,
    requests_per_window: int = 120,
) -> APIRouter:
    limiter = ExecutionWorkerRequestLimiter(limit=requests_per_window)
    router = APIRouter(prefix="/api/v1/execution-worker")

    async def authenticated(request: Request):
        return await _authenticate(
            request, verifier, limiter, max_body_bytes
        )

    @router.post("/lease")
    async def lease(request: Request):
        authenticated_body = await authenticated(request)
        if isinstance(authenticated_body, JSONResponse):
            return authenticated_body
        parsed = _validated(_LeaseBody, authenticated_body.body)
        if (
            parsed is None
            or (
                "acceptance_run_id" in parsed.model_fields_set
                and parsed.acceptance_run_id is None
            )
        ):
            return _error(422, "request validation failed")
        acceptance_worker = re.fullmatch(
            r"relay-acceptance-[0-9a-f]{16}",
            authenticated_body.identity.worker_id,
        ) is not None
        try:
            if parsed.acceptance_run_id is None:
                if acceptance_worker:
                    return _error(403, "targeted acceptance lease required")
                result = await run_in_threadpool(
                    repository.lease,
                    authenticated_body.identity.worker_id,
                    authenticated_body.identity.allowed_agent_ids,
                    lease_seconds,
                )
            else:
                if not acceptance_worker:
                    return _error(403, "targeted acceptance lease forbidden")
                result = await run_in_threadpool(
                    repository.lease_acceptance,
                    authenticated_body.identity.worker_id,
                    authenticated_body.identity.allowed_agent_ids,
                    lease_seconds,
                    parsed.acceptance_run_id,
                )
        except ExecutionRelayError as error:
            return _repository_error(error)
        if result is None:
            return Response(status_code=204, headers=_NO_STORE)
        return JSONResponse(result.model_dump(mode="json"), headers=_NO_STORE)

    @router.post("/heartbeat")
    async def heartbeat(request: Request):
        authenticated_body = await authenticated(request)
        if isinstance(authenticated_body, JSONResponse):
            return authenticated_body
        if _validated(_EmptyBody, authenticated_body.body) is None:
            return _error(422, "request validation failed")
        try:
            run_ids = await run_in_threadpool(
                repository.heartbeat,
                authenticated_body.identity.worker_id,
                lease_seconds=lease_seconds,
            )
        except ExecutionRelayError as error:
            return _repository_error(error)
        return JSONResponse(
            {
                "stop_requests": [
                    {"run_id": str(item.run_id), "status": item.status}
                    for item in run_ids
                ]
            },
            headers=_NO_STORE,
        )

    @router.post("/runs/{run_id}/dispatched")
    async def dispatched(run_id: UUID, request: Request):
        authenticated_body = await authenticated(request)
        if isinstance(authenticated_body, JSONResponse):
            return authenticated_body
        if _validated(_EmptyBody, authenticated_body.body) is None:
            return _error(422, "request validation failed")
        try:
            await run_in_threadpool(
                repository.mark_dispatched,
                authenticated_body.identity.worker_id, run_id
            )
        except ExecutionRelayError as error:
            return _repository_error(error)
        return JSONResponse({"status": "accepted"}, headers=_NO_STORE)

    @router.post("/runs/{run_id}/events")
    async def events(run_id: UUID, request: Request):
        authenticated_body = await authenticated(request)
        if isinstance(authenticated_body, JSONResponse):
            return authenticated_body
        parsed = _validated(_EventsBody, authenticated_body.body)
        if parsed is None or any(event.run_id != run_id for event in parsed.events):
            return _error(422, "request validation failed")
        relay_events = tuple(
            RelayEvent.model_validate(event.model_dump()) for event in parsed.events
        )
        try:
            inserted = await run_in_threadpool(
                repository.append_events,
                authenticated_body.identity.worker_id, relay_events
            )
        except ExecutionRelayError as error:
            return _repository_error(error)
        return JSONResponse(
            {"accepted": len(relay_events), "inserted": inserted},
            headers=_NO_STORE,
        )

    @router.post("/runs/{run_id}/terminal")
    async def terminal(run_id: UUID, request: Request):
        authenticated_body = await authenticated(request)
        if isinstance(authenticated_body, JSONResponse):
            return authenticated_body
        parsed = _validated(_TerminalBody, authenticated_body.body)
        if parsed is None:
            return _error(422, "request validation failed")
        try:
            await run_in_threadpool(
                repository.finish,
                authenticated_body.identity.worker_id, run_id, parsed.status
            )
        except ExecutionRelayError as error:
            return _repository_error(error)
        return JSONResponse({"status": "accepted"}, headers=_NO_STORE)

    @router.post("/runs/{run_id}/stop-ack")
    async def stop_ack(run_id: UUID, request: Request):
        authenticated_body = await authenticated(request)
        if isinstance(authenticated_body, JSONResponse):
            return authenticated_body
        parsed = _validated(_StopAckBody, authenticated_body.body)
        if parsed is None:
            return _error(422, "request validation failed")
        try:
            await run_in_threadpool(
                repository.acknowledge_stop,
                authenticated_body.identity.worker_id,
                run_id,
                parsed.status,
            )
        except ExecutionRelayError as error:
            return _repository_error(error)
        return JSONResponse({"status": "accepted"}, headers=_NO_STORE)

    return router
