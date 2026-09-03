from __future__ import annotations

import json
from ipaddress import ip_address
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.health.platform import build_public_platform_health
from app.spa import (
    OpenedPublicAsset,
    PublicAssetUnavailable,
    is_public_build_asset,
    open_public_build_asset,
    open_public_static_file,
)

from .auth import AuthenticationError, CompletedLogin, cookie_policy
from .fae_access import FaeWorkbenchAccessUnavailable
from .models import AuthContext, IssuedWebSession, Role
from .rate_limit import RateLimitExceeded, RateLimitUnavailable

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _login_csp(auth) -> str:
    if auth.route_prefix == "/":
        asset_source = "'self'"
        image_source = "'self'"
        connect_source = "'self'"
    else:
        base = auth.public_base_url + auth.route_prefix.rstrip("/")
        asset_source = base + "/assets/"
        image_source = base + "/"
        connect_source = base + "/api/"
    return (
        "default-src 'none'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'none'; "
        f"script-src {asset_source}; style-src {asset_source}; "
        f"img-src {image_source} data:; connect-src {connect_source}"
    )


def _opened_response(
    opened: OpenedPublicAsset, *, headers: dict[str, str]
) -> StreamingResponse:
    def chunks():
        try:
            while data := opened.file.read(64 * 1024):
                yield data
        finally:
            opened.file.close()

    return StreamingResponse(
        chunks(),
        media_type=opened.media_type,
        headers={**headers, "Content-Length": str(opened.size)},
    )


def _shell_response(
    opened: OpenedPublicAsset,
    *,
    csp: str,
    asset_base: str,
) -> Response:
    try:
        if opened.size > 2_097_152:
            raise PublicAssetUnavailable("application shell unavailable")
        content = opened.file.read(opened.size + 1)
    finally:
        opened.file.close()
    if len(content) != opened.size:
        raise PublicAssetUnavailable("application shell unavailable")
    base = asset_base.encode("ascii")
    content = content.replace(b'"./assets/', b'"' + base + b"assets/")
    content = content.replace(b'"./favicon.ico"', b'"' + base + b'favicon.ico"')
    disabled = b'<meta name="platform-identity-mode" content="disabled" />'
    enabled = b'<meta name="platform-identity-mode" content="enabled" />'
    if disabled in content:
        content = content.replace(disabled, enabled, 1)
    elif b"<head>" in content:
        content = content.replace(b"<head>", b"<head>" + enabled, 1)
    else:
        content = enabled + content
    return Response(
        content=content,
        media_type="text/html",
        headers={
            **_NO_STORE,
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
        },
    )


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_path: str | None = None


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2048)
    app_id: str = Field(
        default="platform", pattern=r"^[a-z][a-z0-9_-]{0,31}$"
    )


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


def _raise_rate_failure(error: Exception) -> None:
    if isinstance(error, RateLimitExceeded):
        raise HTTPException(
            429,
            "request rate limited",
            headers={"Retry-After": str(error.retry_after), **_NO_STORE},
        ) from None
    if isinstance(error, RateLimitUnavailable):
        raise HTTPException(503, "login unavailable", headers=_NO_STORE) from None
    raise error


def _is_loopback_request(request: Request) -> bool:
    edge_source = getattr(request.state, "edge_source", None)
    if edge_source is not None:
        return bool(edge_source.ip.is_loopback)
    if request.client is None:
        return False
    try:
        return ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def build_auth_router(
    auth,
    *,
    static_dir: str,
    public_assets: frozenset[str],
    detailed_health,
) -> APIRouter:
    router = APIRouter(prefix="" if auth.route_prefix == "/" else auth.route_prefix.rstrip("/"))

    def application_shell() -> Response:
        try:
            opened = open_public_static_file(static_dir, "index.html")
            return _shell_response(
                opened,
                csp=_login_csp(auth),
                asset_base=auth.route_prefix,
            )
        except PublicAssetUnavailable:
            raise HTTPException(503, "application shell unavailable") from None

    @router.get("/", include_in_schema=False)
    async def root(request: Request):
        token = request.cookies.get(auth.cookie_name)
        if token and auth.authenticate(token) is not None:
            return application_shell()
        return RedirectResponse(_local_path(auth, "/login"), status_code=302, headers=_NO_STORE)

    @router.get("/login", include_in_schema=False)
    async def login(request: Request):
        csp = _login_csp(auth)
        try:
            opened = open_public_static_file(static_dir, "index.html")
        except PublicAssetUnavailable:
            response = HTMLResponse("<!doctype html><title>Agent Platform</title>", headers={**_NO_STORE, "Content-Security-Policy": csp})
        else:
            response = _shell_response(
                opened,
                csp=csp,
                asset_base=auth.route_prefix,
            )
        issuer = getattr(auth, "issue_browser_challenge", None)
        if issuer is not None:
            challenge = issuer(request.cookies.get(auth.challenge_cookie_name))
            response.set_cookie(
                auth.challenge_cookie_name,
                challenge,
                max_age=600,
                **cookie_policy(auth.mode, auth.route_prefix),
            )
        return response

    @router.get("/account", include_in_schema=False)
    @router.get("/agents", include_in_schema=False)
    @router.get("/agents/{client_path:path}", include_in_schema=False)
    @router.get("/missions", include_in_schema=False)
    @router.get("/missions/{client_path:path}", include_in_schema=False)
    @router.get("/conversations", include_in_schema=False)
    @router.get("/conversations/{client_path:path}", include_in_schema=False)
    @router.get("/hr", include_in_schema=False)
    @router.get("/hr/", include_in_schema=False)
    @router.get("/hr/{client_path:path}", include_in_schema=False)
    @router.get("/marketing", include_in_schema=False)
    @router.get("/marketing/", include_in_schema=False)
    @router.get("/marketing/{client_path:path}", include_in_schema=False)
    @router.get("/fae/manage", include_in_schema=False)
    @router.get("/fae/manage/", include_in_schema=False)
    @router.get("/fae/manage/{client_path:path}", include_in_schema=False)
    @router.get("/ai-notes", include_in_schema=False)
    @router.get("/ai-notes/{client_path:path}", include_in_schema=False)
    @router.get("/admin", include_in_schema=False)
    @router.get("/admin/{client_path:path}", include_in_schema=False)
    @router.get("/sessions", include_in_schema=False)
    @router.get("/sessions/{client_path:path}", include_in_schema=False)
    @router.get("/review", include_in_schema=False)
    @router.get("/activity", include_in_schema=False)
    @router.get("/identity", include_in_schema=False)
    @router.get("/governance", include_in_schema=False)
    @router.get("/flywheel", include_in_schema=False)
    async def authenticated_shell(client_path: str | None = None):
        del client_path
        return application_shell()

    @router.get("/favicon.ico", include_in_schema=False)
    async def favicon_route():
        try:
            opened = open_public_static_file(static_dir, "favicon.ico")
        except PublicAssetUnavailable:
            try:
                opened = open_public_static_file(
                    static_dir, "platform-logo.svg"
                )
            except PublicAssetUnavailable:
                raise HTTPException(404) from None
        return _opened_response(
            opened,
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/assets/{filename}", include_in_schema=False)
    async def asset(filename: str):
        if not is_public_build_asset(filename) or filename not in public_assets:
            raise HTTPException(401, "authentication required")
        try:
            opened = open_public_build_asset(static_dir, filename)
        except PublicAssetUnavailable:
            raise HTTPException(404)
        return _opened_response(
            opened,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/api/health")
    async def public_health():
        return build_public_platform_health()

    @router.get("/api/v1/auth/dingtalk/config")
    async def public_dingtalk_config(request: Request):
        return_paths = request.query_params.getlist("return_path")
        return_path = return_paths[0] if len(return_paths) == 1 else None
        try:
            app_id, app_key = auth.in_client_configuration(return_path)
        except (AuthenticationError, ValueError):
            raise HTTPException(400, "login request invalid") from None
        return Response(
            content=json.dumps(
                {
                    "client_id": app_key,
                    "corp_id": auth.corp_id,
                    "app_id": app_id,
                }
            ),
            media_type="application/json",
            headers=_NO_STORE,
        )

    @router.post("/api/v1/auth/dingtalk/start")
    async def start(payload: StartBody, request: Request):
        try:
            if getattr(auth, "rate_limiter", None) is None:
                started = auth.start_qr(payload.return_path)
            else:
                started = auth.start_qr(
                    payload.return_path,
                    request.cookies.get(auth.challenge_cookie_name),
                    request.state.edge_source.ip,
                )
        except (RateLimitExceeded, RateLimitUnavailable) as error:
            _raise_rate_failure(error)
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
    async def callback(request: Request, state: str, code: str):
        token = request.cookies.get(auth.cookie_name)
        if token and auth.authenticate(token) is not None:
            return RedirectResponse(
                _local_path(auth, "/account"), status_code=302, headers=_NO_STORE
            )
        try:
            if getattr(auth, "rate_limiter", None) is None:
                completed = await auth.complete_qr(state, code)
            else:
                completed = await auth.complete_qr(
                    state, code, request.state.edge_source.ip
                )
            issued, return_path = _session_value(completed)
        except (RateLimitExceeded, RateLimitUnavailable) as error:
            _raise_rate_failure(error)
        except AuthenticationError:
            return RedirectResponse(
                _local_path(auth, "/login") + "?error=1",
                status_code=302,
                headers=_NO_STORE,
            )
        response = RedirectResponse(return_path or auth.route_prefix, status_code=302)
        _set_session_cookie(response, auth, issued)
        return response

    @router.post("/api/v1/auth/dingtalk/in-client/exchange")
    async def in_client(payload: CodeBody, request: Request):
        try:
            if getattr(auth, "rate_limiter", None) is None:
                completed = await auth.complete_in_client(
                    payload.code, app_id=payload.app_id
                )
            else:
                completed = await auth.complete_in_client(
                    payload.code,
                    request.cookies.get(auth.challenge_cookie_name),
                    request.state.edge_source.ip,
                    app_id=payload.app_id,
                )
            issued, _ = _session_value(completed)
        except (RateLimitExceeded, RateLimitUnavailable) as error:
            _raise_rate_failure(error)
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
        account_contract_v2 = (
            request.headers.get("X-Platform-Account-Contract") == "2"
        )
        try:
            snapshot = auth.account_snapshot(context)
            if account_contract_v2:
                fae_access = getattr(request.app.state, "fae_access", None)
                workspace_scopes = (
                    ["fae_workbench"]
                    if fae_access is not None and fae_access.allows(context)
                    else []
                )
        except AuthenticationError:
            raise HTTPException(503, "account unavailable") from None
        except FaeWorkbenchAccessUnavailable:
            raise HTTPException(503, "account unavailable") from None
        payload = {
            "internal_user_id": str(context.internal_user_id),
            "display_name": snapshot["display_name"],
            "role": context.role.value,
            "departments": snapshot["departments"],
            "gender": snapshot["gender"],
            "real_name": snapshot["real_name"],
            "mobile": snapshot["mobile"],
            "primary_department": snapshot["primary_department"],
            "observation_agent_ids": snapshot["observation_agent_ids"],
            "directory_freshness": snapshot["directory_freshness"],
            "hard_stale_read_only": context.hard_stale_read_only,
            "csrf_token": request.state.csrf_token,
        }
        if account_contract_v2:
            payload["workspace_scopes"] = workspace_scopes
        return payload

    @router.get("/api/v1/internal/session/subject")
    async def internal_session_subject(request: Request):
        if not _is_loopback_request(request):
            raise HTTPException(404, "not found")
        context: AuthContext = request.state.auth_context
        try:
            display_name = auth.account_snapshot(context)["display_name"]
        except Exception:
            raise HTTPException(503, "session subject unavailable") from None
        if not isinstance(display_name, str) or not display_name:
            raise HTTPException(503, "session subject unavailable")
        return {
            "internal_user_id": str(context.internal_user_id),
            "display_name": display_name,
            "active": True,
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
        try:
            payload = detailed_health(request)
        except Exception:
            raise HTTPException(503, "detailed health unavailable") from None
        audit = getattr(request.app.state, "system_health_audit", None)
        if audit is None:
            raise HTTPException(503, "required audit unavailable")
        try:
            audit(context)
        except Exception:
            raise HTTPException(503, "required audit unavailable") from None
        return {**payload, "identity_mode": auth.mode.value}

    return router
