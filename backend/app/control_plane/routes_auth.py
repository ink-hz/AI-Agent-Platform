from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticationError, CompletedLogin, cookie_policy
from .models import AuthContext, IssuedWebSession, Role
from app.spa import is_public_build_asset


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _login_csp(auth) -> str:
    if auth.route_prefix == "/":
        asset_source = "'self'"
        connect_source = "'self'"
    else:
        base = auth.public_base_url + auth.route_prefix.rstrip("/")
        asset_source = base + "/assets/"
        connect_source = base + "/api/"
    return (
        "default-src 'none'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'none'; "
        f"script-src {asset_source}; style-src {asset_source}; "
        f"img-src {asset_source}; connect-src {connect_source}"
    )


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_path: str | None = None


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2048)


def _local_path(auth, path: str) -> str:
    return path if auth.route_prefix == "/" else auth.route_prefix.rstrip("/") + path


def _session_value(value):
    if isinstance(value, CompletedLogin):
        return value.session, value.return_path
    if isinstance(value, IssuedWebSession):
        return value, None
    raise AuthenticationError("login unavailable")


def _set_session_cookie(response: Response, auth, issued: IssuedWebSession) -> None:
    policy = cookie_policy(auth.mode, auth.route_prefix)
    response.set_cookie(
        auth.cookie_name,
        issued.cookie_token,
        **policy,
    )
    # The second HttpOnly Cookie lets the authenticated account bootstrap
    # recover the raw CSRF value after a QR callback navigation. The database
    # still stores only its purpose-separated HMAC digest.
    response.set_cookie(
        auth.csrf_cookie_name,
        issued.csrf_token,
        **policy,
    )
    response.headers.update(_NO_STORE)
    response.headers["X-CSRF-Token"] = issued.csrf_token


def build_auth_router(
    auth, *, static_dir: str, public_assets: frozenset[str]
) -> APIRouter:
    router = APIRouter(prefix="" if auth.route_prefix == "/" else auth.route_prefix.rstrip("/"))
    index = Path(static_dir) / "index.html"
    favicon = Path(static_dir) / "favicon.ico"
    assets = Path(static_dir) / "assets"

    @router.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(_local_path(auth, "/login"), status_code=302, headers=_NO_STORE)

    @router.get("/login", include_in_schema=False)
    async def login():
        csp = _login_csp(auth)
        if not index.is_file():
            return HTMLResponse("<!doctype html><title>Agent Platform</title>", headers={**_NO_STORE, "Content-Security-Policy": csp})
        return FileResponse(index, headers={**_NO_STORE, "Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff"})

    @router.get("/favicon.ico", include_in_schema=False)
    async def favicon_route():
        if not favicon.is_file():
            raise HTTPException(404)
        return FileResponse(favicon, headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})

    @router.get("/assets/{filename}", include_in_schema=False)
    async def asset(filename: str):
        if not is_public_build_asset(filename) or filename not in public_assets:
            raise HTTPException(401, "authentication required")
        target = assets / filename
        if not target.is_file() or target.parent.resolve() != assets.resolve():
            raise HTTPException(404)
        return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})

    @router.get("/api/health")
    async def public_health():
        return {"status": "ok"}

    @router.post("/api/v1/auth/dingtalk/start")
    async def start(payload: StartBody):
        try:
            started = auth.start_qr(payload.return_path)
        except (AuthenticationError, ValueError):
            raise HTTPException(400, "login request invalid") from None
        return Response(
            content=(
                '{"authorization_url":' + json.dumps(started.authorization_url) + "}"
            ),
            media_type="application/json",
            headers=_NO_STORE,
        )

    @router.get("/api/v1/auth/dingtalk/callback")
    async def callback(state: str, code: str):
        try:
            issued, return_path = _session_value(await auth.complete_qr(state, code))
        except AuthenticationError as error:
            code_status = 401 if str(error) == "login attempt invalid" else 503
            raise HTTPException(code_status, str(error)) from None
        response = RedirectResponse(return_path or auth.route_prefix, status_code=302)
        _set_session_cookie(response, auth, issued)
        return response

    @router.post("/api/v1/auth/dingtalk/in-client/exchange")
    async def in_client(payload: CodeBody):
        try:
            issued, _ = _session_value(await auth.complete_in_client(payload.code))
        except AuthenticationError as error:
            raise HTTPException(503, str(error)) from None
        response = Response(
            content=("{\"csrf_token\":" + json.dumps(issued.csrf_token) + "}"),
            media_type="application/json",
        )
        _set_session_cookie(response, auth, issued)
        return response

    @router.get("/api/v1/account")
    async def account(request: Request):
        context: AuthContext = request.state.auth_context
        return {
            "internal_user_id": str(context.internal_user_id),
            "role": context.role.value,
            "hard_stale_read_only": context.hard_stale_read_only,
            "csrf_token": request.state.csrf_token,
        }

    @router.post("/api/v1/auth/logout", status_code=204)
    async def logout(request: Request):
        try:
            auth.logout(request.state.auth_context)
        except AuthenticationError:
            raise HTTPException(401, "authentication required") from None
        response = Response(status_code=204, headers=_NO_STORE)
        response.delete_cookie(
            auth.cookie_name,
            path=auth.route_prefix,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        response.delete_cookie(
            auth.csrf_cookie_name,
            path=auth.route_prefix,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/api/v1/manage/system-health")
    async def system_health(request: Request) -> dict[str, Any]:
        context: AuthContext = request.state.auth_context
        if context.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "platform owner required")
        audit = getattr(request.app.state, "system_health_audit", None)
        if audit is None:
            raise HTTPException(503, "required audit unavailable")
        try:
            audit(context)
        except Exception:
            raise HTTPException(503, "required audit unavailable") from None
        return {
            "status": "ok",
            "identity_mode": auth.mode.value,
            "dependencies": getattr(request.app.state, "dependency_health", {}),
        }

    return router
