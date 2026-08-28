from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable

from .dsn import validate_control_dsn
from .models import AuthContext

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_FAE_AGENT_ID = "ai-fae-agent"
_FAE_LAUNCH_BASE = "https://fae.orbbec.com.cn/app/"
_LAUNCH_CODE = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")


class AgentLaunchError(RuntimeError):
    def __init__(self, code: str, status_code: int = 503):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class IssuedAgentLaunch:
    launch_url: str = field(repr=False)
    expires_at: datetime
    binding_id: UUID
    code: str = field(repr=False)


@dataclass(frozen=True)
class ExchangedAgentIdentity:
    internal_user_id: UUID
    identity_binding_id: UUID
    agent_id: str


@dataclass(frozen=True)
class AgentBindingValidation:
    identity_binding_id: UUID
    agent_id: str
    active: bool
    internal_user_id: UUID | None = None


class AgentLaunchRepositoryProtocol(Protocol):
    def issue(self, **values) -> datetime: ...

    def exchange(
        self, *, code_digest: bytes, code_key_version: int, now: datetime
    ) -> tuple[UUID, UUID, str] | None: ...

    def validate_binding(
        self, *, binding_id: UUID, agent_id: str, now: datetime
    ) -> UUID | None: ...


class AgentLaunchRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(database_url, purpose="app")
        self.environment = parsed.environment
        self._database_url = database_url
        self._connect = connect

    def __repr__(self) -> str:
        return (
            "AgentLaunchRepository(database_url=<redacted>, "
            f"environment={self.environment!r})"
        )

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def issue(
        self,
        *,
        code_digest: bytes,
        code_key_version: int,
        source_session_id: UUID,
        internal_user_id: UUID,
        agent_id: str,
        binding_id: UUID,
        now: datetime,
        ttl_seconds: int,
    ) -> datetime:
        del now
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.issue_agent_launch_v52("
                    "%s,%s,%s,%s,%s,%s,%s,%s) as expires_at",
                    (
                        uuid4(),
                        code_digest,
                        code_key_version,
                        source_session_id,
                        internal_user_id,
                        agent_id,
                        binding_id,
                        ttl_seconds,
                    ),
                ).fetchone()
            if row is None or not isinstance(row["expires_at"], datetime):
                raise AgentLaunchError("launch_unavailable")
            return row["expires_at"]
        except AgentLaunchError:
            raise
        except psycopg.errors.InsufficientPrivilege:
            raise AgentLaunchError("agent_denied", 403) from None
        except psycopg.Error:
            raise AgentLaunchError("launch_unavailable") from None

    def exchange(
        self, *, code_digest: bytes, code_key_version: int, now: datetime
    ) -> tuple[UUID, UUID, str] | None:
        del now
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.exchange_agent_launch_v52(%s,%s)",
                    (code_digest, code_key_version),
                ).fetchone()
            if row is None:
                return None
            return (
                row["internal_user_id"],
                row["identity_binding_id"],
                str(row["agent_id"]),
            )
        except psycopg.Error:
            raise AgentLaunchError("launch_unavailable") from None

    def validate_binding(
        self, *, binding_id: UUID, agent_id: str, now: datetime
    ) -> UUID | None:
        del now
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control."
                    "validate_agent_identity_binding_v52(%s,%s)",
                    (binding_id, agent_id),
                ).fetchone()
            return None if row is None else row["internal_user_id"]
        except psycopg.Error:
            raise AgentLaunchError("binding_unavailable") from None


class AgentLaunchService:
    def __init__(
        self,
        *,
        repository: AgentLaunchRepositoryProtocol,
        secrets,
        authorization,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._authorization = authorization
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue(self, context: AuthContext, agent_id: str) -> IssuedAgentLaunch:
        if not isinstance(context, AuthContext):
            raise AgentLaunchError("authentication_required", 401)
        if context.hard_stale_read_only:
            raise AgentLaunchError("directory_stale", 503)
        if agent_id != _FAE_AGENT_ID:
            raise AgentLaunchError("agent_denied", 403)
        try:
            decision = self._authorization.decide_for_user_id(
                context.internal_user_id, agent_id
            )
        except AgentUseAuthorizationUnavailable:
            raise AgentLaunchError("authorization_unavailable") from None
        if not decision.allowed:
            raise AgentLaunchError("agent_denied", 403)
        code = self._secrets.random_token()
        binding_id = uuid4()
        now = self._clock()
        expires_at = self._repository.issue(
            code_digest=self._secrets.digest("agent-launch", code),
            code_key_version=self._secrets.key_version,
            source_session_id=context.session_id,
            internal_user_id=context.internal_user_id,
            agent_id=agent_id,
            binding_id=binding_id,
            now=now,
            ttl_seconds=60,
        )
        return IssuedAgentLaunch(
            launch_url=f"{_FAE_LAUNCH_BASE}#platform_launch={quote(code, safe='')}",
            expires_at=expires_at,
            binding_id=binding_id,
            code=code,
        )

    def exchange(self, code: str) -> ExchangedAgentIdentity:
        if not isinstance(code, str) or _LAUNCH_CODE.fullmatch(code) is None:
            raise AgentLaunchError("launch_code_invalid", 401)
        row = self._repository.exchange(
            code_digest=self._secrets.digest("agent-launch", code),
            code_key_version=self._secrets.key_version,
            now=self._clock(),
        )
        if row is None:
            raise AgentLaunchError("launch_code_invalid", 401)
        return ExchangedAgentIdentity(*row)

    def validate_binding(
        self, binding_id: UUID, agent_id: str
    ) -> AgentBindingValidation:
        if agent_id != _FAE_AGENT_ID:
            return AgentBindingValidation(binding_id, agent_id, False)
        internal_user_id = self._repository.validate_binding(
            binding_id=binding_id,
            agent_id=agent_id,
            now=self._clock(),
        )
        return AgentBindingValidation(
            binding_id,
            agent_id,
            internal_user_id is not None,
            internal_user_id,
        )


class _ExchangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(min_length=32, max_length=256)


class _BindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str = Field(min_length=1, max_length=128)


def _loopback(request: Request) -> bool:
    edge_source = getattr(request.state, "edge_source", None)
    if edge_source is not None:
        return bool(edge_source.ip.is_loopback)
    if request.client is None:
        return False
    try:
        return ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _raise(error: AgentLaunchError) -> None:
    raise HTTPException(error.status_code, error.code, headers=_NO_STORE) from None


def build_agent_launch_router(service: AgentLaunchService) -> APIRouter:
    router = APIRouter(tags=["agent-launch"])

    @router.post("/api/v1/agents/{agent_id}/launch")
    async def issue(agent_id: str, request: Request, response: Response):
        try:
            result = service.issue(request.state.auth_context, agent_id)
        except AgentLaunchError as exc:
            _raise(exc)
        response.headers.update(_NO_STORE)
        return {
            "launch_url": result.launch_url,
            "expires_at": result.expires_at.isoformat().replace("+00:00", "Z"),
        }

    @router.post("/api/v1/internal/agent-launch/exchange")
    async def exchange(body: _ExchangeBody, request: Request, response: Response):
        if not _loopback(request):
            raise HTTPException(404, "not found", headers=_NO_STORE)
        try:
            result = service.exchange(body.code)
        except AgentLaunchError as exc:
            _raise(exc)
        response.headers.update(_NO_STORE)
        return {
            "internal_user_id": str(result.internal_user_id),
            "identity_binding_id": str(result.identity_binding_id),
            "agent_id": result.agent_id,
        }

    @router.post("/api/v1/internal/agent-bindings/{binding_id}/validate")
    async def validate(
        binding_id: UUID,
        body: _BindingBody,
        request: Request,
        response: Response,
    ):
        if not _loopback(request):
            raise HTTPException(404, "not found", headers=_NO_STORE)
        try:
            result = service.validate_binding(binding_id, body.agent_id)
        except AgentLaunchError as exc:
            _raise(exc)
        if not result.active or result.internal_user_id is None:
            raise HTTPException(401, "binding_inactive", headers=_NO_STORE)
        response.headers.update(_NO_STORE)
        return {
            "internal_user_id": str(result.internal_user_id),
            "identity_binding_id": str(result.identity_binding_id),
            "agent_id": result.agent_id,
            "active": True,
        }

    return router
