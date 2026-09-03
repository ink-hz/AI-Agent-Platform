from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
from datetime import datetime
from typing import Annotated, AsyncIterator, Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent_brain.action_models import ActionProjection
from app.agent_brain.action_service import (
    ActionCommandConflict,
    ActionCommandDenied,
    ActionCommandError,
    ActionCommandService,
)
from app.control_plane.auth import AuthSecrets
from app.control_plane.models import AuthContext

from .authorization import AgentUseAuthorizationUnavailable
from .conversation_models import (
    ConversationAttachmentProjection,
    ConversationCreateResult,
    ConversationEventRecord,
    ConversationMessageRecord,
    ConversationRecord,
    ConversationTurnRecord,
    ConversationTurnSubmission,
)
from .conversation_projection import ConversationProjection
from .conversation_repository import (
    ConversationRepository,
    ConversationRepositoryConflict,
    ConversationRepositoryError,
    ConversationRepositoryNotFound,
    ConversationTurnInProgress,
)
from .conversation_service import ConversationCommandService
from .routes import (
    MissionStreamBusy,
    MissionStreamLimiter,
    _ReservedStreamingResponse,
)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
_SSE_HEADERS = {
    **_NO_STORE,
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}
_MAX_INPUT_BYTES = 32 * 1024


class ConversationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secure(request: Request):
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_PRIVATE_HEADERS}
                raise
            except RequestValidationError:
                response = JSONResponse(
                    {"detail": "conversation request invalid"}, status_code=422
                )
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


class ConversationTextBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(default="", max_length=32768)
    attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=5)
    active_attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=50)

    @field_validator("attachment_ids", "active_attachment_ids", mode="before")
    @classmethod
    def _tuple_ids(cls, value):
        if not isinstance(value, (list, tuple)):
            return value
        try:
            return tuple(UUID(item) if isinstance(item, str) else item for item in value)
        except ValueError:
            return value

    @model_validator(mode="after")
    def _normalized_submission(self) -> ConversationTextBody:
        submission = ConversationTurnSubmission(
            self.text, self.attachment_ids, self.active_attachment_ids
        )
        self.text = submission.text
        self.attachment_ids = submission.attachment_ids
        self.active_attachment_ids = submission.active_attachment_ids
        return self

    def submission(self) -> ConversationTurnSubmission:
        return ConversationTurnSubmission(
            self.text, self.attachment_ids, self.active_attachment_ids
        )


class ConversationFeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rating: Literal["helpful", "unhelpful"]
    reason: Literal["inaccurate", "incomplete", "unclear", "unresolved", "other"] | None = None
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def _valid_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        selected = value.strip()
        if not selected:
            return None
        if len(selected.encode("utf-8")) > 1000:
            raise ValueError("Feedback comment too long")
        return selected

    @model_validator(mode="after")
    def _valid_detail(self) -> "ConversationFeedbackBody":
        if self.rating == "helpful" and (self.reason is not None or self.comment is not None):
            raise ValueError("Helpful feedback cannot include detail")
        if self.rating == "unhelpful" and self.reason is None:
            raise ValueError("Improvement reason required")
        return self


class ConversationRenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def _normalized_title(cls, value: str) -> str:
        selected = value.strip()
        if not selected:
            raise ValueError("Conversation title required")
        return selected


class ActionConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConversationCursorCodec:
    """Authenticated, owner-bound keyset cursor with a distinct payload kind."""

    def __init__(self, secrets: AuthSecrets) -> None:
        if not isinstance(secrets, AuthSecrets):
            raise ValueError("Conversation cursor secrets required")
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

    def issue(
        self,
        owner: UUID,
        updated_at: datetime,
        conversation_id: UUID,
        direct_agent_id: str | None = None,
        status: Literal["active", "archived"] = "active",
    ) -> str:
        if (
            not isinstance(owner, UUID)
            or not isinstance(conversation_id, UUID)
            or not isinstance(updated_at, datetime)
            or updated_at.tzinfo is None
        ):
            raise ValueError("Conversation cursor input invalid")
        document = {
                "conversation_id": str(conversation_id),
                "key_version": self._secrets.key_version,
                "kind": "conversation",
                "status": status,
                "updated_at": updated_at.isoformat(),
            }
        if direct_agent_id is not None:
            document["direct_agent_id"] = direct_agent_id
        payload = json.dumps(
            document,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = self._secrets.sign_mission_cursor(owner, payload)
        return self._encode(payload + signature)

    def read(
        self,
        owner: UUID,
        value: str,
        direct_agent_id: str | None = None,
        status: Literal["active", "archived"] = "active",
    ) -> tuple[datetime, UUID]:
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
            expected_keys = {
                "conversation_id",
                "key_version",
                "kind",
                "status",
                "updated_at",
            }
            if direct_agent_id is not None:
                expected_keys.add("direct_agent_id")
            if set(document) != expected_keys:
                raise ValueError
            if (
                document["kind"] != "conversation"
                or document["key_version"] != self._secrets.key_version
                or document.get("direct_agent_id") != direct_agent_id
                or document["status"] != status
            ):
                raise ValueError
            updated_at = datetime.fromisoformat(document["updated_at"])
            if updated_at.tzinfo is None:
                raise ValueError
            return updated_at, UUID(document["conversation_id"])
        except (
            AttributeError,
            binascii.Error,
            TypeError,
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            raise ValueError("Conversation cursor invalid") from None


def _auth_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication required", headers=_NO_STORE)
    return context


def _ensure_writable(context: AuthContext) -> None:
    if context.hard_stale_read_only:
        raise HTTPException(503, "account is read only", headers=_NO_STORE)


def _parse_idempotency_key(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(
            422,
            "Idempotency-Key must be a UUID",
            headers=_NO_STORE,
        ) from None


def _validate_input_bytes(text: str) -> None:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise HTTPException(
            422, "Conversation text invalid", headers=_NO_STORE
        ) from None
    if size > _MAX_INPUT_BYTES:
        raise HTTPException(
            413, "Conversation text exceeds 32 KiB", headers=_NO_STORE
        )


def _action_payload(action: ActionProjection) -> dict[str, object]:
    return {
        "action_id": str(action.action_id),
        "task_id": str(action.task_id),
        "action_kind": action.action_kind,
        "summary": action.summary,
        "impact": action.impact,
        "status": action.status,
        "execution_status": action.execution_status,
        "action_digest": action.action_digest,
        "action_digest_prefix": action.action_digest[:12],
        "expires_at": action.expires_at.isoformat(),
        "confirmed_at": (
            action.confirmed_at.isoformat()
            if action.confirmed_at is not None
            else None
        ),
        "execution_deadline_at": (
            action.execution_deadline_at.isoformat()
            if action.execution_deadline_at is not None
            else None
        ),
    }


def _action_http_error(error: ActionCommandError) -> HTTPException:
    if isinstance(error, ActionCommandDenied):
        return HTTPException(403, "Action use denied", headers=_NO_STORE)
    if isinstance(error, ActionCommandConflict):
        return HTTPException(409, "Action state conflict", headers=_NO_STORE)
    return HTTPException(503, "Action service unavailable", headers=_NO_STORE)


def _repository_http_error(error: ConversationRepositoryError) -> HTTPException:
    if isinstance(error, ConversationRepositoryNotFound):
        return HTTPException(404, "Conversation not found", headers=_NO_STORE)
    if isinstance(error, ConversationRepositoryConflict):
        if isinstance(error, ConversationTurnInProgress):
            return HTTPException(
                409,
                {
                    "code": "turn_in_progress",
                    "message": "当前对话已有一轮正在执行",
                },
                headers=_NO_STORE,
            )
        return HTTPException(409, "conversation conflict", headers=_NO_STORE)
    return HTTPException(
        503, "Conversation service unavailable", headers=_NO_STORE
    )


def _conversation_payload(record: ConversationRecord) -> dict[str, object]:
    return {
        "conversation_id": str(record.conversation_id),
        "mode": record.mode,
        "direct_agent_id": record.direct_agent_id,
        "title": record.title,
        "status": record.status,
        "summary_through_seq": record.summary_through_seq,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "archived_at": record.archived_at.isoformat() if record.archived_at else None,
    }


def _message_payload(record: ConversationMessageRecord) -> dict[str, object]:
    return {
        "message_id": str(record.message_id),
        "conversation_id": str(record.conversation_id),
        "seq": record.seq,
        "role": record.role,
        "content": record.content,
        "turn_id": str(record.turn_id) if record.turn_id else None,
        "delivery_status": record.delivery_status,
        "created_at": record.created_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "input_attachments": [
            _attachment_payload(item) for item in record.input_attachments
        ],
        "output_attachments": [
            _attachment_payload(item) for item in record.output_attachments
        ],
        "active_attachment_ids": [
            str(attachment_id) for attachment_id in record.active_attachment_ids
        ],
    }


def _attachment_payload(
    record: ConversationAttachmentProjection,
) -> dict[str, object]:
    return {
        "attachment_id": str(record.attachment_id),
        "conversation_id": str(record.conversation_id),
        "source": record.source,
        "display_name": record.display_name,
        "detected_mime": record.detected_mime,
        "size_bytes": record.size_bytes,
        "state": record.state,
        "created_at": record.created_at.isoformat(),
        "retained_until": record.retained_until.isoformat(),
        "processing_coverage": record.processing_coverage,
        "availability_reason": record.availability_reason,
    }


def _turn_payload(record: ConversationTurnRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "turn_id": str(record.turn_id),
        "conversation_id": str(record.conversation_id),
        "user_message_id": str(record.user_message_id),
        "assistant_message_id": (
            str(record.assistant_message_id) if record.assistant_message_id else None
        ),
        "retry_of_turn_id": (
            str(record.retry_of_turn_id) if record.retry_of_turn_id else None
        ),
        "status": record.status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _create_payload(result: ConversationCreateResult) -> dict[str, object]:
    return {
        "conversation": _conversation_payload(result.conversation),
        "message": _message_payload(result.message),
        "turn": _turn_payload(result.turn),
    }


def _event_payload(
    record: ConversationEventRecord,
    *,
    display_name_for_agent: Callable[[str], str | None] | None = None,
) -> dict[str, object]:
    public_payload = dict(
        ConversationProjection.public_payload(record.event_type, record.payload)
    )
    agent_id = public_payload.pop("agent_id", None)
    if agent_id is None:
        agent_id = public_payload.pop("selected_agent_id", None)
    if isinstance(agent_id, str):
        resolved = (
            display_name_for_agent(agent_id)
            if display_name_for_agent is not None
            else None
        )
        public_payload["agent_name"] = resolved or "专业 Agent"
    return {
        "event_id": str(record.event_id),
        "conversation_id": str(record.conversation_id),
        "seq": record.seq,
        "turn_id": str(record.turn_id) if record.turn_id else None,
        "event_type": record.event_type,
        "payload": public_payload,
        "created_at": record.created_at.isoformat(),
    }


def _feedback_payload(record) -> dict[str, object]:
    return {
        "feedback_id": str(record.feedback_id),
        "conversation_id": str(record.conversation_id),
        "message_id": str(record.message_id),
        "turn_id": str(record.turn_id),
        "rating": record.rating,
        "reason": record.reason,
        "created_at": record.created_at.isoformat(),
    }


def _sse_event(
    record: ConversationEventRecord,
    *,
    display_name_for_agent: Callable[[str], str | None] | None = None,
) -> str:
    data = json.dumps(
        _event_payload(record, display_name_for_agent=display_name_for_agent),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"id: {record.seq}\nevent: conversation\ndata: {data}\n\n"


async def conversation_event_stream(
    repository,
    owner: UUID,
    conversation_id: UUID,
    *,
    after: int,
    is_disconnected,
    limiter: MissionStreamLimiter,
    session_revalidator: Callable[[str], object],
    session_token: str,
    expected_session_id: UUID,
    heartbeat_seconds: float = 15,
    revalidate_seconds: float = 15,
    poll_seconds: float = 1,
    slot_reserved: bool = False,
    display_name_for_agent: Callable[[str], str | None] | None = None,
) -> AsyncIterator[str]:
    if (
        heartbeat_seconds <= 0
        or revalidate_seconds <= 0
        or poll_seconds < 0
        or not callable(session_revalidator)
        or not isinstance(session_token, str)
        or not session_token
        or not isinstance(expected_session_id, UUID)
    ):
        raise ValueError("Conversation stream timing invalid")
    if not slot_reserved and not limiter.acquire(owner, conversation_id):
        raise MissionStreamBusy()
    cursor = after
    last_heartbeat = 0.0
    last_revalidation = 0.0

    async def access_is_live() -> bool:
        nonlocal last_revalidation
        now = asyncio.get_running_loop().time()
        if last_revalidation and now - last_revalidation < revalidate_seconds:
            return True
        try:
            live_session = await asyncio.to_thread(
                session_revalidator, session_token
            )
            live_context = live_session[0]
            if (
                not isinstance(live_context, AuthContext)
                or live_context.internal_user_id != owner
                or live_context.session_id != expected_session_id
                or live_context.hard_stale_read_only
            ):
                return False
            await asyncio.to_thread(
                repository.conversation_for_owner, owner, conversation_id
            )
        except Exception:
            return False
        last_revalidation = now
        return True

    try:
        while True:
            if await is_disconnected() or not await access_is_live():
                return
            try:
                if isinstance(repository, ConversationRepository):
                    await asyncio.to_thread(
                        ConversationProjection(repository).project_brain_pending,
                        conversation_id,
                        limit=100,
                    )
                projected = await asyncio.to_thread(
                    repository.sync_mission_events,
                    owner,
                    conversation_id,
                    limit=100,
                )
                events = await asyncio.to_thread(
                    repository.events_after,
                    owner,
                    conversation_id,
                    after=cursor,
                    limit=100,
                )
                active_turn = await asyncio.to_thread(
                    repository.active_turn_for_owner,
                    owner,
                    conversation_id,
                )
            except ConversationRepositoryError:
                return
            for event in events:
                if not await access_is_live():
                    return
                cursor = event.seq
                yield _sse_event(
                    event, display_name_for_agent=display_name_for_agent
                )
            if active_turn is None:
                # A terminal Turn can have more Mission events than one projection
                # batch. Drain every full batch before deciding the stream is done.
                if projected == 100:
                    continue
                if not await access_is_live():
                    return
                try:
                    tail = await asyncio.to_thread(
                        repository.events_after,
                        owner,
                        conversation_id,
                        after=cursor,
                        limit=100,
                    )
                except ConversationRepositoryError:
                    return
                for event in tail:
                    cursor = event.seq
                    yield _sse_event(
                        event, display_name_for_agent=display_name_for_agent
                    )
                if len(tail) < 100:
                    return
                continue
            if events:
                continue
            now = asyncio.get_running_loop().time()
            if not last_heartbeat or now - last_heartbeat >= heartbeat_seconds:
                last_heartbeat = now
                yield f": heartbeat {int(now)}\n\n"
            await asyncio.sleep(poll_seconds)
    finally:
        if not slot_reserved:
            limiter.release(owner, conversation_id)


def build_conversation_router(
    repository,
    agent_use_authorization,
    *,
    command_service: ConversationCommandService | None = None,
    action_service: ActionCommandService | None = None,
    cursor_codec: ConversationCursorCodec,
    session_revalidator: Callable[[str], object],
    session_cookie_name: str,
    brain_enabled: bool = True,
    heartbeat_seconds: float = 15,
    revalidate_seconds: float = 15,
    poll_seconds: float = 1,
    max_streams_per_owner: int = 3,
    max_streams_per_conversation: int = 2,
    max_streams_global: int = 200,
) -> APIRouter:
    if type(brain_enabled) is not bool:
        raise ValueError("Conversation Brain flag invalid")
    if not callable(session_revalidator):
        raise ValueError("Conversation session revalidator required")
    if not isinstance(session_cookie_name, str) or not session_cookie_name:
        raise ValueError("Conversation session cookie name required")
    router = APIRouter(
        tags=["agent-brain-conversations"], route_class=ConversationRoute
    )
    commands = command_service or ConversationCommandService(
        repository, v2_enabled=False
    )
    limiter = MissionStreamLimiter(
        max_per_owner=max_streams_per_owner,
        max_per_mission=max_streams_per_conversation,
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

    def catalog_names(owner: UUID) -> dict[str, str]:
        try:
            cards = agent_use_authorization.permitted_catalog_for_user_id(owner)
        except AgentUseAuthorizationUnavailable:
            raise HTTPException(
                503, "Agent catalog unavailable", headers=_NO_STORE
            ) from None
        return {card.agent_id: card.display_name for card in cards}

    async def require_direct_agent(owner: UUID, agent_id: str) -> None:
        cards = await asyncio.to_thread(permitted, owner)
        if agent_id not in {card.agent_id for card in cards}:
            raise HTTPException(403, "Agent use denied", headers=_NO_STORE)

    async def create(
        context: AuthContext,
        response: Response,
        body: ConversationTextBody,
        idempotency_key: str | None,
        *,
        mode: str,
        direct_agent_id: str | None,
    ):
        _ensure_writable(context)
        request_id = _parse_idempotency_key(idempotency_key)
        _validate_input_bytes(body.text)
        if direct_agent_id is not None:
            await require_direct_agent(context.internal_user_id, direct_agent_id)
        try:
            result = await asyncio.to_thread(
                commands.start,
                context.internal_user_id,
                request_id,
                body.submission(),
                mode=mode,
                direct_agent_id=direct_agent_id,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.status_code = 201 if result.created else 200
        response.headers.update(_NO_STORE)
        return _create_payload(result)

    @router.post("/api/v1/conversations", status_code=201)
    async def start_conversation(
        body: ConversationTextBody,
        request: Request,
        response: Response,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        if not brain_enabled:
            raise HTTPException(
                503, "Agent Brain unavailable", headers=_NO_STORE
            )
        return await create(
            _auth_context(request),
            response,
            body,
            idempotency_key,
            mode="brain",
            direct_agent_id=None,
        )

    @router.post("/api/v1/agents/{agent_id}/conversations", status_code=201)
    async def start_direct_conversation(
        body: ConversationTextBody,
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

    @router.get("/api/v1/conversations")
    async def list_conversations(
        request: Request,
        response: Response,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        before: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
        direct_agent_id: Annotated[
            str | None,
            Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
        ] = None,
        status: Annotated[Literal["active", "archived"], Query()] = "active",
    ):
        context = _auth_context(request)
        try:
            boundary = (
                cursor_codec.read(
                    context.internal_user_id, before, direct_agent_id, status
                )
                if before is not None
                else None
            )
        except ValueError:
            raise HTTPException(
                422, "Conversation cursor invalid", headers=_NO_STORE
            ) from None
        try:
            rows = await asyncio.to_thread(
                repository.list_for_owner,
                context.internal_user_id,
                limit=limit + 1,
                before=boundary,
                direct_agent_id=direct_agent_id,
                status=status,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        visible = rows[:limit]
        next_cursor = None
        if len(rows) > limit and visible:
            last = visible[-1]
            next_cursor = cursor_codec.issue(
                context.internal_user_id,
                last.updated_at,
                last.conversation_id,
                direct_agent_id,
                status,
            )
        response.headers.update(_NO_STORE)
        return {
            "items": [_conversation_payload(item) for item in visible],
            "next_cursor": next_cursor,
        }

    @router.get("/api/v1/conversations/{conversation_id}")
    async def conversation_detail(
        conversation_id: UUID, request: Request, response: Response
    ):
        context = _auth_context(request)
        try:
            conversation = await asyncio.to_thread(
                repository.conversation_for_owner,
                context.internal_user_id,
                conversation_id,
            )
            latest_turn = await asyncio.to_thread(
                repository.latest_turn_for_owner,
                context.internal_user_id,
                conversation_id,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return {
            "conversation": _conversation_payload(conversation),
            "current_turn": _turn_payload(latest_turn),
        }

    @router.get("/api/v1/conversations/{conversation_id}/messages")
    async def conversation_messages(
        conversation_id: UUID,
        request: Request,
        response: Response,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        context = _auth_context(request)
        try:
            rows = await asyncio.to_thread(
                repository.messages_after,
                context.internal_user_id,
                conversation_id,
                after=after,
                limit=limit,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return {"items": [_message_payload(item) for item in rows]}

    @router.post(
        "/api/v1/conversations/{conversation_id}/messages", status_code=201
    )
    async def append_message(
        conversation_id: UUID,
        body: ConversationTextBody,
        request: Request,
        response: Response,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        request_id = _parse_idempotency_key(idempotency_key)
        _validate_input_bytes(body.text)
        try:
            conversation = await asyncio.to_thread(
                repository.conversation_for_owner,
                context.internal_user_id,
                conversation_id,
            )
            if conversation.mode == "brain" and not brain_enabled:
                raise HTTPException(
                    503, "Agent Brain unavailable", headers=_NO_STORE
                )
            if conversation.mode == "direct_agent":
                await require_direct_agent(
                    context.internal_user_id, conversation.direct_agent_id
                )
            if conversation.mode == "brain":
                replay = await asyncio.to_thread(
                    repository.replay_message_for_owner,
                    context.internal_user_id,
                    conversation_id,
                    request_id,
                    body.submission(),
                )
                if replay is not None:
                    response.status_code = 200
                    response.headers.update(_NO_STORE)
                    return _create_payload(replay)
                active_turn = await asyncio.to_thread(
                    repository.active_turn_for_owner,
                    context.internal_user_id,
                    conversation_id,
                )
                if active_turn is not None and (
                    body.attachment_ids or body.active_attachment_ids
                ):
                    raise HTTPException(
                        409,
                        "Attachments require a new Conversation turn",
                        headers=_NO_STORE,
                    )
                if active_turn is not None and active_turn.status != "waiting_user":
                    intervention = await asyncio.to_thread(
                        repository.append_brain_intervention,
                        context.internal_user_id,
                        conversation_id,
                        request_id,
                        body.text,
                    )
                    response.status_code = 202 if intervention.created else 200
                    response.headers.update(_NO_STORE)
                    return {
                        "intervention": {
                            "status": intervention.status,
                            "message_id": str(intervention.message.message_id),
                        },
                        "message": _message_payload(intervention.message),
                        "turn": _turn_payload(intervention.turn),
                    }
            result = await asyncio.to_thread(
                commands.append_turn,
                context.internal_user_id,
                conversation_id,
                request_id,
                body.submission(),
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.status_code = 201 if result.created else 200
        response.headers.update(_NO_STORE)
        return _create_payload(result)

    @router.get(
        "/api/v1/conversations/{conversation_id}/turns/{turn_id}/tasks/{task_id}"
    )
    async def task_detail(
        conversation_id: UUID,
        turn_id: UUID,
        task_id: UUID,
        request: Request,
        response: Response,
    ):
        context = _auth_context(request)
        try:
            detail = await asyncio.to_thread(
                repository.task_detail_for_owner,
                context.internal_user_id,
                conversation_id,
                turn_id,
                task_id,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return detail

    @router.get("/api/v1/conversations/{conversation_id}/actions")
    async def conversation_actions(
        conversation_id: UUID,
        request: Request,
        response: Response,
    ):
        context = _auth_context(request)
        if action_service is None:
            raise HTTPException(503, "Action service unavailable", headers=_NO_STORE)
        try:
            actions = await asyncio.to_thread(
                action_service.list_for_owner,
                context.internal_user_id,
                conversation_id,
            )
        except ActionCommandError as error:
            raise _action_http_error(error) from None
        response.headers.update(_NO_STORE)
        return {"items": [_action_payload(action) for action in actions]}

    @router.post(
        "/api/v1/conversations/{conversation_id}/actions/{action_id}/confirm"
    )
    async def confirm_action(
        conversation_id: UUID,
        action_id: UUID,
        body: ActionConfirmBody,
        request: Request,
        response: Response,
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        if action_service is None:
            raise HTTPException(503, "Action service unavailable", headers=_NO_STORE)
        try:
            await asyncio.to_thread(
                action_service.get_for_owner,
                context.internal_user_id,
                conversation_id,
                action_id,
            )
            action = await asyncio.to_thread(
                action_service.confirm,
                context.internal_user_id,
                action_id,
                body.action_digest,
            )
        except ActionCommandError as error:
            raise _action_http_error(error) from None
        response.headers.update(_NO_STORE)
        return _action_payload(action)

    @router.post(
        "/api/v1/conversations/{conversation_id}/actions/{action_id}/reject"
    )
    async def reject_action(
        conversation_id: UUID,
        action_id: UUID,
        request: Request,
        response: Response,
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        if action_service is None:
            raise HTTPException(503, "Action service unavailable", headers=_NO_STORE)
        try:
            await asyncio.to_thread(
                action_service.get_for_owner,
                context.internal_user_id,
                conversation_id,
                action_id,
            )
            action = await asyncio.to_thread(
                action_service.reject,
                context.internal_user_id,
                action_id,
            )
        except ActionCommandError as error:
            raise _action_http_error(error) from None
        response.headers.update(_NO_STORE)
        return _action_payload(action)

    @router.post(
        "/api/v1/conversations/{conversation_id}/turns/{turn_id}/retry",
        status_code=201,
    )
    async def retry_turn(
        conversation_id: UUID,
        turn_id: UUID,
        request: Request,
        response: Response,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        if not brain_enabled:
            raise HTTPException(
                503, "Agent Brain unavailable", headers=_NO_STORE
            )
        context = _auth_context(request)
        _ensure_writable(context)
        request_id = _parse_idempotency_key(idempotency_key)
        try:
            result = await asyncio.to_thread(
                commands.retry_turn,
                context.internal_user_id,
                conversation_id,
                turn_id,
                request_id,
            )
        except ValueError:
            raise HTTPException(404, "Conversation not found", headers=_NO_STORE)
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.status_code = 201 if result.created else 200
        response.headers.update(_NO_STORE)
        return _create_payload(result)

    @router.post("/api/v1/messages/{message_id}/feedback", status_code=201)
    async def submit_feedback(
        message_id: UUID,
        body: ConversationFeedbackBody,
        request: Request,
        response: Response,
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        try:
            result = await asyncio.to_thread(
                repository.create_feedback,
                context.internal_user_id,
                message_id,
                body.rating,
                body.reason,
                body.comment,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.status_code = 201 if result.created else 200
        response.headers.update(_NO_STORE)
        return _feedback_payload(result.feedback)

    @router.post("/api/v1/conversations/{conversation_id}/turns/current/cancel")
    async def cancel_current_turn(
        conversation_id: UUID, request: Request, response: Response
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        try:
            cancellation = await asyncio.to_thread(
                commands.request_cancel,
                context.internal_user_id,
                conversation_id,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return {
            "conversation_id": str(conversation_id),
            "turn_id": str(cancellation.turn_id),
            "cancel_requested": cancellation.cancel_requested,
        }

    @router.patch("/api/v1/conversations/{conversation_id}")
    async def rename_conversation(
        conversation_id: UUID,
        body: ConversationRenameBody,
        request: Request,
        response: Response,
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        try:
            conversation = await asyncio.to_thread(
                repository.rename,
                context.internal_user_id,
                conversation_id,
                body.title,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return _conversation_payload(conversation)

    @router.post("/api/v1/conversations/{conversation_id}/archive")
    async def archive_conversation(
        conversation_id: UUID, request: Request, response: Response
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        try:
            conversation = await asyncio.to_thread(
                repository.archive,
                context.internal_user_id,
                conversation_id,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return _conversation_payload(conversation)

    @router.post("/api/v1/conversations/{conversation_id}/restore")
    async def restore_conversation(
        conversation_id: UUID, request: Request, response: Response
    ):
        context = _auth_context(request)
        _ensure_writable(context)
        try:
            conversation = await asyncio.to_thread(
                repository.restore,
                context.internal_user_id,
                conversation_id,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        response.headers.update(_NO_STORE)
        return _conversation_payload(conversation)

    @router.get("/api/v1/conversations/{conversation_id}/events")
    async def conversation_events(
        conversation_id: UUID,
        request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
    ):
        context = _auth_context(request)
        session_token = request.cookies.get(session_cookie_name)
        if not session_token:
            raise HTTPException(
                401, "authentication required", headers=_NO_STORE
            )
        try:
            await asyncio.to_thread(
                repository.conversation_for_owner,
                context.internal_user_id,
                conversation_id,
            )
        except ConversationRepositoryError as error:
            raise _repository_http_error(error) from None
        display_names = await asyncio.to_thread(
            catalog_names, context.internal_user_id
        )
        if not limiter.acquire(context.internal_user_id, conversation_id):
            raise HTTPException(
                503, "Conversation stream limit reached", headers=_NO_STORE
            )
        return _ReservedStreamingResponse(
            conversation_event_stream(
                repository,
                context.internal_user_id,
                conversation_id,
                after=after,
                is_disconnected=request.is_disconnected,
                limiter=limiter,
                session_revalidator=session_revalidator,
                session_token=session_token,
                expected_session_id=context.session_id,
                heartbeat_seconds=heartbeat_seconds,
                revalidate_seconds=revalidate_seconds,
                poll_seconds=poll_seconds,
                slot_reserved=True,
                display_name_for_agent=display_names.get,
            ),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
            limiter=limiter,
            owner=context.internal_user_id,
            mission_id=conversation_id,
        )

    return router
