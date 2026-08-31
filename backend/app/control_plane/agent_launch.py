from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Literal, Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable

from .dsn import validate_control_dsn
from .models import AuthContext
from .partner_models import PartnerIdentityError

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_FAE_AGENT_ID = "ai-fae-agent"
_FAE_LAUNCH_BASE = "https://fae.orbbec.com.cn/app/"
_LAUNCH_CODE = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
# The frozen private back-channel contract, checked in under
# contracts/fae_identity_v1. Adding the marker is rolling-deploy safe: an older
# Agent ignores the extra field, and a newer Agent refuses anything without it.
_IDENTITY_CONTRACT_VERSION = "orbbec-fae-identity/v1"
_DISPLAY_NAME_LIMIT = 64
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def _safe_display_name(value: object) -> str | None:
    """Project a stored name into the narrow shape the contract allows.

    Names come from provider directories and from partner records, so their
    length and their bytes are not ours to trust. The contract admits a bounded
    single-line string or nothing at all, and dropping a name is always
    preferable to shipping control characters into an Agent's rendering path.
    """
    if not isinstance(value, str):
        return None
    cleaned = _CONTROL_CHARACTERS.sub("", value).strip()
    return cleaned[:_DISPLAY_NAME_LIMIT].strip() or None


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
class ExchangedAgentSubject:
    subject_id: UUID
    subject_type: Literal["enterprise_member", "partner_operator"]
    identity_binding_id: UUID
    agent_id: str
    internal_user_id: UUID | None = None
    display_name: str | None = None
    partner_display_name: str | None = None


@dataclass(frozen=True)
class AgentBindingValidation:
    subject_id: UUID | None
    subject_type: Literal["enterprise_member", "partner_operator"] | None
    identity_binding_id: UUID
    agent_id: str
    active: bool
    internal_user_id: UUID | None = None
    display_name: str | None = None
    partner_display_name: str | None = None


class AgentLaunchRepositoryProtocol(Protocol):
    def issue(self, **values) -> datetime: ...

    def exchange(
        self, *, code_digest: bytes, code_key_version: int, now: datetime
    ) -> tuple[UUID, str, UUID, str, UUID | None, str | None] | None: ...

    def validate_binding(
        self, *, binding_id: UUID, agent_id: str, now: datetime
    ) -> tuple[UUID, str, UUID, str, UUID | None, str | None, bool] | None: ...

    def revoke_binding(
        self, *, binding_id: UUID, agent_id: str, now: datetime
    ) -> None: ...


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
        subject_id: UUID,
        subject_type: Literal["enterprise_member", "partner_operator"],
        source_session_id: UUID | None,
        internal_user_id: UUID | None,
        agent_id: str,
        binding_id: UUID,
        now: datetime,
        ttl_seconds: int,
    ) -> datetime:
        del now
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.issue_agent_launch_v57("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) as expires_at",
                    (
                        uuid4(),
                        code_digest,
                        code_key_version,
                        subject_id,
                        subject_type,
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
    ) -> tuple[UUID, str, UUID, str, UUID | None, str | None] | None:
        del now
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.exchange_agent_launch_v57(%s,%s)",
                    (code_digest, code_key_version),
                ).fetchone()
            if row is None:
                return None
            return (
                row["subject_id"],
                str(row["subject_type"]),
                row["identity_binding_id"],
                str(row["agent_id"]),
                row["internal_user_id"],
                row["display_name"],
            )
        except psycopg.Error:
            raise AgentLaunchError("launch_unavailable") from None

    def validate_binding(
        self, *, binding_id: UUID, agent_id: str, now: datetime
    ) -> tuple[UUID, str, UUID, str, UUID | None, str | None, bool] | None:
        del now
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control."
                    "validate_agent_identity_binding_v57(%s,%s)",
                    (binding_id, agent_id),
                ).fetchone()
            if row is None:
                return None
            return (
                row["subject_id"],
                str(row["subject_type"]),
                row["identity_binding_id"],
                str(row["agent_id"]),
                row["internal_user_id"],
                row["display_name"],
                bool(row["active"]),
            )
        except psycopg.Error:
            raise AgentLaunchError("binding_unavailable") from None

    def revoke_binding(
        self, *, binding_id: UUID, agent_id: str, now: datetime
    ) -> None:
        del now
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_control."
                    "revoke_agent_identity_binding_v57(%s,%s)",
                    (binding_id, agent_id),
                ).fetchone()
        except psycopg.Error:
            raise AgentLaunchError("binding_unavailable") from None


class AgentLaunchService:
    def __init__(
        self,
        *,
        repository: AgentLaunchRepositoryProtocol,
        secrets,
        authorization,
        partner_service=None,
        partner_provider=None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._authorization = authorization
        self._partner_service = partner_service
        self._partner_provider = partner_provider
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue(self, context: AuthContext, agent_id: str) -> IssuedAgentLaunch:
        if agent_id != _FAE_AGENT_ID:
            raise AgentLaunchError("agent_denied", 403)
        return self.issue_enterprise(context)

    def issue_enterprise(self, context: AuthContext) -> IssuedAgentLaunch:
        if not isinstance(context, AuthContext):
            raise AgentLaunchError("authentication_required", 401)
        if context.hard_stale_read_only:
            raise AgentLaunchError("directory_stale", 503)
        try:
            decision = self._authorization.decide_for_user_id(
                context.internal_user_id, _FAE_AGENT_ID
            )
        except AgentUseAuthorizationUnavailable:
            raise AgentLaunchError("authorization_unavailable") from None
        if not decision.allowed:
            raise AgentLaunchError("agent_denied", 403)
        return self._issue(
            subject_id=context.internal_user_id,
            subject_type="enterprise_member",
            source_session_id=context.session_id,
            internal_user_id=context.internal_user_id,
            launch_parameter="platform_launch",
        )

    async def issue_partner(self, subject_id: UUID) -> IssuedAgentLaunch:
        if not isinstance(subject_id, UUID):
            raise AgentLaunchError("partner_subject_invalid", 422)
        await self._require_partner(subject_id)
        return self._issue(
            subject_id=subject_id,
            subject_type="partner_operator",
            source_session_id=None,
            internal_user_id=None,
            launch_parameter="partner_launch",
        )

    def _issue(
        self,
        *,
        subject_id: UUID,
        subject_type: Literal["enterprise_member", "partner_operator"],
        source_session_id: UUID | None,
        internal_user_id: UUID | None,
        launch_parameter: str,
    ) -> IssuedAgentLaunch:
        code = self._secrets.random_token()
        binding_id = uuid4()
        now = self._clock()
        expires_at = self._repository.issue(
            code_digest=self._secrets.digest("agent-launch", code),
            code_key_version=self._secrets.key_version,
            subject_id=subject_id,
            subject_type=subject_type,
            source_session_id=source_session_id,
            internal_user_id=internal_user_id,
            agent_id=_FAE_AGENT_ID,
            binding_id=binding_id,
            now=now,
            ttl_seconds=60,
        )
        return IssuedAgentLaunch(
            launch_url=(
                f"{_FAE_LAUNCH_BASE}#{launch_parameter}={quote(code, safe='')}"
            ),
            expires_at=expires_at,
            binding_id=binding_id,
            code=code,
        )

    async def exchange(self, code: str) -> ExchangedAgentSubject:
        if not isinstance(code, str) or _LAUNCH_CODE.fullmatch(code) is None:
            raise AgentLaunchError("launch_code_invalid", 401)
        row = self._repository.exchange(
            code_digest=self._secrets.digest("agent-launch", code),
            code_key_version=self._secrets.key_version,
            now=self._clock(),
        )
        if row is None:
            raise AgentLaunchError("launch_code_invalid", 401)
        try:
            (
                subject_id,
                subject_type,
                binding_id,
                agent_id,
                internal_user_id,
                display_name,
            ) = row
        except (TypeError, ValueError):
            raise AgentLaunchError("launch_unavailable") from None
        if subject_type == "partner_operator":
            projection = await self._require_partner(
                subject_id, binding_id=binding_id
            )
            display_name = projection.display_name
            partner_display_name = projection.partner_display_name
        elif subject_type == "enterprise_member" and internal_user_id == subject_id:
            partner_display_name = None
        else:
            raise AgentLaunchError("launch_unavailable")
        return ExchangedAgentSubject(
            subject_id=subject_id,
            subject_type=subject_type,
            identity_binding_id=binding_id,
            agent_id=agent_id,
            internal_user_id=internal_user_id,
            display_name=display_name,
            partner_display_name=partner_display_name,
        )

    async def validate_binding(
        self, binding_id: UUID, agent_id: str
    ) -> AgentBindingValidation:
        if agent_id != _FAE_AGENT_ID:
            return AgentBindingValidation(None, None, binding_id, agent_id, False)
        row = self._repository.validate_binding(
            binding_id=binding_id,
            agent_id=agent_id,
            now=self._clock(),
        )
        if row is None:
            return AgentBindingValidation(None, None, binding_id, agent_id, False)
        try:
            (
                subject_id,
                subject_type,
                selected_binding_id,
                selected_agent_id,
                internal_user_id,
                display_name,
                active,
            ) = row
        except (TypeError, ValueError):
            raise AgentLaunchError("binding_unavailable") from None
        partner_display_name = None
        if active and subject_type == "partner_operator":
            projection = await self._require_partner(
                subject_id, binding_id=selected_binding_id
            )
            display_name = projection.display_name
            partner_display_name = projection.partner_display_name
        elif subject_type != "enterprise_member" and subject_type != "partner_operator":
            raise AgentLaunchError("binding_unavailable")
        return AgentBindingValidation(
            subject_id=subject_id,
            subject_type=subject_type,
            identity_binding_id=selected_binding_id,
            agent_id=selected_agent_id,
            active=active,
            internal_user_id=internal_user_id,
            display_name=display_name,
            partner_display_name=partner_display_name,
        )

    async def _require_partner(
        self, subject_id: UUID, *, binding_id: UUID | None = None
    ):
        if self._partner_service is None or self._partner_provider is None:
            raise AgentLaunchError("partner_identity_unavailable")
        try:
            return await self._partner_service.require_active_fae_subject(
                subject_id, self._partner_provider
            )
        except PartnerIdentityError as error:
            if error.status_code == 403 and binding_id is not None:
                try:
                    self._repository.revoke_binding(
                        binding_id=binding_id,
                        agent_id=_FAE_AGENT_ID,
                        now=self._clock(),
                    )
                except AgentLaunchError:
                    raise AgentLaunchError("binding_unavailable") from None
            code = (
                error.code
                if error.status_code == 403
                else "partner_identity_unavailable"
            )
            raise AgentLaunchError(code, error.status_code) from None
        except Exception:  # noqa: BLE001 - partner boundary is fail-closed
            raise AgentLaunchError("partner_identity_unavailable") from None


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


def _identity_body(result) -> dict[str, object]:
    """The one shape both back-channel responses are allowed to have.

    Exchange and validate answer the same question about the same subject, so
    they share one projection; validate only adds `active`. Anything not listed
    here -- department, role, provider ids, tokens -- stays inside the Platform.
    """
    return {
        "contract_version": _IDENTITY_CONTRACT_VERSION,
        "subject_id": str(result.subject_id),
        "subject_type": result.subject_type,
        "internal_user_id": (
            None if result.internal_user_id is None else str(result.internal_user_id)
        ),
        "identity_binding_id": str(result.identity_binding_id),
        "agent_id": result.agent_id,
        "display_name": _safe_display_name(result.display_name),
        "partner_display_name": _safe_display_name(result.partner_display_name),
    }


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
            result = await service.exchange(body.code)
        except AgentLaunchError as exc:
            _raise(exc)
        response.headers.update(_NO_STORE)
        return _identity_body(result)

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
            result = await service.validate_binding(binding_id, body.agent_id)
        except AgentLaunchError as exc:
            _raise(exc)
        if not result.active or result.subject_id is None or result.subject_type is None:
            raise HTTPException(401, "binding_inactive", headers=_NO_STORE)
        response.headers.update(_NO_STORE)
        return {**_identity_body(result), "active": True}

    return router
