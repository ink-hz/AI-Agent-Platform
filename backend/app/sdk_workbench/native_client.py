"""Personal ZenTao API transport for the platform-owned SDK workspace."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict
from uuid import UUID

import httpx

from app.control_plane.models import AuthContext
from .sso import SdkSsoService
from .zentao import (
    ZenTaoAuthenticationError,
    ZenTaoProtocolError,
    ZenTaoRejected,
    ZenTaoRequestRejected,
    ZenTaoUnavailable,
    _reject_nonstandard,
)


_PATH = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(?:/[A-Za-z0-9_-]+)*\Z")
_MAX_BYTES = 1_048_576


class NativeZenTaoClient:
    def __init__(
        self,
        sso: SdkSsoService,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl_seconds: int = 900,
        max_sessions: int = 1000,
    ) -> None:
        if ttl_seconds < 1 or max_sessions < 1:
            raise ValueError("zentao_session_cache_invalid")
        self._sso = sso
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[tuple[UUID, str], tuple[str, float]] = OrderedDict()
        self._session_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=sso.private_origin,
            transport=transport,
            timeout=10.0,
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self) -> None:
        self._sessions.clear()
        await self._client.aclose()

    async def account(self, actor: AuthContext) -> str:
        """Return the currently entitled ZenTao account without exposing a session."""
        return await self._sso.native_account(actor)

    async def request(
        self,
        actor: AuthContext,
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict | None = None,
        *,
        api_version: int = 2,
    ) -> dict:
        """Call a relative private API route with the current employee's identity."""
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("zentao_invalid_method")
        if not _PATH.fullmatch(path) or api_version not in {1, 2}:
            raise ValueError("zentao_invalid_path")
        account = await self.account(actor)
        key = (actor.internal_user_id, account)
        sid = await self._session(key, account)
        for attempt in range(2):
            try:
                async with self._client.stream(
                    method,
                    f"/api.php/v{api_version}/{path}",
                    json=payload,
                    params=params,
                    headers={"Token": f"ss_{sid}"},
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > _MAX_BYTES:
                            raise ZenTaoProtocolError("zentao_response_too_large")
                    status = response.status_code
            except ZenTaoProtocolError:
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                raise ZenTaoUnavailable("zentao_unavailable") from None
            if status == 401:
                await self._discard_session(key, sid)
                if attempt == 0:
                    sid = await self._session(key, account)
                    continue
                raise ZenTaoAuthenticationError("zentao_authentication_failed")
            if status >= 500:
                raise ZenTaoUnavailable("zentao_unavailable")
            if status in {400, 403, 404, 409, 422}:
                raise ZenTaoRequestRejected("zentao_request_rejected")
            if status not in {200, 201}:
                raise ZenTaoProtocolError("zentao_http_status")
            try:
                body = json.loads(content, parse_constant=_reject_nonstandard)
            except (UnicodeDecodeError, ValueError):
                raise ZenTaoProtocolError("zentao_invalid_json") from None
            if not isinstance(body, dict):
                raise ZenTaoProtocolError("zentao_invalid_envelope")
            if api_version == 2 and not isinstance(body.get("status"), str):
                raise ZenTaoProtocolError("zentao_invalid_envelope")
            if "status" in body and body["status"] != "success":
                raise ZenTaoRejected("zentao_rejected")
            return body
        raise AssertionError("unreachable")

    async def _session(self, key: tuple[UUID, str], account: str) -> str:
        async with self._session_lock:
            now = time.monotonic()
            cached = self._sessions.get(key)
            if cached is not None and cached[1] > now:
                self._sessions.move_to_end(key)
                return cached[0]
            self._sessions.pop(key, None)
            sid = await self._sso.login_personal_session(account)
            self._sessions[key] = (sid, time.monotonic() + self._ttl_seconds)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            return sid

    async def _discard_session(self, key: tuple[UUID, str], sid: str) -> None:
        async with self._session_lock:
            cached = self._sessions.get(key)
            if cached is not None and cached[0] == sid:
                self._sessions.pop(key, None)
