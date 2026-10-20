"""One-use DingTalk session handoff to an individual ZenTao browser session."""

from __future__ import annotations

import hashlib
import re
import secrets
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.control_plane.models import AuthContext, Role
from .zentao import _private_origin


_TICKET = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_SID = re.compile(r"[0-9a-f]{32}\Z")
_CODE = re.compile(r"[a-z][a-z0-9-]{2,19}\Z")
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


def _https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("sdk_sso_https_origin_required")
    return value.rstrip("/")


class AccountBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    account: str = Field(min_length=2, max_length=40)


class SdkSsoService:
    def __init__(
        self, repository, *, private_origin: str, public_origin: str,
        platform_origin: str, entry_code: str, entry_key_file: str | Path,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.private_origin = _private_origin(private_origin)
        self.public_origin = _https_origin(public_origin)
        self.platform_origin = _https_origin(platform_origin)
        if not _CODE.fullmatch(entry_code):
            raise ValueError("sdk_sso_entry_code_invalid")
        self.entry_code = entry_code
        self.entry_key = Path(entry_key_file).read_text(encoding="ascii").strip()
        if len(self.entry_key) != 32 or not self.entry_key.isascii():
            raise ValueError("sdk_sso_entry_key_invalid")
        self.transport = transport

    async def start(self, actor: AuthContext) -> dict[str, str]:
        if actor.hard_stale_read_only:
            raise HTTPException(503, "sdk_sso_identity_stale")
        ticket = secrets.token_urlsafe(32)
        digest = hashlib.sha256(ticket.encode("ascii")).digest()
        issued = await run_in_threadpool(
            self.repository.issue, actor.internal_user_id, actor.session_id, digest
        )
        if not issued:
            raise HTTPException(403, "sdk_sso_access_or_binding_required")
        return {"ticket": ticket, "consume_url": f"{self.public_origin}/sdk-sso/consume"}

    async def bind(self, actor: AuthContext, target: UUID, account: str) -> dict[str, str]:
        if actor.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "sdk_owner_required")
        if actor.hard_stale_read_only:
            raise HTTPException(503, "sdk_sso_identity_stale")
        return await run_in_threadpool(self.repository.bind, actor.internal_user_id, target, account)

    async def consume(self, ticket: str) -> str:
        if not _TICKET.fullmatch(ticket):
            raise HTTPException(403, "sdk_sso_ticket_invalid")
        account = await run_in_threadpool(
            self.repository.consume, hashlib.sha256(ticket.encode("ascii")).digest()
        )
        if account is None:
            raise HTTPException(403, "sdk_sso_ticket_invalid")
        return await run_in_threadpool(self._zen_login_sync, account)

    def _zen_login_sync(self, account: str) -> str:
        with self.repository.login_slot() as timestamp:
            return self._zen_login_with_timestamp(account, timestamp)

    def _zen_login_with_timestamp(self, account: str, timestamp: int) -> str:
        if not 1_000_000_000 <= timestamp < 4_000_000_000:
            raise HTTPException(503, "sdk_sso_clock_unavailable")
        token = hashlib.md5(
            f"{self.entry_code}{self.entry_key}{timestamp}".encode("ascii")
        ).hexdigest()
        params = {
            "m": "user", "f": "apilogin", "account": account,
            "code": self.entry_code, "time": str(timestamp), "token": token,
        }
        try:
            with httpx.Client(
                transport=self.transport, timeout=5, trust_env=False, follow_redirects=False
            ) as client:
                result = client.get(f"{self.private_origin}/api.php", params=params)
        except httpx.HTTPError:
            raise HTTPException(503, "sdk_sso_zentao_unavailable") from None
        if result.status_code not in {302, 303}:
            raise HTTPException(502, "sdk_sso_zentao_rejected")
        location = result.headers.get("location", "")
        if location and (not location.startswith("/") or location.startswith("//")):
            raise HTTPException(502, "sdk_sso_zentao_response_invalid")
        session_ids: list[str] = []
        for header in result.headers.get_list("set-cookie"):
            jar = SimpleCookie()
            try:
                jar.load(header)
            except Exception:
                continue
            if "zentaosid" in jar:
                session_ids.append(jar["zentaosid"].value)
        if len(session_ids) != 1 or not _SID.fullmatch(session_ids[0]):
            raise HTTPException(502, "sdk_sso_zentao_response_invalid")
        return session_ids[0]


def _actor(request: Request) -> AuthContext:
    actor = getattr(request.state, "auth_context", None)
    if actor is None:
        raise HTTPException(401, "authentication_required")
    return actor


def build_sdk_sso_router(service: SdkSsoService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/sdk/sso", tags=["sdk-sso"])

    @router.post("/start")
    async def start(request: Request):
        return JSONResponse(await service.start(_actor(request)), headers=_NO_STORE)

    @router.put("/bindings/{user_id}")
    async def bind(request: Request, user_id: UUID, body: AccountBinding):
        return JSONResponse(await service.bind(_actor(request), user_id, body.account), headers=_NO_STORE)

    @router.post("/consume")
    async def consume(request: Request):
        if (request.url.query or request.headers.get("host") != urlsplit(service.public_origin).netloc
                or request.headers.get("origin") != service.platform_origin
                or request.headers.get("x-forwarded-proto") != "https"):
            raise HTTPException(404, "not_found")
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/x-www-form-urlencoded":
            raise HTTPException(415, "sdk_sso_form_required")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 256:
                raise HTTPException(413, "sdk_sso_form_too_large")
        try:
            values = parse_qs(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
        except (UnicodeError, ValueError):
            raise HTTPException(400, "sdk_sso_form_invalid") from None
        if set(values) != {"ticket"} or len(values["ticket"]) != 1:
            raise HTTPException(400, "sdk_sso_form_invalid")
        session_id = await service.consume(values["ticket"][0])
        response = RedirectResponse("/", status_code=303, headers=_NO_STORE)
        response.set_cookie("zentaosid", session_id, path="/", secure=True, httponly=True, samesite="lax")
        return response

    return router
