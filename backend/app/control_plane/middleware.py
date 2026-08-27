from __future__ import annotations

import hmac
import re
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders, QueryParams
from starlette.responses import JSONResponse
from starlette.requests import Request
from starlette.routing import Match

from app.spa import is_public_build_asset
from .client_address import UntrustedForwardingHeaders, resolve_edge_source
from .rate_limit import RateLimitExceeded, RateLimitUnavailable, rate_limit_response


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_IDENTITY_RESPONSE_PATHS = frozenset(
    {
        "/",
        "/login",
        "/api/health",
        "/api/v1/extensions/voc/health",
        "/api/v1/auth/dingtalk/start",
        "/api/v1/auth/dingtalk/config",
        "/api/v1/auth/dingtalk/callback",
        "/api/v1/auth/dingtalk/in-client/exchange",
        "/api/v1/account",
        "/api/v1/internal/session/subject",
        "/api/v1/auth/logout",
        "/api/v1/manage/system-health",
    }
)
_WORKER_RUN_ROUTE = re.compile(
    r"/api/v1/execution-worker/runs/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
    r"(?:dispatched|events|terminal|stop-ack)\Z"
)
_WORKER_NAMESPACE = "/api/v1/execution-worker"
_DIRECT_AGENT_MISSION_RESPONSE = re.compile(
    r"/api/v1/agents/[^/]+/missions\Z"
)
_DIRECT_AGENT_CONVERSATION_RESPONSE = re.compile(
    r"/api/v1/agents/[^/]+/conversations\Z"
)


def _is_agent_brain_response_path(path: str | None) -> bool:
    if not isinstance(path, str):
        return False
    return (
        path == "/api/v1/catalog/agents"
        or path == "/api/v1/brain/missions"
        or path.startswith("/api/v1/brain/missions/")
        or path == "/api/v1/conversations"
        or path.startswith("/api/v1/conversations/")
        or _DIRECT_AGENT_MISSION_RESPONSE.fullmatch(path) is not None
        or _DIRECT_AGENT_CONVERSATION_RESPONSE.fullmatch(path) is not None
    )


def _is_ai_notes_response_path(path: str | None) -> bool:
    return isinstance(path, str) and (
        path == "/api/v1/ai-notes"
        or path.startswith("/api/v1/ai-notes/")
    )


def is_execution_worker_request(method: str, path: str) -> bool:
    return method == "POST" and (
        path
        in {
            "/api/v1/execution-worker/lease",
            "/api/v1/execution-worker/heartbeat",
        }
        or _WORKER_RUN_ROUTE.fullmatch(path) is not None
    )


def _is_execution_worker_namespace(path: str) -> bool:
    return path == _WORKER_NAMESPACE or path.startswith(
        _WORKER_NAMESPACE + "/"
    )


def _has_canonical_ascii_raw_path(scope, path: str) -> bool:
    raw_path = scope.get("raw_path")
    if not isinstance(raw_path, bytes):
        return False
    try:
        return raw_path.decode("ascii") == path
    except UnicodeDecodeError:
        return False


class DisabledExecutionWorkerNamespaceMiddleware:
    """Reserve the worker namespace when the signed relay is not mounted."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _is_execution_worker_namespace(
            scope.get("path", "")
        ):
            await JSONResponse(
                {"detail": "not found"},
                status_code=404,
                headers=_NO_STORE,
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _unprefixed(path: str, prefix: str) -> str | None:
    if prefix == "/":
        return path
    base = prefix.rstrip("/")
    if path == base:
        return "/"
    if not path.startswith(base + "/"):
        return None
    return path[len(base):]


def is_public_request(
    method: str,
    path: str,
    route_prefix: str,
    public_assets: frozenset[str] | None = None,
) -> bool:
    local = _unprefixed(path, route_prefix)
    if local is None:
        return False
    exact = {
        ("GET", "/"),
        ("GET", "/login"),
        ("GET", "/favicon.ico"),
        ("GET", "/api/health"),
        ("GET", "/api/v1/extensions/voc/health"),
        ("POST", "/api/v1/auth/dingtalk/start"),
        ("GET", "/api/v1/auth/dingtalk/config"),
        ("GET", "/api/v1/auth/dingtalk/callback"),
        ("POST", "/api/v1/auth/dingtalk/in-client/exchange"),
    }
    if (method, local) in exact:
        return True
    if method == "GET" and local.startswith("/assets/"):
        name = local.removeprefix("/assets/")
        return is_public_build_asset(name) and name in (public_assets or frozenset())
    return False


def _origin_matches(
    origin: str | None, expected: str, *, effective_scheme: str | None = None
) -> bool:
    if not origin or origin == "null":
        return False
    try:
        actual = urlsplit(origin)
        canonical = urlsplit(expected)
        return (
            actual.scheme == "https"
            and (effective_scheme is None or effective_scheme == canonical.scheme)
            and actual.username is None
            and actual.password is None
            and not actual.path
            and not actual.query
            and not actual.fragment
            and (actual.scheme, actual.hostname, actual.port)
            == (canonical.scheme, canonical.hostname, canonical.port)
        )
    except ValueError:
        return False


class IdentitySecurityMiddleware:
    """Exact public allowlist plus server-side Session and mutation checks."""

    def __init__(
        self,
        app,
        *,
        auth,
        public_assets: frozenset[str],
        authorization=None,
        routes=(),
    ) -> None:
        self.app = app
        self.auth = auth
        self.public_assets = public_assets
        self.authorization = authorization
        self.routes = self._expand_included_routes(routes)

    @staticmethod
    def _expand_included_routes(routes) -> tuple:
        expanded = []
        for route in routes:
            effective = getattr(route, "effective_route_contexts", None)
            if callable(effective):
                expanded.extend(effective())
            else:
                expanded.append(route)
        return tuple(expanded)

    def _resolved_route(self, scope) -> tuple[str, dict] | None:
        for route in self.routes:
            matcher = getattr(route, "matches", None)
            if matcher is None:
                continue
            match, child_scope = matcher(scope)
            if match is Match.FULL:
                template = getattr(route, "path", None)
                if not isinstance(template, str):
                    return None
                local = _unprefixed(template, self.auth.route_prefix)
                return local or template, child_scope.get("path_params", {})
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"].upper()
        path = scope.get("path", "")
        local_path = _unprefixed(path, self.auth.route_prefix)
        identity_response = (
            local_path in _IDENTITY_RESPONSE_PATHS
            or _is_agent_brain_response_path(local_path)
            or _is_ai_notes_response_path(local_path)
        )

        async def protected_send(message):
            if identity_response and message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["Cache-Control"] = (
                    "private, no-store"
                    if local_path == "/api/v1/account"
                    else "no-store"
                )
                response_headers["Pragma"] = "no-cache"
            await send(message)

        async def worker_send(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["Cache-Control"] = "no-store"
            await protected_send(message)

        public = is_public_request(
            method,path,self.auth.route_prefix,self.public_assets
        )
        headers = Headers(scope=scope)
        edge_source = None
        if getattr(self.auth, "trusted_proxy_networks", ()):
            try:
                edge_source = resolve_edge_source(
                    Request(scope), self.auth.trusted_proxy_networks
                )
            except UntrustedForwardingHeaders:
                await JSONResponse(
                    {"detail": "request forwarding rejected"},
                    status_code=400,
                    headers=_NO_STORE,
                )(scope, receive, protected_send)
                return
            scope.setdefault("state", {})["edge_source"] = edge_source

        if self.auth.route_prefix == "/" and _is_execution_worker_namespace(path):
            if not _has_canonical_ascii_raw_path(
                scope, path
            ) or not is_execution_worker_request(method, path):
                await JSONResponse(
                    {"detail": "not found"},
                    status_code=404,
                    headers=_NO_STORE,
                )(scope, receive, protected_send)
                return
            await self.app(scope, receive, worker_send)
            return

        if public and method not in _SAFE_METHODS and not _origin_matches(
            headers.get("origin"), self.auth.public_base_url,
            effective_scheme=edge_source.scheme if edge_source else None,
        ):
            await JSONResponse(
                {"detail": "request origin rejected"}, status_code=403,
                headers=_NO_STORE,
            )(scope, receive, protected_send)
            return

        session = None
        csrf_cookie = None
        if not public or method not in _SAFE_METHODS:
            # Starlette does not preparse Cookies at middleware time.
            from http.cookies import SimpleCookie

            jar = SimpleCookie()
            try:
                jar.load(headers.get("cookie", ""))
            except Exception:
                jar = SimpleCookie()
            morsel = jar.get(self.auth.cookie_name)
            if morsel is not None:
                session = self.auth.authenticate(morsel.value)
            csrf_morsel = jar.get(self.auth.csrf_cookie_name)
            if csrf_morsel is not None:
                csrf_cookie = csrf_morsel.value

        if not public and session is None:
            await JSONResponse(
                {"detail": "authentication required"}, status_code=401,
                headers=_NO_STORE,
            )(scope, receive, protected_send)
            return

        if session is not None:
            context, csrf_digest = session
            limiter = getattr(self.auth, "rate_limiter", None)
            if limiter is not None:
                try:
                    limiter.check_authenticated(
                        context.internal_user_id, mutation=method not in _SAFE_METHODS
                    )
                except RateLimitExceeded as error:
                    await rate_limit_response(error)(scope, receive, protected_send)
                    return
                except RateLimitUnavailable:
                    await JSONResponse(
                        {"detail": "rate limit unavailable"},
                        status_code=503,
                        headers=_NO_STORE,
                    )(scope, receive, protected_send)
                    return
            scope.setdefault("state", {})["auth_context"] = context
            scope["state"]["csrf_digest"] = csrf_digest
            verifier = getattr(self.auth, "verify_csrf", None)
            csrf_cookie_valid = (
                verifier(csrf_cookie or "", csrf_digest)
                if verifier is not None
                else isinstance(csrf_digest, str)
                and isinstance(csrf_cookie, str)
                and hmac.compare_digest(csrf_cookie, csrf_digest)
            )
            scope["state"]["csrf_token"] = csrf_cookie if csrf_cookie_valid else ""
            if not public and self.authorization is not None:
                resolved = self._resolved_route(scope)
                if resolved is None:
                    await JSONResponse(
                        {"detail": "route not authorized"},
                        status_code=403,
                        headers=_NO_STORE,
                    )(scope, receive, protected_send)
                    return
                route_template, path_params = resolved
                query = QueryParams(scope.get("query_string", b""))
                agent_ids = tuple(
                    value for value in (
                        path_params.get("agent_id"),
                        *query.getlist("agent_id"),
                    ) if value is not None
                )
                decision = self.authorization.decide(
                    context, method, route_template, agent_ids
                )
                if not decision.allowed:
                    await JSONResponse(
                        {"detail": decision.reason},
                        status_code=decision.status_code,
                        headers=_NO_STORE,
                    )(scope, receive, protected_send)
                    return
                try:
                    if context.hard_stale_read_only:
                        audit = getattr(self.auth, "hard_stale_audit", None)
                        if audit is None:
                            raise RuntimeError
                        audit(
                            context.internal_user_id,
                            "read",
                            "management_projection",
                        )
                    else:
                        self.authorization.audit_permitted(
                            context, route_template, decision
                        )
                except Exception:
                    await JSONResponse(
                        {"detail": "required audit unavailable"},
                        status_code=503,
                        headers=_NO_STORE,
                    )(scope, receive, protected_send)
                    return
            if method not in _SAFE_METHODS:
                if not _origin_matches(
                    headers.get("origin"), self.auth.public_base_url,
                    effective_scheme=edge_source.scheme if edge_source else None,
                ):
                    await JSONResponse(
                        {"detail": "request origin rejected"}, status_code=403,
                        headers=_NO_STORE,
                    )(scope, receive, protected_send)
                    return
                submitted = headers.get("x-csrf-token", "")
                verified = (
                    verifier(submitted, csrf_digest)
                    if verifier is not None
                    else isinstance(csrf_digest, str)
                    and hmac.compare_digest(submitted, csrf_digest)
                )
                if not verified:
                    await JSONResponse(
                        {"detail": "CSRF verification failed"}, status_code=403,
                        headers=_NO_STORE,
                    )(scope, receive, protected_send)
                    return

        await self.app(scope, receive, protected_send)
