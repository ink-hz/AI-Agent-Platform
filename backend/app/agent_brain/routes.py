from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from datetime import datetime
import hmac
import json
from typing import Annotated, AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator

from app.control_plane.auth import AuthSecrets
from app.control_plane.models import AuthContext

from .authorization import AgentUseAuthorizationUnavailable
from .repository import (
    MissionEvent,
    MissionRecord,
    MissionRepositoryConflict,
    MissionRepositoryError,
    MissionRepositoryNotFound,
    TERMINAL_MISSION_STATUSES,
)


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_SSE_HEADERS = {
    **_NO_STORE,
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}
_MAX_INPUT_BYTES = 32 * 1024


class MissionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str

    @field_validator("text")
    @classmethod
    def _visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Mission text required")
        return value


class MissionCursorCodec:
    """Authenticated, opaque, owner-bound keyset cursor."""

    def __init__(self, secrets: AuthSecrets) -> None:
        if not isinstance(secrets, AuthSecrets):
            raise ValueError("Mission cursor secrets required")
        self._secrets = secrets

    @staticmethod
    def _encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        if not isinstance(value, str) or not value or "=" in value:
            raise ValueError
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )

    def issue(self, owner: UUID, created_at: datetime, mission_id: UUID) -> str:
        if (
            not isinstance(owner, UUID)
            or not isinstance(mission_id, UUID)
            or not isinstance(created_at, datetime)
            or created_at.tzinfo is None
        ):
            raise ValueError("Mission cursor input invalid")
        payload = json.dumps(
            {
                "created_at": created_at.isoformat(),
                "key_version": self._secrets.key_version,
                "mission_id": str(mission_id),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = self._secrets.sign_mission_cursor(owner, payload)
        return self._encode(payload + signature)

    def read(self, owner: UUID, value: str) -> tuple[datetime, UUID]:
        try:
            raw = self._decode(value)
            if len(raw) <= 32:
                raise ValueError
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(
                signature, self._secrets.sign_mission_cursor(owner, payload)
            ):
                raise ValueError
            document = json.loads(payload)
            if set(document) != {
                "created_at",
                "key_version",
                "mission_id",
            }:
                raise ValueError
            if document["key_version"] != self._secrets.key_version:
                raise ValueError
            created_at = datetime.fromisoformat(document["created_at"])
            if created_at.tzinfo is None:
                raise ValueError
            return created_at, UUID(document["mission_id"])
        except (
            AttributeError,
            binascii.Error,
            TypeError,
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            raise ValueError("Mission cursor invalid") from None


class MissionStreamBusy(RuntimeError):
    pass


class MissionStreamLimiter:
    """Bound per-owner stream counts without buffering event payloads."""

    def __init__(
        self,
        *,
        max_per_owner: int = 3,
        max_per_mission: int = 2,
        max_global: int = 200,
    ) -> None:
        limits = (max_per_owner, max_per_mission, max_global)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in limits):
            raise ValueError("Mission stream limit invalid")
        if not (
            1 <= max_per_mission <= max_per_owner <= max_global <= 10_000
        ):
            raise ValueError("Mission stream limit invalid")
        self._max_per_owner = max_per_owner
        self._max_per_mission = max_per_mission
        self._max_global = max_global
        self._owner_counts: dict[UUID, int] = {}
        self._mission_counts: dict[tuple[UUID, UUID], int] = {}
        self._total = 0

    def acquire(self, owner: UUID, mission_id: UUID) -> bool:
        key = (owner, mission_id)
        owner_count = self._owner_counts.get(owner, 0)
        mission_count = self._mission_counts.get(key, 0)
        if (
            owner_count >= self._max_per_owner
            or mission_count >= self._max_per_mission
            or self._total >= self._max_global
        ):
            return False
        self._owner_counts[owner] = owner_count + 1
        self._mission_counts[key] = mission_count + 1
        self._total += 1
        return True

    def release(self, owner: UUID, mission_id: UUID) -> None:
        key = (owner, mission_id)
        mission_count = self._mission_counts.get(key, 0)
        if mission_count <= 0:
            return
        if mission_count == 1:
            self._mission_counts.pop(key, None)
        else:
            self._mission_counts[key] = mission_count - 1
        owner_count = self._owner_counts.get(owner, 0)
        if owner_count <= 1:
            self._owner_counts.pop(owner, None)
        else:
            self._owner_counts[owner] = owner_count - 1
        self._total -= 1

    def active(self, owner: UUID) -> int:
        return self._owner_counts.get(owner, 0)

    def active_mission(self, owner: UUID, mission_id: UUID) -> int:
        return self._mission_counts.get((owner, mission_id), 0)

    def active_total(self) -> int:
        return self._total

    @asynccontextmanager
    async def slot(self, owner: UUID, mission_id: UUID):
        if not self.acquire(owner, mission_id):
            raise MissionStreamBusy()
        try:
            yield
        finally:
            self.release(owner, mission_id)


class _ReservedStreamingResponse(StreamingResponse):
    def __init__(
        self,
        *args,
        limiter: MissionStreamLimiter,
        owner: UUID,
        mission_id: UUID,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._limiter = limiter
        self._owner = owner
        self._mission_id = mission_id

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._limiter.release(self._owner, self._mission_id)


def _auth_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication required", headers=_NO_STORE)
    return context


def _ensure_writable(context: AuthContext) -> None:
    if context.hard_stale_read_only:
        raise HTTPException(503, "account is read only", headers=_NO_STORE)


def _mission_payload(mission: MissionRecord) -> dict[str, object]:
    return {
        "mission_id": str(mission.mission_id),
        "mode": mission.mode,
        "direct_agent_id": mission.direct_agent_id,
        "status": mission.status,
        "cancel_requested": mission.cancel_requested,
        "row_version": mission.row_version,
        "created_at": mission.created_at.isoformat(),
        "updated_at": mission.updated_at.isoformat(),
        "terminal_at": (
            mission.terminal_at.isoformat() if mission.terminal_at else None
        ),
        "prompt": mission.prompt,
        "content_available": mission.content_available,
    }


def _event_payload(event: MissionEvent) -> dict[str, object]:
    return {
        "event_id": str(event.event_id),
        "mission_id": str(event.mission_id),
        "run_id": str(event.run_id) if event.run_id else None,
        "seq": event.seq,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def _sse_event(event: MissionEvent) -> str:
    data = json.dumps(
        _event_payload(event),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"id: {event.seq}\nevent: mission\ndata: {data}\n\n"


def _repository_http_error(error: MissionRepositoryError) -> HTTPException:
    if isinstance(error, MissionRepositoryNotFound):
        return HTTPException(404, "Mission not found", headers=_NO_STORE)
    if isinstance(error, MissionRepositoryConflict):
        return HTTPException(409, "idempotency conflict", headers=_NO_STORE)
    return HTTPException(503, "Mission service unavailable", headers=_NO_STORE)


def _parse_idempotency_key(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(422, "Idempotency-Key must be a UUID", headers=_NO_STORE) from None


def _validate_input_bytes(text: str) -> None:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise HTTPException(422, "Mission text invalid", headers=_NO_STORE) from None
    if size > _MAX_INPUT_BYTES:
        raise HTTPException(413, "Mission text exceeds 32 KiB", headers=_NO_STORE)


async def mission_event_stream(
    repository,
    owner: UUID,
    mission_id: UUID,
    *,
    after: int,
    is_disconnected,
    limiter: MissionStreamLimiter,
    heartbeat_seconds: float = 15,
    poll_seconds: float = 1,
    slot_reserved: bool = False,
) -> AsyncIterator[str]:
    if heartbeat_seconds <= 0 or poll_seconds < 0:
        raise ValueError("Mission stream timing invalid")
    if not slot_reserved and not limiter.acquire(owner, mission_id):
        raise MissionStreamBusy()
    cursor = after
    last_heartbeat = 0.0
    try:
        while True:
            if await is_disconnected():
                return
            try:
                events = await asyncio.to_thread(
                    repository.events_after,
                    owner,
                    mission_id,
                    after=cursor,
                    limit=100,
                )
                mission = await asyncio.to_thread(
                    repository.mission_for_owner, owner, mission_id
                )
            except MissionRepositoryError:
                return
            for event in events:
                cursor = event.seq
                yield _sse_event(event)
            if mission.status in TERMINAL_MISSION_STATUSES:
                try:
                    terminal_tail = await asyncio.to_thread(
                        repository.events_after,
                        owner,
                        mission_id,
                        after=cursor,
                        limit=100,
                    )
                except MissionRepositoryError:
                    return
                for event in terminal_tail:
                    cursor = event.seq
                    yield _sse_event(event)
                if len(terminal_tail) < 100:
                    return
                continue
            if events:
                continue
            now = asyncio.get_running_loop().time()
            if last_heartbeat == 0.0 or now - last_heartbeat >= heartbeat_seconds:
                last_heartbeat = now
                yield f": heartbeat {int(now)}\n\n"
            await asyncio.sleep(poll_seconds)
    finally:
        if not slot_reserved:
            limiter.release(owner, mission_id)


def build_agent_brain_router(
    mission_repository,
    agent_use_authorization,
    *,
    cursor_codec: MissionCursorCodec,
    heartbeat_seconds: float = 15,
    poll_seconds: float = 1,
    max_streams_per_owner: int = 3,
    max_streams_per_mission: int = 2,
    max_streams_global: int = 200,
) -> APIRouter:
    router = APIRouter(tags=["agent-brain"])
    limiter = MissionStreamLimiter(
        max_per_owner=max_streams_per_owner,
        max_per_mission=max_streams_per_mission,
        max_global=max_streams_global,
    )

    def permitted(owner: UUID):
        try:
            return tuple(
                agent_use_authorization.permitted_agents_for_user_id(owner)
            )
        except AgentUseAuthorizationUnavailable:
            raise HTTPException(
                503, "Agent catalog unavailable", headers=_NO_STORE
            ) from None

    @router.get("/api/v1/catalog/agents")
    async def catalog(request: Request, response: Response):
        context = _auth_context(request)
        response.headers.update(_NO_STORE)
        return {
            "agents": [
                card.model_dump(mode="json")
                for card in await asyncio.to_thread(
                    permitted, context.internal_user_id
                )
            ]
        }

    @router.get("/api/v1/brain/missions")
    async def list_missions(
        request: Request,
        response: Response,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        before: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    ):
        context = _auth_context(request)
        try:
            boundary = (
                cursor_codec.read(context.internal_user_id, before)
                if before is not None
                else None
            )
        except ValueError:
            raise HTTPException(
                422, "Mission cursor invalid", headers=_NO_STORE
            ) from None
        try:
            rows = await asyncio.to_thread(
                mission_repository.list_missions_for_owner,
                context.internal_user_id,
                limit=limit + 1,
                before=boundary,
            )
        except MissionRepositoryError as error:
            raise _repository_http_error(error) from None
        visible = rows[:limit]
        next_cursor = None
        if len(rows) > limit and visible:
            last = visible[-1]
            next_cursor = cursor_codec.issue(
                context.internal_user_id, last.created_at, last.mission_id
            )
        response.headers.update(_NO_STORE)
        return {
            "items": [_mission_payload(mission) for mission in visible],
            "next_cursor": next_cursor,
        }

    async def create(
        context: AuthContext,
        response: Response,
        body: MissionBody,
        idempotency_key: str | None,
        *,
        mode: str,
        direct_agent_id: str | None,
    ):
        _ensure_writable(context)
        request_id = _parse_idempotency_key(idempotency_key)
        _validate_input_bytes(body.text)
        if direct_agent_id is not None:
            cards = await asyncio.to_thread(
                permitted, context.internal_user_id
            )
            if direct_agent_id not in {card.agent_id for card in cards}:
                raise HTTPException(403, "Agent use denied", headers=_NO_STORE)
        try:
            result = await asyncio.to_thread(
                mission_repository.create_mission_for_api,
                context.internal_user_id,
                request_id,
                body.text,
                mode=mode,
                direct_agent_id=direct_agent_id,
            )
        except MissionRepositoryError as error:
            raise _repository_http_error(error) from None
        response.status_code = 201 if result.created else 200
        response.headers.update(_NO_STORE)
        return _mission_payload(result.mission)

    @router.post("/api/v1/brain/missions", status_code=201)
    async def create_brain_mission(
        body: MissionBody,
        request: Request,
        response: Response,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        return await create(
            _auth_context(request),
            response,
            body,
            idempotency_key,
            mode="brain",
            direct_agent_id=None,
        )

    @router.post("/api/v1/agents/{agent_id}/missions", status_code=201)
    async def create_direct_mission(
        body: MissionBody,
        request: Request,
        response: Response,
        agent_id: Annotated[
            str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
        ],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        return await create(
            _auth_context(request),
            response,
            body,
            idempotency_key,
            mode="direct_agent",
            direct_agent_id=agent_id,
        )

    @router.get("/api/v1/brain/missions/{mission_id}")
    async def mission_detail(
        mission_id: UUID, request: Request, response: Response
    ):
        context = _auth_context(request)
        try:
            mission = await asyncio.to_thread(
                mission_repository.mission_for_owner,
                context.internal_user_id,
                mission_id,
            )
        except MissionRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return _mission_payload(mission)

    @router.post("/api/v1/brain/missions/{mission_id}/cancel")
    async def cancel_mission(
        mission_id: UUID, request: Request, response: Response
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        try:
            mission = await asyncio.to_thread(
                mission_repository.request_cancel,
                context.internal_user_id,
                mission_id,
            )
        except MissionRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return _mission_payload(mission)

    @router.get("/api/v1/brain/missions/{mission_id}/events")
    async def mission_events(
        mission_id: UUID,
        request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
    ):
        context = _auth_context(request)
        try:
            await asyncio.to_thread(
                mission_repository.mission_for_owner,
                context.internal_user_id,
                mission_id,
            )
        except MissionRepositoryError as error:
            raise _repository_http_error(error) from None
        if not limiter.acquire(context.internal_user_id, mission_id):
            raise HTTPException(
                503, "Mission stream limit reached", headers=_NO_STORE
            )
        return _ReservedStreamingResponse(
            mission_event_stream(
                mission_repository,
                context.internal_user_id,
                mission_id,
                after=after,
                is_disconnected=request.is_disconnected,
                limiter=limiter,
                heartbeat_seconds=heartbeat_seconds,
                poll_seconds=poll_seconds,
                slot_reserved=True,
            ),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
            limiter=limiter,
            owner=context.internal_user_id,
            mission_id=mission_id,
        )

    return router
